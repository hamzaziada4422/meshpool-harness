"""WP7: zero-token mutation feedback on the agent's tests."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import mutate as MU  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402

LIB = "def clamp(x, lo, hi):\n    if x < lo:\n        return lo\n    if x > hi:\n        return hi\n    return x\n"
WEAK = "from lib import clamp\n\ndef test_clamp():\n    assert clamp(5, 0, 10) == 5\n"
STRONG = ("from lib import clamp\n\ndef test_clamp():\n    assert clamp(5, 0, 10) == 5\n"
          "    assert clamp(-1, 0, 10) == 0\n    assert clamp(11, 0, 10) == 10\n"
          "    assert clamp(0, 0, 10) == 0\n    assert clamp(10, 0, 10) == 10\n")


def _proj(tmp_path, test):
    (tmp_path / "lib.py").write_text(LIB)
    (tmp_path / "test_lib.py").write_text(test)
    return {str(tmp_path / "test_lib.py")}


def test_weak_test_leaves_survivors(tmp_path):
    ch = _proj(tmp_path, WEAK)
    alive = MU.survivors(str(tmp_path), ch)
    assert alive and all(a["file"] == "lib.py" for a in alive)
    assert any("`<` changed to `<=`" in a["desc"] for a in alive)


def test_strong_test_kills_boundary_mutants(tmp_path):
    ch = _proj(tmp_path, STRONG)
    weak = MU.survivors(str(tmp_path), _proj(tmp_path, WEAK))
    (tmp_path / "test_lib.py").write_text(STRONG)
    strong = MU.survivors(str(tmp_path), ch)
    assert len(strong) < len(weak)
    assert not any("`pass`" in a["desc"] or "`None`" in a["desc"] for a in strong)   # branches are covered


def test_real_folder_untouched(tmp_path):
    ch = _proj(tmp_path, WEAK)
    MU.survivors(str(tmp_path), ch)
    assert (tmp_path / "lib.py").read_text() == LIB
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "__pycache__") == ["lib.py", "test_lib.py"]


def test_skipped_when_tests_fail_on_original(tmp_path):
    ch = _proj(tmp_path, WEAK.replace("== 5", "== 6"))
    assert MU.survivors(str(tmp_path), ch) == []


def test_skipped_when_code_under_test_was_edited_too(tmp_path):
    ch = _proj(tmp_path, WEAK) | {str(tmp_path / "lib.py")}
    assert MU.survivors(str(tmp_path), ch) == []


def test_mutants_are_single_changes_and_capped():
    ms = MU.mutants(LIB * 1 + "\n".join(f"def f{i}(a):\n    return a + {i}\n" for i in range(40)))
    assert 0 < len(ms) <= 25 and all(isinstance(s, str) and d.startswith("line ") for s, d in ms)


@pytest.fixture
def _agent_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MH_REVIEW", "0")


def _agent_run(tmp_path):
    (tmp_path / "lib.py").write_text(LIB)
    a = Agent(str(tmp_path), mode="auto")
    a.route = Route(spec="code.test", family="code")
    seq = [{"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "write_file", "arguments": json.dumps({"path": "test_lib.py", "content": WEAK})}}]},
           {"content": "done"}, {"content": "strengthened"}]
    a._post = lambda base, key, body, stream: ({"choices": [{"message": seq.pop(0), "finish_reason": "stop"}],
                                                "usage": {}}, False)
    return a, a.send("write tests for lib.py")


def test_hook_injects_survivors_once(tmp_path, _agent_env, monkeypatch):
    monkeypatch.delenv("MH_MUTATION", raising=False)
    a, out = _agent_run(tmp_path)
    msgs = [m["content"] for m in a.messages if m.get("role") == "user" and "deliberately broken" in str(m["content"])]
    assert len(msgs) == 1 and "lib.py, line" in msgs[0] and out == "strengthened"


def test_hook_off_switch(tmp_path, _agent_env, monkeypatch):
    monkeypatch.setenv("MH_MUTATION", "0")
    a, out = _agent_run(tmp_path)
    assert out == "done"
