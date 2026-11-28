"""While you were away: what finished since the user last looked, and nothing else.

Finished scheduled runs get one line each, and so does a goal that was met, stopped or paused. A task with "Only tell me when something changed" is reported only when
its result differs from the previous run's. Insight cards (within budget) follow. Everything is read from data Meshpool
already keeps; no model call. The last time the user looked is kept in <Meshpool data folder>/insights/away.json.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from meshpool_harness import config as C

MAX_RUNS = 6
SAME = 0.9                      # result similarity at or above which a run "did not change"


def _file() -> Path:
    return C.home() / "insights" / "away.json"


def _state() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    from meshpool_harness import assistant as A
    A._write(_file(), json.dumps(d))


def _words(t: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (t or "").lower()))


def similarity(a: str, b: str) -> float:
    x, y = _words(a), _words(b)
    return len(x & y) / len(x | y) if x | y else 1.0


def one_line(text: str, n: int = 160) -> str:
    from meshpool_harness import assistant as A
    t = A.dashless(re.sub(r"\s+", " ", re.sub(r"[#*_`>]+", "", text or "")).strip())
    m = re.match(r"(.{20,%d}?[.!?])(\s|$)" % n, t)
    t = m.group(1) if m else t
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def last_reply(sid: str) -> str:
    from meshpool_harness.ui import hub
    evs = hub._events(sid)
    return next((e.get("text", "") for e in reversed(evs) if e.get("type") == "assistant" and e.get("text")), "")


def mark_seen(now: float | None = None) -> None:
    s = _state()
    s["seen"] = now or time.time()
    _save(s)


def goal_runs(since: float) -> list[dict]:
    """One line for each goal (agent/goal.py) that was met, stopped or paused since the user last looked."""
    out = []
    try:
        from meshpool_harness.agent import goal as G
        files = list((C.runs_dir() / "sessions").glob("*/goal.json"))     # this data folder's chats
    except Exception:  # noqa: BLE001
        return out
    for f in files:
        st = G.load(f.parent)
        if not st or st.get("status") not in ("met", "stopped", "paused") or st.get("dismissed"):
            continue
        ts = float(st.get("ended") or st.get("updated") or 0)
        if ts <= since:
            continue
        sid = f.parent.name
        try:
            from meshpool_harness.ui import agent_api as AG
            meta = AG.read_meta(sid)
            if meta.get("incognito") or meta.get("deleted"):
                continue
        except Exception:  # noqa: BLE001
            pass
        out.append({"task_id": "goal:" + sid, "name": "Goal", "ts": ts, "line": G.away_line(st)[:240],
                    "chat_id": sid, "changed": True, "goal": st.get("status")})
    return out


def report(now: float | None = None, with_cards: bool = True) -> dict:
    """{runs: [{task_id, name, ts, line, chat_id, changed}], quiet: [names of tasks that did not change],
    cards: [...], since}. Empty runs and cards: nothing to show."""
    from meshpool_harness import assistant as A
    now = now or time.time()
    st = _state()
    since = st.get("seen") or (now - 86400)
    results = st.setdefault("results", {})
    runs, quiet = [], []
    for t in A.tasks():
        sid, ran = t.get("last_thread"), t.get("last_run")
        if not sid or not ran or ran <= since:
            continue
        try:
            from meshpool_harness.ui import agent_api as AG
            if AG.read_meta(sid).get("incognito"):
                continue
        except Exception:  # noqa: BLE001
            pass
        text = last_reply(sid)
        if not text:
            continue
        prev = results.get(t["id"])
        changed = True
        if t.get("only_on_change") and prev and prev.get("sid") != sid:
            changed = similarity(prev.get("text", ""), text) < SAME
        results[t["id"]] = {"sid": sid, "text": text[:4000], "ts": ran}
        if changed:
            runs.append({"task_id": t["id"], "name": t.get("name", ""), "ts": ran, "line": one_line(text),
                         "chat_id": sid, "changed": True})
        else:
            quiet.append(t.get("name", ""))
    runs += goal_runs(since)                 # a goal met, stopped or paused while the user was away: one line each
    runs.sort(key=lambda r: -r["ts"])
    cards = []
    if with_cards:
        try:
            from meshpool_harness import insights as I
            cards = I.cards_for("home")[:3]
        except Exception:  # noqa: BLE001
            cards = []
    _save(st)                                # remember the results for the next comparison, not that it was seen
    return {"runs": runs[:MAX_RUNS], "quiet": quiet, "cards": cards, "since": since,
            "empty": not runs and not cards}
