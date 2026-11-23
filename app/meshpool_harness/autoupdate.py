"""Automatic updates for the Mac and Windows apps: check, download, verify, stage, then install on quit or on one click.

The flow (see docs/plan/AUTO_UPDATE.md):
  1. start_background() checks GitHub shortly after the app starts and every 6 hours (updater.check).
  2. A newer stable release is downloaded in the background into <data folder>/updates/<tag>/ (resumable, with HTTP
     Range), and checked against the release's SHA256SUMS.txt. A release without that file, an installer it does
     not list, or a different hash is never installed (the download is deleted).
  3. Mac: the disk image is mounted read-only (hdiutil, nobrowse), the new Meshpool.app is copied to a staging folder
     and checked (codesign --verify --deep --strict, spctl, same bundle id, same team, the version of the release).
     Windows: the NSIS installer is kept as downloaded.
  4. The state becomes "ready": the page shows "Update ready - Restart". apply() starts a small detached helper and
     the app quits; with no click, on_quit() starts the same helper when the app quits (setting install_on_quit).
  5. The helper waits for the app to exit, swaps the app (Mac: rename old aside, move new in, keep the old one until
     the new one is running, roll back if it is not) or runs the installer silently (Windows: /S /D=<install dir>),
     then starts the app again.
Nothing inside a signed Mac app is ever modified. A pre-release is never downloaded or installed automatically, and a
developer checkout or command-line install is never touched (their update stays a button).
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import meshpool_harness as MH
from meshpool_harness import config as C
from meshpool_harness import fsio
from meshpool_harness import updater as U

MANIFEST = "update-manifest.json"      # published with each release; listed in SHA256SUMS.txt
INTERVAL = 6 * 3600
RETRY = 15 * 60                        # after a failed download or check
APP_KINDS = ("mac-app", "win-app")
MAC_BUNDLE_ID = "ai.meshpool.desktop"
_LOCK = threading.RLock()              # guards _ST
_RUN = threading.Lock()                # one download pipeline at a time
_ST: dict = {"state": "idle"}
_CFG: dict = {"busy": lambda: False, "started": False}


class UpdateError(Exception):
    """An update step failed; the message is for the user."""


# ------------------------------------------------------------------ settings and state
#
# One persisted state machine. The state lives in <data folder>/updates/state.json, written only through fsio (file
# lock + atomic write). _ST is the in-memory mirror (agent/tui.py reads _ST["state"] and _ST["latest"]). Every change
# of state goes through _go(), which refuses an edge that is not in TRANSITIONS.

STATES = ("idle", "checking", "available", "downloading", "verifying", "staging", "ready", "installing", "failed",
          "withdrawn", "uptodate")

TRANSITIONS: dict = {
    "idle": {"checking", "available", "downloading", "uptodate", "failed", "withdrawn", "installing"},
    "checking": {"idle", "uptodate", "available", "downloading", "failed", "withdrawn"},
    "available": {"idle", "checking", "uptodate", "downloading", "failed", "withdrawn", "installing"},
    "downloading": {"verifying", "staging", "ready", "failed", "available", "idle"},
    "verifying": {"staging", "ready", "failed", "idle"},
    "staging": {"ready", "failed", "idle"},
    "ready": {"installing", "idle", "withdrawn", "failed"},
    "installing": {"ready", "idle", "failed"},
    "failed": {"downloading", "idle", "available", "withdrawn", "uptodate", "installing", "checking"},
    "withdrawn": {"idle", "checking", "available", "downloading", "uptodate", "installing", "failed"},
    "uptodate": {"idle", "checking", "available", "downloading", "withdrawn", "installing", "failed"},
}
TRANSIENT = ("checking", "downloading", "verifying", "staging")      # a restart interrupts these: back to idle
WORKING = ("downloading", "verifying", "staging")

# What is written to state.json. Progress (done, total) and other in-memory-only values are not.
PERSIST = ("state", "mode", "tag", "latest", "file", "sha256", "staged", "stage_root", "needs_admin", "bundle", "url",
           "stage", "manual", "error", "message", "fail", "confirmed_at", "install_failures", "attempt",
           "check_error", "checked_at", "need_installer", "withdrawn_at")

# The fields of one update entry (the download, its files and what happened to it). Cleared when it is dropped.
_ENTRY = dict(mode=None, file=None, sha256=None, staged=None, stage_root=None, stage=None, needs_admin=None,
              bundle=None, manual=False, attempt=None, fail=None, install_failures=0, confirmed_at=None,
              error="", message="", done=0, total=0)
_TAG_DIR = re.compile(r"v?\d[\w.\-]*")
CONFIRM_FRESH = 24 * 3600              # a release confirmed as still published this recently installs on quit
BACKOFF_MAX = 24 * 3600


class BadTransition(Exception):
    """A state change that the transition table does not allow."""


def udir() -> Path:
    return C.home() / "updates"


def _write_json(p: Path, data: dict) -> None:
    """Crash-safe JSON write (unique temporary file, fsync, rename): kept for callers of the old helper."""
    fsio.atomic_write_json(p, data, indent=None)


def _state_path() -> Path:
    return udir() / "state.json"


def _persist_locked() -> None:
    snap = {k: _ST[k] for k in PERSIST if _ST.get(k) is not None}
    p = _state_path()
    with fsio.file_lock(p):
        fsio.atomic_write_json(p, snap)


def _persist() -> None:
    try:
        with _LOCK:
            _persist_locked()
    except (OSError, TimeoutError):
        pass                         # the in-memory state is still right; the next change writes it again


def _go(to: str, **fields) -> None:
    """Move to state ``to`` (refused when the table has no such edge), set ``fields``, and persist."""
    if to not in TRANSITIONS:
        raise BadTransition(f"unknown state {to!r}")
    with _LOCK:
        cur = _ST.get("state", "idle")
        if to != cur and to not in TRANSITIONS.get(cur, ()):
            raise BadTransition(f"{cur} -> {to}")
        _ST.update(fields)
        _ST["state"] = to
        _persist()


def _note(**fields) -> None:
    """Change fields without changing the state (and persist them)."""
    with _LOCK:
        _ST.update(fields)
        _persist()


def _set(**kw) -> None:
    """In-memory only (download progress): never written to the file."""
    with _LOCK:
        _ST.update(kw)


def _ensure() -> None:
    """Load the saved state once per data folder (lazily, on first use)."""
    if _CFG.get("loaded_for") != str(udir()):
        _load()


def _read_result() -> dict | None:
    try:
        r = fsio.read_json(udir() / "result.json")
    except fsio.CorruptFile:
        return None
    return r if isinstance(r, dict) else None


def _load() -> None:
    """Read state.json (a damaged one is set aside and counts as idle), migrate the old ready.json once, and resolve
    what the last run left open: an install that was started, a download that was cut off, files that are gone."""
    d = udir()
    with _LOCK:
        _CFG["loaded_for"] = str(d)
        _ST.clear()
        _ST["state"] = "idle"
        p = _state_path()
        data = None
        migrated = False
        try:
            data = fsio.read_json(p)
        except fsio.CorruptFile:
            fsio.quarantine(p)
        old = d / "ready.json"
        if old.exists():
            if data is None:
                try:
                    r = fsio.read_json(old)
                    if isinstance(r, dict) and r.get("state") == "ready":
                        data = {k: r[k] for k in PERSIST if k in r}
                        data["confirmed_at"] = None
                except fsio.CorruptFile:
                    pass
            old.unlink(missing_ok=True)
            migrated = True
        if isinstance(data, dict):
            _ST.update({k: data[k] for k in PERSIST if k in data})
        if _ST.get("state") not in TRANSITIONS:
            _ST["state"] = "idle"
        if _ST["state"] in TRANSIENT:
            _ST.update(state="idle", error="", done=0, total=0)
        if migrated:
            _persist()
        st = dict(_ST)
    if st["state"] == "installing":
        _resolve_installing(st)
    elif st["state"] == "ready" and not _intact(st):
        _drop(st)
    _sweep()


def _resolve_installing(st: dict) -> None:
    """The app was started again after an install was begun. Success means this copy is now the new version."""
    tag = st.get("tag") or ""
    if tag and U._key(MH.__version__) >= U._key(tag):
        _cleanup(st)
        _go("idle", tag="", **_ENTRY, need_installer=None)
        return
    r = _read_result() or {}
    msg = (r.get("message") or "").strip() or "The update did not finish."
    n = int(st.get("install_failures") or 0) + 1
    _go("ready", install_failures=n, error=msg, attempt=None)
    with _LOCK:
        now = dict(_ST)
    if not _intact(now):
        _drop(now)


def _read_settings() -> dict:
    p = udir() / "settings.json"
    try:
        saved = fsio.read_json(p, {})
    except fsio.CorruptFile:
        fsio.quarantine(p)
        saved = {}
    return saved if isinstance(saved, dict) else {}


def settings() -> dict:
    d = {"auto_download": True, "install_on_quit": True}
    saved = _read_settings()
    d.update({k: bool(saved[k]) for k in d if k in saved})
    if os.environ.get("MESHPOOL_AUTO_UPDATE", "").lower() in ("0", "off", "false", "no"):
        d["auto_download"] = False
    return d


def save_settings(body: dict) -> dict:
    before = settings()["auto_download"]
    d = settings()
    for k in ("auto_download", "install_on_quit"):
        if k in (body or {}):
            d[k] = bool(body[k])
    p = udir() / "settings.json"
    try:
        fsio.update_json(p, lambda cur: {**(cur if isinstance(cur, dict) else {}), **d}, {})
    except fsio.CorruptFile:
        fsio.quarantine(p)
        fsio.atomic_write_json(p, d)
    after = settings()
    if after["auto_download"] and not before:
        kick()                      # an available update downloads now, not at the next 6-hourly check (M10-10)
    return after


def _last_result(consume: bool = False) -> dict | None:
    """What the helper reported on its last run. Shown to the user once: reading does not mark it seen, only
    ``consume=True`` (the page's own status request) does."""
    p = udir() / "result.json"
    r = _read_result()
    if r is None or r.get("seen"):
        return None
    if consume:
        r = dict(r, seen=True)
        try:
            fsio.atomic_write_json(p, r)
        except OSError:
            pass
    return r


def _ready() -> dict | None:
    with _LOCK:
        return dict(_ST) if _ST.get("state") == "ready" else None


def status(consume: bool = False) -> dict:
    _ensure()
    with _LOCK:
        s = dict(_ST)
    s["settings"] = settings()
    s["kind"] = U.kind()
    s["current"] = MH.__version__
    s["last_result"] = _last_result(consume)
    cv = (getattr(U, "_CACHE", {}) or {}).get("v")
    if isinstance(cv, dict) and cv.get("ok", True):
        s["check"] = {k: cv.get(k) for k in ("latest", "tag", "newer", "kind", "url", "current", "checked_at",
                                               "pending_restart", "prerelease")}
    else:
        s["check"] = None
    s["check_error"] = s.get("check_error") or ""
    s["checked_at"] = s.get("checked_at") or (s["check"] or {}).get("checked_at")
    s["install_failures"] = int(s.get("install_failures") or 0)
    s["fail"] = s.get("fail") or None
    s["manual"] = bool(s.get("manual"))
    s["next_try"] = (s["fail"] or {}).get("next_try") if s.get("state") == "failed" else None
    return s


def _fp(sums) -> str:
    """A fingerprint of a release's SHA256SUMS (None = the release has none)."""
    if sums is None:
        return "none"
    return hashlib.sha256(json.dumps(sorted(sums.items())).encode("utf-8")).hexdigest()


def _files_ok(st: dict) -> bool:
    if st.get("mode") == "code":
        return bool(st.get("stage")) and Path(st["stage"]).exists()
    files = [st.get("file")] + ([st["staged"]] if st.get("staged") else [])
    return all(f and Path(f).exists() for f in files)


def _intact(st: dict) -> bool:
    """The entry can still be installed: its files (and the stage) are there and it is newer than this copy."""
    try:
        if U._key(st["tag"]) <= U._key(MH.__version__):
            return False
    except (KeyError, TypeError):
        return False
    return _files_ok(st)


# ------------------------------------------------------------------ cleanup (one place for every drop)

def _rm(p) -> None:
    if p:
        shutil.rmtree(str(p), ignore_errors=True)
        try:
            Path(p).unlink()
        except OSError:
            pass


def _cleanup(st: dict) -> None:
    """Delete everything one entry owns: its download folder, its stage (beside the app or in the data folder) and a
    staged code update."""
    tag = st.get("tag") or ""
    if tag and _TAG_DIR.fullmatch(tag):
        _rm(udir() / tag)
    sr = st.get("stage_root")
    if sr and (Path(sr).name.startswith(".Meshpool-update-") or udir() in Path(sr).parents):
        _rm(sr)
    stage = st.get("stage")
    if stage:
        disc = getattr(U, "discard_stage", None)
        try:
            if disc:
                disc(stage)
            else:
                _rm(stage)
        except Exception:  # noqa: BLE001
            pass


def _sweep() -> None:
    """Remove leftovers: stage folders beside the app and download folders that no live entry uses."""
    with _LOCK:
        s = dict(_ST)
    state, tag = s.get("state"), s.get("tag") or ""
    # an interrupted download (idle after a restart, but its tag still recorded) keeps its partial file to resume
    keep_tag = "" if state in ("uptodate", "withdrawn") else tag
    keep_stage = s.get("stage_root") if state in ("ready", "installing") else None
    working_name = f".Meshpool-update-{tag.lstrip('v')}" if state in WORKING and tag else None
    try:
        b = mac_bundle()
        if b is not None and b.parent.is_dir():
            for p in b.parent.glob(".Meshpool-update-*"):
                if str(p) != str(keep_stage) and p.name != working_name:
                    _rm(p)
    except OSError:
        pass
    d = udir()
    try:
        for old in d.iterdir() if d.is_dir() else []:
            if old.is_dir() and _TAG_DIR.fullmatch(old.name) and old.name != keep_tag:
                _rm(old)
    except OSError:
        pass


def _drop(st: dict, to: str = "idle", **fields) -> None:
    """Delete an entry's files and leave it: ready, installing or failed -> ``to`` (idle by default)."""
    _cleanup(st)
    with _LOCK:
        cur = _ST.get("state")
    f = {**_ENTRY, "tag": "", **fields}
    if cur == to:
        _note(**f)
    else:
        _go(to, **f)
    _sweep()


def _withdraw(st: dict) -> None:
    """The release is not published any more: delete it and tell the user why."""
    latest = st.get("latest") or (st.get("tag") or "").lstrip("v")
    msg = f"Meshpool {latest} was withdrawn by its maintainers, so it was not installed."
    _cleanup(st)
    _go("withdrawn", **{**_ENTRY, "error": msg, "message": msg, "withdrawn_at": time.time()})
    _sweep()


def _redownload() -> None:
    """Start the download again in the background (the files the entry needed are gone)."""
    try:
        kick(manual=True)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ download

def _open_range(url: str, start: int):
    h = {"User-Agent": "meshpool-updater"}
    if start:
        h["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60)


def download_resumable(url: str, dst: Path, expected_size: int | None = None, progress=None) -> Path:
    """Download url to dst, continuing a partial dst.part left by an earlier try (an interrupted download, or an app
    that was closed). A server that ignores Range sends the whole file again, and the file is started over."""
    part = dst.with_name(dst.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if expected_size and have > expected_size:
        part.unlink()
        have = 0
    if not (expected_size and have == expected_size):
        try:
            r = _open_range(url, have)
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:           # nothing left to send: the hash check decides
                r = None
            else:
                raise
        if r is not None:
            with r:
                resumed = have > 0 and getattr(r, "status", 200) == 206
                if have and not resumed:
                    have = 0
                done = have
                with open(part, "ab" if resumed else "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done)
    if expected_size and part.exists() and part.stat().st_size < expected_size:
        raise UpdateError("The download was interrupted; it will continue where it stopped.")   # .part is kept
    C.replace_file(part, dst, keep_src=True)    # keep the finished download if antivirus holds it: retry later
    return dst


def _checked(path: Path, name: str, v: dict, sums: dict) -> str:
    """The file's SHA-256 must be the one in SHA256SUMS.txt (read again once: a release being re-uploaded may have
    new sums). A file that does not match is deleted. Returns the hash that was accepted: it is what the Windows
    helper compares the installer with, so it is the one recorded."""
    got = U._sha256(path)
    if got != sums[name] and got != (U._fresh_sums(v) or {}).get(name):
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".part").unlink(missing_ok=True)
        raise UpdateError(f"The download of {name} did not match the release's checksum, so it was deleted. "
                          "It will be tried again later.")
    return got


def read_manifest(v: dict, sums: dict) -> dict | None:
    """The release's update-manifest.json ({"runtime_changed": bool, ...}), checked against SHA256SUMS.txt, or None
    when the release publishes none."""
    a = next((a for a in v.get("assets") or [] if a.get("name") == MANIFEST and a.get("url")), None)
    if not a:
        return None
    if MANIFEST not in sums:
        raise UpdateError("The release's checksums do not list its update manifest, so it was not used.")
    with U._get(a["url"]) as r:
        raw = r.read(1 << 20)
    import hashlib
    if hashlib.sha256(raw).hexdigest() != sums[MANIFEST]:
        raise UpdateError("The release's update manifest did not match its checksum, so it was not used.")
    try:
        m = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("The release's update manifest is not valid.") from e
    return m if isinstance(m, dict) else None


def runtime_changed(manifest: dict | None) -> bool:
    """True unless the release says, in its checked manifest, that only app code changed."""
    return not (manifest and manifest.get("runtime_changed") is False)


# ------------------------------------------------------------------ Mac: stage and verify

def _run(cmd: list, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def mac_bundle() -> Path | None:
    """The Meshpool.app this copy runs from (the folder ending in .app above the app code)."""
    for p in U.APP.parents:
        if p.suffix == ".app":
            return p
    return None


def _plist(app: Path) -> dict:
    with open(app / "Contents" / "Info.plist", "rb") as f:
        return plistlib.load(f)


def _team(app: Path) -> str:
    r = _run(["/usr/bin/codesign", "-dvv", str(app)])
    m = re.search(r"TeamIdentifier=(\S+)", (r.stderr or "") + (r.stdout or ""))
    return "" if not m or m.group(1) == "not" else m.group(1)


def verify_staged_mac(staged: Path, current: Path, v: dict) -> None:
    """Refuse a staged app that is not the signed Meshpool of this release. Raises UpdateError."""
    r = _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(staged)])
    if r.returncode:
        raise UpdateError("The new Meshpool app failed its code-signature check, so it was not installed.")
    try:
        new_info = _plist(staged)
        cur_id = _plist(current).get("CFBundleIdentifier") or MAC_BUNDLE_ID
    except (OSError, ValueError) as e:
        raise UpdateError("The new Meshpool app is not readable, so it was not installed.") from e
    if new_info.get("CFBundleIdentifier") != cur_id:
        raise UpdateError("The new app has a different bundle identifier from this one, so it was not installed.")
    if U._ver(str(new_info.get("CFBundleShortVersionString", ""))) != U._ver(v["tag"]):
        raise UpdateError("The new app's version is not the release's version, so it was not installed.")
    if U._key(v["tag"]) <= U._key(MH.__version__):
        raise UpdateError("The new app is not newer than this one, so it was not installed.")
    exe = new_info.get("CFBundleExecutable") or "Meshpool"
    if not (staged / "Contents" / "MacOS" / exe).is_file():
        raise UpdateError("The new Meshpool app has no program inside, so it was not installed.")
    cur_team, new_team = _team(current), _team(staged)
    if cur_team and new_team != cur_team:
        raise UpdateError("The new app is signed by a different developer than this one, so it was not installed.")
    # Gatekeeper's verdict is required whenever this copy has one (a signed and notarized lineage). A copy that is
    # itself only ad hoc signed cannot get one, so for it the checks above and the SHA-256 are what protect it.
    if _run(["/usr/sbin/spctl", "-a", "-t", "exec", "-vv", str(current)]).returncode == 0:
        if _run(["/usr/sbin/spctl", "-a", "-t", "exec", "-vv", str(staged)]).returncode:
            raise UpdateError("macOS does not accept the new Meshpool app (Gatekeeper), so it was not installed.")


def stage_mac(dmg: Path, v: dict, bundle: Path, work: Path) -> dict:
    """Copy the new app out of the disk image to a staging folder and verify it. Returns what the helper needs."""
    needs_admin = not os.access(bundle.parent, os.W_OK)
    stage_root = work / "stage" if needs_admin else bundle.parent / f".Meshpool-update-{v['tag'].lstrip('v')}"
    shutil.rmtree(stage_root, ignore_errors=True)
    stage_root.mkdir(parents=True)
    mnt = work / "mnt"
    mnt.mkdir(parents=True, exist_ok=True)
    r = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-noautoopen", "-plist",
              "-mountpoint", str(mnt), str(dmg)])
    if r.returncode:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise UpdateError("Could not open the downloaded disk image.")
    try:
        app = next((p for p in sorted(mnt.glob("*.app"))), None)
        if not app:
            raise UpdateError("The disk image has no Meshpool app inside.")
        staged = stage_root / app.name
        if _run(["/usr/bin/ditto", str(app), str(staged)]).returncode:
            raise UpdateError("Could not copy the new app (is the disk full?).")
    except UpdateError:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise
    finally:
        _run(["hdiutil", "detach", str(mnt), "-force"])
    try:
        verify_staged_mac(staged, bundle, v)
    except UpdateError:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise
    return {"staged": str(staged), "stage_root": str(stage_root), "needs_admin": needs_admin, "bundle": str(bundle)}


def mac_helper_script(pid: int, old_app: str, new_app: str, stage_root: str, backup: str, log: str, result: str,
                      needs_admin: bool) -> str:
    """The bash script that finishes a Mac update after the app quits. Every value is shell-quoted. Nothing in the
    script is run as root except the few mv/rm commands passed inline to osascript when the app's folder is
    protected, so a script a user process could rewrite never runs with privileges."""
    for s in (old_app, new_app, stage_root, backup, log, result):
        if "\n" in s or "\r" in s:
            raise UpdateError("A folder name has a line break in it, so the update cannot be installed.")
    q = shlex.quote
    return f"""#!/bin/bash
# Meshpool update helper, written by the app for one update. It waits for Meshpool to quit, swaps the app, and opens it.
PID={int(pid)}
OLD={q(old_app)}
NEW={q(new_app)}
STAGE={q(stage_root)}
BAK={q(backup)}
LOG={q(log)}
RESULT={q(result)}
NEED_ADMIN={1 if needs_admin else 0}
say() {{ printf '%s %s\\n' "$(date '+%F %T')" "$*" >> "$LOG"; }}
finish() {{ printf '{{"ok": %s, "message": "%s"}}\\n' "$1" "$2" > "$RESULT"; }}
# esc: a value inside an AppleScript string. priv: run one shell command as administrator (asks the user).
esc() {{ printf '%s' "$1" | sed 's/\\\\/\\\\\\\\/g; s/"/\\\\"/g'; }}
priv() {{ /usr/bin/osascript -e "do shell script \\"$(esc "$1")\\" with administrator privileges" >> "$LOG" 2>&1; }}
sh_user() {{ /bin/bash -c "$1" >> "$LOG" 2>&1; }}
run() {{ if [ "$NEED_ADMIN" = 1 ]; then priv "$1"; else sh_user "$1"; fi; }}
Q_OLD=$(printf '%q' "$OLD"); Q_NEW=$(printf '%q' "$NEW"); Q_BAK=$(printf '%q' "$BAK")

say "waiting for Meshpool (pid $PID) to quit"
n=0
while kill -0 "$PID" 2>/dev/null; do
  n=$((n + 1))
  if [ "$n" -gt 240 ]; then say "Meshpool did not quit in time; update left for next time"; finish false "Meshpool did not close, so the update was not installed."; exit 1; fi
  sleep 0.5
done

# last check before the swap: the staged app must still be the one that was verified
if ! /usr/bin/codesign --verify --deep --strict "$NEW" >> "$LOG" 2>&1; then
  say "staged app failed codesign; not installing"; finish false "The new app failed its signature check, so it was not installed."
  rm -rf "$STAGE"; open "$OLD"; exit 3
fi

say "swapping $OLD"
# the old app is renamed aside (not deleted), the new one moved in; if the second move fails, the first is undone
if ! run "rm -rf $Q_BAK && mv $Q_OLD $Q_BAK && {{ mv $Q_NEW $Q_OLD || {{ mv $Q_BAK $Q_OLD; exit 5; }}; }}"; then
  say "swap failed or was cancelled; rolled back"
  [ -d "$OLD" ] || run "mv $Q_BAK $Q_OLD"
  finish false "Meshpool could not be updated (permission was not given). It will ask again at the next update."
  open "$OLD"; exit 4
fi
# a quarantine flag would make macOS show the first-open warning again: remove it only when it is there
if /usr/bin/xattr -p com.apple.quarantine "$OLD" >/dev/null 2>&1; then run "/usr/bin/xattr -dr com.apple.quarantine $Q_OLD"; fi

say "opening the new app"
open "$OLD"
ok=0
for i in $(seq 1 60); do
  if pgrep -f "$OLD/Contents/MacOS/" >/dev/null 2>&1; then ok=1; break; fi
  sleep 0.5
done
if [ "$ok" = 1 ]; then
  say "new app is running; removing the old copy"
  run "rm -rf $Q_BAK"; rm -rf "$STAGE"
  finish true "Meshpool was updated."
  exit 0
fi
say "new app did not start; rolling back to the old one"
run "rm -rf $Q_OLD.failed; mv $Q_OLD $Q_OLD.failed && mv $Q_BAK $Q_OLD; rm -rf $Q_OLD.failed"
rm -rf "$STAGE"
finish false "The new Meshpool did not start, so the old one was put back."
open "$OLD"
exit 6
"""


# ------------------------------------------------------------------ Windows

def win_installer_args(installer: str, install_dir: str) -> list:
    """The NSIS installer's silent command line. /D=dir must be last and is not quoted, even with spaces."""
    return [str(installer), "/S", f"/D={install_dir}"]


def _ps(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def win_helper_script(pid: int, installer: str, sha256: str, install_dir: str, exe: str, log: str, result: str) -> str:
    """The PowerShell script that finishes a Windows update after the app quits: wait, check the installer again,
    run it silently, start the app."""
    return f"""$ErrorActionPreference = 'Stop'
$appPid = {int(pid)}
$installer = {_ps(installer)}
$want = {_ps(sha256.lower())}
$dir = {_ps(install_dir)}
$exe = {_ps(exe)}
$log = {_ps(log)}
$result = {_ps(result)}
function Say($m) {{ Add-Content -LiteralPath $log -Value ("{{0}} {{1}}" -f (Get-Date -Format s), $m) }}
function Finish($ok, $msg) {{ Set-Content -LiteralPath $result -Value (ConvertTo-Json @{{ok = $ok; message = $msg}}) }}
try {{
  Say "waiting for Meshpool (pid $appPid) to quit"
  $p = Get-Process -Id $appPid -ErrorAction SilentlyContinue
  if ($p) {{ $null = $p.WaitForExit(120000) }}
  if (Get-Process -Id $appPid -ErrorAction SilentlyContinue) {{ throw 'Meshpool did not close in time' }}
  if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLower() -ne $want) {{ throw 'The installer changed after it was checked' }}
  $sig = (Get-AuthenticodeSignature -LiteralPath $installer).Status.ToString()
  if ($sig -eq 'HashMismatch' -or $sig -eq 'NotTrusted') {{ throw "The installer's signature is not valid ($sig)" }}
  Say "running the installer silently into $dir"
  $run = Start-Process -FilePath $installer -ArgumentList '/S', ('/D=' + $dir) -Wait -PassThru
  if ($run.ExitCode -ne 0) {{ throw "The installer stopped with code $($run.ExitCode)" }}
  Finish $true 'Meshpool was updated.'
}} catch {{
  Say ("failed: " + $_.Exception.Message)
  Finish $false ("Meshpool could not be updated: " + $_.Exception.Message)
}}
Say 'starting Meshpool'
Start-Process -FilePath $exe
"""


def win_helper_cmd(script: str) -> list:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(script)]



# ------------------------------------------------------------------ the pipeline

def _asset_for(v: dict, sums: dict) -> dict:
    a = U._asset(v)
    if not a:
        raise UpdateError("This release has no installer for this computer.")
    if a["name"] not in sums:
        raise UpdateError(f"The release's checksums do not list {a['name']}, so it was not downloaded.")
    return a


def _fail(tag: str, latest: str, url: str, error: str, sums_fp: str | None = None, manual: bool = False,
          retry_now: bool = False) -> None:
    """Record a failed pipeline run for ``tag``: back off exponentially (RETRY, 2x, 4x ... up to 24 h)."""
    with _LOCK:
        prev = _ST.get("fail") or {}
    count = int(prev.get("count") or 0) + 1 if prev.get("tag") == tag else 1
    next_try = time.time() + (0 if retry_now else min(RETRY * 2 ** (count - 1), BACKOFF_MAX))
    if sums_fp is None and prev.get("tag") == tag:
        sums_fp = prev.get("sums_fp")
    rec = {"tag": tag, "count": count, "next_try": next_try, "sums_fp": sums_fp, "error": error}
    _go("failed", **{**_ENTRY, "tag": tag, "latest": latest, "url": url, "error": error, "fail": rec,
                     "manual": manual})
    _sweep()                                    # the stage beside the app goes; the partial download stays to resume


def pipeline(v: dict, download: bool = True, manual: bool = False) -> None:
    """The background work for one release: download, verify, stage. Sets the state as it goes; never raises.
    ``manual`` is the user asking: only then is a prerelease downloaded."""
    _ensure()
    if not _RUN.acquire(blocking=False):
        return
    ctx: dict = {}
    tag, latest, url = v.get("tag", ""), v.get("latest", ""), v.get("url", "")
    is_pre = bool(v.get("prerelease") or U._is_pre(tag))
    try:
        with _LOCK:
            st = dict(_ST)
        if st.get("state") == "installing":
            return
        if st.get("state") == "ready":
            if st.get("tag") == tag and _intact(st):
                return
            _drop(st)                                   # superseded, or its files are gone
        _pipeline(v, download, manual, ctx)
    except UpdateError as e:
        _fail(tag, latest, url, str(e), ctx.get("sums_fp"), is_pre)
    except (OSError, urllib.error.URLError, subprocess.SubprocessError) as e:
        _fail(tag, latest, url, f"Could not download the update ({type(e).__name__}). It will be tried again.",
              ctx.get("sums_fp"), is_pre)
    except BadTransition:
        pass
    except Exception as e:  # noqa: BLE001
        try:
            _fail(tag, latest, url, f"The update failed ({type(e).__name__}).", ctx.get("sums_fp"), is_pre)
        except BadTransition:
            pass
    finally:
        _RUN.release()


def _pipeline(v: dict, download: bool, manual: bool, ctx: dict) -> None:
    k = v["kind"]
    is_pre = bool(v.get("prerelease") or U._is_pre(v["tag"]))
    base = dict(tag=v["tag"], latest=v["latest"], url=v.get("url", ""), error="", message="", done=0, total=0,
                manual=is_pre)
    if k not in APP_KINDS:
        _go("available", fail=None, **base)
        return
    if is_pre and not manual:                                   # never automatic
        _go("available", fail=None, **base)
        return
    if not download:
        _go("available", fail=None, **base)
        return
    if k == "mac-app" and (mac_bundle() is None or "AppTranslocation" in str(mac_bundle())
                           or str(mac_bundle()).startswith("/Volumes/")):
        raise UpdateError("Move Meshpool to your Applications folder and open it from there, then it can update itself.")
    sums = U._sums(v)
    ctx["sums_fp"] = _fp(sums)
    if sums is None:
        raise UpdateError("This release has no checksums, so its download cannot be checked. It was not installed.")
    manifest = read_manifest(v, sums)
    legacy_layout = k == "win-app" and not (U.APP.parent / "bin" / "meshpool.cmd").exists()
    if (k == "win-app" and not runtime_changed(manifest) and not legacy_layout
            and _ST.get("need_installer") != v["tag"]):
        if _code_update(v, base):
            return
    a = _asset_for(v, sums)
    work = udir() / v["tag"]
    work.mkdir(parents=True, exist_ok=True)
    size = a.get("size") or 0
    if size and shutil.disk_usage(work).free < size * 2.3:
        raise UpdateError("There is not enough free disk space to download the update. It will be tried again.")
    dst = work / a["name"]
    _go("downloading", mode="mac-swap" if k == "mac-app" else "win-installer", **base)
    _sweep()                                                    # earlier releases' leftovers
    if not dst.exists():
        download_resumable(a["url"], dst, size or None, lambda n: _set(done=n, total=size))
    _go("verifying")
    got = _checked(dst, a["name"], v, sums)                      # the hash that was accepted is the one recorded
    if k == "win-app":
        st = dict(mode="win-installer", file=str(dst), sha256=got)
    else:
        _go("staging")
        st = dict(mode="mac-swap", file=str(dst), sha256=got, **stage_mac(dst, v, mac_bundle(), work))
    _go("ready", confirmed_at=time.time(), fail=None, install_failures=0, attempt=None, stage=None, **st, **base)


def _code_update(v: dict, base: dict) -> bool:
    """Windows, release says only code changed: stage the code update (updater.prepare changes nothing that runs).
    True when it is ready; False when the release needs the full installer (the same run goes on to download it)."""
    _go("downloading", mode="code", **base)
    r = U.prepare(v, auto=True)
    if r.get("ok") and r.get("stage"):
        _go("ready", mode="code", stage=r["stage"], file=None, sha256=None, confirmed_at=time.time(), fail=None,
            install_failures=0, attempt=None, **base)
        return True
    if r.get("need_installer"):
        _note(need_installer=v["tag"])
        return False
    raise UpdateError(r.get("error") or "The code update failed.")


def _failed_may_wait(v: dict, manual: bool) -> bool:
    """True while the release that failed last time is in its back-off and nothing has changed (M10-06)."""
    with _LOCK:
        s = dict(_ST)
    fl = s.get("fail") or {}
    if s.get("state") != "failed" or fl.get("tag") != v.get("tag") or manual:
        return False
    if time.time() >= float(fl.get("next_try") or 0):
        return False
    if fl.get("sums_fp") is None:
        return True
    try:
        sums = U._sums(v)
    except Exception:  # noqa: BLE001
        return True
    return _fp(sums) == fl["sums_fp"]


def tick(force: bool = True, manual: bool = False) -> dict:
    """One check, and the download when the setting allows it (always when ``manual``: the user asked)."""
    _ensure()
    if os.environ.get("MESHPOOL_NO_UPDATE"):
        return status()
    with _LOCK:
        prev = _ST.get("state")
    if prev in ("idle", "uptodate", "withdrawn"):
        _go("checking")
    try:
        v = U.check(force=force)
    except Exception as e:  # noqa: BLE001
        v = {"ok": False, "error": f"The update check failed ({type(e).__name__})."}
    now = time.time()
    with _LOCK:
        if _ST.get("state") == "checking":
            _go(prev)                                           # a check never leaves its own state behind
    if not v.get("ok"):
        _note(check_error=v.get("error") or "The update check failed.", checked_at=now)     # M10-03
        return status()
    _note(check_error="", checked_at=now)
    with _LOCK:
        s = dict(_ST)
    state, tag = s.get("state"), s.get("tag") or ""
    tags = v.get("tags")
    if state in ("ready", "failed", "available") and tag and tags is not None and tag not in tags \
            and tag != v.get("tag"):
        _withdraw(s)                                            # M10-02: no longer published
        with _LOCK:
            s = dict(_ST)
        state, tag = "withdrawn", ""
    elif state == "ready" and (tag == v.get("tag") or (tags is not None and tag in tags)):
        _note(confirmed_at=now)
    if state in ("installing",) + WORKING:
        return status()
    if not v.get("newer"):
        if state == "ready":
            return status()
        if state == "withdrawn" and now - float(s.get("withdrawn_at") or 0) < CONFIRM_FRESH:
            return status()
        _go("uptodate", **{**_ENTRY, "tag": "", "latest": v.get("latest", "")})
        _sweep()
        return status()
    if state == "ready":
        if tag == v["tag"]:
            if _intact(s):
                return status()
            _drop(s)                                            # M10-04: its files are gone: download again
        elif U._key(v["tag"]) <= U._key(tag):
            return status()                                     # a ready update newer than the offer: keep it
    if v["kind"] == "dev":
        _go("available", tag=v["tag"], latest=v["latest"], url=v.get("url", ""))
        return status()
    if _failed_may_wait(v, manual):
        return status()
    pipeline(v, download=True if manual else settings()["auto_download"], manual=manual)
    return status()


def kick(manual: bool = False) -> None:
    """Run tick() in the background (the page asked for a check, or for the download)."""
    def go():
        try:
            tick(manual=manual)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=go, daemon=True).start()


def manual_install(v: dict) -> dict | None:
    """updater.install() for the Mac and Windows apps: start the background download (it may already be running or
    done) and tell the page. Returns None for a Windows release that changed only code, which installs in place."""
    _ensure()
    k = v["kind"]
    with _LOCK:
        s = dict(_ST)
    st = s.get("state")
    if st == "ready" and s.get("tag") == v["tag"]:
        if _intact(s):
            return {"ok": True, "action": "ready", "message": f"Meshpool {v['latest']} is ready. Restart to update."}
        _drop(s)
        st = "idle"
    if st == "installing":
        return {"ok": False, "error": BUSY_MSG}
    if k == "win-app" and (U.APP.parent / "bin" / "meshpool.cmd").exists():
        try:
            sums = U._sums(v)
            manifest = read_manifest(v, sums) if sums else None
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Could not read the release's checksums ({type(e).__name__}). Try again."}
        if sums and not runtime_changed(manifest) and s.get("need_installer") != v["tag"]:
            return None
    if st in WORKING:
        return {"ok": True, "action": "downloading", "message": "The update is downloading in the background."}
    threading.Thread(target=pipeline, args=(v, True, True), daemon=True).start()
    return {"ok": True, "action": "downloading",
            "message": f"Downloading Meshpool {v['latest']} in the background. Meshpool will say when it is ready."}


# ------------------------------------------------------------------ apply

BUSY_MSG = "An update is already being installed."


def _launch_detached(cmd: list) -> None:
    if os.name == "nt":
        kw = {"creationflags": 0x00000008 | 0x00000200 | 0x08000000}
    else:
        kw = {"start_new_session": True}
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def launch_helper(st: dict) -> dict:
    """Write and start the detached helper for a ready update. The caller quits the app right after."""
    d = udir()
    d.mkdir(parents=True, exist_ok=True)
    log, result = d / "apply.log", d / "result.json"
    if not _files_ok(st):
        _drop(st)
        _redownload()
        return {"ok": False, "error": "The downloaded update is no longer there. It will be downloaded again."}
    result.unlink(missing_ok=True)
    if st["mode"] == "mac-swap":
        staged, bundle = Path(st["staged"]), Path(st["bundle"])
        if not bundle.is_dir():
            return {"ok": False, "error": "The Meshpool app to update is no longer where it was."}
        try:
            verify_staged_mac(staged, bundle, {"tag": st["tag"]})     # again, just before it is used
            text = mac_helper_script(os.getpid(), str(bundle), str(staged), st["stage_root"],
                                     str(bundle.with_name(bundle.name + ".previous")), str(log), str(result),
                                     bool(st.get("needs_admin")))
        except UpdateError as e:
            return {"ok": False, "error": str(e)}
        script = d / "apply-mac.sh"
        script.write_text(text, encoding="utf-8")
        script.chmod(0o700)
        _launch_detached(["/usr/bin/nohup", "/bin/bash", str(script)])
    elif st["mode"] == "win-installer":
        inst = Path(st["file"])
        exe = U.APP.parent / "Meshpool.exe"
        script = d / "apply-win.ps1"
        script.write_text(win_helper_script(os.getpid(), str(inst), st["sha256"], str(U.APP.parent), str(exe),
                                            str(log), str(result)), encoding="utf-8-sig")
        _launch_detached(win_helper_cmd(str(script)))
    else:
        return {"ok": False, "error": "Nothing to install."}
    return {"ok": True, "mode": st["mode"]}


def _start_install(st: dict) -> dict:
    """ready -> installing (saved before anything starts, so a crash or a failed helper is seen at the next start),
    then the helper. The caller holds the install lock."""
    _go("installing", attempt={"tag": st.get("tag"), "started": time.time(), "pid": os.getpid()}, error="")
    try:
        r = launch_helper(st)
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "error": f"Could not start the update ({type(e).__name__})."}
    if not r.get("ok"):
        with _LOCK:
            cur = _ST.get("state")
        if cur == "installing":
            _go("ready", error=r.get("error", ""), attempt=None)
    return r


def apply_code(st: dict) -> dict:
    """Swap a staged code update in (Windows, code-only release). On success the state stays ``installing``: the
    caller restarts or quits, and the next start resolves it. A failure is recorded; when the release needs the full
    installer, the next tick downloads that."""
    try:
        with U.install_lock():
            _go("installing", attempt={"tag": st.get("tag"), "started": time.time(), "pid": os.getpid()}, error="")
            try:
                r = U.apply_staged(st)
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": f"The code update failed ({type(e).__name__})."}
            if r.get("ok"):
                return {"ok": True, "mode": "code"}
            err = r.get("error") or "The code update failed."
            need = bool(r.get("need_installer"))
            _cleanup(st)
            if need:
                _note(need_installer=st.get("tag"))
            _fail(st.get("tag", ""), st.get("latest", ""), st.get("url", ""), err, manual=bool(st.get("manual")),
                  retry_now=need)
            return {"ok": False, "error": err, "need_installer": need}
    except U.UpdateBusy:
        return {"ok": False, "error": BUSY_MSG}


def _reconfirm(st: dict) -> str:
    """Ask GitHub (5 s) whether the ready release is still published: "ok", "withdrawn" (and it is dropped) or
    "unknown" (the check failed)."""
    try:
        v = U.check(force=True, timeout=5)
    except Exception:  # noqa: BLE001
        return "unknown"
    if not v.get("ok"):
        return "unknown"
    tags, tag = v.get("tags"), st.get("tag")
    if tag == v.get("tag") or (tags is not None and tag in tags):
        _note(confirmed_at=time.time())
        return "ok"
    if tags is not None:
        with _LOCK:
            now = dict(_ST)
        if now.get("state") == "ready":
            _withdraw(now)
        return "withdrawn"
    return "unknown"


def begin_apply() -> dict:
    """The page's "Restart to update": start the helper (or swap the staged code in). After an ok result the caller
    closes the app (finish_quit) or, for mode "code", restarts it."""
    _ensure()
    with _LOCK:
        s = dict(_ST)
    if s.get("state") == "installing":
        return {"ok": True, "mode": s.get("mode")}
    if s.get("state") != "ready":
        return {"ok": False, "error": "No update is ready yet."}
    if not _intact(s):
        files = _files_ok(s)
        _drop(s)
        if files:
            return {"ok": False, "error": "This update is already installed."}
        _redownload()
        return {"ok": False, "error": "The downloaded update is no longer there. It will be downloaded again."}
    if _reconfirm(s) == "withdrawn":
        return {"ok": False, "error": _ST.get("message") or "This update was withdrawn, so it was not installed."}
    if s.get("mode") == "code":
        return apply_code(s)
    try:
        with U.install_lock():
            return _start_install(s)
    except U.UpdateBusy:
        return {"ok": False, "error": BUSY_MSG}


def finish_quit(on_quit=()) -> None:
    for f in on_quit:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.5)
    os._exit(0)


def on_quit() -> None:
    """Called when the app quits for any reason: a downloaded update installs now (the setting can turn this off).
    Never after a failed install, never a prerelease the user chose by hand, and only a release that is still
    published (confirmed within 24 h, or by a quick check now)."""
    try:
        _ensure()
        s = _ready()
        if not s or not settings()["install_on_quit"]:
            return
        if int(s.get("install_failures") or 0) or s.get("manual"):
            return
        if not _intact(s):
            _drop(s)
            return
        conf = s.get("confirmed_at")
        if not (conf and time.time() - float(conf) < CONFIRM_FRESH) and _reconfirm(s) != "ok":
            return
        if s.get("mode") == "code":
            apply_code(s)
            return
        with U.install_lock():
            _start_install(s)
    except U.UpdateBusy:
        pass
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ the server's in-place install

def mark_installing(tag: str, latest: str, mode: str) -> bool:
    """The server is installing in place (a command-line copy, or a code update it applies itself): show it."""
    _ensure()
    with _LOCK:
        s = dict(_ST)
    if s.get("state") == "ready":
        _cleanup(s)
    try:
        _go("installing", **{**_ENTRY, "tag": tag, "latest": latest, "mode": mode,
                             "attempt": {"tag": tag, "started": time.time(), "pid": os.getpid()}})
    except BadTransition:
        return False
    return True


def install_finished(ok: bool, error: str = "") -> None:
    """The in-place install ended. Success keeps ``installing`` until the app restarts (the next start resolves it);
    a failure is a ``failed`` entry with the message."""
    with _LOCK:
        s = dict(_ST)
    if s.get("state") != "installing":
        return
    if ok:
        _note(error="")
        return
    _fail(s.get("tag", ""), s.get("latest", ""), s.get("url", ""), error or "The update failed.",
          manual=bool(s.get("manual")))


# ------------------------------------------------------------------ background

def _next_wait(now: float | None = None) -> float:
    """Seconds to the next run: the next scheduled check (RETRY after a failed check) or the failed release's
    next_try, whichever is earlier."""
    now = time.time() if now is None else now
    with _LOCK:
        s = dict(_ST)
    wait = (RETRY if s.get("check_error") else INTERVAL) + random.randint(0, 300)
    nt = (s.get("fail") or {}).get("next_try")
    if s.get("state") == "failed" and nt:
        wait = min(wait, max(float(nt) - now, 0) + random.randint(1, 30))
    return wait


def start_background(busy=None, first_delay: float = 15.0) -> None:
    """Check shortly after start and every 6 hours (after a failed check, every 15 minutes; a failed download waits for
    its own back-off). Not for a git checkout."""
    if _CFG["started"] or U.kind() == "dev" or os.environ.get("MESHPOOL_NO_UPDATE"):
        return
    _CFG["started"] = True
    if busy:
        _CFG["busy"] = busy
    _load()

    def loop():
        time.sleep(first_delay)
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001
                try:
                    _note(check_error="The update check failed.", checked_at=time.time())
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(_next_wait())
    threading.Thread(target=loop, daemon=True, name="meshpool-updates").start()
