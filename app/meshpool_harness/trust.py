"""Folder trust: a project's own hooks and "always allow" rules run only after the user trusts the folder.

A cloned repository can ship `.meshpool/hooks.json` (commands that run on agent events), `.meshpool/permissions.json`
(allow rules that skip the approval question) and `.mcp.json` (MCP servers, which are programs). Each would let a
stranger's repo run commands on this computer, so they are ignored until the user says the folder is theirs. Deny rules always apply:
they only make the agent more careful. Instruction files (MESHPOOL.md, AGENTS.md, CLAUDE.md) and custom commands
are text for the model, not code, and are not gated.

Trusted folders are kept in <MESHPOOL_HOME>/trusted-folders.json. Trusting a folder trusts everything inside it.
Repositories the app clones into its data folder (workspaces) are not trusted until the user says so.
MH_TRUST_PROJECT=1 trusts the current project for one run (CI and scripts).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from meshpool_harness import config as C


def _store() -> Path:
    return C.home() / "trusted-folders.json"


def _load() -> dict:
    d = _load_own()
    try:                                                  # folders trusted with /trust before 0.0.17 (one list)
        old = json.loads((C.home() / "trusted_projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = []
    for f in old if isinstance(old, list) else []:
        if isinstance(f, str) and f:
            d["folders"].setdefault(_norm(f), {"at": 0})
    return d


def _load_own() -> dict:
    try:
        d = json.loads(_store().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("folders"), dict) else {"folders": {}}
    except (OSError, ValueError):
        return {"folders": {}}


def _norm(p) -> str:
    return os.path.normcase(str(Path(p).expanduser().resolve()))


def _save(d: dict) -> None:
    f = _store()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def _hooks_risky(raw: bytes) -> bool:
    h = _json(raw)
    return isinstance(h, dict) and any(isinstance(v, list) and v for v in h.values())


def _perms_risky(raw: bytes) -> bool:
    p = _json(raw)
    return isinstance(p, dict) and bool(p.get("allow"))


def _mcp_risky(raw: bytes) -> bool:
    m = _json(raw)
    return isinstance(m, dict) and bool(m.get("mcpServers") or m.get("servers"))


def risky_files(root) -> list[str]:
    """The project files that would run code or skip questions, relative to the folder. Empty when there are none.
    The chat view asks on every poll, so each file's answer is kept while the file is unchanged."""
    from meshpool_harness import fsio
    out = []
    for rel, fn in ((".meshpool/hooks.json", _hooks_risky), (".meshpool/permissions.json", _perms_risky),
                    (".mcp.json", _mcp_risky), (".meshpool/mcp.json", _mcp_risky)):
        if fsio.parse_cached(Path(root) / rel, fn, "trust-" + fn.__name__, missing=False):
            out.append(rel)
    return out


def _own_folders(raw: bytes) -> tuple:
    d = _json(raw)
    return tuple(d["folders"]) if isinstance(d, dict) and isinstance(d.get("folders"), dict) else ()


def _old_folders(raw: bytes) -> tuple:
    old = _json(raw)
    out = []
    for f in old if isinstance(old, list) else []:
        if isinstance(f, str) and f:
            try:
                out.append(_norm(f))
            except (OSError, RuntimeError):
                continue
    return tuple(out)


def _trusted_folders() -> tuple:
    """What _load()["folders"] holds, for reading only. is_trusted runs on every tool call (hooks, permission rules)
    and every chat poll: both stores are parsed again only when they change."""
    from meshpool_harness import fsio
    return (fsio.parse_cached(_store(), _own_folders, "trust-own", missing=())
            + fsio.parse_cached(C.home() / "trusted_projects.json", _old_folders, "trust-old", missing=()))


def is_trusted(root) -> bool:
    """True when this folder or a folder above it was trusted (or MH_TRUST_PROJECT=1 for this run)."""
    if os.environ.get("MH_TRUST_PROJECT") == "1":
        return True
    try:
        r = _norm(root)
    except (OSError, RuntimeError):
        return False
    if r == _norm(C.home()):                              # Meshpool's own data folder, not the repos it clones there
        return True
    for f in _trusted_folders():
        if r == f or r.startswith(f.rstrip(os.sep) + os.sep):
            return True
    return False


def needs_prompt(root) -> list[str]:
    """Risky files that are being ignored because the folder is not trusted yet."""
    return [] if is_trusted(root) else risky_files(root)


def trust(root) -> None:
    d = _load()
    d["folders"][_norm(root)] = {"at": int(time.time())}
    _save(d)


def revoke(root) -> bool:
    d = _load()
    gone = d["folders"].pop(_norm(root), None) is not None
    if gone:
        _save(d)
    old_f = C.home() / "trusted_projects.json"            # and from the list /trust kept before 0.0.17
    try:
        old = json.loads(old_f.read_text(encoding="utf-8"))
        if isinstance(old, list):
            keep = [x for x in old if not (isinstance(x, str) and _norm(x) == _norm(root))]
            if len(keep) != len(old):
                old_f.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    except (OSError, ValueError):
        pass
    return gone


def folders() -> list[str]:
    return sorted(_load()["folders"])


def status(root) -> dict:
    return {"root": str(root), "trusted": is_trusted(root), "risky": risky_files(root)}


def message(root) -> str:
    files = needs_prompt(root)
    if not files:
        return ""
    return (f"This folder has {' and '.join(files)}, which can run commands or skip approval questions. They are "
            "ignored until you trust the folder (trusting it also trusts its subfolders).")
