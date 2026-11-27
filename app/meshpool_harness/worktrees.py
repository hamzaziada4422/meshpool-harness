"""Isolated copies of a project folder: one per chat, sub-agent, scheduled run or terminal session.

Several agents working in one folder edit each other's files, and each turn's checkpoint then sees the other's edits
(so Undo refuses). An isolated copy gives each its own folder:

- a git folder gets a git worktree on its own branch ``meshpool/<id>``, made from the folder's HEAD (chats, scheduled
  runs, the terminal) or from a snapshot of its working tree including unsaved edits (sub-agents);
- any other folder gets a plain copy, plus a second copy of how it started (``base``) for the diff and the merge.

Copies live in ``<MESHPOOL_HOME>/wt/<id>``: short paths (Windows), outside the user's repository. The registry
``wt/registry.json`` is only written through ``fsio.update_json`` (atomic, locked across processes), and every
operation on one repository holds a per-repository lock.

Merging back never overwrites the user's work: conflicts are computed in memory first (``git merge-tree``) and
reported, and a merge that would touch a file with uncommitted changes in the folder is refused. A record is written
as ``creating`` before anything is made, so a crash mid-create is cleaned up by ``recover()`` at the next start.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import shutil
import stat
import subprocess as _sp
import sys
import time
import uuid
from pathlib import Path

from meshpool_harness import fsio

_WINDOWS = os.name == "nt"
HARNESS_DIRS = (".run", ".junie")                  # the code engine's own folders: never committed or merged
MAX_COPY_BYTES = 500 * 1024 * 1024
SUBMODULE_TIMEOUT = 180
OWNER_KINDS = ("chat", "subagent", "schedule", "cli")
IDENT = {"GIT_AUTHOR_NAME": "Meshpool", "GIT_AUTHOR_EMAIL": "meshpool@localhost",
         "GIT_COMMITTER_NAME": "Meshpool", "GIT_COMMITTER_EMAIL": "meshpool@localhost"}


class WorktreeError(ValueError):
    """An isolated copy could not be made, found or changed. The message is for the user."""


# ---------- paths ----------------------------------------------------------------------------------------------------
def root() -> Path:
    from meshpool_harness import config as C
    return C.home() / "wt"


def _registry() -> Path:
    return root() / "registry.json"


def _norm(p) -> str:
    """One spelling per folder, for comparisons (case-insensitive on Windows, symlinks resolved)."""
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(p))))


def _long(p) -> str:
    """A path Windows accepts beyond 260 characters (``\\\\?\\`` prefix); unchanged elsewhere."""
    s = str(p)
    if not _WINDOWS or s.startswith("\\\\?\\"):
        return s
    s = os.path.abspath(s).replace("/", "\\")
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def _rm_readonly(func, path, _exc):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)     # git's pack files are read-only: Windows refuses to delete them
        parent = os.path.dirname(path)
        if parent and not os.access(parent, os.W_OK):   # POSIX: an entry is removed through its folder, which must be writable
            os.chmod(parent, stat.S_IRWXU)
        if func in (os.rmdir, getattr(os, "rmdir", None)) and os.path.isdir(path) and os.listdir(path):
            os.chmod(path, stat.S_IRWXU)                # a read-only folder whose entries could not be removed: unlock, retry
            shutil.rmtree(path, **{"onexc" if sys.version_info >= (3, 12) else "onerror": _rm_readonly})
            return
        func(path)
    except FileNotFoundError:
        pass


def _rmtree(p) -> None:
    if not os.path.lexists(_long(p)):
        return
    if os.path.islink(p) or os.path.isfile(_long(p)):
        os.unlink(_long(p))
        return
    kw = {"onexc" if sys.version_info >= (3, 12) else "onerror": _rm_readonly}   # onexc is 3.12+, onerror before
    shutil.rmtree(_long(p), **kw)


# ---------- git ------------------------------------------------------------------------------------------------------
def _git_cmd(*args: str) -> list:
    pre = ["git", "-c", "core.quotepath=false"]
    if _WINDOWS:
        pre += ["-c", "core.longpaths=true"]       # deep node_modules / src trees past MAX_PATH
    # never the repository's own programs: no hooks (worktree add runs post-checkout), no fsmonitor
    pre += ["-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}"]
    pre.append("--no-optional-locks")                   # a status never holds the user's .git/index.lock
    return pre + list(args)


def _git(cwd, *args: str, env: dict | None = None, stdin: bytes | None = None) -> _sp.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    e.update(env or {})
    try:
        return _sp.run(_git_cmd(*args), cwd=str(cwd), capture_output=True, env=e, input=stdin)
    except FileNotFoundError as ex:
        raise WorktreeError("Git is not installed on this computer.") from ex


def _out(r: _sp.CompletedProcess) -> str:
    return r.stdout.decode("utf-8", "surrogateescape").strip()


def _err(r: _sp.CompletedProcess) -> str:
    return (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()[:500]


def _ident(cwd) -> dict:
    """A commit identity only where the user has none configured (a fresh computer): theirs always wins."""
    if _out(_git(cwd, "config", "user.email")) and _out(_git(cwd, "config", "user.name")):
        return {}
    return dict(IDENT)


def _engine():
    try:
        from engines.code import engine as E
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from engines.code import engine as E
    return E


def toplevel(folder) -> str | None:
    """The repository's top folder when ``folder`` is inside a git repository with at least one commit."""
    try:
        r = _git(folder, "rev-parse", "--show-toplevel")
    except (WorktreeError, OSError):
        return None
    if r.returncode or not _out(r):
        return None
    if _git(folder, "rev-parse", "--verify", "-q", "HEAD^{commit}").returncode:
        return None                                 # no commit yet: nothing to branch from
    return str(Path(_out(r)).resolve())


def is_git(folder) -> bool:
    return toplevel(folder) is not None


def _head(cwd) -> str:
    return _out(_git(cwd, "rev-parse", "HEAD"))


def _ancestor(cwd, a: str, b: str) -> bool:
    return _git(cwd, "merge-base", "--is-ancestor", a, b).returncode == 0


def _z(r: _sp.CompletedProcess) -> list[str]:
    return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _in_progress(top) -> str:
    """What the folder is in the middle of (a merge, rebase ...), or ''."""
    for ref, what in (("MERGE_HEAD", "a merge"), ("CHERRY_PICK_HEAD", "a cherry-pick"), ("REVERT_HEAD", "a revert")):
        if _git(top, "rev-parse", "-q", "--verify", ref).returncode == 0:
            return what
    for d in ("rebase-merge", "rebase-apply"):
        p = _out(_git(top, "rev-parse", "--git-path", d))
        if p and Path(top, p).exists():
            return "a rebase"
    return ""


def _dirty_paths(top) -> set[str]:
    """Paths (relative to the repository top) with uncommitted changes, staged or not, and untracked files."""
    r = _git(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    out, parts = set(), r.stdout.decode("utf-8", "surrogateescape").split("\0")
    for p in parts:
        if len(p) > 3:
            out.add(p[3:])
    return out


def _commit_pending(wt, message: str) -> bool:
    """Commit everything changed in the copy (harness folders excepted) on its branch. True when a commit was made."""
    ex = [f":!{d}" for d in HARNESS_DIRS]
    if _git(wt, "add", "-A", "--", ".", *ex).returncode:
        raise WorktreeError("Could not record the changes in the isolated copy: " + _err(_git(wt, "status")))
    if _git(wt, "diff", "--cached", "--quiet").returncode == 0:
        return False
    r = _git(wt, "commit", "-q", "--no-verify", "-m", message, env=_ident(wt))
    if r.returncode:
        raise WorktreeError("Could not commit the changes in the isolated copy: " + _err(r))
    return True


# ---------- registry -------------------------------------------------------------------------------------------------
_READ_TRIES = 6
SIDECAR = "copy.json"                             # a plain copy's own description, next to its work/ and base/ folders


class RegistryUnreadable(WorktreeError):
    """The registry exists but cannot be read right now (a sharing violation, a permission). Nothing may be decided
    from it: it is never treated as empty, moved aside or rewritten."""


def _check_doc(d, path) -> dict:
    if d is None:
        return {}
    if not isinstance(d, dict) or not isinstance(d.get("items", {}), dict):
        raise fsio.CorruptFile(path, ValueError("not a registry of isolated copies"))
    return d


def _load() -> tuple[dict, bool]:
    """(registry document, read cleanly). A file that cannot be read right now is retried, then RegistryUnreadable:
    an OSError is never mistaken for damage (moving the registry aside made every copy look like an orphan, and
    recover() deleted them all with their unmerged work). A file that is really damaged (not JSON, not a registry) is
    moved aside and the records are rebuilt from the copies' folders under wt/; the second value is then False."""
    p, last = _registry(), None
    for i in range(_READ_TRIES):
        try:
            return _check_doc(fsio.read_json(p, default={}), p), True
        except fsio.CorruptFile as e:
            if not isinstance(e.cause, OSError):
                return _rebuild(), False
            last = e
            time.sleep(0.05 * (i + 1))
    raise RegistryUnreadable(f"Could not read the list of isolated copies ({last.cause if last else p}). Nothing was "
                             "changed; try again in a moment.")


def _items() -> dict:
    return _load()[0].get("items", {})


def _record_from_folder(d: Path) -> dict | None:
    """What can be known about copy folder ``d`` without its registry record: a git worktree describes itself (its
    repository, branch and where it started); a plain copy has its sidecar. Rebuilt records belong to nobody, so no
    clean-up removes them; the user merges or discards them."""
    note = "Recovered after the list of isolated copies was damaged."
    rec = {"id": d.name, "path": str(d), "base_dirty": False, "owner": {"kind": "cli", "id": "", "pid": None},
           "title": "", "state": "ready", "created": d.stat().st_mtime, "merged": None, "note": note}
    if (d / "work").is_dir() and (d / "base").is_dir():
        side = {}
        try:
            side = fsio.read_json(d / SIDECAR, default={}) or {}
        except fsio.CorruptFile:
            pass
        side = side if isinstance(side, dict) else {}
        rec.update({"kind": "copy", "mode": str(side.get("mode") or "apply"), "source": str(side.get("source") or ""),
                    "top": None, "branch": "", "base": "", "workdir": str(d / "work"), "base_dir": str(d / "base"),
                    "title": str(side.get("title") or "")[:120]})
        if side.get("created"):
            rec["created"] = side["created"]
        return rec
    if not (d / ".git").is_file():
        return None
    try:
        common = _out(_git(d, "rev-parse", "--git-common-dir"))
        branch = _out(_git(d, "symbolic-ref", "-q", "--short", "HEAD"))
        tip = _head(d)
    except (WorktreeError, OSError):
        return None
    common_p = Path(common if os.path.isabs(common) else os.path.join(d, common)).resolve() if common else None
    if not common_p or not tip or common_p.name != ".git" or not common_p.parent.is_dir():
        return None
    top = str(common_p.parent)
    base = _out(_git(top, "merge-base", _head(top), tip)) or tip
    rec.update({"kind": "git", "mode": "commit", "source": top, "top": top, "branch": branch, "base": base,
                "head": base, "sub": "", "workdir": str(d)})
    return rec


def _rebuild() -> dict:
    """Move the damaged registry aside and write one rebuilt from the folders under wt/ (under the registry's lock;
    a second process that already repaired it wins)."""
    p = _registry()
    with fsio.file_lock(p):
        try:
            return _check_doc(fsio.read_json(p, default={}), p)
        except fsio.CorruptFile as e:
            if isinstance(e.cause, OSError):
                raise RegistryUnreadable(f"Could not read the list of isolated copies ({e.cause}).") from e
        moved = fsio.quarantine(p)
        if moved is None and p.exists():
            raise RegistryUnreadable("The list of isolated copies is damaged and could not be moved aside.")
        items = {}
        for d in sorted(root().iterdir()) if root().is_dir() else []:
            if d.is_dir() and d.name not in ("locks",):
                rec = _record_from_folder(d)
                if rec:
                    items[rec["id"]] = rec
        doc = {"version": 1, "items": items, "rebuilt": time.time(), "damaged_copy": str(moved or "")}
        fsio.atomic_write_json(p, doc)
        return doc


def _put(rec: dict) -> None:
    def fn(d):
        d = d if isinstance(d, dict) else {}
        d.setdefault("version", 1)
        d.setdefault("items", {})[rec["id"]] = rec
        return d
    _update(fn)


def _drop(wid: str) -> None:
    def fn(d):
        d = d if isinstance(d, dict) else {}
        d.setdefault("items", {}).pop(wid, None)
        return d
    _update(fn)


def _update(fn) -> None:
    """Locked read-modify-write of the registry. A registry that cannot be read is retried, then RegistryUnreadable;
    nothing built from an unreadable registry is ever written. A damaged one is rebuilt first, then changed."""
    p = _registry()

    def guarded(d):
        return fn(_check_doc(d, p))
    last = None
    for i in range(_READ_TRIES):
        try:
            fsio.update_json(p, guarded, default={"version": 1, "items": {}})
            return
        except fsio.CorruptFile as e:
            if not isinstance(e.cause, OSError):
                _rebuild()                          # then apply fn to the rebuilt registry
                continue
            last = e
            time.sleep(0.05 * (i + 1))
    raise RegistryUnreadable(f"Could not update the list of isolated copies ({last.cause if last else p}). Nothing "
                             "was changed; try again in a moment.")


def _lock(key: str):
    h = hashlib.sha1(_norm(key).encode("utf-8", "surrogateescape")).hexdigest()[:16]
    return fsio.file_lock(root() / "locks" / h, timeout=120)


def get(wid: str) -> dict:
    """The record of copy ``wid`` (a unique prefix of its id works too). KeyError when there is none."""
    items = _items()
    if wid in items:
        return items[wid]
    hits = [v for k, v in items.items() if wid and k.startswith(wid)]
    if len(hits) == 1:
        return hits[0]
    raise KeyError(f"No isolated copy {wid!r}." if not hits else f"{wid!r} matches several copies; give more of the id.")


def listing(source=None, owner_kind: str | None = None, owner_id: str | None = None) -> list[dict]:
    rows = list(_items().values())
    if source:
        s = _norm(source)
        rows = [r for r in rows if _norm(r.get("source", "")) == s or _norm(r.get("top") or "") == s]
    if owner_kind:
        rows = [r for r in rows if (r.get("owner") or {}).get("kind") == owner_kind]
    if owner_id is not None:
        rows = [r for r in rows if str((r.get("owner") or {}).get("id")) == str(owner_id)]
    return sorted(rows, key=lambda r: r.get("created", 0))


def of_owner(kind: str, oid: str) -> dict | None:
    rows = listing(owner_kind=kind, owner_id=oid)
    return rows[-1] if rows else None


def set_owner(wid: str, kind: str, oid: str) -> dict:
    rec = get(wid)
    rec["owner"] = {"kind": kind, "id": str(oid), "pid": os.getpid()}
    _put(rec)
    return rec


def summary(rec: dict) -> dict:
    """What the app shows about a copy."""
    return {k: rec.get(k) for k in ("id", "kind", "mode", "source", "branch", "workdir", "base_dirty", "title",
                                    "created", "merged", "note")}


# ---------- create ---------------------------------------------------------------------------------------------------
def _after_add(rec: dict) -> None:
    """Test seam: runs after the copy exists and before the record says ``ready``."""


def create(source, *, owner_kind: str = "cli", owner_id: str = "", title: str = "", base: str = "head",
           mode: str | None = None, max_bytes: int = MAX_COPY_BYTES) -> dict:
    """Make an isolated copy of ``source``. ``base="head"``: from the folder's HEAD (uncommitted changes stay in the
    folder); ``base="worktree"``: from its working tree as it is now, unsaved edits included (sub-agents).
    Returns the record; work in ``rec["workdir"]``."""
    if owner_kind not in OWNER_KINDS:
        raise ValueError(f"owner_kind must be one of {OWNER_KINDS}")
    src = Path(source).expanduser()
    if not src.is_dir():
        raise WorktreeError(f"Folder not found: {source}")
    src = src.resolve()
    if _norm(src).startswith(_norm(root()) + os.sep) and toplevel(src) is None:
        raise WorktreeError("That folder is itself an isolated copy without git; work in it directly.")
    top = toplevel(src)
    if top:
        from meshpool_harness.core import tools as _TT
        if _TT.git_config_runs_programs(top):           # filters, an external diff or a pager would run its programs
            raise WorktreeError("This repository's own git settings run programs (filters, fsmonitor, an external "
                                "diff), so meshpool does not make isolated copies of it. Trust and review them first.")
    while True:                                     # an id whose branch name is free: clean-up may delete that branch
        wid = uuid.uuid4().hex[:8]
        if not top or _git(top, "rev-parse", "-q", "--verify", f"refs/heads/meshpool/{wid}").returncode:
            break
    rec = {"id": wid, "kind": "git" if top else "copy", "mode": mode or ("apply" if base == "worktree" else "commit"),
           "source": str(src), "top": top, "path": str(root() / wid), "workdir": "",
           "branch": f"meshpool/{wid}" if top else "",
           "base": "", "base_dirty": False, "owner": {"kind": owner_kind, "id": str(owner_id), "pid": os.getpid()},
           "title": str(title or "")[:120], "state": "creating", "created": time.time(), "merged": None}
    root().mkdir(parents=True, exist_ok=True)
    with _lock(top or str(src)):
        _put(rec)                                   # first: a crash from here on leaves a record recover() cleans up
        try:
            if top:
                _create_git(rec, top, src, base)
            else:
                _create_copy(rec, src, max_bytes)
            _after_add(rec)
            rec["state"] = "ready"
            _put(rec)
        except BaseException:
            _cleanup(rec)
            _drop(wid)
            raise
    _hook("WorktreeCreate", rec)
    return rec


def _hook(event: str, rec: dict) -> None:
    """The user's WorktreeCreate / WorktreeRemove hooks (hooks.json, and a trusted folder's own). Never raises."""
    try:
        from meshpool_harness.agent import extras as EX
        EX.run_hooks(Path(rec.get("source") or "."), event, {
            "worktree": {k: rec.get(k) for k in ("id", "kind", "workdir", "path", "branch", "source", "title")}},
            timeout=300)
    except Exception:  # noqa: BLE001 - a hook never breaks making or removing a copy
        pass


def _create_git(rec: dict, top: str, src: Path, base: str) -> None:
    head = _head(top)
    rec["head"] = head
    rec["base_dirty"] = bool(_dirty_paths(top))
    if base == "worktree":
        tree = _engine()._worktree_tree(top)
        if not tree:
            raise WorktreeError("Could not read the folder's current files (is a merge in progress?).")
        r = _git(top, "commit-tree", tree, "-p", head, "-m", "Meshpool: snapshot for an isolated copy",
                 env=_ident(top))
        if r.returncode:
            raise WorktreeError("Could not snapshot the folder: " + _err(r))
        commit = _out(r)
    else:
        commit = head
    r = _git(top, "worktree", "add", "-q", "-b", rec["branch"], rec["path"], commit)
    if r.returncode:
        raise WorktreeError("Could not make an isolated copy (git worktree): " + _err(r))
    rel = os.path.relpath(src, top)
    rec["base"] = commit
    rec["sub"] = "" if rel == "." else Path(rel).as_posix()
    rec["workdir"] = str(Path(rec["path"], rec["sub"])) if rec["sub"] else rec["path"]
    _init_submodules(rec)


def _init_submodules(rec: dict) -> None:
    """A repository with submodules: initialise them inside the new worktree (builds there need them). Never fatal:
    a failure (no network, a missing credential) leaves the copy usable and is told in ``rec["note"]``."""
    wt = rec["path"]
    if not Path(wt, ".gitmodules").is_file():
        return
    try:
        e = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        r = _sp.run(_git_cmd("submodule", "update", "--init", "--recursive"), cwd=wt, capture_output=True, env=e,
                    timeout=SUBMODULE_TIMEOUT)
        if not r.returncode:
            return
        why = _err(r)
    except _sp.TimeoutExpired:
        why = f"it took longer than {SUBMODULE_TIMEOUT} seconds"
    except (OSError, WorktreeError) as ex:
        why = str(ex)
    rec["note"] = ("The folder has git submodules that could not be set up in this copy (" + why + "). Builds in the "
                   f"copy may fail until you run `git submodule update --init --recursive` in {rec['path']}.")


def _walk(base) -> dict:
    """relative posix path -> absolute path of every regular file, skipping the folders checkpoints skip."""
    from meshpool_harness.checkpoints import SKIP_DIRS, SKIP_FILES
    out, b = {}, Path(base)
    for dp, dns, fns in os.walk(_long(b)):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not os.path.islink(os.path.join(dp, d))]
        for f in fns:
            full = os.path.join(dp, f)
            if f in SKIP_FILES or os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, _long(b)).replace(os.sep, "/")
            out[rel] = full
    return out


def _copy_files(files: dict, dst: Path) -> None:
    Path(_long(dst)).mkdir(parents=True, exist_ok=True)    # an empty folder still gets its (empty) copy
    for rel, full in files.items():
        t = Path(_long(dst / rel))
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full, t)


def _create_copy(rec: dict, src: Path, max_bytes: int = MAX_COPY_BYTES) -> None:
    from meshpool_harness.checkpoints import MAX_FILES
    files = _walk(src)
    if len(files) > MAX_FILES:
        raise WorktreeError(f"This folder has more than {MAX_FILES} files: too big for an isolated copy without git. "
                            "Make it a git repository (git init, then commit) to work in an isolated copy.")
    size = sum(os.path.getsize(f) for f in files.values())
    if size > max_bytes:
        raise WorktreeError(f"This folder is over {max_bytes // (1024 * 1024)} MB: too big for an isolated copy "
                            "without git.")
    p = Path(rec["path"])
    _copy_files(files, p / "work")
    _copy_files(files, p / "base")
    rec["workdir"] = str(p / "work")
    rec["base_dir"] = str(p / "base")
    # Where it came from, beside it: if the registry is ever damaged, the record can be rebuilt from the folder.
    fsio.atomic_write_json(p / SIDECAR, {k: rec.get(k) for k in ("id", "source", "title", "mode", "created")},
                           fsync=False)


def _cleanup(rec: dict) -> None:
    """Remove whatever a (failed or abandoned) copy left: worktree registration, branch, folder."""
    top = rec.get("top")
    if rec.get("kind") == "git" and top and Path(top).is_dir():
        if Path(rec["path"]).exists():
            _git(top, "worktree", "remove", "--force", "--force", rec["path"])
        _rmtree(rec["path"])
        _git(top, "worktree", "prune")
        if rec.get("branch"):
            _git(top, "branch", "-D", rec["branch"])
    else:
        _rmtree(rec["path"])


# ---------- diff -----------------------------------------------------------------------------------------------------
def _git_base(rec: dict) -> str:
    """What the copy's changes are measured against: for a commit-mode copy the merge base with the folder's HEAD
    (after a merge, only what is new since), for a sub-agent's copy the snapshot it started from."""
    if rec.get("mode") == "apply":
        return rec["base"]
    wt_head = _head(rec["path"])
    mb = _out(_git(rec["top"], "merge-base", _head(rec["top"]), wt_head)) if Path(rec["top"]).is_dir() else ""
    return mb or rec["base"]


def diff(wid: str, *, max_patch: int = 400_000) -> dict:
    """{files: [{path, status}], patch, stat}: what the copy changed and the folder does not have yet."""
    rec = get(wid)
    if not Path(rec["workdir"] or rec["path"]).exists():
        raise WorktreeError("The isolated copy's folder is gone.")
    if rec["kind"] == "git":
        tree = _engine()._worktree_tree(rec["path"])
        if not tree:
            raise WorktreeError("Could not read the isolated copy's files.")
        b = _git_base(rec)
        ns = _z(_git(rec["path"], "diff", "--name-status", "--no-renames", "-z", b, tree))
        files = [{"status": s[0], "path": p} for s, p in zip(ns[0::2], ns[1::2])]
        patch = _git(rec["path"], "diff", "--no-color", "--no-ext-diff", "--no-renames", b, tree).stdout
        stat_ = _out(_git(rec["path"], "diff", "--shortstat", b, tree))
        patch_s = patch.decode("utf-8", "replace")
    else:
        files, chunks = [], []
        for rel, st, old, new in _copy_changes(rec):
            files.append({"status": st, "path": rel})
            chunks.append(_text_diff(rel, old, new))
        patch_s = "".join(chunks)
        stat_ = f"{len(files)} file{'s' if len(files) != 1 else ''} changed" if files else ""
    if len(patch_s) > max_patch:
        patch_s = patch_s[:max_patch] + "\n[... diff cut here ...]\n"
    return {"id": rec["id"], "files": files, "patch": patch_s, "stat": stat_}


def _read(p) -> bytes | None:
    try:
        with open(_long(p), "rb") as f:
            return f.read()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _copy_changes(rec: dict):
    base, work = _walk(rec["base_dir"]), _walk(rec["workdir"])
    for rel in sorted(set(base) | set(work)):
        old = _read(base[rel]) if rel in base else None
        new = _read(work[rel]) if rel in work else None
        if old != new:
            yield rel, "A" if old is None else "D" if new is None else "M", old, new


def _text_diff(rel: str, old: bytes | None, new: bytes | None) -> str:
    try:
        a = (old or b"").decode("utf-8").splitlines(keepends=True)
        b = (new or b"").decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"Binary file {rel} differs\n"
    return "".join(difflib.unified_diff(a, b, "/dev/null" if old is None else f"a/{rel}",
                                        "/dev/null" if new is None else f"b/{rel}"))


# ---------- merge ----------------------------------------------------------------------------------------------------
def merge(wid: str, message: str = "") -> dict:
    """Bring the copy's changes into the folder it came from.

    Returns {ok: True, files, fast_forward?, commit?} or {ok: False, reason, conflicts|blocked}. With ok False the
    folder is exactly as it was. Commit-mode git copies are merged on the folder's branch (fast-forward, else a merge
    commit); sub-agent copies are applied to the working tree without a commit; plain copies file by file."""
    rec = get(wid)
    if rec.get("state") != "ready":
        raise WorktreeError("That isolated copy is not ready.")
    if not Path(rec["source"]).is_dir():
        raise WorktreeError(f"The folder this copy came from is gone: {rec['source']}")
    msg = message or f"Meshpool: {rec.get('title') or 'changes from an isolated copy'}"
    with _lock(rec.get("top") or rec["source"]):
        if rec["kind"] != "git":
            res = _merge_copy(rec)
        elif rec.get("mode") == "apply":
            res = _apply_git(rec)
        else:
            res = _merge_git(rec, msg)
        if res.get("ok") and res.get("files"):
            rec = get(wid)
            rec["merged"] = time.time()
            _put(rec)
    return res


def _merge_git(rec: dict, msg: str) -> dict:
    top, wt = rec["top"], rec["path"]
    _commit_pending(wt, msg)
    tip = _head(wt)
    doing = _in_progress(top)
    if doing:
        return {"ok": False, "reason": f"The folder is in the middle of {doing}. Finish or abort it first.",
                "conflicts": [], "blocked": []}
    if _git(top, "symbolic-ref", "-q", "HEAD").returncode:
        return {"ok": False, "reason": "The folder is not on a branch (detached HEAD). Check out a branch first.",
                "conflicts": [], "blocked": []}
    h = _head(top)
    if _ancestor(top, tip, h):
        return {"ok": True, "files": [], "note": "Nothing to merge: the folder already has these changes."}
    r = _git(top, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", h, tip)
    out = _z(r)
    if r.returncode == 1:
        return {"ok": False, "reason": "The changes conflict with the folder's own changes to the same lines.",
                "conflicts": sorted(set(out[1:])), "blocked": [], "branch": rec["branch"]}
    if r.returncode or not out:
        raise WorktreeError("Git could not compute the merge: " + _err(r))
    result = out[0]
    changed = set(_z(_git(top, "diff", "--name-only", "--no-renames", "-z", h, result)))
    blocked = sorted(changed & _dirty_paths(top))
    if blocked:
        return {"ok": False, "reason": "The folder has uncommitted changes to files this merge would change. Commit "
                "or put them aside first; nothing was changed.", "conflicts": [], "blocked": blocked}
    ff = _ancestor(top, h, tip)
    env = _ident(top)
    if ff:
        m = _git(top, "merge", "--ff-only", "-q", tip, env=env)
    else:
        m = _git(top, "merge", "--no-ff", "--no-verify", "--no-edit", "-q", "-m", msg, tip, env=env)
    if m.returncode:
        if _git(top, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
            _git(top, "merge", "--abort")
        return {"ok": False, "reason": "Git refused the merge: " + _err(m), "conflicts": [], "blocked": []}
    return {"ok": True, "files": sorted(changed), "fast_forward": ff, "commit": _head(top)}


def _apply_git(rec: dict) -> dict:
    """A sub-agent's copy: three-way merge of the folder's working tree, the snapshot the copy started from and the
    copy's working tree; the folder's files are moved to the result (only paths the copy changed), no commit."""
    E = _engine()
    top, wt, base = rec["top"], rec["path"], rec["base"]
    theirs = E._worktree_tree(wt)
    ours = E._worktree_tree(top)
    if not theirs or not ours:
        raise WorktreeError("Could not read the files to merge.")
    base_tree = _out(_git(top, "rev-parse", base + "^{tree}"))
    if theirs == base_tree:
        return {"ok": True, "files": [], "note": "The copy changed nothing."}
    env = _ident(top)
    oc = _out(_git(top, "commit-tree", ours, "-p", base, "-m", "ours", env=env))
    tc = _out(_git(top, "commit-tree", theirs, "-p", base, "-m", "theirs", env=env))
    r = _git(top, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", oc, tc)
    out = _z(r)
    if r.returncode == 1:
        return {"ok": False, "reason": "These changes conflict with changes already made to the same lines.",
                "conflicts": sorted(set(out[1:])), "blocked": [], "branch": rec["branch"]}
    if r.returncode or not out:
        raise WorktreeError("Git could not compute the merge: " + _err(r))
    result = out[0]
    if result == ours:
        return {"ok": True, "files": [], "note": "Nothing new to apply."}
    changed = sorted(_z(_git(top, "diff", "--name-only", "--no-renames", "-z", ours, result)))
    idx = _git(top, "write-tree")
    if idx.returncode:
        return {"ok": False, "reason": "The folder's index has unresolved conflicts.", "conflicts": [], "blocked": []}
    res = _move_paths(top, ours, result, changed)
    if res["changed_since"]:
        return {"ok": False, "reason": "Files these changes touch were changed in the folder while they were being "
                "merged; nothing was changed. Try again.", "conflicts": [], "blocked": res["changed_since"],
                "branch": rec["branch"]}
    if not res["ok"]:
        raise WorktreeError("Applying the changes to the folder failed part-way; check the folder.")
    return {"ok": True, "files": changed}


def _tree_entries(top, tree: str) -> dict | None:
    """path -> (mode, object id) of every file in ``tree``."""
    r = _git(top, "ls-tree", "-r", "-z", "--full-tree", tree)
    if r.returncode:
        return None
    out = {}
    for row in r.stdout.split(b"\0"):
        meta, sep, path = row.partition(b"\t")
        if sep:
            mode, _typ, oid = meta.split(b" ", 2)
            out[path.decode("utf-8", "surrogateescape")] = (mode.decode(), oid.decode())
    return out


def _move_paths(top: str, expect: str, target: str, paths: list[str]) -> dict:
    """Move only ``paths`` of the folder's working tree from tree ``expect`` to tree ``target``: nothing else is read
    back, reset or removed, and the index is left alone. (engine.restore forces the whole folder to a tree, which also
    reverted or deleted files the user wrote while the merge was being computed.) Every path is first checked against
    ``expect``; when one no longer matches, nothing is touched and it is listed in ``changed_since``. Paths are relative
    to the repository top ``top``. Returns {ok, moved, changed_since}."""
    E = _engine()
    paths = list(dict.fromkeys(paths))
    if not paths:
        return {"ok": True, "moved": [], "changed_since": []}
    want, was = _tree_entries(top, target), _tree_entries(top, expect)
    cur = E._worktree_tree(top)
    now = _tree_entries(top, cur) if cur else None
    if want is None or was is None or now is None:
        return {"ok": False, "moved": [], "changed_since": []}
    stale = sorted(p for p in paths if now.get(p) != was.get(p))
    if stale:
        return {"ok": False, "moved": [], "changed_since": stale}
    write = [p for p in paths if p in want]
    if write:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="mh-index-")
        os.close(fd)
        os.remove(tmp)
        try:
            env = {"GIT_INDEX_FILE": tmp}
            if _git(top, "read-tree", target, env=env).returncode:
                return {"ok": False, "moved": [], "changed_since": []}
            stdin = b"\0".join(p.encode("utf-8", "surrogateescape") for p in write) + b"\0"
            if _git(top, "checkout-index", "-f", "-z", "--stdin", env=env, stdin=stdin).returncode:
                return {"ok": False, "moved": [], "changed_since": []}
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    for p in paths:
        if p not in want:
            fp = Path(_long(Path(top, p)))
            if fp.is_symlink() or fp.is_file():
                fp.unlink()
    after_tree = E._worktree_tree(top)
    after = _tree_entries(top, after_tree) if after_tree else None
    ok = after is not None and all(after.get(p) == want.get(p) for p in paths)
    return {"ok": ok, "moved": paths, "changed_since": []}


def _merge_copy(rec: dict) -> dict:
    """Plain copies: a file the copy changed is written to the folder when the folder's file is still as it was when
    the copy was made (or already equals the copy); anything else is a conflict. All or nothing."""
    src = Path(rec["source"])
    plan, conflicts = [], []
    for rel, _st, old, new in _copy_changes(rec):
        cur = _read(src / rel)
        if cur == new:
            continue
        if cur == old:
            plan.append((rel, new))
        else:
            conflicts.append(rel)
    if conflicts:
        return {"ok": False, "reason": "The folder's copies of these files changed since the isolated copy was made.",
                "conflicts": sorted(conflicts), "blocked": []}
    staged = _stage_all(src, [(rel, new) for rel, new in plan if new is not None])
    # Every new file is on disk next to its target (a full disk failed above, with the folder untouched): now only
    # renames and deletions, which do not need space.
    for t, tmp, data in staged:
        try:
            linked = os.stat(t).st_nlink > 1
        except OSError:
            linked = False
        if linked:                                  # a hard-linked file stays one file: rewritten where it is
            fsio.atomic_write_bytes(t, data, fsync=False, keep_links=True)
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        else:
            fsio._replace(tmp, str(t))
    for rel, new in plan:
        if new is None:
            Path(_long(src / rel)).unlink(missing_ok=True)
    for rel, new in plan:                           # the copy's base is now the folder's state
        b = Path(rec["base_dir"]) / rel
        if new is None:
            Path(_long(b)).unlink(missing_ok=True)
        else:
            fsio.atomic_write_bytes(_long(b), new, fsync=False)
    return {"ok": True, "files": sorted(r for r, _ in plan)}


def _stage_all(src: Path, items: list) -> list:
    """Write each (rel, data) to a temporary file next to its target in ``src``, with the target's permission bits (a
    new file: open()'s default). Returns [(target, temp, data)]. On any failure every temporary file is removed and the
    error raised: nothing in the folder has changed."""
    import tempfile
    staged = []
    try:
        for rel, data in items:
            t = Path(_long(src / rel))
            if t.is_symlink():
                t = Path(os.path.realpath(t))       # the link stays a link; its target gets the new content
            t.parent.mkdir(parents=True, exist_ok=True)
            try:
                mode = os.stat(t).st_mode & 0o7777  # an executable stays executable
            except OSError:
                mode = fsio.default_mode()
            fd, tmp = tempfile.mkstemp(prefix=f".{t.name}.", suffix=".mh-merge", dir=str(t.parent))
            staged.append((t, tmp, data))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            if os.name != "nt":
                os.chmod(tmp, mode)
    except BaseException:
        for _t, tmp, _d in staged:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise
    return staged


# ---------- reset, discard, prune, recover ---------------------------------------------------------------------------
def reset(wid: str) -> dict:
    """Throw away the copy's changes and start it again from the folder as it is now (the copy stays)."""
    rec = get(wid)
    with _lock(rec.get("top") or rec["source"]):
        if rec["kind"] == "git":
            wt, top = rec["path"], rec["top"]
            target = _head(top) if rec.get("mode") != "apply" else rec["base"]
            for args in (("reset", "-q", "--hard", target), ("clean", "-q", "-fd")):
                r = _git(wt, *args)
                if r.returncode:
                    raise WorktreeError("Could not reset the isolated copy: " + _err(r))
            rec["base"] = target
        else:
            p = Path(rec["path"])
            _rmtree(p / "work")
            _rmtree(p / "base")
            files = _walk(rec["source"])
            _copy_files(files, p / "work")
            _copy_files(files, p / "base")
        rec["merged"] = None
        _put(rec)
    return {"ok": True}


def discard(wid: str, keep_work: bool = False) -> dict:
    """Delete the copy's changes and remove the copy. Its branch stays only when it holds commits the folder does not
    have (``kept_branch``, with a ``note`` saying so), so finished work is never lost silently. Uncommitted changes
    are deleted; ``keep_work=True`` (sub-agents, deleted chats) commits them first so they are kept as well."""
    rec = get(wid)
    kept = kept_dir = None
    with _lock(rec.get("top") or rec["source"]):
        top = rec.get("top")
        if rec["kind"] == "git" and top and Path(top).is_dir():
            if Path(rec["path"]).is_dir() and rec.get("state") == "ready":
                try:
                    if keep_work:
                        _commit_pending(rec["path"], f"Meshpool: unmerged work from {rec.get('title') or rec['id']}")
                    tip = _head(rec["path"])
                    if tip and tip != rec["base"] and not _ancestor(top, tip, _head(top)):
                        kept = rec["branch"]
                except WorktreeError:
                    pass
            if Path(rec["path"]).exists():
                _git(top, "worktree", "remove", "--force", "--force", rec["path"])
            _rmtree(rec["path"])
            _git(top, "worktree", "prune")
            if not kept and rec.get("branch"):
                _git(top, "branch", "-D", rec["branch"])
        else:
            if keep_work and rec["kind"] != "git" and rec.get("state") == "ready":
                try:
                    kept_dir = _keep_copy(rec)
                except OSError as e:                # could not move it: it stays where it is, record and all
                    work = rec.get("workdir") or rec["path"]
                    return {"ok": True, "kept_branch": None, "kept_dir": work,
                            "note": f"Its changes were kept in {work} (moving them aside failed: {e})."}
            if not kept_dir:
                _rmtree(rec["path"])
        _drop(rec["id"])
    _hook("WorktreeRemove", rec)
    if kept_dir:
        note = (f"Its changed files were kept in {kept_dir} (the base folder beside it holds the files as they were "
                "when the copy was made). Delete that folder when you do not need them.")
    elif kept:
        note = (f"Discarded. Its branch {kept} was kept because it has commits that are not in the folder; delete it "
                f"with `git branch -D {kept}` when you do not need them.")
    else:
        note = "Discarded: the copy and its changes are gone."
    return {"ok": True, "kept_branch": kept, "kept_dir": kept_dir, "note": note}


def kept_root() -> Path:
    """Where the work of plain copies that could not be merged is kept (outside wt/, so no clean-up touches it)."""
    from meshpool_harness import config as C
    return C.home() / "kept-copies"


def _keep_copy(rec: dict) -> str | None:
    """A plain copy whose work was not merged (a conflict, a stopped sub-agent): move the whole copy (work/ and base/)
    to kept-copies/<id> instead of deleting it. Returns the kept work folder, or None when the copy changed nothing."""
    try:
        changed = any(True for _ in _copy_changes(rec))
    except OSError:
        changed = True                              # cannot tell: keep it
    if not changed or not Path(rec["path"]).is_dir():
        return None
    kr = kept_root()
    kr.mkdir(parents=True, exist_ok=True)
    dst, n = kr / rec["id"], 1
    while dst.exists():
        dst, n = kr / f"{rec['id']}-{n}", n + 1
    shutil.move(_long(rec["path"]), _long(dst))
    return str(dst / "work")


# ---------- sub-agents: one copy each, applied in order (agent/delegate.py, assistant.run_subtasks) ---------------------
def subagent_copy(source, owner_id: str, title: str = "") -> dict | None:
    """An isolated copy of ``source`` (as it is now, unsaved edits included) for one editing sub-agent, or None when
    none can be made (no git, too big, disk full): the sub-agent then shares the folder."""
    try:
        return create(str(source), owner_kind="subagent", owner_id=owner_id, title=title, base="worktree",
                      max_bytes=50 * 1024 * 1024)       # a plain copy only of a small folder
    except Exception:  # noqa: BLE001
        return None


def apply_subagent_copy(wt: dict) -> dict:
    """Bring one sub-agent's copy into its folder and remove the copy. Call in task order. Conflicting changes are
    never applied: the result is ``{"ok": False, "reason", "bad": [paths]}`` and the work stays on its branch
    (``kept_branch``) or, for a plain copy, in a folder of its own (``kept_dir``)."""
    try:
        res = merge(wt["id"])
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "reason": str(e), "conflicts": []}
    try:
        d = discard(wt["id"], keep_work=not res.get("ok"))
        kept, kept_dir = d.get("kept_branch"), d.get("kept_dir")
    except Exception:  # noqa: BLE001
        kept, kept_dir = None, (None if res.get("ok") else wt.get("workdir"))   # not removed: it is still there
    return {"ok": bool(res.get("ok")), "reason": str(res.get("reason") or ""),
            "bad": list(res.get("conflicts") or res.get("blocked") or []), "kept_branch": kept, "kept_dir": kept_dir}


def prune_owner(kind: str, oid: str, keep_work: bool = True) -> list[dict]:
    """Remove every copy an owner holds (a deleted chat). Unmerged git work stays on its branch."""
    out = []
    for rec in listing(owner_kind=kind, owner_id=str(oid)):
        try:
            out.append(dict(discard(rec["id"], keep_work=keep_work), id=rec["id"]))
        except (WorktreeError, KeyError, OSError) as e:
            out.append({"ok": False, "id": rec["id"], "error": str(e)})
    return out


def _pid_alive(pid) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except ImportError:  # pragma: no cover
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    except (TypeError, ValueError):
        return False


def _default_alive(kind: str, oid: str, rec: dict) -> bool:
    if kind in ("subagent", "schedule"):            # in-process work: gone when its process is
        return _pid_alive((rec.get("owner") or {}).get("pid"))
    return True                                     # chats and terminal sessions keep their copy until told


def recover(alive=None) -> dict:
    """Start-up clean-up. Removes copies whose creation never finished, records whose folder is gone, copies whose
    owner ``alive(kind, id)`` says is gone (sub-agents and unclaimed scheduled runs: their process is gone), and
    folders under wt/ that no record knows. Copies of chats that still exist survive restarts."""
    done = {"creating": [], "missing": [], "dead_owner": [], "orphan_dirs": [], "kept_branches": []}
    if not root().is_dir():
        return done
    try:
        doc, clean = _load()
    except RegistryUnreadable as e:                 # decide nothing from a registry that could not be read
        done["error"] = str(e)
        return done
    for rec in list(doc.get("items", {}).values()):
        wid, owner = rec["id"], rec.get("owner") or {}
        try:
            if rec.get("state") != "ready":
                with _lock(rec.get("top") or rec["source"]):    # a creator still at work holds this lock
                    cur = _items().get(wid)
                    if cur and cur.get("state") != "ready":
                        _cleanup(cur)
                        _drop(wid)
                        done["creating"].append(wid)
                continue
            if not Path(rec["path"]).exists():
                with _lock(rec.get("top") or rec["source"]):
                    top = rec.get("top")
                    if rec["kind"] == "git" and top and Path(top).is_dir():
                        _git(top, "worktree", "prune")
                        tip = _out(_git(top, "rev-parse", "-q", "--verify", rec["branch"]))
                        if tip and (tip == rec["base"] or _ancestor(top, tip, _head(top))):
                            _git(top, "branch", "-D", rec["branch"])
                        elif tip:
                            done["kept_branches"].append(rec["branch"])   # its commits are not in the folder yet
                    _drop(wid)
                done["missing"].append(wid)
                continue
            kind, oid = owner.get("kind", ""), str(owner.get("id", ""))
            ok = _default_alive(kind, oid, rec) if alive is None else alive(kind, oid, rec)
            if not ok:
                r = discard(wid, keep_work=True)
                done["dead_owner"].append(wid)
                if r.get("kept_branch"):
                    done["kept_branches"].append(r["kept_branch"])
        except (WorktreeError, OSError, KeyError, TimeoutError):
            continue
    # Orphan folders are deleted only on the word of a registry read cleanly in this run, now and just before each
    # deletion: an unreadable or rebuilt registry knows too little, and the folders may hold unmerged work.
    try:
        known, clean_now = set(_load()[0].get("items", {})), True
    except RegistryUnreadable:
        known, clean_now = set(), False
    if not (clean and clean_now):
        return done
    for d in root().iterdir():
        if d.name in ("locks",) or not d.is_dir() or d.name in known:
            continue
        try:
            doc_now, ok_now = _load()
        except RegistryUnreadable:
            break
        if not ok_now or time.time() - d.stat().st_mtime < 120 or d.name in doc_now.get("items", {}):
            continue                                # maybe being made right now
        gitfile = d / ".git"
        common = None
        if gitfile.is_file():
            try:
                g = gitfile.read_text(encoding="utf-8").strip()
                if g.startswith("gitdir:"):
                    common = Path(g.split(":", 1)[1].strip()).parent.parent   # <repo>/.git/worktrees/<name>
            except OSError:
                pass
        _rmtree(d)
        if common and common.is_dir():
            _sp.run(_git_cmd("--git-dir", str(common), "worktree", "prune"), capture_output=True)
        done["orphan_dirs"].append(d.name)
    return done


# ---------- terminal: meshpool worktree list|diff|merge|discard ------------------------------------------------------
USAGE = """usage: meshpool worktree list [--all]
       meshpool worktree diff <id> [--stat]
       meshpool worktree merge <id>
       meshpool worktree discard <id>   (deletes the copy's changes AND removes the copy; its git branch is kept
                                        only if it has commits the folder does not)

Isolated copies (git worktrees, or plain copies for folders without git) made by `meshpool --worktree`, chats,
sub-agents and scheduled runs. `list` shows the copies of the current folder's repository (--all: every copy)."""


def _changes_count(rec: dict) -> str:
    try:
        return str(len(diff(rec["id"], max_patch=0)["files"]))
    except (WorktreeError, KeyError, OSError):
        return "?"


def cli(argv: list[str], out=None) -> int:
    out = out or sys.stdout
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, file=out)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "list":
            here = os.getcwd()
            rows = listing() if "--all" in rest else listing(source=toplevel(here) or here)
            if not rows:
                print("No isolated copies" + ("" if "--all" in rest else " for this folder (--all shows every one)")
                      + ".", file=out)
                return 0
            for r in rows:
                o = r.get("owner") or {}
                print(f"{r['id']}  {r['kind']:4}  {o.get('kind', ''):8} {_changes_count(r):>3} changed  "
                      f"{r.get('branch') or '-':18} {r['source']}  {r.get('title') or ''}".rstrip(), file=out)
            return 0
        if cmd in ("diff", "merge", "discard") and not rest:
            print(USAGE, file=out)
            return 2
        if cmd == "diff":
            d = diff(rest[0])
            if "--stat" in rest or not d["patch"]:
                for f in d["files"]:
                    print(f"{f['status']}  {f['path']}", file=out)
                print(d["stat"] or "No changes.", file=out)
            else:
                out.write(d["patch"])
            return 0
        if cmd == "merge":
            r = merge(rest[0])
            if r["ok"]:
                print(r.get("note") or f"Merged {len(r['files'])} file(s)"
                      + (" (fast-forward)." if r.get("fast_forward") else "."), file=out)
                return 0
            print(r["reason"], file=out)
            for p in r.get("conflicts") or []:
                print(f"  conflict: {p}", file=out)
            for p in r.get("blocked") or []:
                print(f"  uncommitted: {p}", file=out)
            return 1
        if cmd == "discard":
            r = discard(rest[0])
            print(r["note"], file=out)
            return 0
    except (WorktreeError, KeyError) as e:
        print(f"error: {e.args[0] if e.args else e}", file=out)
        return 1
    print(USAGE, file=out)
    return 2
