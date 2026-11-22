"""HTTP handlers for insights. ui/server.py calls handle_get / handle_post for paths under /api/insights and sends
the (status, body) it gets back as JSON.

  GET  /api/insights?where=home | where=chat:<id> | where=turn:<id>     cards for that boundary
  POST /api/insights/act {id, event}                                    shown | accepted | dismissed | muted
  GET  /api/insights/settings                                           switches, per-type rates, history, notices
  POST /api/insights/settings {insights_on?, learn_from_edits?, quiet_hours?, type?, on?, action?, id?}
       action: "reset" (forget learning), "undo_mute" (id), "ack_notices"
"""
from __future__ import annotations

import os
import re
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


def draft_skill(body: dict) -> dict:
    """A SKILL.md draft from the chats a `skill` card named, for the user to review and edit before it is saved.
    The fast model writes it when one is reachable; otherwise a plain template from the chats' own first messages."""
    from meshpool_harness import assistant as AS
    ids = set(body.get("chat_ids") or [])
    chats = [c for c in I.load_chats() if c["id"] in ids and not c.get("incognito")][:6]
    if not chats:
        raise ValueError("Those chats are not available any more.")
    hint = AS.dashless(str(body.get("hint") or chats[0]["texts"][0])[:40]).strip(" .")
    name = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-")[:40] or "my-routine"
    asks = [re.sub(r"\s+", " ", c["texts"][0]).strip()[:300] for c in chats if c["texts"]]
    text = None
    if os.environ.get("MH_LEARN", "1") != "0":
        try:
            from meshpool_harness.core.client import LiveClient
            r = LiveClient("skill").chat(model=os.environ.get("MH_MODEL_FAST") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash",
                messages=[{"role": "system", "content": "Write a short SKILL.md body that teaches an assistant a procedure "
                           "the user keeps asking for. Numbered steps, plain sentences, no em dashes, only what the requests "
                           "below show. Return only the body text."},
                          {"role": "user", "content": "\n\n".join(f"Request {i + 1}: {a}" for i, a in enumerate(asks))}],
                max_tokens=600, temperature=0.2, task_id="skill-draft", role="worker")
            text = (r["choices"][0]["message"].get("content") or "").strip()
        except Exception:  # noqa: BLE001
            text = None
    if not text:
        text = ("Use this when the user asks for the same job again.\n\nRequests it came from:\n"
                + "\n".join(f"- {a}" for a in asks) + "\n\nSteps:\n1. Check what the user attached or named.\n"
                "2. Do the job the same way as in those requests.\n3. Show what you checked before you finish.")
    return {"name": name, "description": f"Repeat: {hint}", "body": AS.dashless(text),
            "evidence": [{"chat_id": c["id"], "title": c.get("title", "")} for c in chats]}


def handle_post(path: str, body: dict) -> tuple[int, dict]:
    body = body or {}
    try:
        if path == "/api/insights/os":                          # the one notification a day; None in quiet hours or when used
            return 200, {"card": I.take_os_notification()}
        if path == "/api/insights/draft-skill":
            return 200, draft_skill(body)
        if path == "/api/insights/accept":                       # a teach card: write the rules as learned facts
            from meshpool_harness import teach as T
            pl = ((body.get("action") or {}).get("payload") or {})
            r = T.accept(pl.get("rules") or [], pl.get("file") or "", pl.get("chat_id") or "")
            return 200, {"ok": True, "result": r}
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
