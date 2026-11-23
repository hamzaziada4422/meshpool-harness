"""Update Meshpool from its GitHub releases, from inside the app.

check()   -> the latest release and whether it is newer than this copy (the version on disk, not the one running)
prepare() -> downloads, checks and stages a code update beside the app folders; the running code is not touched
apply_staged() -> swaps a staged update in (journalled, so a kill half way is repaired at the next start by
             meshpool_recover.py) and installs new packages for the command-line copy after the swap
install() -> for the in-place kinds this only prepares; the server then applies and restarts in one step. The safest
  way this kind of install allows:
  - command-line install (~/.meshpool-cli) and Windows app: the release's app code replaces this copy's code in place
    (the bundled runtimes, Pi and Hermes are kept); new Python packages, if the release lists any, are installed for
    the command-line copy, while the Windows app gets the full installer instead;
  - Mac app (and a Windows release that changed its runtime): handled by autoupdate.py, which downloads the installer
    in the background, checks it, and installs it after the app quits. Nothing inside a signed Mac app is changed
    (that would break its signature): the whole app is swapped by a helper;
  - a developer checkout (a git clone): nothing is changed; it says to run git pull.
  Installers are checked against the release's SHA256SUMS.txt when it has one. A code update swaps each code folder by
  renaming, keeping the old folders until every new one is in place, and puts the old ones back if any step fails.
restart() -> starts a fresh copy of the app and closes this one, so the new code runs.

check() only looks; autoupdate.start_background() calls it at start and every 6 hours and downloads in the background,
and install() is what the Update button runs for the command-line and in-place code updates.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import meshpool_harness as MH
from meshpool_harness import config as C
from meshpool_harness import fsio

REPO = os.environ.get("MESHPOOL_REPO", "hamzaziada4422/meshpool-harness")
API = os.environ.get("MESHPOOL_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases?per_page=30"
SRC = os.environ.get("MESHPOOL_UPDATE_SRC") or ""   # tests only: a local archive, not checked
SRC_ASSET = "Meshpool-src.tar.gz"     # the code update, published with each release and listed in SHA256SUMS.txt
APP = C.APP                                  # the folder that holds meshpool_harness/
CODE_DIRS = ("meshpool_harness", "engines", "profiles", "defaults")   # what a code update replaces; vendor/ is kept
CLI_REQ = "requirements-cli.txt"     # the package list the command line and the bundled runtimes are installed from
SUMS = "SHA256SUMS.txt"              # published with each release by packaging/release.sh
TOP_FILES = ("meshpool_recover.py", "sitecustomize.py")   # shipped beside the code folders: start-up recovery (M11-06)
NOTICES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")
JOURNAL = ".update-journal.json"
QUIT_DEADLINE = 60                   # seconds the quit hooks get in restart() before the process exits (M11-03)
_CACHE: dict = {}
_HELD = threading.local()            # install_lock() depth of this thread: the lock is re-entrant inside one thread
_PRE = re.compile(r"\d[.-]?(alpha|beta|preview|pre|rc|dev|a|b|c)(?![a-z])\.?(\d*)", re.I)


def _ver(s: str) -> tuple:
    """(major, minor, patch) of a version or tag such as v0.2.1 or 0.3.0rc1."""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", s or "")
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)


def _is_pre(s: str) -> bool:
    """A prerelease version or tag: 0.3.0rc1, v0.3.0-beta.2 and the like."""
    return bool(_PRE.search(s or ""))


def _key(s: str) -> tuple:
    """Sort key: by number, and a prerelease before the release it leads to (0.3.0rc1 < 0.3.0)."""
    m = _PRE.search(s or "")
    return _ver(s) + ((0, int(m.group(2) or 0)) if m else (1, 0))


def _get(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "meshpool-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def kind() -> str:
    """How this copy was installed: dev, mac-app, win-app, linux-app or cli."""
    if (APP.parent / ".git").exists():
        return "dev"
    # The command-line install (~/.meshpool-cli) keeps its virtual environment beside the app. It may also hold a
    # python/ folder (the copy install.sh downloads when the computer has none), which is what config.packaged()
    # looks for, so test for the command-line install first.
    if (APP.parent / "venv").is_dir() or (APP / "meshpool_launch.py").is_file():
        return "cli"
    if C.packaged():
        return "mac-app" if sys.platform == "darwin" else "win-app" if os.name == "nt" else "linux-app"
    return "cli"


def installed_version() -> str:
    """The version of the code on disk. It differs from MH.__version__ (the code that runs) after an update was
    swapped in and the app has not restarted yet."""
    try:
        text = (APP / "meshpool_harness" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    except OSError:
        pass
    return MH.__version__


def _on_disk(running: str) -> tuple:
    """(the version on disk, the newer of it and the running version, whether a restart is pending)."""
    disk = installed_version()
    return disk, (disk if _key(disk) > _key(running) else running), _key(disk) > _key(running)


def check(force: bool = False, timeout: float | None = None) -> dict:
    """The latest release and whether it is newer than this copy. "Newer" is measured against the version on disk
    (an update already swapped in counts), "current" stays the version that runs; pending_restart says the two differ.
    "tags" lists every release this copy may be offered, newer or not (the background updater drops what vanished)."""
    running = MH.__version__
    disk, cur, pending = _on_disk(running)
    now = time.time()
    if not force and _CACHE.get("t", 0) > now - 6 * 3600 and "rels" in _CACHE:
        rels = _CACHE["rels"]
    else:
        try:
            with _get(API, timeout=20 if timeout is None else timeout) as r:
                rels = json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            why = ("GitHub is limiting update checks right now (rate limit); try again later" if code in (403, 429)
                   else f"GitHub answered {code}" if code else f"Could not reach GitHub ({type(e).__name__})")
            return {"ok": False, "current": running, "pending_restart": pending, "checked_at": now,
                    "error": f"Could not check for updates: {why}."}
        rels = rels if isinstance(rels, list) else [rels]
        _CACHE.update(t=now, rels=rels)
    tags = [r["tag_name"] for r in sorted(_candidates(rels, cur), key=lambda r: _key(r["tag_name"]), reverse=True)]
    rel = _pick(rels, cur)
    if not rel:
        v = {"ok": True, "current": running, "latest": cur, "tag": "", "newer": False, "notes": "", "kind": kind(),
                "url": f"https://github.com/{REPO}/releases", "assets": [], "tags": tags, "checked_at": now,
                "pending_restart": pending}
        _CACHE["v"] = v                       # the last check, for the page's status (autoupdate.status)
        return v
    tag = rel.get("tag_name") or ""
    v = {"ok": True, "current": running, "latest": tag.lstrip("v"), "tag": tag, "newer": _key(tag) > _key(cur),
            "prerelease": bool(rel.get("prerelease")), "notes": (rel.get("body") or "")[:4000],
            "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
            "published": rel.get("published_at"), "kind": kind(),
            "assets": [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size")}
                       for a in rel.get("assets") or []],
            "tags": tags, "checked_at": now, "pending_restart": pending}
    _CACHE["v"] = v
    return v


def _legacy(r: dict, releases: list, cur: str) -> bool:
    """A build from the 2.x line that came before the 0.x renumbering. Its number looks higher than every 0.x and
    1.x release, but it is older: a 0.x copy never moves to 2.x, and a later copy only to a 2.x published after the
    first release of the renumbered line."""
    if _ver(r["tag_name"])[0] < 2:
        return False
    if _ver(cur)[0] < 1:
        return True
    first = min((x["published_at"] for x in releases if isinstance(x, dict) and x.get("published_at")
                 and _ver(x.get("tag_name") or "")[0] < 2), default="")
    return bool(first) and (not r.get("published_at") or r["published_at"] < first)


def _candidates(releases: list, cur: str) -> list:
    """Every release this copy may be offered, whether or not it is newer: never a draft, never one still uploading
    (no SHA256SUMS.txt), never one from the old 2.x line, and a prerelease only when this copy is a prerelease."""
    ok = [r for r in releases if isinstance(r, dict) and not r.get("draft") and r.get("tag_name")
          and ("assets" not in r or any((a or {}).get("name") == SUMS for a in r["assets"] or []))]   # still uploading
    if not _is_pre(cur):
        ok = [r for r in ok if not r.get("prerelease") and not _is_pre(r["tag_name"])]
    return [r for r in ok if not _legacy(r, releases, cur)]


def _pick(releases: list, cur: str) -> dict | None:
    """The newest published release this copy may move to, or None: a candidate that is newer than this copy."""
    ok = [r for r in _candidates(releases, cur) if _key(r["tag_name"]) > _key(cur)]
    return max(ok, key=lambda r: _key(r["tag_name"]), default=None)


def _rosetta() -> bool:
    """True when this process is an Intel build running on Apple silicon (Rosetta). platform.machine() then says
    x86_64, but the Mac would rather have the native app."""
    try:
        r = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def _asset(v: dict) -> dict | None:
    names = [a for a in v.get("assets") or [] if a.get("url")]
    if sys.platform == "darwin":
        # An Apple silicon Mac running the Intel app (a release without a native build ships the Intel app as
        # Meshpool.dmg) goes back to the native app with the next release that has one.
        intel = platform.machine() == "x86_64" and not _rosetta()
        dmgs = [a for a in names if a["name"].lower().endswith(".dmg")]
        pick = [a for a in dmgs if ("intel" in a["name"].lower()) == intel]
        return (pick or dmgs or [None])[0]
    if os.name == "nt":
        return next((a for a in names if a["name"].lower().endswith(".exe")), None)
    return None


def _download(url: str, dst: Path) -> Path:
    with _get(url, timeout=60) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    return dst


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fresh_sums(v: dict) -> dict | None:
    """The checksums read again: a release whose files were being replaced may have new ones by now."""
    try:
        return _sums(v)
    except Exception:  # noqa: BLE001
        return None


def _sums(v: dict) -> dict | None:
    """{file name: sha256} from the release's SHA256SUMS.txt, or None when the release has none."""
    a = next((a for a in v.get("assets") or [] if a.get("name") == SUMS and a.get("url")), None)
    if not a:
        return None
    with _get(a["url"]) as r:
        text = r.read(1 << 20).decode("utf-8", "replace")
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].strip().lstrip("*")] = parts[0].lower()
    return out


def _open(p: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    elif os.name == "nt":
        os.startfile(str(p))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(p)])


def _installer(v: dict, why: str) -> dict:
    a = _asset(v)
    if not a:
        return {"ok": False, "error": f"{why} This release has no installer for this computer. Download it from the "
                                      f"release page.", "url": v["url"]}
    try:
        sums = _sums(v)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not read the release's checksums ({type(e).__name__}). Try again, or "
                                      "download it from the release page.", "url": v["url"]}
    if sums is None:
        return {"ok": False, "error": "This release has no checksums, so its download cannot be checked. Download it "
                                      "from the release page.", "url": v["url"]}
    if a["name"] not in sums:
        return {"ok": False, "error": f"The release's checksums do not list {a['name']}. Download it from the release "
                                      "page.", "url": v["url"]}
    dl = Path.home() / "Downloads"
    p = (dl if dl.is_dir() else Path(tempfile.gettempdir())) / a["name"]
    part = p.with_name(p.name + ".part")
    try:
        try:
            _download(a["url"], part)
        except PermissionError:                 # macOS privacy settings can refuse the Downloads folder
            p = Path(tempfile.gettempdir()) / a["name"]
            part = p.with_name(p.name + ".part")
            _download(a["url"], part)
        if _sha256(part) != sums[a["name"]] and _sha256(part) != (_fresh_sums(v) or {}).get(a["name"]):
            part.unlink()
            return {"ok": False, "error": f"The download of {a['name']} did not match the release's checksum, so it "
                                          "was deleted. The release may have been updating on GitHub while it "
                                          "downloaded: try again in a few minutes.", "url": v["url"]}
        C.replace_file(part, p, keep_src=True)
    except OSError as e:
        if not isinstance(e, PermissionError):
            part.unlink(missing_ok=True)       # a locked finished download is kept for the next try
        return {"ok": False, "error": f"Could not download {a['name']} ({type(e).__name__}). Try again.",
                "url": v["url"]}
    _open(p)
    step = ("Drag Meshpool into Applications and replace the old copy, then open it again."
            if sys.platform == "darwin" else "Follow the installer; it replaces this copy.")
    return {"ok": True, "action": "installer", "file": str(p),
            "message": f"Downloaded Meshpool {v['latest']} and opened the installer. {why} {step}"}


class UpdateBusy(Exception):
    """Another update is being installed (by this app, a second window or another process)."""


class _RollbackFailed(OSError):
    """A swap failed and the old folders could not all be put back: the journal is kept for start-up recovery."""


@contextlib.contextmanager
def install_lock():
    """Exclusive across threads and processes. Re-entrant inside one thread, so prepare()/apply_staged() can take it
    themselves while a caller (the server) already holds it around prepare + apply. Raises UpdateBusy when busy."""
    depth = getattr(_HELD, "n", 0)
    if depth:
        _HELD.n = depth + 1
        try:
            yield
        finally:
            _HELD.n -= 1
        return
    lock = fsio.file_lock(C.home() / "updates" / "install", timeout=0)
    try:
        lock.__enter__()
    except TimeoutError:
        raise UpdateBusy("An update is already being installed.") from None
    try:
        _HELD.n = 1
        try:
            yield
        finally:
            _HELD.n = 0
    finally:
        lock.__exit__(*sys.exc_info())


def _recover() -> None:
    """Put the copy back if an earlier update was killed half way (only while holding install_lock())."""
    try:
        try:
            import meshpool_recover as R
        except ImportError:
            import importlib.util
            spec = importlib.util.spec_from_file_location("meshpool_recover", APP / "meshpool_recover.py")
            if not spec or not spec.loader or not (APP / "meshpool_recover.py").is_file():
                return
            R = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(R)
        R.recover(APP)
    except Exception:  # noqa: BLE001
        pass


def _journal(pairs: list, info: dict, phase: str) -> None:
    fsio.atomic_write_json(APP / JOURNAL, {
        "version": 1, "tag": info.get("tag", ""), "phase": phase, "pid": os.getpid(), "time": time.time(),
        "stage": info.get("stage", ""),
        "pairs": [{"staged": str(s), "dest": str(d), "backup": str(o), "existed": had} for s, d, o, had in pairs]})


def _rollback(done: list) -> bool:
    """Put every swapped folder back as it was. True when every one is back."""
    clean = True
    for dst, old, had in reversed(done):
        failed = dst.with_name(f".{dst.name}.failed")
        shutil.rmtree(failed, ignore_errors=True)
        try:
            dst.rename(failed)
            if had:
                old.rename(dst)
        except OSError:
            clean = False
        shutil.rmtree(failed, ignore_errors=True)
    return clean


def _swap_dirs(pairs: list) -> list:
    """Move each staged folder into place by renaming, keeping every old folder as a backup (pairs are
    (staged, dst, backup, dst existed)). On an OSError the folders already swapped are put back and the error is
    raised (_RollbackFailed when they could not all be put back). Returns what to delete or roll back later."""
    done = []
    try:
        for staged, dst, old, had in pairs:
            shutil.rmtree(old, ignore_errors=True)
            if had:
                dst.rename(old)
            try:
                staged.rename(dst)
            except OSError:
                if had:
                    old.rename(dst)
                raise
            done.append((dst, old, had))
    except OSError as e:
        if not _rollback(done):
            raise _RollbackFailed(str(e)) from None
        raise
    return done


def _requirements(src: Path, new: Path) -> tuple:
    """(the release's package list, this copy's record of its own). The command line and the Windows app are
    installed from packaging/requirements-cli.txt (a copy sits in the app folder); app/requirements.txt is the
    developer list. A copy without that record is treated as changed, so new packages are never skipped."""
    rel = src / "packaging" / CLI_REQ
    if rel.exists():
        return rel, APP / CLI_REQ
    return new / "requirements.txt", APP / "requirements.txt"   # a release from before the record existed


def _safe(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", tag or "x")


def _sweep(tag: str) -> None:
    """Remove stale stages of this tag and the old-style .<name>.new folders (only while holding the lock)."""
    for d in APP.glob(f".update-{_safe(tag)}-*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    for name in CODE_DIRS:
        shutil.rmtree(APP / f".{name}.new", ignore_errors=True)


def prepare(v: dict, auto: bool = False) -> dict:
    """Download, check and stage the release's code beside the app folders without touching the running code.
    Returns {"ok": True, "action": "staged", "stage", "tag", "latest", "packages"}, {"ok": False, "need_installer": True,
    "error"} or {"ok": False, "error"}. Raises UpdateBusy when another update is being installed."""
    with install_lock():
        _recover()
        return _prepare(v, auto)


def _prepare(v: dict, auto: bool) -> dict:
    k = v["kind"]
    if k == "dev":
        return {"ok": False, "error": "This copy runs from a git checkout. Run git pull in it to update."}
    if k == "linux-app":                       # the Linux download updates by hand for now (no in-place swap yet)
        return {"ok": False, "error": f"Meshpool {v['latest']} is available. Download Meshpool-Linux-x64.tar.gz from the "
                                      "release page and unpack it in place of this folder; your data is kept.",
                "url": v["url"]}
    inst = (lambda v, why: {"ok": False, "need_installer": True, "error": why}) if auto else _installer
    if k == "win-app" and not (APP.parent / "bin" / "meshpool.cmd").exists():
        # An install from before the terminal command moved to its own bin folder: a code update cannot fix the
        # launcher or the PATH entry (with Meshpool.exe beside it, `meshpool` would open the app), the installer does.
        return inst(v, "This update also changes how Meshpool is installed.")
    if not os.access(APP, os.W_OK):
        return inst(v, "This copy's folder is read-only.") if k == "win-app" else \
            {"ok": False, "error": f"Meshpool cannot write to {APP}. Run the installer again to update."}
    src_url, want = SRC.replace("{tag}", v["tag"]) if SRC else "", None
    if not SRC:
        # The code comes from the release's own archive, checked against the release's checksums: an archive
        # that is missing, unlisted or different is never installed.
        a = next((a for a in v.get("assets") or [] if a.get("name") == SRC_ASSET and a.get("url")), None)
        try:
            sums = _sums(v) or {}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Could not read the release's checksums ({type(e).__name__}). Nothing was "
                                          "changed. Try again."}
        if not a or SRC_ASSET not in sums:
            return inst(v, "This release cannot be checked for a code update.") if k == "win-app" else \
                {"ok": False, "error": "This release cannot be checked, so it was not installed. Download it from the "
                                       "release page.", "url": v.get("url", "")}
        src_url, want = a["url"], sums[SRC_ASSET]
    _sweep(v["tag"])
    stage = APP / f".update-{_safe(v['tag'])}-{secrets.token_hex(4)}"
    pkgs = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            tgz = _download(src_url, t / "src.tgz")
            if want and _sha256(tgz) != want and _sha256(tgz) != (_fresh_sums(v) or {}).get(SRC_ASSET):
                return {"ok": False, "error": "The update did not match the release's checksum, so nothing was changed. "
                                              "The release may have been updating on GitHub: try again in a few minutes."}
            with tarfile.open(tgz) as tf:
                try:
                    tf.extractall(t, filter="data")
                except TypeError:                         # Python without the tarfile filter: check paths ourselves
                    for m in tf.getmembers():
                        if m.name.startswith(("/", "\\")) or ".." in Path(m.name).parts or m.issym() or m.islnk():
                            return {"ok": False, "error": "The release download looks unsafe. Nothing was changed."}
                    tf.extractall(t)
            src = next((d for d in t.iterdir() if d.is_dir() and (d / "app" / "meshpool_harness").is_dir()), None)
            if not src:
                return {"ok": False, "error": "The release download did not contain the app. Nothing was changed."}
            new = src / "app"
            got = re.search(r'__version__ = "([^"]+)"', (new / "meshpool_harness" / "__init__.py").read_text())
            if not got or _ver(got.group(1)) != _ver(v["tag"]):
                return {"ok": False, "error": "The release download has a different version than its tag. Nothing was "
                                              "changed."}
            req_new_p, req_old_p = _requirements(src, new)
            req_new = req_new_p.read_text(encoding="utf-8") if req_new_p.exists() else ""
            req_old = req_old_p.read_text(encoding="utf-8") if req_old_p.exists() else None
            needs_pkgs = bool(req_new.strip() and (req_old is None or req_new.strip() != req_old.strip()))
            if needs_pkgs and k == "win-app":
                return inst(v, "This release needs new bundled packages.")
            try:
                stage.mkdir()
                dirs, files = [], []
                for name in CODE_DIRS:
                    if (new / name).is_dir():
                        shutil.copytree(new / name, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                        dirs.append(name)
                if (new / "requirements.txt").exists():
                    shutil.copy2(new / "requirements.txt", stage / "requirements.txt")
                    files.append("requirements.txt")
                if (src / "packaging" / CLI_REQ).exists():
                    shutil.copy2(src / "packaging" / CLI_REQ, stage / CLI_REQ)
                    files.append(CLI_REQ)
                for f in NOTICES:
                    if (src / f).exists() and (APP / f).exists():
                        shutil.copy2(src / f, stage / f)
                        files.append(f)
                for f in TOP_FILES:
                    if (new / f).exists():
                        shutil.copy2(new / f, stage / f)
                        files.append(f)
                old = None
                if needs_pkgs:
                    pkgs = str(stage / (CLI_REQ if req_new_p.name == CLI_REQ else "requirements.txt"))
                    old = str(req_old_p) if req_old_p.exists() else None
                fsio.atomic_write_json(stage / ".complete", {
                    "tag": v["tag"], "latest": v["latest"], "kind": k, "dirs": dirs, "files": files,
                    "packages": pkgs, "packages_old": old, "created": time.time()})   # last: the stage is whole
            except OSError as e:
                shutil.rmtree(stage, ignore_errors=True)
                if k == "win-app":
                    return inst(v, "Meshpool could not replace its files while it runs.")
                return {"ok": False, "error": f"Could not stage Meshpool's files ({type(e).__name__}: {e}). Nothing was "
                                              "changed. Close other Meshpool windows and try again."}
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"ok": True, "action": "staged", "stage": str(stage), "tag": v["tag"], "latest": v["latest"],
            "packages": pkgs}


def discard_stage(path) -> None:
    """Delete a stage folder made by prepare() (only a .update-* folder directly inside the app folder)."""
    p = Path(path)
    try:
        ok = p.name.startswith(".update-") and p.is_dir() and p.resolve().parent == APP.resolve()
    except OSError:
        return
    if ok:
        shutil.rmtree(p, ignore_errors=True)


def _pip(req: str) -> tuple:
    """(ok, the end of stderr) of pip install -r req."""
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req],
                           capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"
    return r.returncode == 0, (r.stderr or "")[-400:]


def apply_staged(st) -> dict:
    """Swap a staged update in. st is prepare()'s result or the stage folder. Raises UpdateBusy when another update is
    being installed. The caller restarts the app afterwards (restart())."""
    with install_lock():
        _recover()
        return _apply(st)


def _apply(st) -> dict:
    stage = Path(st["stage"] if isinstance(st, dict) else st)
    try:
        marker = fsio.read_json(stage / ".complete")
    except fsio.CorruptFile:
        marker = None
    if not isinstance(marker, dict) or not stage.name.startswith(".update-"):
        return {"ok": False, "need_installer": False,
                "error": "The update was not completely prepared, so nothing was changed. Try again."}
    k = marker.get("kind", "cli")
    pairs = []
    for name in marker.get("dirs") or []:
        dst = APP / name
        pairs.append((stage / name, dst, dst.with_name(f".{name}.old"), dst.exists()))
    info = {"tag": marker.get("tag", ""), "stage": str(stage)}
    _journal(pairs, info, "swapping")                 # before the first rename: a kill from here on is repaired
    try:
        done = _swap_dirs(pairs)
    except OSError as e:
        if not isinstance(e, _RollbackFailed):        # everything is back: nothing for start-up recovery to do
            (APP / JOURNAL).unlink(missing_ok=True)
            discard_stage(stage)
        return {"ok": False, "need_installer": k == "win-app",
                "error": f"Could not replace Meshpool's files ({type(e).__name__}: {e}). Nothing was changed. "
                         "Close other Meshpool windows and try again."}
    if marker.get("packages"):
        _journal(pairs, info, "packages")
        ok, err = _pip(marker["packages"])
        if not ok:
            back = _rollback(done)
            restored = False
            if back and marker.get("packages_old"):
                restored, _ = _pip(marker["packages_old"])
            if back:
                (APP / JOURNAL).unlink(missing_ok=True)
                discard_stage(stage)
                state = ("Meshpool's code is back to the previous version, and its packages were reinstalled from the "
                         "previous list." if restored else
                         "Meshpool's code is back to the previous version, but its packages may be partly new (the "
                         "previous list could not be reinstalled). If Meshpool misbehaves, run the installer again.")
            else:
                state = "Meshpool's files could not all be put back; the next start repairs them."
            return {"ok": False, "need_installer": False,
                    "error": f"Installing the new release's packages failed. {state} {err}".strip()}
    _journal(pairs, info, "files")
    for f in marker.get("files") or []:
        try:
            fsio.atomic_write_bytes(APP / f, (stage / f).read_bytes())
        except OSError:
            pass                                       # the record or a notice: the next update retries it
    (APP / JOURNAL).unlink(missing_ok=True)            # every step succeeded
    for _, old, _had in done:
        shutil.rmtree(old, ignore_errors=True)
    discard_stage(stage)
    _CACHE.clear()
    return {"ok": True, "action": "restart",
            "message": f"Updated to Meshpool {marker.get('latest', '')}. Restart to use it."}


def install(auto: bool = False) -> dict:
    """Prepare an update. auto=True is the background caller: it never opens an installer, and says need_installer
    when only the full installer will do. The in-place kinds are only staged here (the server applies and restarts in
    one step, apply_staged()); the running code is never swapped by this call."""
    v = check(force=True)
    if not v.get("ok") and not v.get("pending_restart"):
        return v
    if not v.get("ok") or not v["newer"]:
        if v.get("pending_restart"):
            return {"ok": True, "action": "restart",
                    "message": f"Meshpool {installed_version()} is installed. Restart to use it."}
        return {"ok": True, "action": "none", "message": f"Meshpool {v['current']} is the latest version."}
    if v["kind"] in ("mac-app", "win-app") and not auto:
        from meshpool_harness import autoupdate as AU
        r = AU.manual_install(v)
        if r is not None:
            return r
    try:
        return prepare(v, auto)
    except UpdateBusy as e:
        return {"ok": False, "busy": True, "error": str(e)}


def _relaunch_cmd() -> list:
    """The command that starts this app again. It runs in the user's folder, so the folder must not go on sys.path:
    a json.py or argparse.py there would be imported in place of Python's own. -P keeps it off (Python 3.11 and
    later); on 3.10 a tiny bootstrap removes it before anything is imported."""
    exe = sys.executable
    if os.name == "nt" and sys.argv[1:2] == ["app"]:     # the Windows app runs windowless: back under pythonw.exe
        w = Path(exe).with_name("pythonw.exe")
        exe = str(w) if w.exists() else exe
    if sys.version_info >= (3, 11):
        return [exe, "-P", "-m", "meshpool_harness", *sys.argv[1:]]
    boot = ("import sys, runpy\n"
            "if sys.path and sys.path[0] in ('', '.'):\n    del sys.path[0]\n"
            "runpy.run_module('meshpool_harness', run_name='__main__', alter_sys=True)\n")
    return [exe, "-c", boot, *sys.argv[1:]]


def wait_for_exit(pid: int, timeout: float) -> bool:
    """True when process pid has exited (or does not exist), False when it is still running after timeout seconds."""
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32                      # type: ignore[attr-defined]
        h = k.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not h:
            return True                                 # no such process (or already gone)
        try:
            return k.WaitForSingleObject(h, max(0, int(timeout * 1000))) == 0
        finally:
            k.CloseHandle(h)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass                                        # alive, owned by someone else
        else:
            try:                                        # an exited process nobody has reaped yet still answers kill 0
                with open(f"/proc/{int(pid)}/stat", "rb") as f:
                    if f.read().rsplit(b")", 1)[-1].split()[:1] == [b"Z"]:
                        return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def restart(on_quit=()) -> None:
    """Start a new copy of the app (it waits for this process to exit) and close this one. The quit hooks get at most
    QUIT_DEADLINE seconds: a hook that hangs must not keep the old process, and so the update, from finishing."""
    env = dict(os.environ, MESHPOOL_RESTART_WAIT=str(os.getpid()), PYTHONPATH=str(APP))
    kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(_relaunch_cmd(), env=env, cwd=os.getcwd(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)

    def run_hooks():
        for f in on_quit:
            try:
                f()
            except Exception:  # noqa: BLE001
                pass
    t = threading.Thread(target=run_hooks, daemon=True)
    t.start()
    t.join(QUIT_DEADLINE)
    os._exit(0)
