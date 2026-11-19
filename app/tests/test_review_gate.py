"""WP3: the independent review before a code turn ends. No network: the transport is replaced by a script."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import review as RV  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.delenv("MH_REVIEW", raising=False)
    monkeypatch.setenv("MH_MUTATION", "0")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _run(tmp_path, reviewer, spec="code.build", worker=None):
    """Worker writes a file, then says done (then 'checked' if nudged); `reviewer` is the reviewer's reply or an
    exception. Returns (agent, final text, reviewer request bodies, all bodies)."""
    a = Agent(str(tmp_path), mode="auto")
    a.route = Route(spec=spec, family="code")
    worker = list(worker or [
        {"content": "", "tool_calls": [_call(1, "write_file", {"path": "a.py", "content": "x = 1\n"})]},
        {"content": "done"}, {"content": "checked, nothing real"}])
    bodies, rev = [], []

    def fake(base, key, body, stream):
        bodies.append(body)
        if "tools" not in body:                        # the reviewer: no tools
            rev.append(body)
            if isinstance(reviewer, Exception):
                raise reviewer
            return {"choices": [{"message": {"content": reviewer}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 7}}, False
        return {"choices": [{"message": worker.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    out = a.send("Write a.py so that x is 1; negative input must raise ValueError.")
    return a, out, rev, bodies


def test_lgtm_ends_the_turn_after_one_review(tmp_path):
    a, out, rev, _ = _run(tmp_path, "LGTM")
    assert out == "done" and len(rev) == 1
    assert rev[0]["messages"][0]["content"] == RV.SYSTEM and len(rev[0]["messages"]) == 2
    assert "negative input must raise" in rev[0]["messages"][1]["content"] and "x = 1" in rev[0]["messages"][1]["content"]
    assert rev[0]["max_tokens"] == 700 and rev[0]["temperature"] == 0


def test_findings_are_injected_and_the_turn_continues(tmp_path):
    a, out, rev, _ = _run(tmp_path, "1. f(-1) must raise ValueError; it returns 1.")
    users = [m["content"] for m in a.messages if m.get("role") == "user"]
    assert any(u.startswith("[Meshpool] A second reviewer") and "f(-1)" in u for u in users)
    assert out == "checked, nothing real" and len(rev) == 1       # once per request


def test_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_REVIEW", "0")
    _, out, rev, _ = _run(tmp_path, "1. problem")
    assert rev == [] and out == "done"


def test_other_specs_do_not_review(tmp_path):
    _, out, rev, _ = _run(tmp_path, "1. problem", spec="code.explain")
    assert rev == []


def test_reviewer_error_is_swallowed(tmp_path):
    _, out, rev, _ = _run(tmp_path, RuntimeError("HTTP 500: boom"))
    assert out == "done" and len(rev) == 1


def test_empty_reply_does_nothing(tmp_path):
    _, out, rev, _ = _run(tmp_path, "  ")
    assert out == "done"


def test_usage_is_counted(tmp_path):
    a, *_ = _run(tmp_path, "LGTM")
    assert a.usage["in"] == 100 and a.usage["out"] == 7 and a.usage["calls"] >= 2


def test_helpers():
    assert RV.findings("lgtm, all good") == "" and RV.findings("1. x") == "1. x"
    assert RV.clean_request("do it\n\n​meta\n<reproduction>x</reproduction>") == "do it"
    assert RV.enabled("code.test") and not RV.enabled("code.explain")
