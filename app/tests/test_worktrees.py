"""G-11: isolated copies (git worktrees, plain copies without git): create, diff, merge, conflicts, discard, crash
safety, start-up recovery, Windows paths, checkpoints inside a copy, the terminal commands."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from meshpool_harness import worktrees as WT

ID = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(cwd, *args) -> str:
    r = subprocess.run(["git", *ID, *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\ntwo\nthree\n")
    (r / "b.txt").write_text("bee\n")
    (r / "src").mkdir()
    (r / "src" / "m.py").write_text("x = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def status(r) -> str:
    return git(r, "status", "--porcelain")


# ---------- git: create / list / diff / discard ----------------------------------------------------------------------
def test_create_lists_diffs_and_discard_removes_everything(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c1", title="fix a")
    wd = Path(rec["workdir"])
    assert rec["kind"] == "git" and rec["state"] == "ready" and rec["branch"] == f"meshpool/{rec['id']}"
    assert wd.is_dir() and (wd / "a.txt").read_text() == "one\ntwo\nthree\n"
    assert not WT._norm(wd).startswith(WT._norm(repo))               # outside the user's repository
    assert [r["id"] for r in WT.listing(source=repo)] == [rec["id"]]
    assert WT.of_owner("chat", "c1")["id"] == rec["id"]
    (wd / "a.txt").write_text("one\nTWO\nthree\n")
    (wd / "new.txt").write_text("hi\n")
    d = WT.diff(rec["id"])
    assert {(f["status"], f["path"]) for f in d["files"]} == {("M", "a.txt"), ("A", "new.txt")}
    assert "+TWO" in d["patch"] and "2 files changed" in d["stat"]
    assert status(repo) == "" and (repo / "a.txt").read_text() == "one\ntwo\nthree\n"   # the folder never moved
    WT.discard(rec["id"])
    assert not wd.exists() and WT.listing() == []
    assert rec["branch"] not in git(repo, "branch", "--list")
    assert len(git(repo, "worktree", "list").splitlines()) == 1


def test_subfolder_of_a_repo_works_in_the_same_subfolder_of_the_copy(repo):
    rec = WT.create(repo / "src", owner_kind="cli")
    assert Path(rec["workdir"]) == Path(rec["path"]) / "src" and (Path(rec["workdir"]) / "m.py").is_file()


def test_folder_with_no_commit_falls_back_to_a_plain_copy(tmp_path):
    f = tmp_path / "fresh"
    f.mkdir()
    git(f, "init", "-q")
    (f / "x.txt").write_text("x\n")
    assert WT.create(f, owner_kind="cli")["kind"] == "copy"


# ---------- two chats in parallel ------------------------------------------------------------------------------------
def test_two_chats_edit_the_same_repo_in_parallel_without_clobbering(repo):
    recs = [WT.create(repo, owner_kind="chat", owner_id=f"c{i}") for i in (1, 2)]
    errors = []

    def work(rec, name, n):
        try:
            for i in range(n):                        # interleaved writes to the same file name in both copies
                (Path(rec["workdir"]) / "shared.txt").write_text(f"{name} {i}\n")
                (Path(rec["workdir"]) / f"{name}.txt").write_text(f"{name}\n")
                time.sleep(0.001)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=work, args=(r, n, 50)) for r, n in zip(recs, ("alpha", "beta"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    a, b = (Path(r["workdir"]) for r in recs)
    assert (a / "shared.txt").read_text() == "alpha 49\n" and (b / "shared.txt").read_text() == "beta 49\n"
    assert not (a / "beta.txt").exists() and not (b / "alpha.txt").exists()
    assert status(repo) == ""                         # the user's folder saw none of it
    # both merge back (chat 2 drops its shared.txt, else it would conflict with chat 1)
    (b / "shared.txt").unlink()
    r1 = WT.merge(recs[0]["id"])
    assert r1["ok"] and r1["fast_forward"]
    r2 = WT.merge(recs[1]["id"])
    assert r2["ok"] and not r2["fast_forward"]       # a merge commit: main moved on since chat 2 started
    assert (repo / "alpha.txt").read_text() == "alpha\n" and (repo / "beta.txt").read_text() == "beta\n"
    assert (repo / "shared.txt").read_text() == "alpha 49\n"
    assert status(repo) == ""
    assert len(git(repo, "log", "--merges", "--oneline").splitlines()) == 1


def test_parallel_creates_on_one_repo_all_succeed(repo):
    out, errs = [], []

    def mk(i):
        try:
            out.append(WT.create(repo, owner_kind="chat", owner_id=str(i)))
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=mk, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len({r["id"] for r in out}) == 6 and len(WT.listing()) == 6


def test_two_merges_into_one_repo_at_the_same_moment_are_serialised(repo):
    recs = [WT.create(repo, owner_kind="chat", owner_id=str(i)) for i in range(4)]
    for i, r in enumerate(recs):
        (Path(r["workdir"]) / f"f{i}.txt").write_text(f"{i}\n")
    out = []
    ts = [threading.Thread(target=lambda r=r: out.append(WT.merge(r["id"]))) for r in recs]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(r["ok"] for r in out), out
    assert sorted(p.name for p in repo.glob("f*.txt")) == ["f0.txt", "f1.txt", "f2.txt", "f3.txt"]
    assert status(repo) == "" and not (repo / ".git" / "MERGE_HEAD").exists()


def test_spaces_and_non_ascii_names_survive_create_diff_merge(tmp_path):
    r = tmp_path / "my repo é"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "naïve file.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    rec = WT.create(r, owner_kind="cli")
    (Path(rec["workdir"]) / "naïve file.txt").write_text("b\n")
    (Path(rec["workdir"]) / "ünïcode new.md").write_text("n\n")
    assert sorted(f["path"] for f in WT.diff(rec["id"])["files"]) == ["naïve file.txt", "ünïcode new.md"]
    (r / "ünïcode new.md").write_text("mine\n")            # untracked in the folder, with the same odd name
    res = WT.merge(rec["id"])
    assert not res["ok"] and res["blocked"] == ["ünïcode new.md"]
    (r / "ünïcode new.md").unlink()
    assert WT.merge(rec["id"])["ok"] and (r / "naïve file.txt").read_text() == "b\n"


# ---------- merge safety ---------------------------------------------------------------------------------------------
def test_merge_conflict_is_reported_and_touches_nothing(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    (Path(rec["workdir"]) / "a.txt").write_text("one\nCOPY\nthree\n")
    (repo / "a.txt").write_text("one\nUSER\nthree\n")
    git(repo, "commit", "-qam", "user edit")
    head = git(repo, "rev-parse", "HEAD")
    r = WT.merge(rec["id"])
    assert not r["ok"] and r["conflicts"] == ["a.txt"] and r["branch"] == rec["branch"]
    assert git(repo, "rev-parse", "HEAD") == head and status(repo) == ""
    assert (repo / "a.txt").read_text() == "one\nUSER\nthree\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_merge_refuses_to_overwrite_uncommitted_user_changes(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    (Path(rec["workdir"]) / "a.txt").write_text("copy\n")
    (repo / "a.txt").write_text("my unsaved work\n")          # uncommitted, same file
    head = git(repo, "rev-parse", "HEAD")
    r = WT.merge(rec["id"])
    assert not r["ok"] and r["blocked"] == ["a.txt"]
    assert (repo / "a.txt").read_text() == "my unsaved work\n" and git(repo, "rev-parse", "HEAD") == head


def test_merge_refuses_to_overwrite_an_untracked_user_file(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    (Path(rec["workdir"]) / "notes.md").write_text("from copy\n")
    (repo / "notes.md").write_text("mine, never committed\n")
    r = WT.merge(rec["id"])
    assert not r["ok"] and r["blocked"] == ["notes.md"]
    assert (repo / "notes.md").read_text() == "mine, never committed\n"


def test_merge_keeps_unrelated_uncommitted_user_changes(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    (Path(rec["workdir"]) / "b.txt").write_text("bee 2\n")
    (repo / "a.txt").write_text("my unsaved work\n")
    r = WT.merge(rec["id"])
    assert r["ok"] and r["files"] == ["b.txt"]
    assert (repo / "a.txt").read_text() == "my unsaved work\n" and (repo / "b.txt").read_text() == "bee 2\n"
    assert git(repo, "status", "--porcelain") == "M a.txt"


def test_merge_refused_mid_merge_or_detached(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    (Path(rec["workdir"]) / "b.txt").write_text("x\n")
    git(repo, "checkout", "-q", "--detach")
    r = WT.merge(rec["id"])
    assert not r["ok"] and "detached" in r["reason"]


def test_nothing_to_merge_and_second_merge_after_more_work(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    assert WT.merge(rec["id"])["files"] == []
    (Path(rec["workdir"]) / "b.txt").write_text("1\n")
    assert WT.merge(rec["id"])["ok"]
    assert WT.diff(rec["id"])["files"] == []           # merged: nothing left to show
    (Path(rec["workdir"]) / "b.txt").write_text("2\n")
    assert [f["path"] for f in WT.diff(rec["id"])["files"]] == ["b.txt"]
    assert WT.merge(rec["id"])["ok"] and (repo / "b.txt").read_text() == "2\n"


def test_reset_discards_changes_and_restarts_from_folder_head(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    (Path(rec["workdir"]) / "a.txt").write_text("junk\n")
    (Path(rec["workdir"]) / "junk.txt").write_text("junk\n")
    (repo / "c.txt").write_text("c\n")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-qm", "c")
    WT.reset(rec["id"])
    wd = Path(rec["workdir"])
    assert (wd / "a.txt").read_text() == "one\ntwo\nthree\n" and not (wd / "junk.txt").exists()
    assert (wd / "c.txt").exists() and WT.diff(rec["id"])["files"] == []


# ---------- crash safety and recovery --------------------------------------------------------------------------------
def test_crash_mid_create_leaves_nothing(repo, monkeypatch):
    def boom(rec):
        assert Path(rec["path"]).is_dir()            # the worktree exists at this point
        raise RuntimeError("power cut")
    monkeypatch.setattr(WT, "_after_add", boom)
    with pytest.raises(RuntimeError):
        WT.create(repo, owner_kind="chat", owner_id="c")
    assert WT.listing() == [] and [p for p in WT.root().iterdir() if p.name not in ("locks",) and p.is_dir()] == []
    assert "meshpool/" not in git(repo, "branch", "--list")
    assert len(git(repo, "worktree", "list").splitlines()) == 1


def test_git_failure_mid_create_is_cleaned_and_reported(repo, monkeypatch):
    real = WT._git

    def fake(cwd, *args, **kw):
        if args[:2] == ("worktree", "add"):
            real(cwd, *args, **kw)                    # it half-worked, then failed
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: disk full")
        return real(cwd, *args, **kw)
    monkeypatch.setattr(WT, "_git", fake)
    with pytest.raises(WT.WorktreeError, match="disk full"):
        WT.create(repo, owner_kind="chat", owner_id="c")
    monkeypatch.setattr(WT, "_git", real)
    assert WT.listing() == [] and "meshpool/" not in git(repo, "branch", "--list")


def test_process_killed_mid_create_is_cleaned_by_recover(repo):
    """A real hard crash: another process dies (os._exit) after git made the worktree, before the record was ready."""
    code = ("import os, sys; from meshpool_harness import worktrees as WT; "
            "WT._after_add = lambda rec: os._exit(9); WT.create(sys.argv[1], owner_kind='chat', owner_id='c')")
    r = subprocess.run([sys.executable, "-c", code, str(repo)], cwd=str(Path(WT.__file__).parents[1]),
                       env=dict(os.environ), capture_output=True)
    assert r.returncode == 9
    [rec] = WT.listing()
    assert rec["state"] == "creating" and Path(rec["path"]).is_dir()
    done = WT.recover()
    assert done["creating"] == [rec["id"]] and WT.listing() == [] and not Path(rec["path"]).exists()
    assert "meshpool/" not in git(repo, "branch", "--list") and len(git(repo, "worktree", "list").splitlines()) == 1


def test_recover_prunes_dead_owners_orphans_and_vanished_folders_but_keeps_live_chats(repo):
    live = WT.create(repo, owner_kind="chat", owner_id="alive")
    dead = WT.create(repo, owner_kind="chat", owner_id="deleted-while-closed")
    (Path(dead["workdir"]) / "b.txt").write_text("unmerged work\n")
    gone = WT.create(repo, owner_kind="chat", owner_id="alive2")
    WT._rmtree(gone["path"])
    sub = WT.create(repo, owner_kind="subagent", owner_id="d1")
    WT.set_owner(sub["id"], "subagent", "d1")
    recs = WT._items()
    recs[sub["id"]]["owner"]["pid"] = 999999999          # its process is gone
    WT._put(recs[sub["id"]])
    orphan = WT.root() / "deadbeef"
    orphan.mkdir()
    (orphan / "f").write_text("x")
    os.utime(orphan, (time.time() - 3600, time.time() - 3600))
    done = WT.recover(alive=lambda kind, oid, rec: oid in ("alive", "alive2") if kind == "chat"
                      else WT._default_alive(kind, oid, rec))
    assert set(WT._items()) == {live["id"]} and Path(live["workdir"]).is_dir()
    assert done["dead_owner"] == [dead["id"], sub["id"]] and done["missing"] == [gone["id"]]
    assert done["orphan_dirs"] == ["deadbeef"] and not orphan.exists()
    assert done["kept_branches"] == [dead["branch"]]       # unmerged work stays on its branch
    assert git(repo, "show", f"{dead['branch']}:b.txt") == "unmerged work"


def test_orphan_git_worktree_folder_is_detached_from_its_repo(repo):
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    WT._drop(rec["id"])                                      # registry lost it
    os.utime(rec["path"], (time.time() - 3600, time.time() - 3600))
    WT.recover()
    assert not Path(rec["path"]).exists() and len(git(repo, "worktree", "list").splitlines()) == 1


def test_damaged_registry_is_quarantined_not_trusted(repo):
    WT.root().mkdir(parents=True, exist_ok=True)
    WT._registry().write_text("{not json")
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    assert [r["id"] for r in WT.listing()] == [rec["id"]]
    assert list(WT.root().glob("registry.json.corrupt-*"))


def test_prune_owner_on_chat_delete_keeps_unmerged_work_on_branch(repo):
    clean = WT.create(repo, owner_kind="chat", owner_id="x")
    worked = WT.create(repo, owner_kind="chat", owner_id="x")
    (Path(worked["workdir"]) / "new.txt").write_text("keep me\n")
    res = {r["id"]: r for r in WT.prune_owner("chat", "x")}
    assert WT.listing() == [] and not Path(worked["path"]).exists() and not Path(clean["path"]).exists()
    assert res[worked["id"]]["kept_branch"] == worked["branch"] and res[clean["id"]]["kept_branch"] is None
    assert clean["branch"] not in git(repo, "branch", "--list")
    assert git(repo, "show", f"{worked['branch']}:new.txt") == "keep me"


# ---------- sub-agent copies: from the working tree, applied without a commit ----------------------------------------
def test_subagent_copies_start_from_unsaved_edits_and_apply_to_the_working_tree(repo):
    (repo / "a.txt").write_text("one\ntwo\nthree\nparent edit\n")      # the parent turn's unsaved edit
    (repo / "draft.txt").write_text("untracked draft\n")
    head = git(repo, "rev-parse", "HEAD")
    s1 = WT.create(repo, owner_kind="subagent", owner_id="d", base="worktree")
    s2 = WT.create(repo, owner_kind="subagent", owner_id="d", base="worktree")
    s3 = WT.create(repo, owner_kind="subagent", owner_id="d", base="worktree")
    assert s1["mode"] == "apply" and (Path(s1["workdir"]) / "draft.txt").exists()
    assert "parent edit" in (Path(s1["workdir"]) / "a.txt").read_text()
    (Path(s1["workdir"]) / "a.txt").write_text("ONE\ntwo\nthree\nparent edit\n")
    (Path(s2["workdir"]) / "b.txt").unlink()
    (Path(s2["workdir"]) / "s2.txt").write_text("s2\n")
    (Path(s3["workdir"]) / "a.txt").write_text("uno\ntwo\nthree\nparent edit\n")    # same line as s1
    assert WT.merge(s1["id"])["files"] == ["a.txt"]
    assert sorted(WT.merge(s2["id"])["files"]) == ["b.txt", "s2.txt"]
    r3 = WT.merge(s3["id"])
    assert not r3["ok"] and r3["conflicts"] == ["a.txt"]
    assert (repo / "a.txt").read_text() == "ONE\ntwo\nthree\nparent edit\n"
    assert not (repo / "b.txt").exists() and (repo / "s2.txt").read_text() == "s2\n"
    assert (repo / "draft.txt").read_text() == "untracked draft\n"
    assert git(repo, "rev-parse", "HEAD") == head                         # no commit: the turn's Undo covers it
    assert git(repo, "diff", "--cached", "--name-only") == ""             # the index is untouched


# ---------- folders without git: plain copies ------------------------------------------------------------------------
@pytest.fixture
def plain(tmp_path):
    p = tmp_path / "plain"
    (p / "sub").mkdir(parents=True)
    (p / "a.txt").write_text("a\n")
    (p / "sub" / "b.txt").write_text("b\n")
    (p / "node_modules").mkdir()
    (p / "node_modules" / "big.js").write_text("skip me")
    return p


def test_non_git_copy_diff_merge_conflict_discard(plain):
    rec = WT.create(plain, owner_kind="chat", owner_id="c")
    wd = Path(rec["workdir"])
    assert rec["kind"] == "copy" and (wd / "sub" / "b.txt").read_text() == "b\n"
    assert not (wd / "node_modules").exists()
    (wd / "a.txt").write_text("a2\n")
    (wd / "new.txt").write_text("n\n")
    (wd / "sub" / "b.txt").unlink()
    d = WT.diff(rec["id"])
    assert {(f["status"], f["path"]) for f in d["files"]} == {("M", "a.txt"), ("A", "new.txt"), ("D", "sub/b.txt")}
    assert "+a2" in d["patch"]
    assert (plain / "a.txt").read_text() == "a\n"                # untouched until merge
    r = WT.merge(rec["id"])
    assert r["ok"] and r["files"] == ["a.txt", "new.txt", "sub/b.txt"]
    assert (plain / "a.txt").read_text() == "a2\n" and (plain / "new.txt").exists() and not (plain / "sub/b.txt").exists()
    assert WT.diff(rec["id"])["files"] == []
    # a conflict: both changed a.txt since; all or nothing
    (wd / "a.txt").write_text("copy again\n")
    (wd / "other.txt").write_text("o\n")
    (plain / "a.txt").write_text("user again\n")
    r = WT.merge(rec["id"])
    assert not r["ok"] and r["conflicts"] == ["a.txt"]
    assert (plain / "a.txt").read_text() == "user again\n" and not (plain / "other.txt").exists()
    WT.discard(rec["id"])
    assert not Path(rec["path"]).exists() and WT.listing() == []


def test_non_git_too_many_files_is_refused(plain, monkeypatch):
    import meshpool_harness.checkpoints as CP
    monkeypatch.setattr(CP, "MAX_FILES", 1)
    with pytest.raises(WT.WorktreeError, match="too big"):
        WT.create(plain, owner_kind="chat", owner_id="c")
    assert WT.listing() == []


# ---------- Windows ----------------------------------------------------------------------------------------------------
def test_windows_long_paths_and_git_longpaths(monkeypatch):
    monkeypatch.setattr(WT, "_WINDOWS", True)
    assert WT._git_cmd("status")[:5] == ["git", "-c", "core.quotepath=false", "-c", "core.longpaths=true"]
    deep = "C:\\Users\\me\\" + "\\".join(["very-long-folder-name"] * 15)
    monkeypatch.setattr(WT.os.path, "abspath", lambda s: s)
    assert WT._long(deep) == "\\\\?\\" + deep
    assert WT._long("\\\\server\\share\\x") == "\\\\?\\UNC\\server\\share\\x"
    assert WT._long("\\\\?\\C:\\x") == "\\\\?\\C:\\x"
    monkeypatch.setattr(WT, "_WINDOWS", False)
    assert WT._long("/a/b") == "/a/b" and "core.longpaths=true" not in WT._git_cmd("status")


def test_read_only_files_are_removed(tmp_path):
    d = tmp_path / "ro"
    (d / "objects").mkdir(parents=True)
    f = d / "objects" / "pack"
    f.write_text("x")
    os.chmod(f, 0o444)
    os.chmod(d / "objects", 0o555)
    try:
        WT._rmtree(d)
    finally:
        if d.exists():
            os.chmod(d / "objects", 0o755)
    assert not d.exists()


def test_deep_paths_survive_create_merge_discard(repo):
    deep = repo.joinpath(*(["nested-folder-name-x"] * 12))
    deep.mkdir(parents=True)
    (deep / "f.txt").write_text("deep\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "deep")
    rec = WT.create(repo, owner_kind="cli")
    rel = deep.relative_to(repo)
    (Path(rec["workdir"]) / rel / "f.txt").write_text("deeper\n")
    assert WT.merge(rec["id"])["ok"] and (deep / "f.txt").read_text() == "deeper\n"
    WT.discard(rec["id"])
    assert not Path(rec["path"]).exists()


# ---------- checkpoints (Undo) inside a copy -------------------------------------------------------------------------
def test_checkpoints_undo_inside_a_copy_leaves_the_folder_alone(repo, tmp_path):
    from meshpool_harness.checkpoints import Checkpoints
    rec = WT.create(repo, owner_kind="chat", owner_id="c")
    wd = Path(rec["workdir"])
    cp = Checkpoints(str(wd), tmp_path / "cp")
    t = cp.begin_turn("edit")
    (wd / "a.txt").write_text("agent edit\n")
    (wd / "made.txt").write_text("made\n")
    cp.end_turn()
    (repo / "a.txt").write_text("user edit in the real folder\n")
    assert [f["path"] for f in cp.diff()["files"]] == ["a.txt", "made.txt"]
    res = cp.revert_to_before(t["n"] if "n" in t else t.get("turn", 1))
    assert not res.get("error"), res
    assert (wd / "a.txt").read_text() == "one\ntwo\nthree\n" and not (wd / "made.txt").exists()
    assert (repo / "a.txt").read_text() == "user edit in the real folder\n"


# ---------- terminal --------------------------------------------------------------------------------------------------
def test_cli_list_diff_merge_discard(repo, monkeypatch):
    monkeypatch.chdir(repo)
    rec = WT.create(repo, owner_kind="cli", title="terminal session")
    (Path(rec["workdir"]) / "b.txt").write_text("from cli\n")
    out = io.StringIO()
    assert WT.cli(["list"], out) == 0 and rec["id"] in out.getvalue() and "1 changed" in out.getvalue()
    out = io.StringIO()
    assert WT.cli(["diff", rec["id"][:5]], out) == 0 and "+from cli" in out.getvalue()
    out = io.StringIO()
    assert WT.cli(["merge", rec["id"]], out) == 0 and "Merged 1 file" in out.getvalue()
    assert (repo / "b.txt").read_text() == "from cli\n"
    out = io.StringIO()
    assert WT.cli(["discard", rec["id"]], out) == 0 and WT.listing() == []
    assert WT.cli(["merge"], io.StringIO()) == 2
    out = io.StringIO()
    assert WT.cli(["diff", "nope"], out) == 1 and "No isolated copy" in out.getvalue()


def test_cli_merge_conflict_exits_1_and_lists_files(repo, monkeypatch):
    rec = WT.create(repo, owner_kind="cli")
    (Path(rec["workdir"]) / "a.txt").write_text("copy\n")
    (repo / "a.txt").write_text("user\n")
    git(repo, "commit", "-qam", "u")
    out = io.StringIO()
    assert WT.cli(["merge", rec["id"]], out) == 1 and "conflict: a.txt" in out.getvalue()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_plain_copy_merge_keeps_file_modes(tmp_path):
    """Review (0.0.16): merging a plain copy wrote every file 0600 (mkstemp), so run.sh lost its x bit."""
    f = tmp_path / "plain"
    f.mkdir()
    (f / "run.sh").write_text("echo a\n")
    os.chmod(f / "run.sh", 0o755)
    (f / "notes.txt").write_text("a\n")
    os.chmod(f / "notes.txt", 0o644)
    rec = WT.create(f, owner_kind="cli")
    assert rec["kind"] == "copy"
    (Path(rec["workdir"]) / "run.sh").write_text("echo b\n")
    (Path(rec["workdir"]) / "new.txt").write_text("n\n")
    assert WT.merge(rec["id"])["ok"]
    assert (f / "run.sh").read_text() == "echo b\n" and (f / "run.sh").stat().st_mode & 0o777 == 0o755
    assert (f / "new.txt").stat().st_mode & 0o044          # a new file is readable as open() would make it, not 0600
