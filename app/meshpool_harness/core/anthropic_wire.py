"""Anthropic Messages wire for an Anthropic-style API key endpoint: chat-completions requests in, chat-completions
responses out, so the rest of Meshpool (agent loop, tools) never knows which wire the endpoint speaks. Replies can be
read whole (`from_response`) or as an SSE stream (`StreamAssembler`)."""
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
        return [{"type": "text", "text": content}] if content and content.strip() else []
    out = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text" and isinstance(p.get("text"), str) and p["text"].strip():
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


def to_request(body: dict, cache: bool | None = None) -> dict:
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
    cache = _cache_on() if cache is None else cache
    system = [s for s in system if s and s.strip()]
    if system:
        blk = {"type": "text", "text": "\n\n".join(system)}
        if cache:
            blk["cache_control"] = dict(_EPHEMERAL)
        out["system"] = [blk]
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
            tools[-1]["cache_control"] = dict(_EPHEMERAL)
        out["tools"] = tools
        out["tool_choice"] = _tool_choice(body.get("tool_choice"), body.get("parallel_tool_calls"))
    if cache and msgs:
        last = len(msgs) - 1
        _mark(msgs[last])
        for j in range(last - 1, -1, -1):             # the previous user turn
            if msgs[j]["role"] == "user":
                _mark(msgs[j])
                break
    return out


_EPHEMERAL = {"type": "ephemeral"}


def _cache_on() -> bool:
    return os.environ.get("MH_ANTHROPIC_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def _mark(msg: dict) -> None:
    """Put a cache breakpoint on the last block of `msg` that can carry one (never thinking, never empty text)."""
    for b in reversed(msg["content"]):
        t = b.get("type")
        if t in ("thinking", "redacted_thinking"):
            continue
        if t == "text" and not (b.get("text") or "").strip():
            continue
        b["cache_control"] = dict(_EPHEMERAL)
        return


def _tool_choice(tc, parallel) -> dict:
    if tc == "none":
        return {"type": "none"}
    if tc == "required":
        out = {"type": "any"}
    elif isinstance(tc, dict) and (tc.get("function") or {}).get("name"):
        out = {"type": "tool", "name": tc["function"]["name"]}
    else:
        out = {"type": "auto"}
    if parallel is False:
        out["disable_parallel_tool_use"] = True
    return out


_STOP = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


def from_response(p: dict) -> dict:
    text, calls, think = [], [], []
    for b in p.get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text.append(b.get("text", ""))
        elif b.get("type") == "thinking":
            think.append(b.get("thinking", ""))
        elif b.get("type") == "tool_use":
            inp = b.get("input")
            args = inp if isinstance(inp, str) else json.dumps(inp or {})   # a str is undecodable streamed JSON, kept raw
            calls.append({"id": b.get("id", ""), "type": "function",
                          "function": {"name": b.get("name", ""), "arguments": args}})
    msg = {"role": "assistant", "content": "".join(text) or None}
    if "".join(think):
        msg["reasoning_content"] = "".join(think)
    if calls:
        msg["tool_calls"] = calls
    u = p.get("usage") or {}
    cached = u.get("cache_read_input_tokens") or 0
    pin = (u.get("input_tokens") or 0) + cached + (u.get("cache_creation_input_tokens") or 0)
    return {"id": p.get("id", ""), "model": p.get("model", ""),
            "choices": [{"index": 0, "message": msg, "finish_reason": _STOP.get(p.get("stop_reason"), "stop")}],
            "usage": {"prompt_tokens": pin, "completion_tokens": u.get("output_tokens") or 0,
                      "total_tokens": pin + (u.get("output_tokens") or 0),
                      # cache writes kept apart: they bill at the write rate, not the input rate (core/usage.py)
                      "prompt_tokens_details": {"cached_tokens": cached,
                                                "cache_write_tokens": u.get("cache_creation_input_tokens") or 0}}}


_ERRORS = {"rate_limit_error": 429, "overloaded_error": 529, "api_error": 500, "invalid_request_error": 400,
           "authentication_error": 401, "permission_error": 403}


class StreamAssembler:
    """Builds a chat-completions reply from the Messages streaming events. Feed it one SSE event at a time."""

    def __init__(self, on_text=None, on_reasoning=None):
        self.on_text, self.on_reasoning = on_text, on_reasoning
        self.id = self.model = ""
        self.usage: dict = {}
        self.stop_reason = None
        self.done = False
        self._blocks: dict = {}          # index -> {"type", "text" | "thinking" | "json", ...}

    def feed(self, event: str, data: str) -> None:
        try:
            d = json.loads(data)
        except (ValueError, TypeError):
            return
        if not isinstance(d, dict):
            return
        kind = event or d.get("type") or ""
        if kind == "message_start":
            m = d.get("message") if isinstance(d.get("message"), dict) else {}
            self.id, self.model = m.get("id") or self.id, m.get("model") or self.model
            self._merge_usage(m.get("usage"))
        elif kind == "content_block_start":
            cb = d.get("content_block") if isinstance(d.get("content_block"), dict) else {}
            t = cb.get("type")
            blk = {"type": t, "text": cb.get("text") or "", "thinking": cb.get("thinking") or "",
                   "id": cb.get("id", ""), "name": cb.get("name", ""), "input": cb.get("input"), "json": ""}
            self._blocks[d.get("index", len(self._blocks))] = blk
            if t == "text" and blk["text"] and self.on_text:
                self.on_text(blk["text"])
            elif t == "thinking" and blk["thinking"] and self.on_reasoning:
                self.on_reasoning(blk["thinking"])
        elif kind == "content_block_delta":
            blk = self._blocks.get(d.get("index"))
            dl = d.get("delta") if isinstance(d.get("delta"), dict) else {}
            if blk is None:
                return
            t = dl.get("type")
            if t == "text_delta":
                blk["text"] += dl.get("text") or ""
                if dl.get("text") and self.on_text:
                    self.on_text(dl["text"])
            elif t == "input_json_delta":
                blk["json"] += dl.get("partial_json") or ""
            elif t == "thinking_delta":
                blk["thinking"] += dl.get("thinking") or ""
                if dl.get("thinking") and self.on_reasoning:
                    self.on_reasoning(dl["thinking"])
        elif kind == "message_delta":
            dl = d.get("delta") if isinstance(d.get("delta"), dict) else {}
            self.stop_reason = dl.get("stop_reason") or self.stop_reason
            self._merge_usage(d.get("usage"))
        elif kind == "message_stop":
            self.done = True
        elif kind == "error":
            e = d.get("error") if isinstance(d.get("error"), dict) else {}
            code = _ERRORS.get(e.get("type"), 500)
            raise RuntimeError(f"HTTP {code}: {e.get('message') or e.get('type') or 'stream error'}")

    def _merge_usage(self, u) -> None:
        if isinstance(u, dict):
            self.usage.update({k: v for k, v in u.items() if v is not None})

    def result(self) -> dict:
        if not (self.done or self.stop_reason):
            from .stream import StreamCut                      # lazy: stream.py may import this module
            raise StreamCut("no message_stop" if self._blocks or self.id else "no events")
        content = []
        for i in sorted(self._blocks, key=lambda x: (not isinstance(x, int), x if isinstance(x, int) else 0)):
            b = self._blocks[i]
            if b["type"] == "text":
                content.append({"type": "text", "text": b["text"]})
            elif b["type"] == "thinking":
                content.append({"type": "thinking", "thinking": b["thinking"]})
            elif b["type"] == "tool_use":
                raw = b["json"].strip()
                if raw:
                    try:
                        inp = json.loads(raw)
                    except ValueError:
                        inp = b["json"]                      # keep the raw text: the agent reports bad arguments
                else:
                    inp = b["input"] if isinstance(b["input"], dict) else {}
                content.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": inp})
        return from_response({"id": self.id, "model": self.model, "content": content,
                              "stop_reason": self.stop_reason, "usage": self.usage})
