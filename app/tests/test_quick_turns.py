"""Quick turns: a real task added to a quick turn gets a full budget; the wrap is enforced even when the model
refuses tool_choice; the Pi wrap is a model-only nudge that never reaches another turn. No network, no real model."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import pace as PACE  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    core._REFUSED.clear()
    yield
    core._REFUSED.clear()


def _call(i, cmd="echo hi"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}]}


def _script(a, replies, on_call=None):
    seq, a.bodies = list(replies), []

    def fake(base, key, body, stream):
        a.bodies.append(json.loads(json.dumps(body)))
        if on_call:
            on_call(len(a.bodies))
        msg = seq.pop(0) if seq else {"role": "assistant", "content": "end"}
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake


def _wrapped(a):
    return any(PACE.WRAP in str(m.get("content")) for b in a.bodies for m in b["messages"])


def _quick(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a.pace_deadline = time.time() + PACE.QUICK_S
    return a


TASK = "Please create a report.docx with the full summary and the sources"


def test_task_added_to_quick_turn_gets_full_budget(tmp_path):
    a = _quick(tmp_path)
    _script(a, [_call(i, f"echo {i}") for i in range(5)] + [{"role": "assistant", "content": "all done"}],
            on_call=lambda n: a.steer(TASK) if n == 1 else None)
    assert a.send("what is the capital of France?") == "all done"
    assert not _wrapped(a) and all(b["tool_choice"] == "auto" for b in a.bodies)
    assert a.pace_deadline == 0


def test_task_added_after_the_wrap_lifts_it_with_a_note(tmp_path):
    a = _quick(tmp_path)
    _script(a, [_call(i, f"echo {i}") for i in range(5)] + [{"role": "assistant", "content": "ok"}],
            on_call=lambda n: a.steer(TASK) if n == 4 else None)
    a.send("what is the capital of France?")
    users = [m["content"] for m in a.messages if m["role"] == "user" and isinstance(m["content"], str)]
    assert any("no longer a quick question" in u for u in users)
    assert a.bodies[-1]["tool_choice"] == "auto" and a._pace_wrapped is False


def test_task_sent_as_quick_turn_ends_gets_full_budget(tmp_path):
    a = _quick(tmp_path)
    _script(a, [{"role": "assistant", "content": "Paris."}])
    a.send("what is the capital of France?")
    a.pace_deadline = time.time() - 1                # the quick budget has run out
    a.steer(TASK)                                    # arrives just as the turn ended
    _script(a, [_call(i, f"echo {i}") for i in range(5)] + [{"role": "assistant", "content": "done it"}])
    assert a.resume_steers() == "done it"
    assert not _wrapped(a) and a.pace_deadline == 0


def test_quick_followup_keeps_quick_budget(tmp_path):
    a = _quick(tmp_path)
    _script(a, [{"role": "assistant", "content": "Paris."}])
    a.send("what is the capital of France?")
    a.pace_deadline = time.time() - 1
    a.steer("and what about Spain?")
    _script(a, [_call(i, f"echo {i}") for i in range(2)] + [{"role": "assistant", "content": "Madrid."}])
    assert a.resume_steers() == "Madrid."
    assert a.pace_deadline > time.time() + PACE.QUICK_S - 5      # a fresh quick budget, not the stale one
    _script(a, [_call(i, f"echo {i}") for i in range(5)] + [{"role": "assistant", "content": "Madrid."}])
    a.steer("and what about Italy?")
    a.pace_deadline = time.time() + PACE.QUICK_S
    a.resume_steers()
    assert _wrapped(a)                                           # the quick limits still apply


def test_quick_wrap_enforced_when_tool_choice_refused(tmp_path):
    a = _quick(tmp_path)
    core._REFUSED[a.current_model()] = {"tool_choice"}
    marker = tmp_path / "ran.txt"
    _script(a, [_call(i, f"echo {i}") for i in range(3)]
            + [_call(3, f"touch {marker}"), _call(4, f"touch {marker}")])
    out = a.send("what is the capital of France?")
    assert not marker.exists()                                   # calls on the tools-off steps never ran
    assert "tool_choice" not in a.bodies[-1]                     # the model refused it: no way to ask for tools off
    res = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert sum("Not run: time is up for this quick request" in r for r in res) == 2
    assert out == "(I ran out of time for this quick request. Ask again to let me look further.)"
    ids = {c["id"] for m in a.messages for c in m.get("tool_calls") or []}
    assert ids == {m["tool_call_id"] for m in a.messages if m["role"] == "tool"}      # every call has its result
    assert len(a.bodies) == 5


def test_quick_wrap_text_after_a_refused_call_is_the_reply(tmp_path):
    a = _quick(tmp_path)
    core._REFUSED[a.current_model()] = {"tool_choice"}
    _script(a, [_call(i, f"echo {i}") for i in range(4)] + [{"role": "assistant", "content": "Paris."}])
    assert a.send("what is the capital of France?") == "Paris."


# ---------- Pi: the wrap is a nudge, not a chat message --------------------------------------------------------------

def _pi(tmp_path):
    from meshpool_harness.harnesses.pi_rpc import PiAgent
    p = PiAgent.__new__(PiAgent)
    p.events, p.plan, p._alive = [], False, True
    p.sent = []
    p._request = lambda cmd, timeout=30: p.sent.append(cmd) or {}
    p._emit = lambda ev: p.events.append(ev)
    return p


def test_pi_quick_wrap_is_not_a_user_message(tmp_path):
    p = _pi(tmp_path)
    p._turn_seq, p._turn_live = 1, True
    assert p.nudge(PACE.WRAP, p.turn_token()) is True
    assert p.sent == [{"type": "steer", "message": PACE.WRAP}]
    assert not [e for e in p.events if e.get("type") == "user"]          # nothing in the transcript


def test_pi_quick_wrap_never_reaches_next_turn(tmp_path):
    p = _pi(tmp_path)
    p._turn_seq, p._turn_live = 1, True
    tok = p.turn_token()
    p._turn_over()                                                       # the turn ended
    assert p.nudge(PACE.WRAP, tok) is False
    p._turn_seq, p._turn_live = 2, True                                  # a later turn started
    assert p.nudge(PACE.WRAP, tok) is False and p.sent == []
