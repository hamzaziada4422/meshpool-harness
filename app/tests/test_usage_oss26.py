"""OSS-26: usage is never double counted (reasoning inside output, cache reads and writes inside input) and cache
writes are priced at the provider's write rate, through the real wire conversions."""
import pytest

from meshpool_harness.core import anthropic_wire as AW
from meshpool_harness.core import usage as U
from meshpool_harness.core.wire import from_responses


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "0")
    for k in ("MH_PRICES", "MH_PRICE_IN", "MH_PRICE_OUT", "MH_PRICE_CACHED", "MH_BUDGET_PRICES"):
        monkeypatch.delenv(k, raising=False)


def test_anthropic_cache_writes_bill_at_the_write_rate():
    reply = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
             "usage": {"input_tokens": 1000, "cache_read_input_tokens": 20000,
                       "cache_creation_input_tokens": 50000, "output_tokens": 800}}
    n = U.normalize(AW.from_response(reply)["usage"])
    assert (n["in"], n["cached"], n["cache_write"], n["out"]) == (71000, 20000, 50000, 800)
    cost, _ = U.cost_of("claude-sonnet-5-5", n)
    # sonnet 5.5: in 2, cached 0.10, cache write 2.5, out 10 (USD per 1M)
    assert cost == pytest.approx((1000 * 2 + 20000 * 0.10 + 50000 * 2.5 + 800 * 10) / 1e6)


def test_reasoning_tokens_are_inside_output_and_not_charged_twice():
    chat = {"prompt_tokens": 1000, "completion_tokens": 3000,
            "prompt_tokens_details": {"cached_tokens": 400},
            "completion_tokens_details": {"reasoning_tokens": 2500}}
    n = U.normalize(chat)
    assert (n["in"], n["cached"], n["out"], n["reasoning"]) == (1000, 400, 3000, 2500)
    cost, _ = U.cost_of("claude-sonnet-5-5", n)
    assert cost == pytest.approx((600 * 2 + 400 * 0.10 + 3000 * 10) / 1e6)


def test_responses_wire_keeps_cached_inside_input():
    p = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
         "usage": {"input_tokens": 5000, "input_tokens_details": {"cached_tokens": 4000},
                   "output_tokens": 900, "output_tokens_details": {"reasoning_tokens": 700}}}
    n = U.normalize(from_responses(p)["usage"])
    assert n["in"] == 5000 and n["cached"] == 4000 and n["out"] == 900
    t = U.start_task(label="t")
    U.record("claude-sonnet-5-5", from_responses(p)["usage"], task=t)
    tot = t.totals()
    assert tot["in"] == 5000 and tot["out"] == 900 and tot["calls"] == 1
