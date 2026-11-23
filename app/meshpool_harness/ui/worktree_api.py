"""Isolated copies (worktrees) in the app.

GET  /api/worktree?workdir=<folder>     -> {repo, users, suggest}: open chats already working in that folder, so a new
                                           Code chat there is offered an isolated copy
GET  /api/worktree/list                 -> {items}: every copy
GET  /api/worktree/<chat>               -> {worktree, diff}: the chat's copy and what it changed
POST /api/worktree/<chat> {action}      -> merge: bring the changes into the folder (conflicts and uncommitted files in
                                           the way are reported, nothing is overwritten); discard: throw the changes
                                           away and start the copy again from the folder
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs


def _chat_copy(sid: str) -> dict:
    from meshpool_harness import worktrees as WT
    from meshpool_harness.ui import agent_api as A
    meta = A.read_meta(sid)
    wid = (meta.get("worktree") or {}).get("id")
    if not wid:
        raise KeyError("This chat does not work in an isolated copy.")
    return WT.get(wid)


def handle(method: str, u, body: bytes = b""):
    from meshpool_harness import worktrees as WT
    from meshpool_harness.ui import agent_api as A
    q = parse_qs(u.query)
    if u.path == "/api/worktree" and method == "GET":
        folder = (q.get("workdir") or [""])[0].strip()
        if not folder:
            return 400, {"error": "workdir is required"}
        users = A.worktree_users(folder, exclude=(q.get("exclude") or [""])[0])
        return 200, {"repo": WT.is_git(folder), "users": users, "suggest": bool(users)}
    if u.path == "/api/worktree/list":
        return 200, {"items": [WT.summary(r) for r in WT.listing()]}
    m = re.fullmatch(r"/api/worktree/([\w-]+)", u.path)
    if not m:
        return 404, {"error": "not found"}
    sid = m.group(1)
    try:
        rec = _chat_copy(sid)
    except KeyError as e:
        return 404, {"error": str(e.args[0] if e.args else e)}
    if method == "GET":
        try:
            d = WT.diff(rec["id"])
        except WT.WorktreeError as e:
            return 200, {"worktree": WT.summary(rec), "diff": None, "error": str(e)}
        return 200, {"worktree": WT.summary(rec), "diff": d}
    b = json.loads(body.decode("utf-8")) if body else {}
    act = b.get("action")
    s = A.LIVE.get(sid)
    if s is not None and s.state in ("working", "waiting"):
        return 409, {"error": "Wait for the chat to finish (or stop it) first."}
    if act == "merge":
        r = WT.merge(rec["id"])
        return 200, dict(r, diff=WT.diff(rec["id"]))
    if act == "discard":
        r = WT.reset(rec["id"])
        return 200, dict(r, diff=WT.diff(rec["id"]))
    return 400, {"error": "Unknown action: use merge or discard."}
