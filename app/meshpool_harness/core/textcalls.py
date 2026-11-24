"""Recover tool calls that a model wrote as text in `content` (local / OpenAI-compatible servers).

`recover(msg, tool_names)` turns them into `msg["tool_calls"]` only when every call names a tool offered in this
request; prose around tagged calls stays as content. Never raises."""
from __future__ import annotations

import itertools
import json
import re
import uuid

_counter = itertools.count(1)
_THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.S | re.I)
_TC_BLOCK = re.compile(r"<tool_call>(.*?)(?:</tool_call>|\Z)", re.S | re.I)
_FUNC_BLOCK = re.compile(r"<function=([^>\s]+)\s*>(.*?)(?:</function>|\Z)", re.S | re.I)
_PARAM = re.compile(r"<parameter=([^>\s]+)\s*>(.*?)(?:</parameter>|(?=<parameter=)|\Z)", re.S | re.I)
_NAME_KEYS = ("name", "tool", "tool_name", "function")
_ARG_KEYS = ("arguments", "parameters", "args", "input")


def _mask_think(text: str) -> str:
    """Same length as text, with <think> blocks blanked out, so offsets found in it map back to the original."""
    return _THINK.sub(lambda m: " " * len(m.group(0)), text)


def _decode_at(text: str, i: int):
    """(value, end) of the JSON value starting at text[i], or None."""
    try:
        return json.JSONDecoder().raw_decode(text, i)
    except ValueError:
        return None


def _as_args(v):
    if v is None:
        return {}
    if isinstance(v, str):
        if not v.strip():
            return {}
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def _call_from_obj(o, names):
    """Normalize one JSON object to (name, args dict) or None; None also when the name is not offered."""
    if not isinstance(o, dict):
        return None
    if o.get("type") == "function" and isinstance(o.get("function"), dict):
        o = o["function"]
    name = None
    for k in _NAME_KEYS:
        v = o.get(k)
        if isinstance(v, dict) and k == "function":      # {"function": {"name":..,"arguments":..}}
            o = v
            name = v.get("name")
            break
        if isinstance(v, str):
            name = v
            break
    if not isinstance(name, str) or name not in names:
        return None
    args = {}
    for k in _ARG_KEYS:
        if k in o:
            args = _as_args(o[k])
            break
    if args is None:
        return None
    return name, args


def _calls_from_value(v, names):
    """List of (name, args) for a JSON value that is a call, a list of calls or {"tool_calls": [...]}; None if any
    element is not an acceptable call."""
    if isinstance(v, dict) and isinstance(v.get("tool_calls"), list):
        v = v["tool_calls"]
    items = v if isinstance(v, list) else [v]
    if not items:
        return None
    out = []
    for it in items:
        c = _call_from_obj(it, names)
        if c is None:
            return None
        out.append(c)
    return out


def _make(calls):
    out = []
    for name, args in calls:
        out.append({"id": f"call_txt_{next(_counter)}_{uuid.uuid4().hex[:6]}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}})
    return out


def _param_value(raw: str):
    s = raw.strip()
    try:
        return json.loads(s)
    except ValueError:
        return s


def _function_block(name, body, names):
    """(name, args) for the body of `<function=name>…</function>`: a JSON object, or <parameter=k>v</parameter>."""
    if name not in names:
        return None
    b = body.strip()
    if not b:
        return name, {}
    if b.startswith("{"):
        d = _decode_at(b, 0)
        if d and isinstance(d[0], dict) and not b[d[1]:].strip():
            return name, d[0]
        return None
    params = _PARAM.findall(b)
    if params and not _PARAM.sub("", b).strip():
        return name, {k: _param_value(v) for k, v in params}
    return None


def _tagged_blocks(text: str, names):
    """Find calls in formats a-d. Returns (calls, spans) or None if nothing found / an unknown name appears."""
    masked = _mask_think(text)
    calls, spans = [], []
    # <tool_call> ... </tool_call>, whose body is JSON or a <function=...> block
    for m in _TC_BLOCK.finditer(masked):
        body = text[m.start(1):m.end(1)].strip()
        got = None
        if body.startswith("<function="):
            fm = _FUNC_BLOCK.fullmatch(body) or _FUNC_BLOCK.match(body)
            if fm:
                c = _function_block(fm.group(1), fm.group(2), names)
                got = [c] if c else None
        else:
            if body.startswith("{") or body.startswith("["):
                d = _decode_at(body, 0)
                if d and not body[d[1]:].strip():
                    got = _calls_from_value(d[0], names)
        if not got:
            return None
        calls += got
        spans.append((m.start(), m.end()))
    # bare <function=X>…</function> outside any <tool_call>
    for m in _FUNC_BLOCK.finditer(masked):
        if any(a <= m.start() < b for a, b in spans):
            continue
        closed = text[m.start():m.end()].lower().endswith("</function>")
        c = _function_block(m.group(1), text[m.start(2):m.end(2)], names) if (closed or m.group(2).strip()) else None
        if not c:
            return None
        calls.append(c)
        spans.append((m.start(), m.end()))
    # <|python_tag|>{...}; {...}
    for m in re.finditer(r"<\|python_tag\|>", masked):
        if any(a <= m.start() < b for a, b in spans):
            continue
        i, end, found = m.end(), m.end(), []
        while True:
            while i < len(text) and text[i] in " \t\r\n;":
                i += 1
            if i >= len(text) or text[i] != "{":
                break
            d = _decode_at(text, i)
            if not d:
                return None
            c = _call_from_obj(d[0], names)
            if c is None:
                return None
            found.append(c)
            i = end = d[1]
        if not found:
            return None
        calls += found
        spans.append((m.start(), end))
    # [TOOL_CALLS][...]  or  [TOOL_CALLS]name[ARGS]{...}
    for m in re.finditer(r"\[TOOL_CALLS\]", masked):
        if any(a <= m.start() < b for a, b in spans):
            continue
        i = m.end()
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        am = re.compile(r"([A-Za-z0-9_.\-]+)\s*\[ARGS\]\s*").match(text, i)
        if am:
            d = _decode_at(text, am.end())
            if not d or not isinstance(d[0], dict) or am.group(1) not in names:
                return None
            calls.append((am.group(1), d[0]))
            spans.append((m.start(), d[1]))
            continue
        if i < len(text) and text[i] in "[{":
            d = _decode_at(text, i)
            got = _calls_from_value(d[0], names) if d else None
            if not got:
                return None
            calls += got
            spans.append((m.start(), d[1]))
            continue
        return None
    if not calls:
        return None
    return calls, spans


def _whole_json(text: str, names):
    s = text.strip()
    fm = re.fullmatch(r"```[A-Za-z]*\s*\n?(.*?)\n?\s*```", s, re.S)
    if fm:
        s = fm.group(1).strip()
    if not s or s[0] not in "[{":
        return None
    try:
        v = json.loads(s)
    except ValueError:
        return None
    return _calls_from_value(v, names)


def recover(msg: dict, tool_names) -> bool:
    """Convert tool calls written as text in msg["content"] into msg["tool_calls"]. True when it did."""
    try:
        if not isinstance(msg, dict) or msg.get("tool_calls"):
            return False
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            return False
        names = set(tool_names or ())
        if not names:
            return False
        masked = _mask_think(content)
        tagged = None
        if re.search(r"<tool_call>|<function=|<\|python_tag\|>|\[TOOL_CALLS\]", masked, re.I):
            tagged = _tagged_blocks(content, names)
        if tagged:
            calls, spans = tagged
            spans.sort()
            rest, pos = [], 0
            for a, b in spans:
                rest.append(content[pos:a])
                pos = max(pos, b)
            rest.append(content[pos:])
            prose = "".join(rest).strip()
        else:
            calls = _whole_json(masked if "<think>" in content.lower() and masked.strip() else content, names)
            if not calls:
                return False
            prose = ""
        msg["tool_calls"] = _make(calls)
        msg["content"] = prose or None
        return True
    except Exception:
        return False
