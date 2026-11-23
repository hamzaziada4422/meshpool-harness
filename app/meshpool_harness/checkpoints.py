"""Per-turn checkpoints of a project folder, for any harness: what each turn changed, and Undo.

No Git needed. Content is kept in ONE deduplicated, compressed store per folder (shared by every chat in that folder):
``<runs>/checkpoints/<hash of folder>/objects``, plus a persisted stat cache (``stat.json``) so a restart does not
re-read unchanged files. Each chat keeps only a small ``turns.json`` listing, per turn, the paths that turn changed
with their value before (``pre``) and after (``post``). Values: ``"<sha>:<octal mode>"`` for a stored file, a ``"!..."``
marker for a path that exists but whose contents are not stored (ignored, over 2 MB, a link), ``None`` for absent.

Modes (``Checkpoints(root, store, mode=...)``)
- ``"scan"`` (default; outside agents such as Codex or Pi): ``begin_turn`` scans the folder, ``end_turn`` scans again
  and books every difference to the turn (minus writes Meshpool itself made, see ``note_foreign_write``).
- ``"journal"`` (Meshpool's own agent): ``begin_turn``/``end_turn`` never walk the folder. Changes are booked only
  inside tool calls. Wire-up for the agent loop::

      cp.begin_turn(title, message_index, event_index)
      with cp.tool(name, args, read_only=False):      # around EVERY tool call, whatever its name
          result = run_tool(...)
      cp.window_open() / cp.window_close()            # while a background process the chat started may still run
      cp.end_turn()

  ``write_file``, ``edit``, ``notebook_edit`` (``args["path"]``) and ``apply_patch`` (paths parsed from the patch)
  record the before-image of each target just before and the after-image just after. ``read_only=True`` records
  nothing. Any other tool (shell, MCP, delegate...) is an opaque window: an incremental scan before and after.
  Edits the user makes between tool calls are never booked. ``tool`` is a no-op when no turn is open.

Undo (``revert_to_before(turn)``) only touches paths the turns changed, and only while they still hold what the agent
left. Before it deletes or overwrites a file it copies it to ``<store>/undone/<time>/<path>`` (the newest 20 folders
are kept) and returns that folder as ``"backup"``. Paths changed while another chat worked in the same folder are
marked ``ambiguous`` and left alone (reported in ``kept`` with a note). The last ``MH_UNDO_TURNS`` (default 100) turns
stay undoable; older turns keep their file list but not their contents (``pruned``). Turn numbers only ever grow
(``seq``; pass ``seq_floor=`` from chat metadata). ``turns.json`` is written atomically with a ``.bak`` copy and a
damaged one is quarantined and recovered, never treated as empty.

Skipped: .git and other tool folders, dependency and build folders, anything the project's .gitignore lists (simple
patterns), files over 2 MB, and projects with more than 20,000 files. Ignored, too-big and unreadable files, links and
ignored folders are still listed with a "not stored" marker, so a file that only became visible during a turn is never
mistaken for one the agent created, and Undo never deletes or overwrites a path whose earlier (or current) contents it
did not store.
"""
from __future__ import annotations

import contextlib
import difflib
import fnmatch
import hashlib
import itertools
import json
import os
import re
import shutil
import stat
import threading
import time
import zlib
from pathlib import Path

from meshpool_harness import fsio

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".next", ".nuxt", "dist", "build", ".cache", ".turbo", ".gradle", "target", ".idea", ".DS_Store",
             "Pods", ".terraform", ".tox", "coverage"}
SKIP_FILES = {".DS_Store"}
MAX_FILE = 2_000_000
MAX_FILES = 20_000
WRITE_TOOLS = {"write_file", "edit", "notebook_edit"}
RACY_NS = 2_000_000_000          # a file modified this recently is re-hashed next time (same-size, same-mtime edits)
GC_EVERY = 600
GC_GRACE = 3600
BACKUPS_KEEP = 20
FOREIGN_KEEP = 5000
FOREIGN_AGE = 24 * 3600
AMBIGUOUS_NOTE = "another chat was working in this folder at the same time, so it was left as is"


class MissingObject(OSError):
    """A saved copy is missing or fails its checksum."""


def undo_turns() -> int:
    try:
        return max(1, int(os.environ.get("MH_UNDO_TURNS", 100)))
    except ValueError:
        return 100


def _ignore_rules(root: Path) -> list[tuple[str, bool]]:
    rules = []
    gi = root / ".gitignore"
    try:
        for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            rules.append((line.rstrip("/").lstrip("/"), line.endswith("/")))
    except OSError:
        pass
    return rules


def _ignored(rel: str, is_dir: bool, rules) -> bool:
    name = rel.rsplit("/", 1)[-1]
    for pat, dir_only in rules:
        if dir_only and not is_dir:
            continue
        if "/" in pat:
            if fnmatch.fnmatch(rel, pat) or rel.startswith(pat + "/"):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


# Manifest values: "<sha>:<octal mode>" for a stored file (old manifests: "<sha>"), or a "!..." marker for a path that
# exists but whose contents were not stored. Keys ending in "/" are folders that were not walked (ignored or a link):
# everything under them existed, contents unknown.
def _stored(v) -> bool:
    return isinstance(v, str) and not v.startswith("!")


def _sha(v) -> str | None:
    return v.partition(":")[0] if _stored(v) else None


def _mode(v) -> int | None:
    m = v.partition(":")[2] if _stored(v) else ""
    return int(m, 8) if m else None


def _same(x, y) -> bool:
    """Same recorded state. Unstored contents are never "the same" as anything."""
    if x is None or y is None:
        return x is None and y is None
    if not (_stored(x) and _stored(y)):
        return False
    mx, my = _mode(x), _mode(y)
    return _sha(x) == _sha(y) and (mx is None or my is None or mx == my)


def _unknown(path: str, manifest: dict) -> bool:
    """True if `path` existed in `manifest` without its contents stored (marker, or under an unwalked folder)."""
    v = manifest.get(path)
    if v is not None and not _stored(v):
        return True
    parts = path.split("/")
    return any("/".join(parts[:i]) + "/" in manifest for i in range(1, len(parts)))


def default_shared_dir(root) -> Path:
    from meshpool_harness import config
    key = hashlib.sha1(str(Path(root).resolve()).encode("utf-8", "replace")).hexdigest()[:16]
    return config.runs_dir() / "checkpoints" / key


def _big(st) -> str:
    return f"!big:{st.st_size}:{st.st_mtime_ns}"


def _status(a, b) -> str:
    return "added" if a is None else "deleted" if b is None else "modified"


def _val(manifest: dict, path: str):
    """Value of `path` in a scan manifest: its entry, "!unknown" if it sat under a folder that was not walked."""
    v = manifest.get(path)
    if v is not None:
        return v
    return "!unknown" if _unknown(path, manifest) else None


def _rel_in_root(root: Path, path) -> str | None:
    try:
        p = Path(path)
        full = p if p.is_absolute() else root / p
        real = Path(os.path.realpath(full.parent)) / full.name
        rel = real.relative_to(root).as_posix()
    except (ValueError, OSError, TypeError):
        return None
    return None if rel in ("", ".") else rel


# ---- foreign writes: things Meshpool did on the user's behalf (Undo, the in-app editor, attachments) -------------
_FW_LOCK = threading.Lock()
_FW_MEM: dict[str, list] = {}
_FW_FILE: dict[str, tuple] = {}


def _plain_sha(root: Path, rel: str):
    """Bare sha1 of the file now, None if it is gone, "" if it is not a hashable regular file."""
    p = root / rel
    try:
        st = p.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
        return ""
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def note_foreign_write(root, path, actor: str = "", shared=None) -> None:
    """Record that Meshpool itself wrote or deleted `path` (so a scan window that sees the change does not book it to
    an agent). Kept in memory and in ``<shared>/foreign.jsonl`` (other processes), last 24 h / 5000 lines."""
    try:
        r = Path(root).resolve()
        rel = _rel_in_root(r, path)
        if not rel:
            return
        e = {"path": rel, "sha": _plain_sha(r, rel), "time": time.time(), "actor": str(actor)}
        with _FW_LOCK:
            lst = _FW_MEM.setdefault(str(r), [])
            lst.append(e)
            cut = time.time() - FOREIGN_AGE
            lst[:] = [x for x in lst if x["time"] >= cut][-FOREIGN_KEEP:]
        sh = Path(shared) if shared else default_shared_dir(r)
        f = sh / "foreign.jsonl"
        with fsio.file_lock(sh / "foreign", timeout=5):
            sh.mkdir(parents=True, exist_ok=True)
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(e) + "\n")
            if f.stat().st_size > 600_000:
                keep = [x for x in _read_foreign_file(f) if x["time"] >= time.time() - FOREIGN_AGE][-FOREIGN_KEEP:]
                fsio.atomic_write_text(f, "".join(json.dumps(x) + "\n" for x in keep), fsync=False)
    except Exception:
        pass


def _read_foreign_file(f: Path) -> list:
    out = []
    try:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
                if isinstance(e, dict) and "path" in e and "time" in e:
                    out.append(e)
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _foreign_since(root: Path, shared: Path, start: float) -> dict:
    """path -> [sha, ...] of Meshpool's own writes made since `start`."""
    seen, out = set(), {}
    with _FW_LOCK:
        mem = list(_FW_MEM.get(str(root), []))
    f = shared / "foreign.jsonl"
    try:
        st = f.stat()
        sig = (st.st_mtime_ns, st.st_size)
        cached = _FW_FILE.get(str(f))
        if cached and cached[0] == sig:
            disk = cached[1]
        else:
            disk = _read_foreign_file(f)
            _FW_FILE[str(f)] = (sig, disk)
    except OSError:
        disk = []
    for e in mem + disk:
        if e["time"] < start:
            continue
        k = (e["path"], e["time"], e.get("actor"))
        if k in seen:
            continue
        seen.add(k)
        out.setdefault(e["path"], []).append(e.get("sha"))
    return out


def _explained(shas, value) -> bool:
    for s in shas or []:
        if s is None and value is None:
            return True
        if s and _stored(value) and _sha(value) == s:
            return True
    return False


# ---- open scan windows, per folder, to tell apart chats working in the same folder at the same time ---------------
_WIN_LOCK = threading.Lock()
_WINDOWS: dict[str, dict] = {}
_WIN_IDS = itertools.count(1)


def _win_open(root_key: str, store_key: str) -> dict:
    w = {"id": next(_WIN_IDS), "root": root_key, "store": store_key, "start": time.time(), "over": False}
    with _WIN_LOCK:
        cur = _WINDOWS.setdefault(root_key, {})
        for k, o in list(cur.items()):
            if time.time() - o["start"] > 6 * 3600:          # abandoned
                del cur[k]
            elif o["store"] != store_key:
                o["over"] = w["over"] = True
        cur[w["id"]] = w
    return w


def _win_close(w: dict) -> bool:
    with _WIN_LOCK:
        _WINDOWS.get(w["root"], {}).pop(w["id"], None)
    return w["over"]


_PATCH_RE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$")
_UDIFF_RE = re.compile(r"^(?:---|\+\+\+) [ab]/(.+?)(?:\t.*)?\s*$")


def _patch_paths(args: dict) -> list:
    out = []
    for v in (args or {}).values():
        if not isinstance(v, str):
            continue
        for line in v.splitlines():
            m = _PATCH_RE.match(line) or _UDIFF_RE.match(line)
            if m and m.group(1) not in out:
                out.append(m.group(1))
    return out


_LAST_GC: dict[str, float] = {}


class Checkpoints:
    def __init__(self, root: str, store: Path, mode: str = "scan", shared=None, seq_floor: int = 0):
        self.root = Path(root).resolve()
        self.store = Path(store)
        self.mode = "journal" if mode == "journal" else "scan"
        self.shared = Path(shared) if shared else default_shared_dir(self.root)
        self.objects = self.shared / "objects"
        self.legacy_objects = self.store / "objects"
        self.stat_path = self.shared / "stat.json"
        self.index_path = self.store / "turns.json"
        self.bak_path = self.store / "turns.json.bak"
        self.seq_path = self.store / "seq"
        self.disabled = ""
        self.warning = ""
        self.seq_floor = int(seq_floor or 0)
        self._lk = threading.RLock()
        self._idx: dict | None = None
        self._dirty = False
        self._cache: dict | None = None
        self._cache_dirty = False
        self._cache_saved = 0.0
        self._registered = False
        self._bg = None
        self._bg_count = 0
        self._win = None
        self._begin = None
        self._legacy: dict = {}

    @property
    def seq(self) -> int:
        return self._load()["seq"]

    # ---------- turns.json ---------------------------------------------------------------------------------------
    def _read_index(self):
        """-> (raw, recovered). Damage is quarantined and recovered from the .bak; never treated as empty."""
        def ok(x):
            return isinstance(x, list) or (isinstance(x, dict) and isinstance(x.get("turns"), list))
        try:
            raw = fsio.read_json(self.index_path, None)
            if raw is None and self.bak_path.exists():       # crashed between quarantine and rewrite
                raw = fsio.read_json(self.bak_path, None)
                return (raw, True) if ok(raw) else (None, False)
            if raw is not None and not ok(raw):
                raise fsio.CorruptFile(self.index_path, ValueError("unexpected shape"))
            return raw, False
        except fsio.CorruptFile:
            fsio.quarantine(self.index_path)
            try:
                bak = fsio.read_json(self.bak_path, None)
                if ok(bak):
                    return bak, True
                if bak is not None:
                    fsio.quarantine(self.bak_path)
            except fsio.CorruptFile:
                fsio.quarantine(self.bak_path)
            self.warning = "The Undo history of this chat was damaged and could not be recovered."
            return None, True

    def _read_seq_file(self) -> int:
        try:
            return int(self.seq_path.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _load(self) -> dict:
        with self._lk:
            if self._idx is not None:
                return self._idx
            raw, recovered = self._read_index()
            if isinstance(raw, list):
                raw = {"seq": 0, "turns": raw}
            idx = raw if isinstance(raw, dict) else {"seq": 0, "turns": []}
            idx["turns"] = [t for t in idx["turns"] if isinstance(t, dict)]
            top = max([int(t.get("n") or 0) for t in idx["turns"]] + [int(idx.get("seq") or 0)])
            idx["seq"] = max(top, self.seq_floor, self._read_seq_file())
            self._idx = idx
            if recovered:
                self._dirty = True
                self._flush()
            return idx

    def _flush(self) -> bool:
        """Write turns.json (atomic, previous version kept as .bak). A failure keeps the in-memory state for a retry."""
        with self._lk:
            if self._idx is None or not self._dirty:
                return True
            try:
                self.store.mkdir(parents=True, exist_ok=True)
                with fsio.file_lock(self.index_path, timeout=10):
                    if self.index_path.exists():
                        with contextlib.suppress(OSError):
                            self.bak_path.unlink()
                        try:
                            os.link(self.index_path, self.bak_path)
                        except OSError:
                            with contextlib.suppress(OSError):
                                shutil.copy2(self.index_path, self.bak_path)
                    fsio.atomic_write_json(self.index_path, self._idx, indent=None, separators=(",", ":"))
                    with contextlib.suppress(OSError):
                        fsio.atomic_write_text(self.seq_path, str(self._idx["seq"]), fsync=False)
            except (OSError, TimeoutError) as e:
                self.warning = f"Couldn't save the Undo history ({getattr(e, 'strerror', None) or e})."
                return False
            self._dirty = False
            self.warning = ""
            return True

    def _register(self) -> None:
        if self._registered:
            return
        try:
            def add(d):
                d[str(self.store)] = {"time": time.time(), "root": str(self.root)}
            fsio.update_json(self.shared / "chats.json", add, default={}, timeout=5)
            self._registered = True
        except Exception:
            pass

    @staticmethod
    def _done(t: dict) -> bool:
        return bool(t["done"]) if "done" in t else bool(t.get("after"))

    def _open_turn(self) -> dict | None:
        ts = self._load()["turns"]
        return ts[-1] if ts and not self._done(ts[-1]) and not ts[-1].get("pruned") else None

    # ---------- object store ---------------------------------------------------------------------------------------
    def _put(self, sha: str, data: bytes, durable: bool = False) -> None:
        p = self.objects / sha[:2] / sha[2:]
        try:
            if p.stat().st_size > 0:
                return
        except OSError:
            pass
        fsio.atomic_write_bytes(p, zlib.compress(data, 6), fsync=durable)

    def _get(self, sha: str) -> bytes:
        for base in (self.objects, self.legacy_objects):
            try:
                data = zlib.decompress((base / sha[:2] / sha[2:]).read_bytes())
            except (OSError, zlib.error):
                continue
            if hashlib.sha1(data).hexdigest() == sha:
                return data
        raise MissingObject(f"the saved copy {sha[:8]} is missing or damaged")

    def _has(self, sha: str) -> bool:
        return (self.objects / sha[:2] / sha[2:]).exists()

    def _touch_objects(self, values) -> None:
        now = time.time()
        for v in values:
            s = _sha(v)
            if not s:
                continue
            p = self.objects / s[:2] / s[2:]
            try:
                if now - p.stat().st_mtime > 600:
                    os.utime(p, None)
            except OSError:
                pass

    def _load_cache(self) -> dict:
        if self._cache is None:
            try:
                raw = fsio.read_json(self.stat_path, {})
            except fsio.CorruptFile:
                raw = {}
            self._cache = {k: v for k, v in (raw or {}).items() if isinstance(v, list) and len(v) == 3}
        return self._cache

    def _save_cache(self, force: bool = False) -> None:
        if self._cache is None or not self._cache_dirty:
            return
        if not force and time.time() - self._cache_saved < 5:
            return
        try:
            fsio.atomic_write_json(self.stat_path, self._cache, indent=None, fsync=False, separators=(",", ":"))
            self._cache_dirty = False
            self._cache_saved = time.time()
        except OSError:
            pass

    # ---------- reading the folder -----------------------------------------------------------------------------------
    def _scan(self, store: bool = True, persist: bool = True) -> dict | None:
        """Walk the folder (stat only; hash just the files whose size or mtime changed). -> manifest or None."""
        if self.disabled:
            return None
        cache = self._load_cache()
        rules = _ignore_rules(self.root)
        files, count, new_cache, hashed = {}, 0, {}, False
        racy = time.time_ns() - RACY_NS

        def note(rel: str, marker: str) -> bool:
            nonlocal count
            count += 1
            if count > MAX_FILES:
                self.disabled = ("This folder is too big to track changes in, so Undo is off here. "
                                 "Work in a smaller folder to turn it back on.")
                return False
            files[rel] = marker
            return True

        for dirpath, dirnames, filenames in os.walk(self.root):
            d = Path(dirpath)
            rel_d = "" if d == self.root else d.relative_to(self.root).as_posix()
            keep = []
            for n in dirnames:
                rel = f"{rel_d}/{n}" if rel_d else n
                if n in SKIP_DIRS or n.startswith(".meshpool"):
                    continue
                if (d / n).is_symlink():
                    if not note(rel + "/", "!link"):
                        return None
                    continue
                if _ignored(rel, True, rules):
                    if not note(rel + "/", "!ignored"):
                        return None
                    continue
                keep.append(n)
            dirnames[:] = keep
            for n in filenames:
                rel = f"{rel_d}/{n}" if rel_d else n
                if n in SKIP_FILES:
                    continue
                p = d / n
                try:
                    st = p.lstat()
                except OSError:
                    if not note(rel, "!unreadable"):
                        return None
                    continue
                marker = ("!link" if stat.S_ISLNK(st.st_mode) else "!special" if not stat.S_ISREG(st.st_mode)
                          else "!ignored" if _ignored(rel, False, rules) else _big(st) if st.st_size > MAX_FILE else "")
                if marker:
                    if not note(rel, marker):
                        return None
                    continue
                mode = f":{stat.S_IMODE(st.st_mode):o}"
                prev = cache.get(rel)
                if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
                    new_cache[rel] = prev
                    if not note(rel, prev[2] + mode):
                        return None
                    continue
                try:
                    data = p.read_bytes()
                    sha = hashlib.sha1(data).hexdigest()
                    if store:
                        self._put(sha, data)
                except OSError:
                    sha = ""
                if not note(rel, sha + mode if sha else "!unreadable"):
                    return None
                if sha and st.st_mtime_ns < racy:
                    new_cache[rel] = [st.st_size, st.st_mtime_ns, sha]
                hashed = True
        if persist:
            if hashed or len(new_cache) != len(cache):
                self._cache_dirty = True
            self._cache = new_cache
            self._save_cache()
        return files

    def _value_of(self, rel: str, rules=None, store: bool = True, durable: bool = False):
        """The recorded value of one path now, the way a scan would see it."""
        parts = rel.split("/")
        if any(x in SKIP_DIRS or x.startswith(".meshpool") for x in parts[:-1]) or parts[-1] in SKIP_FILES:
            return None
        rules = _ignore_rules(self.root) if rules is None else rules
        cur = self.root
        for i, x in enumerate(parts[:-1]):
            cur = cur / x
            try:
                st = cur.lstat()
            except OSError:
                return None
            if stat.S_ISLNK(st.st_mode):
                return "!link"
            if not stat.S_ISDIR(st.st_mode):
                return None
            if _ignored("/".join(parts[:i + 1]), True, rules):
                return "!ignored"
        p = self.root / rel
        try:
            st = p.lstat()
        except OSError:
            return None
        if stat.S_ISDIR(st.st_mode):
            return None
        if stat.S_ISLNK(st.st_mode):
            return "!link"
        if not stat.S_ISREG(st.st_mode):
            return "!special"
        if _ignored(rel, False, rules):
            return "!ignored"
        if st.st_size > MAX_FILE:
            return _big(st)
        mode = f":{stat.S_IMODE(st.st_mode):o}"
        cache = self._load_cache()
        prev = cache.get(rel)
        if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns and (not store or self._has(prev[2])):
            return prev[2] + mode
        try:
            data = p.read_bytes()
        except OSError:
            return "!unreadable"
        sha = hashlib.sha1(data).hexdigest()
        if store:
            self._put(sha, data, durable=durable)
            if st.st_mtime_ns < time.time_ns() - RACY_NS:
                cache[rel] = [st.st_size, st.st_mtime_ns, sha]
                self._cache_dirty = True
        return sha + mode

    # ---------- windows (scan-mode turns, journal opaque tools) -----------------------------------------------------
    def _begin_window(self) -> dict | None:
        w = _win_open(str(self.root), str(self.store.resolve()))
        w["before"] = self._scan()
        if w["before"] is None:
            _win_close(w)
            return None
        return w

    def _end_window(self, w) -> tuple:
        """-> (pre, post, ambiguous paths) for what changed since the window began."""
        if not w:
            return {}, {}, set()
        after = self._scan()
        over = _win_close(w) if "id" in w else w.get("over", False)
        if after is None:
            return {}, {}, set()
        before = w["before"]
        fw = _foreign_since(self.root, self.shared, w["start"])
        pre, post, amb = {}, {}, set()
        for path in sorted(set(before) | set(after)):
            if path.endswith("/") or before.get(path) == after.get(path):
                continue
            va = _val(after, path)
            if _explained(fw.get(path), va):
                continue
            pre[path], post[path] = _val(before, path), va
            if over:
                amb.add(path)
        return pre, post, amb

    def _book(self, t: dict, pre: dict, post: dict, amb=()) -> None:
        if not post:
            return
        with self._lk:
            self._touch_objects(list(pre.values()) + list(post.values()))
            t.setdefault("pre", {})
            t.setdefault("post", {})
            for p, v in pre.items():
                t["pre"].setdefault(p, v)
            t["post"].update(post)
            t["changed"] = self._changed(t, set(amb))
            self._dirty = True

    @staticmethod
    def _changed(t: dict, amb=()) -> list:
        old = {c["path"] for c in t.get("changed") or [] if c.get("ambiguous")}
        out = []
        for p in sorted(t.get("post") or {}):
            a, b = (t.get("pre") or {}).get(p), t["post"][p]
            if a == b:
                continue
            c = {"path": p, "status": _status(a, b)}
            if p in old or p in amb:
                c["ambiguous"] = True
            out.append(c)
        return out

    # ---------- turns ------------------------------------------------------------------------------------------------
    def begin_turn(self, title: str, message_index: int | None = None, event_index: int | None = None) -> dict | None:
        with self._lk:
            idx = self._load()
            if self.mode == "scan":
                if self.disabled:
                    return None
                w = self._begin_window()
                if w is None:
                    return None
                self._win = w
                self._begin = w["before"]
            prev = self._open_turn()
            if prev is not None:                       # a turn that never ended (crash): close it as it stands
                prev["done"], prev["incomplete"] = True, True
            n = idx["seq"] + 1
            idx["seq"] = n
            t = {"n": n, "title": (title or "")[:120], "time": time.time(), "message_index": message_index,
                 "event_index": event_index, "pre": {}, "post": {}, "changed": [], "done": False, "mode": self.mode}
            idx["turns"].append(t)
            self._dirty = True
            self._register()
            self._flush()
            if self.mode == "scan":
                with contextlib.suppress(OSError):
                    fsio.atomic_write_json(self.store / "open.json", {"n": n, "time": t["time"], "files": self._begin},
                                           indent=None, fsync=False, separators=(",", ":"))
            self._save_cache(True)
            return dict(t)

    def end_turn(self):
        with self._lk:
            t = self._open_turn()
            if t is None:
                return
            if self.mode == "scan":
                w = self._win
                if w is None:
                    before = self._begin_manifest(t)
                    w = {"before": before, "start": t["time"], "over": False} if before is not None else None
                self._win = self._begin = None
                self._book(t, *self._end_window(w))
                with contextlib.suppress(OSError):
                    (self.store / "open.json").unlink()
            elif self._bg_count:
                self._bg_count = 0
                w, self._bg = self._bg, None
                self._book(t, *self._end_window(w))
            t["done"] = True
            t["changed"] = self._changed(t)
            self._dirty = True
            self._prune()
            self._flush()
        self._save_cache(True)
        self.gc()

    def _begin_manifest(self, t: dict):
        if self._begin is not None:
            return self._begin
        try:
            raw = fsio.read_json(self.store / "open.json", None)
        except fsio.CorruptFile:
            return None
        if isinstance(raw, dict) and raw.get("n") == t["n"] and isinstance(raw.get("files"), dict):
            return raw["files"]
        return None

    def _prune(self) -> None:
        turns = self._load()["turns"]
        live = [t for t in turns if not t.get("pruned") and self._done(t)]
        for t in live[:max(0, len(live) - undo_turns())]:
            for k in ("pre", "post", "before", "after"):
                t.pop(k, None)
            t["pruned"] = True
            t["done"] = True
            self._dirty = True
        self._cleanup_legacy()

    def _cleanup_legacy(self) -> None:
        if any("before" in t for t in self._load()["turns"]):
            return
        for d in (self.store / "objects", self.store / "manifests"):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def turns(self) -> list:
        return [{"n": t.get("n"), "title": t.get("title"), "time": t.get("time"), "changed": t.get("changed"),
                 "done": self._done(t), "pruned": bool(t.get("pruned")), "message_index": t.get("message_index"),
                 "event_index": t.get("event_index")} for t in self._load()["turns"]]

    # ---------- journal: tool calls ------------------------------------------------------------------------------------
    def _targets(self, name: str, args: dict) -> list:
        if name == "apply_patch":
            raw = _patch_paths(args)
        else:
            raw = next(([args[k]] for k in ("path", "file_path", "notebook_path")
                        if isinstance(args.get(k), str) and args[k]), [])
        out = []
        for p in raw:
            r = _rel_in_root(self.root, p)
            if r and r not in out:
                out.append(r)
        return out

    @contextlib.contextmanager
    def tool(self, name: str, args: dict, read_only: bool = False):
        """Wrap one tool call (journal mode). See the module docstring."""
        t = None if (self.mode != "journal" or read_only) else self._open_turn()
        if t is None:
            yield
            return
        args = args if isinstance(args, dict) else {}
        if name in WRITE_TOOLS or name == "apply_patch":
            rels = self._targets(name, args)
            rules = _ignore_rules(self.root)

            def val(r, durable=False):
                try:
                    return self._value_of(r, rules, durable=durable)
                except Exception:
                    return "!unreadable"
            pre = {r: val(r, True) for r in rels if r not in (t.get("pre") or {})}
            try:
                yield
            finally:
                post = {r: val(r) for r in rels}
                self._book(t, pre, post)
                self._flush()
            return
        w = self._begin_window()
        try:
            yield
        finally:
            self._book(t, *self._end_window(w))
            self._flush()
            self._save_cache()

    def window_open(self) -> None:
        """Keep a scan window open (journal mode): a background process the chat started may still change files."""
        with self._lk:
            if self.mode != "journal" or self._open_turn() is None:
                return
            self._bg_count += 1
            if self._bg_count == 1:
                self._bg = self._begin_window()

    def window_close(self) -> None:
        with self._lk:
            if self._bg_count <= 0:
                return
            self._bg_count -= 1
            if self._bg_count == 0:
                w, self._bg = self._bg, None
                t = self._open_turn()
                if t is not None:
                    self._book(t, *self._end_window(w))
                    self._flush()

    # ---------- reading ------------------------------------------------------------------------------------------------
    def _manifest(self, mid: str):
        try:
            m = fsio.read_json(self.store / "manifests" / f"{mid}.json", None)
        except fsio.CorruptFile:
            return None
        return m if isinstance(m, dict) else None

    def _pre_post(self, t: dict, live: bool = False) -> tuple:
        """(pre, post) of a turn, whatever format it was written in."""
        if "before" not in t:
            if live and not self._done(t) and t.get("mode") == "scan":
                before = self._begin_manifest(t)
                after = self._scan(store=False, persist=False) if before is not None else None
                if after is not None:
                    paths = self._paths_differing(before, after)
                    return ({p: _val(before, p) for p in paths}, {p: _val(after, p) for p in paths})
            return t.get("pre") or {}, t.get("post") or {}
        if not t.get("after"):
            return {}, {}
        if t["n"] not in self._legacy:
            a, b = self._manifest(t["before"]), self._manifest(t["after"])
            if a is None or b is None:
                raise ValueError(f"The saved Undo record of turn {t['n']} is missing or damaged.")
            paths = [c["path"] for c in t.get("changed") or []]
            self._legacy[t["n"]] = ({p: _val(a, p) for p in paths}, {p: _val(b, p) for p in paths})
        return self._legacy[t["n"]]

    @staticmethod
    def _paths_differing(a: dict, b: dict) -> list:
        return [p for p in sorted(set(a) | set(b)) if not p.endswith("/") and a.get(p) != b.get(p)]

    def _text(self, v, path: str):
        if not v:
            return []
        if not _stored(v):
            return None                          # exists, contents not stored: shown like a binary file
        try:
            data = self._get(_sha(v))
        except MissingObject:
            try:
                data = (self.root / path).read_bytes()
            except OSError:
                return None
            if hashlib.sha1(data).hexdigest() != _sha(v):
                return None
        if b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", "replace").splitlines()

    def diff(self, turn: int | None = None, max_chars: int = 400_000) -> dict:
        """Unified diff for one turn, or for the whole thread (turn None: first turn's start to now). Writes nothing."""
        turns = self._load()["turns"]
        if not turns:
            return {"files": [], "patch": "", "note": self.disabled or "No changes recorded yet."}
        amb = set()
        if turn is None:
            a, b = {}, {}
            for t in sorted(turns, key=lambda x: x["n"]):
                if t.get("pruned"):
                    continue
                try:
                    pre, post = self._pre_post(t, live=True)
                except ValueError:
                    continue
                for p in sorted(post):
                    if pre.get(p) == post[p] and p not in a:
                        continue
                    a.setdefault(p, pre.get(p))
                    b[p] = post[p]
                amb |= {c["path"] for c in t.get("changed") or [] if c.get("ambiguous")}
        else:
            t = next((x for x in turns if x["n"] == turn), None)
            if not t:
                raise ValueError("No such turn.")
            if t.get("pruned"):
                return {"files": [c | {"add": 0, "del": 0, "binary": True} for c in t.get("changed") or []],
                        "patch": "", "note": "This turn is too old to show its changes."}
            a, b = self._pre_post(t, live=True)
            amb = {c["path"] for c in t.get("changed") or [] if c.get("ambiguous")}
        files, chunks, size = [], [], 0
        for path in sorted(set(a) | set(b)):
            if a.get(path) == b.get(path):
                continue
            ch = {"path": path, "status": _status(a.get(path), b.get(path))}
            if path in amb:
                ch["ambiguous"] = True
            old, new = self._text(a.get(path), path), self._text(b.get(path), path)
            if old is None or new is None:
                files.append(ch | {"add": 0, "del": 0, "binary": True})
                chunks.append(f"diff --meshpool a/{path} b/{path}\nBinary file {ch['status']}\n")
                continue
            lines = list(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=3, lineterm=""))
            add = sum(1 for x in lines if x.startswith("+") and not x.startswith("+++"))
            rem = sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))
            files.append(ch | {"add": add, "del": rem})
            text = "\n".join(lines) + "\n"
            if size + len(text) <= max_chars:
                chunks.append(text)
                size += len(text)
        return {"files": files, "patch": "".join(chunks), "note": ""}

    # ---------- undo ---------------------------------------------------------------------------------------------------
    def revert_to_before(self, turn: int) -> dict:
        """Undo the file changes the agent made in `turn` and every later turn. Only files those turns changed are
        touched, and only while they still hold what the agent left: files the user (or another tool) created or
        edited since stay as they are and are listed in "kept". Whatever Undo deletes or overwrites is first copied to
        "backup". Returns what changed on disk."""
        turn = int(turn)
        with self._lk:
            idx = self._load()
            turns = idx["turns"]
            t = next((x for x in turns if x["n"] == turn), None)
            if not t:
                raise ValueError("No such turn.")
            if self.disabled:
                raise ValueError(self.disabled)
            later = [x for x in turns if x["n"] >= turn]
            if any(x.get("pruned") for x in later):
                raise ValueError(f"This turn is too old to undo: Meshpool keeps the last {undo_turns()} turns "
                                 "undoable. Undo by hand or with Git.")
            agent_left: dict = {}      # path -> value the agent's last change left
            target: dict = {}          # path -> value before the agent's first change
            amb: set = set()
            for x in sorted((x for x in later if self._done(x)), key=lambda x: x["n"]):
                pre, post = self._pre_post(x)
                for ch in x.get("changed") or []:
                    p = ch["path"]
                    agent_left[p] = post.get(p)
                    target.setdefault(p, pre.get(p))
                    if ch.get("ambiguous"):
                        amb.add(p)
            restored, deleted, kept, notes = [], [], [], []

            def keep(path: str, why: str = ""):
                kept.append(path)
                if why:
                    notes.append(f"{path}: {why}")

            rules = _ignore_rules(self.root)
            now = {p: self._value_of(p, rules, store=False) for p in agent_left}
            # 1. decide every action first, touching nothing
            unlinks, writes = [], []
            for path, left in sorted(agent_left.items()):
                cur, tgt = now[path], target.get(path)
                if path in amb:
                    keep(path, AMBIGUOUS_NOTE)
                elif cur is not None and not _stored(cur):      # can't tell what it holds now
                    keep(path, "its current contents aren't tracked (ignored, over 2 MB or a link), so it was left as is")
                elif not _same(cur, left):                      # changed after the agent: not ours to undo
                    keep(path)
                elif tgt is not None and not _stored(tgt):      # its earlier state was never stored
                    keep(path, "its earlier version wasn't saved (ignored or over 2 MB), so it was left as is")
                elif tgt is not None:
                    if not _same(cur, tgt):
                        writes.append(path)
                elif cur is not None:
                    unlinks.append(path)
            if writes and self._case_insensitive():
                gone = {p.lower() for p in unlinks}
                for path in list(writes):
                    other = self._case_twin(path)
                    if other and path.lower() not in gone:
                        writes.remove(path)
                        keep(path, f"{other} now takes its name on this disk (names ignore case here), so it was left as is")

            # 2. apply: removals first (so a case-only rename or a file<->folder swap can be put back), then writes
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bdir = self.store / "undone" / stamp
            for i in itertools.count(2):
                if not bdir.exists():
                    break
                bdir = self.store / "undone" / f"{stamp}-{i}"
            copied = []

            def backup(path: str) -> bool:
                p = self.root / path
                if p.is_symlink() or not p.is_file():
                    return True
                try:
                    dst = bdir / path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
                    copied.append(path)
                    return True
                except OSError:
                    return False

            for path in sorted(unlinks, reverse=True):
                p = self.root / path
                try:
                    if not self._inside(p.parent):
                        keep(path, "it is behind a link that points outside the project, so it was left as is")
                        continue
                    if not backup(path):
                        keep(path, "couldn't save a copy of it first, so it was left as is")
                        continue
                    p.unlink()
                    deleted.append(path)
                except OSError as e:
                    keep(path, f"couldn't delete it ({e.strerror or e})")
            blocked = {k.lower(): k for k in kept} if self._case_insensitive() else {}
            for path in writes:
                p = self.root / path
                clash = blocked.get(path.lower())
                if clash and clash != path:       # the other-case name could not be removed and is this same file here
                    keep(path, f"{clash} still has its name on this disk (names ignore case here), so it was left as is")
                    continue
                try:
                    if not self._inside(p.parent):
                        keep(path, "it is behind a link that points outside the project, so it was left as is")
                        continue
                    try:
                        data = self._get(_sha(target[path]))
                    except MissingObject:
                        keep(path, "its earlier copy is missing or damaged, so it was left as is")
                        continue
                    if p.is_symlink():
                        p.unlink()                # replace the link, never write through it
                    elif p.is_dir():
                        # a folder now stands where the file was, so it was made after the turn began: remove it only
                        # if nothing but empty folders is left in it
                        if not self._remove_empty_tree(p):
                            keep(path, "a folder with other files in it now has its name, so it was left as is")
                            continue
                    if not backup(path):
                        keep(path, "couldn't save a copy of it first, so it was left as is")
                        continue
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(data)
                    if _mode(target[path]) is not None:
                        os.chmod(p, _mode(target[path]))
                    restored.append(path)
                except OSError as e:
                    keep(path, f"couldn't put it back ({getattr(e, 'strerror', None) or e})")
            cache = self._load_cache()
            for path in restored + deleted:
                cache.pop(path, None)
                note_foreign_write(self.root, path, str(self.store), shared=self.shared)
            self._cache_dirty = True
            idx["turns"] = [x for x in turns if x["n"] < turn]
            self._legacy.clear()
            self._dirty = True
            self._prune()
            self._flush()
            self._save_cache(True)
            self._trim_backups()
        self.gc()
        out = {"restored": restored, "deleted": deleted, "kept": kept, "message_index": t.get("message_index")}
        if notes:
            out["notes"] = notes
        if copied:
            out["backup"] = str(bdir)
        return out

    def _trim_backups(self) -> None:
        d = self.store / "undone"
        try:
            names = sorted(x.name for x in d.iterdir() if x.is_dir())
        except OSError:
            return
        for n in names[:-BACKUPS_KEEP]:
            shutil.rmtree(d / n, ignore_errors=True)

    def _case_twin(self, path: str) -> str | None:
        """Another file in the same folder whose name differs from `path` only in case."""
        p = self.root / path
        try:
            for e in os.scandir(p.parent):
                if e.name != p.name and e.name.lower() == p.name.lower() and not e.is_dir(follow_symlinks=False):
                    return (path.rpartition("/")[0] + "/" if "/" in path else "") + e.name
        except OSError:
            pass
        return None

    # ---------- garbage collection ----------------------------------------------------------------------------------
    def _marks(self):
        """Shas still referenced by any chat's turns or the stat cache, or None when something is unreadable."""
        marks: set = set()

        def add(v):
            s = _sha(v)
            if s:
                marks.add(s)

        def add_turns(turns):
            for t in turns:
                for k in ("pre", "post"):
                    for v in (t.get(k) or {}).values():
                        add(v)

        try:
            reg = fsio.read_json(self.shared / "chats.json", {}) or {}
        except fsio.CorruptFile:
            return None, []
        dead = []
        for sp in list(reg):
            if Path(sp) == self.store:
                continue
            d = Path(sp)
            if not d.exists():
                dead.append(sp)
                continue
            raw = None
            for f in (d / "turns.json", d / "turns.json.bak"):
                try:
                    raw = fsio.read_json(f, None)
                    break
                except fsio.CorruptFile:
                    continue
            else:
                return None, []
            if isinstance(raw, dict):
                raw = raw.get("turns")
            add_turns([t for t in raw or [] if isinstance(t, dict)])
            with contextlib.suppress(Exception):
                o = fsio.read_json(d / "open.json", None)
                for v in ((o or {}).get("files") or {}).values():
                    add(v)
        add_turns(self._load()["turns"])
        for v in (self._begin or {}).values():
            add(v)
        try:
            disk = fsio.read_json(self.stat_path, {}) or {}
        except fsio.CorruptFile:
            return None, []
        for e in list(disk.values()) + list((self._cache or {}).values()):
            if isinstance(e, list) and len(e) == 3:
                marks.add(e[2])
        return marks, dead

    def gc(self, force: bool = False) -> dict:
        """Delete saved copies nothing refers to any more (older than an hour). At most every 10 minutes unless forced."""
        key = str(self.shared)
        now = time.time()
        if not force and now - _LAST_GC.get(key, 0) < GC_EVERY:
            return {}
        _LAST_GC[key] = now
        try:
            self._save_cache(True)
            with fsio.file_lock(self.shared / "gc", timeout=30 if force else 2):
                marks, dead = self._marks()
                if marks is None:
                    return {}
                deleted = kept = 0
                try:
                    subs = list(os.scandir(self.objects))
                except OSError:
                    subs = []
                for sd in subs:
                    if not sd.is_dir():
                        continue
                    for e in os.scandir(sd.path):
                        sha = sd.name + e.name
                        try:
                            old = e.stat().st_mtime < now - GC_GRACE
                        except OSError:
                            continue
                        if sha in marks or not old:
                            kept += 1
                            continue
                        with contextlib.suppress(OSError):
                            os.unlink(e.path)
                            deleted += 1
                    with contextlib.suppress(OSError):
                        os.rmdir(sd.path)
                if dead:
                    def drop(d):
                        for s in dead:
                            d.pop(s, None)
                    with contextlib.suppress(Exception):
                        fsio.update_json(self.shared / "chats.json", drop, default={}, timeout=5)
                return {"deleted": deleted, "kept": kept}
        except (OSError, TimeoutError):
            return {}

    def _inside(self, p: Path) -> bool:
        try:
            Path(os.path.realpath(p)).relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _remove_empty_tree(d: Path) -> bool:
        for dirpath, dirnames, filenames in os.walk(d, topdown=False):
            if filenames:
                return False
            try:
                os.rmdir(dirpath)
            except OSError:
                return False
        return True

    def _case_insensitive(self) -> bool:
        name = self.root.name
        if name.swapcase() != name:
            alt = self.root.parent / name.swapcase()
            try:
                return alt.exists() and os.path.samefile(alt, self.root)
            except OSError:
                return False
        try:
            self.store.mkdir(parents=True, exist_ok=True)
            probe = self.store / "CaseProbe.tmp"
            probe.write_bytes(b"")
            try:
                return (self.store / "caseprobe.tmp").exists()
            finally:
                probe.unlink()
        except OSError:
            return False
