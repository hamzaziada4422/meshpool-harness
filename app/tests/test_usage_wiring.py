"""WS04 WP3a: the usage ledger wired into the chat agent, the engines' loop and the office jobs (S-02, X-10, W-14,
M04-02, M35-02, M28-07, M28-02 agent part). Mock servers only; nothing reaches a real model."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness.agent import core as K
from meshpool_harness.agent import office_jobs as OJ
from meshpool_harness.agent.core import Agent, Interrupted
from meshpool_harness.core import gateway as GW
from meshpool_harness.core import usage
from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import Caps, _run
from meshpool_harness.core.tools import Workspace

KEY = "sk-test-secret-12345678"
MODEL = "deepseek-v4.1-flash"


class Srv:
    """A threaded OpenAI-style server. reply(n, body) gives the nth assistant message; every reply carries usage."""

    def __init__(self, reply=None, delay=0.0, prompt=100, completion=10):
        self.reply = reply or (lambda n, body: {"role": "assistant", "content": "Done."})
        self.delay, self.prompt, self.completion = delay, prompt, completion
        self.lock = threading.Lock()
        self.chats = self.running = self.max_running = 0
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not self.path.endswith("/chat/completions"):          # a capability probe, not a model call
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                with srv.lock:
                    srv.chats += 1
                    n = srv.chats
                    srv.running += 1
                    srv.max_running = max(srv.max_running, srv.running)
                try:
                    time.sleep(srv.delay)
                    data = json.dumps({"id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                                               "message": srv.reply(n, body)}],
                                       "usage": {"prompt_tokens": srv.prompt, "completion_tokens": srv.completion}}
                                      ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    with srv.lock:
                        srv.running -= 1

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_API_WIRE", raising=False)   # another test file can leave an injected "anthropic"
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    monkeypatch.delenv("MH_MAX_INFLIGHT", raising=False)
    monkeypatch.delenv("MH_BUDGET_USD", raising=False)
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MH_RECOVER_MIN", "0")
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "0")
    GW._CFG["t"] = 0
    yield tmp_path
    GW._CFG["t"] = 0


@pytest.fixture
def serve(env, monkeypatch):
    made = []

    def make(**kw):
        s = Srv(**kw)
        made.append(s)
        monkeypatch.setenv("MESHPOOL_BASE_URL", s.base)
        return s
    yield make
    for s in made:
        s.close()


def _agent(tmp_path, events=None):
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=(events.append if events is not None else None))
    a.model = MODEL
    return a


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


# ---------- S-02, X-10: nested engine calls are in the session usage, with the provider's numbers ------------------
ENGINE = """
import json
from meshpool_harness.core import usage
for _ in range(41):
    usage.record("deepseek-v4.1-flash", {"prompt_tokens": 50000, "completion_tokens": 900,
                                         "prompt_tokens_details": {"cached_tokens": 10000}})
usage.flush()
print(json.dumps({"ok": True, "steps": []}))
"""


def test_nested_office_engine_calls_reach_the_session_usage(serve, monkeypatch, tmp_path):
    srv = serve(reply=lambda n, body: _call("office_task", {"engine": "sheets", "request": "Build a spreadsheet "
                                                            "with a monthly budget and totals"})
                if n == 1 else {"role": "assistant", "content": "Done."})
    orig = OJ.new_job

    def fake_job(*a, **k):                                  # the engine is a tiny process that records 41 calls
        jid = orig(*a, **k)
        OJ.JOBS[jid]["cmd"] = [sys.executable, "-c", ENGINE]
        return jid
    monkeypatch.setattr(OJ, "new_job", fake_job)
    events = []
    a = _agent(tmp_path, events)
    a.send("make me a budget spreadsheet")
    assert srv.chats == 2
    assert a.usage["calls"] == 2 + 41
    assert a.usage["in"] == 2 * srv.prompt + 41 * 50000
    assert a.usage["cached"] == 41 * 10000
    assert a.usage["out"] == 2 * srv.completion + 41 * 900
    nested = [e for e in events if e["type"] == "usage" and e.get("source") == "engine:sheets"]
    assert len(nested) == 41 and all(e["aux"] is True and e["in"] == 50000 for e in nested)
    own = [e for e in events if e["type"] == "usage" and e.get("source") == "chat"]
    assert len(own) == 2 and not any(e["aux"] for e in own)          # the context meter reads these only
    assert a.usage["unreported_calls"] == 0 and a.usage["cost"] > 0


# ---------- M04-02: a chat stops on the spending limit ---------------------------------------------------------------
def test_a_chat_stops_after_the_call_that_crosses_the_limit(serve, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    srv = serve(reply=lambda n, body: _call("glob", {"pattern": "*.py"}, f"c{n}"), prompt=2_000_000)
    events = []
    a = _agent(tmp_path, events)
    a.send("look around")
    assert srv.chats <= 2
    assert a.budget_stopped is True
    errs = [e["text"] for e in events if e["type"] == "error"]
    assert errs and "Spending limit" in errs[0] and "Raise the limit in Settings" in errs[0]
    assert events[-1]["type"] == "turn_end"
    a.send("again")                                                  # a new message is a new task: it may go on
    assert a.budget_stopped is True and srv.chats <= 4


def test_a_shared_task_keeps_counting_across_turns(serve, monkeypatch, tmp_path):
    srv = serve(prompt=2_000_000)
    a = _agent(tmp_path)
    a.usage_task = usage.start_task(chat=a.id, budget_usd=0.10)
    a.send("one")
    a.send("two")                                                    # the session's task is already over its limit
    assert srv.chats == 1 and a.budget_stopped is True


def test_parallel_fan_out_stops_at_the_limit(env, monkeypatch):
    orig = usage.record
    monkeypatch.setattr(usage, "record", lambda *a, **k: (time.sleep(0.2), orig(*a, **k))[1])   # calls overlap
    task = usage.start_task(chat="fan", budget_usd=0.10)
    client = MockClient([{"role": "assistant", "content": "x"}] * 96, usage={"prompt_tokens": 3_000_000,
                                                                              "completion_tokens": 0})
    ok, refused = [], []

    def one():
        try:
            with usage.bind(task, "helper"):
                client.chat(model="claude-opus-5-5", messages=[{"role": "user", "content": "hi"}], max_tokens=4096)
            ok.append(1)
        except usage.BudgetExceeded:
            refused.append(1)
    ts = [threading.Thread(target=one) for _ in range(96)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert len(ok) <= 2 and len(ok) + len(refused) == 96 and ok


# ---------- M35-02: the reply that crosses the budget is used ----------------------------------------------------------
def _loop(tmp_path, script, usage_dict):
    client = MockClient(script, usage=usage_dict)
    res = _run("do it", client=client, model=MODEL, profile=None, ws=Workspace(str(tmp_path), str(tmp_path / ".run")),
               ctx={"task_id": "t", "model": "mock", "profile": "deepseek"}, system_text="sys",
               caps=Caps(max_turns=8), require_check=False)
    return res, client


def test_a_crossing_reply_that_finishes_is_accepted(env, monkeypatch):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    res, client = _loop(env, [tool_call("finish", {"answer": "done", "evidence": []})],
                        {"prompt_tokens": 2_000_000, "completion_tokens": 10})
    assert res.ok and res.reason == "finished" and res.answer == "done"
    assert len(client.requests) == 1


def test_a_crossing_reply_without_finish_stops_the_next_call(env, monkeypatch):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    script = [tool_call("glob", {"pattern": "*"}, "c1"), tool_call("finish", {"answer": "done", "evidence": []})]
    res, client = _loop(env, script, {"prompt_tokens": 2_000_000, "completion_tokens": 10})
    assert not res.ok and res.reason.startswith("Spending limit")
    assert len(client.requests) == 1


def test_the_budget_prices_override_still_applies(env, monkeypatch):
    monkeypatch.setenv("MH_BUDGET_USD", "1")
    monkeypatch.setenv("MH_BUDGET_PRICES", "100,10,100")             # every model counts at this price
    res, client = _loop(env, [tool_call("glob", {"pattern": "*"}), tool_call("finish", {"answer": "d", "evidence": []})],
                        {"prompt_tokens": 100_000, "completion_tokens": 0})
    assert res.reason.startswith("Spending limit") and len(client.requests) == 1


# ---------- W-14: the price follows the model; no usage is not free -----------------------------------------------------
def test_the_same_tokens_cost_different_amounts_per_model(env):
    n = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    out = {}
    for model in ("claude-opus-5-5", MODEL):
        task = usage.start_task(chat="p-" + model, budget_usd=1.0)
        client = MockClient([{"role": "assistant", "content": "x"}] * 2, usage=n)
        with usage.bind(task, "helper"):
            client.chat(model=model, messages=[{"role": "user", "content": "hi"}])
            try:
                client.chat(model=model, messages=[{"role": "user", "content": "hi"}])
                out[model] = "ok"
            except usage.BudgetExceeded:
                out[model] = "refused"
    assert out == {"claude-opus-5-5": "refused", MODEL: "ok"}


def test_a_reply_without_usage_is_charged_as_an_estimate(env):
    task = usage.start_task(chat="nou", budget_usd=5.0)
    client = MockClient([{"role": "assistant", "content": "x" * 3000}], usage=[None])
    with usage.bind(task, "helper"):
        client.chat(model="claude-opus-5-5", messages=[{"role": "user", "content": "y" * 3000}])
    t = task.totals()
    assert t["calls"] == 1 and t["unreported_calls"] == 1 and task.spent() > 0


def test_the_gateway_host_price_comes_before_the_list_price():
    assert usage.price_for(MODEL, "https://llm.meshpool.ai/v1")["in"] == pytest.approx(0.15 * 2.19, rel=0.01)
    assert usage.price_for(MODEL, "https://other.example/v1")["in"] == 0.15
    assert usage.price_for(MODEL)["in"] == 0.15


# ---------- reviewer / grader: counted once, as aux -------------------------------------------------------------------
def test_a_reviewer_call_is_counted_once_and_as_aux(serve, tmp_path):
    srv = serve(reply=lambda n, body: {"role": "assistant", "content": "LGTM"}, prompt=500, completion=7)
    events = []
    a = _agent(tmp_path, events)
    turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
    assert a._aux_complete("system", "user", turn) == "LGTM"
    assert a.usage["calls"] == 1 and a.usage["in"] == 500 and turn["calls"] == 1
    ev = [e for e in events if e["type"] == "usage"]
    assert len(ev) == 1 and ev[0]["aux"] is True and ev[0]["source"] == "review"
    a._absorb_usage(turn)                                             # nothing new: never added twice
    assert a.usage["calls"] == 1
    assert srv.chats == 1


# ---------- M28-07: Stop before the connection exists ----------------------------------------------------------------
def test_stop_before_connect_keeps_the_slot_and_sends_nothing(serve, monkeypatch, tmp_path):
    srv = serve()
    real = K._urlopen

    def slow(req, timeout, sink):
        time.sleep(1.0)
        return real(req, timeout, sink)
    monkeypatch.setattr(K, "_urlopen", slow)
    a = _agent(tmp_path)
    a.messages.append({"role": "user", "content": "hi"})
    from meshpool_harness import providers as PV
    prov, model = PV.split(a.current_model())
    base, key = PV.endpoint(prov)
    gate = GW.health(base, key, model).gate
    box = {}

    def go():
        try:
            a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
        box["at"] = time.time()
    th = threading.Thread(target=go)
    th.start()
    until = time.time() + 5                     # wait for the worker to hold its slot (a fixed sleep races under load)
    while not gate.inflight and time.time() < until:
        time.sleep(0.01)
    t0 = time.time()
    a._stop = True
    th.join(5)
    assert isinstance(box.get("e"), Interrupted) and box["at"] - t0 < 0.8   # Stop answers quickly
    assert gate.inflight == 1                                          # the abandoned request still holds its slot
    deadline = time.time() + 5
    while gate.inflight and time.time() < deadline:
        time.sleep(0.05)
    assert gate.inflight == 0                                          # given back when the worker ends
    assert srv.chats == 0                                              # and the request never went out


# ---------- M28-02 (agent part): the reviewer call goes through the gate -----------------------------------------------
def test_reviewer_calls_obey_the_cap(serve, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_MAX_INFLIGHT", "2")
    GW._CFG["t"] = 0
    srv = serve(delay=0.4)
    a = _agent(tmp_path)
    errs = []

    def one():
        try:
            a._aux_complete("system", "user", {"in": 0, "cached": 0, "out": 0, "calls": 0})
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=one) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errs and srv.chats == 4 and srv.max_running == 2


def test_an_empty_reasoning_reply_that_is_retried_is_billed_too(env, monkeypatch):
    """A reasoning model that thinks through its limit gets one larger retry; both calls are billed."""
    from meshpool_harness.core import usage as U
    from meshpool_harness.core.wire import SimpleLedger
    replies = [{"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 4000}},
               {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}]
    monkeypatch.setattr(SimpleLedger, "_chat", lambda self, **kw: replies.pop(0))
    task = U.start_task(label="t")
    with U.bind(task, "engine"):
        led = SimpleLedger("t")
        out = led.chat(model="deepseek-v4.1-flash", messages=[{"role": "user", "content": "hi"}], max_tokens=4000)
        U.record("deepseek-v4.1-flash", out["usage"])          # what LiveClient records for the reply it returns
    t = task.totals()
    assert t["calls"] == 2 and t["out"] == 4500
