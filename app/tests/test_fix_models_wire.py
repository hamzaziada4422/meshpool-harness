"""Model-specific reply quirks seen on the Meshpool proxy and other OpenAI-compatible servers, reproduced with a fake
server: tool-call arguments as a dict / None / repeated JSON, missing or duplicate ids, parallel calls that share one
index, tool calls written as text (Hermes, GLM, Kimi), <think> tags in content, usage inside the choice, and the
transient gateway errors (503 'authentication database unreachable', a spurious 401). All offline."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.core import wire as W  # noqa: E402


def sse(*events):
    out = []
    for e in events:
        out.append("data: " + (e if isinstance(e, str) else json.dumps(e)) + "\n")
    return out


def delta(**d):
    return {"id": "x", "choices": [{"index": 0, "delta": d}]}


def fin(reason="stop"):
    return {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def msg(r):
    return r["choices"][0]["message"]


# ---- streaming assembly ---------------------------------------------------------------------------------------------
def test_parallel_calls_sharing_index_zero_are_not_concatenated():
    """gpt-oss and some proxies number every parallel call 0: names and arguments used to be glued together."""
    r = W.sse_to_chat(sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}]),
        delta(tool_calls=[{"index": 0, "id": "c2", "function": {"name": "read_file", "arguments": '{"path":"b.txt"}'}}]),
        fin("tool_calls"), "[DONE]"))
    calls = msg(r)["tool_calls"]
    assert [c["id"] for c in calls] == ["c1", "c2"]
    assert [json.loads(c["function"]["arguments"]) for c in calls] == [{"path": "a.txt"}, {"path": "b.txt"}]


def test_whole_calls_without_ids_at_one_index():
    r = W.sse_to_chat(sse(
        delta(tool_calls=[{"index": 0, "function": {"name": "a", "arguments": '{"x":1}'}}]),
        delta(tool_calls=[{"index": 0, "function": {"name": "b", "arguments": '{"y":2}'}}]),
        fin("stop"), "[DONE]"))
    calls = msg(r)["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["a", "b"]
    assert len({c["id"] for c in calls}) == 2 and all(c["id"] for c in calls)      # ids made up and unique
    assert r["choices"][0]["finish_reason"] == "tool_calls"                         # 'stop' with calls is a tool turn


def test_index_starting_at_one_and_fragmented_name():
    r = W.sse_to_chat(sse(
        delta(tool_calls=[{"index": 1, "id": "c1", "function": {"name": "read_", "arguments": ""}}]),
        delta(tool_calls=[{"index": 1, "function": {"name": "file", "arguments": '{"path":'}}]),
        delta(tool_calls=[{"index": 1, "function": {"arguments": '"a"}'}}]), fin("tool_calls")))
    c = msg(r)["tool_calls"][0]
    assert c["function"]["name"] == "read_file" and json.loads(c["function"]["arguments"]) == {"path": "a"}


def test_usage_inside_choice_moonshot_style():
    ev = {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop",
                                  "usage": {"prompt_tokens": 12, "completion_tokens": 3}}]}
    r = W.sse_to_chat(sse(delta(content="hi"), ev, "[DONE]"))
    assert r["usage"]["prompt_tokens"] == 12


def test_arguments_as_dict_in_a_delta():
    r = W.sse_to_chat(sse(delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "f", "arguments": {"a": 1}}}]),
                          fin("tool_calls")))
    assert json.loads(msg(r)["tool_calls"][0]["function"]["arguments"]) == {"a": 1}


# ---- normalize_reply ------------------------------------------------------------------------------------------------
def reply(**m):
    return {"choices": [{"index": 0, "message": {"role": "assistant", **m}, "finish_reason": "stop"}], "usage": {}}


@pytest.mark.parametrize("raw,want", [
    ({"path": "a"}, {"path": "a"}),                      # dict
    (None, {}),                                          # None
    ("", {}),                                            # blank
    ('{"path":"a"}{"path":"a"}', {"path": "a"}),         # the same call twice (a restarted stream)
    ('{"a":1}{"b":2}', {"a": 1, "b": 2}),                # two objects: merged
    (json.dumps(json.dumps({"p": 1})), {"p": 1}),        # double encoded
])
def test_arguments_forms(raw, want):
    r = W.normalize_reply(reply(content=None, tool_calls=[{"id": "c", "function": {"name": "f", "arguments": raw}}]))
    assert json.loads(msg(r)["tool_calls"][0]["function"]["arguments"]) == want


def test_truncated_arguments_are_left_alone():
    r = W.normalize_reply(reply(tool_calls=[{"id": "c", "function": {"name": "f", "arguments": '{"a": "cut'}}]))
    assert msg(r)["tool_calls"][0]["function"]["arguments"] == '{"a": "cut'


def test_missing_and_duplicate_ids():
    r = W.normalize_reply(reply(tool_calls=[{"function": {"name": "f", "arguments": "{}"}},
                                            {"id": "same", "function": {"name": "g", "arguments": "{}"}},
                                            {"id": "same", "function": {"name": "h", "arguments": "{}"}}]))
    ids = [c["id"] for c in msg(r)["tool_calls"]]
    assert len(set(ids)) == 3 and all(ids)


def test_think_tags_leave_the_content():
    r = W.normalize_reply(reply(content="<think>plan it\nmore</think>\n\nThe answer is 4."))
    assert msg(r)["content"] == "The answer is 4." and "plan it" in msg(r)["reasoning_content"]
    r = W.normalize_reply(reply(content="reasoning with no opening tag</think>Done."))
    assert msg(r)["content"] == "Done."
    r = W.normalize_reply(reply(content="<think>cut off while thinking"))
    assert msg(r)["content"] is None and "cut off" in msg(r)["reasoning_content"]
    r = W.normalize_reply(reply(content="plain <b>html</b> stays"))
    assert msg(r)["content"] == "plain <b>html</b> stays"


def test_hermes_tool_call_in_content():
    r = W.normalize_reply(reply(content='ok <tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>'))
    c = msg(r)["tool_calls"][0]
    assert c["function"]["name"] == "read_file" and json.loads(c["function"]["arguments"]) == {"path": "a.py"}
    assert "tool_call" not in (msg(r)["content"] or "") and r["choices"][0]["finish_reason"] == "tool_calls"


def test_glm_xml_tool_call_in_content():
    r = W.normalize_reply(reply(content="<tool_call>read_file\n<arg_key>path</arg_key>\n<arg_value>a.py</arg_value>\n"
                                        "<arg_key>limit</arg_key><arg_value>20</arg_value></tool_call>"))
    c = msg(r)["tool_calls"][0]
    assert c["function"]["name"] == "read_file" and json.loads(c["function"]["arguments"]) == {"path": "a.py", "limit": 20}


def test_kimi_token_tool_call_in_content():
    txt = ("<|tool_calls_section_begin|><|tool_call_begin|>functions.read_file:0<|tool_call_argument_begin|>"
           '{"path": "a.py"}<|tool_call_end|><|tool_calls_section_end|>')
    r = W.normalize_reply(reply(content=txt))
    c = msg(r)["tool_calls"][0]
    assert c["function"]["name"] == "read_file" and json.loads(c["function"]["arguments"]) == {"path": "a.py"}
    assert msg(r)["content"] is None


def test_content_as_list_of_parts():
    r = W.normalize_reply(reply(content=[{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}]))
    assert msg(r)["content"] == "hello"


def test_responses_reply_goes_through_the_same_cleanup():
    p = {"output": [{"type": "function_call", "name": "f", "arguments": {"a": 1}}], "usage": {}}
    c = W.from_responses(p)
    t = msg(c)["tool_calls"][0]
    assert t["id"] and json.loads(t["function"]["arguments"]) == {"a": 1}


# ---- the whole transport against a fake server ----------------------------------------------------------------------
class Fake(BaseHTTPRequestHandler):
    script: list = []          # one entry per request: ("status", body-or-lines)
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Fake.seen.append(body)
        status, payload = Fake.script.pop(0) if Fake.script else (200, {"choices": [{"message": {"content": "late"}}]})
        self.send_response(status)
        if isinstance(payload, list):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write("".join(payload).encode())
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())


@pytest.fixture()
def server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Fake.script, Fake.seen = [], []
    base = f"http://127.0.0.1:{srv.server_port}/v1"
    monkeypatch.setenv("MESHPOOL_BASE_URL", base)
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_WIRE", "chat")
    monkeypatch.setenv("MH_STREAM", "1")
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    yield srv
    srv.shutdown()


def test_transport_streams_and_cleans(server):
    Fake.script = [(200, sse(delta(content="<think>hm</think>"), delta(content="Hi"), fin(), "[DONE]"))]
    r = W.SimpleLedger().chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)
    assert msg(r)["content"] == "Hi" and msg(r)["reasoning_content"] == "hm"


def test_transport_retries_transient_gateway_errors(server):
    """The proxy answers 503 'authentication database unreachable' and sometimes a spurious 401 under load: a retry
    succeeds. A call that fails the same way three times still raises."""
    ok = (200, sse(delta(content="ok"), fin(), "[DONE]"))
    bad503 = (503, {"error": {"message": "Service Unavailable, the authentication database is temporarily unreachable."}})
    bad401 = (401, {"error": {"message": "Authentication Error, The request could not be authenticated"}})
    Fake.script = [bad503, bad401, ok]
    r = W.SimpleLedger().chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)
    assert msg(r)["content"] == "ok" and len(Fake.seen) == 3
    Fake.script, Fake.seen = [bad401, bad401, bad401, bad401], []
    with pytest.raises(RuntimeError, match="HTTP 401"):
        W.SimpleLedger().chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)
    assert len(Fake.seen) <= 4


def test_server_ignores_stream_true_and_sends_plain_json():
    body = json.dumps({"id": "x", "choices": [{"message": {"role": "assistant", "content": "<think>a</think>b"},
                                                "finish_reason": "stop"}], "usage": {"prompt_tokens": 1}})
    r = W.sse_to_chat([body + "\n"])
    assert msg(r)["content"] == "b" and r["usage"]["prompt_tokens"] == 1


def test_error_body_with_http_200_raises():
    with pytest.raises(RuntimeError, match="overloaded"):
        W.sse_to_chat(['{"error": {"message": "overloaded"}}\n'])


def test_reasoning_model_that_thinks_through_the_limit_gets_one_larger_retry(server):
    """grok-4.7 with max_tokens=200: reasoning_content only, finish_reason length, no answer."""
    cut = sse({"id": "x", "choices": [{"index": 0, "delta": {"reasoning_content": "thinking..."}}]},
              fin("length"), "[DONE]")
    Fake.script = [(200, cut), (200, sse(delta(content="hi"), fin(), "[DONE]"))]
    r = W.SimpleLedger().chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=200, retries=0)
    assert msg(r)["content"] == "hi"
    assert [b["max_tokens"] for b in Fake.seen] == [200, 600]
