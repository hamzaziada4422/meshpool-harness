"""One meshpool UI server per MESHPOOL_HOME. Two of them on the same home share the same chats and write over each
other, so the second one stops and says where the first one is.

The lock is the file ui.lock in the home folder: {"pid": ..., "port": ...}. It is made with O_EXCL, so two servers that
start at the same moment cannot both win. A lock whose pid is gone (a crash) is taken over."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.harnesses import _pid_alive


def lock_path() -> Path:
    return C.home() / "ui.lock"


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def claim() -> dict | None:
    """Take the lock for this process. None when it is ours; else the live server's lock ({pid, port}) that holds it."""
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    for _ in range(5):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            cur = _read(p)
            pid = cur.get("pid")
            if isinstance(pid, int) and pid != me and _pid_alive(pid):
                if not cur.get("port"):                      # it is still starting: give it a moment to bind
                    for _ in range(25):
                        time.sleep(0.2)
                        cur = _read(p)
                        if cur.get("port") or not _pid_alive(pid):
                            break
                    if not _pid_alive(pid):
                        continue
                return cur
            try:                                              # a crash left it, or it is ours from before: take over
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                time.sleep(0.1)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": me, "port": 0}, f)
        return None
    return {"pid": 0, "port": 0}


def set_port(port: int) -> None:
    p = lock_path()
    try:
        tmp = p.with_name(p.name + f".{os.getpid()}")
        tmp.write_text(json.dumps({"pid": os.getpid(), "port": int(port)}), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def release() -> None:
    p = lock_path()
    if _read(p).get("pid") == os.getpid():
        try:
            p.unlink()
        except OSError:
            pass


def already_running_text(held: dict) -> str:
    port = held.get("port")
    where = f"http://127.0.0.1:{port}" if port else "another window"
    return f"meshpool is already running at {where}. Open that, or quit it first."
