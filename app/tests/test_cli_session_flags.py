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
    for k in ("MH_SYSTEM_PROMPT", "MH_APPEND_PROMPT", "MH_SESSION_ID", "MH_FORK_SESSION", "MH_NO_PERSIST"):
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
    o = json.loads((tmp_path / "sessions" / old / "session.json").read_text())
    f = json.loads((tmp_path / "sessions" / "forked-1" / "session.json").read_text())
    assert [m["content"] for m in o["messages"]] == ["first"]
    assert [m["content"] for m in f["messages"]] == ["first", "second"] and f["id"] == "forked-1"


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_scripts(shell, capsys):
    assert cli.main(["completion", shell]) == 0
    out = capsys.readouterr().out
    assert "meshpool" in out and "worktree" in out and "session-id" in out


def test_completion_needs_a_shell(capsys):
    assert cli.main(["completion"]) == 2
