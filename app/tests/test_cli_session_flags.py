"""Headless flags: --system-prompt(-file), --append-system-prompt-file, --session-id, --fork-session,
--no-session-persistence, and meshpool completion."""
import json
import os

import pytest

from meshpool_harness import cli
from meshpool_harness.agent import core as K
from meshpool_harness.agent.core import Agent


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    for k in ("MH_SYSTEM_PROMPT", "MH_APPEND_PROMPT", "MH_SESSION_ID", "MH_FORK_SESSION", "MH_NO_PERSIST"):
        monkeypatch.delenv(k, raising=False)
    import meshpool_harness.agent.tui as tui
    seen = {}
    monkeypatch.setattr(tui, "main", lambda *a, **k: seen.update(args=a, env={x: os.environ.get(x) for x in (
        "MH_SYSTEM_PROMPT", "MH_APPEND_PROMPT", "MH_SESSION_ID", "MH_FORK_SESSION", "MH_NO_PERSIST")}) or 0)
    yield seen
    for k in ("MH_SYSTEM_PROMPT", "MH_APPEND_PROMPT", "MH_SESSION_ID", "MH_FORK_SESSION", "MH_NO_PERSIST",
              "MH_NO_COMMANDS", "MH_STRICT_MCP", "MH_START_AGENT"):
        os.environ.pop(k, None)


def test_prompt_files_are_read(env, tmp_path):
    (tmp_path / "sys.md").write_text("You are a release bot.")
    (tmp_path / "more.md").write_text("Answer in one line.")
    assert cli.main(["-p", "hi", "--system-prompt-file", str(tmp_path / "sys.md"),
                     "--append-system-prompt", "Be brief.", "--append-system-prompt-file", str(tmp_path / "more.md")]) == 0
    assert env["env"]["MH_SYSTEM_PROMPT"] == "You are a release bot."
    assert env["env"]["MH_APPEND_PROMPT"] == "Be brief.\n\nAnswer in one line."


def test_a_missing_prompt_file_is_an_error(env, tmp_path):
    assert cli.main(["-p", "hi", "--system-prompt-file", str(tmp_path / "nope.md")]) == 2


def test_session_id_checks(env, tmp_path):
    assert cli.main(["-p", "hi", "--session-id", "bad id!"]) == 2
    assert cli.main(["-p", "hi", "--session-id", "sub-x"]) == 2
    assert cli.main(["-p", "hi", "--session-id", "nightly-42"]) == 0 and env["env"]["MH_SESSION_ID"] == "nightly-42"
    os.environ.pop("MH_SESSION_ID", None)
    (tmp_path / "sessions" / "taken").mkdir(parents=True)
    (tmp_path / "sessions" / "taken" / "session.json").write_text("{}")
    assert cli.main(["-p", "hi", "--session-id", "taken"]) == 2
    assert cli.main(["-p", "hi", "-c", "--session-id", "new-one"]) == 2          # needs --fork-session
    assert cli.main(["-p", "hi", "-c", "--fork-session", "--session-id", "new-one"]) == 0
    assert env["env"]["MH_FORK_SESSION"] == "1"


def test_fork_and_persistence_need_their_partners(env):
    assert cli.main(["-p", "hi", "--fork-session"]) == 2
    assert cli.main(["--no-session-persistence"]) == 2
    assert cli.main(["-p", "hi", "--no-session-persistence"]) == 0 and env["env"]["MH_NO_PERSIST"] == "1"


def test_system_prompt_override_keeps_the_data_rule(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MH_SYSTEM_PROMPT", "You only write haiku.")
    monkeypatch.setenv("MH_APPEND_PROMPT", "Sign with MP.")
    a = Agent(str(tmp_path), mode="auto")
    sp = a.messages[0]["content"]
    assert sp.startswith("You only write haiku.") and K.DATA_RULE in sp and sp.endswith("Sign with MP.")
    assert "MH_SYSTEM_PROMPT" not in os.environ                    # this run's agent only, not its sub-agents
    b = Agent(str(tmp_path), mode="auto")
    assert not b.messages[0]["content"].startswith("You only write haiku.")


def test_session_id_and_no_persistence(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MH_SESSION_ID", "chosen-1")
    a = Agent(str(tmp_path), mode="auto")
    assert a.id == "chosen-1"
    a.save()
    assert (tmp_path / "sessions" / "chosen-1" / "session.json").exists()
    monkeypatch.setenv("MH_NO_PERSIST", "1")
    b = Agent(str(tmp_path), mode="auto")
    b.save()
    assert not (b.dir / "session.json").exists()


def test_fork_leaves_the_old_session_alone(env, tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a.messages.append({"role": "user", "content": "first"})
    a.save()
    old = a.id
    r = Agent.resume(str(tmp_path))
    r.fork_id("forked-1")
    r.messages.append({"role": "user", "content": "second"})
    r.save()
    o = Agent.resume(str(tmp_path), old)                    # read back the way meshpool reads a session
    f = Agent.resume(str(tmp_path), "forked-1")
    assert [m["content"] for m in o.messages[1:]] == ["first"]
    assert [m["content"] for m in f.messages[1:]] == ["first", "second"] and f.id == "forked-1"


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_scripts(shell, capsys):
    assert cli.main(["completion", shell]) == 0
    out = capsys.readouterr().out
    assert "meshpool" in out and "worktree" in out and "session-id" in out


def test_completion_needs_a_shell(capsys):
    assert cli.main(["completion"]) == 2


def test_settings_file_json_or_lines(env, tmp_path):
    (tmp_path / "s.json").write_text('{"MH_MODEL": "acme/m1", "MH_HOOKS": false}')
    assert cli.main(["-p", "hi", "--settings", str(tmp_path / "s.json")]) == 0
    assert os.environ["MH_MODEL"] == "acme/m1" and os.environ["MH_HOOKS"] == "0"
    (tmp_path / "s.env").write_text("# mine\nMH_EFFORT=high\n")
    assert cli.main(["-p", "hi", "--settings", str(tmp_path / "s.env"), "--model", "acme/m2"]) == 0
    assert os.environ["MH_EFFORT"] == "high" and os.environ["MH_MODEL"] == "acme/m2"     # flags still win
    (tmp_path / "bad.json").write_text('{"PATH": "/evil"}')
    assert cli.main(["-p", "hi", "--settings", str(tmp_path / "bad.json")]) == 2
    for k in ("MH_MODEL", "MH_HOOKS", "MH_EFFORT"):
        os.environ.pop(k, None)


def test_mcp_add_json(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    assert cli.main(["mcp", "add-json", "files", '{"command": "npx", "args": ["-y", "srv"]}']) == 0
    assert cli.main(["mcp", "add-json", "docs", '{"mcpServers": {"x": {"url": "https://example.com/mcp"}}}']) == 0
    d = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]
    assert d["files"]["args"] == ["-y", "srv"] and d["docs"] == {"url": "https://example.com/mcp"}
    assert cli.main(["mcp", "add-json", "files", '{"command": "node"}']) == 0
    assert "Replaced" in capsys.readouterr().out
    assert cli.main(["mcp", "add-json", "bad", '{"args": []}']) == 2
    assert cli.main(["mcp", "add-json", "bad", "{nope"]) == 2
    assert cli.main(["mcp", "add-json", "bad name!", "{}"]) == 2


def test_strict_mcp_config_uses_only_this_runs_servers(env, tmp_path, monkeypatch):
    from meshpool_harness.agent import mcp as M
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"saved": {"command": "nothing-here-xyz"}}}))
    seen = {}

    class FakeServer:
        def __init__(self, name, spec, *a):
            seen[name] = spec
            self.name, self.status, self.error, self.tools = name, "error", "x", []

        def start(self, t):
            pass
    monkeypatch.setattr(M, "Server", FakeServer)
    monkeypatch.setattr(M, "record_health", lambda *a: None)
    monkeypatch.setattr(M, "active_config", lambda p=None: M.load_config(p))
    M.Manager(roots=[str(tmp_path)], extra={"run": {"command": "x"}}).start(timeout=1, use_health=False)
    assert set(seen) == {"saved", "run"}
    seen.clear()
    monkeypatch.setenv("MH_STRICT_MCP", "1")
    M.Manager(roots=[str(tmp_path)], extra={"run": {"command": "x"}}).start(timeout=1, use_health=False)
    assert set(seen) == {"run"}


def test_agent_flag_takes_on_a_saved_agent(env, tmp_path):
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "reader.md").write_text("---\nname: reader\ndescription: Reads only\ntools: Read, Grep\n"
                                              "model: acme/reader\n---\nYou only read and explain.\n")
    a = Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto")
    with pytest.raises(ValueError, match="No saved sub-agent named nobody"):
        a.as_agent("nobody")
    a.as_agent("Reader")
    assert "YOUR ROLE (reader)" in a.messages[0]["content"] and a.model == "acme/reader"
    names = {x["function"]["name"] for x in a.tool_schemas()}
    assert "read_file" in names and "grep" in names and "bash" not in names and "write_file" not in names
    assert a._permit("bash", {"command": "ls"})[0] is False
    assert a._permit("read_file", {"path": "x"})[0] is True
    assert cli.main(["-p", "hi", "--subagent", "reader"]) == 0 and os.environ.pop("MH_START_AGENT") == "reader"


def test_disable_slash_commands(env, tmp_path, monkeypatch):
    from meshpool_harness import skills as SK
    from meshpool_harness.agent import extras as EX
    monkeypatch.setattr(SK, "ensure_defaults", lambda: None)
    (tmp_path / "home" / "skills" / "x").mkdir(parents=True)
    (tmp_path / "home" / "skills" / "x" / "SKILL.md").write_text("---\nname: x\ndescription: Does x.\n---\nBody.")
    (tmp_path / ".meshpool" / "commands").mkdir(parents=True)
    (tmp_path / ".meshpool" / "commands" / "go.md").write_text("Expanded go command.")
    monkeypatch.setenv("MH_NO_COMMANDS", "1")
    a = Agent(str(tmp_path), mode="auto")
    names = {x["function"]["name"] for x in a.tool_schemas()}
    assert "use_skill" not in names and "Skills you can use" not in a.messages[0]["content"]
    sent = []
    monkeypatch.setattr(Agent, "_turn", lambda self: sent.append(self.messages[-1]["content"]) or "ok")
    a.send("/go now")
    assert "Expanded go command" not in str(sent[-1])
    assert cli.main(["-p", "hi", "--disable-slash-commands"]) == 0
