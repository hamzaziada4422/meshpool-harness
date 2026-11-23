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

import base64
import contextlib
import hashlib
import json
import os
import re
import time
import shutil
import threading
import uuid
from pathlib import Path

from meshpool_harness import fsio

JOURNAL_MAX_BYTES = 2 * 1024 * 1024
TRANSCRIPT_MAX_LINES = 6000
TRANSCRIPT_KEEP_LINES = 3000

_GENS: dict[str, tuple] = {}                     # chat folder -> (stat of session.json, its generation)
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


def _sj_stat(d: Path):
    try:
        st = (d / "session.json").stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def _gen_of(d: Path) -> str | None:
    """The generation of the snapshot on disk now. Cached against the file's stat, so a snapshot written by another
    process (the CLI, a scheduled run) is noticed and later journal lines never carry a stale generation."""
    key, st = str(d), _sj_stat(d)
    with _GENS_GUARD:
        hit = _GENS.get(key)
    if hit and hit[0] == st:
        return hit[1]
    try:
        g = (fsio.read_json(d / "session.json", None) or {}).get("_jgen")
    except (fsio.CorruptFile, AttributeError):
        g = None
    with _GENS_GUARD:
        _GENS[key] = (st, g)
    return g


# ---- images out of line (OSS-05) ------------------------------------------------------------------------------------
BLOB_PREFIX = "meshpool-blob:"
BLOB_GC_AGE = 600.0
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_MIME = {v: k for k, v in _EXT.items()}
_DATA_RE = re.compile(r"^data:(image/[a-z+.-]+);base64,(.*)$", re.S)
_REF_RE = re.compile(r"meshpool-blob:([0-9a-f]{64}\.[a-z]{3,4})")
_NAME_RE = re.compile(r"^[0-9a-f]{64}\.[a-z]{3,4}$")
HEAD = "session.head.json"


def _store_blob(d: Path, url: str):
    """The ref for a data URL (the image written once, content-addressed), or None to leave it inline."""
    m = _DATA_RE.match(url)
    ext = _EXT.get(m.group(1)) if m else None
    if not ext:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return None
    if base64.b64encode(raw).decode("ascii") != m.group(2):     # only what round-trips byte for byte
        return None
    name = hashlib.sha256(raw).hexdigest() + "." + ext
    p = d / "blobs" / name
    if p.exists():
        with contextlib.suppress(OSError):
            os.utime(p, None)                                    # still in use: keeps it out of the GC window
    else:
        fsio.atomic_write_bytes(p, raw, fsync=False)
    return BLOB_PREFIX + name


def externalize(d: Path, messages: list) -> list:
    """``messages`` with inline image data URLs replaced by blob refs; the input is never mutated."""
    out = None
    for i, m in enumerate(messages):
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        parts = None
        for k, part in enumerate(c):
            iu = part.get("image_url") if isinstance(part, dict) and part.get("type") == "image_url" else None
            url = iu.get("url") if isinstance(iu, dict) else None
            if isinstance(url, str) and url.startswith("data:"):
                ref = _store_blob(d, url)
                if ref:
                    if parts is None:
                        parts = list(c)
                    parts[k] = {"type": "image_url", "image_url": {"url": ref}}
        if parts is not None:
            if out is None:
                out = list(messages)
            out[i] = dict(m, content=parts)
    return messages if out is None else out


def resolve_refs(d, messages: list) -> list:
    """The inverse of ``externalize``; a missing blob becomes a text part. Returns a new list when anything changed."""
    d = Path(d)
    out = None
    for i, m in enumerate(messages):
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        parts = None
        for k, part in enumerate(c):
            iu = part.get("image_url") if isinstance(part, dict) and part.get("type") == "image_url" else None
            url = iu.get("url") if isinstance(iu, dict) else None
            if isinstance(url, str) and url.startswith(BLOB_PREFIX):
                name = url[len(BLOB_PREFIX):]
                new = {"type": "text", "text": "[image no longer available]"}
                if _NAME_RE.match(name):
                    try:
                        raw = (d / "blobs" / name).read_bytes()
                        new = {"type": "image_url", "image_url": {
                            "url": "data:%s;base64,%s" % (_MIME.get(name.rsplit(".", 1)[1], "image/png"),
                                                          base64.b64encode(raw).decode("ascii"))}}
                    except OSError:
                        pass
                if parts is None:
                    parts = list(c)
                parts[k] = new
        if parts is not None:
            if out is None:
                out = list(messages)
            out[i] = dict(m, content=parts)
    return messages if out is None else out


def _gc_blobs(d: Path, snapshot_json: str) -> None:
    bd = d / "blobs"
    if not bd.is_dir():
        return
    used = set(_REF_RE.findall(snapshot_json))
    try:
        jr = (d / "session.journal").read_text("utf-8", "ignore")
        used |= set(_REF_RE.findall(jr))
    except OSError:
        pass
    now = time.time()
    for p in bd.iterdir():
        try:
            if p.name not in used and not p.name.startswith(".") and now - p.stat().st_mtime > BLOB_GC_AGE:
                p.unlink()
        except OSError:
            continue


def _head_of(data: dict, count: int) -> dict:
    h = {k: data.get(k) for k in ("id", "workdir", "title", "created", "updated", "model", "harness", "kind",
                                  "imported", "unfinished")}
    h["count"] = count
    return h


def read_head(d) -> dict | None:
    """The small listing record of a chat, or None when there is none (an older chat) or it is unreadable."""
    try:
        # written right after each snapshot: a head older than session.json was left by a build that does not write
        # heads (or by a failed write), so it is stale and the caller reads the chat itself
        if os.stat(Path(d) / HEAD).st_mtime_ns < os.stat(Path(d) / "session.json").st_mtime_ns:
            return None
        h = fsio.read_json(Path(d) / HEAD, None)
    except (fsio.CorruptFile, OSError):
        return None
    return h if isinstance(h, dict) and isinstance(h.get("id"), str) and h["id"] else None


def write_snapshot(d, data: dict) -> None:
    """Write the whole chat. The previous snapshot is kept as ``session.json.bak``; the journal it superseded goes."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    sj = d / "session.json"
    gen = uuid.uuid4().hex[:12]
    with fsio.file_lock(sj):
        msgs = data.get("messages")
        slim = dict(data, messages=externalize(d, msgs)) if isinstance(msgs, list) else data
        if sj.exists():
            _backup(sj, d / "session.json.bak")
        fsio.atomic_write_json(sj, dict(slim, _jgen=gen), indent=None, default=str)
        with contextlib.suppress(OSError):
            fsio.atomic_write_json(d / HEAD, _head_of(data, len(msgs) if isinstance(msgs, list) else 0),
                                   indent=None, fsync=False, default=str)
        with _GENS_GUARD:
            _GENS[str(d)] = (_sj_stat(d), gen)
        with contextlib.suppress(OSError):
            os.unlink(d / "session.journal")
        with contextlib.suppress(Exception):
            if (d / "blobs").is_dir():
                _gc_blobs(d, json.dumps(slim.get("messages"), ensure_ascii=False, default=str))


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
        f.flush()                    # no fsync per append: the full snapshot at turn end is the durable point


def append_journal(d, start: int, messages: list, meta: dict) -> None:
    d = Path(d)
    with fsio.file_lock(d / "session.json"):
        rec = {"from": start, "messages": externalize(d, messages), "meta": meta}
        gen = _gen_of(d)
        if gen:
            rec["gen"] = gen
        _append_line(d / "session.journal", _dumps(rec) + "\n")
        h = read_head(d)
        if h is not None:                              # keep the listing record current without reading the chat
            h.update({k: meta[k] for k in ("title", "updated", "model", "unfinished") if k in meta})
            h["count"] = start + len(messages)
            with contextlib.suppress(OSError):
                fsio.atomic_write_json(d / HEAD, h, indent=None, fsync=False, default=str)


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


def read(d, resolve_blobs: bool = False) -> dict | None:
    """The chat as last saved, or ``None`` when nothing usable exists. A damaged snapshot is quarantined and the
    backup is used; the journal is replayed on top. Images are blob refs unless ``resolve_blobs`` (what goes to a
    model); listings and search keep the refs."""
    d = Path(d)
    data, primary = _read_snapshot(d)
    if data is None:
        return None
    data = _replay(d, data)
    data.pop("_jgen", None)
    if resolve_blobs and isinstance(data.get("messages"), list):
        data["messages"] = resolve_refs(d, data["messages"])
    return data


def read_path(p, resolve_blobs: bool = False) -> dict | None:
    return read(Path(p).parent, resolve_blobs)


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
