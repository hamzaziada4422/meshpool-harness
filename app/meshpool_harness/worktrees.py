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
OWNER_KINDS = ("chat", "subagent", "schedule", "cli")
IDENT = {"GIT_AUTHOR_NAME": "Meshpool", "GIT_AUTHOR_EMAIL": "meshpool@localhost",
         "GIT_COMMITTER_NAME": "Meshpool", "GIT_COMMITTER_EMAIL": "meshpool@localhost"}


class WorktreeError(Exception):
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
        func(path)
    except FileNotFoundError:
        pass


def _rmtree(p) -> None:
    if not os.path.lexists(_long(p)):
        return
    if os.path.islink(p) or os.path.isfile(_long(p)):
        os.unlink(_long(p))
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(_long(p), onexc=_rm_readonly)
    else:  # pragma: no cover
        shutil.rmtree(_long(p), onerror=_rm_readonly)


# ---------- git ------------------------------------------------------------------------------------------------------
def _git_cmd(*args: str) -> list:
    pre = ["git", "-c", "core.quotepath=false"]
    if _WINDOWS:
        pre += ["-c", "core.longpaths=true"]       # deep node_modules / src trees past MAX_PATH
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
def _items() -> dict:
    try:
        d = fsio.read_json(_registry(), default={}) or {}
    except fsio.CorruptFile:
        fsio.quarantine(_registry())
        d = {}
    return d.get("items", {}) if isinstance(d, dict) else {}


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
    try:
        fsio.update_json(_registry(), fn, default={"version": 1, "items": {}})
    except fsio.CorruptFile:
        fsio.quarantine(_registry())
        fsio.update_json(_registry(), fn, default={"version": 1, "items": {}})


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
                                    "created", "merged")}


# ---------- create ---------------------------------------------------------------------------------------------------
def _after_add(rec: dict) -> None:
    """Test seam: runs after the copy exists and before the record says ``ready``."""


def create(source, *, owner_kind: str = "cli", owner_id: str = "", title: str = "", base: str = "head",
           mode: str | None = None) -> dict:
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
                _create_copy(rec, src)
            _after_add(rec)
            rec["state"] = "ready"
            _put(rec)
        except BaseException:
            _cleanup(rec)
            _drop(wid)
            raise
    return rec


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
    for rel, full in files.items():
        t = Path(_long(dst / rel))
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full, t)


def _create_copy(rec: dict, src: Path) -> None:
    from meshpool_harness.checkpoints import MAX_FILES
    files = _walk(src)
    if len(files) > MAX_FILES:
        raise WorktreeError(f"This folder has more than {MAX_FILES} files: too big for an isolated copy without git. "
                            "Make it a git repository (git init, then commit) to work in an isolated copy.")
    size = sum(os.path.getsize(f) for f in files.values())
    if size > MAX_COPY_BYTES:
        raise WorktreeError("This folder is over 500 MB: too big for an isolated copy without git.")
    p = Path(rec["path"])
    _copy_files(files, p / "work")
    _copy_files(files, p / "base")
    rec["workdir"] = str(p / "work")
    rec["base_dir"] = str(p / "base")


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
    if not E.restore(top, (result, _out(idx))):
        raise WorktreeError("Applying the changes to the folder failed part-way; check the folder.")
    return {"ok": True, "files": changed}


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
    for rel, new in plan:
        t = src / rel
        if new is None:
            Path(_long(t)).unlink(missing_ok=True)
        else:
            Path(_long(t)).parent.mkdir(parents=True, exist_ok=True)
            fsio.atomic_write_bytes(_long(t), new, fsync=False)
    for rel, new in plan:                           # the copy's base is now the folder's state
        b = Path(rec["base_dir"]) / rel
        if new is None:
            Path(_long(b)).unlink(missing_ok=True)
        else:
            Path(_long(b)).parent.mkdir(parents=True, exist_ok=True)
            Path(_long(b)).write_bytes(new)
    return {"ok": True, "files": sorted(r for r, _ in plan)}


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
    """Remove the copy. keep_work=True first commits its changes on its branch and keeps the branch when it holds
    anything the folder does not have (``kept_branch``), so nothing unmerged is lost silently."""
    rec = get(wid)
    kept = None
    with _lock(rec.get("top") or rec["source"]):
        top = rec.get("top")
        if rec["kind"] == "git" and top and Path(top).is_dir():
            if keep_work and Path(rec["path"]).is_dir() and rec.get("state") == "ready":
                try:
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
            _rmtree(rec["path"])
        _drop(rec["id"])
    return {"ok": True, "kept_branch": kept}


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
    for rec in list(_items().values()):
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
    known = set(_items())
    for d in root().iterdir():
        if d.name in ("locks",) or not d.is_dir() or d.name in known:
            continue
        if time.time() - d.stat().st_mtime < 120 or d.name in set(_items()):   # maybe being made right now
            continue
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
       meshpool worktree discard <id>

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
            print("Discarded.", file=out)
            return 0
    except (WorktreeError, KeyError) as e:
        print(f"error: {e.args[0] if e.args else e}", file=out)
        return 1
    print(USAGE, file=out)
    return 2
