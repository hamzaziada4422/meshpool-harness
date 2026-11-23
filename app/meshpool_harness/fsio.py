"""Crash-safe file writes and cross-process locks, shared by every store that keeps state on disk.

Why this exists: chat sessions, checkpoints, schedules, memory, project files, corpora and caches were rewritten in
place or through one shared ``.tmp`` name. A full disk, a crash, a kill or two writers at once then left a truncated or
mixed file, and the chat, its Undo history or every scheduled task was lost. Everything that persists state writes
through here:

- ``atomic_write_bytes`` / ``atomic_write_text`` / ``atomic_write_json``: write a unique temporary file next to the
  target, flush and fsync it, then rename it over the target. Readers see the old file or the new one, never half of
  one. On failure the target is untouched and the temporary file is removed.
- ``file_lock(path)``: an exclusive lock that holds across processes (the app, the CLI, a scheduled run started by the
  OS, a second app instance), not only across threads. Use it around read-modify-write cycles.
- ``update_json(path, fn, default)``: the read-modify-write cycle done right (lock, read, change, atomic write).
- ``read_json(path, default)``: tolerant read that tells a missing file from a damaged one (``CorruptFile``), so a
  caller never mistakes an unreadable file for an empty one and then overwrites it.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class CorruptFile(Exception):
    """The file exists but cannot be read or parsed. Callers must not treat it as empty and overwrite it."""

    def __init__(self, path, cause: BaseException):
        super().__init__(f"{path}: {cause}")
        self.path = Path(path)
        self.cause = cause


def _replace(src: str, dst: str) -> None:
    # Windows: a reader, the search indexer or antivirus can hold the target for a moment.
    attempts = 40 if os.name == "nt" else 1
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05)


def _fsync_dir(d: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path, data: bytes, *, fsync: bool = True) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        _replace(tmp, str(p))
        if fsync:
            _fsync_dir(p.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(path, text: str, *, encoding: str = "utf-8", fsync: bool = True) -> None:
    atomic_write_bytes(path, text.encode(encoding, "surrogatepass" if encoding.startswith("utf") else "strict"),
                       fsync=fsync)


def atomic_write_json(path, obj: Any, *, indent: int | None = 1, fsync: bool = True, **dump_kw) -> None:
    dump_kw.setdefault("ensure_ascii", False)
    atomic_write_text(path, json.dumps(obj, indent=indent, **dump_kw), fsync=fsync)


def read_json(path, default: Any = None) -> Any:
    """Missing file -> ``default``. Unreadable or damaged file -> ``CorruptFile`` (never silently ``default``)."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return default
    except OSError as e:          # sharing violation, permission: not the same as "no data"
        raise CorruptFile(p, e) from e
    if not raw.strip():
        raise CorruptFile(p, ValueError("empty file"))
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CorruptFile(p, e) from e


def quarantine(path) -> Path | None:
    """Move a damaged file aside (``name.corrupt-<time>``) so it can be inspected and is never overwritten."""
    p = Path(path)
    if not p.exists():
        return None
    n = 0
    while True:                         # time-ordered, so the newest copy sorts last; never overwrites an older one
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}{'-%d' % n if n else ''}"
        dst = p.with_name(f"{p.name}.corrupt-{stamp}")
        if not dst.exists():
            break
        n += 1
    with contextlib.suppress(OSError):
        _replace(str(p), str(dst))
        return dst
    return None


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lk = _THREAD_LOCKS.get(key)
        if lk is None:
            lk = _THREAD_LOCKS[key] = threading.RLock()
        return lk


@contextlib.contextmanager
def file_lock(path, *, timeout: float = 30.0, poll: float = 0.02) -> Iterator[None]:
    """Exclusive lock on ``<path>.lock`` across threads and processes. Raises ``TimeoutError`` after ``timeout``."""
    p = Path(path)
    lock_path = p.with_name(p.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    tl = _thread_lock(str(lock_path.resolve()))
    if not tl.acquire(timeout=timeout if timeout >= 0 else -1):
        raise TimeoutError(f"lock busy: {lock_path}")
    try:
        f = open(lock_path, "a+b")
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"lock busy: {lock_path}")
                    time.sleep(poll)
            try:
                yield
            finally:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            f.close()
    finally:
        tl.release()


def update_json(path, fn: Callable[[Any], Any], default: Any = None, *, timeout: float = 30.0) -> Any:
    """Locked read-modify-write. ``fn`` gets the current value (or a copy of ``default``) and returns the new value
    (or mutates in place and returns None). A damaged file raises ``CorruptFile`` and is left untouched."""
    with file_lock(path, timeout=timeout):
        cur = read_json(path, default=json.loads(json.dumps(default)) if default is not None else None)
        new = fn(cur)
        if new is None:
            new = cur
        atomic_write_json(path, new)
        return new
