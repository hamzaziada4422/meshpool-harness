"""Per-reply receipts and feedback.

    GET  /api/receipt?chat=<id>            the receipt of the chat's last turn (time, tokens, agent and why)
    POST /api/receipt/feedback {chat, outcome}   up, down or retry: what the user did with a reply. Kept on this
                                                  computer in the outcome log; a "down" or "retry" counts against the
                                                  agent that wrote it when routing is refit.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

OUTCOMES = {"up", "down", "retry"}


def handle(method: str, u, body: bytes = b""):
    if u.path == "/api/receipt" and method == "GET":
        from meshpool_harness import receipts as RC
        chat = (parse_qs(u.query).get("chat") or [""])[0]
        return 200, {"receipt": RC.receipt_for(chat) if chat else None}
    if u.path == "/api/receipt/feedback" and method == "POST":
        b = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(b, dict):
            return 400, {"error": "The request must be a JSON object."}
        chat, outcome = str(b.get("chat") or ""), str(b.get("outcome") or "")
        if not chat or outcome not in OUTCOMES:
            return 400, {"error": "Say which chat and whether it was up, down or retry."}
        from meshpool_harness.ui import agent_api as A
        if not A.exists(chat):
            return 404, {"error": "That chat no longer exists."}
        from meshpool_harness.hrouter import outcomes as OUT
        t = b.get("turn")
        OUT.mark(chat, outcome, int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None)
        return 200, {"ok": True}
    return 404, {"error": "not found"}
