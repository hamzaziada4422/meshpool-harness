"""Put this copy back the way it was if an in-place update was killed half way (M11-06).

Standard library only, and it lives beside the app folders rather than inside them, because the thing it repairs is
exactly the case where meshpool_harness/ is missing: an update swaps each code folder by renaming it away and
renaming the new one in, so a power cut, a kill or a crash between the two renames leaves no package at all, and
nothing inside the package can run to repair it. app/sitecustomize.py calls recover() at every interpreter start
(the app folder is on PYTHONPATH for the command, the launchers and restart()), and updater.py calls it before it
prepares or applies an update.

The updater writes <app>/.update-journal.json before its first rename and removes it after its last step. While the
journal exists the copy is in an unknown state; recover() returns every folder to its pre-update state.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

JOURNAL = ".update-journal.json"
RECOVERED = ".update-recovered.json"
FRESH = 600          # seconds a journal whose writer is still running is trusted to be in progress


def _alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():               # this process is not mid-swap while it runs recovery
        return False
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32                # type: ignore[attr-defined]
            h = k.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259     # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:  # noqa: BLE001
        return False


def boot_time() -> float | None:
    """Unix time the system last booted, or None when it cannot be found. A journal written before it belongs to a
    process that cannot be running now, even if its pid has been handed to something else after a reboot."""
    try:
        if os.name == "nt":
            import ctypes
            return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0   # type: ignore[attr-defined]
        if os.path.exists("/proc/stat"):
            with open("/proc/stat", "r", encoding="ascii", errors="replace") as f:
                for line in f:
                    if line.startswith("btime "):
                        return float(line.split()[1])
        if os.uname().sysname == "Darwin":
            out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=2).stdout
            import re
            m = re.search(r"sec\s*=\s*(\d+)", out)
            if m:
                return float(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def _rm(p: str) -> None:
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            os.unlink(p)
        except OSError:
            pass


def _restore(pair: dict) -> None:
    dest, backup = pair.get("dest") or "", pair.get("backup") or ""
    if not dest:
        return
    if pair.get("existed"):
        if backup and os.path.lexists(backup):
            if os.path.lexists(dest):
                aside = f"{dest}.recovering-{os.getpid()}"
                _rm(aside)
                os.rename(dest, aside)
                _rm(aside)
            os.rename(backup, dest)
        # no backup: the old folder was never moved, so it is still in place
    elif os.path.lexists(dest):
        _rm(dest)                                     # the update created it; the old copy had none


def _write(path: str, data: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def recover(app_dir) -> str | None:
    """Undo an interrupted update in app_dir. Returns a one-line message when it did, else None. Never raises."""
    try:
        app = os.fspath(app_dir)
        jp = os.path.join(app, JOURNAL)
        if not os.path.exists(jp):                    # the common case: one stat
            return None
        with open(jp, "r", encoding="utf-8") as f:
            j = json.load(f)
        if not isinstance(j, dict):
            j = {}
        pid = j.get("pid")
        t = float(j.get("time") or 0)
        if isinstance(pid, int) and _alive(pid) and time.time() - t < FRESH:
            boot = boot_time()
            if boot is None or t >= boot:             # written before the last boot: the pid is a stranger's now
                return None                           # another process is swapping right now
        for pair in reversed(j.get("pairs") or []):
            try:
                _restore(pair)
            except Exception:  # noqa: BLE001
                pass
        for name in os.listdir(app):
            if name.startswith(".update-") and os.path.isdir(os.path.join(app, name)):
                shutil.rmtree(os.path.join(app, name), ignore_errors=True)
        tag = j.get("tag") or ""
        msg = (f"An update to Meshpool {tag} was interrupted, so the previous version was put back."
               if tag else "An update was interrupted, so the previous version was put back.")
        try:
            _write(os.path.join(app, RECOVERED), {"message": msg, "tag": tag, "time": time.time()})
        except Exception:  # noqa: BLE001
            pass
        os.unlink(jp)                                 # last: until now a second start would retry
        return msg
    except Exception:  # noqa: BLE001
        return None
