"""Folder trust: a project's own hooks and "always allow" rules run only after the user trusts the folder.

A cloned repository can ship `.meshpool/hooks.json` (commands that run on agent events) and
`.meshpool/permissions.json` (allow rules that skip the approval question). Both would let a stranger's repo run
commands on this computer, so they are ignored until the user says the folder is theirs. Deny rules always apply:
they only make the agent more careful. Instruction files (MESHPOOL.md, AGENTS.md, CLAUDE.md) and custom commands
are text for the model, not code, and are not gated.

Trusted folders are kept in <MESHPOOL_HOME>/trusted-folders.json. Trusting a folder trusts everything inside it.
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


def risky_files(root) -> list[str]:
    """The project files that would run code or skip questions, relative to the folder. Empty when there are none."""
    out = []
    base = Path(root) / ".meshpool"
    try:
        h = json.loads((base / "hooks.json").read_text(encoding="utf-8"))
        if isinstance(h, dict) and any(isinstance(v, list) and v for v in h.values()):
            out.append(".meshpool/hooks.json")
    except (OSError, ValueError):
        pass
    try:
        p = json.loads((base / "permissions.json").read_text(encoding="utf-8"))
        if isinstance(p, dict) and p.get("allow"):
            out.append(".meshpool/permissions.json")
    except (OSError, ValueError):
        pass
    return out


def is_trusted(root) -> bool:
    """True when this folder or a folder above it was trusted (or MH_TRUST_PROJECT=1 for this run)."""
    if os.environ.get("MH_TRUST_PROJECT") == "1":
        return True
    try:
        r = _norm(root)
    except (OSError, RuntimeError):
        return False
    home = _norm(C.home())
    if r == home or r.startswith(home + os.sep):          # Meshpool's own data folder
        return True
    for f in _load()["folders"]:
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
            "ignored until you trust the folder.")
