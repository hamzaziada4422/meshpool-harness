"""S-08 / H-07 / G-04: a turn cannot run away. Default step cap 200 (MH_MAX_STEPS, read per turn), an outer breaker for a
model that repeats itself, tools-off steps that still ask for tools, greedy sampling only for deepseek. The model is a
script: no network."""
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
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    for v in ("MH_MAX_STEPS", "MH_REPEAT_STOP", "MH_STUCK_STEPS", "MH_MODEL", "MH_PROFILE"):
        monkeypatch.delenv(v, raising=False)


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _bash(i, cmd):
    return {"content": "", "tool_calls": [_call(i, "bash", {"command": cmd})]}


def _agent(tmp_path, script, profile="code"):
    """script(n, body) -> assistant message for request number n (0-based)."""
    (tmp_path / "m.py").write_text("x = 1\n")
    events, bodies = [], []
    a = Agent(str(tmp_path), mode="auto", profile=profile, emit=events.append)

    def fake(base, key, body, stream):
        bodies.append(body)
        events_at.append(len(events))
        return {"choices": [{"message": a.script(len(bodies) - 1, body), "finish_reason": "stop"}], "usage": {}}, False
    events_at = []
    a._post, a.script = fake, script
    a.events, a.bodies, a.events_at = events, bodies, events_at
    return a


def _valid(a):
    """Every assistant tool call has its result before the next non-tool message."""
    msgs = a.messages
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            want = {c["id"] for c in m["tool_calls"]}
            got = set()
            for n in msgs[i + 1:]:
                if n.get("role") != "tool":
                    break
                got.add(n["tool_call_id"])
            assert want <= got, (i, want, got)


def _repeat(n, body):
    return _bash(n, "cat m.py")


def _errors(a):
    return [e for e in a.events if e.get("type") == "error"]


@pytest.mark.parametrize("profile", ["code", "work"])
def test_repeating_mock_model_is_stopped(tmp_path, profile):
    a = _agent(tmp_path, _repeat, profile)
    out = a.send("fix it")
    assert len(a.bodies) <= 36 + 8 + 3
    errs = _errors(a)
    assert len(errs) == 1 and "repeating the same steps" in errs[0]["text"]
    assert out.strip()
    assert a.bodies[-1]["tool_choice"] == "none"
    _valid(a)


def test_breaker_never_fires_before_stuck_cap(tmp_path):
    a = _agent(tmp_path, _repeat, "work")          # work profile: only the outer breaker acts
    a.send("fix it")
    assert a.events_at[-1] >= 0 and len(a.bodies) >= 36 + 8           # 44 repeated steps, then the closing request
    err_at = next(i for i, e in enumerate(a.events) if e.get("type") == "error")
    assert sum(1 for e in a.events[:err_at] if e.get("type") == "assistant") >= 36 + 8
    b = _agent(tmp_path, _repeat, "code")          # code profile: their hard wrap at 36 comes first, ours after it
    b.send("fix it")
    assert len(b.bodies) >= 37 and _errors(b)


def test_tools_off_step_calls_are_not_run(tmp_path):
    def script(n, body):
        if body["tool_choice"] == "none":           # a model that ignores tool_choice none
            return {"content": "I could not finish.", "tool_calls": [_call(n, "bash", {"command": "touch ran.txt"})]}
        return _bash(n, "cat m.py")
    a = _agent(tmp_path, script, "code")
    out = a.send("fix it")
    assert not (tmp_path / "ran.txt").exists()
    assert "I could not finish." in out
    assert any("Not run" in str(m.get("content")) for m in a.messages if m.get("role") == "tool")
    assert len(_errors(a)) == 1
    _valid(a)


def test_default_step_cap_bounds_requests(tmp_path):
    a = _agent(tmp_path, lambda n, body: _bash(n, f"touch f{n}.txt"))     # progress every step, never stops
    a.send("go")
    assert len(a.bodies) == 200
    errs = _errors(a)
    assert len(errs) == 1 and "200 steps" in errs[0]["text"] and "Max steps per reply" in errs[0]["text"]
    _valid(a)


def test_cap_setting_applies_next_turn(tmp_path, monkeypatch):
    a = _agent(tmp_path, lambda n, body: _bash(n, f"touch g{n}.txt"))
    monkeypatch.setenv("MH_MAX_STEPS", "5")
    a.send("go")
    assert len(a.bodies) == 5 and "5 steps" in _errors(a)[0]["text"]
    monkeypatch.setenv("MH_MAX_STEPS", "0")
    n0 = len(a.bodies)
    a.script = lambda n, body: {"content": "done"} if n - n0 >= 250 else _bash(n, f"touch h{n}.txt")
    out = a.send("again")
    assert out == "done" and len(a.bodies) - n0 == 251 and len(_errors(a)) == 1      # no second cap error


def test_breaker_resets_on_progress(tmp_path):
    def script(n, body):
        if n == 40:
            return _bash(n, "touch progress.txt")
        return {"content": "done"} if n >= 81 else _bash(n, "cat m.py")
    a = _agent(tmp_path, script, "work")
    assert a.send("go") == "done"
    assert not _errors(a)


def test_breaker_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_REPEAT_STOP", "0")
    a = _agent(tmp_path, lambda n, body: {"content": "done"} if n >= 60 else _bash(n, "cat m.py"), "work")
    assert a.send("go") == "done" and not _errors(a)


def test_unknown_model_is_not_greedy(tmp_path):
    a = _agent(tmp_path, lambda n, b: {"content": "x"})
    a.model = "acme-xyz"
    assert "temperature" not in a._body()
    a.model = "deepseek-v4.1-flash"
    assert a._body()["temperature"] == 0.0


def test_cli_max_steps_flag():
    from meshpool_harness.cli import _agent_parser
    p = _agent_parser()
    assert p.parse_args(["--max-steps", "7"]).max_turns == 7
    assert p.parse_args(["--max-turns", "3"]).max_turns == 3
    assert p.parse_args(["--max-steps", "0"]).max_turns == 0
    assert p.parse_args([]).max_turns is None


def test_max_steps_is_a_setting(tmp_path):
    from meshpool_harness import config as C
    from meshpool_harness.ui import server as S
    row = next(f for f in C.FIELDS if f[0] == "MH_MAX_STEPS")
    assert row[1] == "Max steps per reply" and row[2] == "200"
    shown = {f["key"]: f for f in S.get_settings()["fields"]}["MH_MAX_STEPS"]
    assert shown["default"] == "200"
    out = S.save_settings({"values": {"MH_MAX_STEPS": "50"}})
    assert {f["key"]: f for f in out["fields"]}["MH_MAX_STEPS"]["value"] == "50"
    assert Agent(str(tmp_path), mode="auto")._step_cap() == 50
    S.save_settings({"values": {"MH_MAX_STEPS": ""}})
    assert Agent(str(tmp_path), mode="auto")._step_cap() == 200
