"""Plan-and-follow for long tasks. Pure functions, no model calls, no tokens spent deciding.

A plan is the agent's todo_write list. For a task that needs one (decided from the route and the request at the start
of the turn), the first model call is told to write it before acting; while it works, a one-line reminder names the
current step; the panel and the "plan" event show which step the task is on. Short tasks pay nothing."""
from __future__ import annotations

import os
import re

EVERY = 6                                # tool calls without a plan update before a reminder

_REQ_LINE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+\S", re.M)
_REQ_INLINE = re.compile(r"(?:^|[\s(])\d{1,2}[.)]\s+[A-Za-z]")
_FILE = re.compile(r"[\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|c|cpp|h|md|csv|xlsx|docx|pptx|pdf|json|ya?ml|"
                   r"html|css|sql|txt|toml|sh)\b", re.I)
_FOCUSED = {"code.debug", "code.review", "code.explain", "code.terminal", "code.test"}


def mode(route=None) -> str:
    """auto | 1 | 0: MH_PLAN when set, else the route's harness profile (default auto)."""
    from meshpool_harness.hrouter.profiles import effective
    return {"on": "1", "off": "0"}.get(effective(route)["plan"], "auto")


def every() -> int:
    try:
        return max(1, int(os.environ.get("MH_PLAN_EVERY", EVERY)))
    except ValueError:
        return EVERY


def signals(text: str) -> dict:
    text = (text or "").split("\n\n​")[0]
    size = len(text.strip())
    text = text[:4000]                          # the file pattern is slow on a huge pasted line: scan the start
    reqs = max(len(_REQ_LINE.findall(text)), len(_REQ_INLINE.findall(text)))
    return {"reqs": reqs, "files": len({f.lower() for f in _FILE.findall(text)}), "len": size}


def needed(route, text: str, plan_mode: bool = False) -> bool:
    """Does this request need a written plan? route: an hrouter Route or None."""
    m = mode(route)
    if plan_mode or m == "0":
        return False
    if m == "1":
        return True
    spec = getattr(route, "spec", "") or ""
    lane = getattr(route, "lane", "") or ""
    if spec == "chat" or lane == "chat":
        return False
    s = signals(text)
    many = s["reqs"] >= 3 or s["files"] >= 3
    if lane == "deep" and spec != "code.explain":
        return True
    if spec in _FOCUSED:                              # focused work: only an explicit list of things to do
        return many
    if getattr(route, "difficulty", "") == "hard":
        return True
    if spec in ("code.build", "code.refactor"):
        return s["len"] >= 200 or s["files"] >= 2 or s["reqs"] >= 2
    return many or s["len"] >= 900


def instruction() -> str:
    return ("This task needs a plan. Before acting, write it with todo_write: 3 to 8 concrete steps, each one "
            "checkable when done, the first marked in_progress. Then follow it, keeping the list current as steps "
            "finish (you may rewrite it if the work changes).")


def current(todos: list) -> int:
    """Index of the current step: the first in_progress, else the first pending; -1 when all are done or none."""
    for want in ("in_progress", "pending"):
        for i, t in enumerate(todos or []):
            if t.get("status", "pending") == want:
                return i
    return -1


def state(todos: list) -> dict | None:
    """The plan as the event, the run result and the panel carry it. current is the 1-based step number
    (0 when every step is done)."""
    if not todos:
        return None
    items = [{"content": str(t.get("content", "")), "status": t.get("status", "pending")} for t in todos]
    cur = current(items)
    return {"items": items, "current": cur + 1 if cur >= 0 else 0, "total": len(items),
            "done": sum(i["status"] == "completed" for i in items)}


def _short(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def reminder(todos: list, tail: str = "") -> str:
    """One short line: where the plan stands. Empty when nothing is left."""
    cur = current(todos)
    if cur < 0:
        return ""
    done = [_short(t["content"], 40) for t in todos if t.get("status") == "completed"][-3:]
    line = f"Plan: step {cur + 1} of {len(todos)} — {_short(todos[cur]['content'], 90)}."
    if done:
        line += " Done so far: " + "; ".join(done) + "."
    return line + (" " + tail if tail else "")


def panel(todos: list) -> dict:
    """The session panel's plan section: each step is done, current or pending."""
    st = state(todos)
    if not st:
        return {"steps": [], "current": 0, "total": 0, "done": 0}
    steps = []
    for i, it in enumerate(st["items"], 1):
        s = "done" if it["status"] == "completed" else "current" if i == st["current"] else "pending"
        steps.append({"n": i, "text": it["content"], "state": s})
    return {"steps": steps, "current": st["current"], "total": st["total"], "done": st["done"]}
