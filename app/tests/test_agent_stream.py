"""The app agent's transport on real local HTTP servers: SSE framing and bad chunks, JSON replies to a stream request,
cut streams, the keep-alive probe, the Anthropic Messages stream, stated rate-limit waits, reasoning_content, text
tool calls, <think> text, thinking that eats the output budget, and the CLI exit code (docs/audit/plans/ws02-stream.md)."""
import email.utils
import http.server
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "2")
    monkeypatch.setenv("MH_AGENT_IDLE_S", "2")
    monkeypatch.setenv("MH_MODEL", "plain-model")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    core._REFUSED.clear()


class Srv:
    """A scripted endpoint: each request is answered by the next function in `script` (the last one repeats)."""

    def __init__(self, *script):
        self.script, self.reqs = list(script), []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                outer.reqs.append({"path": self.path, "hdr": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                n = len(outer.reqs) - 1
                outer.script[min(n, len(outer.script) - 1)](self)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()


def sse(*events, done=True):
    """A handler streaming data events (str = raw line block, else JSON-encoded) and, by default, [DONE]."""
    def h(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for e in events:
            self.wfile.write(e if isinstance(e, bytes) else b"data: " + json.dumps(e).encode() + b"\n\n")
        if done:
            self.wfile.write(b"data: [DONE]\n\n")
    return h


def js(obj, code=200, headers=None):
    def h(self):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    return h


def chunk(content=None, finish=None, **delta):
    d = dict(delta)
    if content is not None:
        d["content"] = content
    return {"id": "c", "choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def reply(content="", **msg):
    return {"choices": [{"message": {"role": "assistant", "content": content, **msg}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


def agent(tmp_path, srv, monkeypatch, model=None):
    monkeypatch.setenv("MESHPOOL_BASE_URL", srv.base)
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")      # no window probe request before the first call
    if model:
        monkeypatch.setenv("MH_MODEL", model)
    a = Agent(str(tmp_path), mode="auto")
    a.events = []
    a.emit = a.events.append
    return a


def deltas(a):
    return "".join(e["text"] for e in a.events if e.get("type") == "text_delta")


def post(a, srv, stream=True, model="plain-model"):
    return a._post(srv.base, "k", {"model": model, "messages": [{"role": "user", "content": "hi"}]}, stream)


def test_agent_non_object_chunk_does_not_crash(tmp_path, monkeypatch):
    s = Srv(sse(b"data: \"x\"\n\n", b"data: 42\n\n", b"data: [1, 2]\n\n", chunk("hel"), chunk("lo"), chunk(finish="stop")))
    a = agent(tmp_path, s, monkeypatch)
    resp, streamed = post(a, s)
    s.close()
    assert resp["choices"][0]["message"]["content"] == "hello" and streamed and deltas(a) == "hello"


def test_agent_multiline_data_field(tmp_path, monkeypatch):
    whole = json.dumps(chunk("joined"))
    half = whole.index(',')
    block = ("data: " + whole[:half] + "\ndata: " + whole[half:] + "\n\n").encode()
    s = Srv(sse(block, chunk(finish="stop")))
    a = agent(tmp_path, s, monkeypatch)
    resp, _ = post(a, s)
    s.close()
    assert resp["choices"][0]["message"]["content"] == "joined" and deltas(a) == "joined"


def test_server_ignoring_stream_costs_one_call(tmp_path, monkeypatch):
    # already fixed on the base (sse_to_chat reads a plain JSON body); kept as the proof that it stays one request
    s = Srv(js(reply("plain json")))
    a = agent(tmp_path, s, monkeypatch)
    resp, _ = post(a, s)
    s.close()
    assert resp["choices"][0]["message"]["content"] == "plain json" and len(s.reqs) == 1


def test_cut_stream_with_text_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(Agent, "_probe", lambda self, base, key, model: "ok")
    s = Srv(sse(chunk("half an ans"), done=False), sse(chunk("the full answer"), chunk(finish="stop")))
    a = agent(tmp_path, s, monkeypatch)
    msg, _ = a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    s.close()
    assert msg["content"] == "the full answer" and len(s.reqs) == 2
    assert any(e.get("type") == "stream_reset" for e in a.events)


def test_probe_on_keepalive_only_body_ends(monkeypatch):
    def trickle(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for _ in range(60):
                self.wfile.write(b": ping\n")
                self.wfile.flush()
                time.sleep(0.2)
        except OSError:
            pass
    s = Srv(trickle)
    monkeypatch.setenv("MH_PROBE_S", "1")
    t0 = time.time()
    out = core._probe_once(s.base, "k", "m")
    s.close()
    assert time.time() - t0 < 4 and out.startswith("HTTP 0")


def test_anthropic_wire_is_streamed(tmp_path, monkeypatch):
    def ev(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()

    def messages(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for b in (ev("message_start", {"type": "message_start", "message": {"id": "m1", "model": "claude-x",
                                                                           "usage": {"input_tokens": 5}}}),
                  ev("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "text", "text": ""}}),
                  ev("ping", {"type": "ping"}),
                  ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": "hel"}}),
                  ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": "lo"}}),
                  ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
                  ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                       "usage": {"output_tokens": 2}}),
                  ev("message_stop", {"type": "message_stop"})):
            self.wfile.write(b)
    s = Srv(messages)
    monkeypatch.setenv("MH_API_WIRE", "anthropic")
    a = agent(tmp_path, s, monkeypatch)
    resp, streamed = a._post(s.base, "sk-good-key-1234", {"model": "claude-x", "messages": [
        {"role": "user", "content": "hi"}]}, True)
    s.close()
    assert s.reqs[0]["body"]["stream"] is True and s.reqs[0]["path"].endswith("/messages")
    assert resp["choices"][0]["message"]["content"] == "hello" and streamed and deltas(a) == "hello"


def test_reset_past_budget_ends_turn_at_once(tmp_path, monkeypatch):
    when = email.utils.formatdate(time.time() + 7200, usegmt=True)
    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "60")
    s = Srv(js({"error": {"message": "slow down"}}, 429, {"Retry-After": when}))
    a = agent(tmp_path, s, monkeypatch)
    slept = []
    monkeypatch.setattr(Agent, "_sleep", lambda self, x: slept.append(x))
    t0 = time.time()
    with pytest.raises(RuntimeError, match="429.*resets in"):
        a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    s.close()
    assert time.time() - t0 < 5 and not [x for x in slept if x >= 60] and len(s.reqs) == 1


def _tool_turn(tmp_path, monkeypatch, model):
    call = {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})}}
    first = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "I should read it.",
                                      "tool_calls": [call]}, "finish_reason": "tool_calls"}], "usage": {}}
    s = Srv(js(first), js(reply("it says hi")))
    (tmp_path / "a.txt").write_text("hi")
    monkeypatch.setenv("MH_STREAM", "0")
    a = agent(tmp_path, s, monkeypatch, model)
    assert a.send("read a.txt") == "it says hi"
    s.close()
    return s


def test_reasoning_content_kept_and_sent_back_in_the_turn(tmp_path, monkeypatch):
    s = _tool_turn(tmp_path, monkeypatch, "deepseek-reasoner")
    asst = [m for m in s.reqs[1]["body"]["messages"] if m["role"] == "assistant"]
    assert asst and asst[-1].get("reasoning_content") == "I should read it."
    other = tmp_path / "o"
    other.mkdir()
    s2 = _tool_turn(other, monkeypatch, "plain-model")          # an unknown model: the field is not sent
    assert all("reasoning_content" not in m for m in s2.reqs[1]["body"]["messages"])


@pytest.mark.parametrize("text", ['<function=read_file>{"path": "a.txt"}</function>',
                                  '[TOOL_CALLS][{"name": "read_file", "arguments": {"path": "a.txt"}}]'])
def test_text_tool_call_runs_the_tool(tmp_path, monkeypatch, text):
    (tmp_path / "a.txt").write_text("file body")
    s = Srv(sse(chunk(text), chunk(finish="stop")), sse(chunk("it says: file body"), chunk(finish="stop")))
    a = agent(tmp_path, s, monkeypatch)
    out = a.send("read a.txt")
    s.close()
    assert out == "it says: file body" and len(s.reqs) == 2
    assert any(m["role"] == "tool" and "file body" in m["content"] for m in a.messages)
    assert any(e.get("type") == "stream_reset" for e in a.events)


def test_think_text_not_streamed_and_thinking_status(tmp_path, monkeypatch):
    s = Srv(sse(chunk("<think>let me ", ), chunk("reason</think>The answer"), chunk(" is 4"), chunk(finish="stop")))
    a = agent(tmp_path, s, monkeypatch)
    resp, _ = post(a, s)
    s.close()
    assert deltas(a) == "The answer is 4" and "think" not in deltas(a)
    assert [e for e in a.events if e.get("text") == "Thinking…"].__len__() == 1
    assert resp["choices"][0]["message"]["content"] == "The answer is 4"


def test_length_cut_during_thinking_continues(tmp_path, monkeypatch):
    cut = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "hmm hmm"},
                        "finish_reason": "length"}], "usage": {}}
    s = Srv(js(cut), js(reply("4")))
    monkeypatch.setenv("MH_STREAM", "0")
    a = agent(tmp_path, s, monkeypatch)
    out = a.send("2+2?")
    s.close()
    assert out == "4" and len(s.reqs) == 2
    assert "answer now" in json.dumps(s.reqs[1]["body"]["messages"][-1]).lower() or \
        "answer" in s.reqs[1]["body"]["messages"][-1]["content"].lower()
    assert not any(e.get("type") == "error" for e in a.events)


def test_cli_exits_non_zero_on_cut_streams(tmp_path, monkeypatch, capsys):
    from meshpool_harness.agent import tui
    monkeypatch.setattr(Agent, "_probe", lambda self, base, key, model: "ok")
    s = Srv(sse(chunk("a trunca"), done=False))                # every reply is cut: no finish, no [DONE]
    monkeypatch.setenv("MESHPOOL_BASE_URL", s.base)
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    rc = tui.main(workdir=str(tmp_path), prompt="hello", auto=True, no_mcp=True, onboarded=True)
    s.close()
    assert rc == 1 and len(s.reqs) >= 2
