"""Git worktrees for tasks (worktrees.py): create with .worktreeinclude, list with state, and a remove that never
loses commits or changes without force."""
import json
import subprocess
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
