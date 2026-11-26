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
import stat
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


_UMASK: int | None = None
_UMASK_LOCK = threading.Lock()


def _read_umask() -> int:
    """The process umask, read without changing it where the OS allows (Linux: /proc). Elsewhere it can only be read
    by setting it and putting it back; that happens once, under a lock."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    m = os.umask(0o022)
    os.umask(m)
    return m


def default_mode() -> int:
    """Permission bits a plain ``open(..., "w")`` gives a new file: 0o666 minus the umask (read once per process)."""
    global _UMASK
    if _UMASK is None:
        with _UMASK_LOCK:
            if _UMASK is None:
                _UMASK = _read_umask()
    return 0o666 & ~_UMASK


def _write_in_place(p: Path, data: bytes, *, fsync: bool, mode: int | None, private: bool) -> None:
    """A file with more than one hard link (CLAUDE.md and AGENTS.md as one file): a rename would give this name a new
    file and leave the other names on the old text, so the shared file is rewritten where it is."""
    if private:
        with contextlib.suppress(OSError):
            os.chmod(p, 0o600)
    with open(p, "r+b") as f:
        f.write(data)
        f.truncate()
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    if mode is not None and not private:
        with contextlib.suppress(OSError):
            os.chmod(p, mode)


def atomic_write_bytes(path, data: bytes, *, fsync: bool = True, mode: int | None = None,
                       private: bool = False, keep_links: bool = False) -> None:
    """Write ``data`` to ``path``; a reader sees the old file or the new one, never half of one.

    Permission bits: ``mode`` when given. ``private=True`` keeps the file owner-only (0600) from its first byte (keys,
    tokens, chats). Otherwise an existing file keeps its bits and a new file gets what ``open()`` would give it (0o666
    minus the umask), not the temporary file's 0600.

    A symlink is followed and its target rewritten (the link stays a link). ``keep_links=True`` (the user's own files:
    the agent's write_file and edit, Undo) rewrites a file with several hard links in place, so every name keeps
    naming the same file (CLAUDE.md and AGENTS.md as one). It is off by default: the stores keep their backup as a
    hard link to the previous file, which a write in place would overwrite too."""
    p = Path(path)
    if p.is_symlink():
        p = Path(os.path.realpath(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = os.stat(p)
    except OSError:
        st = None
    if keep_links and st is not None and st.st_nlink > 1 and stat.S_ISREG(st.st_mode):
        _write_in_place(p, data, fsync=fsync, mode=mode, private=private)
        return
    if mode is None and not private:
        mode = (st.st_mode & 0o7777) if st is not None else default_mode()
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))   # created 0600
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        if private:
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
        elif mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(tmp, mode)                 # OSS-15: keep the permission bits (an executable stays executable)
        _replace(tmp, str(p))
        if fsync:
            _fsync_dir(p.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(path, text: str, *, encoding: str = "utf-8", fsync: bool = True, mode: int | None = None,
                      private: bool = False) -> None:
    atomic_write_bytes(path, text.encode(encoding, "surrogatepass" if encoding.startswith("utf") else "strict"),
                       fsync=fsync, mode=mode, private=private)


def atomic_write_json(path, obj: Any, *, indent: int | None = 1, fsync: bool = True, private: bool = False,
                      **dump_kw) -> None:
    dump_kw.setdefault("ensure_ascii", False)
    atomic_write_text(path, json.dumps(obj, indent=indent, **dump_kw), fsync=fsync, private=private)


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


def update_json(path, fn: Callable[[Any], Any], default: Any = None, *, timeout: float = 30.0,
                private: bool = False) -> Any:
    """Locked read-modify-write. ``fn`` gets the current value (or a copy of ``default``) and returns the new value
    (or mutates in place and returns None). A damaged file raises ``CorruptFile`` and is left untouched."""
    with file_lock(path, timeout=timeout):
        cur = read_json(path, default=json.loads(json.dumps(default)) if default is not None else None)
        new = fn(cur)
        if new is None:
            new = cur
        atomic_write_json(path, new, private=private)
        return new


# ---------- small files read again and again ---------------------------------------------------------------------------
# Hooks, the trust list, project records and model tables were read and parsed on every tool call, chat poll or model
# request. parse_cached keeps the parsed value while the file is unchanged: the same modification time, size, inode
# and change time. A file changed in the last RACY_S seconds is read again each time, because a second write in the
# same clock tick could leave all four the same (FAT keeps times in 2 s steps).
RACY_S = 2.0
_PARSED: dict = {}
_PARSED_LOCK = threading.Lock()
READS = [0]                          # files parse_cached really read (tests count them)


def file_stamp(path) -> tuple | None:
    """(mtime_ns, size, inode, ctime_ns) of a file, or None when it cannot be seen."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)


def parse_cached(path, parse: Callable[[bytes], Any], kind: str, missing: Any = None) -> Any:
    """``parse(raw bytes)`` of the file, kept while the file is unchanged; ``missing`` when it does not exist or
    cannot be read. ``kind`` names the parser (one file can be parsed two ways). ``parse`` handles its own errors and
    returns a default. The value is shared between callers: they must not change it."""
    key = (os.path.abspath(str(path)), kind)
    stamp = file_stamp(path)
    if stamp is None:
        with _PARSED_LOCK:
            _PARSED.pop(key, None)
        return missing
    with _PARSED_LOCK:
        hit = _PARSED.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    t0 = time.time()
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return missing
    READS[0] += 1
    val = parse(raw)
    old = (t0 - RACY_S) * 1e9
    if file_stamp(path) == stamp and stamp[0] < old and stamp[3] < old:
        with _PARSED_LOCK:
            if len(_PARSED) > 1024:
                _PARSED.clear()
            _PARSED[key] = (stamp, val)
    return val
