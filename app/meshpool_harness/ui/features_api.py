"""Routes added with the feature-ledger work. One small handler per feature, registered in routes_extra.AREAS.

  GET  /api/trust?root=PATH        {"root", "trusted", "risky": [files that run code or skip questions]}
  POST /api/trust {"root", "action": "trust" | "revoke"}
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


RULE_LISTS = ("allow", "ask", "deny")


def _rules_file() -> Path:
    from meshpool_harness import config as C
    return C.home() / "permissions.json"


def _rules_read() -> dict:
    f = _rules_file()
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError(f"{f} is not valid JSON, so it is left as it is. Fix or remove it, then try again.")
    if not isinstance(d, dict):
        raise ValueError(f"{f} is not a rules file.")
    return d


def rules_get() -> tuple[int, dict]:
    """Every rule in the user's own permissions.json: allow, ask and deny, and what was allowed per folder."""
    d = _rules_read()
    folders = [{"folder": k, "allow": [str(x) for x in (v.get("allow") or []) if isinstance(x, str)]}
               for k, v in sorted((d.get("projects") or {}).items()) if isinstance(v, dict)]
    return 200, {"path": str(_rules_file()), **{k: [str(x) for x in (d.get(k) or []) if isinstance(x, str)]
                                                for k in RULE_LISTS}, "folders": [f for f in folders if f["allow"]]}


def rules_post(b: dict) -> tuple[int, dict]:
    """{"action": "add" | "remove", "list" (or "kind"): "allow" | "ask" | "deny", "rule", "folder" (remove from a
    folder)}"""
    from meshpool_harness.agent import core as K
    with K.RULES_LOCK:
        return _rules_post(b)


def _rules_post(b: dict) -> tuple[int, dict]:
    from meshpool_harness.agent import core as K
    from meshpool_harness.core import permrules as PR
    act, lst, raw = b.get("action"), b.get("list") or b.get("kind"), str(b.get("rule") or "")   # "kind": main's name
    rule = raw.strip()
    if lst not in RULE_LISTS or act not in ("add", "remove") or not rule:
        raise ValueError("Give an action (add or remove), a list (allow, ask or deny) and a rule.")
    if act == "add" and PR.check_rule(raw):
        raise ValueError(PR.check_rule(raw))
    d = _rules_read()
    if b.get("folder"):
        layer = ((d.get("projects") or {}).get(str(b["folder"])) or {})
        if act != "remove" or lst != "allow":
            raise ValueError("Folder rules can only be removed here.")
    else:
        layer = d
    have = layer.setdefault(lst, []) if (act == "add" or lst in layer) else []
    if act == "add" and rule not in have:
        have.append(rule)
    elif act == "remove":
        if rule not in have:
            raise ValueError("That rule is not there any more.")
        have.remove(rule)
    try:
        K.write_rules_file(_rules_file(), d)
    except OSError as e:
        raise ValueError(f"Could not save the rules: {e}")
    return rules_get()


def office_get() -> tuple[int, dict]:
    import sys
    from meshpool_harness import office as OF
    from meshpool_harness.ui import server as S
    return 200, dict(OF.status(), running=bool(S.OFFICE_HTTPD), desktop_office=sys.platform in ("win32", "darwin"),
                     trust_note=OF.TRUST_NOTE)


def _cert_bytes(OF) -> bytes:
    """The certificate the Office listener would load now (empty when there is none)."""
    cp = OF.cert_paths()
    try:
        return cp[0].read_bytes() + cp[1].read_bytes() if cp else b""
    except OSError:
        return b""


def _stop_office(S) -> None:
    for srv in list(S.OFFICE_HTTPD):
        srv.shutdown()
        srv.server_close()
    S.OFFICE_HTTPD.clear()


def office_post(b: dict) -> tuple[int, dict]:
    """The click on Settings' button is the user's say-so to trust the certificate (the system shows its own
    confirmation too: Windows asks before adding a root, macOS asks for the login password)."""
    from meshpool_harness import office as OF
    from meshpool_harness.ui import server as S
    act = b.get("action") or "install"
    try:
        if act == "install":
            before = _cert_bytes(OF)
            lines = OF.install([str(a) for a in (b.get("apps") or list(OF.APPS))], confirm=lambda cmd: True)
            if S.OFFICE_HTTPD and _cert_bytes(OF) != before:   # renewed: the listener still holds the old one
                _stop_office(S)
            if not S.OFFICE_HTTPD:
                S._start_office()
        elif act == "uninstall":
            lines = OF.uninstall()
            _stop_office(S)
        else:
            raise ValueError("action is install or uninstall")
    except OF.OfficeError as e:
        raise ValueError(str(e))
    return 200, dict(office_get()[1], lines=lines)


ROUTES = {("GET", "/api/rules"): lambda u, b: rules_get(),
          ("POST", "/api/rules"): lambda u, b: rules_post(json.loads(b.decode("utf-8")) if b else {}),
          ("GET", "/api/office"): lambda u, b: office_get(),
          ("POST", "/api/office"): lambda u, b: office_post(json.loads(b.decode("utf-8")) if b else {}),
          ("GET", "/api/trust"): lambda u, b: trust_get(parse_qs(u.query)),
          ("POST", "/api/trust"): lambda u, b: trust_post(json.loads(b.decode("utf-8")) if b else {})}


def handle(method: str, u, body: bytes = b""):
    fn = ROUTES.get((method, u.path))
    if not fn:
        return 404, {"error": "not found"}
    return fn(u, body)
