"""Routes added in 0.0.10, one module per area. server.py sends every path in EXTRA_PREFIXES here after its token
check. Each module answers with handle(method, url, body) -> (status, payload[, content type]); an area whose module
is missing answers 404, so a partial install never breaks the app."""
from __future__ import annotations

import importlib

AREAS = (("/api/memory", "memory_api"), ("/api/xray", "memory_api"), ("/api/insights", "insights_api"),
         ("/api/work", "work_api"), ("/api/summaries", "work_api"), ("/api/receipt", "receipt_api"),
         ("/api/checks", "receipt_api"), ("/api/firstrun", "firstrun_api"), ("/api/away", "firstrun_api"))


def handle(method: str, u, body: bytes = b""):
    for prefix, mod in AREAS:
        if u.path.startswith(prefix):
            try:
                m = importlib.import_module("meshpool_harness.ui." + mod)
            except ImportError:
                return 404, {"error": "not available"}
            if hasattr(m, "handle"):
                return m.handle(method, u, body)
            from urllib.parse import parse_qs
            import json
            if method == "GET":
                res = m.handle_get(u.path, parse_qs(u.query))
            else:
                res = m.handle_post(u.path, json.loads(body.decode("utf-8")) if body else {})
            st, out = res[0], res[1]
            if isinstance(out, dict) and "_bytes" in out:          # a file answer (the recap image)
                extra = {"Content-Disposition": f'attachment; filename="{out.get("filename", "file")}"'} if out.get("download") else {}
                return st, out["_bytes"], out.get("content_type", "application/octet-stream"), extra
            return st, out
    return 404, {"error": "not found"}
