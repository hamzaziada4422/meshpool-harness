"""Build-time helpers that fetch, verify and unpack the third-party programs Meshpool ships INSIDE its installers:
LibreOffice (pruned by lo_prune.py), Chromium, Git. Nothing here runs in the installed app, and the app never
downloads any of it. Codex and OpenCode are not shipped (the benchmark in testkit/ runs them with npx).
Standard library only; build_all.py loads this file by path.

Everything is pinned: version, source URL and SHA-256. To move a pin, change the version and refresh the hash with the
command printed next to it (or run `python packaging/build_all.py --check-pins`).
"""
from __future__ import annotations

import hashlib
import time
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
    "linux-x64": ("deb/x86_64", f"LibreOffice_{LO_BUILD}_Linux_x86-64_deb.tar.gz",
                  "8cd04a88396a9230264ff784ec89cc090cbfd0960c192483ba2b80fa53939416", 218569368),
}
LO_SOURCE_URL = f"https://downloadarchive.documentfoundation.org/libreoffice/old/{LO_BUILD}/src/"     # libreoffice-{LO_BUILD}.tar.xz
LO_EXE = {"mac-arm64": "LibreOffice.app/Contents/MacOS/soffice", "mac-x64": "LibreOffice.app/Contents/MacOS/soffice",
          "win-x64": "program/soffice.exe", "linux-x64": "program/soffice"}

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


# ------------------------------------------------------------------------------------------------ ffmpeg and pandoc
# Programs that ship inside two PyPI wheels (their publishers build the binaries and attach them to the wheel), so each
# is one pinned URL with a SHA-256 per platform, like LibreOffice. The binary is taken out of the wheel and put in
# <app>/vendor/<name>/; the wheel itself is not installed. Standard library only.
#   ffmpeg  imageio-ffmpeg 0.7.0 (BSD wrapper; the program is a static GPL build of FFmpeg 7.0 to 7.1, no ffprobe)
#   pandoc  pypandoc_binary 1.13 (the program is pandoc 2.19.2, GPL-2.0-or-later)
# Why these and not the newest: Mac files must run on macOS 11. The macOS pandoc in pypandoc_binary 1.14 and later needs
# macOS 12 to 15 (the wheel tag says 11 anyway), the official pandoc 3.x Mac zips need 13 to 26, and every Apple silicon
# ffmpeg that could be fetched needs macOS 12 or later (imageio-ffmpeg 12, martin-riedl.de 12, osxexperts 13). Read with
# packaging/mac/wheel_minos.py; the numbers are in packaging/mac/verified_minos.json and held by tests.
# Refresh a hash:  curl -fsSL <url> | sha256sum   (or the digests on https://pypi.org/project/<name>/<version>/#files)
_PYPI = "https://files.pythonhosted.org/packages/"
WHEEL_PROGRAMS = {
    "ffmpeg": {
        "version": "imageio-ffmpeg 0.7.0",
        "license": "GPL-2.0-or-later (static FFmpeg build with libx264 and others); the wrapper is BSD-2-Clause",
        "source": "https://ffmpeg.org/releases/ (the version is in the binary's name inside the wheel); builds: "
                  "https://github.com/imageio/imageio-binaries",
        "member": r"imageio_ffmpeg/binaries/ffmpeg-[^/]+",
        "exe": {"win-x64": "ffmpeg.exe"},                       # every other platform: ffmpeg
        "pins": {
            "mac-arm64": (_PYPI + "24/d3/be06b86c75acc49113aaf869b8c3d1e721ee01e1d1d7fa12cfc7d9708ea3/imageio_ffmpeg-0.7.0-py3-none-macosx_11_0_arm64.whl",
                          "5cb5c37d3c19aa8e7cd95b9f4833e6a3d9fd33e4b1d126a42995b14aef9fef02", 21114068),
            "mac-x64": (_PYPI + "f1/ef/e006996ba8735c1605b05fc931b7b49297cbdf11603259701bd0d62e17e1/imageio_ffmpeg-0.7.0-py3-none-macosx_10_9_intel.macosx_10_9_x86_64.whl",
                        "fafa399433b571e8963471487cd7a7bb3ee7b532af42e6342145add6379f479b", 24933149),
            "win-x64": (_PYPI + "e5/55/1d50cdd78d862a30c2ce44f01cea387f77b364b21fa2fecf1590258b215b/imageio_ffmpeg-0.7.0-py3-none-win_amd64.whl",
                        "7a22b45f60321d4172bdf2bc61f716bce4c81b85727bd59c7c7e371055a6a440", 31247004),
            "linux-x64": (_PYPI + "04/96/15fe7ef562f7494c358cbd9b521d2da82d7a331b5513703756166478bd5f/imageio_ffmpeg-0.7.0-py3-none-manylinux2014_x86_64.whl",
                          "4e1f36bef7bedd4bc5dc25f60a8345d88f57133fa1e754f09b411ac61b854408", 29498414),
        },
    },
    "pandoc": {
        "version": "pypandoc_binary 1.13 (pandoc 2.19.2)",
        "license": "GPL-2.0-or-later; the wrapper is MIT",
        "source": "https://github.com/jgm/pandoc/releases/tag/2.19.2",
        "member": r"pypandoc/files/pandoc(?:\.exe)?",
        "exe": {"win-x64": "pandoc.exe"},
        "pins": {
            "mac-arm64": (_PYPI + "80/5e/70051958952850b9747032f9c854d1ae7870052c7d5697feb0a1d74d65c2/pypandoc_binary-1.13-py3-none-macosx_11_0_arm64.whl",
                          "21ef0345726d36fc45a50211320614daf2caede684b0d0963ce8738292809746", 20136798),
            "mac-x64": (_PYPI + "97/f3/8e1ba6d7ecc97cb57ac61a3b8e935967e1f273421e37b6861c6742ad202d/pypandoc_binary-1.13-py3-none-macosx_10_9_x86_64.whl",
                        "946666388eb79b307d7f497b3b33045ef807750f8e5ef3440e0ba3bbab698044", 20136798),
            "win-x64": (_PYPI + "0a/d2/bcbf20b497d0393eb29b03d677eba204afdd4ce3af1ed0cd584194378e60/pypandoc_binary-1.13-py3-none-win_amd64.whl",
                        "3881aa7c84faec2007c0ae4466d3a1cfc93171206b8540f2defa8ea971bf6fd6", 24762997),
            "linux-x64": (_PYPI + "c8/91/df8eff0a17a3e714589e84516aebaf82d952930ef0894a5b160ae7bcd3cb/pypandoc_binary-1.13-py3-none-manylinux_2_5_x86_64.manylinux1_x86_64.manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                          "9455fdd9521cbf4b56d79a56b806afa94c8c22f3c8ef878536e58d941a70f6d6", 17055071),
        },
    },
}
# The Apple silicon ffmpeg needs macOS 12 (read from the file); the app promises 11, so it is exempt from the macOS 11
# check up to 12.0 and the app says so on an older Mac (bundled.ffmpeg_needs_newer_macos). Nothing else depends on it:
# speech to text decodes audio with PyAV, whose Apple silicon build needs 11.
FFMPEG_MAC_ARM64_NEEDS = "12.0"

# ------------------------------------------------------------------------------------------------ Speech model (MIT)
# OpenAI Whisper "base" (multilingual, 74 M parameters) converted for CTranslate2 by Systran; faster-whisper loads it
# from a folder. The revision is a commit, the hashes are of the files at that commit, so the model cannot change under
# a build. Size: 146 MB, which is what makes transcription work without a download.
WHISPER_MODEL = "base"
WHISPER_REPO = "Systran/faster-whisper-base"
WHISPER_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
WHISPER_FILES = {                                           # name: (sha256, size)
    "config.json": ("56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a", 2309),
    "model.bin": ("d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9", 145217532),
    "tokenizer.json": ("fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab", 2203239),
    "vocabulary.txt": ("34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913", 459861),
}


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
    pause = time.sleep if opener is None else (lambda s: None)   # tests pass their own opener: no waiting
    opener = opener or urllib.request.urlopen
    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    tries = [u for u in urls for _ in range(3)]            # each mirror three times before the next one
    for n, url in enumerate(tries):
        if n and tries[n - 1] == url:
            pause(5 * (n % 3))                              # a moment before trying the same mirror again
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


def _deb_members(deb: Path) -> dict:
    """The members of a .deb (an ar archive): {name: bytes}. Pure Python, so no dpkg-deb or ar is needed."""
    data = deb.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise BundleError(f"{deb.name} is not a Debian package")
    out, pos = {}, 8
    while pos + 60 <= len(data):
        head = data[pos:pos + 60]
        name = head[:16].decode().strip().rstrip("/")
        size = int(head[48:58].decode().strip())
        out[name] = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
    return out


def _extract_debs(archive: Path, work: Path, dest: Path) -> None:
    """Linux: the pinned release is a tarball of .deb packages. Every package's files go under one tree, and its
    opt/libreoffice<version>/ folder (program/, share/, presets/) becomes dest."""
    import io
    import tarfile
    def untar(t, path, flt):
        try:
            t.extractall(path, filter=flt)
        except TypeError:                                    # a Python without extraction filters
            t.extractall(path)
    with tarfile.open(archive) as t:
        untar(t, work / "t", "data")
    tree = work / "root"
    tree.mkdir()
    debs = sorted((work / "t").rglob("*.deb"))
    if not debs:
        raise BundleError("no .deb packages in the LibreOffice tarball")
    for deb in debs:
        members = _deb_members(deb)
        name = next((n for n in members if n.startswith("data.tar")), None)
        if not name:
            raise BundleError(f"{deb.name} has no data.tar")
        with tarfile.open(fileobj=io.BytesIO(members[name])) as t:
            untar(t, tree, "tar")
    root = next((p for p in (tree / "opt").glob("libreoffice*") if (p / "program" / "soffice").exists()), None)
    if not root:
        raise BundleError("program/soffice not found after unpacking the packages")
    for item in root.iterdir():
        shutil.move(str(item), str(dest / item.name))


def extract(archive: Path, dest: Path, key: str) -> Path:
    """Unpack a pinned download into dest so that dest/LO_EXE[key] exists. macOS: copy LibreOffice.app out of the dmg.
    Windows: msiexec /a (administrative extract). Non-Windows hosts use msiextract or 7z for the msi."""
    dest.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="lo-extract-", dir=str(dest.parent)))
    try:
        if key.startswith("linux"):
            _extract_debs(archive, work, dest)
        elif key.startswith("mac"):
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


# ------------------------------------------------------------------------------------------------ ffmpeg, pandoc, speech model
def fetch_wheel_program(name: str, key: str, cache: Path, dest: Path) -> Path:
    """Download the pinned wheel (cached, SHA-256 checked), take its program out and put it in dest as ffmpeg or pandoc
    (.exe on Windows), executable, with MESHPOOL-<NAME>.json beside it. Returns the program's path."""
    import zipfile
    spec = WHEEL_PROGRAMS[name]
    if key not in spec["pins"]:
        raise BundleError(f"no pinned {name} for {key}")
    url, sha, size = spec["pins"][key]
    archive = cache / url.rsplit("/", 1)[1]
    if not (archive.exists() and sha256_of(archive) == sha):
        download_verified([url], archive, sha, size, label=f"{name} ({key})")
    exe = spec["exe"].get(key, name)
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        hits = [i for i in z.infolist() if re.fullmatch(spec["member"], i.filename)]
        if len(hits) != 1:
            raise BundleError(f"{archive.name} should hold exactly one {name} program, found {[h.filename for h in hits]}")
        with z.open(hits[0]) as src, open(dest / exe, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
    (dest / exe).chmod(0o755)
    (dest / f"MESHPOOL-{name.upper()}.json").write_text(
        json.dumps({"program": name, "from": spec["version"], "member": hits[0].filename, "wheel": archive.name,
                    "sha256": sha, "license": spec["license"], "source": spec["source"]}, indent=1), encoding="utf-8")
    return dest / exe


def fetch_whisper_model(cache: Path, dest: Path) -> Path:
    """The pinned faster-whisper model into dest/<WHISPER_MODEL>/ (config.json, model.bin, tokenizer.json, vocabulary.txt)."""
    out = dest / WHISPER_MODEL
    shutil.rmtree(dest, ignore_errors=True)
    out.mkdir(parents=True)
    for fname, (sha, size) in WHISPER_FILES.items():
        got = cache / f"whisper-{WHISPER_REVISION[:8]}-{fname}"
        if not (got.exists() and sha256_of(got) == sha):
            url = f"https://huggingface.co/{WHISPER_REPO}/resolve/{WHISPER_REVISION}/{fname}"
            download_verified([url], got, sha, size, label=f"speech model {fname}")
        shutil.copy2(got, out / fname)
    (out / "MESHPOOL-WHISPER.json").write_text(
        json.dumps({"model": "OpenAI Whisper base (multilingual)", "converted_by": WHISPER_REPO, "revision": WHISPER_REVISION,
                    "license": "MIT", "sha256": {k: v[0] for k, v in WHISPER_FILES.items()}}, indent=1), encoding="utf-8")
    return out
