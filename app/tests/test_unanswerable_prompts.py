"""OSS-24 / OSS-33: a permission prompt nobody can answer fails fast; no tool card stays running; the repeated-call
guard is on by default."""
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import guards as G  # noqa: E402
from meshpool_harness.harnesses.acp import ACPAgent  # noqa: E402
from meshpool_harness.ui import agent_api as API  # noqa: E402
from test_acp_fixes import FAKE  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.delenv("MESHPOOL_HEADLESS", raising=False)


class _A:
    _stop = False
    title = "t"


def _session(scheduled=None):
    s = API.Session.__new__(API.Session)
    s.events, s.pendings, s._n = [], {}, 0
    s.lock = threading.RLock()
    s.changed = threading.Condition(s.lock)
    s.state, s.scheduled, s.agent = "working", scheduled, _A()
    s.emit = lambda ev: s.events.append(ev)
    return s


def test_scheduled_session_refuses_at_once():
    s = _session(scheduled={"id": "x"})
    t0 = time.time()
    d, note = s.approve("bash", "Run it?", "ls")
    assert d == "no" and "unattended" in note and time.time() - t0 < 1
    assert s.events[-1]["type"] == "approval_done" and s.events[-1]["decision"] == "no" and s.events[-1]["reason"]
    assert not s.pendings


def test_deleted_chat_refuses_at_once():
    s = _session()
    s.deleted = True
    t0 = time.time()
    d, note = s.approve("bash", "Run it?", "ls")
    assert d == "no" and "deleted" in note and time.time() - t0 < 1


def test_unanswered_prompt_times_out(monkeypatch):
    monkeypatch.setenv("MH_APPROVAL_TIMEOUT_S", "0.5")
    s = _session()
    t0 = time.time()
    d, note = s.approve("bash", "Run it?", "ls")
    assert d == "no" and "No answer within" in note and "Ask again when you are back." in note
    assert 0.4 < time.time() - t0 < 3
    assert s.events[-1] == dict(s.events[-1], type="approval_done", decision="timeout")
    assert not s.pendings


def test_answered_prompt_still_works(monkeypatch):
    monkeypatch.setenv("MH_APPROVAL_TIMEOUT_S", "5")
    s = _session()
    threading.Timer(0.3, lambda: s.answer(next(iter(s.pendings)), "yes")).start()   # ids are unique strings (ws09 M02-02)
    assert s.approve("bash", "Run it?", "ls")[0] == "yes"


def _acp(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    script = tmp_path / "fake_acp.py"
    script.write_text(FAKE)
    proj = tmp_path / "proj"
    proj.mkdir()
    m = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
         "args": [str(script), str(tmp_path / "store.json"), str(tmp_path / "calls.jsonl")], "office": False,
         "share_mcp": False}
    return ACPAgent(m, str(proj), **kw)


def test_acp_permission_in_scheduled_session_does_not_block(tmp_path, monkeypatch):
    s = _session(scheduled={"id": "x"})
    a = _acp(tmp_path, monkeypatch, approve=s.approve, mode="supervised")
    try:
        t0 = time.time()
        a.send("perm:execute")
        assert time.time() - t0 < 15
        assert any(e.get("text") == "chose no1" for e in a.events if e.get("type") == "text_delta") or \
            any("chose no1" in str(e.get("text", "")) for e in a.events)
    finally:
        a.close()


def test_acp_turn_end_closes_open_tool_cards(tmp_path, monkeypatch):
    a = _acp(tmp_path, monkeypatch)
    try:
        a._notification({"method": "session/update", "params": {"update": {
            "sessionUpdate": "tool_call", "toolCallId": "t1", "title": "ls", "kind": "execute", "status": "in_progress"}}})
        a._notification({"method": "session/update", "params": {"update": {
            "sessionUpdate": "tool_call", "toolCallId": "t2", "title": "cat", "kind": "read", "status": "in_progress"}}})
        a._notification({"method": "session/update", "params": {"update": {
            "sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed"}}})
        a.send("hello")
        starts = {e["id"] for e in a.events if e["type"] == "tool_start"}
        ends = {e["id"]: e for e in a.events if e["type"] == "tool_end"}
        assert starts == {"t1", "t2"} == set(ends)
        assert ends["t1"]["ok"] is False and ends["t1"]["output"] == "Not finished: the turn ended."
    finally:
        a.close()


def test_builtin_interrupt_mid_parallel_batch_ends_every_tool(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    (tmp_path / "f.txt").write_text("x")
    def stopped(*x, **k):
        raise core.Interrupted()
    monkeypatch.setattr(a.ws, "grep", stopped, raising=False)
    calls = [{"id": f"c{i}", "type": "function", "function": {"name": "grep", "arguments": json.dumps({"pattern": "x"})}}
             for i in range(3)] + [{"id": "c9", "type": "function", "function": {
                 "name": "read_file", "arguments": json.dumps({"path": "f.txt"})}}]
    seq = [{"content": "", "tool_calls": calls}, {"content": "done"}]
    a._post = lambda base, key, body, stream: ({"choices": [{"message": seq.pop(0), "finish_reason": "stop"}],
                                                "usage": {}}, False)
    evs = []
    a.emit = lambda ev: evs.append(ev)
    a.send("go")
    starts = [e["id"] for e in evs if e["type"] == "tool_start"]
    ends = [e["id"] for e in evs if e["type"] == "tool_end"]
    assert starts and sorted(set(starts)) == sorted(ends) and len(ends) == len(set(ends))


def _same(n=3):
    return [{"id": f"i{i}", "function": {"name": "bash", "arguments": '{"command":"ls"}'}} for i in range(n)]


def test_repeat_calls_default_on(monkeypatch):
    monkeypatch.delenv("MH_DOOM", raising=False)
    g = G.Guards({"task_id": "t"}, None, require_check=False)
    assert "GUARD loop" in (g.repeat_calls(_same()) or "")


def test_repeat_calls_off_with_zero(monkeypatch):
    monkeypatch.setenv("MH_DOOM", "0")
    g = G.Guards({"task_id": "t"}, None, require_check=False)
    assert g.repeat_calls(_same()) is None
