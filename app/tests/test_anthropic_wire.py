"""Anthropic Messages wire: tool_choice, prompt caching, streamed assembly, thinking."""
import copy
import json

import pytest

from meshpool_harness.core import anthropic_wire as AW

TOOLS = [{"type": "function", "function": {"name": n, "description": "d",
                                           "parameters": {"type": "object", "properties": {}}}} for n in "abc"]


def _req(**extra):
    return AW.to_request({"model": "m", "tools": TOOLS, "messages": [{"role": "user", "content": "hi"}], **extra})


def test_tool_choice_none_is_kept():
    assert _req(tool_choice="none")["tool_choice"] == {"type": "none"}
    assert _req(tool_choice="auto")["tool_choice"] == {"type": "auto"}
    assert _req()["tool_choice"] == {"type": "auto"}
    assert _req(tool_choice="required")["tool_choice"] == {"type": "any"}
    named = {"type": "function", "function": {"name": "b"}}
    assert _req(tool_choice=named)["tool_choice"] == {"type": "tool", "name": "b"}
    assert _req(tool_choice="auto", parallel_tool_calls=False)["tool_choice"] == {
        "type": "auto", "disable_parallel_tool_use": True}
    assert _req(tool_choice="required", parallel_tool_calls=False)["tool_choice"]["disable_parallel_tool_use"] is True
    assert _req(tool_choice=named, parallel_tool_calls=False)["tool_choice"]["disable_parallel_tool_use"] is True
    assert _req(tool_choice="none", parallel_tool_calls=False)["tool_choice"] == {"type": "none"}
    assert "disable_parallel_tool_use" not in _req(parallel_tool_calls=True)["tool_choice"]


def _conversation():
    call = lambda i: {"id": i, "type": "function", "function": {"name": "a", "arguments": "{\"x\": 1}"}}
    return {"model": "m", "tools": TOOLS, "messages": [
        {"role": "system", "content": "rules one"}, {"role": "system", "content": "rules two"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "  ", "tool_calls": [call("t1")]},
        {"role": "tool", "tool_call_id": "t1", "content": "out1"},
        {"role": "assistant", "content": "done one"},
        {"role": "user", "content": [{"type": "text", "text": "  "}, {"type": "text", "text": "second question"}]},
        {"role": "assistant", "content": None, "tool_calls": [call("t2")]},
        {"role": "tool", "tool_call_id": "t2", "content": "out2"}]}


def _marked(r):
    found = []
    for t in r.get("tools", []):
        if "cache_control" in t:
            found.append(("tool", t["name"]))
    sysv = r.get("system")
    if isinstance(sysv, list):
        found += [("system", b["text"]) for b in sysv if "cache_control" in b]
    for i, m in enumerate(r["messages"]):
        for j, b in enumerate(m["content"]):
            if "cache_control" in b:
                found.append(("msg", i, j))
    return found


def test_prompt_caching_breakpoints(monkeypatch):
    monkeypatch.delenv("MH_ANTHROPIC_CACHE", raising=False)
    body = _conversation()
    before = copy.deepcopy(body)
    r = AW.to_request(body)
    assert body == before                                                    # caller's body untouched
    assert isinstance(r["system"], list) and r["system"][0]["text"] == "rules one\n\nrules two"
    marked = _marked(r)
    assert len(marked) <= 4
    # messages: user(first), assistant(tool_use), user(tool_result), assistant(text), user(second), assistant(tool_use),
    # user(tool_result) -> merged turns; the last message and the previous user turn carry breakpoints
    roles = [m["role"] for m in r["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert set(marked) == {("tool", "c"), ("system", "rules one\n\nrules two"),
                           ("msg", 6, 0), ("msg", 4, len(r["messages"][4]["content"]) - 1)}
    assert r["messages"][6]["content"][0]["type"] == "tool_result"
    for m in r["messages"]:                                                  # no empty text anywhere
        for b in m["content"]:
            assert b["type"] != "text" or b["text"].strip()
    assert all(b["text"].strip() for b in r["system"])
    monkeypatch.setenv("MH_ANTHROPIC_CACHE", "0")
    off = AW.to_request(body)
    assert _marked(off) == [] and off["system"][0]["text"] == "rules one\n\nrules two"


def test_caching_single_turn_and_no_tools(monkeypatch):
    monkeypatch.delenv("MH_ANTHROPIC_CACHE", raising=False)
    r = AW.to_request({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert _marked(r) == [("msg", 0, 0)]


def _events(*evs):
    return [(e, json.dumps(d)) for e, d in evs]


STREAM = _events(
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x", "content": [],
        "stop_reason": None, "usage": {"input_tokens": 25, "cache_read_input_tokens": 100,
                                       "cache_creation_input_tokens": 7, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("ping", {"type": "ping"}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me "}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "look."}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {
        "type": "tool_use", "id": "toolu_1", "name": "ls", "input": {}}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"path\": "}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"/tmp\""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "}"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                       "usage": {"output_tokens": 15}}),
    ("message_stop", {"type": "message_stop"}))


def test_streamed_messages_reply_is_assembled():
    got = []
    a = AW.StreamAssembler(on_text=got.append)
    for ev, data in STREAM:
        a.feed(ev, data)
    r = a.result()
    ch = r["choices"][0]
    assert ch["message"]["content"] == "Let me look." and ch["finish_reason"] == "tool_calls"
    calls = ch["message"]["tool_calls"]
    assert len(calls) == 1 and calls[0]["id"] == "toolu_1" and calls[0]["function"]["name"] == "ls"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "/tmp"}
    assert got == ["Let me ", "look."]
    assert r["id"] == "msg_1" and r["model"] == "claude-x"
    u = r["usage"]
    assert u["prompt_tokens"] == 132 and u["completion_tokens"] == 15
    assert u["prompt_tokens_details"]["cached_tokens"] == 100


def test_event_name_optional_and_garbage_skipped():
    a = AW.StreamAssembler()
    for ev, data in STREAM:
        a.feed("", data)                      # type taken from the data
        a.feed(ev, "not json")
        a.feed(ev, "[1, 2]")
    assert a.result()["choices"][0]["finish_reason"] == "tool_calls"


def test_bad_tool_json_is_kept_raw():
    a = AW.StreamAssembler()
    a.feed("content_block_start", json.dumps({"index": 0, "content_block": {"type": "tool_use", "id": "x", "name": "f", "input": {}}}))
    a.feed("content_block_delta", json.dumps({"index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"a\": "}}))
    a.feed("message_delta", json.dumps({"delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 3}}))
    r = a.result()
    assert r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == "{\"a\": "
    assert r["choices"][0]["finish_reason"] == "length"


def test_cut_anthropic_stream_raises():
    a = AW.StreamAssembler()
    for ev, data in STREAM[:5]:
        a.feed(ev, data)
    with pytest.raises(RuntimeError) as e:
        a.result()
    assert str(e.value).startswith("HTTP 0:") and "ended before the reply was complete" in str(e.value)


def test_error_event_raises_classified():
    a = AW.StreamAssembler()
    with pytest.raises(RuntimeError, match=r"HTTP 529: Overloaded"):
        a.feed("error", json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))
    for typ, code in (("rate_limit_error", 429), ("api_error", 500), ("invalid_request_error", 400),
                      ("authentication_error", 401), ("permission_error", 403), ("weird", 500)):
        with pytest.raises(RuntimeError, match=f"HTTP {code}: boom"):
            a.feed("error", json.dumps({"type": "error", "error": {"type": typ, "message": "boom"}}))


def test_thinking_goes_to_reasoning():
    seen = []
    a = AW.StreamAssembler(on_reasoning=seen.append)
    for ev, d in _events(
            ("message_start", {"message": {"id": "m", "model": "x", "usage": {"input_tokens": 1}}}),
            ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm "}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "ok"}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "sig"}}),
            ("content_block_start", {"index": 1, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "answer"}}),
            ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}),
            ("message_stop", {})):
        a.feed(ev, d)
    m = a.result()["choices"][0]["message"]
    assert m["content"] == "answer" and m["reasoning_content"] == "hmm ok" and seen == ["hmm ", "ok"]
    plain = AW.from_response({"content": [{"type": "thinking", "thinking": "t", "signature": "s"},
                                          {"type": "text", "text": "yes"}], "stop_reason": "end_turn"})
    pm = plain["choices"][0]["message"]
    assert pm["content"] == "yes" and pm["reasoning_content"] == "t"
