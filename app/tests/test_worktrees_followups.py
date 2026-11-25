"""G-11 follow-ups and OSS-34: run_subtasks gives each editing sub-agent its own copy, discard means the same thing in
the app and the terminal, git submodules are initialised in a new copy, identical sub-agent requests run once."""
from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import worktrees as WT
from meshpool_harness.agent import core as K
from meshpool_harness.agent import delegate as D

ID = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(cwd, *args) -> str:
    r = subprocess.run(["git", *ID, *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def _msg(content="", calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"c{i}-{time.time_ns()}", "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": m, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, False


@pytest.fixture
def editors(tmp_path, monkeypatch):
    """A fake model whose sub-agents write the file named in their prompt: 'write <path> <text>'."""
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "edit")
    calls = []

    def post(self, base, key, body, stream):
        if body["messages"][-1]["role"] == "tool":
            return _msg("done")
        goal = next(m["content"] for m in body["messages"][1:] if m["role"] == "user").split("\n")[0]
        calls.append(goal)
        _, path, text = goal.split(" ", 2)
        return _msg("", [("write_file", {"path": path, "content": text + "\n"})])
    monkeypatch.setattr(K.Agent, "_post", post)
    return calls


def _run(repo, *prompts):
    return AS.run_subtasks([{"prompt": p} for p in prompts], str(repo),
                           parent={"root": str(repo), "mode": "auto"}, trusted=True)


def test_run_subtasks_two_editors_on_one_file_do_not_clobber(repo, editors):
    out = _run(repo, "write shared.txt from-A", "write shared.txt from-B", "write own.txt only-C")
    assert (repo / "shared.txt").read_text() == "from-A\n"            # task order wins; B is never forced in
    assert (repo / "own.txt").read_text() == "only-C\n"              # a part that does not conflict is applied
    assert out.count("changes were applied") == 2 and "NOT applied" in out and "shared.txt" in out
    assert WT.listing() == []                                        # every copy is removed
    assert "meshpool/" in git(repo, "branch", "--list")              # B's work stays on its branch, named in the report


def test_run_subtasks_one_editor_and_read_only_share_the_folder(repo, editors, monkeypatch):
    _run(repo, "write one.txt solo")
    assert (repo / "one.txt").exists() and WT.listing() == []
    assert "meshpool/" not in git(repo, "branch", "--list")


def test_identical_sub_agent_requests_in_one_batch_run_once(repo, editors):
    out = _run(repo, "write d.txt same", "write d.txt same")
    assert editors == ["write d.txt same"] and "1 parts finished" in out
    rows = D.check_goals([{"goal": "Research Acme pricing"}, {"goal": "Research Acme pricing"},
                          {"goal": "Research Acme pricing", "context": "B2B"}])
    assert [r["context"] for r in rows] == ["", "B2B"]


def test_delegate_two_editors_never_write_the_same_folder(repo, editors, monkeypatch):
    class P:
        root = str(repo)
        usage = {"in": 0, "out": 0, "calls": 0}

        def emit(self, e): ...
    d = D.Delegation(P(), D.check_goals([{"goal": "write f.txt from-A"}, {"goal": "write f.txt from-B"}]),
                     {"access": "edit"}, 0)
    assert d.isolate
    a, b = d._copy(d.rows[0]), d._copy(d.rows[1])
    assert a["workdir"] != b["workdir"] and str(repo) not in (a["workdir"], b["workdir"])
    for rec in (a, b):
        WT.discard(rec["id"])


# ---------- discard ---------------------------------------------------------------------------------------------------
def test_discard_removes_the_copy_and_keeps_a_branch_only_for_unmerged_commits(repo):
    rec = WT.create(repo)
    (Path(rec["workdir"]) / "junk.txt").write_text("x")
    r = WT.discard(rec["id"])
    assert r["kept_branch"] is None and "gone" in r["note"] and WT.listing() == []
    assert not Path(rec["workdir"]).exists() and "meshpool/" not in git(repo, "branch", "--list")
    rec = WT.create(repo)
    wd = Path(rec["workdir"])
    (wd / "done.txt").write_text("y")
    git(wd, "add", "-A")
    git(wd, "commit", "-qm", "unmerged")
    r = WT.discard(rec["id"])
    assert r["kept_branch"] == rec["branch"] and rec["branch"] in r["note"] and not wd.exists()
    assert rec["branch"] in git(repo, "branch", "--list")


def test_cli_discard_removes_the_copy(repo, monkeypatch):
    rec = WT.create(repo)
    out = io.StringIO()
    assert WT.cli(["discard", rec["id"]], out) == 0 and "Discarded" in out.getvalue()
    assert WT.listing() == [] and not Path(rec["workdir"]).exists()
    assert "removes the copy" in WT.USAGE


def test_app_discard_removes_the_copy_and_reset_keeps_it(repo, tmp_path, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui import worktree_api as API
    rec = WT.create(repo, owner_kind="chat", owner_id="chat1")
    monkeypatch.setattr(A, "read_meta", lambda sid: {"worktree": {"id": rec["id"]}})
    monkeypatch.setattr(A, "LIVE", {})
    metas = {}
    monkeypatch.setattr(A, "write_meta", lambda sid, **kw: metas.update(kw))

    def call(body):
        return API.handle("POST", urlparse("/api/worktree/chat1"), json.dumps(body).encode())
    (Path(rec["workdir"]) / "junk.txt").write_text("x")
    st, out = call({"action": "reset"})
    assert st == 200 and out["diff"]["files"] == [] and Path(rec["workdir"]).is_dir()
    st, out = call({"action": "discard"})
    assert st == 200 and out["ok"] and out["removed"] and WT.listing() == [] and not Path(rec["workdir"]).exists()
    assert metas["worktree"]["removed"] is True


# ---------- submodules ------------------------------------------------------------------------------------------------
def _with_submodule(tmp_path, repo, monkeypatch):
    # a local submodule needs the file transport, which git blocks for submodules by default
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    lib = tmp_path / "lib"
    lib.mkdir()
    git(lib, "init", "-q", "-b", "main")
    (lib / "lib.py").write_text("v = 1\n")
    git(lib, "add", "-A")
    git(lib, "commit", "-qm", "lib")
    git(repo, "submodule", "add", "-q", str(lib), "vendor/lib")
    git(repo, "commit", "-qm", "add submodule")


def test_submodules_are_initialised_in_the_copy(repo, tmp_path, monkeypatch):
    _with_submodule(tmp_path, repo, monkeypatch)
    rec = WT.create(repo)
    assert (Path(rec["workdir"]) / "vendor" / "lib" / "lib.py").read_text() == "v = 1\n" and not rec.get("note")
    assert WT.diff(rec["id"])["files"] == []                         # an initialised submodule is not a change
    WT.discard(rec["id"])
    assert WT.listing() == []


def test_a_failing_submodule_update_leaves_a_usable_copy_with_a_note(repo, tmp_path, monkeypatch):
    _with_submodule(tmp_path, repo, monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "never")                # the transport is refused: the update fails
    rec = WT.create(repo)
    assert Path(rec["workdir"], "app.py").is_file() and "submodule" in rec["note"] and WT.get(rec["id"])["state"] == "ready"
    WT.discard(rec["id"])
