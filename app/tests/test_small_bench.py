"""The small cost tests (testkit/loop/small.py): metering, statistics, presets and the offline overhead budgets.

Everything here is offline: a fake OpenAI-compatible server stands in for the model, the whole file runs in well under
20 seconds. The Codex CLI test is skipped when the CLI is not installed.
"""
import json
import math
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

LOOP = Path(__file__).resolve().parents[2] / "testkit" / "loop"
sys.path.insert(0, str(LOOP))

import bench  # noqa: E402
import small  # noqa: E402
import small_codex  # noqa: E402
import small_fake  # noqa: E402
import small_meter as SM  # noqa: E402
import small_offline  # noqa: E402

PRICES = {"in": 0.15, "cached": 0.003, "out": 0.60}
TOOLS_RESP = [{"type": "function", "name": "exec_command",
               "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]
TOOLS_CHAT = [{"type": "function", "function": {"name": "shell", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}}}}}]


@pytest.fixture(autouse=True)
def _loopback(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(k, raising=False)


def post(url, body, headers=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode(),
                               headers={"content-type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=20) as resp:
        return resp.status, resp.read()


# ---------- the meter counts every request ------------------------------------------------------------------------
@pytest.mark.parametrize("trailing_newline", [True, False])
def test_meter_counts_each_streamed_and_plain_request(trailing_newline):
    """A fake proxy upstream counts the requests it received; the meter must report exactly that many calls, with the
    reply's own prompt, cached and completion tokens, whatever the wire (Responses or chat), streamed or not, and even
    when the last SSE event (the one with the usage) has no newline after it."""
    fake = small_fake.FakeServer(steps=["ls", "cat a", "cat b"], trailing_newline=trailing_newline).start()
    m = SM.Meter(fake.base, PRICES).start()
    try:
        base = m.base_for("t1", "codex", 0)
        for i in range(4):                                                    # a streamed Responses conversation
            post(base + "/responses", {"model": "x", "stream": True, "tools": TOOLS_RESP,
                                        "input": [{"role": "user", "content": "work " * 50 * (i + 1)}]})
        post(base + "/responses", {"model": "x", "stream": False, "tools": TOOLS_RESP, "input": "one more"})
        for i in range(3):                                                    # a streamed chat conversation
            post(base + "/chat/completions", {"model": "x", "stream": True, "tools": TOOLS_CHAT,
                                               "messages": [{"role": "user", "content": "hi " * 40 * (i + 1)}]})
        post(base + "/chat/completions", {"model": "x", "tools": TOOLS_CHAT, "messages": [{"role": "user", "content": "plain"}]})
        got = m.totals()[("t1", "codex", "0")]
        reqs = fake.of()
        assert len(reqs) == 9
        assert got["calls"] == len(reqs), "the meter must count every request, not one per task"
        assert got["usage_missing"] == 0 and got["failed"] == 0
        for key, field in (("prompt", "prompt"), ("cached", "cached"), ("completion", "completion")):
            assert got[key] == sum(r["usage"][field] for r in reqs), key
        assert got["cached"] > 0
    finally:
        m.close()
        fake.close()


def test_meter_reads_chunked_request_bodies_and_flags_missing_usage():
    import http.client
    fake = small_fake.FakeServer(steps=["ls"], usage=False).start()       # an endpoint that sends no usage at all
    m = SM.Meter(fake.base, PRICES).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", m.port, timeout=20)
        body = json.dumps({"model": "x", "stream": True, "tools": TOOLS_RESP, "input": "hello"}).encode()
        conn.request("POST", "/m/t2/codex/0/v1/responses", body=iter([body[:10], body[10:]]),
                     headers={"content-type": "application/json", "transfer-encoding": "chunked"}, encode_chunked=True)
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200 and fake.count() == 1
        got = m.totals()[("t2", "codex", "0")]
        assert got["calls"] == 1 and got["usage_missing"] == 1 and got["prompt"] == 0   # counted, flagged, not invented
    finally:
        m.close()
        fake.close()


def test_meter_asks_streamed_chat_for_usage_and_counts_refusals_apart():
    fake = small_fake.FakeServer().start()
    m = SM.Meter(fake.base, PRICES).start()
    try:
        post(m.base_for("t3", "meshpool", 0) + "/chat/completions", {"model": "x", "stream": True, "messages": [
            {"role": "user", "content": "hi"}]})                               # no stream_options from the client
        assert fake.requests[0]["body"]["stream_options"] == {"include_usage": True}
        assert m.totals()[("t3", "meshpool", "0")]["calls"] == 1
    finally:
        m.close()
        fake.close()
    dead = SM.Meter("http://127.0.0.1:9/v1", PRICES).start()                   # nothing listens there
    try:
        with pytest.raises(urllib.error.HTTPError):
            post(dead.base_for("t4", "x", 0) + "/chat/completions", {"model": "x", "messages": []})
        t = dead.totals()[("t4", "x", "0")]
        assert t["calls"] == 0 and t["failed"] == 1
    finally:
        dead.close()


def test_meter_budget_is_a_hard_stop():
    fake = small_fake.FakeServer().start()
    m = SM.Meter(fake.base, {"in": 5000.0, "cached": 0.0, "out": 5000.0}, max_usd=0.05).start()   # one call is over budget
    try:
        base = m.base_for("t5", "meshpool", 0)
        post(base + "/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "hello " * 20}]})
        assert m.exceeded
        with pytest.raises(urllib.error.HTTPError) as e:
            post(base + "/chat/completions", {"model": "x", "messages": [{"role": "user", "content": "again"}]})
        assert e.value.code == 402
        assert fake.count() == 1, "a refused request must never reach the model"
        assert ("t5", "meshpool", "0") in m.blocked_keys and m.blocked == 1
    finally:
        m.close()
        fake.close()


def test_azure_auth_header_and_api_version_are_applied():
    fake = small_fake.FakeServer().start()
    m = SM.Meter(fake.base, PRICES, azure_key="az-key-1", extra_query="api-version=2025-01-01").start()
    try:
        post(m.base_for("t6", "meshpool", 0) + "/chat/completions", {"model": "dep", "messages": [{"role": "user", "content": "hi"}]},
             headers={"authorization": "Bearer something-else"})
        h = {k.lower(): v for k, v in fake.requests[0]["headers"].items()}
        assert h.get("api-key") == "az-key-1" and "authorization" not in h
        assert "api-version=2025-01-01" in fake.requests[0]["raw_path"]
    finally:
        m.close()
        fake.close()


@pytest.mark.skipif(not (small_codex.find_codex() and small_codex.find_codex()[0] != "npx"),
                    reason="Codex CLI not installed")
def test_real_codex_is_metered_per_request():
    """The earlier Codex number (1.0 calls/task) came from Codex's own per-turn usage event. Run the real Codex CLI
    on a 3-step script through the meter: the meter's count must equal the requests the fake received, and exceed 1."""
    import tempfile
    cmd = small_codex.find_codex()
    fake = small_fake.FakeServer(steps=["ls", "cat notes.txt"]).start()
    m = SM.Meter(fake.base, PRICES).start()
    wd = tempfile.mkdtemp(prefix="mhsmall-test-")
    Path(wd, "notes.txt").write_text("alpha\n" * 20)
    try:
        r = small_codex.run(cmd, "Read notes.txt and tell me how many lines it has.", wd,
                            m.base_for("t7", "codex", 0), "deepseek-v4.1-flash", timeout=15)
        got = m.totals()[("t7", "codex", "0")]
        assert r["answer"].startswith("Done"), r
        assert got["calls"] == fake.count() == 3, (got, fake.count())
        assert got["calls"] > 1 and got["cached"] > 0
        assert got["prompt"] == sum(x["usage"]["prompt"] for x in fake.of())
    finally:
        m.close()
        fake.close()
        shutil.rmtree(wd, ignore_errors=True)


# ---------- cost, statistics, presets ------------------------------------------------------------------------------
def test_cost_formula_is_benchs():
    row = {"prompt_tokens": 38758, "cached_tokens": 31264, "completion_tokens": 1414}
    n = {"prompt": 38758, "cached": 31264, "completion": 1414}
    assert SM.cost_usd(n, PRICES) == pytest.approx(bench.cost(row, {"in": 0.15, "cached": 0.003, "out": 0.60}))
    assert SM.cost_usd(n, PRICES) == pytest.approx(0.0021, abs=2e-4)           # the cost report's "before" arm
    assert small.PRICES == PRICES


def test_presets_use_real_tasks_and_nest():
    ids = {t["id"] for t in small.all_tasks()}
    assert len(small.QUICK) == 4 and len(small.COST8) == 8 and len(small.COST20) == 20
    assert len(set(small.COST20)) == 20 and set(small.COST20) <= ids
    assert set(small.QUICK) <= set(small.COST8) <= set(small.COST20)
    assert [t["id"] for t in small.first_n(4, small.all_tasks())] == small.QUICK
    assert {"alg-topo-cycle", "dbg-retry", "mf-atomic-transfer", "te-business-days", "data-cohort-retention",
            "rf-split-invoice", "amb-search-normalize", "chat-explain"} == {i.replace("v2-", "") for i in small.COST8}


def _pairs(a_costs, b_costs, a_ok=None, b_ok=None):
    n = len(a_costs)
    return [({"usd": a_costs[i], "pass": (a_ok or [True] * n)[i]}, {"usd": b_costs[i], "pass": (b_ok or [True] * n)[i]})
            for i in range(n)]


def test_verdict_small_n_has_no_interval_and_says_not_significant():
    st = small.ratio_stats(_pairs([1, 1, 1, 1], [2, 2, 2, 2]))
    line = small.verdict_line("meshpool", "codex", st)
    assert line.startswith("VERDICT: CHEAPER (ratio 0.50, 4 tasks, solved 4 vs 4")
    assert "not significant" in line and not st["interval"]


def test_verdict_with_interval_flags_real_and_noisy_differences():
    clear = small.ratio_stats(_pairs([1.0 + 0.01 * i for i in range(10)], [2.0 + 0.01 * i for i in range(10)]))
    assert clear["interval"] and clear["hi"] < 1.0 and clear["significant"]
    assert "CI" in small.verdict_line("m", "c", clear) and "not significant" not in small.verdict_line("m", "c", clear)
    a = [1, 5, 1, 5, 1, 5, 1, 5, 1, 5]
    b = [5, 1, 5, 1, 5, 1, 5, 1, 5, 1.2]
    noisy = small.ratio_stats(_pairs(a, b))
    assert noisy["interval"] and noisy["lo"] < 1.0 < noisy["hi"] and not noisy["significant"]
    assert "not significant" in small.verdict_line("m", "c", noisy)


def test_verdict_not_cheaper_and_failed_solves_count_against_cost():
    st = small.ratio_stats(_pairs([3, 3, 3, 3], [1, 1, 1, 1]))
    assert small.verdict_line("m", "c", st).startswith("VERDICT: NOT CHEAPER (ratio 3.00, 4 tasks, solved 4 vs 4")
    # equal dollars, but the subject solved half: $ per solved doubles
    st = small.ratio_stats(_pairs([1, 1, 1, 1], [1, 1, 1, 1], a_ok=[True, True, False, False]))
    assert st["ratio"] == pytest.approx(2.0) and st["solved_a"] == 2 and st["solved_b"] == 4
    none = small.ratio_stats(_pairs([1, 1], [1, 1], a_ok=[False, False]))
    assert math.isnan(none["ratio"]) and "NOT CHEAPER (ratio n/a" in small.verdict_line("m", "c", none)


# ---------- offline overhead budgets -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def offline():
    return small_offline.measure_meshpool()


def test_offline_every_lane_makes_the_scripted_six_calls(offline):
    for lane in small_offline.LANES:
        r = offline[lane]
        assert r.get("calls") == 6, (lane, r)
        assert r["per_call_tokens"] == sorted(r["per_call_tokens"]), f"{lane}: the context must only grow over the script"


def test_code_lane_fixed_overhead_within_limit(offline):
    """The default code lane resends its system prompt and tool schemas on every call. The limit is the value measured
    when it was set plus 10% (small_budgets.json): a bigger prompt or a new tool schema fails here, on purpose. If the
    increase is intended, re-measure with `python testkit/loop/small_offline.py --write-budgets`."""
    b = small_offline.load_budgets()["lanes"]["code"]
    assert b["fixed_tokens_limit"] == int(b["measured_fixed_tokens"] * 1.10)
    got = offline["code"]["fixed_tokens"]
    assert got <= b["fixed_tokens_limit"], (f"code lane fixed overhead is {got} tokens per call, over the limit "
                                            f"{b['fixed_tokens_limit']} (measured {b['measured_fixed_tokens']})")
    assert offline["code"]["growth_per_call"] <= b["growth_per_call_limit"]


def test_offline_all_lane_budgets_hold(offline):
    assert small_offline.check_budgets(offline) == []
