"""The programs Meshpool ships inside its installers, and how the installed app finds them.

Rule: in the packaged app (config.packaged(), or a frozen build) Meshpool uses ONLY its own copy of Node.js, Git, Pi,
Chromium, LibreOffice (office_runtime.py), ffmpeg, pandoc and the speech-to-text model. It never falls back to PATH, npx or a download. Codex and OpenCode are not
shipped at all (harnesses.NOT_SHIPPED). In a source checkout or command-line install nothing changes: PATH and npx
still work.

Layout (packaging/build_all.py), relative to config.APP.parent (macOS: Meshpool.app/Contents/Resources):
  node/            Node.js (bin/node, bin/npx; Windows: node.exe)
  git/             Windows: MinGit (cmd, mingw64, usr/bin).  macOS: bin/git and libexec/git-core
  app/vendor/      hermes, pi (with its node_modules), libreoffice, browsers, ffmpeg, pandoc, whisper (the speech model)
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


def front_dirs() -> list:
    """The bundled programs' folders that go first on PATH, in order: Node, Git, LibreOffice, ffmpeg, pandoc. [] outside
    the packaged app."""
    if not packaged():
        return []
    front = [str(p) for p in git_dirs()]
    if node_dir().is_dir():
        front.insert(0, str(node_dir() if os.name == "nt" else node_dir() / "bin"))
    lo = vendor_dir("libreoffice") / ("LibreOffice.app/Contents/MacOS" if sys.platform == "darwin" else "program")
    if lo.is_dir():                                   # so vendored skills that look for `soffice` find ours
        front.append(str(lo))
    for name, exe in (("ffmpeg", bundled_ffmpeg()), ("pandoc", bundled_pandoc())):
        if exe and not (name == "ffmpeg" and ffmpeg_needs_newer_macos()):
            front.append(str(Path(exe).parent))       # `ffmpeg` and `pandoc` by name run Meshpool's own copies
    return front


def use_bundled_tools() -> None:
    """Put the bundled Node and Git first on this process's PATH, so every child (the agents' shell, the Node agents,
    `git` called by name anywhere in Meshpool, MCP servers started with npx) gets Meshpool's own. Idempotent. Only in
    the packaged app; a source checkout keeps the developer's PATH."""
    if not packaged():
        return
    front = front_dirs()
    cur = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p not in front]
    os.environ["PATH"] = os.pathsep.join(front + cur)
    # The agents' shell finds Meshpool's own Python first on PATH; these name the rest. The speech model is a folder
    # faster-whisper loads directly (the skill's transcribe.py also tells the model hub client never to go online; that is
    # not set here, because the agent's shell is also the customer's own and their Hugging Face work must keep working).
    model = whisper_model_dir()
    if model:
        os.environ.setdefault("MESHPOOL_WHISPER_MODEL", str(model))
    if sys.executable:
        os.environ.setdefault("MESHPOOL_PYTHON", sys.executable)


def bundled_program(name: str) -> str | None:
    """<app>/vendor/<name>/<name>(.exe) when this build has it (ffmpeg, pandoc), else None."""
    for n in (f"{name}.exe", name):
        p = vendor_dir(name) / n
        if p.is_file():
            return str(p)
    return None


def bundled_ffmpeg() -> str | None:
    return bundled_program("ffmpeg")


def bundled_pandoc() -> str | None:
    return bundled_program("pandoc")


def whisper_model_dir() -> Path | None:
    """The speech-to-text model that ships in the app (a CTranslate2 folder faster-whisper loads offline), or None."""
    d = vendor_dir("whisper")
    for p in sorted(d.glob("*")) if d.is_dir() else ():
        if (p / "model.bin").is_file() and (p / "config.json").is_file():
            return p
    return None


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


FFMPEG_ARM64_NEEDS_MACOS = (12, 0)      # the Apple silicon ffmpeg in the app (read from the file); packaging/mac/verified_minos.json


def ffmpeg_needs_newer_macos() -> str:
    """The app opens on macOS 11, but the Apple silicon ffmpeg needs macOS 12: on such a Mac, why audio and video
    conversion is not offered ("" everywhere else, including every Intel Mac). Speech to text does not use ffmpeg."""
    if sys.platform != "darwin" or not packaged() or platform.machine() != "arm64":
        return ""
    try:
        v = tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return ""
    if len(v) < 2 or v >= FFMPEG_ARM64_NEEDS_MACOS:
        return ""
    return (f"Audio and video conversion needs macOS {FFMPEG_ARM64_NEEDS_MACOS[0]} or later on Apple silicon; this Mac runs "
            f"macOS {v[0]}.{v[1]}. Speech to text, PDF, image, document and spreadsheet tools are not affected.")


_VERSIONS: dict[tuple, str] = {}


def program_version(exe: str, arg: str = "--version") -> str:
    """First line of `exe arg`, e.g. "pandoc 2.19.2" ("" if it does not run). Asked once per program and cached; a
    probe that hangs or fails must never break the settings page, so it is short and never raises."""
    key = (exe, arg)
    if key not in _VERSIONS:
        import subprocess
        try:
            r = subprocess.run([exe, arg], capture_output=True, text=True, timeout=8, errors="replace",
                               stdin=subprocess.DEVNULL)
            lines = (r.stdout or r.stderr).strip().splitlines()
            _VERSIONS[key] = (lines[0].strip() if r.returncode == 0 and lines else "")[:120]
        except (OSError, subprocess.SubprocessError, ValueError):
            _VERSIONS[key] = ""
    return _VERSIONS[key]


def speech_status() -> dict:
    """{"ok", "detail", "fix"} for speech to text: the faster-whisper package and the model in the app."""
    import importlib.util
    model = whisper_model_dir()
    try:
        pkg = importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        pkg = False
    if pkg and model:
        ver = ""
        try:
            from importlib import metadata
            ver = f"faster-whisper {metadata.version('faster-whisper')}, CTranslate2 {metadata.version('ctranslate2')}, "
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "detail": f"{ver}model {model.name} ({model}), works offline", "fix": ""}
    if packaged():
        return {"ok": False, "detail": "missing from this install", "fix": "Missing from this install; reinstall meshpool."}
    what = "the faster-whisper package" if not pkg else "the speech model"
    return {"ok": False, "detail": f"{what} is not here",
            "fix": "The desktop app includes speech to text. In a terminal install: pip install faster-whisper, then set "
                   "MESHPOOL_WHISPER_MODEL to a faster-whisper model folder (Systran/faster-whisper-base)."}
