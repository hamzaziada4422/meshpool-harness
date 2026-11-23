"""WS04 WP3b: the spending limit in chats, goals and scheduled runs (M04-02); usage of external harnesses, the relay,
image calls and test calls in the one ledger (S-02, G-09, X-10); a 429 on the chat's own call lowers the gate (P-12).
Mock servers only; nothing reaches a real model."""
import base64
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from meshpool_harness.agent.core import Agent
from meshpool_harness.core import gateway as GW
from meshpool_harness.core import usage
from meshpool_harness.harnesses.acp import ACPAgent
from meshpool_harness.harnesses.pi_rpc import PiAgent
from meshpool_harness.ui import agent_api as A

KEY = "sk-test-secret-12345678"
MODEL = "deepseek-v4.1-flash"


class Srv:
    """An OpenAI-style server. reply(n, body) is the nth assistant message; `first` lists statuses for the first
    calls (e.g. [429]); every 200 reply reports `prompt` prompt tokens."""

    def __init__(self, reply, prompt=2_000_000, first=()):
        self.reply, self.prompt, self.first = reply, prompt, list(first)
        self.lock = threading.Lock()
        self.chats = 0
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not self.path.endswith("/chat/completions"):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                with srv.lock:
                    srv.chats += 1
                    n = srv.chats
                    status = srv.first.pop(0) if srv.first else 200
                if status != 200:
                    data = json.dumps({"error": {"message": "slow down"}}).encode()
                    self.send_response(status)
                    self.send_header("Retry-After", "1")
                else:
                    data = json.dumps({"id": "x", "choices": [{"index": 0, "finish_reason": "stop",
                                                               "message": srv.reply(n, body)}],
                                       "usage": {"prompt_tokens": srv.prompt, "completion_tokens": 10}}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _call(n):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{n}", "type": "function", "function": {"name": "glob", "arguments": json.dumps({"pattern": "*.py"})}}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    monkeypatch.setenv("MH_MODEL", MODEL)
    monkeypatch.setenv("MH_AGENT_PERMISSIONS", "auto")
    monkeypatch.delenv("MH_BUDGET_USD", raising=False)
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setenv("MH_RECOVER_MIN", "0")
    monkeypatch.setenv("MH_ENDPOINT_PRICES", "0")
    monkeypatch.setenv("MH_AUTO_RESUME", "0")
    GW._CFG["t"] = 0
    yield tmp_path
    GW._CFG["t"] = 0


@pytest.fixture
def serve(env, monkeypatch):
    made = []

    def make(**kw):
        s = Srv(kw.pop("reply", lambda n, body: _call(n)), **kw)
        made.append(s)
        monkeypatch.setenv("MESHPOOL_BASE_URL", s.base)
        return s
    yield make
    for s in made:
        s.close()


def _session(tmp_path):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    s = A.get(A.create({"workdir": str(work), "kind": "code", "harness": "meshpool-code"})["id"])
    s.agent.model = MODEL
    return s


def _idle(s, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if s.state == "idle":
            return
        time.sleep(0.05)
    raise AssertionError("the session never went idle")


def _errors(s):
    return [e["text"] for e in s.events if e.get("type") == "error"]


# ---------- M04-02: chats, goals and scheduled runs stop on the spending limit ------------------------------------
def test_a_chat_message_stops_on_the_limit(serve, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    srv = serve()
    s = _session(tmp_path)
    s.send("look around")
    _idle(s)
    assert srv.chats <= 2
    assert any(t.startswith("Spending limit") for t in _errors(s))
    assert s.agent.budget_stopped is True


def test_a_goal_stops_on_the_limit(serve, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    srv = serve()
    s = _session(tmp_path)
    s.set_goal("keep looking")
    s.send("start")
    _idle(s)
    assert srv.chats <= 2                                             # no further turns after the limit
    assert s.goal == "keep looking"                                   # paused, not cleared
    assert any("Goal paused: the spending limit" in e.get("text", "") for e in s.events if e["type"] == "status")


def test_goal_continuations_share_the_task_and_a_new_message_gets_a_new_one(serve, tmp_path):
    texts = iter(["Part one.\nGOAL: CONTINUE", "Part two.\nGOAL: CONTINUE", "All done.\nGOAL: DONE"])
    # each turn acts once (a tool call), then answers: the goal loop sees progress
    serve(reply=lambda n, body: _call(n) if n % 2 else {"role": "assistant", "content": next(texts)}, prompt=100)
    s = _session(tmp_path)
    seen = []
    orig = s._start_turn

    def spy(text, images, cont=False):
        orig(text, images, cont)
        seen.append((cont, s.agent.usage_task))
    s._start_turn = spy
    s.set_goal("three parts")
    s.send("start")
    end = time.time() + 60
    while time.time() < end and len(seen) < 3:
        time.sleep(0.05)
    _idle(s)
    assert [c for c, _ in seen] == [False, True, True]
    assert seen[0][1] is seen[1][1] is seen[2][1] and seen[0][1] is not None
    s.send("another")                                                 # not a continuation: a new task
    _idle(s)
    assert s.agent.usage_task is not seen[0][1]


def test_a_new_message_after_a_budget_stop_has_a_fresh_limit(serve, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    srv = serve(reply=lambda n, body: _call(n) if n <= 1 else {"role": "assistant", "content": "ok"})
    s = _session(tmp_path)
    s.send("one")
    _idle(s)
    first = s.agent.usage_task
    assert s.agent.budget_stopped is True and srv.chats == 1
    s.send("two")                                                     # a fresh task: its own limit, the call runs
    _idle(s)
    assert s.agent.usage_task is not first and srv.chats >= 2
    assert first.spent() > 0.10


def test_a_scheduled_run_stops_on_the_limit(serve, monkeypatch, tmp_path):
    monkeypatch.setenv("MH_BUDGET_USD", "0.10")
    srv = serve()
    work = tmp_path / "work"
    work.mkdir()
    sid = A.run_scheduled({"id": "t1", "name": "Nightly", "prompt": "look around", "folder": str(work),
                           "kind": "code", "harness": "meshpool-code"})
    s = A.get(sid)
    _idle(s)
    assert srv.chats <= 2
    assert any(t.startswith("Spending limit") for t in _errors(s))
    assert s.agent.usage_task.label == "scheduled"


# ---------- S-02, G-09, X-10: external harnesses report into the ledger -------------------------------------------
def _records(task):
    usage.flush()
    return task.records()


def test_pi_message_end_usage_goes_to_the_ledger(env, tmp_path):
    p = PiAgent({"id": "pi", "name": "Pi"}, str(tmp_path))
    p.usage_task = usage.start_task(chat=p.id)
    p._on({"type": "message_end", "message": {"role": "assistant", "model": "deepseek-v4.1-flash", "content": [],
                                               "usage": {"input": 1000, "output": 200, "cacheRead": 300,
                                                         "cacheWrite": 0, "cost": {"total": 0.002}}}})
    recs = _records(p.usage_task)
    assert len(recs) == 1 and recs[0]["source"] == "external:pi"
    assert recs[0]["in"] == 1300 and recs[0]["cached"] == 300 and recs[0]["out"] == 200
    assert recs[0]["reported"] is True and recs[0]["reported_cost"] == 0.002


def test_acp_prompt_response_usage_and_the_unreported_call(env, tmp_path):
    a = ACPAgent({"id": "codex", "name": "Codex"}, str(tmp_path))
    a.usage_task = usage.start_task(chat=a.id)
    answers = iter([{"stopReason": "end_turn", "usage": {"inputTokens": 500, "outputTokens": 40,
                                                          "cachedReadTokens": 100, "cachedWriteTokens": 0,
                                                          "thoughtTokens": 10, "totalTokens": 640}},
                    {"stopReason": "end_turn"}])
    a._request = lambda method, params, *x, **k: next(answers)
    a._text = ["hello"]
    a._prompt_call([{"type": "text", "text": "hi there"}])
    a._prompt_call([{"type": "text", "text": "and again"}])
    one, two = _records(a.usage_task)
    assert one["source"] == "external:codex" and one["reported"] is True
    assert one["in"] == 600 and one["cached"] == 100 and one["out"] == 40 and one["reasoning"] == 10
    assert two["reported"] is False and two["est"] is True and two["in"] >= 1       # counted, flagged, never free
    assert a.usage_task.totals()["unreported_calls"] == 1


def test_codex_exec_turn_completed_usage_normalizes():
    n = usage.normalize({"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 300,
                         "reasoning_output_tokens": 120})
    assert n == {"in": 1000, "cached": 800, "cache_write": 0, "out": 300, "reasoning": 120, "reported": True}
    n = usage.normalize({"inputTokens": 500, "outputTokens": 40, "cachedReadTokens": 100, "cachedWriteTokens": 20,
                         "thoughtTokens": 10})
    assert n["in"] == 620 and n["cached"] == 100 and n["cache_write"] == 20 and n["reasoning"] == 10


# ---------- the relay, image calls and test calls are counted ------------------------------------------------------
def test_the_chatgpt_relay_records_each_reply(env):
    from meshpool_harness.core import chatgpt_relay as R
    task = usage.start_task(chat="relay")
    out = {"choices": [{"message": {"role": "assistant", "content": "hello"}}],
           "usage": {"prompt_tokens": 70, "completion_tokens": 5}}
    with usage.bind(task, "chat"):
        R._account("gpt-5", out, {"messages": [{"role": "user", "content": "hi"}]})
    (r,) = _records(task)
    assert r["source"] == "external:chatgpt-relay" and r["in"] == 70 and r["out"] == 5


def test_a_forge_image_call_is_counted(env):
    from engines.forge import design as D
    task = usage.start_task(chat="img")
    buf = io.BytesIO()
    Image.new("RGB", (16, 9), "blue").save(buf, "PNG")
    png = base64.b64encode(buf.getvalue()).decode()
    c = D.ImageClient(model="gpt-image-2", base="http://127.0.0.1:1/v1",
                      post=lambda url, body, t: (200, {"data": [{"b64_json": png}],
                                                       "usage": {"input_tokens": 50, "output_tokens": 1000}}))
    with usage.bind(task, "engine:forge-image"):
        c.generate("a blue slide")
    recs = _records(task)
    assert len(recs) == 1 and recs[0]["source"] == "engine:forge-image" and recs[0]["out"] == 1000


def test_the_live_check_and_the_settings_test_are_counted(serve, env):
    from meshpool_harness import cli
    from meshpool_harness import provider_setup as PS
    srv = serve(reply=lambda n, body: {"role": "assistant", "content": "ok"}, prompt=7)
    r = cli.ping(MODEL)
    assert r["ok"] is True
    st, data, _ = PS.probe_chat(srv.base, KEY, MODEL)
    assert st == 200
    usage.flush()
    got = [x for x in usage.read_records() if x["source"] == "check"]
    assert len(got) == 2 and all(x["in"] == 7 and x["reported"] for x in got)


# ---------- P-12: a 429 on the chat's own call lowers the gate -----------------------------------------------------
def test_a_429_on_the_chat_call_lowers_the_gate(serve, tmp_path):
    srv = serve(reply=lambda n, body: {"role": "assistant", "content": "Done."}, prompt=100, first=[429])
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None)
    a.model = MODEL
    g = GW.health(srv.base, KEY, MODEL).gate
    before = g.limit                                                  # the cap of this (local) endpoint
    assert before >= 2
    a.send("hello")
    assert srv.chats == 2 and g.limit == before // 2
