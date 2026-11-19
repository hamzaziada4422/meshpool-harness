"""The loop guard: a repeated call with nothing changed in between is refused with the reason, not run again. In the
V3 eval traces the model sometimes dumped the bytes of an unchanged file 30-50 times (od -c | sed -n '8,9p', '9,10p',
...); a note alone never stopped it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _run(tmp_path, replies):
    (tmp_path / "m.py").write_text("x = 1\n")
    a = Agent(str(tmp_path), mode="auto")
    seq, seen = list(replies), []

    def fake(base, key, body, stream):
        seen.append(body["messages"])
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    a.send("do it")
    tools = [m for m in a.messages if m.get("role") == "tool"]
    return a, tools


def _bash(i, cmd):
    return {"content": "", "tool_calls": [_call(i, "bash", {"command": cmd})]}


def test_identical_call_refused_from_the_third_run(tmp_path):
    a, tools = _run(tmp_path, [_bash(i, "cat -A m.py") for i in range(4)] + [{"content": "done"}])
    assert "Not run" not in tools[0]["content"] and "Not run" not in tools[1]["content"]
    assert tools[2]["content"].startswith("[Meshpool] Not run") and tools[3]["content"].startswith("[Meshpool] Not run")


def test_numbers_only_sweep_refused_from_the_fifth(tmp_path):
    sweep = [_bash(i, f"od -c m.py | sed -n '{i},{i + 1}p'") for i in range(6)]
    a, tools = _run(tmp_path, sweep + [{"content": "done"}])
    assert all("Not run" not in t["content"] for t in tools[:4])
    assert tools[4]["content"].startswith("[Meshpool] Not run") and tools[5]["content"].startswith("[Meshpool] Not run")


def test_rerun_after_an_edit_is_never_refused(tmp_path):
    steps = []
    for i in range(4):
        steps.append(_bash(10 + i, "python3 m.py"))
        steps.append({"content": "", "tool_calls": [_call(20 + i, "write_file", {"path": "m.py", "content": f"x = {i}\n"})]})
    a, tools = _run(tmp_path, steps + [{"content": "done"}])
    assert not any("Not run" in t["content"] for t in tools)


def test_inspection_is_not_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_AGENT_V3", "1")
    a = Agent(str(tmp_path), mode="auto")
    g = {"hist": [], "doom_at": -10, "seen": set(), "edits": 0, "last": 0}
    a.ws.commands.append({"cmd": "cat m.py", "code": 0})
    a._v3_notes(12, [], g)
    assert g["last"] == 0                         # a successful read-only command did not count as progress
    a.ws.commands.append({"cmd": "python3 m.py", "code": 0})
    a._v3_notes(13, [], g)
    assert g["last"] == 13
