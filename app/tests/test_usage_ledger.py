"""The usage ledger: provider-reported tokens, per-model prices, task budgets, cross-thread/process writes."""
import http.server
import json
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from meshpool_harness.core import usage as U

APP = Path(__file__).resolve().parents[1]
M = "deepseek-v4.1-flash"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "0")
    for k in ("MH_PRICE_IN", "MH_PRICE_OUT", "MH_PRICE_CACHED", "MH_PRICES", "MH_BUDGET_USD", "MH_BUDGET_PRICES",
              "MH_USAGE_TASK", "MH_USAGE_SOURCE", "MH_CHAT_ID", "MH_USAGE_LEDGER"):
        monkeypatch.delenv(k, raising=False)
    U._CFG.update(key=None, val={}, at=0.0)
    U.flush()
    yield tmp_path
    U.flush()


def _lines(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines()]


# ---- normalize / estimate

def test_normalize_chat_deepseek_responses_anthropic_pi():
    n = U.normalize({"prompt_tokens": 100, "completion_tokens": 20,
                     "prompt_tokens_details": {"cached_tokens": 60},
                     "completion_tokens_details": {"reasoning_tokens": 5}})
    assert n == {"in": 100, "cached": 60, "cache_write": 0, "out": 20, "reasoning": 5, "reported": True}
    n = U.normalize({"prompt_tokens": 100, "completion_tokens": 7, "prompt_cache_hit_tokens": 80,
                     "prompt_cache_miss_tokens": 20})
    assert (n["in"], n["cached"], n["out"]) == (100, 80, 7)
    n = U.normalize({"input_tokens": 50, "input_tokens_details": {"cached_tokens": 30}, "output_tokens": 40,
                     "output_tokens_details": {"reasoning_tokens": 25}})
    assert (n["in"], n["cached"], n["out"], n["reasoning"]) == (50, 30, 40, 25)
    n = U.normalize({"input_tokens": 10, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 50,
                     "output_tokens": 9})
    assert (n["in"], n["cached"], n["cache_write"], n["out"]) == (260, 200, 50, 9)
    n = U.normalize({"prompt_tokens": 300, "completion_tokens": 4, "cache_creation": 40,
                     "prompt_tokens_details": {"cached_tokens": 100}})
    assert (n["in"], n["cached"], n["cache_write"]) == (300, 100, 40)
    n = U.normalize({"input": 10, "output": 5, "cacheRead": 90, "cacheWrite": 20})
    assert (n["in"], n["cached"], n["cache_write"], n["out"]) == (120, 90, 20, 5)


@pytest.mark.parametrize("raw", [None, {}, {"total_tokens": 5}, "x", []])
def test_normalize_missing(raw):
    n = U.normalize(raw)
    assert n["reported"] is False and n["in"] == n["out"] == 0


def test_estimate_never_zero():
    e = U.estimate(0, 0)
    assert e["in"] >= 1 and e["est"] is True and e["reported"] is False
    e = U.estimate(10, 1)
    assert (e["in"], e["out"]) == (4, 1)
    assert U.estimate(3000, 600)["in"] == 1000


# ---- prices

def test_price_order_override_legacy_table(monkeypatch):
    assert U.price_for(M)["source"] == "table"
    assert U.price_for("chatgpt:" + M)["in"] == 0.15
    assert U.price_for("openai/claude-opus-5-5")["in"] == 4
    assert U.price_for("nobody-model") == {"in": None, "cached": None, "cache_write": None, "out": None,
                                           "source": "unknown"}
    monkeypatch.setenv("MH_PRICE_IN", "3")
    assert U.price_for(M)["source"] == "table"          # needs both IN and OUT
    monkeypatch.setenv("MH_PRICE_OUT", "9")
    p = U.price_for(M)
    assert (p["in"], p["out"], p["source"]) == (3, 9, "setting") and p["cached"] == pytest.approx(0.3)
    monkeypatch.setenv("MH_PRICES", json.dumps({M: {"in": 1, "out": 2, "cached": 0.5}, "gpt-*": {"in": 5, "out": 6}}))
    p = U.price_for(M)
    assert (p["in"], p["out"], p["cached"]) == (1, 2, 0.5)
    assert U.price_for("gpt-9")["in"] == 5
    assert U.price_for("zzz")["in"] == 3              # the legacy global applies to every other model


def test_table_exact_beats_glob(tmp_path, monkeypatch):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"models": {"foo-*": {"in": 1, "out": 1}, "foo-bar": {"in": 2, "out": 2},
                                         "foo-bar-*": {"in": 3, "out": 3}}}))
    monkeypatch.setattr(U, "_TABLE_FILE", f)
    assert U.price_for("foo-bar")["in"] == 2
    assert U.price_for("foo-baz")["in"] == 1
    assert U.price_for("foo-bar-1")["in"] == 3        # the longer glob wins


def test_budget_prices_override(monkeypatch):
    n = {"in": 1_000_000, "cached": 0, "cache_write": 0, "out": 0}
    assert U.budget_cost(M, n) == pytest.approx(0.15)
    monkeypatch.setenv("MH_BUDGET_PRICES", "2,0.2,8")
    assert U.budget_cost(M, n) == pytest.approx(2.0)
    assert U.budget_cost("mystery", n) == pytest.approx(2.0)


class _Models(http.server.BaseHTTPRequestHandler):
    body = b"{}"
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *a):
        pass


def _serve(body):
    h = type("H", (_Models,), {"body": json.dumps(body).encode(), "hits": 0})
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, h, f"http://127.0.0.1:{srv.server_address[1]}/v1"


def test_endpoint_prices_per_token_and_per_million(home, monkeypatch):
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "1")
    srv, h, base = _serve({"data": [
        {"id": "vendor/m-token", "pricing": {"prompt": "0.000002", "completion": "0.000008",
                                              "input_cache_read": "0.0000005", "input_cache_write": "0.0000025"}},
        {"id": "m-million", "price": {"in": 1.5, "out": 6, "cached": 0.15}},
        {"id": "m-bad", "pricing": {"prompt": "-1", "completion": "-1"}}]})
    try:
        U._fetch_endpoint_prices(base)
        p = U.price_for("m-token", base)
        assert (p["in"], p["out"], p["cached"], p["cache_write"], p["source"]) == (2.0, 8.0, 0.5, 2.5, "endpoint")
        assert U.price_for("vendor/m-token", base)["in"] == 2.0
        p = U.price_for("m-million", base)
        assert (p["in"], p["out"], p["cached"], p["source"]) == (1.5, 6.0, 0.15, "endpoint")
        assert U.price_for("m-bad", base)["source"] == "unknown"
        assert U.price_for(M, base)["source"] == "table"
        n = h.hits
        U._EP_MEM.clear()
        U._fetch_endpoint_prices(base)                  # served from the disk cache
        assert h.hits == n
        assert list((home / "usage").glob("endpoint-prices-*.json"))
    finally:
        srv.shutdown()


def test_price_for_never_fetches_on_the_call_path(home, monkeypatch):
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "1")
    srv, h, base = _serve({"data": [{"id": "later", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]})
    try:
        t0 = time.time()
        assert U.price_for("later", base)["source"] == "unknown"      # first call: nothing yet, no waiting
        assert time.time() - t0 < 0.5
        th = U._EP_THREADS.get((str(home), base.rstrip("/")))
        assert th is not None
        th.join(5)
        assert U.price_for("later", base)["source"] == "endpoint"
        assert U.prefetch_prices(base) is None                         # once per 24 h
    finally:
        srv.shutdown()


def test_dead_endpoint_is_unknown_and_fast(home, monkeypatch):
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "1")
    base = "http://127.0.0.1:9/v1"
    t0 = time.time()
    assert U.price_for("whatever", base)["source"] == "unknown"
    assert time.time() - t0 < 0.5
    U._EP_THREADS[(str(home), base.rstrip("/"))].join(6)
    assert U.price_for("whatever", base)["source"] == "unknown"
    assert U.prefetch_prices(base) is None                              # failure cached for 10 min
    assert time.time() - t0 < 4


# ---- cost

def test_cost_math_cached_cache_write_reasoning_not_double_counted(monkeypatch):
    monkeypatch.setenv("MH_PRICES", json.dumps({"m": {"in": 10, "out": 40, "cached": 1, "cache_write": 12}}))
    n = {"in": 1000, "cached": 400, "cache_write": 100, "out": 200, "reasoning": 150}
    c, src = U.cost_of("m", n)
    assert src == "setting"
    assert c == pytest.approx((500 * 10 + 400 * 1 + 100 * 12 + 200 * 40) / 1e6)
    monkeypatch.setenv("MH_PRICES", json.dumps({"m": {"in": 10, "out": 40}}))
    c, _ = U.cost_of("m", n)
    assert c == pytest.approx((500 * 10 + 400 * 1 + 100 * 12.5 + 200 * 40) / 1e6)


def test_unknown_price_cost_none_budget_cost_conservative():
    n = {"in": 1000, "cached": 0, "cache_write": 0, "out": 1000}
    assert U.cost_of("mystery", n) == (None, "unknown")
    b = U.budget_cost("mystery", n)
    assert b > 0
    assert b >= U.budget_cost("claude-fable-5-2", n) - 1e-12
    assert b == pytest.approx((1000 * 8 + 1000 * 40) / 1e6)


# ---- records and tasks

def test_record_writes_month_task_and_ancestor_files(home):
    parent = U.start_task("c1", 0)
    child = U.start_task("c1", None, parent=parent)
    r = U.record(M, {"prompt_tokens": 1000, "completion_tokens": 100}, task=child, source="engine",
                 base="https://llm.example.com/v1", extra={"step": 3})
    U.flush()
    assert r["cost"] == pytest.approx((1000 * 0.15 + 100 * 0.6) / 1e6)
    assert (r["model"], r["host"], r["source"], r["task"], r["chat"], r["step"]) == (
        M, "llm.example.com", "engine", child.id, "c1", 3)
    assert r["reported"] is True and r["est"] is False and r["v"] == 1
    month = list((home / "usage").glob("calls-*.jsonl"))
    assert len(month) == 1 and _lines(month[0])[0]["id"] == r["id"]
    assert _lines(child.path)[0]["id"] == r["id"]
    assert _lines(parent.path)[0]["id"] == r["id"]
    meta = json.loads((home / "usage" / "tasks" / f"{child.id}.json").read_text())
    assert meta["parent"] == parent.id and meta["chat"] == "c1"
    assert set(meta) == {"id", "chat", "parent", "budget", "label", "started"}


def test_record_without_usage_is_estimated_and_charged():
    r = U.record("mystery", None, request_chars=3000, reply_chars=300)
    assert r["reported"] is False and r["est"] is True and r["in"] == 1000 and r["out"] == 100
    assert r["cost"] is None and r["budget_cost"] > 0 and r["price_src"] == "unknown"
    assert r["source"] == "background"


def test_record_never_raises(monkeypatch):
    def boom(*a):
        raise OSError("boom")
    monkeypatch.setattr(U, "_month_file", boom)
    assert isinstance(U.record(M, {"prompt_tokens": 1}), dict)


def test_ledger_off_writes_nothing_but_counts(home, monkeypatch):
    monkeypatch.setenv("MH_USAGE_LEDGER", "0")
    t = U.start_task("x", 1)
    r = U.record(M, {"prompt_tokens": 10, "completion_tokens": 1}, task=t)
    U.flush()
    assert r["in"] == 10 and not list((home / "usage").glob("calls-*.jsonl")) and not t.path.exists()
    assert t.totals()["calls"] == 1


def test_record_is_fast_and_memory_totals_are_immediate():
    t = U.start_task("fast", 100)
    t0 = time.perf_counter()
    for _ in range(2000):
        U.record(M, {"prompt_tokens": 100, "completion_tokens": 10}, task=t)
    assert time.perf_counter() - t0 < 0.5
    assert t.totals()["calls"] == 2000                    # visible at once, before any flush
    assert t.spent() > 0
    t0 = time.perf_counter()
    for _ in range(500):
        U.reserve(M, request_chars=100, task=t).close()
    assert time.perf_counter() - t0 < 0.5
    U.flush()
    assert len(_lines(t.path)) == 2000
    assert t.totals()["calls"] == 2000                    # file lines merge without double counting
    assert t.spent() == pytest.approx(2000 * (100 * 0.15 + 10 * 0.6) / 1e6)


def test_background_writer_flushes_without_flush_call(home):
    t = U.start_task("bg", 0)
    U.record(M, {"prompt_tokens": 1, "completion_tokens": 1}, task=t)
    deadline = time.time() + 5
    while time.time() < deadline and not t.path.exists():
        time.sleep(0.1)
    assert t.path.exists() and len(_lines(t.path)) == 1


def test_threads_and_processes_lose_no_lines(home):
    t = U.start_task("conc", 0)
    code = ("from meshpool_harness.core import usage as U\n"
            "for i in range(30):\n"
            "    U.record('deepseek-v4.1-flash', {'prompt_tokens': 10, 'completion_tokens': 5})\n")
    env = dict(os.environ, PYTHONPATH=str(APP), **U.child_env(t, "engine"))
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(APP)) for _ in range(3)]

    def worker():
        for i in range(50):
            U.record(M, {"prompt_tokens": 10, "completion_tokens": 5}, task=t)
            if i % 10 == 0:
                U.flush()

    ths = [threading.Thread(target=worker) for _ in range(8)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert [p.wait(60) for p in procs] == [0, 0, 0]
    U.flush()
    rows = _lines(t.path)
    assert len(rows) == 8 * 50 + 3 * 30
    assert len({r["id"] for r in rows}) == len(rows)
    tot = t.totals()
    assert tot["calls"] == 490 and tot["in"] == 4900
    assert tot["by_source"]["engine"]["calls"] == 90
    assert len(_lines(next((home / "usage").glob("calls-*.jsonl")))) == 490


def test_child_process_writes_into_parent_task(home):
    parent = U.start_task("pc", 0)
    env = dict(os.environ, PYTHONPATH=str(APP), **U.child_env(parent, "office"))
    code = ("from meshpool_harness.core import usage as U\n"
            "t, s = U.current()\nassert t.id == %r and s == 'office'\n"
            "U.record('deepseek-v4.1-flash', {'prompt_tokens': 7, 'completion_tokens': 3})\n" % parent.id)
    subprocess.run([sys.executable, "-c", code], env=env, cwd=str(APP), check=True, timeout=60)
    tot = parent.totals()
    assert tot["calls"] == 1 and tot["by_source"]["office"]["in"] == 7


def test_chat_id_pointer_resolution(monkeypatch):
    t = U.start_task("chat-9", 0)
    assert U.current() == (None, "background")
    monkeypatch.setenv("MH_CHAT_ID", "chat-9")
    got, src = U.current()
    assert got.id == t.id and src == "chat"
    t2 = U.start_task("chat-9", 0)                       # a newer task for the same chat takes the pointer
    assert U.current()[0].id == t2.id
    with U.bind(t, "reviewer"):
        assert U.current() == (t, "reviewer")
    monkeypatch.delenv("MH_CHAT_ID")
    monkeypatch.setenv("MH_USAGE_TASK", t.id)
    monkeypatch.setenv("MH_USAGE_SOURCE", "helper")
    assert U.current()[1] == "helper" and U.current()[0].id == t.id
    assert U.get_task("nope") is None and U.get_task("../x") is None
    assert U.get_task(t.id).chat == "chat-9"


def test_budget_default_from_setting(monkeypatch):
    assert U.start_task("a").budget == 0
    monkeypatch.setenv("MH_BUDGET_USD", "0.5")
    t = U.start_task("a")
    assert t.budget == 0.5
    assert U.start_task("a", parent=t).budget == 0
    monkeypatch.setenv("MH_BUDGET_USD", "junk")
    assert U.start_task("a").budget == 0


# ---- budgets

def test_reserve_no_budget_is_noop():
    t = U.start_task("n", 0)
    r = U.reserve(M, request_chars=10 ** 6, task=t)
    r.close()
    r.close()
    U.reserve(M).close()                                 # no task at all


def test_over_budget_raises_not_runtime_error():
    t = U.start_task("b", 0.10)
    U.record("claude-fable-5-2", {"input_tokens": 0, "output_tokens": 3000}, task=t)     # $0.12
    assert t.spent() == pytest.approx(0.12)
    with pytest.raises(U.BudgetExceeded) as ei:
        U.reserve(M, task=t)
    e = ei.value
    assert not isinstance(e, RuntimeError) and e.task == t.id and e.budget == 0.10
    assert str(e) == ("Spending limit reached: this task has used $0.12 of its $0.10 limit "
                      "(Settings, Spending limit per task).")
    assert str(pickle.loads(pickle.dumps(e))) == str(e)


def test_crossing_call_goes_through():
    t = U.start_task("x", 0.10)
    res = U.reserve(M, request_chars=300, max_tokens=100, task=t)          # nothing spent yet: allowed
    U.record("claude-fable-5-2", {"input_tokens": 0, "output_tokens": 3000}, task=t, reservation=res)
    assert t.spent() > 0.10
    with pytest.raises(U.BudgetExceeded):
        U.reserve(M, task=t)


def test_other_process_spend_counts_after_its_flush(home):
    t = U.start_task("xp", 0.10)
    env = dict(os.environ, PYTHONPATH=str(APP), **U.child_env(t, "engine"))
    code = ("from meshpool_harness.core import usage as U\n"
            "U.record('claude-fable-5-2', {'input_tokens': 0, 'output_tokens': 3000})\n")
    subprocess.run([sys.executable, "-c", code], env=env, cwd=str(APP), check=True, timeout=60)
    with pytest.raises(U.BudgetExceeded):
        U.reserve(M, task=t)


def test_reservations_bound_parallel_fanout():
    t = U.start_task("fan", 0.10)
    est = U.budget_cost("claude-sonnet-5-5", U.estimate(0, 4096 * 3))
    assert 0.04 < est < 0.05
    ok, lock = [], threading.Lock()
    gate = threading.Barrier(96)

    def go():
        gate.wait()
        try:
            r = U.reserve("claude-sonnet-5-5", max_tokens=4096, task=t)
        except U.BudgetExceeded:
            return
        with lock:
            ok.append(r)

    ths = [threading.Thread(target=go) for _ in range(96)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert 1 <= len(ok) <= 3
    [r.close() for r in ok]
    U.reserve(M, task=t).close()                         # all released: allowed again


def test_ancestor_budget_enforced_from_child():
    parent = U.start_task("anc", 0.10)
    child = U.start_task("anc", None, parent=parent)
    assert child.budget == 0
    U.reserve(M, task=child).close()
    U.record("claude-fable-5-2", {"input_tokens": 0, "output_tokens": 3000}, task=child)
    assert parent.spent() == pytest.approx(0.12)
    with pytest.raises(U.BudgetExceeded) as ei:
        U.reserve(M, task=child)
    assert ei.value.task == parent.id


# ---- poll, summarize

def test_poll_is_incremental_and_deduped(home):
    t = U.start_task("p", 0)
    assert t.poll() == []
    a = U.record(M, {"prompt_tokens": 1, "completion_tokens": 1}, task=t)
    assert [r["id"] for r in t.poll()] == [a["id"]]
    assert t.poll() == []
    U.flush()                                            # the same record arrives from the file: not returned again
    assert t.poll() == []
    b = U.record(M, {"prompt_tokens": 1, "completion_tokens": 1}, task=t)
    c = U.record(M, {"prompt_tokens": 1, "completion_tokens": 1}, task=t)
    U.flush()
    assert [r["id"] for r in t.poll()] == [b["id"], c["id"]]
    # another process's lines (here: appended straight to the file) show up too
    line = json.dumps({"id": "ext1", "ts": time.time(), "model": M, "in": 5, "out": 1, "reported": True,
                       "cost": 0.0, "budget_cost": 0.0, "source": "x"}) + "\n"
    with open(t.path, "ab") as f:
        f.write(line.encode())
    assert [r["id"] for r in t.poll()] == ["ext1"]


def test_summarize_by_model_chat_day(home):
    t1, t2 = U.start_task("chat-a", 0), U.start_task("chat-b", 0)
    U.record(M, {"prompt_tokens": 1000, "completion_tokens": 100}, task=t1, source="chat")
    U.record(M, {"prompt_tokens": 1000, "completion_tokens": 100}, task=t2, source="engine")
    U.record("claude-opus-5-5", {"input_tokens": 100, "output_tokens": 10}, task=t2, source="engine")
    U.record("mystery", None, task=t2, source="helper", request_chars=30, reply_chars=30)
    U.flush()
    s = U.summarize(U.read_records(0))
    assert s["calls"] == 4 and s["unpriced_calls"] == 1 and s["unreported_calls"] == 1
    assert s["by_model"][M]["calls"] == 2 and s["by_model"]["mystery"]["unpriced_calls"] == 1
    assert s["by_chat"]["chat-a"]["calls"] == 1 and s["by_chat"]["chat-b"]["calls"] == 3
    assert s["by_source"]["engine"]["calls"] == 2
    day = time.strftime("%Y-%m-%d")
    assert s["by_day"][day]["calls"] == 4
    assert s["cost"] == pytest.approx(2 * (1000 * 0.15 + 100 * 0.6) / 1e6 + (100 * 4 + 10 * 20) / 1e6)
    assert s["budget_cost"] > s["cost"]
    assert list(U.read_records(time.time() + 10)) == []
    # damaged line is skipped
    f = next((home / "usage").glob("calls-*.jsonl"))
    with open(f, "ab") as fh:
        fh.write(b"{not json\n")
    assert len(list(U.read_records(0))) == 4
