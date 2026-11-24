"""Git worktrees for chats: each task can work in its own checkout on its own branch, so parallel chats never edit the
same files, and the main checkout stays as it is.

  create(repo, name)   <repo>/.meshpool/worktrees/<name> on a new branch meshpool/<name>, from HEAD (or base). The
                       folder is added to .git/info/exclude (local, never committed). Files listed in a
                       .worktreeinclude at the repo root (ignored files such as .env) are copied in.
  listing(repo)        the worktrees Meshpool made, with branch, uncommitted changes and unpushed commits.
  remove(path)         refuses while the worktree has uncommitted changes or commits that are on no other branch
                       or remote, unless force=True; the branch is deleted only when nothing would be lost.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
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


def create(path, name: str = "", base: str = "") -> dict:
    root = repo_root(path)
    name = (name or "").strip() or _next_name(root)
    if not _NAME.match(name):
        raise WorktreeError("A worktree name uses letters, digits, dots, dashes and underscores.")
    dest = root / DIR / name
    if dest.exists():
        raise WorktreeError(f"A worktree named {name} already exists.")
    branch = f"meshpool/{name}"
    if _git(["rev-parse", "--verify", "--quiet", "refs/heads/" + branch], root, check=False).returncode == 0:
        raise WorktreeError(f"The branch {branch} already exists. Pick another name.")
    _exclude(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-b", branch, str(dest), base or "HEAD"], root, timeout=300)
    copied = _include(root, dest)
    return {"name": name, "path": str(dest), "branch": branch, "repo": str(root), "copied": copied}


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
    _git(["worktree", "remove", "--force", str(p)] if force else ["worktree", "remove", str(p)], root, timeout=120)
    gone = False
    if item.get("branch"):                               # -d refuses a branch whose commits would be lost
        gone = _git(["branch", "-d", item["branch"]], root, check=False).returncode == 0
    return {"removed": item["name"], "branch_deleted": gone, "branch": item.get("branch")}
