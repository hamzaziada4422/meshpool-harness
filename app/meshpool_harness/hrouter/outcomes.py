"""The outcome log: what each routed turn cost and whether it held up, so the routing table is refit from facts.

One JSON line per turn in <Meshpool data folder>/routing/outcomes.jsonl:
  {ts, chat, spec, lane, language, harness, model, secs, tokens_in, tokens_cached, tokens_out, calls, reason, error,
  stopped}

A later undo, retry or "that's wrong" on the same chat marks the turn as failed (mark()). Nothing leaves the
computer. refit() summarises the log per spec and harness: the numbers a routing.json update starts from.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness import fsio

MAX_BYTES = 20 * 1024 * 1024


def path() -> Path:
    return C.home() / "routing" / "outcomes.jsonl"


def _append(row: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with fsio.file_lock(p):
        if p.exists() and p.stat().st_size > MAX_BYTES:          # keep the newest half
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            fsio.atomic_write_text(p, "\n".join(lines[len(lines) // 2:]) + "\n")
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _review_of(agent) -> dict | None:
    """The independent check of this turn, in short (agent/grader.py): what the receipt shows."""
    r = getattr(agent, "last_review", None)
    if not isinstance(r, dict) or r.get("turn") != getattr(agent, "_turn_no", None):
        return None
    t = r.get("tokens") or {}
    return {"verdict": r.get("verdict"), "acceptance": r.get("acceptance"), "met": r.get("met", 0), "total": r.get("total", 0),
            "rounds": r.get("round", 0), "secs": r.get("secs", 0), "tokens": int(t.get("in", 0)) + int(t.get("out", 0)),
            "error": r.get("error") or ""}


def _nested_calls(a) -> int | None:
    """Calls of the agent's current task that were not the chat's own (engines, reviewers, helpers, sub-agents)."""
    try:
        t = getattr(a, "_task", None)
        if t is None:
            return None
        return sum(int(b.get("calls") or 0) for k, b in t.totals()["by_source"].items() if k != "chat")
    except Exception:  # noqa: BLE001
        return None


def log_turn(session, route, usage_before: dict, ev0: int | None = None, turn: int | None = None) -> None:
    a = session.agent
    u = dict(getattr(a, "usage", {}) or {})
    d = {k: (u.get(k, 0) or 0) - (usage_before.get(k, 0) or 0) for k in ("in", "cached", "out", "calls")}
    unpriced = int((u.get("unpriced_calls", 0) or 0) - (usage_before.get("unpriced_calls", 0) or 0))
    cost = None                                   # None: no call of this turn has a known price
    if "cost" in u and d["calls"] > unpriced:
        cost = round(float(u.get("cost") or 0) - float(usage_before.get("cost") or 0), 6)
    evs = getattr(session, "events", None) or []
    ev = list(evs[ev0:]) if ev0 is not None else list(evs[-30:])    # this turn's events only (old window: no ev0)
    model = a.current_model() if hasattr(a, "current_model") else getattr(a, "model", None)
    _append({"ts": round(time.time(), 1), "chat": session.id, "turn": turn, "spec": getattr(route, "spec", None),
             "lane": getattr(route, "lane", None), "language": getattr(route, "language", None),
             "chat_spec": getattr(getattr(a, "route", None), "spec", None), "harness": session.harness.get("id"),
             "model": model, "secs": round(time.time() - session.t_turn, 1), "tokens_in": d["in"],
             "tokens_cached": d["cached"], "tokens_out": d["out"], "calls": d["calls"],
             "cost_usd": cost, "unpriced_calls": max(unpriced, 0), "nested_calls": _nested_calls(a),
             "reason": (getattr(session, "auto_reason", "") or getattr(route, "harness_reason", "")
                        or getattr(route, "reason", "") or "")[:300],
             "error": any(e.get("type") == "error" for e in ev),
             "stopped": any(e.get("type") == "interrupted" for e in ev),
             "review": _review_of(a)})


def mark(chat: str, outcome: str, turn: int | None = None) -> None:
    """Record what the user did after a turn: undo, retry, thumbs up or down."""
    row = {"ts": round(time.time(), 1), "chat": chat, "outcome": outcome}
    if turn is not None:
        row["turn"] = int(turn)
    _append(row)


def rows(chat: str) -> list[dict]:
    """The logged turn rows (not marks) of one chat."""
    out = []
    try:
        for line in path().read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("chat") == chat and "outcome" not in r:
                out.append(r)
    except OSError:
        pass
    return out


def refit() -> dict:
    """Per spec and harness: turns, failure share, median seconds and output tokens, average cost where measured.

    A mark applies to ONE row: the latest row of that chat (and turn, when the mark names one) logged at or before it.
    """
    rws, marks = [], []
    try:
        for line in path().read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            (marks if "outcome" in r else rws).append(r)
    except OSError:
        return {}
    rws.sort(key=lambda r: r.get("ts") or 0)
    hit: dict[int, list] = {}                                    # id(row) -> its marks, oldest first
    for m in sorted(marks, key=lambda m: m.get("ts") or 0):
        best = None
        for r in rws:
            if r.get("chat") != m.get("chat") or (r.get("ts") or 0) > (m.get("ts") or 0):
                continue
            if m.get("turn") is not None and r.get("turn") != m["turn"]:
                continue
            best = r
        if best is not None:
            hit.setdefault(id(best), []).append(m["outcome"])
    out: dict = {}
    for r in rws:
        k = f"{r.get('spec')}|{r.get('harness')}"
        b = out.setdefault(k, {"turns": 0, "failed": 0, "secs": [], "out": [], "cost": []})
        b["turns"] += 1
        ms = hit.get(id(r), [])
        thumbs = [o for o in ms if o in ("up", "down")]
        bad = (r.get("error") or r.get("stopped") or any(o in ("undo", "retry") for o in ms)
               or (thumbs and thumbs[-1] == "down"))
        b["failed"] += bool(bad)
        b["secs"].append(r.get("secs") or 0)
        b["out"].append(r.get("tokens_out") or 0)
        if isinstance(r.get("cost_usd"), (int, float)):
            b["cost"].append(float(r["cost_usd"]))
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0   # noqa: E731
    return {k: {"turns": v["turns"], "fail_rate": round(v["failed"] / v["turns"], 3), "median_secs": med(v["secs"]),
                "median_out_tokens": med(v["out"]),
                **({"avg_cost_usd": round(sum(v["cost"]) / len(v["cost"]), 6)} if v["cost"] else {})}
            for k, v in out.items()}
