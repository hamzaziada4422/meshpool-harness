"""Crash-safe chat storage: a snapshot plus an append-only journal, and an append-only transcript.

Why: ``session.json`` used to be rewritten whole (every few seconds during a turn) through one shared ``.tmp`` name, or
in place by the harness chats, so a full disk, a kill or two writers left a truncated file and the chat vanished.

Layout of one chat folder:

- ``session.json``       the last full snapshot (written atomically; there is never a moment without a readable one)
- ``session.json.bak``   the snapshot before that (hard link), used when ``session.json`` is damaged
- ``session.journal``    JSON lines appended after the snapshot: ``{"from": n, "messages": [...], "meta": {...}}``
- ``transcript.jsonl``   what the thread shows, one event per line, append-only
- ``transcript.state.json``  ``{"running": bool}``
- ``transcript.json``    legacy single-file transcript, still read

All writes go through ``fsio``.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from meshpool_harness import fsio

JOURNAL_MAX_BYTES = 2 * 1024 * 1024
TRANSCRIPT_MAX_LINES = 6000
TRANSCRIPT_KEEP_LINES = 3000

_GENS: dict[str, str] = {}                       # chat folder -> generation of its current snapshot
_GENS_GUARD = threading.Lock()


def _dumps(obj, **kw) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, **kw)


def _looks_complete(p: Path) -> bool:
    """A cheap check that a file ends like a finished JSON object, so a damaged file never replaces the backup."""
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-min(f.tell(), 8), os.SEEK_END)
            return f.read().rstrip().endswith(b"}")
    except OSError:
        return False


def _backup(src: Path, bak: Path) -> None:
    if not _looks_complete(src):
        return
    tmp = bak.with_name(f".{bak.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        os.link(src, tmp)
    except OSError:
        try:
            shutil.copyfile(src, tmp)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            return
    try:
        fsio._replace(str(tmp), str(bak))
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _gen_of(d: Path) -> str | None:
    key = str(d)
    with _GENS_GUARD:
        if key in _GENS:
            return _GENS[key]
    try:
        g = (fsio.read_json(d / "session.json", None) or {}).get("_jgen")
    except (fsio.CorruptFile, AttributeError):
        g = None
    with _GENS_GUARD:
        _GENS[key] = g
    return g


def write_snapshot(d, data: dict) -> None:
    """Write the whole chat. The previous snapshot is kept as ``session.json.bak``; the journal it superseded goes."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    sj = d / "session.json"
    gen = uuid.uuid4().hex[:12]
    with fsio.file_lock(sj):
        if sj.exists():
            _backup(sj, d / "session.json.bak")
        fsio.atomic_write_json(sj, dict(data, _jgen=gen), indent=None, default=str)
        with _GENS_GUARD:
            _GENS[str(d)] = gen
        with contextlib.suppress(OSError):
            os.unlink(d / "session.journal")


def journal_size(d) -> int:
    try:
        return os.path.getsize(Path(d) / "session.journal")
    except OSError:
        return 0


def _append_line(p: Path, line: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = line.encode("utf-8", "surrogatepass")
    with open(p, "ab") as f:
        if f.tell() > 0:
            with open(p, "rb") as r:                 # a crash left a torn last line: start a new one
                r.seek(-1, os.SEEK_END)
                if r.read(1) != b"\n":
                    raw = b"\n" + raw
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def append_journal(d, start: int, messages: list, meta: dict) -> None:
    d = Path(d)
    with fsio.file_lock(d / "session.json"):
        rec = {"from": start, "messages": messages, "meta": meta}
        gen = _gen_of(d)
        if gen:
            rec["gen"] = gen
        _append_line(d / "session.journal", _dumps(rec) + "\n")


def _read_snapshot(d: Path):
    sj, bak = d / "session.json", d / "session.json.bak"
    data = None
    try:
        data = fsio.read_json(sj, None)
        if data is not None and not isinstance(data, dict):
            raise fsio.CorruptFile(sj, ValueError("not an object"))
    except fsio.CorruptFile:
        with contextlib.suppress(Exception), fsio.file_lock(sj, timeout=5):
            fsio.quarantine(sj)
        data = None
    if data is not None:
        return data, True
    try:
        data = fsio.read_json(bak, None)
        if data is not None and not isinstance(data, dict):
            raise fsio.CorruptFile(bak, ValueError("not an object"))
    except fsio.CorruptFile:
        with contextlib.suppress(Exception), fsio.file_lock(sj, timeout=5):
            fsio.quarantine(bak)
        data = None
    return data, False


def _replay(d: Path, data: dict) -> dict:
    jp = d / "session.journal"
    try:
        lines = jp.read_bytes().split(b"\n")
    except OSError:
        return data
    gen = data.get("_jgen")
    msgs = data.get("messages")
    if not isinstance(msgs, list):
        return data
    for raw in lines:
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue                                  # a torn line (crash mid-append)
        if not isinstance(rec, dict) or rec.get("gen") != gen:
            continue                                  # belongs to another snapshot
        start, new = rec.get("from"), rec.get("messages")
        if not isinstance(start, int) or not isinstance(new, list) or not (0 <= start <= len(msgs)):
            continue
        msgs = msgs[:start] + new
        if isinstance(rec.get("meta"), dict):
            data.update(rec["meta"])
    data["messages"] = msgs
    return data


def read(d) -> dict | None:
    """The chat as last saved, or ``None`` when nothing usable exists. A damaged snapshot is quarantined and the
    backup is used; the journal is replayed on top."""
    d = Path(d)
    data, primary = _read_snapshot(d)
    if data is None:
        return None
    data = _replay(d, data)
    data.pop("_jgen", None)
    return data


def read_path(p) -> dict | None:
    return read(Path(p).parent)


def stamp(d) -> tuple:
    """Cache key for a chat folder's saved state (snapshot and journal)."""
    d = Path(d)
    out = []
    for n in ("session.json", "session.journal"):
        try:
            st = (d / n).stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


class TranscriptLog:
    """What a thread shows, saved as one JSON event per line so a save appends instead of rewriting everything."""

    def __init__(self, d):
        self.dir = Path(d)
        self.path = self.dir / "transcript.jsonl"
        self.state = self.dir / "transcript.state.json"
        self.legacy = self.dir / "transcript.json"
        self._lines: int | None = None
        self._running: bool | None = None
        self._guard = threading.RLock()

    # -- writing
    @staticmethod
    def _enc(events) -> bytes:
        return "".join(_dumps(e) + "\n" for e in events).encode("utf-8", "surrogatepass")

    def _count(self) -> int:
        try:
            return self.path.read_bytes().count(b"\n")
        except OSError:
            return 0

    def append(self, events: list) -> None:
        if not events:
            return
        with self._guard, fsio.file_lock(self.path):
            if not self.path.exists():                 # first append after the old format: carry the old events over
                old = self._read_legacy()
                if old and old.get("events"):
                    fsio.atomic_write_bytes(self.path, self._enc(old["events"]))
                    self._lines = None
            if self._lines is None:
                self._lines = self._count()
            _append_line(self.path, self._enc(events).decode("utf-8", "surrogatepass"))
            self._lines += len(events)
            if self._lines > TRANSCRIPT_MAX_LINES:
                evs = self._read_events()[-TRANSCRIPT_KEEP_LINES:]
                fsio.atomic_write_bytes(self.path, self._enc(evs))
                self._lines = len(evs)

    def rewrite(self, events: list, running: bool) -> None:
        with self._guard, fsio.file_lock(self.path):
            fsio.atomic_write_bytes(self.path, self._enc(events))
            self._lines = len(events)
            self._write_state(running)

    def set_running(self, running: bool) -> None:
        with self._guard, fsio.file_lock(self.path):
            if self._running is None:
                try:
                    self._running = bool((fsio.read_json(self.state, {}) or {}).get("running"))
                except (fsio.CorruptFile, AttributeError):
                    self._running = None
            if self._running is not None and self._running == bool(running):
                return
            self._write_state(running)

    def _write_state(self, running: bool) -> None:
        running = bool(running)
        fsio.atomic_write_json(self.state, {"running": running}, indent=None)
        self._running = running

    # -- reading
    def _read_events(self) -> list:
        out = []
        try:
            raw = self.path.read_bytes()
        except OSError:
            return out
        for ln in raw.split(b"\n"):
            if not ln.strip():
                continue
            try:
                e = json.loads(ln.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue                               # a torn line
            if isinstance(e, dict):
                out.append(e)
        return out

    def _read_legacy(self):
        try:
            d = fsio.read_json(self.legacy, None)
        except fsio.CorruptFile:
            return None
        return d if isinstance(d, dict) else None

    def read(self) -> dict | None:
        if self.path.exists():
            try:
                running = bool((fsio.read_json(self.state, {}) or {}).get("running"))
            except (fsio.CorruptFile, AttributeError):
                running = False
            return {"events": self._read_events(), "running": running}
        old = self._read_legacy()
        if old is None:
            return None
        return {"events": old.get("events") or [], "running": bool(old.get("running"))}
