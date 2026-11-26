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


def test_repeats_that_do_work_are_never_refused(tmp_path):
    """Polling a CI run, rerunning a check or calling a tool whose result can change is not a loop."""
    steps = [_bash(30 + i, "python3 m.py") for i in range(4)] + [{"content": "done"}]
    a, tools = _run(tmp_path, steps)
    assert not any("Not run" in t["content"] for t in tools)


def test_reading_other_line_ranges_is_not_a_loop(tmp_path):
    """Paging through a big file changes only numbers (start/end); it is progress, not a repeat."""
    reads = [{"content": "", "tool_calls": [_call(40 + i, "read_file", {"path": "m.py", "start": i * 300 + 1,
                                                                        "end": (i + 1) * 300})]} for i in range(7)]
    a, tools = _run(tmp_path, reads + [{"content": "done"}])
    assert not any("Not run" in t["content"] for t in tools)


def test_codex_shell_command_list_is_inspected_by_its_script(tmp_path):
    """The Codex-style shell tool sends ["bash", "-lc", script]; the guard looks at the script."""
    a = Agent(str(tmp_path), mode="auto")
    assert a._inspection("shell", json.dumps({"command": ["bash", "-lc", "od -c m.py | sed -n '1,2p'"]}))
    assert a._inspection("shell", json.dumps({"command": "cat m.py"}))
    assert not a._inspection("shell", json.dumps({"command": ["bash", "-lc", "python3 m.py"]}))


def _killed_env(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "m.py").write_text("x = 1\n")
    return Agent(str(tmp_path), mode="auto")


def _sh(cmd):
    return {"id": "s", "function": {"name": "shell", "arguments": json.dumps({"command": ["bash", "-lc", cmd]})}}


def test_three_in_a_row_counts_a_file_changed_outside_the_edit_tools(tmp_path):
    """A command rerun after a script or the user changed a file is not a loop, even with no edit-tool call."""
    a = _killed_env(tmp_path)
    runs = []
    a._run_tool = lambda c, cut=False: runs.append(1) or f"[exit 1]\nfail {len(runs)}"
    g = {}
    for i in range(5):
        (tmp_path / "m.py").write_text(f"x = {i + 2}\n")
        out = a._repeat_guard(_sh("python3 m.py"), g)
        assert "repeating yourself" not in out
    assert len(runs) == 5


def test_a_pair_taking_turns_that_flips_the_tree_is_still_caught(tmp_path):
    """git stash, git stash pop, again and again: the tree flips between two states, and the fifth turn is refused."""
    a = _killed_env(tmp_path)
    state = {"n": 0}

    def run(c, cut=False):
        state["n"] += 1
        (tmp_path / "m.py").write_text("x = 1\n" if "pop" in json.dumps(c) else "x = 9\n")
        return "[exit 0]\nok"
    a._run_tool = run
    g = {}
    outs = [a._repeat_guard(_sh("git stash" if i % 2 == 0 else "git stash pop"), g) for i in range(7)]
    assert any("repeating yourself" in o for o in outs)


def test_a_command_rerun_with_nothing_changed_is_still_refused_the_fourth_time(tmp_path):
    a = _killed_env(tmp_path)
    a._run_tool = lambda c, cut=False: "[exit 1]\nsame"
    g = {}
    outs = [a._repeat_guard(_sh("python3 m.py --x"), g) for _ in range(4)]
    assert not any("repeating yourself" in o for o in outs[:3])
    assert "Not run" in outs[3]
