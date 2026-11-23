"""G-11 in the app, sub-agents, scheduled runs and the terminal: chats in an isolated copy (worktree), the suggestion
when another chat already works in the folder, the Merge / Discard panel API, delete and start-up clean-up, sub-agents
in their own copies, the scheduler's run-folder seam, `meshpool --worktree` and `meshpool worktree ...`."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from meshpool_harness import worktrees as WT
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import worktree_api as API

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
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {}, raising=False)
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(A, "_start_reaper", lambda: None)
    from meshpool_harness.ui import workbench as W
    monkeypatch.setattr(W, "add_project", lambda *a, **k: None)
    return tmp_path


class FakeSession:
    def __init__(self, workdir=None, mode=None, harness=None, kind="code", model=None, plan=False, **kw):
        import uuid
        from meshpool_harness.checkpoints import Checkpoints
        self.id = uuid.uuid4().hex[:8]
        (A.SESSIONS / self.id).mkdir()
        self.workdir, self.kind, self.state, self.harness = str(workdir), kind, "idle", {"id": harness or "x"}
        self.agent = SimpleNamespace(title="", deleted=False)
        self.events, self.sent = [], []
        self.cp = Checkpoints(self.workdir, A.SESSIONS / self.id / "checkpoints")

    def view(self, since):
        return {"id": self.id, "workdir": self.workdir, "worktree": A.read_meta(self.id).get("worktree")}

    def emit(self, ev):
        self.events.append(ev)

    def send(self, text):
        self.sent.append(text)

    def close(self):
        pass


def call(method, path, body=None):
    st, out = API.handle(method, urlparse(path), json.dumps(body).encode() if body is not None else b"")[:2]
    return st, out


# ---------- chats ------------------------------------------------------------------------------------------------------
def test_code_chat_in_a_worktree_diff_merge_discard_and_delete(app, repo, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    v = A.create({"workdir": str(repo), "kind": "code", "harness": "meshpool-code", "task": "add a feature",
                  "worktree": True})
    s = A.LIVE[v["id"]]
    wt = v["worktree"]
    assert wt and wt["source"] == str(repo.resolve()) and wt["branch"].startswith("meshpool/")
    assert s.workdir == wt["workdir"] != str(repo) and str(s.cp.root) == str(Path(wt["workdir"]).resolve())
    assert WT.get(wt["id"])["owner"] == {"kind": "chat", "id": v["id"], "pid": WT.os.getpid()}
    rows = A.listing()
    assert [r for r in rows if r["id"] == v["id"]][0]["workdir"] == str(repo.resolve())   # listed under its folder
    (Path(s.workdir) / "feature.py").write_text("x = 1\n")
    st, out = call("GET", f"/api/worktree/{v['id']}")
    assert st == 200 and [f["path"] for f in out["diff"]["files"]] == ["feature.py"]
    assert not (repo / "feature.py").exists()
    s.state = "working"
    assert call("POST", f"/api/worktree/{v['id']}", {"action": "merge"})[0] == 409   # never mid-turn
    s.state = "idle"
    st, out = call("POST", f"/api/worktree/{v['id']}", {"action": "merge"})
    assert st == 200 and out["ok"] and out["files"] == ["feature.py"] and out["diff"]["files"] == []
    assert (repo / "feature.py").read_text() == "x = 1\n" and git(repo, "status", "--porcelain") == ""
    (Path(s.workdir) / "junk.py").write_text("junk\n")
    st, out = call("POST", f"/api/worktree/{v['id']}", {"action": "discard"})
    assert st == 200 and out["ok"] and out["diff"]["files"] == [] and not (Path(s.workdir) / "junk.py").exists()
    assert Path(s.workdir).is_dir()                       # discard keeps the copy: the chat goes on in it
    monkeypatch.setattr(A, "_saved", lambda sid: {"workdir": s.workdir})
    A.thread_action(v["id"], {"action": "delete"})
    assert WT.listing() == [] and not Path(wt["workdir"]).exists()
    assert "meshpool/" not in git(repo, "branch", "--list")


def test_merge_conflict_through_the_panel_is_reported(app, repo, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    v = A.create({"workdir": str(repo), "kind": "code", "task": "t", "worktree": True})
    (Path(v["workdir"]) / "app.py").write_text("print('copy')\n")
    (repo / "app.py").write_text("print('user')\n")
    git(repo, "commit", "-qam", "user")
    st, out = call("POST", f"/api/worktree/{v['id']}", {"action": "merge"})
    assert st == 200 and not out["ok"] and out["conflicts"] == ["app.py"]
    assert (repo / "app.py").read_text() == "print('user')\n"


def test_isolated_copy_is_for_code_chats_and_a_failed_chat_leaves_no_copy(app, repo, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    with pytest.raises(ValueError, match="Code chats"):
        A.create({"workdir": str(repo), "kind": "cowork", "task": "t", "worktree": True})

    def broken(*a, **k):
        raise RuntimeError("agent failed to start")
    monkeypatch.setattr(A, "Session", broken)
    with pytest.raises(RuntimeError):
        A.create({"workdir": str(repo), "kind": "code", "task": "t", "worktree": True})
    assert WT.listing() == []


def test_a_second_chat_in_the_same_folder_is_offered_an_isolated_copy(app, repo, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    st, out = call("GET", f"/api/worktree?workdir={repo}")
    assert st == 200 and out == {"repo": True, "users": [], "suggest": False}
    first = A.create({"workdir": str(repo), "kind": "code", "task": "one"})
    A.LIVE[first["id"]].state = "working"
    isolated = A.create({"workdir": str(repo), "kind": "code", "task": "two", "worktree": True})
    st, out = call("GET", f"/api/worktree?workdir={repo}")
    assert out["suggest"] and [u["id"] for u in out["users"]] == [first["id"]]     # the isolated one is not in the way
    assert out["users"][0]["state"] == "working" and isolated["id"] not in json.dumps(out)
    st, out = call("GET", f"/api/worktree?workdir={repo}&exclude={first['id']}")
    assert not out["suggest"]


def test_recover_at_start_drops_copies_of_chats_deleted_while_closed(app, repo, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    keep = A.create({"workdir": str(repo), "kind": "code", "task": "keep", "worktree": True})
    gone = A.create({"workdir": str(repo), "kind": "code", "task": "gone", "worktree": True})
    A.LIVE.clear()
    import shutil
    shutil.rmtree(A.SESSIONS / gone["id"])                # deleted by another app instance / by hand
    done = A.recover_worktrees()
    assert done["dead_owner"] == [gone["worktree"]["id"]]
    assert [r["id"] for r in WT.listing()] == [keep["worktree"]["id"]]


# ---------- scheduled runs -------------------------------------------------------------------------------------------
def test_scheduled_run_on_a_repo_runs_in_its_own_copy(app, repo, monkeypatch, tmp_path):
    from meshpool_harness import assistant as AS
    monkeypatch.setattr(A, "Session", FakeSession)
    monkeypatch.setattr(AS, "set_thread", lambda *a: None)
    sid = A.run_scheduled({"id": "t1", "name": "nightly", "prompt": "tidy up", "folder": str(repo), "kind": "code"})
    s = A.LIVE[sid]
    wt = A.read_meta(sid)["worktree"]
    assert s.workdir == wt["workdir"] and wt["source"] == str(repo.resolve()) and s.sent
    assert WT.get(wt["id"])["owner"]["kind"] == "chat" and WT.get(wt["id"])["owner"]["id"] == sid
    # opting out, and folders without git, run in the folder itself
    assert AS.run_folder({"folder": str(repo), "worktree": False}) == {"workdir": str(repo), "worktree_id": ""}
    plain = tmp_path / "plain"
    plain.mkdir()
    assert AS.run_folder({"folder": str(plain)}) == {"workdir": str(plain), "worktree_id": ""}
    assert AS.run_folder({"folder": ""}) == {"workdir": "", "worktree_id": ""}


def test_scheduled_run_that_fails_to_start_leaves_no_copy(app, repo, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("no agent")
    monkeypatch.setattr(A, "Session", broken)
    with pytest.raises(RuntimeError):
        A.run_scheduled({"id": "t1", "prompt": "p", "folder": str(repo)})
    assert WT.listing() == []


# ---------- sub-agents -------------------------------------------------------------------------------------------------
def _sub_env(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as K
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    for k in ("MH_SUBAGENTS", "MH_SUBAGENT_ACCESS", "MH_SUBAGENT_PARALLEL", "MH_SUBAGENT_DEPTH"):
        monkeypatch.delenv(k, raising=False)
    return K


def _fake_children(monkeypatch, K, writes):
    """Each sub-agent writes the files listed for its goal, then reports; it records what it saw of the others."""
    from tests.test_delegate import _goal, _is_child, _msg
    seen = {}

    def post(self, base, key, body, stream):
        if not _is_child(body):
            return _msg("parent done")
        goal = next(k for k in writes if _goal(body).startswith(k))
        if body["messages"][-1]["role"] == "tool":
            return _msg(f"report: {goal}")
        root = Path(self.root)
        seen[goal] = sorted(p.name for p in root.iterdir() if p.is_file())
        return _msg("", [("write_file", {"path": str(root / name), "content": text})
                         for name, text in writes[goal]])
    monkeypatch.setattr(K.Agent, "_post", post)
    return seen


def test_parallel_sub_agents_work_in_their_own_copies_and_the_turn_applies_them(repo, tmp_path, monkeypatch):
    K = _sub_env(tmp_path, monkeypatch)
    from meshpool_harness.agent import delegate as D
    (repo / "wip.txt").write_text("the chat's unsaved edit\n")      # sub-agents start from the turn's current files
    seen = _fake_children(monkeypatch, K, {"Write the alpha module": [("alpha.py", "a = 1\n")],
                                           "Write the beta module": [("beta.py", "b = 2\n")]})
    parent = K.Agent(str(repo), mode="auto")
    out, ok = D.run_tool(parent, {"tasks": [{"goal": "Write the alpha module"}, {"goal": "Write the beta module"}]})
    assert ok, out
    assert (repo / "alpha.py").read_text() == "a = 1\n" and (repo / "beta.py").read_text() == "b = 2\n"
    assert "wip.txt" in seen["Write the alpha module"] and "beta.py" not in seen["Write the alpha module"]
    data = json.loads(out.rsplit("```json\n", 1)[1].rstrip("`\n"))
    assert [r["changes_applied"] for r in data["results"]] == [True, True]
    assert data["results"][0]["files_written"] == [str(repo / "alpha.py")]        # the project's path, not the copy's
    assert WT.listing() == [] and "meshpool/" not in git(repo, "branch", "--list")
    assert git(repo, "log", "--oneline").count("\n") == 0                         # no commit: the turn's Undo covers it


def test_conflicting_sub_agents_are_reported_and_never_forced_in(repo, tmp_path, monkeypatch):
    K = _sub_env(tmp_path, monkeypatch)
    from meshpool_harness.agent import delegate as D
    _fake_children(monkeypatch, K, {"Fix the greeting one way": [("app.py", "print('one')\n")],
                                    "Fix the greeting another way": [("app.py", "print('two')\n")]})
    out, ok = D.run_tool(K.Agent(str(repo), mode="auto"),
                         {"tasks": [{"goal": "Fix the greeting one way"}, {"goal": "Fix the greeting another way"}]})
    assert (repo / "app.py").read_text() == "print('one')\n"
    data = json.loads(out.rsplit("```json\n", 1)[1].rstrip("`\n"))
    assert [r["changes_applied"] for r in data["results"]] == [True, False]
    assert data["results"][1]["conflicts"] == ["app.py"] and "NOT applied" in out
    kept = [b.strip() for b in git(repo, "branch", "--list", "meshpool/*").splitlines()]
    assert len(kept) == 1 and git(repo, "show", f"{kept[0]}:app.py") == "print('two')"
    assert WT.listing() == []


def test_a_single_or_read_only_sub_agent_shares_the_project(repo, tmp_path, monkeypatch):
    K = _sub_env(tmp_path, monkeypatch)
    from meshpool_harness.agent import delegate as D
    seen = _fake_children(monkeypatch, K, {"Write the alpha module": [("alpha.py", "a\n")]})
    made = []
    real = WT.create
    monkeypatch.setattr(WT, "create", lambda *a, **k: made.append(1) or real(*a, **k))
    D.run_tool(K.Agent(str(repo), mode="auto"), {"tasks": [{"goal": "Write the alpha module"}]})
    assert made == [] and (repo / "alpha.py").exists() and seen


# ---------- terminal ---------------------------------------------------------------------------------------------------
def test_meshpool_worktree_session_and_subcommands(repo, monkeypatch, capsys):
    from meshpool_harness import cli
    from meshpool_harness.agent import tui
    got = {}

    def fake_main(workdir, prompt, *a, **k):
        got["dir"] = workdir
        Path(workdir, "cli.txt").write_text("from the terminal\n")
        return 0
    monkeypatch.setattr(tui, "main", fake_main)
    monkeypatch.chdir(repo)
    assert cli.main(["--worktree", "-p", "add a file"]) == 0
    [rec] = WT.listing()
    assert got["dir"] == rec["workdir"] and not (repo / "cli.txt").exists()
    err = capsys.readouterr().err
    assert f"isolated copy ({rec['id']})" in err and f"meshpool worktree merge {rec['id']}" in err
    assert "1 file(s) changed" in err
    assert cli.main(["worktree", "list"]) == 0 and rec["id"] in capsys.readouterr().out
    assert cli.main(["worktree", "merge", rec["id"]]) == 0 and (repo / "cli.txt").exists()
    assert cli.main(["worktree", "discard", rec["id"]]) == 0 and WT.listing() == []
    with pytest.raises(SystemExit, match="cannot be combined"):
        cli.main(["--worktree", "-c"])
    assert cli.main(["worktree"]) == 2
    assert "worktree" in cli.COMMANDS


def test_worktree_routes_reach_their_module_not_the_work_page(app, repo):
    from meshpool_harness.ui import routes_extra as RX
    st, out = RX.handle("GET", urlparse(f"/api/worktree?workdir={repo}"))[:2]
    assert st == 200 and out["repo"] is True                 # "/api/work" is a prefix of "/api/worktree"
    st, out = RX.handle("GET", urlparse("/api/worktree/list"))[:2]
    assert st == 200 and out == {"items": []}
