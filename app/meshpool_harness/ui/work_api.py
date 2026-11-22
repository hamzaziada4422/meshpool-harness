"""HTTP side of Your work and the summaries. server.py hands a path and a query (or a JSON body) to these functions and
sends back what they return: (status, payload). A payload with "_bytes" is a file: send those bytes with its
"content_type" (and a Content-Disposition of "filename" when "download" is set).

GET  /api/work?range=1|3|6|12            Your work for that range
GET  /api/work/recap.png?range=&download=1   the recap card as a PNG
GET  /api/work/recap?range=              the same card as base64, with the counts it was drawn from (preview)
GET  /api/summaries/chat?id=             {id, summary}
GET  /api/summaries/project?id=          the project brief, plus when it was last opened
POST /api/summaries/catchup {id, since?} what happened since the last visit (marks the project opened)
POST /api/summaries/opened {id}          record a visit without a catch-up
"""
from __future__ import annotations

import re

from meshpool_harness import summaries as S
from meshpool_harness import yourwork as YW

ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")


def _q(query, key, default=""):
    v = (query or {}).get(key, default)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    return str(v) if v is not None else default


def _range(query) -> int:
    try:
        n = int(_q(query, "range", "3"))
    except ValueError:
        return 3
    return n if n in YW.RANGES else 3


def handles(path: str) -> bool:
    return path.startswith("/api/work") or path.startswith("/api/summaries/")


def handle_get(path: str, query: dict | None = None) -> tuple[int, dict]:
    query = query or {}
    if path == "/api/work":
        return 200, YW.build(_range(query))
    if path == "/api/work/recap":
        return 200, YW.recap_preview(_range(query))
    if path == "/api/work/recap.png":
        m = _range(query)
        return 200, {"_bytes": YW.recap_png(m), "content_type": "image/png",
                     "filename": f"meshpool-recap-{m}m.png", "download": _q(query, "download") == "1"}
    if path == "/api/summaries/chats":                      # every chat's one-liner, for the sidebar hover
        return 200, {"summaries": {sid: r.get("summary", "") for sid, r in S._all().items() if r.get("summary")}}
    if path in ("/api/summaries/chat", "/api/summaries/project"):
        sid = _q(query, "id")
        if not ID.fullmatch(sid):
            return 400, {"error": "Missing id."}
        if path.endswith("/chat"):
            return 200, {"id": sid, "summary": S.summary_for(sid)}
        brief = S.project_brief(sid)
        if not brief:
            return 404, {"error": "That project does not exist."}
        return 200, dict(brief, last_opened=S.last_opened(sid))
    return 404, {"error": "Not found."}


def handle_post(path: str, body: dict | None = None) -> tuple[int, dict]:
    body = body or {}
    pid = str(body.get("id") or "")
    if path in ("/api/summaries/catchup", "/api/summaries/opened") and not ID.fullmatch(pid):
        return 400, {"error": "Missing id."}
    if path == "/api/summaries/catchup":
        since = body.get("since")
        try:
            since = float(since) if since else None
        except (TypeError, ValueError):
            since = None
        return 200, S.catch_me_up(pid, since)
    if path == "/api/summaries/opened":
        return 200, {"id": pid, "previous": S.touch_opened(pid)}
    return 404, {"error": "Not found."}
