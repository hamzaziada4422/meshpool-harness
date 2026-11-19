"""Build-time helpers that fetch, verify and unpack the third-party programs Meshpool ships INSIDE its installers:
LibreOffice (pruned by lo_prune.py), Chromium, Git. Nothing here runs in the installed app, and the app never
downloads any of it. Codex and OpenCode are not shipped (the benchmark in testkit/ runs them with npx).
Standard library only; build_all.py loads this file by path.

Everything is pinned: version, source URL and SHA-256. To move a pin, change the version and refresh the hash with the
command printed next to it (or run `python packaging/build_all.py --check-pins`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


class BundleError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ LibreOffice (MPL-2.0)
# Refresh a hash:  curl -fsSL <url>.sha256     (The Document Foundation publishes one next to every file; also check
# the .asc signature with gpg against https://www.documentfoundation.org/gpg/ before moving the pin.)
LO_VERSION = "26.2.7"
LO_BUILD = "26.2.7.3"     # the archive names every build: these are the bytes download.documentfoundation.org served as 26.2.7
# Only the permanent archive: "stable" moves on to the next release and its links then break old builds.
_ARCHIVE = "https://downloadarchive.documentfoundation.org/libreoffice/old/{b}/{d}/{n}"
LO_PINNED = {
    # key: (distribution folder, file name, sha256, size in bytes)
    "mac-arm64": ("mac/aarch64", f"LibreOffice_{LO_BUILD}_MacOS_aarch64.dmg",
                  "58601808ae2b919c82dc7c4d6a26389de0e167f344ca48e5b6c9189218eb95ae", 297813809),
    "mac-x64": ("mac/x86_64", f"LibreOffice_{LO_BUILD}_MacOS_x86-64.dmg",
                "26a830205c617cc624b2a83783ef46434a4d6c337c3a873f79c43012171f83de", 308070986),
    "win-x64": ("win/x86_64", f"LibreOffice_{LO_BUILD}_Win_x86-64.msi",
                "480fecf759fc6393bd614fbf43753eb95a8198fc1a065d41b727e3b664cd1e59", 373121024),
}
LO_SOURCE_URL = f"https://downloadarchive.documentfoundation.org/libreoffice/old/{LO_BUILD}/src/"     # libreoffice-{LO_BUILD}.tar.xz
LO_EXE = {"mac-arm64": "LibreOffice.app/Contents/MacOS/soffice", "mac-x64": "LibreOffice.app/Contents/MacOS/soffice",
          "win-x64": "program/soffice.exe"}

# ------------------------------------------------------------------------------------------------ Git (GPL-2.0)
# Windows: MinGit from Git for Windows (zip, already how the release workflow gets it). Pin it by filling in the two
# values (needs GitHub access, which the authoring session did not have):
#   gh release view --repo git-for-windows/git --json tagName,assets -q '.tagName, (.assets[]|select(.name|test("^MinGit-[0-9.]+-64-bit.zip$"))|.name+" "+.digest)'
# Git for Windows 2.56.1 (v2.56.1.windows.1). The hash is the release notes' and was checked against the download.
MINGIT_URL = "https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/MinGit-2.56.1-64-bit.zip"
MINGIT_SHA256 = "4a92850b6e917da4fd010669b614b55dccf5b0ec333a534cc4be4700dfefdafd"
# macOS: git has no official macOS binary, so it is built from the pinned source tarball by packaging/mac/build_git.sh.
GIT_VERSION = "2.56.1"
GIT_SOURCE_SHA256 = "f0b0bf89264b9bd2e1c640363e7529643989da76281600473c50745cd34a607f"   # from kernel.org sha256sums.asc
GIT_SOURCE_URL = f"https://mirrors.edge.kernel.org/pub/software/scm/git/git-{GIT_VERSION}.tar.xz"


def _say(msg: str, cb: Callable[[str], None] | None = None) -> None:
    print(f"[bundle] {msg}", file=sys.stderr, flush=True)
    if cb:
        cb(msg)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    """Raise BundleError unless the file's SHA-256 equals the pinned one (an unfilled placeholder is refused)."""
    exp = (expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", exp):
        raise BundleError("the pinned LibreOffice checksum is not filled in (packaging/bundled_tools.py)")
    got = sha256_of(path)
    if got != exp:
        raise BundleError(f"checksum mismatch for {Path(path).name}: expected {exp}, got {got}. "
                                 "The file was not used.")


def download_verified(urls: list[str], dest: Path, sha256: str, size: int = 0,
                      progress: Callable[[str], None] | None = None, opener=None, label: str = "LibreOffice") -> Path:
    """Download the first URL that works to dest, check the SHA-256, and delete the file if it does not match."""
    opener = opener or urllib.request.urlopen
    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for url in urls:
        try:
            with opener(url, timeout=60) as r:
                hdr = getattr(r, "headers", None)
                total = int((hdr.get("Content-Length") if hdr else 0) or size or 0)
                done, next_mark = 0, 0
                h = hashlib.sha256()
                with open(dest, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if total and done * 100 // total >= next_mark:
                            _say(f"Downloading {label}: {done * 100 // total}% of {total / 1e6:.0f} MB", progress)
                            next_mark += 5
            break
        except Exception as e:                      # try the next mirror
            errors.append(f"{url.split('/')[2]}: {e}")
            dest.unlink(missing_ok=True)
    else:
        raise BundleError(f"could not download {label} ({'; '.join(errors)})")
    try:
        verify_sha256(dest, sha256)
    except BundleError:
        dest.unlink(missing_ok=True)
        raise
    return dest


def lo_urls(key: str) -> list[str]:
    d, name, _sha, _size = LO_PINNED[key]
    return [_ARCHIVE.format(b=LO_BUILD, d=d, n=name)]


# ---------------------------------------------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 1800) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise BundleError(f"{cmd[0]} failed: {(r.stderr or r.stdout)[-400:]}")


def extract(archive: Path, dest: Path, key: str) -> Path:
    """Unpack a pinned download into dest so that dest/LO_EXE[key] exists. macOS: copy LibreOffice.app out of the dmg.
    Windows: msiexec /a (administrative extract). Non-Windows hosts use msiextract or 7z for the msi."""
    dest.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="lo-extract-", dir=str(dest.parent)))
    try:
        if key.startswith("mac"):
            if shutil.which("hdiutil"):
                mnt = work / "mnt"
                mnt.mkdir()
                _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-mountpoint", str(mnt), str(archive)])
                try:
                    app = mnt / "LibreOffice.app"
                    if not app.is_dir():
                        raise BundleError("LibreOffice.app is not in the disk image")
                    if shutil.which("ditto"):
                        _run(["ditto", str(app), str(dest / "LibreOffice.app")])
                    else:
                        shutil.copytree(app, dest / "LibreOffice.app", symlinks=True)
                finally:
                    subprocess.run(["hdiutil", "detach", "-quiet", "-force", str(mnt)], capture_output=True)
            else:
                raise BundleError("the macOS trees must be built on a Mac: hdiutil opens the LibreOffice disk image (7z drops its symlinks)")
        else:
            out = work / "x"
            out.mkdir()
            if os.name == "nt":
                _run(["msiexec", "/a", str(archive), "/qn", f"TARGETDIR={out}"])
            elif shutil.which("msiextract"):
                _run(["msiextract", "-C", str(out), str(archive)])
            elif shutil.which("7z") or shutil.which("7zz"):
                _run([shutil.which("7z") or shutil.which("7zz"), "x", "-y", f"-o{out}", str(archive)])
            else:
                raise BundleError("need msiexec (Windows), msiextract or 7z to unpack the LibreOffice MSI")
            exe = next(out.rglob("soffice.exe"), None)
            if not exe:
                raise BundleError("soffice.exe not found after unpacking the MSI")
            root = exe.parent.parent                        # <...>/LibreOffice  (program/, share/, presets/)
            for item in root.iterdir():
                shutil.move(str(item), str(dest / item.name))
            # The MSI keeps the Visual C++ runtime (vcruntime140.dll, msvcp140.dll ...) in System64, outside the
            # LibreOffice folder, because the installer puts it in Windows' system folder. Without it a clean Windows
            # cannot start soffice, so it goes next to the program files.
            sysdir = out / "System64"
            for dll in sysdir.glob("*.dll") if sysdir.is_dir() else ():
                shutil.copy2(dll, dest / "program" / dll.name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not (dest / LO_EXE[key]).is_file():
        raise BundleError(f"LibreOffice unpacked, but {LO_EXE[key]} is missing")
    return dest




def lo_marker(folder: Path, key: str) -> None:
    """MESHPOOL-LIBREOFFICE.json beside the bundled copy: which release it is and where its source is (MPL-2.0)."""
    _d, name, sha, _ = LO_PINNED[key]
    (folder / "MESHPOOL-LIBREOFFICE.json").write_text(
        json.dumps({"version": LO_VERSION, "build": LO_BUILD, "file": name, "sha256": sha, "source": LO_SOURCE_URL,
                    "pruned": "packaging/lo_prune.py (headless conversion only)"}, indent=1),
        encoding="utf-8")


def fetch_libreoffice(key: str, cache: Path, dest: Path) -> Path:
    """Download (or reuse the cached file), verify the pinned SHA-256, unpack into dest. Returns dest."""
    _d, name, sha, size = LO_PINNED[key]
    archive = cache / name
    if archive.exists() and sha256_of(archive) == sha:
        pass
    else:
        download_verified(lo_urls(key), archive, sha, size, label=f"LibreOffice {LO_VERSION} ({key})")
    shutil.rmtree(dest, ignore_errors=True)
    extract(archive, dest, key)
    lo_marker(dest, key)
    return dest


# ------------------------------------------------------------------------------------------------ Hermes (MIT)
# The Hermes Agent checkout is a whole repository (tests, a website, desktop and mobile apps, docs, evals, a terminal
# UI, optional skills ...). The ACP adapter Meshpool runs imports only its Python packages and reads the bundled skills,
# so the build copies just those. Verified by running the adapter from the pruned copy (initialize, session/new and a
# prompt answered by a local mock model): docs/plan/BUNDLED_TOOLS.md.
HERMES_DROP_TOP = {"tests", "tests-js", "website", "apps", "docs", "evals", "contributors", "nix", "docker", "ui-tui",
                   "web", "optional-skills", "assets", "datagen-config-examples", "mcp-research-data",
                   "optional-mcps", "scripts"}
HERMES_DROP_TOP_FILES = ("README*", "CONTRIBUTING*", "SECURITY*", "AGENTS.md", "Dockerfile*", "docker-compose*",
                         "flake.*", "setup*", "eslint*", "uv.lock", "package-lock.json", "constraints-*.txt", "cli-config.yaml.example",
                         "batch_runner.py", "mini_swe_runner.py", ".*")
HERMES_DROP_ANYWHERE = {"tests", "__tests__", ".git", ".github", "__pycache__", ".pytest_cache", "node_modules"}


def hermes_ignore(root: Path):
    """shutil.copytree ignore function that leaves out everything the bundled Hermes does not run (see above)."""
    import fnmatch
    root = Path(root).resolve()

    def ignore(d, names):
        top = Path(d).resolve() == root
        out = set()
        for n in names:
            if n in HERMES_DROP_ANYWHERE or n.endswith(".pyc"):
                out.add(n)
            elif top and (n in HERMES_DROP_TOP or any(fnmatch.fnmatch(n, g) for g in HERMES_DROP_TOP_FILES)):
                out.add(n)
        return out
    return ignore


# ------------------------------------------------------------------------------------------------ Chromium
# Browser use opens a real, visible window (agent/browser_mcp_server.py), so the full Chromium stays; the headless
# shell that `playwright install chromium` adds next to it (about 280 MB) is skipped with --no-shell. Left out of the
# copy that ships: the other languages' strings, the Widevine DRM module and Playwright's ffmpeg (video recording).
def prune_chromium(folder: Path) -> int:
    """Delete the UI languages except English, Widevine and the hyphenation data from an installed Playwright Chromium.
    Returns the bytes removed."""
    gone = 0
    folder = Path(folder)

    def rm(p: Path):
        nonlocal gone
        try:
            gone += sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            pass
    for loc in list(folder.rglob("locales")):                       # Linux, Windows: locales/<lang>.pak
        if loc.is_dir():
            for f in loc.glob("*.pak"):
                if f.name not in ("en-US.pak", "en-GB.pak"):
                    rm(f)
            for f in loc.glob("*.pak.info"):
                rm(f)
    for lp in list(folder.rglob("*.lproj")):                        # macOS: Resources/<lang>.lproj
        if lp.name not in ("en.lproj", "en_US.lproj", "en_GB.lproj", "Base.lproj"):
            rm(lp)
    for name in ("WidevineCdm", "hyphen-data"):
        for p in list(folder.rglob(name)):
            rm(p)
    for p in list(folder.glob("ffmpeg-*")):                         # video recording only; the Mac build needs macOS 15
        rm(p)
    return gone


# ------------------------------------------------------------------------------------------------ MinGit
def fetch_mingit(cache: Path, dest: Path) -> Path:
    """Windows: MinGit (Git and Bash) unpacked into dest, verified against the pinned SHA-256."""
    if "FILL-IN" in (MINGIT_URL, MINGIT_SHA256):
        raise BundleError("MINGIT_URL / MINGIT_SHA256 in packaging/bundled_tools.py are not filled in yet "
                          "(the command is in the comment above them), or put a verified MinGit folder in deps/git-win")
    archive = cache / MINGIT_URL.rsplit("/", 1)[1]
    if not (archive.exists() and sha256_of(archive) == MINGIT_SHA256):
        download_verified([MINGIT_URL], archive, MINGIT_SHA256, 0, label="MinGit")
    import zipfile
    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    return dest
