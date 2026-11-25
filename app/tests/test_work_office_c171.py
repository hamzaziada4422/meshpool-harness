"""MH_WORK_OFFICE_STYLE=c171: a Work chat's office edit, or a file the user names, sends the request Codex CLI 0.171
sends (its prompt, its one command tool), with none of Meshpool's office notes, checks or caps. A new deck, report or
model and Word tracked changes keep the Work route. No network and no model calls (the transport is scripted)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.hrouter import profiles as PR  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    for var in ("MH_ENGINE_STYLE", "MH_HARNESS_PROFILE", "MH_CORE_PROMPT_FILE", "MH_APPEND_PROMPT",
                "MH_OFFICE_C171_CHECKS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    monkeypatch.setenv("MH_OFFICE_DIRECT", "1")
    monkeypatch.setenv("MH_WORK_OFFICE_STYLE", "c171")


def _work(tmp_path):
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None)
    a.set_route(Route(spec="data", family="data", base="work", profile="core", settings=PR.settings("core")), "x")
    return a


def _turn(a, text):
    bodies = []

    def fake(base, key, body, stream):
        bodies.append(json.loads(json.dumps(body)))
        return {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, True
    a._post = fake
    a.send(text)
    return bodies[0]


def test_an_office_edit_sends_codex_171s_request(tmp_path):
    a = _work(tmp_path)
    assert a.office_c171_for("Sort data.xlsx by date and save it as out.xlsx")
    body = _turn(a, "Sort data.xlsx by date and save it as out.xlsx")
    assert [t["function"]["name"] for t in body["tools"]][0] == "exec_command"
    assert not any(t["function"]["name"].startswith("mcp__meshpool_office__") for t in body["tools"])
    assert body["messages"][0]["content"] == core._c171_prompt()                    # Codex's own instructions
    assert "<environment_context>" in body["messages"][1]["content"]
    assert a._c171() and not a._office_direct() and not a._lean()                # no office notes, checks or caps


def test_a_data_question_keeps_the_lean_work_route(tmp_path):
    a = _work(tmp_path)
    assert not a.office_c171_for("What is the total amount in orders.csv?")
    body = _turn(a, "What is the total amount in orders.csv?")
    assert body["messages"][0]["content"] != core._c171_prompt() and not a._c171()


def test_a_new_deck_or_tracked_changes_keep_the_work_route(tmp_path):
    a = _work(tmp_path)
    assert not a.office_c171_for("Make a 10-slide pitch deck for investors")
    assert not a.office_c171_for("Edit contract.docx with tracked changes: replace net 30 with net 45")
    a.office_engines = True                       # an earlier message brought the engines: they stay
    assert not a.office_c171_for("Sort data.xlsx by date")


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_WORK_OFFICE_STYLE")
    a = _work(tmp_path)
    assert not a.office_c171_for("Sort data.xlsx by date")
    _turn(a, "Sort data.xlsx by date")
    assert not a._c171() and a._office_direct()


def test_switching_routes_rewrites_the_system_prompt(tmp_path):
    a = _work(tmp_path)
    lean = a.messages[0]["content"]
    _turn(a, "Sort data.xlsx by date")
    assert a.messages[0]["content"] == core._c171_prompt() != lean
    _turn(a, "Now make a 10-slide pitch deck for investors from it")     # the engines: the Work route again
    assert not a.office_c171 and a.messages[0]["content"] != core._c171_prompt()


def test_the_session_adds_no_office_notes_to_a_codex_request():
    from meshpool_harness.ui.agent_api import Session
    agent = SimpleNamespace(office_c171_for=lambda t: "xlsx" in t)
    s = SimpleNamespace(harness={"type": "meshpool"}, agent=agent)
    assert Session._codex_office(s, "Sort data.xlsx") is True
    assert Session._codex_office(s, "Summarise notes.txt") is False
    s.harness = {"type": "pi"}
    assert Session._codex_office(s, "Sort data.xlsx") is False
