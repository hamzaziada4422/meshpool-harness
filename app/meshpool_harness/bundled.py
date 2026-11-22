"""The programs Meshpool ships inside its installers, and how the installed app finds them.

Rule: in the packaged app (config.packaged(), or a frozen build) Meshpool uses ONLY its own copy of Node.js, Git, Pi,
Chromium and LibreOffice (office_runtime.py). It never falls back to PATH, npx or a download. Codex and OpenCode are not
shipped at all (harnesses.NOT_SHIPPED). In a source checkout or command-line install nothing changes: PATH and npx
still work.

Layout (packaging/build_all.py), relative to config.APP.parent (macOS: Meshpool.app/Contents/Resources):
  node/            Node.js (bin/node, bin/npx; Windows: node.exe)
  git/             Windows: MinGit (cmd, mingw64, usr/bin).  macOS: bin/git and libexec/git-core
  app/vendor/      hermes, pi (with its node_modules), libreoffice, browsers
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from meshpool_harness import config as C


def packaged() -> bool:
    return bool(getattr(sys, "frozen", False)) or C.packaged()


def root() -> Path:
    return C.APP.parent


def node_dir() -> Path:
    return root() / "node"


def bundled_node() -> str | None:
    for p in (node_dir() / "node.exe", node_dir() / "bin" / "node"):
        if p.is_file():
            return str(p)
    return None


def node_exe() -> str | None:
    """Node: the bundled one; in a source checkout also MH_NODE or PATH. None in the packaged app when it is missing."""
    hit = bundled_node()
    if hit or packaged():
        return hit
    import shutil
    return os.environ.get("MH_NODE") or shutil.which("node")


def git_dirs() -> list[Path]:
    """Folders of the bundled Git that belong on PATH (Windows MinGit has three), existing ones only."""
    g = root() / "git"
    cands = [g / "cmd", g / "mingw64" / "bin", g / "usr" / "bin"] if os.name == "nt" else [g / "bin"]
    return [p for p in cands if p.is_dir()]


def bundled_git() -> str | None:
    for d in git_dirs():
        for n in ("git.exe", "git"):
            if (d / n).is_file():
                return str(d / n)
    return None


def bundled_bash() -> str | None:
    """Windows: the bash inside MinGit (git/usr/bin/bash.exe, which the build copies from MinGit's sh.exe; git/bin/
    bash.exe in a full Git; else sh.exe itself, the same bash running in POSIX mode)."""
    g = root() / "git"
    for p in (g / "usr" / "bin" / "bash.exe", g / "bin" / "bash.exe", g / "usr" / "bin" / "sh.exe"):
        if p.is_file():
            return str(p)
    return None


def use_bundled_tools() -> None:
    """Put the bundled Node and Git first on this process's PATH, so every child (the agents' shell, the Node agents,
    `git` called by name anywhere in Meshpool, MCP servers started with npx) gets Meshpool's own. Idempotent. Only in
    the packaged app; a source checkout keeps the developer's PATH."""
    if not packaged():
        return
    front = [str(p) for p in git_dirs()]
    if node_dir().is_dir():
        front.insert(0, str(node_dir() if os.name == "nt" else node_dir() / "bin"))
    lo = vendor_dir("libreoffice") / ("LibreOffice.app/Contents/MacOS" if sys.platform == "darwin" else "program")
    if lo.is_dir():                                   # so vendored skills that look for `soffice` find ours
        front.append(str(lo))
    cur = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p not in front]
    os.environ["PATH"] = os.pathsep.join(front + cur)


CHROMIUM_NEEDS_MACOS = (13, 0)          # Playwright's Chromium 159 (the one in the app); packaging/mac/check_minos.py exempts it at 13


def browser_needs_newer_macos() -> str:
    """The app opens on macOS 11, but its Chromium needs macOS 13: on a Mac in between, why browser use cannot run
    ("" everywhere else). Web search and page reading do not need a browser."""
    if sys.platform != "darwin" or not packaged():
        return ""
    try:
        v = tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return ""
    if len(v) < 2 or v >= CHROMIUM_NEEDS_MACOS:
        return ""
    return (f"Browser use needs macOS {CHROMIUM_NEEDS_MACOS[0]} or later; this Mac runs macOS {v[0]}.{v[1]}. "
            "web_search, web_research and fetch_url read public pages without a browser.")


def browsers_dir() -> Path:
    """Playwright's Chromium, installed into the app at build time (app/vendor/browsers)."""
    return C.APP / "vendor" / "browsers"


def vendor_dir(name: str) -> Path:
    return C.APP / "vendor" / name
