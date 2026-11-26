"""Recover tool calls that a model wrote as text in `content` (local / OpenAI-compatible servers, DeepSeek DSML, and
Claude-style `<invoke name="..."><parameter name="...">` XML).

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
# DeepSeek DSML (V4: <｜DSML｜tool_calls>; V4.1: <｜DSML｜ calls>, a space before the tag name; an ASCII "|" variant
# also occurs on the wire). Gateways sometimes return it as plain content instead of structured tool_calls.
_D = r"<\s*[｜|]\s*DSML\s*[｜|]\s*"
_DE = r"<\s*/\s*[｜|]\s*DSML\s*[｜|]\s*"
_DSML_WRAP = re.compile(_D + r"(?:tool_)?calls\s*>(.*?)(?:" + _DE + r"(?:tool_)?calls\s*>|\Z)", re.S)
_DSML_INVOKE = re.compile(_D + r'invoke\s+name\s*=\s*"([^"]+)"\s*>(.*?)(?:' + _DE + r"invoke\s*>|(?=" + _D + r"invoke\b)|\Z)",
                          re.S)
_DSML_PARAM = re.compile(_D + r'parameter\s+name\s*=\s*"([^"]+)"(?:\s+string\s*=\s*"(true|false)")?\s*>(.*?)(?:'
                         + _DE + r"parameter\s*>|(?=" + _D + r"parameter\b)|(?=" + _DE + r"invoke)|\Z)", re.S)
_DSML_ANY = re.compile(r"[｜|]\s*DSML\s*[｜|]")
_EOS = re.compile(r"<[｜|]end▁of▁sentence[｜|]>")
# Claude-style calls written as text: <invoke name="x"><parameter name="k">v</parameter></invoke>, bare or inside a
# <function_calls> or <tool_calls> wrapper, with an optional "prefix:" on each tag name.
_XN = r"(?:[A-Za-z_][\w.-]*:)?"
_XML_WRAP = re.compile(r"<\s*" + _XN + r"(function_calls|tool_calls)\s*>(.*?)(?:<\s*/\s*" + _XN + r"\1\s*>|\Z)",
                       re.S | re.I)
_XML_WRAP_END = re.compile(r"<\s*/\s*" + _XN + r"(?:function_calls|tool_calls)\s*>", re.I)
_XML_INVOKE = re.compile(r"<\s*" + _XN + r"invoke\s+name\s*=\s*[\"']([^\"']+)[\"']\s*>(.*?)(?:<\s*/\s*" + _XN
                         + r"invoke\s*>|(?=<\s*" + _XN + r"invoke\b)|(?=<\s*/\s*" + _XN
                         + r"(?:function_calls|tool_calls)\s*>)|\Z)", re.S | re.I)
_XML_PARAM = re.compile(r"<\s*" + _XN + r"parameter\s+name\s*=\s*[\"']([^\"']+)[\"']\s*>(.*?)(?:<\s*/\s*" + _XN
                        + r"parameter\s*>|(?=<\s*" + _XN + r"parameter\b)|(?=<\s*/\s*" + _XN + r"invoke)|\Z)",
                        re.S | re.I)
_NAME_KEYS = ("name", "tool", "tool_name", "function")
_ARG_KEYS = ("arguments", "parameters", "args", "input")


def _mask_think(text: str) -> str:
    """Same length as text, with <think> blocks blanked out, so offsets found in it map back to the original."""
    return _THINK.sub(lambda m: " " * len(m.group(0)), text)


def _decode_at(text: str, i: int):
    """(value, end) of the JSON value starting at text[i], or None."""
    try:
        return json.JSONDecoder(strict=False).raw_decode(text, i)    # a raw newline inside a string is still the call
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


def _dsml_invokes(body: str, names):
    """[(name, args)] for the DSML invoke blocks in body, or None when one names an unknown tool or nothing parses.
    string="true" (the default) keeps a value verbatim; string="false" is JSON (raw text when it does not parse)."""
    out = []
    for m in _DSML_INVOKE.finditer(body):
        name = m.group(1).strip().split("::")[-1]          # V4.1 namespaces: "search::lookup" -> "lookup"
        if name not in names:
            return None
        args = {}
        for k, is_str, v in _DSML_PARAM.findall(m.group(2)):
            if is_str == "false":
                try:
                    args[k] = json.loads(v.strip())
                except ValueError:
                    args[k] = v
            else:
                args[k] = v[1:] if v.startswith("\n") else v   # the value starts right after ">" by the spec
        out.append((name, args))
    return out or None


def _xml_invokes(body: str, names):
    """[(name, args)] for the XML invoke blocks in body, or None when one names a tool not offered or nothing parses.
    A value that is JSON (a number, list, object) is decoded; any other value is kept as written, less the newline
    right after the opening tag and right before the closing one."""
    out = []
    for m in _XML_INVOKE.finditer(body):
        name = m.group(1).strip()
        if name not in names:
            return None
        inner = m.group(2)
        params = _XML_PARAM.findall(inner)
        if _XML_PARAM.sub("", inner).strip():             # loose text inside the invoke: not a call
            return None
        args = {}
        for k, v in params:
            try:
                args[k] = json.loads(v.strip())
            except ValueError:
                v = v[1:] if v.startswith("\n") else v
                args[k] = v[:-1] if v.endswith("\n") else v
        out.append((name, args))
    return out or None


def _tagged_blocks(text: str, names):
    """Find calls in formats a-d. Returns (calls, spans) or None if nothing found / an unknown name appears."""
    masked = _mask_think(text)
    calls, spans = [], []
    # DeepSeek DSML: a <｜DSML｜tool_calls> (or "｜DSML｜ calls") wrapper around invoke blocks, or bare invoke blocks
    if _DSML_ANY.search(masked):
        wraps = list(_DSML_WRAP.finditer(masked))
        for m in wraps:
            got = _dsml_invokes(text[m.start(1):m.end(1)], names)
            if not got:
                return None
            calls += got
            spans.append((m.start(), m.end()))
        if not wraps:
            inv = list(_DSML_INVOKE.finditer(masked))
            got = _dsml_invokes(text[inv[0].start():inv[-1].end()], names) if inv else None
            if not got:
                return None
            calls += got
            spans.append((inv[0].start(), inv[-1].end()))
        for m in _EOS.finditer(masked):
            spans.append((m.start(), m.end()))
        return calls, spans
    # Claude-style XML: <function_calls> / <tool_calls> wrappers around invoke blocks, or bare invoke blocks
    if _XML_INVOKE.search(masked):
        found = []
        for m in _XML_WRAP.finditer(masked):
            got = _xml_invokes(text[m.start(2):m.end(2)], names)
            if not got:
                return None
            found.append((m.start(), m.end(), got))
        for m in _XML_INVOKE.finditer(masked):
            if any(a <= m.start() < b for a, b, _ in found):
                continue
            got = _xml_invokes(text[m.start():m.end()], names)
            if not got:
                return None
            found.append((m.start(), m.end(), got))
        found.sort(key=lambda x: x[0])
        return [c for _, _, got in found for c in got], [(a, b) for a, b, _ in found]
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
        v = json.loads(s, strict=False)
    except ValueError:
        return None
    return _calls_from_value(v, names)


_LONE_TAG = re.compile(r"(?:\A|\n)[ \t]*</?[A-Za-z_][\w:.-]*/?>[ \t]*\Z")


def _trailing_json(text: str, names):
    """(calls, prose) when the text ends with a JSON call after some words ("I'll commit only those files." then
    {"tool_calls": [{"name": "exec_command", "arguments": {...}}]}): the words stay as narration, the call runs. The
    object must name an offered tool and end the text (a closing code fence may follow). Persona run, Omar 0.0.19: the
    reply was the raw call, and no commit was made."""
    t = text.rstrip()
    fm = re.search(r"\n?```\s*\Z", t)
    if fm:
        t = t[:fm.start()].rstrip()
    for m in re.finditer(r"(?:\A|\n)[ \t]*(?:```[A-Za-z]*[ \t]*\n[ \t]*)?(?=[\[{])", t):
        i = m.end()
        d = _decode_at(t, i)
        if not d or t[d[1]:].strip():
            continue
        got = _calls_from_value(d[0], names)
        if got:
            prose = t[:m.start()].rstrip()
            prose = _LONE_TAG.sub("", prose).rstrip()         # a stray tag the model put before the call
            return got, prose
    return None


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
        if re.search(r"<tool_call>|<function=|<\|python_tag\|>|\[TOOL_CALLS\]", masked, re.I) or _DSML_ANY.search(masked) \
                or _XML_INVOKE.search(masked):
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
            body = masked if "<think>" in content.lower() and masked.strip() else content
            calls = _whole_json(body, names)
            prose = ""
            if not calls:
                tj = _trailing_json(body, names)
                if not tj:
                    return False
                calls, prose = tj
        msg["tool_calls"] = _make(calls)
        msg["content"] = prose or None
        return True
    except Exception:
        return False


def _strip_json_call(text: str) -> str:
    """The text without a JSON tool call that ends it or is all of it (any tool name: the tools are off here, so
    nothing may run). Words before it stay."""
    any_name = _AnyNames()
    t = text.rstrip()
    if not t or "tool_calls" not in t and '"arguments"' not in t and '"parameters"' not in t:
        return text
    got = _whole_json(t, any_name)
    if got:
        return ""
    tj = _trailing_json(t, any_name)
    return tj[1] if tj else text


class _AnyNames:
    """A set that contains every name."""
    def __contains__(self, x):
        return isinstance(x, str) and bool(x)


def strip_markup(text: str) -> str:
    """Text with tool call markup removed: DeepSeek DSML calls and end-of-sentence tokens, and Claude-style
    `<invoke name=...>` calls with their <function_calls> / <tool_calls> wrappers. For a reply whose calls cannot run
    (tools are off for the final answer), so the user never sees raw call markup."""
    try:
        if not isinstance(text, str):
            return text
        dsml, xml = bool(_DSML_ANY.search(text)), bool(_XML_INVOKE.search(text))
        if not dsml and not xml:
            return _strip_json_call(text)
        out = text
        if dsml:
            out = _DSML_WRAP.sub("", out)
            out = _DSML_INVOKE.sub("", out)
            out = re.sub(_DE + r"(?:tool_)?calls\s*>", "", out)
            out = _EOS.sub("", out)
        if xml:
            out = _XML_WRAP.sub("", out)
            out = _XML_INVOKE.sub("", out)
            out = _XML_WRAP_END.sub("", out)
        return out.strip()
    except Exception:  # noqa: BLE001
        return text
