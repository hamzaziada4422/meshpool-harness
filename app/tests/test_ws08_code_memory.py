"""F-08: memory, schedules and chat search work in Code (Codex-style) chats; scheduled tasks keep the chat's mode."""
import sys

import pytest

from meshpool_harness import assistant as A
from meshpool_harness.agent import assistant_mcp_server as S
from meshpool_harness.agent import mcp as M
from meshpool_harness.agent.core import Agent
from meshpool_harness.ui import agent_api as API

NAMES = ["mcp__meshpool-assistant__memory_save", "mcp__meshpool-assistant__schedule_task",
         "mcp__meshpool-assistant__run_subtasks", "mcp__meshpool-web__web_search",
         "mcp__meshpool-office__make_deck", "mcp__github__x"]


class _Mcp:
    last_images: list = []

    def schemas(self):
        return [{"type": "function", "function": {"name": n, "description": "", "parameters": {"type": "object"}}}
                for n in NAMES]


def test_code_chat_keeps_assistant_tools(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(a, "_codex", lambda: True)
    a.mcp = _Mcp()
    got = {t["function"]["name"] for t in a.tool_schemas()}
    assert "mcp__meshpool-assistant__memory_save" in got and "mcp__meshpool-assistant__schedule_task" in got
    assert "mcp__meshpool-assistant__run_subtasks" not in got and "mcp__meshpool-web__web_search" not in got
    assert "mcp__meshpool-office__make_deck" not in got and "mcp__github__x" in got
    a.office_on = True
    assert "mcp__meshpool-office__make_deck" in {t["function"]["name"] for t in a.tool_schemas()}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(API, "SESSIONS", tmp_path / "sessions")
    (tmp_path / "sessions" / "chat-c").mkdir(parents=True)
    return tmp_path


def test_scheduled_task_takes_the_chats_kind(home, monkeypatch):
    API.write_meta("chat-c", kind="code")
    monkeypatch.setenv("MH_CHAT_ID", "chat-c")
    S.schedule_task("check build", "daily")
    monkeypatch.delenv("MH_CHAT_ID")
    S.schedule_task("morning news", "daily")
    S.schedule_task("explicit", "daily", mode="cowork")
    kinds = {t["prompt"]: t["kind"] for t in A.tasks()}
    assert kinds == {"check build": "code", "morning news": "cowork", "explicit": "cowork"}


def test_manager_passes_chat_to_the_assistant_server():
    m = M.Manager(roots=["."], chat="abc-1")
    spec = {"command": sys.executable, "args": ["-m", "meshpool_harness.agent.assistant_mcp_server"]}
    got = m._with_chat(spec)       # with each call (_chat, _project): one assistant process serves every chat
    assert got["chat_id"] == "abc-1" and got["per_call_project"] and "MH_CHAT_ID" not in (got.get("env") or {})
    assert "env" not in m._with_chat({"command": "x", "args": ["other"]})


def test_scheduled_task_takes_the_calling_chats_kind_and_folder(home, monkeypatch, tmp_path):
    from meshpool_harness.agent import mcp_stdio
    API.write_meta("chat-c", kind="code")
    monkeypatch.delenv("MH_CHAT_ID", raising=False)
    monkeypatch.setattr(mcp_stdio._CALL, "private", {"_chat": "chat-c", "_project": str(tmp_path)}, raising=False)
    S.schedule_task("per call", "daily")
    t = next(t for t in A.tasks() if t["prompt"] == "per call")
    assert t["kind"] == "code" and t.get("folder") == str(tmp_path)


def test_outside_code_agents_schedule_in_code_mode(home, monkeypatch, tmp_path):
    """Codex/OpenCode (ACP) get the chat id on the assistant server; Pi, a Code-only agent, gets kind=code."""
    import shutil
    from meshpool_harness import config as C
    from meshpool_harness.harnesses import acp, pi_rpc
    M.config_file().parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(C.APP / "defaults" / "mcp.json", M.config_file())   # the built-in servers, as on first run
    env = lambda row: {e["name"]: e["value"] for e in row.get("env") or []}   # noqa: E731
    rows = {r["name"]: r for r in acp.mcp_servers_for_acp([str(tmp_path)], "chat-x")}
    assert env(rows["meshpool-assistant"]).get("MH_CHAT_ID") == "chat-x"
    pi = pi_rpc._mcp_for_pi(tmp_path)["mcpServers"]
    assert pi["meshpool-assistant"]["env"].get("MH_CHAT_KIND") == "code"
    monkeypatch.setenv("MH_CHAT_KIND", "code")
    S.schedule_task("nightly lint", "daily")
    assert {t["prompt"]: t["kind"] for t in A.tasks()}["nightly lint"] == "code"
