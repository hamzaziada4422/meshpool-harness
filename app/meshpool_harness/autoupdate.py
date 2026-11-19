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

def udir() -> Path:
    return C.home() / "updates"


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    C.replace_file(tmp, p)


def settings() -> dict:
    d = {"auto_download": True, "install_on_quit": True}
    try:
        saved = json.loads((udir() / "settings.json").read_text(encoding="utf-8"))
        d.update({k: bool(saved[k]) for k in d if k in saved})
    except (OSError, ValueError, TypeError):
        pass
    if os.environ.get("MESHPOOL_AUTO_UPDATE", "").lower() in ("0", "off", "false", "no"):
        d["auto_download"] = False
    return d


def save_settings(body: dict) -> dict:
    d = settings()
    for k in ("auto_download", "install_on_quit"):
        if k in (body or {}):
            d[k] = bool(body[k])
    _write_json(udir() / "settings.json", d)
    return d


def _set(**kw) -> None:
    with _LOCK:
        _ST.update(kw)


def status() -> dict:
    with _LOCK:
        s = dict(_ST)
    s["settings"] = settings()
    s["kind"] = U.kind()
    s["current"] = MH.__version__
    s["last_result"] = _last_result()
    return s


def _last_result() -> dict | None:
    """What the helper reported on its last run, once: shown to the user after the app comes back."""
    p = udir() / "result.json"
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if r.get("seen"):
        return None
    r["seen"] = True
    try:
        _write_json(p, r)
    except OSError:
        pass
    return r


def _ready() -> dict | None:
    with _LOCK:
        return dict(_ST) if _ST.get("state") == "ready" else None


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
    C.replace_file(part, dst)
    return dst


def _checked(path: Path, name: str, v: dict, sums: dict) -> None:
    """The file's SHA-256 must be the one in SHA256SUMS.txt (read again once: a release being re-uploaded may have
    new sums). A file that does not match is deleted."""
    got = U._sha256(path)
    if got != sums[name] and got != (U._fresh_sums(v) or {}).get(name):
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".part").unlink(missing_ok=True)
        raise UpdateError(f"The download of {name} did not match the release's checksum, so it was deleted. "
                          "It will be tried again later.")


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
  open "$OLD"; exit 3
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


def _persistable(st: dict) -> dict:
    keep = ("state", "mode", "tag", "latest", "file", "sha256", "staged", "stage_root", "needs_admin", "bundle", "url")
    return {k: st[k] for k in keep if k in st}


def pipeline(v: dict, download: bool = True) -> None:
    """The background work for one release: download, verify, stage. Sets the state as it goes; never raises."""
    if not _RUN.acquire(blocking=False):
        return
    try:
        _pipeline(v, download)
    except UpdateError as e:
        _set(state="error", error=str(e), url=v.get("url", ""), tag=v.get("tag", ""), latest=v.get("latest", ""))
    except (OSError, urllib.error.URLError, subprocess.SubprocessError) as e:
        _set(state="error", error=f"Could not download the update ({type(e).__name__}). It will be tried again.",
             url=v.get("url", ""), tag=v.get("tag", ""), latest=v.get("latest", ""))
    except Exception as e:  # noqa: BLE001
        _set(state="error", error=f"The update failed ({type(e).__name__}).", url=v.get("url", ""))
    finally:
        _RUN.release()


def _pipeline(v: dict, download: bool) -> None:
    base = dict(tag=v["tag"], latest=v["latest"], url=v.get("url", ""), error="", done=0, total=0)
    k = v["kind"]
    if k not in APP_KINDS:
        _set(state="available", **base)
        return
    if v.get("prerelease") or U._is_pre(v["tag"]):             # never automatic
        _set(state="available", manual=True, **base)
        return
    if not download:
        _set(state="available", **base)
        return
    if k == "mac-app" and (mac_bundle() is None or "AppTranslocation" in str(mac_bundle())
                           or str(mac_bundle()).startswith("/Volumes/")):
        raise UpdateError("Move Meshpool to your Applications folder and open it from there, then it can update itself.")
    sums = U._sums(v)
    if sums is None:
        raise UpdateError("This release has no checksums, so its download cannot be checked. It was not installed.")
    manifest = read_manifest(v, sums)
    legacy_layout = k == "win-app" and not (U.APP.parent / "bin" / "meshpool.cmd").exists()
    if k == "win-app" and not runtime_changed(manifest) and not legacy_layout:
        _code_update(v, base)
        return
    a = _asset_for(v, sums)
    work = udir() / v["tag"]
    work.mkdir(parents=True, exist_ok=True)
    for old in udir().iterdir() if udir().is_dir() else []:     # earlier releases' leftovers
        if old.is_dir() and old.name != v["tag"] and re.fullmatch(r"v?\d[\w.\-]*", old.name):
            shutil.rmtree(old, ignore_errors=True)
    size = a.get("size") or 0
    if size and shutil.disk_usage(work).free < size * 2.3:
        raise UpdateError("There is not enough free disk space to download the update. It will be tried again.")
    dst = work / a["name"]
    _set(state="downloading", mode="mac-swap" if k == "mac-app" else "win-installer", **base)
    if not dst.exists():
        download_resumable(a["url"], dst, size or None, lambda n: _set(done=n, total=size))
    _set(state="verifying")
    _checked(dst, a["name"], v, sums)
    if k == "win-app":
        st = dict(state="ready", mode="win-installer", file=str(dst), sha256=sums[a["name"]])
    else:
        _set(state="staging")
        st = dict(state="ready", mode="mac-swap", file=str(dst), sha256=sums[a["name"]],
                  **stage_mac(dst, v, mac_bundle(), work))
    _set(**st, **base)
    try:
        _write_json(udir() / "ready.json", _persistable(_ST))
    except OSError:
        pass


def _code_update(v: dict, base: dict) -> None:
    """Windows, release says only code changed: the existing in-place code update, run in the background when no
    task is running. The new code runs after the next start."""
    if _CFG["busy"]():
        _set(state="available", **base)
        return
    _set(state="downloading", mode="code", **base)
    r = U.install(auto=True)
    if r.get("ok") and r.get("action") == "restart":
        _set(state="ready", mode="code", **base)
    elif r.get("need_installer"):
        _set(state="available", **base)
        raise UpdateError("This release needs the full installer; it will be downloaded when it is checked.")
    else:
        raise UpdateError(r.get("error") or "The code update failed.")


def tick(force: bool = True) -> dict:
    """One check, and the download when the setting allows it."""
    if os.environ.get("MESHPOOL_NO_UPDATE"):
        return status()
    _set(state="checking") if _ST.get("state") in ("idle", "uptodate", "error") else None
    v = U.check(force=force)
    if not v.get("ok"):
        _set(state="error", error=v.get("error", ""))
        return status()
    if not v["newer"]:
        if _ST.get("state") != "ready":
            _set(state="uptodate", error="", tag="", latest=v["latest"])
        return status()
    if _ST.get("state") == "ready" and _ST.get("tag") == v["tag"]:
        return status()
    if v["kind"] == "dev":
        _set(state="available", tag=v["tag"], latest=v["latest"], url=v.get("url", ""))
        return status()
    pipeline(v, download=settings()["auto_download"])
    return status()


def kick(manual: bool = False) -> None:
    """Run tick() in the background (the page asked for a check, or for the download)."""
    def go():
        try:
            if manual:
                v = U.check(force=True)
                if v.get("ok") and v["newer"] and v["kind"] in APP_KINDS:
                    pipeline(v, download=True)
                    return
            tick()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=go, daemon=True).start()


def manual_install(v: dict) -> dict | None:
    """updater.install() for the Mac and Windows apps: start the background download (it may already be running or
    done) and tell the page. Returns None for a Windows release that changed only code, which installs in place."""
    k = v["kind"]
    st = _ST.get("state")
    if st == "ready" and _ST.get("tag") == v["tag"]:
        return {"ok": True, "action": "ready", "message": f"Meshpool {v['latest']} is ready. Restart to update."}
    if k == "win-app" and (U.APP.parent / "bin" / "meshpool.cmd").exists():
        try:
            sums = U._sums(v)
            manifest = read_manifest(v, sums) if sums else None
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Could not read the release's checksums ({type(e).__name__}). Try again."}
        if sums and not runtime_changed(manifest):
            return None
    if st in ("downloading", "verifying", "staging"):
        return {"ok": True, "action": "downloading", "message": "The update is downloading in the background."}
    threading.Thread(target=pipeline, args=(v, True), daemon=True).start()
    return {"ok": True, "action": "downloading",
            "message": f"Downloading Meshpool {v['latest']} in the background. Meshpool will say when it is ready."}


# ------------------------------------------------------------------ apply

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
    result.unlink(missing_ok=True)
    if st["mode"] == "mac-swap":
        staged, bundle = Path(st["staged"]), Path(st["bundle"])
        if not staged.is_dir() or not bundle.is_dir():
            return {"ok": False, "error": "The downloaded update is no longer there. It will be downloaded again."}
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
        if not inst.is_file():
            return {"ok": False, "error": "The downloaded update is no longer there. It will be downloaded again."}
        exe = U.APP.parent / "Meshpool.exe"
        script = d / "apply-win.ps1"
        script.write_text(win_helper_script(os.getpid(), str(inst), st["sha256"], str(U.APP.parent), str(exe),
                                            str(log), str(result)), encoding="utf-8-sig")
        _launch_detached(win_helper_cmd(str(script)))
    else:
        return {"ok": False, "error": "Nothing to install."}
    _set(helper=True)
    return {"ok": True, "mode": st["mode"]}


def begin_apply() -> dict:
    """The page's "Restart to update": start the helper. For an in-place code update the page restarts the app as
    before. After an ok result the caller closes the app (finish_quit)."""
    with _LOCK:
        st = _ready()
        if not st:
            return {"ok": False, "error": "No update is ready yet."}
        if st.get("mode") == "code":
            return {"ok": True, "mode": "code"}
        if st.get("helper"):
            return {"ok": True, "mode": st["mode"]}
        return launch_helper(st)


def finish_quit(on_quit=()) -> None:
    for f in on_quit:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.5)
    os._exit(0)


def on_quit() -> None:
    """Called when the app quits for any reason: a downloaded update installs now (the setting can turn this off)."""
    st = _ready()
    if st and st.get("mode") != "code" and not st.get("helper") and settings()["install_on_quit"]:
        try:
            launch_helper(st)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ background

def _load_ready() -> None:
    p = udir() / "ready.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        files = [st.get("file")] + ([st["staged"]] if st.get("staged") else [])
        if st.get("state") == "ready" and U._key(st["tag"]) > U._key(MH.__version__) and all(f and Path(f).exists() for f in files):
            _set(**st)
            return
    except (OSError, ValueError, KeyError):
        pass
    p.unlink(missing_ok=True)


def start_background(busy=None, first_delay: float = 15.0) -> None:
    """Check shortly after start and every 6 hours (after a failure, every 15 minutes). Not for a git checkout."""
    if _CFG["started"] or U.kind() == "dev" or os.environ.get("MESHPOOL_NO_UPDATE"):
        return
    _CFG["started"] = True
    if busy:
        _CFG["busy"] = busy
    _load_ready()

    def loop():
        time.sleep(first_delay)
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001
                _set(state="error", error="The update check failed.")
            time.sleep((RETRY if _ST.get("state") == "error" else INTERVAL) + random.randint(0, 300))
    threading.Thread(target=loop, daemon=True, name="meshpool-updates").start()
