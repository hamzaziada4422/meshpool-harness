"""Release code review (isolated copies): an unreadable registry never costs a copy, a damaged one is rebuilt from the
folders, a sub-agent's changes are applied only to the paths it changed, a plain copy that cannot be merged is kept,
and a plain-copy merge is all or nothing."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from meshpool_harness import fsio
from meshpool_harness import worktrees as WT

ID = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(cwd, *args) -> str:
    r = subprocess.run(["git", *ID, *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    (r / "b.txt").write_text("bee\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def plain(tmp_path):
    p = tmp_path / "plain"
    p.mkdir()
    (p / "a.txt").write_text("a\n")
    (p / "b.txt").write_text("b\n")
    return p


def _age(*paths):
    for p in paths:
        os.utime(p, (time.time() - 3600, time.time() - 3600))


# ---------- 1: the registry --------------------------------------------------------------------------------------------
def test_an_unreadable_registry_is_never_moved_aside_and_recover_deletes_no_copy(repo, plain, monkeypatch):
    g = WT.create(repo, owner_kind="chat", owner_id="c1")
    c = WT.create(plain, owner_kind="chat", owner_id="c2")
    (Path(g["workdir"]) / "a.txt").write_text("unmerged work\n")
    (Path(c["workdir"]) / "a.txt").write_text("unmerged plain work\n")
    _age(g["path"], c["path"])
    before = WT._registry().read_bytes()
    real = fsio.read_json

    def locked(p, default=None):                     # a sharing violation / permission error on Windows
        if Path(p) == WT._registry():
            raise fsio.CorruptFile(p, PermissionError(13, "The process cannot access the file"))
        return real(p, default)
    monkeypatch.setattr(fsio, "read_json", locked)
    monkeypatch.setattr(WT.time, "sleep", lambda s: None)
    done = WT.recover()
    with pytest.raises(WT.WorktreeError):
        WT._put(dict(g, title="changed"))            # nothing is written from an unreadable registry
    monkeypatch.setattr(fsio, "read_json", real)
    assert Path(g["path"]).is_dir() and Path(c["path"]).is_dir()
    assert (Path(g["workdir"]) / "a.txt").read_text() == "unmerged work\n"
    assert done["orphan_dirs"] == [] and done.get("error")
    assert not list(WT.root().glob("registry.json.corrupt-*")) and WT._registry().read_bytes() == before
    assert {r["id"] for r in WT.listing()} == {g["id"], c["id"]}


def test_a_damaged_registry_is_rebuilt_from_the_copies_and_recover_keeps_them(repo, plain):
    g = WT.create(repo, owner_kind="chat", owner_id="c1", title="feature")
    c = WT.create(plain, owner_kind="subagent", owner_id="d1", title="plain one")
    (Path(g["workdir"]) / "new.txt").write_text("work\n")
    (Path(c["workdir"]) / "a.txt").write_text("plain work\n")
    _age(g["path"], c["path"])
    WT._registry().write_text("{not json")
    rows = {r["id"]: r for r in WT.listing()}
    assert set(rows) == {g["id"], c["id"]}
    assert rows[g["id"]]["kind"] == "git" and rows[g["id"]]["branch"] == g["branch"]
    assert WT._norm(rows[g["id"]]["top"]) == WT._norm(repo)
    assert rows[c["id"]]["kind"] == "copy" and rows[c["id"]]["source"] == str(plain.resolve())
    assert list(WT.root().glob("registry.json.corrupt-*"))     # the damaged file is kept for inspection
    done = WT.recover()
    assert done["orphan_dirs"] == [] and Path(g["path"]).is_dir() and Path(c["path"]).is_dir()
    assert {f["path"] for f in WT.diff(g["id"])["files"]} == {"new.txt"}
    r = WT.merge(c["id"])                                         # a rebuilt plain copy still merges
    assert r["ok"] and (plain / "a.txt").read_text() == "plain work\n"


def test_orphan_folders_are_not_swept_on_the_word_of_a_rebuilt_registry(repo):
    junk = WT.root() / "deadbeef"
    junk.mkdir(parents=True)
    (junk / "f").write_text("x")
    _age(junk)
    WT._registry().write_text("[]")                              # valid JSON, not a registry
    assert WT.recover()["orphan_dirs"] == [] and junk.exists()
    assert WT.recover()["orphan_dirs"] == ["deadbeef"]           # read cleanly this time: a real orphan goes


# ---------- 2: a sub-agent's changes touch only what it changed --------------------------------------------------------
def _hook_after_ours(monkeypatch, repo, action):
    """Run ``action`` right after the merge has read the folder (``ours``): the user saving files meanwhile."""
    E = WT._engine()
    real = E._worktree_tree
    state = {"done": False}

    def spy(workdir):
        out = real(workdir)
        if not state["done"] and WT._norm(workdir) == WT._norm(repo):
            state["done"] = True
            action()
        return out
    monkeypatch.setattr(E, "_worktree_tree", spy)


def test_apply_leaves_files_the_user_writes_during_the_merge_alone(repo, monkeypatch):
    s = WT.create(repo, owner_kind="subagent", owner_id="d", base="worktree")
    (Path(s["workdir"]) / "a.txt").write_text("one\nTWO\nthree\n")

    def user_saves():
        (repo / "b.txt").write_text("user edit while merging\n")
        (repo / "notes.txt").write_text("a new file of the user's\n")
    _hook_after_ours(monkeypatch, repo, user_saves)
    r = WT.merge(s["id"])
    assert r["ok"] and r["files"] == ["a.txt"]
    assert (repo / "a.txt").read_text() == "one\nTWO\nthree\n"
    assert (repo / "b.txt").read_text() == "user edit while merging\n"            # not reverted
    assert (repo / "notes.txt").read_text() == "a new file of the user's\n"       # not deleted


def test_apply_refuses_when_a_path_it_changes_was_written_during_the_merge(repo, monkeypatch):
    s = WT.create(repo, owner_kind="subagent", owner_id="d", base="worktree")
    (Path(s["workdir"]) / "a.txt").write_text("one\nTWO\nthree\n")
    _hook_after_ours(monkeypatch, repo, lambda: (repo / "a.txt").write_text("the user's newest\n"))
    r = WT.merge(s["id"])
    assert not r["ok"] and r["blocked"] == ["a.txt"]
    assert (repo / "a.txt").read_text() == "the user's newest\n"


# ---------- 3: a plain copy that cannot be merged is kept --------------------------------------------------------------
def test_plain_subagent_copy_with_a_conflict_is_kept_and_named(plain):
    c = WT.subagent_copy(plain, "d1", "part one")
    assert c and c["kind"] == "copy"
    (Path(c["workdir"]) / "a.txt").write_text("sub-agent work\n")
    (Path(c["workdir"]) / "extra.txt").write_text("more work\n")
    (plain / "a.txt").write_text("user changed it\n")
    r = WT.apply_subagent_copy(c)
    assert not r["ok"] and r["bad"] == ["a.txt"] and r["kept_dir"]
    kept = Path(r["kept_dir"])
    assert (kept / "a.txt").read_text() == "sub-agent work\n" and (kept / "extra.txt").read_text() == "more work\n"
    assert not WT._norm(kept).startswith(WT._norm(WT.root()))   # outside wt/: no clean-up sweeps it
    assert WT.listing() == [] and (plain / "a.txt").read_text() == "user changed it\n"
    assert WT.recover()["orphan_dirs"] == [] and kept.is_dir()


def test_plain_copy_without_changes_or_discarded_on_purpose_is_removed(plain):
    c = WT.subagent_copy(plain, "d1")
    assert WT.apply_subagent_copy(c)["kept_dir"] is None and not Path(c["path"]).exists()
    c = WT.create(plain, owner_kind="chat", owner_id="x")
    (Path(c["workdir"]) / "a.txt").write_text("thrown away\n")
    assert WT.discard(c["id"])["kept_dir"] is None and not Path(c["path"]).exists()


# ---------- 10: a plain-copy merge is all or nothing -------------------------------------------------------------------
def test_plain_merge_that_fails_part_way_changes_nothing(plain, monkeypatch):
    import tempfile
    c = WT.create(plain, owner_kind="chat", owner_id="x")
    (Path(c["workdir"]) / "a.txt").write_text("A2\n")
    (Path(c["workdir"]) / "b.txt").write_text("B2\n")
    real = tempfile.mkstemp
    n = {"k": 0}

    def full(*a, **kw):
        if WT._norm(kw.get("dir") or "").startswith(WT._norm(plain)):
            n["k"] += 1
            if n["k"] == 2:
                raise OSError(28, "No space left on device")
        return real(*a, **kw)
    monkeypatch.setattr(tempfile, "mkstemp", full)
    with pytest.raises(OSError):
        WT.merge(c["id"])
    monkeypatch.setattr(tempfile, "mkstemp", real)
    assert (plain / "a.txt").read_text() == "a\n" and (plain / "b.txt").read_text() == "b\n"
    assert sorted(p.name for p in plain.iterdir()) == ["a.txt", "b.txt"]          # no temporary files left
    assert WT.merge(c["id"])["ok"] and (plain / "a.txt").read_text() == "A2\n"
    assert WT.diff(c["id"])["files"] == []                                         # base_dir follows the folder
