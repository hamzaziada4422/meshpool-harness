"""Routes added with the feature-ledger work. One small handler per feature, registered in routes_extra.AREAS.

  GET  /api/trust?root=PATH        {"root", "trusted", "risky": [files that run code or skip questions]}
  POST /api/trust {"root", "action": "trust" | "revoke"}
  GET  /api/worktree?root=PATH     {"worktrees": [{name, path, branch, dirty, unpushed}]}
  POST /api/worktree {"root", "action": "create", "name"} | {"root", "action": "remove", "path", "force"}
  GET  /api/office                 the Excel, PowerPoint and Word add-in: certificate, manifest, sideloaded, running
  POST /api/office {"action": "install" | "uninstall", "apps": [...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs


def _root(raw: str) -> str:
    p = Path(str(raw or "")).expanduser()
    if not raw or not p.is_dir():
        raise ValueError("Folder not found.")
    return str(p.resolve())


def trust_get(q: dict) -> tuple[int, dict]:
    from meshpool_harness import trust as TR
    return 200, TR.status(_root((q.get("root") or [""])[0]))


def trust_post(b: dict) -> tuple[int, dict]:
    from meshpool_harness import trust as TR
    root = _root(b.get("root"))
    act = b.get("action") or "trust"
    if act == "trust":
        TR.trust(root)
    elif act == "revoke":
        TR.revoke(root)
    else:
        raise ValueError("action is trust or revoke")
    return 200, TR.status(root)


def worktree_get(q: dict) -> tuple[int, dict]:
    from meshpool_harness import worktrees as WT
    try:
        return 200, {"worktrees": WT.listing(_root((q.get("root") or [""])[0]))}
    except WT.WorktreeError as e:
        raise ValueError(str(e))


def worktree_post(b: dict) -> tuple[int, dict]:
    from meshpool_harness import worktrees as WT
    root = _root(b.get("root"))
    try:
        if (b.get("action") or "create") == "create":
            return 200, WT.create(root, str(b.get("name") or ""))
        if b.get("action") == "remove":
            return 200, WT.remove(_root(b.get("path")), force=bool(b.get("force")))
    except WT.WorktreeError as e:
        raise ValueError(str(e))
    raise ValueError("action is create or remove")


def office_get() -> tuple[int, dict]:
    import sys
    from meshpool_harness import office as OF
    from meshpool_harness.ui import server as S
    return 200, dict(OF.status(), running=bool(S.OFFICE_HTTPD), desktop_office=sys.platform in ("win32", "darwin"),
                     trust_note=OF.TRUST_NOTE)


def office_post(b: dict) -> tuple[int, dict]:
    """The click on Settings' button is the user's say-so to trust the certificate (the system shows its own
    confirmation too: Windows asks before adding a root, macOS asks for the login password)."""
    from meshpool_harness import office as OF
    from meshpool_harness.ui import server as S
    act = b.get("action") or "install"
    try:
        if act == "install":
            lines = OF.install([str(a) for a in (b.get("apps") or list(OF.APPS))], confirm=lambda cmd: True)
            if not S.OFFICE_HTTPD:
                S._start_office()
        elif act == "uninstall":
            lines = OF.uninstall()
            for srv in list(S.OFFICE_HTTPD):
                srv.shutdown()
                srv.server_close()
            S.OFFICE_HTTPD.clear()
        else:
            raise ValueError("action is install or uninstall")
    except OF.OfficeError as e:
        raise ValueError(str(e))
    return 200, dict(office_get()[1], lines=lines)


ROUTES = {("GET", "/api/office"): lambda u, b: office_get(),
          ("POST", "/api/office"): lambda u, b: office_post(json.loads(b.decode("utf-8")) if b else {}),
          ("GET", "/api/worktree"): lambda u, b: worktree_get(parse_qs(u.query)),
          ("POST", "/api/worktree"): lambda u, b: worktree_post(json.loads(b.decode("utf-8")) if b else {}),
          ("GET", "/api/trust"): lambda u, b: trust_get(parse_qs(u.query)),
          ("POST", "/api/trust"): lambda u, b: trust_post(json.loads(b.decode("utf-8")) if b else {})}


def handle(method: str, u, body: bytes = b""):
    fn = ROUTES.get((method, u.path))
    if not fn:
        return 404, {"error": "not found"}
    return fn(u, body)
