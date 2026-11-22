"""The one place that decides which LibreOffice Meshpool runs.

The installed app never uses a LibreOffice the customer happens to have (PATH, /Applications, Program Files): it
ships its own, inside the installer, and nothing is downloaded at runtime. Order, first hit wins:

  1. MH_SOFFICE            explicit override (developers, CI). Path to the soffice executable.
  2. the bundled copy      <app>/vendor/libreoffice (packaging/build_all.py puts it there from the pinned release).
  3. the system copy       ONLY outside the packaged app (source checkout, command-line install) and only when
                           MH_ALLOW_SYSTEM_SOFFICE=1. Never in the packaged app.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

VERSION = "26.2.7"          # the release packaging/bundled_tools.py bundles (tests/test_bundled_tools.py keeps them equal)
_EXE = {"darwin": "LibreOffice.app/Contents/MacOS/soffice", "win32": "program/soffice.exe", "linux": "program/soffice"}


def is_packaged() -> bool:
    """True inside the installed app (Meshpool.app, the Windows install, or any frozen build)."""
    if getattr(sys, "frozen", False):
        return True
    from meshpool_harness import config
    return config.packaged()


def bundled_dir() -> Path:
    from meshpool_harness import config
    return config.APP / "vendor" / "libreoffice"


def _bundled_exe() -> str | None:
    rel = _EXE.get(sys.platform)
    if rel:
        p = bundled_dir() / rel
        if p.is_file():
            return str(p)
    return None


def _system_copy() -> str | None:
    hit = shutil.which("soffice") or shutil.which("libreoffice")
    if hit:
        return hit
    cands: list[Path] = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"):
            if base:
                cands.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        cands.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    return next((str(c) for c in cands if c.exists()), None)


def resolve() -> tuple[str | None, str]:
    """(path, source). source is MH_SOFFICE, bundled, system (developer opt-in), or "" when there is none."""
    override = os.environ.get("MH_SOFFICE")
    if override and Path(override).is_file():
        return override, "MH_SOFFICE"
    hit = _bundled_exe()
    if hit:
        return hit, "bundled"
    if not is_packaged() and os.environ.get("MH_ALLOW_SYSTEM_SOFFICE") == "1":
        hit = _system_copy()
        if hit:
            return hit, "system (MH_ALLOW_SYSTEM_SOFFICE=1)"
    return None, ""


def find_soffice() -> str | None:
    return resolve()[0]


def describe() -> dict:
    """For the doctor row: {path, source, note}."""
    path, source = resolve()
    if path:
        note = {"bundled": "the copy that ships with Meshpool", "MH_SOFFICE": "set by MH_SOFFICE"}.get(source, source)
        return {"path": path, "source": source, "note": note}
    if is_packaged():
        return {"path": None, "source": "", "note": "missing from this install (Meshpool never uses a LibreOffice "
                "installed on your computer); reinstall Meshpool"}
    return {"path": None, "source": "", "note": "not found (source checkout: set MH_SOFFICE, or "
            "MH_ALLOW_SYSTEM_SOFFICE=1 to use the system copy)"}


def missing_reason() -> str:
    return describe()["note"]
