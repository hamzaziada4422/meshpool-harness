"""Visible quality checks: a record of the checks that actually ran while a turn made a file or a report.

The office engines (deck rules, workbook rules, Office schema) and the research verifier call `passed()` or
`failed()` when a check finishes. The turn runner calls `begin(run_id)` at the start of a turn and `end(run_id)` after
it, shows `events_for()` in the progress panel and `final_line()` under the reply.

Honest by construction: only a check that ran is recorded; a rule that could not be evaluated ("not_run") is never
listed. With no recorder active every call does nothing, so the engines behave exactly as before.

The record is a small file under <Meshpool data folder>/checks/, not memory, because the engines often run in another
process (the office tool server) than the app. `begin()` writes a pointer file and sets MH_CHECK_RUN for child
processes; both are read by `passed()`.

PA-toplevel-23: that pointer and MH_CHECK_RUN are one per process, so two chats running at once would mix their
checks. In this process the recorder is therefore also keyed by context: `begin()` binds the run to the calling
thread's context (a ContextVar), `bind()` carries it into a worker thread, and `env_for(chat)` gives an engine process
started for one chat that chat's own run. The process-wide pointer is only the fallback (a tool server started
before the turn, say).
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from meshpool_harness import config as C

STALE_S = 6 * 3600
_LABELS = {                                   # kind -> (singular, plural) wording for the one-line summary
    "deck_rules": ("design rule", "design rules"),
    "workbook_rules": ("workbook rule", "workbook rules"),
    "sources": ("source", "sources"),
}


_RUN: contextvars.ContextVar = contextvars.ContextVar("meshpool_check_run", default="")
_ACTIVE: dict = {}                            # run id -> start time: runs begun in this process and not ended
_ACTIVE_LOCK = threading.Lock()


def _dir() -> Path:
    d = C.home() / "checks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(run_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", str(run_id))[:100] or "run"


def _file(run_id: str) -> Path:
    return _dir() / f"{_safe(run_id)}.jsonl"


def _pointer() -> Path:
    return _dir() / "current.json"


def begin(run_id: str) -> None:
    """Start recording for `run_id`. Replaces any earlier record of the same id."""
    with _ACTIVE_LOCK:
        _ACTIVE[str(run_id)] = time.time()
    _RUN.set(str(run_id))                     # this thread's context: its checks go to this run, whatever began later
    try:
        _file(run_id).write_text("", encoding="utf-8")
        _pointer().write_text(json.dumps({"run": str(run_id), "ts": time.time()}), encoding="utf-8")
        os.environ["MH_CHECK_RUN"] = str(run_id)
    except OSError:
        pass


def end(run_id: str) -> None:
    """Stop recording. The record stays readable."""
    with _ACTIVE_LOCK:
        _ACTIVE.pop(str(run_id), None)
    if _RUN.get() == str(run_id):
        _RUN.set("")
    try:
        if current() == str(run_id):
            _pointer().unlink(missing_ok=True)
        if os.environ.get("MH_CHECK_RUN") == str(run_id):
            os.environ.pop("MH_CHECK_RUN", None)
    except OSError:
        pass


def current() -> str:
    """The run being recorded now ("" when none): this context's own run first, then the process-wide one."""
    own = _RUN.get()
    if own:
        with _ACTIVE_LOCK:
            live = own in _ACTIVE             # bound here and not ended (a thread reused after end() falls through)
        if live and (C.home() / "checks" / f"{_safe(own)}.jsonl").is_file():   # and its record is in this data folder
            return own
    rid = os.environ.get("MH_CHECK_RUN", "")
    if rid:
        return rid
    try:
        d = json.loads(_pointer().read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts", 0)) < STALE_S:
            return str(d.get("run", ""))
    except (OSError, ValueError):
        pass
    return ""


@contextmanager
def bind(run_id: str):
    """Record the checks made inside this block (in a worker thread, say) into `run_id`."""
    tok = _RUN.set(str(run_id or ""))
    try:
        yield
    finally:
        _RUN.reset(tok)


def run_for(chat_id: str) -> str:
    """The newest run begun in this process for chat `chat_id` (run ids are "<chat id>:<turn start>"), else ""."""
    if not chat_id:
        return ""
    pre = f"{chat_id}:"
    with _ACTIVE_LOCK:
        mine = [(t, r) for r, t in _ACTIVE.items() if r.startswith(pre)]
    return max(mine)[1] if mine else ""


def env_for(chat_id: str) -> dict:
    """Environment for a process started for chat `chat_id`: its checks go to that chat's run, not to whichever chat
    began a turn last. Empty when the chat has no run here (the inherited behaviour stays)."""
    rid = run_for(chat_id)
    return {"MH_CHECK_RUN": rid} if rid else {}


def record(kind: str, label: str, count: int | None = None, ok: bool = True, subject: str = "") -> bool:
    """One check that ran. Returns False (and does nothing) when no recorder is active. Never raises."""
    try:
        rid = current()
        if not rid:
            return False
        row = {"ts": round(time.time(), 3), "kind": kind, "label": label, "count": count, "ok": bool(ok),
               "subject": subject}
        with _file(rid).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 - a check record must never break an engine
        return False


def passed(kind: str, label: str, count: int | None = None, subject: str = "") -> bool:
    return record(kind, label, count, True, subject)


def failed(kind: str, label: str, count: int | None = None, subject: str = "") -> bool:
    return record(kind, label, count, False, subject)


def rules_ran(kind: str, label: str, statuses: dict, subject: str = "") -> None:
    """From a validator's per-rule status dict ({"P3": "pass" | "fail" | "warn" | "not_run"}): the rules that ran.
    Rules marked not_run are not counted."""
    try:
        ran = [s for s in (statuses or {}).values() if s != "not_run"]
        ok = sum(1 for s in ran if s in ("pass", "warn"))
        bad = sum(1 for s in ran if s == "fail")
        if ok:
            passed(kind, label, ok, subject)
        if bad:
            failed(kind, label, bad, subject)
    except Exception:  # noqa: BLE001
        pass


def _rows(run_id: str) -> list[dict]:
    out = []
    try:
        for line in _file(run_id).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def _latest(rows: list[dict]) -> list[dict]:
    """A deck checked three times while it was fixed counts once. Per (kind, subject) the latest run of results
    counts: the rows written within a second of the last row of that group."""
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["kind"], r.get("subject", "")), []).append(r)
    out = []
    for g in groups.values():
        end = g[-1]["ts"]
        out += [r for r in g if end - r["ts"] <= 1.0]
    return sorted(out, key=lambda r: r["ts"])


def _phrase(kind: str, label: str, n: int | None) -> str:
    one, many = _LABELS.get(kind, (label, label))
    if n is None:
        return label
    return f"{n} {one if n == 1 else many}"


def summary(run_id: str) -> dict:
    """{"line": "Checked: 20 design rules, Office schema, 14 sources", "items": [...], "failed": n}.
    The line lists what passed; checks that did not pass are counted in "failed" and named in the items."""
    rows = _latest(_rows(run_id))
    ok_rows = [r for r in rows if r["ok"]]
    bad_rows = [r for r in rows if not r["ok"]]
    merged: dict = {}
    for r in ok_rows:                                  # same kind from several files: add their counts
        m = merged.setdefault(r["kind"], {"label": r["label"], "count": None})
        if r.get("count") is not None:
            m["count"] = (m["count"] or 0) + int(r["count"])
    parts = [_phrase(k, m["label"], m["count"]) for k, m in merged.items()]
    nbad = sum(int(r.get("count") or 1) for r in bad_rows)
    line = ("Checked: " + ", ".join(parts)) if parts else ""
    if line and nbad:
        line += f" ({nbad} did not pass)"
    return {"line": line, "items": [{"kind": r["kind"], "label": _phrase(r["kind"], r["label"], r.get("count")),
                                      "ok": r["ok"], "subject": r.get("subject", "")} for r in rows],
            "failed": nbad}


def final_line(run_id: str) -> str:
    return summary(run_id)["line"]


def events_for(run_id: str, since: int = 0) -> list[dict]:
    """Events for the progress panel, one per recorded check after the first `since` records."""
    out = []
    for r in _rows(run_id)[since:]:
        out.append({"type": "check", "kind": r["kind"], "ok": r["ok"], "subject": r.get("subject", ""),
                    "text": _phrase(r["kind"], r["label"], r.get("count")) + (" passed" if r["ok"] else " did not pass")})
    return out
