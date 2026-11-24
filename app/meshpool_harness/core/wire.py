"""Stand-alone model transport: any OpenAI-compatible endpoint (chat-completions or Responses API). No eval/ needed.
Env: MESHPOOL_BASE_URL and MESHPOOL_API_KEY (or OPENAI_BASE_URL with OPENAI_API_KEY: providers.key_base/key), MH_WIRE=chat|responses.
A model id's provider prefix (providers.py) sends the call to the ChatGPT sign-in."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import stream as ST
from meshpool_harness.core import http as _mhttp


def to_responses(body: dict) -> dict:
    """Chat-completions request -> Responses API request (same content, same order: the prefix stays byte-stable).
    Content parts (text and images) convert as for the ChatGPT sign-in (chatgpt._parts); unlike that endpoint, system
    messages stay in the input and the output limit and temperature are sent."""
    from .chatgpt import _parts
    items = []
    for m in body.get("messages", []):
        role = m.get("role")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""),
                          "output": c if isinstance(c := (m.get("content") or ""), str) else json.dumps(c)})
            continue
        if m.get("content"):
            c = m["content"]
            items.append({"role": role, "content": c if isinstance(c, str) else
                          json.dumps(c) if role in ("assistant", "tool") else _parts(c)})
        for c in m.get("tool_calls") or []:
            f = c.get("function", {})
            items.append({"type": "function_call", "call_id": c.get("id", ""), "name": f.get("name", ""),
                          "arguments": f.get("arguments", "{}")})
    out = {"model": body["model"], "input": items, "max_output_tokens": body.get("max_tokens", 8192)}
    if body.get("prompt_cache_key"):
        out["prompt_cache_key"] = body["prompt_cache_key"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("tools"):
        out["tools"] = [{"type": "function", "name": t["function"]["name"],
                         "description": t["function"].get("description", ""),
                         "parameters": t["function"].get("parameters", {})} for t in body["tools"]]
        out["tool_choice"] = body.get("tool_choice", "auto")
    if (body.get("response_format") or {}).get("type") == "json_object":
        out["text"] = {"format": {"type": "json_object"}}
    return out


def from_responses(p: dict) -> dict:
    """Responses API reply -> chat-completions shape the harness expects."""
    text, calls = [], []
    for it in p.get("output", []):
        if it.get("type") == "message":
            text += [c.get("text", "") for c in it.get("content", []) if c.get("type") in ("output_text", "text")]
        elif it.get("type") == "function_call":
            calls.append({"id": it.get("call_id") or it.get("id", ""), "type": "function",
                          "function": {"name": it.get("name", ""), "arguments": it.get("arguments", "{}")}})
    u = p.get("usage") or {}
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0)
    msg = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        msg["tool_calls"] = calls
    return normalize_reply({"id": p.get("id"), "choices": [{"index": 0, "message": msg,
                                                            "finish_reason": "tool_calls" if calls else "stop"}],
                            "usage": {"prompt_tokens": u.get("input_tokens", 0),
                                      "completion_tokens": u.get("output_tokens", 0),
                                      "prompt_tokens_details": {"cached_tokens": cached},
                                      "prompt_cache_hit_tokens": cached}})


_THINK_RX = re.compile(r"<(think|thinking|reasoning)>(.*?)</\1>", re.S | re.I)
_CALL_BLOCK_RX = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
_KIMI_RX = re.compile(r"<\|tool_call_begin\|>\s*(?:functions\.)?([\w.\-]+?)(?::\d+)?\s*<\|tool_call_argument_begin\|>(.*?)<\|tool_call_end\|>", re.S)
_KIMI_SECTION_RX = re.compile(r"<\|tool_calls_section_begin\|>.*?(?:<\|tool_calls_section_end\|>|$)", re.S)
_GLM_ARG_RX = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.S)


def split_think(text: str) -> tuple[str, str]:
    """(answer, reasoning): reasoning a model wrote into its content as <think>...</think> (also <thinking>, <reasoning>),
    a lone closing tag (the opening one was part of the prompt template) or a block that never closed (cut off)."""
    if not text or "<" not in text:
        return text or "", ""
    found = [m.group(2).strip() for m in _THINK_RX.finditer(text)]
    rest = _THINK_RX.sub("", text)
    m = re.search(r"</(?:think|thinking|reasoning)>", rest, re.I)
    if m:                                                # reasoning without an opening tag: everything before it
        found.append(rest[:m.start()].strip())
        rest = rest[m.end():]
    m = re.search(r"<(?:think|thinking|reasoning)>", rest, re.I)
    if m:                                                # never closed: the rest is thinking, the reply was cut off
        found.append(rest[m.end():].strip())
        rest = rest[:m.start()]
    return rest.strip(), "\n\n".join(f for f in found if f)


def _json_objects(raw: str) -> list:
    """Every JSON value in `raw`, one after another ('{"a":1}{"a":1}' is two); [] when it does not parse that way."""
    dec, out, i = json.JSONDecoder(), [], 0
    raw = raw.strip()
    while i < len(raw):
        try:
            v, j = dec.raw_decode(raw, i)
        except ValueError:
            return []
        out.append(v)
        i = j
        while i < len(raw) and raw[i] in " \r\n\t,":
            i += 1
    return out


def fix_arguments(a) -> str:
    """Tool-call arguments as the JSON object string every consumer expects: a dict or list is dumped, None or blank
    is {}, a JSON string holding JSON (double encoded) is unwrapped, repeated objects ('{..}{..}', a model that emitted
    the call twice or a stream that restarted) are merged, the same one twice counting once. Text that is not JSON
    (a cut-off call) is returned unchanged: the caller says it was cut off."""
    if a is None:
        return "{}"
    if isinstance(a, (dict, list)):
        return json.dumps(a, ensure_ascii=False)
    if not isinstance(a, str):
        return json.dumps(a, ensure_ascii=False)
    raw = a.strip()
    if not raw or raw in ("null", "None"):
        return "{}"
    vals = _json_objects(raw)
    if not vals:
        return a
    for _ in range(2):                                   # a JSON string that holds the object
        if len(vals) == 1 and isinstance(vals[0], str):
            inner = _json_objects(vals[0])
            if inner:
                vals = inner
    if len(vals) == 1:
        return a if isinstance(vals[0], dict) and _json_objects(a) == vals else json.dumps(vals[0], ensure_ascii=False)
    dicts = [v for v in vals if isinstance(v, dict)]
    if len(dicts) != len(vals):
        return a
    merged: dict = {}
    for d in dicts:
        merged.update(d)
    return json.dumps(merged, ensure_ascii=False)


def _calls_in_text(text: str) -> tuple[str, list]:
    """Tool calls a model wrote as text instead of in tool_calls: <tool_call>{"name":..,"arguments":..}</tool_call>
    (Hermes, Qwen), <tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call> (GLM) and Kimi's
    <|tool_call_begin|>functions.name:0<|tool_call_argument_begin|>{..}<|tool_call_end|>. Returns (text without them,
    calls); ([text, []]) when there are none."""
    calls: list = []
    if "<|tool_call_begin|>" in text:
        for m in _KIMI_RX.finditer(text):
            calls.append((m.group(1), m.group(2).strip()))
        text = _KIMI_SECTION_RX.sub("", text)
    if "<tool_call>" in text:
        for m in _CALL_BLOCK_RX.finditer(text):
            body = m.group(1).strip()
            vals = _json_objects(body)
            if vals and isinstance(vals[0], dict):
                d = vals[0]
                f = d.get("function") if isinstance(d.get("function"), dict) else d
                name = f.get("name")
                if name:
                    calls.append((str(name), f.get("arguments", f.get("parameters", {}))))
                continue
            name = re.split(r"[\s<]", body, maxsplit=1)[0]
            if name and _GLM_ARG_RX.search(body):
                calls.append((name, {k.strip(): _glm_value(v) for k, v in _GLM_ARG_RX.findall(body)}))
        text = _CALL_BLOCK_RX.sub("", text)
    out = [{"id": "", "type": "function", "function": {"name": n, "arguments": a}} for n, a in calls if n]
    return (text.strip() if out else text), out


def _glm_value(v: str):
    v = v.strip()
    try:
        return json.loads(v)
    except ValueError:
        return v


def normalize_reply(resp: dict) -> dict:
    """Make a chat-completions reply from any server the shape the harness reads, whatever the model did (in place).
      * <think> blocks in the content move to reasoning_content (they must not reach the user or the next request);
      * content that is a list of parts becomes text; tool calls written as text become tool_calls;
      * every tool call gets a unique id, a name and arguments that are a JSON string (see fix_arguments);
      * finish_reason says tool_calls when there are some (some servers say stop), and stop when a cut says nothing."""
    if not isinstance(resp, dict):
        return resp
    for ch in resp.get("choices") or []:
        m = ch.get("message") if isinstance(ch, dict) else None
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c
                        if not isinstance(p, dict) or p.get("type") in (None, "text", "output_text"))
        if isinstance(c, str):
            c, think = split_think(c)
            if think:
                m["reasoning_content"] = (m.get("reasoning_content") or "") + ("\n" if m.get("reasoning_content") else "") + think
            calls = list(m.get("tool_calls") or [])
            if not calls and ("<tool_call>" in c or "<|tool_call_begin|>" in c):
                c, calls = _calls_in_text(c)
            m["content"] = c or None
        else:
            calls = list(m.get("tool_calls") or [])
        seen: set = set()
        fixed = []
        for n, tc in enumerate(calls):
            if not isinstance(tc, dict):
                continue
            f = tc.get("function") if isinstance(tc.get("function"), dict) else {"name": tc.get("name", ""), "arguments": tc.get("arguments")}
            name = str(f.get("name") or "").strip()
            args = fix_arguments(f.get("arguments"))
            if not name and args in ("{}", ""):
                continue
            cid = str(tc.get("id") or "")
            if not cid or cid in seen:
                cid = "call_" + hashlib.md5(f"{n}:{name}:{args}".encode("utf-8", "replace")).hexdigest()[:12]
                while cid in seen:
                    cid += "x"
            seen.add(cid)
            fixed.append({"id": cid, "type": "function", "function": {"name": name, "arguments": args}})
        if fixed:
            m["tool_calls"] = fixed
            if ch.get("finish_reason") in (None, "stop", "end_turn", "function_call"):
                ch["finish_reason"] = "tool_calls"
        else:
            m.pop("tool_calls", None)
            if ch.get("finish_reason") == "tool_calls":
                ch["finish_reason"] = "stop"
    return resp


def is_gateway_blip(status: int, raw) -> bool:
    """The Meshpool gateway sometimes fails its own key lookup under load: 503 'authentication database is temporarily
    unreachable' (type no_db_connection) or a 401 'Authentication Error' for a key that works a second later. Both
    pass on a retry; a key that is really wrong fails the same way three times and the caller then reports it."""
    if status not in (401, 503):
        return False
    t = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    return bool(re.search(r"no_db_connection|authentication database|temporarily unreachable|Authentication Error", t, re.I))


def log_usage_line(tag: str, model: str, usage: dict) -> None:
    """The spend guard cannot read Responses usage; write the per-call token line it would have written."""
    path = os.environ.get("MH_USAGE_LOG", "")       # opt-in; never a fixed machine path
    if not path or not os.path.exists(path):
        return
    with open(path, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},{tag},{model},{usage.get('prompt_tokens', 0)},"
                f"{(usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)},{usage.get('completion_tokens', 0)}\n")




def _first_then_idle(r, idle):
    """Yield SSE lines; after the first one, cut the socket timeout to the idle cutoff."""
    first = True
    for line in r:
        yield line
        if first:
            first = False
            try:
                r.fp.raw._sock.settimeout(idle)
            except Exception:                            # noqa: BLE001 - best effort
                pass


def _decode_sse_data(data: str) -> list:
    """The JSON values (or the string "[DONE]") one SSE event's data text holds. A server that forgets the blank line
    between events delivers several JSON lines as one event: when the joined text does not decode, try each line."""
    data = data.strip()
    if data == "[DONE]":
        return [data]
    try:
        return [json.loads(data)]
    except ValueError:
        if "\n" not in data:
            return []
    out = []
    for ln in data.split("\n"):
        ln = ln.strip()
        if ln == "[DONE]":
            out.append(ln)
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def sse_to_chat(lines, *, on_text=None, on_reasoning=None, data_idle=None, check=None) -> dict:
    """Assemble a streamed chat-completions reply (SSE lines) into the non-streamed response shape: content,
    tool calls (deltas merged by index), reasoning text and the final usage chunk.
    on_text gets the answer text as it arrives (without <think> blocks), on_reasoning the reasoning deltas and the
    think text. data_idle: end the read when only keep-alives arrive for that many seconds. check(): once per line."""
    from .stream import ThinkSplitter, iter_sse
    content, reasoning, calls, usage, finish, rid = [], [], {}, {}, None, None
    done, plain, events = False, [], 0
    by_index: dict = {}                                  # provider index -> key of the call slot currently using it
    splitter = ThinkSplitter() if (on_text or on_reasoning) else None

    def live(c, r):
        if c and on_text:
            on_text(c)
        if r and on_reasoning:
            on_reasoning(r)

    def tee(src):                                        # iter_sse drops non-field lines: keep them for the JSON fallback
        for raw in src:
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
            if len(plain) < 400 and line and not line.startswith(("data", ":", "event:", "id:", "retry:")):
                plain.append(line)                       # a server that ignored stream:true answers with plain JSON
            yield raw

    for _name, data in iter_sse(tee(lines), data_idle=data_idle, check=check):
        events += 1
        for ev in _decode_sse_data(data):
            if ev == "[DONE]":
                done = True
                break
            if not isinstance(ev, dict):
                continue
            if ev.get("error"):                          # an error event after HTTP 200 (overload, rejected request)
                err = ev["error"]
                msg = err.get("message") or json.dumps(err)[:300] if isinstance(err, dict) else str(err)[:300]
                code = err.get("code") or err.get("status") if isinstance(err, dict) else None
                code = int(code) if str(code).isdigit() and 400 <= int(code) < 600 else 502
                raise RuntimeError(f"meshpool HTTP {code}: {msg}")
            rid = ev.get("id") or rid
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                if not isinstance(ch, dict):
                    continue
                if isinstance(ch.get("usage"), dict) and ch["usage"] and not usage:
                    usage = ch["usage"]                  # Moonshot puts the usage inside the choice
                d = ch.get("delta") or {}
                if d.get("content"):
                    content.append(d["content"])
                    if splitter is not None and isinstance(d["content"], str):
                        live(*splitter.feed(d["content"]))
                rc = d.get("reasoning_content") or (d.get("reasoning") if isinstance(d.get("reasoning"), str) else "")
                if rc:
                    reasoning.append(rc)
                    live("", rc)
                for tc in d.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    f = tc.get("function") or {}
                    tid = tc.get("id")
                    idx = tc.get("index")
                    if idx is None:                      # no index: a new call id starts a new call
                        key = max(calls) if calls else None
                        if key is not None and tid and calls[key]["id"] and calls[key]["id"] != tid:
                            key = None
                    else:
                        key = by_index.get(idx)
                        cur = calls.get(key)
                        if cur is not None and (
                                (tid and cur["id"] and tid != cur["id"])            # another id: another call (W-07)
                                or (f.get("name") and not tid and cur["function"]["name"]
                                    and cur["function"]["arguments"])):             # a name without id after its arguments
                            key = None
                    if key is None:
                        key = max(calls) + 1 if calls else 0
                        calls[key] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    if idx is not None:
                        by_index[idx] = key
                    slot = calls[key]
                    if tid:
                        slot["id"] = tid
                    slot["function"]["name"] += f.get("name") or ""
                    a = f.get("arguments")
                    if a is not None and not isinstance(a, str):         # a server that sends the object, not its text
                        a = json.dumps(a, ensure_ascii=False)
                    slot["function"]["arguments"] += a or ""
                finish = ch.get("finish_reason") or finish
        if done:
            break
    if splitter is not None:
        live(*splitter.flush())
    if not events and plain:                             # not SSE at all: a whole JSON reply (or an error body)
        try:
            whole = json.loads("\n".join(plain))
        except ValueError:
            whole = None
        if isinstance(whole, dict) and whole.get("choices"):
            return normalize_reply(whole)
        if isinstance(whole, dict) and whole.get("error"):
            err = whole["error"]
            msg = (err.get("message") if isinstance(err, dict) else str(err)) or json.dumps(err)[:300]
            raise RuntimeError(f"meshpool HTTP 200 but not a model reply: {str(msg)[:200]}")   # a 200: not transient
        raise RuntimeError("meshpool HTTP 200 but not a model reply: " + "\n".join(plain)[:200])
    if finish is None and not done:                      # closed cleanly mid-reply: do not pass a cut-off reply on
        raise RuntimeError("HTTP 0: the stream ended before the reply was complete")
    msg = {"role": "assistant", "content": "".join(content) or None}
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return normalize_reply({"id": rid, "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                            "usage": usage})


class _Attempt(RuntimeError):
    """One HTTP attempt that did not answer 200; carries what the retry loop needs."""
    def __init__(self, status: int, raw: bytes, retry_after: str = ""):
        super().__init__(f"meshpool HTTP {status}: {raw[:300].decode('utf-8', 'replace')}")
        self.status, self.raw, self.retry_after = status, raw, retry_after


class SimpleLedger:
    """Minimal drop-in for eval.ledger.Ledger when the harness is installed on its own."""
    kind = ""                # class defaults: an instance made without __init__ still reads the address itself
    api_version = ""

    def __init__(self, phase: str = "run"):
        from meshpool_harness import providers as PV    # an OpenAI key only ever goes with OPENAI_BASE_URL
        self.base_url, self.api_key = PV.key_base(), PV.key()
        self.kind, self.api_version = "", ""            # "": read from the base address (or the saved setting)

    def chat(self, *, max_tokens=8192, extra=None, **kw):
        """One call; a reasoning model that spends the whole output limit thinking and answers nothing (empty
        content, no tool call, finish_reason length) is asked once more with a limit three times as large."""
        out = self._chat(max_tokens=max_tokens, extra=extra, **kw)
        try:
            ch = out["choices"][0]
            m = ch["message"]
            empty = ch.get("finish_reason") == "length" and not (m.get("content") or "").strip() and not m.get("tool_calls")
        except (KeyError, IndexError, TypeError, AttributeError):
            empty = False
        if empty and 0 < max_tokens < 24000:
            from meshpool_harness.core import usage   # the caller records the reply it gets; this one is billed too
            usage.record(kw.get("model", ""), out.get("usage"), base=getattr(self, "base_url", ""),
                         extra={"retry": "empty_reply"})
            ex = dict(extra or {})
            for k in ("max_completion_tokens",):
                if ex.get(k):
                    ex[k] = min(32000, int(ex[k]) * 3)
            return self._chat(max_tokens=min(32000, max_tokens * 3), extra=ex, **kw)
        return out

    def _chat(self, *, model, messages, profile="deepseek", task_id="", role="worker", job_id="", max_tokens=8192,
               temperature=None, extra=None, timeout=600, retries=2, key_only=False, side=False, **_):
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        body.update(extra or {})
        if body.get("max_completion_tokens"):            # the profile asked for the newer parameter name
            body.pop("max_tokens", None)
        if os.environ.get("MH_CACHE_KEY") == "1" and task_id:
            root = task_id.split("-esc")[0]
            from meshpool_harness.core import modelcaps as _MC   # fields this endpoint refused for this model before
            gone = _MC.refused(self.base_url, model)
            for k in ("prompt_cache_key", "user"):
                if k not in gone:
                    body[k] = root
        wire = os.environ.get("MH_WIRE", "chat")
        from meshpool_harness import providers as PV
        prov, body["model"] = PV.split(body["model"])    # the model's provider decides where the call goes
        if prov == "chatgpt" and not key_only:           # the user's ChatGPT sign-in (core/chatgpt.py); key_only: the check
            #                                                tests the key endpoint, whatever the settings say
            from meshpool_harness.core import chatgpt as CG
            out = from_responses(CG.chat(body, task_id=task_id, idle=int(os.environ.get("MH_STREAM_IDLE_S", "120"))))
            log_usage_line(task_id, body["model"], out["usage"])
            return out
        base_url, api_key = self.base_url, self.api_key
        # Streaming by default: a long generation keeps flowing and only a stall is cut off. A non-streamed call
        # waits up to 600 s per attempt, and a stalled upstream can sink a 20-minute engine run (seen live).
        anthropic = PV.wire_for(base_url) == "anthropic"           # an Anthropic-style key endpoint: Messages API
        stream = os.environ.get("MH_STREAM", "1") == "1" and (wire == "chat" or anthropic)
        if wire == "chat":
            body["messages"] = ST.prepare_messages(body.get("messages") or [])
        if stream:
            body["stream"] = True
            if not anthropic:
                body["stream_options"] = {"include_usage": True}
            idle = int(os.environ.get("MH_STREAM_IDLE_S", "60"))
            timeout = int(os.environ.get("MH_STREAM_FIRST_S", "120"))   # longer wait for the first token
        from meshpool_harness.core import endpoint as EP
        extra_h = {"X-Harness-Task-Id": task_id,
                   **({"session_id": task_id.split("-esc")[0]}
                      if os.environ.get("MH_SESSION_HEADERS") == "1" and task_id else {})}
        if anthropic:
            from meshpool_harness.core import anthropic_wire as AW
            url, hdrs = f"{base_url}/messages", AW.headers(api_key)
            body = AW.to_request(body)
            if stream:
                body["stream"] = True
        else:                                              # every other provider: core/endpoint.py decides URL and key header
            if wire == "responses":
                body = to_responses(body)
            url, body, hdrs = EP.chat_request(base_url, api_key, body, kind=self.kind, api_version=self.api_version,
                                              path="responses" if wire == "responses" else "chat")
            hdrs = PV.custom_auth(hdrs, base_url, api_key)  # a header and scheme the user set for this endpoint
        req = urllib.request.Request(url, data=ST.encode_body(body), method="POST",
                                     headers={**hdrs, "Content-Type": "application/json",
                                              **({"Accept": "text/event-stream"} if stream else {}), **extra_h})
        status, raw, retry_after, sse_reply = 0, b"", "", False
        gw_tries = 0
        attempt = 0
        from meshpool_harness.core import gateway as GW
        model = body.get("model", "")

        def probe():
            from meshpool_harness.agent.core import _probe_once
            return _probe_once(base_url, api_key, model, timeout=timeout)

        def send_once():
            """One HTTP attempt. A reply other than 200 raises _Attempt, so the gate counts it as a failed call and
            the retry loop below reads the status from it."""
            nonlocal sse_reply
            st, rw, ra = 0, b"", ""
            try:
                with EP.urlopen(req, timeout) as r:
                    st = r.status
                    sse_reply = stream and st == 200 and (
                        not anthropic or "event-stream" in (r.headers.get("Content-Type") or "").lower())
                    if sse_reply:
                        lines = _first_then_idle(r, idle)
                        if anthropic:
                            asm = AW.StreamAssembler()
                            for ev_name, ev_data in ST.iter_sse(lines, data_idle=timeout):
                                asm.feed(ev_name, ev_data)
                            rw = json.dumps(asm.result()).encode()
                        else:
                            rw = json.dumps(sse_to_chat(lines, data_idle=timeout)).encode()
                    else:
                        rw = ST.read_body(r, timeout)
            except urllib.error.HTTPError as e:
                rw, st = e.read(), e.code
                ra_s = ST.retry_after_seconds(getattr(e, "headers", None))
                ra = f" [retry-after: {ra_s:g}]" if ra_s is not None else ""
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as e:
                rw, st = f"timed out or connection lost: {_mhttp.explain(e, url)}".encode(), 0   # HTTPException: IncompleteRead
            if st != 200:
                raise _Attempt(st, rw, ra)
            return st, rw, ra

        while True:
            try:
                status, raw, retry_after = GW.call(base_url, api_key, model, send_once, probe=probe,
                                                   recover_s=0 if side else None)   # a side call never waits out an outage
            except _Attempt as a:
                status, raw, retry_after = a.status, a.raw, a.retry_after
            if status in (200, 429):
                break
            if gw_tries < 3 and is_gateway_blip(status, raw):
                gw_tries += 1                            # the gateway's own auth lookup failed for a moment: retry
                time.sleep(1.0 * gw_tries)
                continue
            if attempt >= retries:
                break
            attempt += 1
            time.sleep(1.5 * attempt)
        payload = json.loads(raw.decode("utf-8", "replace")) if raw[:1] in (b"{", b"[") else {"raw": raw[:300].decode("utf-8", "replace")}
        if status == 0 and isinstance(payload, dict) and payload.get("raw"):             # no reply at all: the cause (a TLS certificate, a timeout) is the text
            raise RuntimeError(f"meshpool HTTP 0: {payload['raw']}")
        if status != 200:
            err = RuntimeError(f"meshpool HTTP {status}: " + EP.explain(
                status, payload, kind=self.kind or EP.resolve(base_url)[0], host=PV.host(base_url),
                model=body.get("model", ""), key=api_key, api_version=self.api_version or EP.resolve(base_url)[1])
                + retry_after)
            err.body = payload           # the service's own error (param, code): callers learn refused fields from it
            raise err
        from meshpool_harness import provider_setup as _PS
        shape = ("openai" if anthropic and sse_reply                  # a streamed Anthropic reply is assembled as chat
                 else "anthropic" if anthropic else "responses" if wire == "responses" else "openai")
        if not _PS._is_reply(shape, payload):
            txt = payload.get("raw") if isinstance(payload, dict) and "raw" in payload else _PS._not_reply_msg(payload)
            if isinstance(payload, dict) and not payload.get("error") and "raw" not in payload:
                txt = json.dumps(payload)[:200]
            raise RuntimeError(f"meshpool HTTP 200 but not a model reply: {str(txt)[:200]}")
        if anthropic:
            return normalize_reply(payload) if sse_reply else AW.from_response(payload)
        return from_responses(payload) if wire == "responses" else normalize_reply(payload)
