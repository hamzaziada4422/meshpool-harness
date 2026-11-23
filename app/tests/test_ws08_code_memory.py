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
    assert m._with_chat(spec)["env"]["MH_CHAT_ID"] == "abc-1"
    assert "env" not in m._with_chat({"command": "x", "args": ["other"]})
