"""The per-task receipt: one quiet line under a finished reply. Time, cost, and which agent and model Auto chose, with
the reason. Every number comes from the outcome log (hrouter/outcomes.py); nothing is estimated or invented.

Cost: the outcome row carries the turn's cost from the call ledger (each call priced for its own model, nested engine
calls included) and the number of calls with no known price. A call with no known price is never shown as $0. Rows
written before that existed fall back to the user's own prices (MH_PRICE_IN, MH_PRICE_OUT and optionally
MH_PRICE_CACHED, USD per million tokens), else the receipt says the cost was not measured.
"""
from __future__ import annotations

import re

import json
import os

from meshpool_harness.hrouter import outcomes as OUT


def _price() -> tuple[float, float, float] | None:
    try:
        from meshpool_harness import config as C
        cfg = C.read()
    except Exception:  # noqa: BLE001
        cfg = {}

    def get(k):
        return os.environ.get(k) or cfg.get(k) or ""
    try:
        pi, po = float(get("MH_PRICE_IN")), float(get("MH_PRICE_OUT"))
        pc = float(get("MH_PRICE_CACHED") or pi)
    except ValueError:
        return None
    return pi, po, pc


def cost_of(tokens_in: int, tokens_cached: int, tokens_out: int) -> float | None:
    """USD from the user's own prices, or None when none are set."""
    pr = _price()
    if not pr:
        return None
    pi, po, pc = pr
    return round((max(tokens_in - tokens_cached, 0) * pi + tokens_cached * pc + tokens_out * po) / 1e6, 4)


def turns(chat_id: str) -> list[dict]:
    """The logged turns of one chat, oldest first. Mark rows (undo, retry, wrong) are not turns."""
    out = []
    try:
        for line in OUT.path().read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("chat") == chat_id and "outcome" not in r:
                out.append(r)
    except OSError:
        pass
    return out


def _harness_name(hid: str) -> str:
    if not hid:
        return ""
    try:
        from meshpool_harness import harnesses as H
        return H.get(hid).get("name") or hid
    except Exception:  # noqa: BLE001
        return hid


def _reason_from_events(chat_id: str) -> str:
    try:
        from meshpool_harness.ui import hub
        for e in reversed(hub._events(chat_id)):
            a = e.get("auto") if isinstance(e, dict) else None
            if a and a.get("reason"):
                return str(a["reason"])
    except Exception:  # noqa: BLE001
        pass
    return ""


def _fmt_secs(s: float | None) -> str:
    """One duration format, the same as the app's: "<1 s", "42 s", "3 min 45 s", "1 h 5 min"."""
    if s is None:
        return ""
    if s < 1:
        return "<1 s"
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {s % 3600 // 60} min"


def fmt_cost(usd: float) -> str:
    """One cost format: "$0.42", and "<$0.01" for anything under a cent."""
    return "<$0.01" if 0 < usd < 0.01 else f"${usd:.2f}"


_KNOWN = {"deepseek": "DeepSeek", "gpt": "GPT", "qwen": "Qwen", "glm": "GLM", "kimi": "Kimi", "llama": "Llama",
          "mistral": "Mistral", "grok": "Grok", "openai": "OpenAI", "minimax": "MiniMax"}


def model_name(mid: str) -> str:
    """The model as the composer names it: "deepseek-v4.1-flash" reads "DeepSeek V4.1 Flash" (app.js modelName)."""
    head, sep, rest = str(mid or "").partition(":")
    bare = rest if sep and head == "chatgpt" else str(mid or "")
    words = [w for w in re.split(r"[-_ ]+", bare.split("/")[-1]) if w]
    out = " ".join(_KNOWN.get(w.lower()) or (w.upper() if re.match(r"v?\d", w) else w[:1].upper() + w[1:])
                   for w in words)
    return re.sub(r"(\d) (?=\d)", r"\1.", out)


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def receipt_for(chat_id: str, turn_index: int | None = None) -> dict | None:
    """The receipt for one turn (the last one by default; negative indexes count from the end). None when that
    turn was not logged."""
    rows = turns(chat_id)
    if not rows:
        return None
    i = len(rows) - 1 if turn_index is None else (turn_index if turn_index >= 0 else len(rows) + turn_index)
    if not 0 <= i < len(rows):
        return None
    r = rows[i]
    tin, tc, tout = int(r.get("tokens_in") or 0), int(r.get("tokens_cached") or 0), int(r.get("tokens_out") or 0)
    new_row = "cost_usd" in r or "unpriced_calls" in r
    unpriced = int(r.get("unpriced_calls") or 0)
    if new_row:
        cost = r.get("cost_usd") if isinstance(r.get("cost_usd"), (int, float)) else None
    else:
        cost = cost_of(tin, tc, tout)
    reason = r.get("reason") or (_reason_from_events(chat_id) if r.get("harness") else "")
    hid = r.get("harness") or ""
    out = {"chat": chat_id, "turn": i, "ts": r.get("ts"), "secs": r.get("secs"),
           "tokens_in": tin, "tokens_cached": tc, "tokens_out": tout, "calls": int(r.get("calls") or 0),
           "cost": cost, "unpriced_calls": unpriced,
           "cost_label": "" if cost is not None and not unpriced else
           ("cost not known (unpriced model)" if new_row and unpriced and cost is None else
            "" if cost is not None else "not measured"),
           "harness": hid, "harness_name": _harness_name(hid), "model": r.get("model") or "", "reason": reason,
           "spec": r.get("spec"), "error": bool(r.get("error")), "stopped": bool(r.get("stopped")),
           "review": r.get("review") if isinstance(r.get("review"), dict) else None}
    out["text"] = line(out)
    return out


_KINDS = {"docs": "documents", "doc": "documents", "code": "code", "data": "data", "research": "research",
          "slides": "slides", "sheets": "spreadsheets", "general": "general work"}


def plain_reason(reason: str) -> str:
    """The router's note in plain words: "docs words (1)" becomes "chosen for documents"; prose is kept as written."""
    t = (reason or "").strip().rstrip(".")
    if not t:
        return ""
    m = re.match(r"(?i)^(\w+) words?(?: \(\d+\))?(?: in a \w+ chat)?(?:, .*)?$", t)
    if m:
        return "chosen for " + _KINDS.get(m.group(1).lower(), m.group(1).lower())
    # The router's own notes are not for people: "no strong signal" says nothing, and "Best benchmark quality for a
    # data task (csv)" reads as "chosen for data". Prose written for people is kept as it is.
    if re.match(r"(?i)^(no strong signal|general)$", t):
        return ""
    m = re.match(r"(?i)^best benchmark quality for (?:a |an )?(\w+)(?: task)?(?: \(.*\))?$", t)
    if m:
        return "chosen for " + _KINDS.get(m.group(1).lower(), m.group(1).lower())
    return t[:1].lower() + t[1:]


def line(r: dict) -> str:
    """The one line: "42 s · $0.03 · DeepSeek V4.1 Flash, chosen for documents". The model is named as the composer
    names it; an outside agent (Codex, Pi, Hermes) is named before it, meshpool's own agents are not."""
    bits = []
    if r.get("secs") is not None:
        bits.append(_fmt_secs(r["secs"]))
    unpriced = int(r.get("unpriced_calls") or 0)
    if r.get("cost") is not None:
        money = fmt_cost(r["cost"])
        bits.append(money + (f" + {unpriced} unpriced call{'s' if unpriced != 1 else ''}" if unpriced else ""))
    elif unpriced:
        bits.append("cost not known (unpriced model)")
    elif r.get("tokens_in") or r.get("tokens_out"):
        bits.append(f"{_fmt_tokens(r['tokens_in'] + r['tokens_out'])} tokens, cost not measured")
    rv = r.get("review")
    if isinstance(rv, dict) and rv.get("verdict"):
        bits.append("checked independently: " + (
            "could not run" if rv.get("error") else f"{rv.get('met', 0)} of {rv.get('total', 0)} met"
            + (f", {rv['rounds']} fix round{'s' if rv['rounds'] != 1 else ''}" if rv.get("rounds") else "")
            + f" ({_fmt_secs(rv.get('secs'))}, {_fmt_tokens(int(rv.get('tokens') or 0))} tokens)"))
    agent = r.get("harness_name") or ""
    if str(r.get("harness") or agent).lower().startswith("meshpool"):
        agent = ""
    who = " on ".join(x for x in (agent, model_name(r.get("model") or "")) if x)
    if who:
        why = plain_reason(r.get("reason") or "")
        bits.append(who + (f", {why}" if why else ""))
    return " · ".join(bits)
