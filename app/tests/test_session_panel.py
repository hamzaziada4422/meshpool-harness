"""The session side panel (environment, context, connectors, outputs, changes, background tasks) and GitHub sessions
(a repository cloned into a managed workspace, signing in without writing the token anywhere)."""
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W

TOKEN = "ghp_SECRETSECRETSECRET1234"


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(W, "_MODELS", {"t": 0, "key": "", "rows": [{"id": "big-model", "context": 32000}], "error": ""})
    from meshpool_harness import mcp_apps as CN
    monkeypatch.setattr(CN, "github_token", lambda: TOKEN)
    return tmp_path


@pytest.fixture
def repo(env):
    r = env / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "feat")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "init")
    git(r, "remote", "add", "origin", "https://github.com/o/r.git")
    (r / "a.txt").write_text("one\ntwo\n")
    return r


def session(root, events=(), mcp=None, state="idle", **kw):
    agent = SimpleNamespace(context=None, messages=[], current_model=lambda: "big-model", _delegations={})
    s = SimpleNamespace(id="s1", workdir=str(root), lock=threading.RLock(), events=[dict(e, t=e.get("t", time.time()), i=i)
                        for i, e in enumerate(events)], mcp=mcp, agent=agent, cp=None, state=state, scheduled=None,
                        stop=lambda: None)
    s.__dict__.update(kw)
    A.LIVE["s1"] = s
    return s


@pytest.fixture(autouse=True)
def clean():
    yield
    A.LIVE.pop("s1", None)
    W.TERMS.clear()


def test_environment_repo_branch_and_tree(repo):
    session(repo)
    d = W.session_panel("s1")
    assert d["environment"]["where"] == "computer" and d["environment"]["folder"] == str(repo)
    assert d["repo"]["slug"] == "o/r" and d["repo"]["url"] == "https://github.com/o/r"
    assert d["branch"]["name"] == "feat" and "feat" in d["branch"]["branches"]
    assert d["tree"]["files"] == 1


def test_managed_workspace_hides_the_folder(env):
    ws = W.workspaces_dir() / "o__r"
    ws.mkdir(parents=True)
    git(ws, "init", "-q")
    git(ws, "remote", "add", "origin", "git@github.com:o/r.git")
    session(ws)
    d = W.session_panel("s1")
    assert d["environment"]["managed"] and d["environment"]["folder"] == "" and d["environment"]["name"] == "o/r"
    assert d["repo"]["slug"] == "o/r"


def test_connectors_used_come_from_mcp_tool_names(repo):
    mcp = SimpleNamespace(status=lambda: [
        {"name": "github", "status": "connected", "tools": ["x", "y"], "error": ""},
        {"name": "notion", "status": "connected", "tools": ["z"], "error": ""},
        {"name": "off", "status": "disabled", "tools": [], "error": ""}])
    session(repo, [{"type": "tool_start", "name": "mcp__github__create_issue", "mcp": True},
                   {"type": "tool_start", "name": "mcp__github__list_prs", "mcp": True},
                   {"type": "tool_start", "name": "bash"}], mcp=mcp)
    c = W.session_panel("s1")["connectors"]
    by = {r["name"]: r for r in c["servers"]}
    assert by["github"]["used"] == 2 and by["notion"]["used"] == 0 and "off" not in by
    assert c["used"] == ["github"] and c["servers"][0]["name"] == "github"


def test_outputs_from_tool_calls_that_still_exist(repo):
    (repo / "made.md").write_text("x")
    (repo / "sub").mkdir()
    (repo / "sub" / "r.xlsx").write_text("x")
    session(repo, [{"type": "tool_start", "name": "write_file", "summary": str(repo / "made.md")},
                   {"type": "tool_start", "name": "edit", "summary": "a.txt"},
                   {"type": "tool_start", "name": "write_file", "summary": "gone.txt"},
                   {"type": "tool_start", "name": "write_file", "summary": "../../etc/passwd"},
                   {"type": "tool_end", "ok": True, "output": "Saved the sheet: sub/r.xlsx"}])
    outs = {o["path"]: o["how"] for o in W.session_panel("s1")["outputs"]}
    assert outs == {"made.md": "written", "a.txt": "edited", "sub/r.xlsx": "saved"}


def test_context_uses_last_usage_and_the_endpoint_window(repo):
    session(repo, [{"type": "usage", "in": 1000}, {"type": "usage", "in": 8000}])
    c = W.session_panel("s1")["context"]
    assert (c["used"], c["size"], c["percent"]) == (8000, 32000, 25) and c["source"].startswith("endpoint")


def test_context_prefers_what_the_harness_reports(repo):
    s = session(repo)
    s.agent.context = {"used": 50, "size": 200}
    assert W.session_panel("s1")["context"]["percent"] == 25


def test_context_estimates_from_messages_and_guesses_window(repo):
    s = session(repo)
    s.agent.current_model = lambda: "mystery"
    s.agent.messages = [{"role": "user", "content": "x" * 40000}]
    c = W.context_info(s)
    assert c["used"] == 10000 and c["size"] == 128000 and "estimate" in c["source"]
    assert W.model_window("claude-opus-x")[0] == 200000


def fake_api(draft=True, pr=True):
    def api(path, token, method="GET", body=None):
        if "head=o:feat" in path:
            return [{"number": 7}] if pr else []
        if path == "/repos/o/r/pulls/7":
            return {"number": 7, "title": "Add totals", "html_url": "https://github.com/o/r/pull/7", "state": "open",
                    "draft": draft, "merged_at": None, "head": {"ref": "feat", "sha": "abc"}, "base": {"ref": "main"},
                    "additions": 12, "deletions": 3, "changed_files": 2}
        if path.startswith("/repos/o/r/pulls?state=open"):
            return [{"number": 7, "title": "Add totals", "html_url": "u7", "draft": draft, "head": {"ref": "feat"}},
                    {"number": 9, "title": "Other", "html_url": "u9", "draft": False, "head": {"ref": "x"}}]
        if "/check-runs" in path:
            return {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "failure", "app": {}}]}
        if path.endswith("/status"):
            return {"statuses": []}
        raise AssertionError(path)
    return api


def test_pr_summary_from_a_faked_github(repo, monkeypatch):
    session(repo)
    monkeypatch.setattr(W, "_gh_api", fake_api())
    d = W.session_pr("s1")
    p = d["pr"]
    assert (p["number"], p["additions"], p["deletions"], p["draft"], p["can_ready"]) == (7, 12, 3, True, True)
    assert p["checks"] == "failing" and p["failing"] == ["tests"]
    assert [o["number"] for o in d["others"]] == [9]


def test_pr_summary_without_a_pr_or_remote(repo, monkeypatch):
    session(repo)
    monkeypatch.setattr(W, "_gh_api", fake_api(pr=False))
    d = W.session_pr("s1")
    assert d["available"] and d["pr"] is None and len(d["others"]) == 2
    git(repo, "remote", "set-url", "origin", "https://example.com/x.git")
    assert W.session_pr("s1")["available"] is False


def test_background_tasks_and_stop(repo):
    now = time.time()
    s = session(repo, [{"type": "subagent", "sid": "sa-1", "kind": "start", "title": "Research", "t": now - 5},
                       {"type": "subagent", "sid": "sa-2", "kind": "start", "title": "Old", "t": now - 9},
                       {"type": "subagent", "sid": "sa-2", "kind": "done", "t": now - 3}], state="working")
    W.TERMS["t1"] = {"id": "t1", "cwd": str(repo), "command": "npm run dev", "rc": None, "start": now - 20, "p": None}
    W.TERMS["t2"] = {"id": "t2", "cwd": str(repo), "command": "ls", "rc": 0, "start": now - 30, "p": None}
    W.TERMS["t3"] = {"id": "t3", "cwd": "/elsewhere", "command": "x", "rc": None, "start": now, "p": None}
    bg = W.session_panel("s1")["background"]
    assert sorted(r["id"] for r in bg["running"]) == ["sa-1", "t1"] and bg["finished"] == 2
    stopped = []
    rec = {"stop": False, "agent": SimpleNamespace(stop=lambda: stopped.append(1))}
    s.agent._delegations = {"d": SimpleNamespace(lock=threading.Lock(), children={"sa-1": rec})}
    W.session_bg_stop("s1", "subagent", "sa-1")
    assert rec["stop"] and stopped
    with pytest.raises(ValueError):
        W.session_bg_stop("s1", "terminal", "t3")


def test_clone_command_never_carries_the_token(env):
    argv = W.clone_argv("o/r", Path("/dest"))
    assert not any(TOKEN in a for a in argv) and "https://github.com/o/r.git" in argv
    assert "credential.helper=" in argv and any("MESHPOOL_GH_TOKEN" in a for a in argv)
    # the token helper answers for github.com only, never for another host the remote might be changed to
    helper = [a for a in argv if "MESHPOOL_GH_TOKEN" in a]
    assert helper and all(a.startswith("credential.https://github.com.helper=") for a in helper)
    assert W._gh_git_env({})["MESHPOOL_GH_TOKEN"] == TOKEN
    for bad in ("o", "o/r;rm", "../x/y z", "-x/y"):
        with pytest.raises(ValueError):
            W.clone_argv(bad, Path("/d"))


def test_github_workspace_clones_into_managed_folder_without_leaking(env, monkeypatch):
    real, seen = subprocess.run, []

    def run(argv, *a, **kw):
        if "clone" in argv:
            seen.append((argv, kw.get("env")))
            dest = argv[-1]
            real(["git", "init", "-q", dest], capture_output=True, check=True)
            real(["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=dest, check=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, *a, **kw)
    monkeypatch.setattr(W.subprocess, "run", run)
    r = W.github_workspace({"repo": "o/r"})
    assert W.is_managed(r["path"]) and r["name"] == "o/r" and not r["reused"]
    argv, e = seen[0]
    assert not any(TOKEN in a for a in argv) and e["MESHPOOL_GH_TOKEN"] == TOKEN
    assert TOKEN not in (Path(r["path"]) / ".git" / "config").read_text()
    assert [p["name"] for p in W.projects() if p["managed"]] == ["o/r"]
    # a second session reuses the copy (fetch, no clone); the fetch signs in the same way
    fetched = []
    monkeypatch.setattr(W, "_git", lambda root, *a, **k: fetched.append(a) or ("https://github.com/o/r.git" if a[0] == "remote" else ""))
    assert W.github_workspace({"repo": "o/r"})["reused"] and any(a[0] == "fetch" for a in fetched)


def test_git_network_commands_sign_in_only_in_managed_workspaces(env, monkeypatch):
    calls = []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: calls.append((argv, kw["env"])) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    ws = W.workspaces_dir() / "o__r"
    ws.mkdir(parents=True)
    other = env / "mine"
    other.mkdir()
    W._git(ws, "push")
    W._git(other, "push")
    assert "credential.helper=" in calls[0][0] and calls[0][1]["MESHPOOL_GH_TOKEN"] == TOKEN
    assert "credential.helper=" not in calls[1][0] and "MESHPOOL_GH_TOKEN" not in calls[1][1]
    assert not any(TOKEN in a for a in calls[0][0])


def test_branch_create_and_missing(env, monkeypatch):
    real = subprocess.run

    def run(argv, *a, **kw):
        if "clone" in argv:
            dest = argv[-1]
            real(["git", "init", "-q", "-b", "main", dest], capture_output=True, check=True)
            real(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "i"], cwd=dest, check=True)
            real(["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=dest, check=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "fetch" in argv:                      # never reach the real GitHub from a test
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, *a, **kw)
    monkeypatch.setattr(W.subprocess, "run", run)
    with pytest.raises(ValueError, match="no branch"):
        W.github_workspace({"repo": "o/r", "branch": "nope"})
    assert W.github_workspace({"repo": "o/r", "branch": "work", "create": True})["branch"] == "work"
    with pytest.raises(ValueError, match="valid branch"):
        W.github_workspace({"repo": "o/r", "branch": "-bad"})


def test_repo_list_filters_and_needs_a_token(env, monkeypatch):
    W._REPOS.update(t=0, tok="", rows=[])
    monkeypatch.setattr(W, "_gh_all", lambda path, token, limit=300: [
        {"full_name": "o/alpha", "private": True, "description": "Totals", "pushed_at": "x", "default_branch": "main"},
        {"full_name": "o/beta", "description": None, "default_branch": "dev"}])
    assert [r["full_name"] for r in W.github_repos("alp")["repos"]] == ["o/alpha"]
    assert len(W.github_repos("")["repos"]) == 2
    from meshpool_harness import mcp_apps as CN
    monkeypatch.setattr(CN, "github_token", lambda: "")
    with pytest.raises(ValueError, match="Connect GitHub"):
        W.github_repos("")


def test_outputs_skip_hidden_and_cache_folders(repo):
    (repo / ".ruff_cache").mkdir()
    (repo / ".ruff_cache" / "x").write_text("x")
    (repo / "keep.txt").write_text("x")
    session(repo, [{"type": "tool_start", "name": "write_file", "summary": ".ruff_cache/x"},
                   {"type": "tool_start", "name": "write_file", "summary": "keep.txt"}])
    assert [o["path"] for o in W.session_panel("s1")["outputs"]] == ["keep.txt"]
