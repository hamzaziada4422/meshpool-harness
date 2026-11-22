"""GET /api/scheduler -> {available, installed}; POST /api/scheduler {on: true|false} registers or removes the operating
system job that runs due tasks while Meshpool is closed (scheduler_os.py)."""
from __future__ import annotations

import json


def handle(method: str, u, body: bytes = b""):
    from meshpool_harness import scheduler_os as SO
    if u.path != "/api/scheduler":
        return 404, {"error": "not found"}
    if method == "POST":
        b = json.loads(body.decode("utf-8")) if body else {}
        try:
            msg = SO.install() if b.get("on") else SO.uninstall()
        except RuntimeError as e:
            return 400, {"error": str(e)}
        return 200, {"available": SO.available(), "installed": SO.installed(), "message": msg}
    return 200, {"available": SO.available(), "installed": SO.installed()}
