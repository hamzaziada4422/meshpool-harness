"""Anthropic Messages wire for an Anthropic-style API key endpoint: chat-completions requests in, chat-completions
responses out, so the rest of Meshpool (agent loop, tools) never knows which wire the endpoint speaks. Not streamed."""
from __future__ import annotations

import json
import os

VERSION = "2023-06-01"


def headers(api_key: str) -> dict:
    h = {"anthropic-version": VERSION, "Content-Type": "application/json"}
    if api_key:
        h["x-api-key"] = api_key
    return h


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def _blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    out = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" and p.get("text"):
            out.append({"type": "text", "text": p["text"]})
        elif p.get("type") == "image_url":
            url = (p.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                head, data = url.split(";base64,", 1)
                out.append({"type": "image", "source": {"type": "base64", "media_type": head[5:] or "image/png",
                                                        "data": data}})
            elif url:
                out.append({"type": "image", "source": {"type": "url", "url": url}})
    return out


def to_request(body: dict) -> dict:
    system, msgs = [], []

    def push(role: str, blocks: list) -> None:
        if not blocks:
            return
        if msgs and msgs[-1]["role"] == role:        # the API wants alternating turns: merge neighbours
            msgs[-1]["content"].extend(blocks)
        else:
            msgs.append({"role": role, "content": list(blocks)})

    pending, nsyn = [], 0                            # ids made up for tool calls that came without one
    for m in body.get("messages") or []:
        role = m.get("role")
        if role == "system":
            system.append(_text_of(m.get("content")))
        elif role == "tool":
            c = m.get("content")
            tid = m.get("tool_call_id") or (pending.pop(0) if pending else "toolu_unknown")
            if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
                res = _blocks(c) or "(no output)"        # screenshots and charts the tool returned reach the model
            else:
                res = _text_of(c) or "(no output)"
            push("user", [{"type": "tool_result", "tool_use_id": tid, "content": res}])
        elif role == "assistant":
            blocks = _blocks(m.get("content"))
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    args = {}
                cid = c.get("id")
                if not cid:
                    nsyn += 1
                    cid = f"toolu_synth_{nsyn}"
                    pending.append(cid)
                blocks.append({"type": "tool_use", "id": cid, "name": f.get("name", ""),
                               "input": args if isinstance(args, dict) else {}})
            push("assistant", blocks)
        else:
            push("user", _blocks(m.get("content")) or [{"type": "text", "text": "(empty)"}])
    out = {"model": body["model"], "max_tokens": int(body.get("max_tokens") or 8192), "messages": msgs}
    cache = os.environ.get("MH_ANTHROPIC_CACHE", "1") != "0"
    if system:
        text = "\n\n".join(s for s in system if s)
        # Explicit cache breakpoints (the Messages API caches only what is marked): the instructions, the tools and
        # the end of the conversation so far. Everything before a marker is read from the cache on the next call.
        out["system"] = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}] if cache else text
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    tools = []
    for t in body.get("tools") or []:
        f = t.get("function") or {}
        if f.get("name"):
            tools.append({"name": f["name"], "description": f.get("description", ""),
                          "input_schema": f.get("parameters") or {"type": "object", "properties": {}}})
    if tools:
        if cache:
            tools[-1]["cache_control"] = {"type": "ephemeral"}
        out["tools"] = tools
        tc = body.get("tool_choice")
        out["tool_choice"] = ({"type": "any"} if tc == "required" else
                              {"type": "tool", "name": tc["function"]["name"]} if isinstance(tc, dict) else
                              {"type": "auto"})
    if cache and msgs and msgs[-1]["content"] and isinstance(msgs[-1]["content"][-1], dict) \
            and msgs[-1]["content"][-1].get("type") in ("text", "tool_result", "tool_use"):
        msgs[-1]["content"][-1] = dict(msgs[-1]["content"][-1], cache_control={"type": "ephemeral"})
    return out


_STOP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


def from_response(p: dict) -> dict:
    text, calls = [], []
    for b in p.get("content") or []:
        if b.get("type") == "text":
            text.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            calls.append({"id": b.get("id", ""), "type": "function",
                          "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input") or {})}})
    msg = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        msg["tool_calls"] = calls
    u = p.get("usage") or {}
    cached = u.get("cache_read_input_tokens") or 0
    pin = (u.get("input_tokens") or 0) + cached + (u.get("cache_creation_input_tokens") or 0)
    return {"id": p.get("id", ""), "model": p.get("model", ""),
            "choices": [{"index": 0, "message": msg, "finish_reason": _STOP.get(p.get("stop_reason"), "stop")}],
            "usage": {"prompt_tokens": pin, "completion_tokens": u.get("output_tokens") or 0,
                      "total_tokens": pin + (u.get("output_tokens") or 0),
                      "prompt_tokens_details": {"cached_tokens": cached}}}
