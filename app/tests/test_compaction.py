"""A task that runs for hundreds of steps keeps its thread: older steps become a handoff note, the task and the recent
messages stay word for word, and the conversation is always valid for the model."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import compaction as K  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _convo(steps=40, big=0):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Migrate the billing module to v2."}]
    for i in range(steps):
        name, args = (("edit", {"path": f"src/mod{i % 5}.py", "old": "a", "new": "b"}) if i % 3 == 0
                      else ("bash", {"command": f"pytest tests/t{i}.py"}))
        msgs.append({"role": "assistant", "content": f"Step {i}: working on it.", "tool_calls": [_call(i, name, args)]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": ("[exit 1]\nFAILED test" if i == 7 else "[exit 0]\nok") + "x" * big})
    return msgs


def _valid(msgs):
    calls = set()
    for m in msgs:
        if m.get("role") == "assistant":
            calls |= {c["id"] for c in m.get("tool_calls") or []}
        if m.get("role") == "tool":
            assert m["tool_call_id"] in calls, "a tool result lost its call"
    return True


def test_split_never_starts_the_tail_on_a_tool_result_and_keeps_recent_messages():
    msgs = _convo(40)
    head, middle, tail = K.split(msgs)
    assert [m["role"] for m in head] == ["system", "user"]
    assert tail[0]["role"] in ("user", "assistant") and len(tail) >= K.KEEP_RECENT
    assert head + middle + tail == msgs
    assert K.split(_convo(3)) is None                      # too short to be worth it


def test_the_note_has_the_log_facts_even_when_no_model_answers(monkeypatch):
    monkeypatch.setenv("MH_COMPACT_MODEL", "0")
    msgs = _convo(40)
    task, last = msgs[1], msgs[-1]
    r = K.compact_history(msgs)
    assert r and r["removed"] > 20 and r["model"] is False
    assert msgs[1] is task and msgs[-1] is last                     # the task and the latest message, word for word
    note = msgs[2]["content"]
    assert "Files changed:" in note and "src/mod0.py" in note and "Commands that failed" in note and "t7.py" in note
    assert "could not be written" in note and _valid(msgs)
    assert len(msgs) < 20


def test_a_written_summary_goes_in_and_a_failing_model_does_not_break_anything():
    class Fake:
        def chat(self, **kw):
            return {"choices": [{"message": {"content": "Done: moved invoices. Next: update the tests."}}]}

    msgs = _convo(40)
    K.compact_history(msgs, client=Fake())
    assert "Done: moved invoices." in msgs[2]["content"]

    class Broken:
        def chat(self, **kw):
            raise RuntimeError("endpoint down")
    msgs2 = _convo(40)
    assert K.compact_history(msgs2, client=Broken())["model"] is False and _valid(msgs2)


def test_a_three_hundred_step_task_finishes_and_never_overflows(tmp_path, monkeypatch):
    """The agent runs 300 tool steps with big outputs under a small context limit: it must compact along the way,
    send valid requests every time, and still deliver the final answer."""
    monkeypatch.setenv("MH_COMPACT_MODEL", "0")
    monkeypatch.setenv("MH_COMPACT_CHARS", "30000")
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "16384")   # a small window: eliding old output is not enough, notes are needed
    monkeypatch.setenv("MH_MAX_STEPS", "400")         # a long task by choice: the 200-step default (S-08) is raised
    (tmp_path / "a.txt").write_text("hello\n")
    a = Agent(str(tmp_path), mode="auto")
    n = {"i": 0}
    sizes, notes = [], []

    def fake(base, key, body, stream):
        msgs = body["messages"]
        assert _valid(msgs)
        sizes.append(core._context_size(msgs))
        notes.append(any("were summarized to save space" in str(m.get("content")) for m in msgs))
        i = n["i"] = n["i"] + 1
        if i > 300:
            return {"choices": [{"message": {"content": "all 300 steps done"}, "finish_reason": "stop"}], "usage": {}}, False
        cmd = f"python3 -c \"print('step {i} ' + 'y' * 3000)\""
        return {"choices": [{"message": {"content": f"Running step {i}.", "tool_calls": [_call(i, "bash", {"command": cmd})]},
                             "finish_reason": "tool_calls"}], "usage": {}}, False
    a._post = fake
    out = a.send("Run the whole checklist, 300 steps.")
    assert out == "all 300 steps done"
    assert any(notes), "it never compacted"
    assert max(sizes) < 90000, f"the conversation grew to {max(sizes)} characters"
    assert a.messages[1]["content"].startswith("Run the whole checklist")      # the task is still there, word for word
