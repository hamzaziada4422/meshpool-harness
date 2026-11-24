"""Git panel: stage and unstage single files, commit only what is staged, recent commits."""
import subprocess
from pathlib import Path

import pytest

from meshpool_harness.ui import workbench as W


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, capture_output=True,
                          text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("base\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base commit")
    return tmp_path


def test_stage_unstage_and_commit_only_what_is_staged(repo):
    (repo / "a.txt").write_text("changed\n")
    (repo / "b.txt").write_text("new\n")
    st = W.git_action(str(repo), {"action": "stage", "paths": ["b.txt"]})
    by = {f["path"]: f for f in st["files"]}
    assert st["staged"] == 1 and by["b.txt"]["staged"] and not by["a.txt"]["staged"] and by["a.txt"]["unstaged"]
    st = W.git_action(str(repo), {"action": "commit", "message": "add b", "staged_only": True})
    assert [f["path"] for f in st["files"]] == ["a.txt"]                    # a.txt stays uncommitted
    assert st["log"][0]["subject"] == "add b" and st["log"][1]["subject"] == "base commit"
    W.git_action(str(repo), {"action": "stage", "paths": ["a.txt"]})
    st = W.git_action(str(repo), {"action": "unstage", "paths": ["a.txt"]})
    assert st["staged"] == 0 and (repo / "a.txt").read_text() == "changed\n"   # unstaging keeps the change


def test_commit_staged_with_nothing_staged_says_so(repo):
    (repo / "a.txt").write_text("changed\n")
    with pytest.raises(ValueError, match="Nothing is staged"):
        W.git_action(str(repo), {"action": "commit", "message": "x", "staged_only": True})


@pytest.mark.parametrize("bad", ["--all", "../outside.txt", "/etc/passwd"])
def test_stage_takes_files_of_this_project_only(repo, bad):
    with pytest.raises(ValueError):
        W.git_action(str(repo), {"action": "stage", "paths": [bad]})


def test_unstage_before_the_first_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    git(tmp_path, "init", "-q")
    (tmp_path / "x.txt").write_text("x")
    W.git_action(str(tmp_path), {"action": "stage", "paths": ["x.txt"]})
    st = W.git_action(str(tmp_path), {"action": "unstage", "paths": ["x.txt"]})
    assert st["staged"] == 0 and (tmp_path / "x.txt").exists() and st["log"] == []


def test_paths_in_a_subfolder_project_renames_and_spaces(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("x")
    (tmp_path / "sub" / "old.txt").write_text("o")
    (tmp_path / "top.txt").write_text("t")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "i")
    proj = tmp_path / "sub"
    (proj / "x.txt").write_text("changed")
    (proj / "a b.txt").write_text("spaces")
    git(tmp_path, "mv", "sub/old.txt", "sub/new.txt")
    (tmp_path / "top.txt").write_text("outside the project")
    st = W.git_status(str(proj))
    paths = sorted(f["path"] for f in st["files"])
    assert paths == ["a b.txt", "new.txt", "x.txt"]                          # project-relative, only this folder
    st = W.git_action(str(proj), {"action": "stage", "paths": ["x.txt", "a b.txt"]})
    assert {f["path"] for f in st["files"] if f["staged"]} == {"x.txt", "a b.txt", "new.txt"}
    with pytest.raises(ValueError):
        W.git_action(str(proj), {"action": "stage", "paths": [":/"]})        # no pathspec magic
