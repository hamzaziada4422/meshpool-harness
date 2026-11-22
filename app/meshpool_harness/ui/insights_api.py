"""HTTP handlers for insights. ui/server.py calls handle_get / handle_post for paths under /api/insights and sends
the (status, body) it gets back as JSON.

  GET  /api/insights?where=home | where=chat:<id> | where=turn:<id>     cards for that boundary
  POST /api/insights/act {id, event}                                    shown | accepted | dismissed | muted
  GET  /api/insights/settings                                           switches, per-type rates, history, notices
  POST /api/insights/settings {insights_on?, learn_from_edits?, quiet_hours?, type?, on?, action?, id?}
       action: "reset" (forget learning), "undo_mute" (id), "ack_notices"
"""
from __future__ import annotations

from urllib.parse import parse_qs

from meshpool_harness import insights as I


def handles(path: str) -> bool:
    return path == "/api/insights" or path.startswith("/api/insights/")


def handle_get(path: str, query: str | dict = "") -> tuple[int, dict]:
    q = parse_qs(query) if isinstance(query, str) else {k: v if isinstance(v, list) else [v] for k, v in query.items()}
    if path == "/api/insights":
        where = (q.get("where") or ["home"])[0]
        return 200, {"cards": I.cards_for(where), "enabled": I.enabled(), "quiet": I.quiet_now()}
    if path == "/api/insights/settings":
        return 200, I.settings_view()
    return 404, {"error": "Not found."}


def handle_post(path: str, body: dict) -> tuple[int, dict]:
    body = body or {}
    try:
        if path == "/api/insights/act":
            r = I.act(str(body.get("id", "")), str(body.get("event", "")))
            return (200 if r.get("ok") else 404), r
        if path == "/api/insights/settings":
            act = body.get("action")
            if act == "reset":
                return 200, I.reset_learning()
            if act == "undo_mute":
                r = I.undo_mute(str(body.get("id", "")))
                return (200 if r["ok"] else 404), dict(I.settings_view(), **r)
            if act == "ack_notices":
                I.ack_notices()
                return 200, I.settings_view()
            kw = {k: body[k] for k in ("insights_on", "learn_from_edits", "quiet_hours") if k in body}
            if kw:
                I.save_settings(**kw)
            if "type" in body:
                I.set_type(str(body["type"]), bool(body.get("on", True)))
            return 200, I.settings_view()
    except ValueError as e:
        return 400, {"error": str(e)}
    return 404, {"error": "Not found."}
