"""The outcome log: what each routed turn cost and whether it held up, so the routing table is refit from facts.

One JSON line per turn in <Meshpool data folder>/routing/outcomes.jsonl:
  {ts, chat, spec, lane, language, harness, model, secs, tokens_in, tokens_cached, tokens_out, calls, reason, error,
  stopped}

A later undo, retry or "that's wrong" on the same chat marks the turn as failed (mark()). Nothing leaves the
computer. refit() summarises the log per spec and harness: the numbers a routing.json update starts from.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from meshpool_harness import config as C

_LOCK = threading.Lock()
MAX_BYTES = 20 * 1024 * 1024


def path() -> Path:
    return C.home() / "routing" / "outcomes.jsonl"


def _append(row: dict) -> None:
    p = path()
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > MAX_BYTES:          # keep the newest half
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            p.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
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


def log_turn(session, route, usage_before: dict) -> None:
    a = session.agent
    u = dict(getattr(a, "usage", {}) or {})
    d = {k: (u.get(k, 0) or 0) - (usage_before.get(k, 0) or 0) for k in ("in", "cached", "out", "calls")}
    ev = session.events[-30:] if getattr(session, "events", None) else []
    model = a.current_model() if hasattr(a, "current_model") else getattr(a, "model", None)
    _append({"ts": round(time.time(), 1), "chat": session.id, "spec": getattr(route, "spec", None),
             "lane": getattr(route, "lane", None), "language": getattr(route, "language", None),
             "chat_spec": getattr(getattr(a, "route", None), "spec", None), "harness": session.harness.get("id"),
             "model": model, "secs": round(time.time() - session.t_turn, 1), "tokens_in": d["in"],
             "tokens_cached": d["cached"], "tokens_out": d["out"], "calls": d["calls"],
             "reason": (getattr(session, "auto_reason", "") or getattr(route, "harness_reason", "")
                        or getattr(route, "reason", "") or "")[:300],
             "error": any(e.get("type") == "error" for e in ev),
             "stopped": any(e.get("type") == "interrupted" for e in ev),
             "review": _review_of(a),
             **({"cost": round(float(u["cost"]) - float(usage_before.get("cost", 0) or 0), 6)}
                if isinstance(u.get("cost"), (int, float)) else {})})


def mark(chat: str, outcome: str) -> None:
    """Record what the user did after a turn: undo, retry, thumbs up or down."""
    _append({"ts": round(time.time(), 1), "chat": chat, "outcome": outcome})


def refit() -> dict:
    """Per spec and harness: turns, failure share, median seconds and output tokens."""
    rows, marks = [], {}
    try:
        for line in path().read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            (marks.setdefault(r["chat"], []).append(r) if "outcome" in r else rows.append(r))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for r in rows:
        k = f"{r.get('spec')}|{r.get('harness')}"
        b = out.setdefault(k, {"turns": 0, "failed": 0, "secs": [], "out": []})
        b["turns"] += 1
        bad = r.get("error") or r.get("stopped") or any(
            m["outcome"] in ("undo", "retry", "down") and m["ts"] >= r["ts"] for m in marks.get(r["chat"], []))
        b["failed"] += bool(bad)
        b["secs"].append(r.get("secs") or 0)
        b["out"].append(r.get("tokens_out") or 0)
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0   # noqa: E731
    return {k: {"turns": v["turns"], "fail_rate": round(v["failed"] / v["turns"], 3), "median_secs": med(v["secs"]),
                "median_out_tokens": med(v["out"])} for k, v in out.items()}
