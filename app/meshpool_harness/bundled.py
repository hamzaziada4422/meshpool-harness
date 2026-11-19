"""The programs Meshpool ships inside its installers, and how the installed app finds them.

Rule: in the packaged app (config.packaged(), or a frozen build) Meshpool uses ONLY its own copy of Node.js, Git, the
Node agents (Codex, OpenCode, Pi) and LibreOffice (office_runtime.py). It never falls back to PATH, npx or a download.
In a source checkout or command-line install nothing changes: PATH and npx still work.

Layout (packaging/build_all.py), relative to config.APP.parent (macOS: Meshpool.app/Contents/Resources):
  node/            Node.js (bin/node, bin/npx; Windows: node.exe)
  git/             Windows: MinGit (cmd, mingw64, usr/bin).  macOS: bin/git and libexec/git-core
  app/vendor/      hermes, pi, codex-acp, opencode (each with its own node_modules), libreoffice
"""
from __future__ import annotations

import json
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
    """Windows: the bash inside MinGit (git/usr/bin/bash.exe, or git/bin/bash.exe)."""
    g = root() / "git"
    for p in (g / "usr" / "bin" / "bash.exe", g / "bin" / "bash.exe"):
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
    lo = vendor_dir("libreoffice") / ("program" if os.name == "nt" else "LibreOffice.app/Contents/MacOS")
    if lo.is_dir():                                   # so vendored skills that look for `soffice` find ours
        front.append(str(lo))
    cur = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p not in front]
    os.environ["PATH"] = os.pathsep.join(front + cur)


def browsers_dir() -> Path:
    """Playwright's Chromium, installed into the app at build time (app/vendor/browsers)."""
    return C.APP / "vendor" / "browsers"


# ---------- vendored Node packages (Codex, OpenCode) --------------------------------------------------------------

def vendor_dir(name: str) -> Path:
    return C.APP / "vendor" / name


def package_bin(vendor: str, pkg: str, bin_name: str | None = None) -> Path | None:
    """The command file of an installed npm package (its package.json "bin"), under app/vendor/<vendor>/node_modules."""
    pj = vendor_dir(vendor) / "node_modules" / Path(*pkg.split("/")) / "package.json"
    try:
        bins = json.loads(pj.read_text(encoding="utf-8")).get("bin")
    except (OSError, ValueError):
        return None
    if isinstance(bins, str):
        rel = bins
    elif isinstance(bins, dict) and bins:
        rel = bins.get(bin_name) or next(iter(bins.values()))
    else:
        return None
    p = (pj.parent / rel).resolve()
    return p if p.is_file() else None


def _opencode_binary() -> Path | None:
    """OpenCode's native program. Its npm package only links it in a postinstall step (which the build skips), so
    take it straight from the platform package."""
    osn = {"darwin": "darwin", "win32": "windows"}.get(sys.platform)
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    if not osn:
        return None
    p = vendor_dir("opencode") / "node_modules" / f"opencode-{osn}-{arch}" / "bin" / ("opencode.exe" if osn == "windows" else "opencode")
    return p if p.is_file() else None


def node_agent_command(key: str) -> tuple[str, list[str]] | None:
    """(command, leading args) that run a vendored Node agent with the bundled Node. key: codex, opencode, codex-login."""
    node = bundled_node()
    if key == "opencode":
        exe = _opencode_binary()
        return (str(exe), []) if exe else None
    if not node:
        return None
    if key == "codex":
        js = package_bin("codex-acp", "@agentclientprotocol/codex-acp")
    elif key == "codex-login":
        js = package_bin("codex-acp", "@openai/codex")
    else:
        return None
    return (node, [str(js)]) if js else None
