"""Transport behaviour of the model-reply readers (core/stream.py, wire.sse_to_chat, SimpleLedger, ChatGPT reader)."""
import email.utils
import http.client
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness.core import chatgpt as CG
from meshpool_harness.core import stream as ST
from meshpool_harness.core import wire as W


def _ev(obj):
    return "data: " + json.dumps(obj) + "\n\n"


def _delta(**d):
    return _ev({"choices": [{"index": 0, "delta": d}]})


def _finish(reason="stop"):
    return _ev({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]})


def _lines(text):
    return text.splitlines(keepends=True)


@pytest.fixture
def serve(tmp_path, monkeypatch):
    """serve(handler_cls, path_prefix="/v1") -> a SimpleLedger pointed at a local server with that handler."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    servers = []

    def make(handler, wire=None):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
        monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-1234567890")
        if wire:
            monkeypatch.setenv("MH_API_WIRE", wire)
        return W.SimpleLedger("t")
    yield make
    for s in servers:
        s.shutdown()


def _json_handler(seen, reply=None, status=200, headers=()):
    reply = reply if reply is not None else {"choices": [{"message": {"role": "assistant", "content": "ok"},
                                                          "finish_reason": "stop"}], "usage": {}}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(raw)
            b = json.dumps(reply).encode()
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    return H


MSG = [{"role": "user", "content": "hi"}]


# ---- W-07 -----------------------------------------------------------------------------------------------------------
def test_parallel_calls_sharing_index_0_stay_separate():
    text = (_delta(tool_calls=[{"index": 0, "id": "a", "function": {"name": "read", "arguments": ""}}])
            + _delta(tool_calls=[{"index": 0, "id": "b", "function": {"name": "grep", "arguments": ""}}])
            + _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"p": 1}'}}])
            + _finish("tool_calls") + "data: [DONE]\n\n")
    calls = W.sse_to_chat(_lines(text))["choices"][0]["message"]["tool_calls"]
    assert [(c["id"], c["function"]["name"]) for c in calls] == [("a", "read"), ("b", "grep")]
    assert json.loads(calls[1]["function"]["arguments"]) == {"p": 1}
    assert json.loads(calls[0]["function"]["arguments"] or "{}") == {}


def test_parallel_calls_with_arguments_in_between_stay_separate():
    text = (_delta(tool_calls=[{"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"x": 1}'}}])
            + _delta(tool_calls=[{"index": 0, "id": "b", "function": {"name": "grep", "arguments": '{"y": 2}'}}])
            + _finish("tool_calls"))
    calls = W.sse_to_chat(_lines(text))["choices"][0]["message"]["tool_calls"]
    assert [json.loads(c["function"]["arguments"]) for c in calls] == [{"x": 1}, {"y": 2}]


# ---- W-21 -----------------------------------------------------------------------------------------------------------
def test_multiline_data_field():
    text = ('data: {"choices":[{"index":0,\n'
            'data: "delta":{"content":"hel"}}]}\n\n'
            + _delta(content="lo") + _finish() + "data: [DONE]\n\n")
    assert W.sse_to_chat(_lines(text))["choices"][0]["message"]["content"] == "hello"
    # a server that omits the blank line: every line is its own JSON value
    nb = ("data: " + json.dumps({"choices": [{"delta": {"content": "a"}}]}) + "\n"
          + "data: " + json.dumps({"choices": [{"delta": {"content": "b"}}]}) + "\n"
          + "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n"
          + "data: [DONE]\n")
    assert W.sse_to_chat(_lines(nb))["choices"][0]["message"]["content"] == "ab"


def test_iter_sse_framing():
    got = list(ST.iter_sse(_lines(": hi\nevent: x\ndata: a\ndata:b\n\ndata: c")))
    assert got == [("x", "a\nb"), ("message", "c")]        # one leading space stripped, EOF dispatches


# ---- W-09 -----------------------------------------------------------------------------------------------------------
def test_engine_call_on_keepalive_only_stream_ends(serve, monkeypatch):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(50):                    # 10 s of nothing but keep-alives
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass
    led = serve(H)
    monkeypatch.setenv("MH_STREAM_FIRST_S", "1")
    monkeypatch.setenv("MH_STREAM_IDLE_S", "1")
    t0 = time.time()
    with pytest.raises(RuntimeError, match="HTTP 0"):
        led.chat(model="m", messages=MSG, retries=0)
    assert time.time() - t0 < 4


def test_chatgpt_reader_keepalive_only_ends():
    def gen():
        for _ in range(50):
            time.sleep(0.2)
            yield ": keep-alive\n"
            yield "\n"
    t0 = time.time()
    with pytest.raises(RuntimeError, match="HTTP 0"):
        CG.read_stream(gen(), data_idle=1)
    assert time.time() - t0 < 4


def test_iter_sse_ping_events_do_not_count_as_data():
    now = [0.0]

    def clock():
        return now[0]

    def gen():
        for i in range(10):
            now[0] += 1
            yield "event: ping\n"
            yield 'data: {"type": "ping"}\n'
            yield "\n"
    with pytest.raises(RuntimeError, match="only keep-alives"):
        list(ST.iter_sse(gen(), data_idle=3, clock=clock))


# ---- H-26 -----------------------------------------------------------------------------------------------------------
def test_lone_surrogates_never_reach_the_wire(serve):
    seen = []
    led = serve(_json_handler(seen))
    led.chat(model="m", messages=[{"role": "user", "content": "a\ud800b\udfff"}], retries=0)
    raw = seen[0]
    text = raw.decode("utf-8")                          # strict: a lone surrogate cannot be encoded at all
    assert "\\ud800" not in text.lower() and "\\udfff" not in text.lower()
    assert json.loads(text)["messages"][0]["content"] == "a�b�"


def test_encode_body_cleans_keys_and_nested_values():
    out = ST.encode_body({"k\ud800": [{"v": "x\udc00"}], "ok": "é"})
    assert json.loads(out.decode("utf-8")) == {"k�": [{"v": "x�"}], "ok": "é"}
    assert "é".encode("utf-8") in out


# ---- W-10 -----------------------------------------------------------------------------------------------------------
def test_retry_after_forms():
    f = ST.retry_after_seconds
    assert f({"Retry-After": "5"}) == 5
    assert f({"retry-after": " 2.5 "}) == 2.5
    assert f({"retry-after-ms": "1500", "Retry-After": "9"}) == 1.5
    assert f({"Retry-After": "600"}) == 600                       # no cap here
    assert 100 < f({"Retry-After": email.utils.formatdate(time.time() + 120, usegmt=True)}) <= 120
    assert f({"Retry-After": email.utils.formatdate(time.time() - 500, usegmt=True)}) == 0
    assert f({"Retry-After": "soon"}) is None and f({}) is None and f(None) is None
    h = http.client.HTTPMessage()
    h["retry-after"] = "7"
    assert f(h) == 7


def test_engine_429_carries_http_date_retry_after(serve):
    seen = []
    when = email.utils.formatdate(time.time() + 300, usegmt=True)
    led = serve(_json_handler(seen, reply={"error": "slow down"}, status=429, headers=[("Retry-After", when)]))
    with pytest.raises(RuntimeError) as e:
        led.chat(model="m", messages=MSG, retries=0)
    m = re.search(r"\[retry-after: ([0-9.]+)\]", str(e.value))
    assert m and 280 < float(m.group(1)) <= 300                  # the real wait, not a 60 s cap


# ---- W-22 -----------------------------------------------------------------------------------------------------------
def test_prepare_messages_scopes_reasoning():
    msgs = [{"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "reasoning_content": "old"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": None, "reasoning_content": "now", "tool_calls": [{"id": "c"}]},
            {"role": "tool", "tool_call_id": "c", "content": "r", "reasoning_content": "stray"}]
    before = json.dumps(msgs)
    out = ST.prepare_messages(msgs)
    assert json.dumps(msgs) == before                             # input untouched
    assert "reasoning_content" not in out[1]
    assert out[3]["reasoning_content"] == "now"
    assert "reasoning_content" not in out[4]
    assert ST.prepare_messages([{"role": "assistant", "reasoning_content": "x"}])[0]["reasoning_content"] == "x"


def test_engine_sends_reasoning_within_the_turn(serve):
    seen = []
    led = serve(_json_handler(seen))
    msgs = [{"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "reasoning_content": "old"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": None, "reasoning_content": "now",
             "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "r"}]
    led.chat(model="m", messages=msgs, retries=0)
    sent = json.loads(seen[0])["messages"]
    assert "reasoning_content" not in sent[1] and sent[3]["reasoning_content"] == "now"
    assert "reasoning_content" in msgs[1]                         # the caller's list is not changed


# ---- W-12 -----------------------------------------------------------------------------------------------------------
def test_anthropic_engine_call_is_streamed(serve):
    seen = {}

    def sse(ev, obj):
        return f"event: {ev}\ndata: {json.dumps(obj)}\n\n".encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["path"] = self.path
            seen["accept"] = self.headers.get("Accept")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for ev, obj in [
                ("message_start", {"type": "message_start", "message": {"id": "m1", "model": "claude-x",
                                                                         "usage": {"input_tokens": 4}}}),
                ("ping", {"type": "ping"}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "Hel"}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "lo"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("content_block_start", {"type": "content_block_start", "index": 1,
                                         "content_block": {"type": "tool_use", "id": "tu1", "name": "read",
                                                           "input": {}}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                         "delta": {"type": "input_json_delta", "partial_json": '{"path": '}}),
                ("ping", {"type": "ping"}),
                ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                         "delta": {"type": "input_json_delta", "partial_json": '"a.txt"}'}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 1}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                                   "usage": {"output_tokens": 9}}),
                ("message_stop", {"type": "message_stop"}),
            ]:
                self.wfile.write(sse(ev, obj))
    led = serve(H, wire="anthropic")
    out = led.chat(model="claude-x", messages=MSG, retries=0)
    assert seen["path"] == "/v1/messages" and seen["body"]["stream"] is True
    assert "event-stream" in (seen["accept"] or "")
    msg = out["choices"][0]["message"]
    assert msg["content"] == "Hello"
    assert msg["tool_calls"][0]["function"]["name"] == "read"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}
    assert out["choices"][0]["finish_reason"] == "tool_calls"


# ---- read_body ------------------------------------------------------------------------------------------------------
def test_read_body_deadline():
    now = [0.0]

    class R:
        def read(self, n=-1):
            now[0] += 2
            return b"x"
    with pytest.raises(RuntimeError, match=r"HTTP 0: connection problem \(timed out: the reply body took longer than 5 s\)"):
        ST.read_body(R(), 5, clock=lambda: now[0])

    class Done:
        def __init__(self):
            self.parts = [b"ab", b"cd", b""]

        def read(self, n=-1):
            return self.parts.pop(0)
    assert ST.read_body(Done(), 5, clock=lambda: 0.0) == b"abcd"


# ---- H-17 live deltas -----------------------------------------------------------------------------------------------
def test_think_tags_not_in_live_deltas():
    text = (_delta(content="<thi") + _delta(content="nk>plan the</th") + _delta(content="ink>Answer ")
            + _delta(content="here") + _finish())
    live, think = [], []
    out = W.sse_to_chat(_lines(text), on_text=live.append, on_reasoning=think.append)
    assert "".join(live) == "Answer here" and "<" not in "".join(live)
    assert "".join(think) == "plan the"
    assert out["choices"][0]["message"]["content"] == "Answer here"


def test_reasoning_delta_keys_reach_on_reasoning():
    text = _delta(reasoning_content="r1 ") + _delta(reasoning="r2") + _delta(content="x") + _finish()
    think = []
    out = W.sse_to_chat(_lines(text), on_reasoning=think.append)
    assert "".join(think) == "r1 r2"
    assert out["choices"][0]["message"]["reasoning_content"] == "r1 r2"


def test_think_splitter_flush_and_split_tags():
    s = ST.ThinkSplitter()
    parts = [s.feed(c) for c in ["a<", "think", ">b</", "think>c<", "x"]]
    parts.append(s.flush())
    assert "".join(p[0] for p in parts) == "ac<x" and "".join(p[1] for p in parts) == "b"


# ---- already fixed on the base (proving tests; they pass without this change) ---------------------------------------
def test_json_reply_to_a_stream_request_is_used_in_one_call(serve):      # W-05 (already fixed)
    seen = []
    led = serve(_json_handler(seen))
    out = led.chat(model="m", messages=MSG, retries=0)
    assert len(seen) == 1 and out["choices"][0]["message"]["content"] == "ok"
    assert json.loads(seen[0])["stream"] is True


def test_object_arguments_are_serialized():                               # W-08 (already fixed)
    text = _delta(tool_calls=[{"index": 0, "id": "a", "function": {"name": "f", "arguments": {"k": "v"}}}]) + _finish("tool_calls")
    call = W.sse_to_chat(_lines(text))["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"k": "v"}


def test_cut_stream_is_an_error():                                        # W-19 (already fixed)
    with pytest.raises(RuntimeError, match="HTTP 0: the stream ended before the reply was complete"):
        W.sse_to_chat(_lines(_delta(content="par")))


def test_non_object_data_lines_are_skipped():                             # W-20 (already fixed)
    text = "data: 42\n\ndata: [1, 2]\n\ndata: \"s\"\n\n" + _delta(content="ok") + _finish()
    assert W.sse_to_chat(_lines(text))["choices"][0]["message"]["content"] == "ok"
