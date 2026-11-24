"""Fixes for the independent review of the feature-ledger work. Each test is one finding, with its attack input."""
import json
import os
import subprocess

import pytest

from meshpool_harness import trust as TR
from meshpool_harness.agent import core
from meshpool_harness.core import tools as T


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path


@pytest.mark.parametrize("cmd", [
    "git log -1 --format=%x65cho%x20pwned ${HOME:+--output=$HOME/.bashrc}",       # H1: shell expansion
    "git log -1 --format=tformat:hi $'--output=/tmp/x'",                         # H1: ANSI-C quoting
    "git grep -Otouch foo", "git grep -iOtouch foo", "git grep --ope=touch x",   # H2: pager runs a program
    "git diff --no-index /etc/passwd /dev/null", "git blame --contents /etc/hostname a.py",   # M1: reads outside
    "git diff -- ../../etc/passwd", "git show HEAD:/etc/passwd", "git log ~/x",
    "git log (rm x)", "git log @(rm x)", "git log %HOME%",                       # M8: PowerShell and cmd syntax
    "git log --outp=x", "git remote show origin", "git stash", "git branch new", "git diff --ext-diff",
])
def test_git_fast_path_refuses_every_attack(cmd):
    assert not T.git_read_only(cmd)


def test_git_fast_path_still_covers_everyday_reads():
    for c in ("git status", "git status -s", "git log --oneline -5", "git log -n5", "git diff HEAD~1",
              "git diff --cached", "git show HEAD --stat", "git branch -a", "git tag -l", "git remote -v",
              "git stash list", "git blame a.py", "git log -- src/a.py", "git diff main...HEAD", "git log --format=%h"):
        assert T.git_read_only(c), c


def test_git_fast_path_needs_a_harmless_repo_config(env):
    r = env / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    assert T.git_config_safe(r)
    subprocess.run(["git", "-C", str(r), "config", "core.fsmonitor", "touch pwned"], check=True)   # M2
    assert not T.git_config_safe(r)
    a = core.Agent(str(r), approve=lambda *x: ("no", ""), mode="ask")
    assert not a._permit("bash", {"command": "git status"})[0]


def test_cloned_workspaces_in_the_data_folder_are_not_trusted(env):         # H3
    ws = env / "home" / "workspaces" / "someone__repo"
    (ws / ".meshpool").mkdir(parents=True)
    (ws / ".meshpool" / "hooks.json").write_text(json.dumps({"SessionStart": [{"command": "true"}]}))
    assert not TR.is_trusted(ws) and TR.needs_prompt(ws)
    assert TR.is_trusted(env / "home")


def test_saved_rules_are_narrow(env):                                         # M3
    rf = core.rule_for
    assert rf("bash", {"command": "python -m pytest -q"}) == ["bash:python -m pytest", "bash:python -m pytest *"]
    assert rf("bash", {"command": "node build.js --prod"}) == ["bash:node build.js", "bash:node build.js *"]
    for c in ("bash scripts/x.sh", "env FOO=1 make", "sudo make install", "git -C x log", "npx something",
              "python -c 'print(1)'", "make && make install"):
        assert rf("bash", {"command": c}) == [], c
    assert rf("bash", {"command": "npm test"}) == ["bash:npm test", "bash:npm test *"]
    proj = env / "p"
    proj.mkdir()
    core.save_folder_rules(proj, rf("bash", {"command": "npm test"}))
    b = core.Agent(str(proj), approve=lambda *x: ("no", ""), mode="ask")
    assert b._permit("bash", {"command": "npm test"})[0] and b._permit("bash", {"command": "npm test -- --ci"})[0]
    assert not b._permit("bash", {"command": "npm testify"})[0]            # a word boundary, not a prefix


def test_save_with_nothing_narrow_is_allowed_once_only(env):                  # M4
    a = core.Agent(str(env), approve=lambda *x: ("save", ""), mode="ask")
    assert a._permit("bash", {"command": "make && make install"}) == (True, "")
    a.approve = lambda *x: ("no", "")
    assert not a._permit("bash", {"command": "ls -la"})[0]                 # nothing wider was allowed


def test_power_files_always_ask(env):                                         # M6
    TR.trust(env)
    asked = []
    a = core.Agent(str(env), approve=lambda *x: (asked.append(x[1]), ("no", ""))[1], mode="edits")
    ok, _ = a._permit("write_file", {"path": ".meshpool/hooks.json", "content": "{}"})
    assert not ok and "hooks.json" in asked[-1]
    assert not a._permit("edit", {"path": ".mcp.json", "old": "a", "new": "b"})[0]
    assert a._permit("write_file", {"path": "notes.md", "content": "x"})[0]   # other edits still go through
    a.mode = "auto"
    assert not a._permit("write_file", {"path": str(env / "home" / "hooks.json"), "content": "{}"})[0]


def test_malformed_permissions_file_is_never_overwritten(env):
    f = env / "home" / "permissions.json"
    f.write_text("{ not json", encoding="utf-8")
    assert core.save_folder_rules(env, ["bash:npm test"]) == []
    assert f.read_text() == "{ not json"


def test_chat_only_undo_trims_what_the_model_sees(env, monkeypatch):           # M5
    from meshpool_harness.ui import agent_api as A
    proj = env / "proj"
    proj.mkdir()
    s = A.Session(workdir=str(proj), mode="auto", harness="meshpool-code")
    replies = iter(["first answer", "second answer"])
    s.agent._post = lambda *x: ({"choices": [{"message": {"role": "assistant", "content": next(replies)},
                                              "finish_reason": "stop"}], "usage": {}}, False)
    for text in ("one", "two"):
        s.send(text)
        for _ in range(200):
            if s.state == "idle" and sum(1 for e in s.events if e["type"] == "turn_end") >= (1 if text == "one" else 2):
                break
            import time
            time.sleep(0.05)
    assert any("second answer" == m.get("content") for m in s.agent.messages)
    s.revert(2, "chat")
    assert not any("second answer" == m.get("content") for m in s.agent.messages)
    assert any("first answer" == m.get("content") for m in s.agent.messages)
    s.close()


def test_reasoning_is_saved_once_per_call(env, monkeypatch):                   # M7
    import io

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    chunks = [{"choices": [{"delta": {"reasoning_content": f"part {i} "}}]} for i in range(50)]
    chunks.append({"choices": [{"delta": {"content": "Answer."}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    monkeypatch.setattr(core, "_urlopen", lambda req, t, sink: Resp(body.encode()))
    a = core.Agent(str(env), mode="auto")
    seen = []
    a.emit = seen.append
    a._post("http://x/v1", "k", {"model": "m", "messages": []}, True)
    th = [e for e in seen if e.get("type") == "thought"]
    assert sum(1 for e in th if e.get("delta")) == 50
    final = [e for e in th if e.get("final")]
    assert len(final) == 1 and final[0]["text"].startswith("part 0") and "part 49" in final[0]["text"]
