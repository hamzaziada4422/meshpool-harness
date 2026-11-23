"""Retry-After handling of the engine client (W-10) and the reasoning_content refusal (W-22). No network."""
import sys
import time
from email.utils import formatdate
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.core import client as C  # noqa: E402

OK = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {}}


class FakeLedger:
    """Raises (or returns) the scripted outcomes in order; records the messages of every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, **kw):
        self.calls.append([dict(m) for m in kw["messages"]])
        out = self.script.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def sleeps(monkeypatch):
    rec = []
    monkeypatch.setattr(time, "sleep", rec.append)
    monkeypatch.delenv("MH_429_BACKOFF", raising=False)
    monkeypatch.delenv("MH_RATE_LIMIT_WAIT_S", raising=False)
    monkeypatch.delenv("MH_MODEL_ALLOW", raising=False)
    monkeypatch.delenv("MH_EVENT_LOG", raising=False)
    return rec


def make(script, phase="phase_2_build"):
    c = C.LiveClient(phase)
    c.ledger = FakeLedger(script)
    return c


def chat(c, messages=None):
    return c.chat(model="deepseek-chat", messages=messages or [{"role": "user", "content": "hi"}])


def test_http_date_retry_after():
    date = formatdate(time.time() + 90, usegmt=True)
    msg = f"HTTP 429: slow down [retry-after: {date}]"
    assert 85 <= C.stated_wait(msg) <= 91
    assert 85 <= C.rate_limit_wait(msg, 1) <= 91
    assert C.stated_wait("HTTP 429 [retry-after-ms: 2500]") == 2.5
    assert C.stated_wait("HTTP 429 [retry-after: 7]") == 7.0
    assert C.stated_wait("HTTP 429 nothing said") is None
    assert C.stated_wait("HTTP 429 [retry-after: not a date]") is None
    assert C.rate_limit_wait("x [retry-after: 600]", 1) == 300.0          # callers without a budget keep the cap
    assert C.rate_limit_wait(msg, 1, cap=30) == 30.0


def test_reset_past_budget_ends_without_waiting(sleeps, monkeypatch):
    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "60")
    c = make([RuntimeError("HTTP 429: token window used up [retry-after: 3600]"), OK])
    with pytest.raises(RuntimeError) as ei:
        chat(c)
    assert "token window used up" in str(ei.value) and "wait budget" in str(ei.value)
    assert not [s for s in sleeps if s >= 60] and not sleeps
    assert len(c.ledger.calls) == 1


def test_engine_honours_retry_after(sleeps):
    c = make([RuntimeError("HTTP 429: slow down [retry-after: 7]"), OK])
    assert chat(c) is OK
    assert sleeps == [7.0]
    sleeps.clear()
    c = make([RuntimeError("HTTP 429: slow down [retry-after-ms: 7000]"), OK])
    assert chat(c) is OK
    assert sleeps == [7.0]


def test_engine_budget_spent_raises(sleeps, monkeypatch):
    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "100")
    c = make([RuntimeError("HTTP 429: slow down [retry-after: 40]")] * 3 + [OK])
    with pytest.raises(RuntimeError) as ei:
        chat(c)
    assert sleeps == [40.0, 40.0] and "wait budget" in str(ei.value)
    # without a stated wait the fixed waits are charged to the same budget (65 s each, then spent)
    sleeps.clear()
    c = make([RuntimeError("HTTP 429: slow down")] * 5 + [OK])
    with pytest.raises(RuntimeError) as ei:
        chat(c)
    assert sleeps == [65.0, 35.0] and "spent" in str(ei.value)


def test_long_rate_limit_waits_do_not_use_up_attempts(sleeps):
    c = make([RuntimeError("HTTP 429: slow down [retry-after: 1]")] * 30 + [OK])
    assert chat(c) is OK and len(sleeps) == 30


def test_reasoning_content_refusal_is_stripped_once(sleeps):
    err = RuntimeError("HTTP 400: Unsupported field: messages[2].reasoning_content")
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a", "reasoning_content": "thinking"}]
    c = make([err, OK])
    assert chat(c, msgs) is OK
    assert "reasoning_content" in c.ledger.calls[0][1] and "reasoning_content" not in c.ledger.calls[1][1]
    assert "reasoning_content" not in msgs[1]
    # a second refusal is not stripped again: it ends the call like any other bad request
    c = make([err, err, OK])
    with pytest.raises(RuntimeError):
        c.chat(model="deepseek-chat", messages=[{"role": "assistant", "content": "a", "reasoning_content": "t"}])
    assert len(c.ledger.calls) == 2


def test_side_calls_keep_their_fail_fast_rule(sleeps):
    c = make([RuntimeError("HTTP 429: slow down [retry-after: 40]"), OK], phase="phase_3_side")
    with pytest.raises(RuntimeError):
        chat(c)
    assert not sleeps and len(c.ledger.calls) == 1



def test_a_5xx_before_a_429_does_not_change_the_rate_limit_budget(sleeps, monkeypatch):
    # The transient branch has its own (short) connect budget; it must not replace the rate-limit wait budget.
    monkeypatch.setattr(C, "CONNECT_BUDGET_S", 120)
    c = make([RuntimeError("HTTP 502: upstream"), RuntimeError("HTTP 429: slow down [retry-after: 600]"), OK])
    assert chat(c) is OK
    assert 600.0 in sleeps
