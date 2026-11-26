"""Keeps the computer from going to sleep on its own while a task runs (the screen may still turn off).

hold() when a turn starts, release() when it ends; counted, so several chats at once keep it awake until the last
ends. macOS: caffeinate -i. Windows: SetThreadExecutionState from one keeper thread. Linux: systemd-inhibit when
there is one. MH_KEEP_AWAKE=0 turns it off.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

_lock = threading.Lock()
_count = 0
_proc: subprocess.Popen | None = None
_win: threading.Event | None = None


def _command() -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        return ["caffeinate", "-i", "-w", str(os.getpid())]          # also ends if meshpool goes away
    if sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
        return ["systemd-inhibit", "--what=idle:sleep", "--who=meshpool", "--why=A task is running",
                "--mode=block", "sleep", "infinity"]
    return None


def _windows_keeper(stop: threading.Event) -> None:
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    k = ctypes.windll.kernel32                                         # type: ignore[attr-defined]
    k.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    stop.wait()
    k.SetThreadExecutionState(ES_CONTINUOUS)


def _start() -> None:
    global _proc, _win
    if sys.platform == "win32":
        _win = threading.Event()
        threading.Thread(target=_windows_keeper, args=(_win,), daemon=True).start()
        return
    cmd = _command()
    if cmd:
        try:
            _proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        except OSError:
            _proc = None


def _stop() -> None:
    global _proc, _win
    if _win is not None:
        _win.set()
        _win = None
    if _proc is not None:
        try:
            _proc.terminate()
            _proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                _proc.kill()
            except Exception:  # noqa: BLE001
                pass
        _proc = None


def hold() -> None:
    global _count
    if os.environ.get("MH_KEEP_AWAKE") == "0":
        return
    with _lock:
        _count += 1
        if _count == 1:
            _start()


def release() -> None:
    global _count
    with _lock:
        if _count <= 0:
            return
        _count -= 1
        if _count == 0:
            _stop()


def held() -> bool:
    return _count > 0
