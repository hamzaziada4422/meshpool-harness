"""Agent engine fixes from the feature ledger: read-only git never asks, search respects .gitignore and skips binary
files, read_file says when a file is binary."""
import subprocess

from meshpool_harness.core import tools as T


def _repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / ".gitignore").write_text("build/\n*.log\n", encoding="utf-8")
    (d / "build").mkdir()
    (d / "build" / "gen.py").write_text("needle = 1\n", encoding="utf-8")
    (d / "a.py").write_text("needle = 2\n", encoding="utf-8")
    (d / "run.log").write_text("needle in a log\n", encoding="utf-8")
    (d / "blob.dat").write_bytes(b"needle\0\x01\x02" * 10)
    return d


def test_git_read_only_classifier():
    yes = ["git status", "git log --oneline -5", "git diff HEAD~1", "git log -p", "git show HEAD", "git branch",
           "git branch -a", "git tag -l", "git remote -v", "git stash list", "git --no-pager diff", "git blame a.py"]
    no = ["git branch new", "git branch -D x", "git tag v1", "git -c core.pager=sh log", "git diff --output=x",
          "git status; rm -rf /", "git status && ls", "git remote add o u", "git stash", "git push", "git log | head",
          "git show --ext-diff", "git reflog expire --all", "git log $(rm x)", "git commit -m x", "git checkout .",
          "git -C /etc status", "gitk", ""]
    assert all(T.git_read_only(c) for c in yes), [c for c in yes if not T.git_read_only(c)]
    assert not any(T.git_read_only(c) for c in no), [c for c in no if T.git_read_only(c)]


def test_read_only_git_skips_the_question(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness.agent import core
    d = _repo(tmp_path)
    asked = []
    a = core.Agent(str(d), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="ask")
    assert a._permit("bash", {"command": "git status"}) == (True, "")
    assert a._permit("bash", {"command": "git log --oneline -3"}) == (True, "")
    assert not asked
    assert not a._permit("bash", {"command": "git commit -am wip"})[0] and asked


def test_grep_and_glob_respect_gitignore_and_skip_binaries(tmp_path):
    d = _repo(tmp_path)
    ws = T.Workspace(str(d), str(tmp_path / "run"))
    out = ws.grep("needle", output_mode="files_with_matches")
    assert out.split("\n") == ["a.py"], out
    assert "gen.py" in ws.grep("needle", path="build", output_mode="files_with_matches")   # asked for on purpose
    assert ws.glob("**/*.py").split("\n") == ["a.py"]
    assert "is a binary file" in ws.read_file("blob.dat")
    assert "needle = 2" in ws.read_file("a.py")


def test_outside_git_nothing_is_filtered(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (d / "x.log").write_text("needle\n", encoding="utf-8")
    ws = T.Workspace(str(d), str(tmp_path / "run"))
    assert "x.log" in ws.grep("needle", output_mode="files_with_matches")


def test_every_system_prompt_says_tool_output_is_data(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness.agent import core
    for profile in ("code", "work"):
        assert core.DATA_RULE in core.Agent(str(tmp_path), profile=profile).system_prompt()


def _costed(agent, n_tools, cost):
    """A model that reads a file n_tools times and then answers, each call billed at `cost` (usage.cost)."""
    import json as _j
    seq = [{"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {
        "name": "glob", "arguments": _j.dumps({"pattern": "*"})}}]} for i in range(n_tools)]
    seq.append({"role": "assistant", "content": "done"})

    def fake(base, key, body, stream):
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": cost}}, False
    agent._post = fake


def test_session_cost_comes_from_the_provider_and_the_task_limit_stops_a_chat(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_s")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_BUDGET_USD", raising=False)
    a = core.Agent(str(tmp_path), mode="auto")
    _costed(a, 2, 0.01)
    assert a.send("go") == "done"
    assert abs(a.usage["cost"] - 0.03) < 1e-9
    monkeypatch.setenv("MH_BUDGET_USD", "0.025")
    b = core.Agent(str(tmp_path), mode="auto")
    events = []
    b.emit = events.append
    _costed(b, 10, 0.01)
    b.send("go")
    errs = [e["text"] for e in events if e.get("type") == "error"]
    assert errs and "spending limit of $" in errs[0] and b.usage["calls"] == 3
    assert b.unfinished


def test_cost_from_gateway_header_and_user_prices(monkeypatch):
    from meshpool_harness.agent import core

    class R:
        headers = {"x-litellm-response-cost": "0.0042"}
    resp = {"usage": {"prompt_tokens": 5}}
    core._header_cost(R(), resp)
    assert core._call_cost(resp["usage"]) == 0.0042
    monkeypatch.delenv("MH_PRICE_IN", raising=False)
    monkeypatch.delenv("MH_PRICE_OUT", raising=False)
    assert core._call_cost({"prompt_tokens": 1000}) is None
    monkeypatch.setenv("MH_PRICE_IN", "1")
    monkeypatch.setenv("MH_PRICE_OUT", "2")
    assert core._call_cost({"prompt_tokens": 1_000_000, "completion_tokens": 500_000}) == 2.0


def test_refusal_and_content_filter_are_said_plainly(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_s")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    for msg, fin in (({"role": "assistant", "content": ""}, "content_filter"),
                     ({"role": "assistant", "content": None, "refusal": "I can't help with that."}, "stop")):
        a = core.Agent(str(tmp_path), mode="auto")
        a._post = lambda base, key, body, stream, m=msg, f=fin: ({"choices": [{"message": dict(m), "finish_reason": f}],
                                                                  "usage": {}}, False)
        out = a.send("hi")
        assert "declined to answer" in out, out
