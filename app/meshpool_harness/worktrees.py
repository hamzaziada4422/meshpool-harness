"""Git worktrees for chats: each task can work in its own checkout on its own branch, so parallel chats never edit the
same files, and the main checkout stays as it is.

  create(repo, name)   <repo>/.meshpool/worktrees/<name> on a new branch meshpool/<name>, from HEAD (or base). The
                       folder is added to .git/info/exclude (local, never committed). Files listed in a
                       .worktreeinclude at the repo root (ignored files such as .env) are copied in.
  listing(repo)        the worktrees Meshpool made, with branch, uncommitted changes and unpushed commits.
  prune(repo, keep)    removes the oldest clean, fully pushed worktrees beyond the newest `keep`.
  owner(path)          (repo, name) when path is inside one of Meshpool's worktrees, else None.
  hold(path, obj)      marks a worktree in use while a chat or sub-agent works in it; prune skips it (in_use).

  Options of create: base "HEAD" (default), a branch or commit, or "remote" (the remote's default branch, fetched
  first); sparse: folders to check out (a sparse checkout for big monorepos). Each new worktree is locked
  (git worktree lock) so git worktree prune never drops it while Meshpool uses it; remove unlocks it.
  remove(path)         refuses while the worktree has uncommitted changes or commits that are on no other branch
                       or remote, unless force=True; the branch is deleted only when nothing would be lost.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import threading
import time
import weakref
from pathlib import Path

DIR = Path(".meshpool") / "worktrees"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")


class WorktreeError(ValueError):
    pass


def _git(args: list, cwd, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    if not Path(cwd).is_dir():
        raise WorktreeError(f"Folder not found: {cwd}")
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise WorktreeError(f"git could not run: {e}")
    if check and r.returncode:
        raise WorktreeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                            else f"git {' '.join(args)} failed")
    return r


def repo_root(path) -> Path:
    """The main checkout of the repository that holds path (also from inside one of its worktrees)."""
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path, check=False)
    if r.returncode:
        raise WorktreeError("Not a git repository.")
    common = Path(r.stdout.strip())
    return common.parent if common.name == ".git" else Path(_git(["rev-parse", "--show-toplevel"], path).stdout.strip())


def _exclude(root: Path) -> None:
    """.meshpool/worktrees/ in .git/info/exclude, so the checkouts never show up as changes in the main one."""
    ex = root / ".git" / "info" / "exclude"
    try:
        have = ex.read_text(encoding="utf-8") if ex.exists() else ""
        if "/.meshpool/worktrees/" not in have:
            ex.parent.mkdir(parents=True, exist_ok=True)
            ex.write_text(have + ("" if have.endswith("\n") or not have else "\n") + "/.meshpool/worktrees/\n",
                          encoding="utf-8")
    except OSError:
        pass


def _include(root: Path, dest: Path) -> list[str]:
    """Copies the files a .worktreeinclude lists (glob patterns, one per line) into the new worktree."""
    f = root / ".worktreeinclude"
    if not f.is_file():
        return []
    pats = [ln.strip() for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    copied = []
    for cur, dirs, files in os.walk(root):
        rel_dir = Path(cur).relative_to(root)
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules") and not (rel_dir / d).as_posix().startswith(DIR.as_posix())]
        for n in files:
            rel = (rel_dir / n).as_posix()
            if any(fnmatch.fnmatch(rel, p.lstrip("/")) or fnmatch.fnmatch(n, p) for p in pats):
                target = dest / rel
                if target.exists():
                    continue
                if (root / rel).is_symlink():          # never follow a link out of the repo
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / rel, target)
                    copied.append(rel)
                except OSError:
                    continue
    return copied


def _base_ref(root: Path, base: str) -> str:
    """HEAD, a ref the user named, or "remote": the default branch of origin, freshly fetched."""
    base = (base or os.environ.get("MH_WORKTREE_BASE", "") or "HEAD").strip()
    if base.lower() != "remote":
        if base != "HEAD" and _git(["rev-parse", "--verify", "--quiet", base + "^{commit}"], root, check=False).returncode:
            raise WorktreeError(f"No branch or commit named {base}.")
        return base
    _git(["-c", "core.hooksPath=/dev/null", "fetch", "--quiet", "origin"], root, timeout=180)
    head = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], root, check=False).stdout.strip()
    if not head:
        for cand in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
            if _git(["rev-parse", "--verify", "--quiet", cand], root, check=False).returncode == 0:
                head = cand
                break
    if not head:
        raise WorktreeError("Could not find the remote's default branch (origin/HEAD).")
    return head.replace("refs/remotes/", "")


def owner(path) -> tuple[Path, str] | None:
    """(main checkout, worktree name) when path is in one of Meshpool's worktrees."""
    p = Path(path).expanduser().resolve()
    for parent in (p, *p.parents):
        if parent.parent.name == "worktrees" and parent.parent.parent.name == ".meshpool":
            return parent.parent.parent.parent, parent.name
    return None


# Worktrees in use: a chat or sub-agent working in one holds it (hold, from Agent), so prune never removes it under
# them. In this process a count per worktree; for other processes a marker file per process id in the worktree's own
# git folder (.git/worktrees/<name>/meshpool-in-use-<pid>), honoured while that process is alive.
_HELD: dict = {}
_HELD_LOCK = threading.RLock()                  # reentrant: a GC finalizer may release while held
_MARK = "meshpool-in-use-"


def _top(path) -> Path | None:
    own = owner(path)
    return (own[0] / DIR / own[1]).resolve() if own else None


def _admin(top: Path) -> Path | None:
    """The worktree's own folder inside the main repository's .git (from its .git file), or None."""
    try:
        line = (top / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    p = Path(line[7:].strip())
    p = p if p.is_absolute() else (top / p)
    return p.resolve() if p.is_dir() else None


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":                                   # os.kill on Windows would end the process
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)         # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259         # STILL_ACTIVE
        except Exception:  # noqa: BLE001
            return True                                   # unsure: keep the worktree
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                                       # another user's live process
    except OSError:
        return False
    return True


def hold(path, obj=None) -> bool:
    """Marks the worktree that holds path as in use by this process, until release(path), or until obj (a chat's
    agent) is gone. False when path is not in one of Meshpool's worktrees."""
    top = _top(path)
    if top is None:
        return False
    key, adm = str(top), _admin(top)
    with _HELD_LOCK:                                      # count and marker change together (see release)
        _HELD[key] = _HELD.get(key, 0) + 1
        if adm is not None:
            try:
                (adm / f"{_MARK}{os.getpid()}").write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
    if obj is not None:
        weakref.finalize(obj, release, key)
    return True


def release(path) -> None:
    top = _top(path)
    if top is None:
        return
    key, adm = str(top), _admin(top)
    with _HELD_LOCK:                                      # a hold racing this release keeps its marker
        n = _HELD.get(key, 0) - 1
        if n > 0:
            _HELD[key] = n
            return
        _HELD.pop(key, None)
        if adm is not None:
            try:
                (adm / f"{_MARK}{os.getpid()}").unlink()
            except OSError:
                pass


def in_use(path) -> bool:
    """True while a chat or sub-agent, in this process or another live one, works in this worktree."""
    top = _top(path)
    if top is None:
        return False
    adm = _admin(top)
    with _HELD_LOCK:                                      # this process's marker is only removed with no holder
        if _HELD.get(str(top), 0) > 0:
            return True
        if adm is None:
            return False
        busy = False
        try:
            marks = list(adm.glob(_MARK + "*"))
        except OSError:
            return False
        for m in marks:
            try:
                pid = int(m.name[len(_MARK):])
            except ValueError:
                continue
            if pid != os.getpid() and _alive(pid):
                busy = True
            else:                                         # a process that ended, or this one with no holder left
                try:
                    m.unlink()
                except OSError:
                    pass
        return busy


def prune(path, keep: int = 5) -> list[str]:
    """Removes the oldest of Meshpool's worktrees beyond the newest `keep`, only those with no uncommitted changes
    and no unpushed commits (remove refuses the rest), and never one a chat or sub-agent still works in (in_use).
    Returns the names removed."""
    root = repo_root(path)
    rows = [w for w in listing(root) if not w.get("missing")]
    rows.sort(key=lambda w: Path(w["path"]).stat().st_mtime if Path(w["path"]).exists() else 0, reverse=True)
    gone = []
    for w in rows[max(0, int(keep)):]:
        if w.get("dirty") or w.get("unpushed") or in_use(w["path"]):
            continue
        try:
            remove(w["path"])
            gone.append(w["name"])
        except WorktreeError:
            continue
    return gone


def create(path, name: str = "", base: str = "", sparse: list | None = None) -> dict:
    root = repo_root(path)
    from meshpool_harness.core import tools as T
    if not T.git_config_safe(root):
        raise WorktreeError("This repository's own git settings or hooks run programs, so Meshpool does not make "
                            "worktrees in it. Use git worktree yourself if you trust it.")
    name = (name or "").strip() or _next_name(root)
    if not _NAME.match(name):
        raise WorktreeError("A worktree name uses letters, digits, dots, dashes and underscores.")
    dest = root / DIR / name
    if dest.exists():
        raise WorktreeError(f"A worktree named {name} already exists.")
    branch = f"meshpool/{name}"
    if _git(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch], root, check=False).returncode == 0:
        raise WorktreeError(f"The branch {branch} already exists. Pick another name.")
    ref = _base_ref(root, base)
    sparse = [s.strip().strip("/") for s in (sparse or []) if s and s.strip().strip("/")]
    if any(".." in Path(s).parts or Path(s).is_absolute() for s in sparse):
        raise WorktreeError("Sparse folders are paths inside the repository.")
    _exclude(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    nohooks = ["-c", "core.hooksPath=/dev/null"]
    _git(nohooks + ["worktree", "add", "--lock", "--reason", "Meshpool task", "-b", branch]
         + (["--no-checkout"] if sparse else []) + [str(dest), ref], root, timeout=300)
    if sparse:                                            # only these folders on disk: fast in a big monorepo
        _git(nohooks + ["sparse-checkout", "set", "--cone", *sparse], dest, timeout=120)
        _git(nohooks + ["checkout"], dest, timeout=300)
    copied = _include(root, dest)
    keep = os.environ.get("MH_WORKTREE_KEEP", "")
    pruned = prune(root, int(keep)) if keep.isdigit() and int(keep) > 0 else []
    _hook(root, "WorktreeCreate", {"worktree_name": name, "worktree_path": str(dest), "branch": branch, "base": ref})
    return {"name": name, "path": str(dest), "branch": branch, "repo": str(root), "copied": copied, "base": ref,
            "sparse": sparse, "pruned": pruned}


def _next_name(root: Path) -> str:
    n = 1
    while (root / DIR / f"task-{n}").exists():
        n += 1
    return f"task-{n}"


def _state(path: Path, branch: str) -> dict:
    dirty = bool(_git(["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "status", "--porcelain"], path,
                      check=False).stdout.strip())
    # commits on this branch that no other branch or remote has: what removing it would lose
    r = _git(["rev-list", "--count", branch, "--not", "--exclude=" + branch, "--branches", "--remotes"],
             path, check=False)                       # --exclude for --branches takes the name without refs/heads/
    lone = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
    return {"dirty": dirty, "unpushed": lone}


def _hook(root: Path, event: str, payload: dict) -> None:
    """WorktreeCreate and WorktreeRemove hooks (yours, and the project's once the folder is trusted). A hook that
    fails never undoes the worktree."""
    try:
        from meshpool_harness.agent import extras as EX
        EX.run_hooks(root, event, payload, timeout=300)
    except Exception:  # noqa: BLE001
        pass


def carry(src, dest) -> str:
    """Moves the uncommitted changes (new files too) of one checkout to another checkout of the same repository,
    through git stash, which all worktrees share. Returns "" when done or nothing to move; otherwise why it stopped.
    When the changes do not apply cleanly they stay safe in the stash (git stash list)."""
    import uuid
    src, dest = Path(src), Path(dest)
    if not _git(["status", "--porcelain", "--", "."], src).stdout.strip():
        return ""
    if _git(["status", "--porcelain"], dest).stdout.strip():
        return "the destination has uncommitted changes of its own; commit or stash them first"
    nohooks = ["-c", "core.hooksPath=/dev/null"]
    tag = "meshpool handoff " + uuid.uuid4().hex[:12]
    before = _git(["rev-parse", "-q", "--verify", "refs/stash"], src, check=False).stdout.strip()
    r = _git(nohooks + ["stash", "push", "--include-untracked", "-m", tag, "--", "."], src, check=False)
    after = _git(["rev-parse", "-q", "--verify", "refs/stash"], src, check=False).stdout.strip()
    if r.returncode or not after or after == before:  # nothing was set aside (a submodule change, say)
        return "git could not set these changes aside " + ((r.stderr or r.stdout).strip()[:200] or "(nothing to stash)")
    ref = next((ln.split(" ", 1)[0] for ln in _git(["stash", "list", "--format=%gd %s"], src).stdout.splitlines()
                if ln.endswith(tag)), "")
    if not ref:
        return "git set the changes aside but they cannot be found; they are in git stash list as " + tag
    r = _git(nohooks + ["stash", "apply", ref], dest, check=False)
    if r.returncode:                                  # back as they were: the clean destination reset, the changes home
        _git(nohooks + ["reset", "-q", "--hard"], dest, check=False)
        _git(["clean", "-q", "-fd"], dest, check=False)
        back = _git(nohooks + ["stash", "pop", ref], src, check=False)
        return ("they do not apply cleanly there" + ("" if back.returncode == 0 else
                f"; they are kept in git's stash as {tag} (git stash list)"))
    _git(["stash", "drop", ref], src, check=False)
    return ""


def listing(path) -> list[dict]:
    root = repo_root(path)
    out, cur = [], {}
    for line in _git(["worktree", "list", "--porcelain"], root).stdout.splitlines() + [""]:
        if not line:
            if cur.get("worktree"):
                p = Path(cur["worktree"])
                try:
                    p.relative_to(root / DIR)
                except ValueError:
                    cur = {}
                    continue
                br = cur.get("branch", "").replace("refs/heads/", "")
                item = {"name": p.name, "path": str(p), "branch": br, "locked": "locked" in cur}
                if p.exists():
                    item.update(_state(p, br or "HEAD"))
                else:
                    item.update(dirty=False, unpushed=0, missing=True)
                out.append(item)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v or True
    return out


def remove(path, force: bool = False) -> dict:
    p = Path(path).expanduser().resolve()
    root = repo_root(p)
    try:
        p.relative_to(root / DIR)
    except ValueError:
        raise WorktreeError("Only worktrees Meshpool made (under .meshpool/worktrees) can be removed here.")
    item = next((w for w in listing(root) if Path(w["path"]).resolve() == p), None)
    if not item:
        raise WorktreeError("No such worktree.")
    if not force and (item.get("dirty") or item.get("unpushed")):
        why = []
        if item.get("dirty"):
            why.append("uncommitted changes")
        if item.get("unpushed"):
            why.append(f"{item['unpushed']} commit{'s' if item['unpushed'] != 1 else ''} that no other branch or remote has")
        raise WorktreeError(f"{item['name']} has {' and '.join(why)}. Commit and push them first, or remove it "
                            "anyway: uncommitted changes are lost, commits stay on the branch.")
    _git(["worktree", "unlock", str(p)], root, check=False)     # Meshpool's own lock, from create
    _git(["worktree", "remove", "--force", str(p)] if force else ["worktree", "remove", str(p)], root, timeout=120)
    gone = False
    if item.get("branch"):                               # -d refuses a branch whose commits would be lost
        gone = _git(["branch", "-d", item["branch"]], root, check=False).returncode == 0
    _hook(root, "WorktreeRemove", {"worktree_name": item["name"], "worktree_path": str(p),
                                   "branch": item.get("branch"), "branch_deleted": gone})
    return {"removed": item["name"], "branch_deleted": gone, "branch": item.get("branch")}
