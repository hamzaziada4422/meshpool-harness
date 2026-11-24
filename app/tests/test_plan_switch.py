"""Switching plan mode while a turn runs never changes that turn's tools: it applies from the next message.
No network, no real model."""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402
from test_codex_engine import _agent, _call, _script  # noqa: E402
from test_ui_new_chat import app, page  # noqa: E402,F401  (the real app server and a Chromium page)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    monkeypatch.delenv("MH_ENGINE_STYLE", raising=False)
    monkeypatch.delenv("MH_HARNESS_PROFILE", raising=False)


def test_plan_toggle_mid_turn_keeps_turn_tools(tmp_path):
    a = _agent(tmp_path)
    _script(a, [_call("shell", {"command": ["bash", "-lc", "echo one"]}),
                _call("shell", {"command": ["bash", "-lc", "touch made.txt"]}, "c2"),
                {"role": "assistant", "content": "done"}])
    real = a._post

    def post(base, key, body, stream):
        if len(a.bodies) == 1:
            a.plan = True                            # the user flips plan mode while the turn runs
        return real(base, key, body, stream)
    a._post = post
    a.send("go")
    tools = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert not any("unknown tool" in t.lower() or "plan mode" in t.lower() for t in tools)
    assert (tmp_path / "made.txt").exists()
    names = [{t["function"]["name"] for t in b["tools"]} for b in a.bodies]
    assert all("shell" in n for n in names) and names[0] == names[-1]
    assert a.plan is True                            # applied when the turn ended
    a.plan = False                                   # between turns it applies at once
    assert a.plan is False


def test_agent_plan_is_plain_outside_a_turn(tmp_path):
    a = _agent(tmp_path)
    a.plan = True
    assert a.plan is True and a._plan_next is None
    a.plan = False
    assert a.plan is False


def _session(state):
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.id, s.plan, s.state = [], threading.Lock(), "t-plan", False, state
    s.emit = lambda ev: s.events.append(ev)
    s.agent = type("Ag", (), {"plan": False, "modes": 0, "apply_mode": lambda self: setattr(self, "modes", self.modes + 1)})()
    return s


def test_session_set_plan_while_working_waits_for_next_turn(monkeypatch):
    meta = []
    monkeypatch.setattr(A, "write_meta", lambda sid, **kw: meta.append(kw))
    s = _session("working")
    s.set_plan(True)
    assert s.plan is True and meta == [{"plan": True}]
    assert s.agent.plan is False and s.agent.modes == 0           # the running turn is untouched
    assert s.events[-1] == {"type": "status", "text": "Plan mode turns on from your next message."}
    s.set_plan(False)
    assert s.events[-1]["text"] == "Plan mode turns off from your next message."
    s2 = _session("idle")                                          # between turns it applies at once
    s2.set_plan(True)
    assert s2.agent.plan is True and s2.agent.modes == 1


# ---------- the "Build this plan" card ---------------------------------------------------------------------------------

def test_build_card_only_on_plan_turns(page):
    """The card belongs to a turn that was sent in plan mode, not to the chat's mode when the page is drawn."""
    page.evaluate("""() => { openThread("t-card", true); S.cur.pollT && clearInterval(S.cur.pollT); S.cur.pollT = null;
        S.cur.view.plan = true; }""")      # the chat is in plan mode NOW, as after a reload

    def turn(plan, text):
        page.evaluate("""([plan, text]) => {
            applyEv({type: "user", text: "ask", plan});
            applyEv({type: "assistant", text, streamed: false, has_tools: false});
            applyEv({type: "turn_end", turn: {}, session: {}});
        }""", [plan, text])
    turn(False, "An old answer")
    assert page.locator(".buildplan").count() == 0       # old answers get no card just because plan mode is on now
    turn(True, "1. do it")
    assert page.locator(".buildplan").count() == 1
    turn(False, "A later answer")                         # a new message removes the card and shows none
    assert page.locator(".buildplan").count() == 0
    assert page.errors == [], page.errors
