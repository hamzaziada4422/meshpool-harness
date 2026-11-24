"""Git worktrees for tasks (worktrees.py): create with .worktreeinclude, list with state, and a remove that never
loses commits or changes without force."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from meshpool_harness import worktrees as WT


def _git(*a, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git("init", "-q", cwd=r)
    (r / "a.txt").write_text("a")
    (r / ".gitignore").write_text(".env\n")
    (r / ".env").write_text("TOKEN=local")
    (r / ".worktreeinclude").write_text("# copied into new worktrees\n.env\n")
    _git("add", "-A", cwd=r)
    _git("commit", "-qm", "init", cwd=r)
    return r


def test_create_copies_included_files_and_keeps_the_main_checkout_clean(repo):
    w = WT.create(repo, "fix-1")
    assert w["branch"] == "meshpool/fix-1" and w["copied"] == [".env"]
    assert (repo / ".meshpool" / "worktrees" / "fix-1" / ".env").read_text() == "TOKEN=local"
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""
    assert [x["name"] for x in WT.listing(repo)] == ["fix-1"]
    assert WT.repo_root(w["path"]) == repo                    # from inside the worktree too
    with pytest.raises(WT.WorktreeError):
        WT.create(repo, "fix-1")
    with pytest.raises(WT.WorktreeError):
        WT.create(repo, "../escape")
    assert WT.create(repo)["name"] == "task-1"


def test_remove_refuses_to_lose_work_and_force_keeps_the_branch(repo):
    w = WT.create(repo, "fix-2")
    p = repo / ".meshpool" / "worktrees" / "fix-2"
    (p / "b.txt").write_text("b")
    with pytest.raises(WT.WorktreeError, match="uncommitted"):
        WT.remove(p)
    _git("add", "-A", cwd=p)
    _git("commit", "-qm", "b", cwd=p)
    assert WT.listing(repo)[0]["unpushed"] == 1
    with pytest.raises(WT.WorktreeError, match="no other branch"):
        WT.remove(p)
    r = WT.remove(p, force=True)
    assert not p.exists() and r["branch_deleted"] is False        # the commit is still on meshpool/fix-2
    assert "meshpool/fix-2" in subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    w3 = WT.create(repo, "clean")
    assert WT.remove(w3["path"])["branch_deleted"] is True       # nothing to lose: branch goes too
    with pytest.raises(WT.WorktreeError):
        WT.remove(repo)                                          # never the main checkout


def test_cli_and_api(repo, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)
    from meshpool_harness import cli
    assert cli.main(["worktree", "add", "cli-1"]) == 0
    assert cli.main(["worktree", "list"]) == 0 and "cli-1" in capsys.readouterr().out
    assert cli.main(["worktree", "remove", "cli-1"]) == 0
    from meshpool_harness.ui import features_api as F
    st, out = F.handle("POST", urlparse("/api/worktree"), json.dumps({"root": str(repo), "name": "api-1"}).encode())
    assert st == 200 and out["branch"] == "meshpool/api-1"
    st, out = F.handle("GET", urlparse("/api/worktree?root=" + str(repo)))
    assert [w["name"] for w in out["worktrees"]] == ["api-1"]
    st, out = F.handle("POST", urlparse("/api/worktree"), json.dumps(
        {"root": str(repo), "action": "remove", "path": str(repo / ".meshpool" / "worktrees" / "api-1")}).encode())
    assert out["removed"] == "api-1"


def test_the_app_routes_reach_the_worktree_api(repo):
    """/api/work is a prefix of /api/worktree: the worktree route must win (a bug the browser test caught)."""
    from meshpool_harness.ui import routes_extra as R
    st, out = R.handle("GET", urlparse("/api/worktree?root=" + str(repo)))
    assert st == 200 and out == {"worktrees": []}


def test_base_ref_sparse_lock_and_owner(repo):
    (repo / "pkg").mkdir()
    (repo / "pkg" / "x.py").write_text("x")
    (repo / "docs").mkdir()
    (repo / "docs" / "d.md").write_text("d")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "two", cwd=repo)
    first = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=repo, capture_output=True,
                           text=True).stdout.strip()
    w = WT.create(repo, "old", base=first)
    assert w["base"] == first and not (Path(w["path"]) / "pkg").exists()
    with pytest.raises(WT.WorktreeError, match="No branch"):
        WT.create(repo, "bad", base="nope")
    s = WT.create(repo, "slim", sparse=["pkg"])
    assert (Path(s["path"]) / "pkg" / "x.py").exists() and not (Path(s["path"]) / "docs").exists()
    assert next(x for x in WT.listing(repo) if x["name"] == "slim")["locked"] is True
    assert WT.owner(Path(s["path"]) / "pkg") == (repo, "slim") and WT.owner(repo) is None
    with pytest.raises(WT.WorktreeError):
        WT.create(repo, "esc", sparse=["../x"])
    WT.remove(s["path"])                                        # unlocks its own lock
    assert "slim" not in [x["name"] for x in WT.listing(repo)]


def test_prune_keeps_the_newest_and_anything_with_work(repo):
    import os
    import time as _t
    names = []
    for i in range(4):
        w = WT.create(repo, f"t{i}")
        os.utime(w["path"], (_t.time() - 100 + i, _t.time() - 100 + i))
        names.append(w["name"])
    (repo / ".meshpool" / "worktrees" / "t0" / "wip.txt").write_text("unsaved")   # the oldest has work: kept
    os.utime(repo / ".meshpool" / "worktrees" / "t0", (_t.time() - 100, _t.time() - 100))
    gone = WT.prune(repo, keep=1)
    left = [x["name"] for x in WT.listing(repo)]
    assert sorted(gone) == ["t1", "t2"] and sorted(left) == ["t0", "t3"]


def test_a_worktree_chat_asks_before_touching_the_main_checkout(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness.agent import core
    w = WT.create(repo, "guarded")
    a = core.Agent(w["path"], approve=lambda *x: ("no", ""), mode="auto")
    for cmd in (f"git -C {repo} reset --hard", f"rm {repo}/a.txt", "GIT_DIR=../../../.git git log",
                "git --work-tree=/x status"):
        assert not a._permit("bash", {"command": cmd})[0], cmd
    assert a._permit("bash", {"command": f"ls {w['path']}"})[0]           # its own folder is fine
    assert a._permit("bash", {"command": "make build"})[0]


def test_carry_moves_uncommitted_changes_to_another_checkout(repo):
    w = WT.create(repo, "hand")
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("new file")
    assert WT.carry(repo, w["path"]) == ""
    assert Path(w["path"], "a.txt").read_text() == "changed" and Path(w["path"], "new.txt").read_text() == "new file"
    assert (repo / "a.txt").read_text() == "a" and not (repo / "new.txt").exists()   # the source is clean now
    assert WT.carry(repo, w["path"]) == ""                                              # nothing to move: fine


def test_carry_that_conflicts_keeps_the_changes_in_the_stash(repo):
    w = WT.create(repo, "clash")
    Path(w["path"], "a.txt").write_text("theirs")                     # uncommitted there, so the pop cannot apply
    (repo / "a.txt").write_text("mine")
    why = WT.carry(repo, w["path"])
    assert "stash" in why
    assert "meshpool handoff" in subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True,
                                                text=True).stdout                      # nothing lost


def test_worktree_hooks_run_after_create_and_remove(repo, tmp_path, monkeypatch):
    home = tmp_path / "mphome"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    log = tmp_path / "hooklog.txt"
    (home / "hooks.json").write_text(json.dumps({
        "WorktreeCreate": [{"command": f"cat >> {log}; echo >> {log}"}],
        "WorktreeRemove": [{"command": f"cat >> {log}; echo >> {log}"}]}))
    w = WT.create(repo, "hooked")
    WT.remove(w["path"])
    events = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert [e["event"] for e in events] == ["WorktreeCreate", "WorktreeRemove"]
    assert events[0]["worktree_path"] == w["path"] and events[0]["branch"] == "meshpool/hooked"
    assert events[1]["branch_deleted"] is True
