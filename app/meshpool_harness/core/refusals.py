"""Read an endpoint's refusal text and say what to change, so the agent fixes the request instead of resending it.

    d = diagnose("HTTP 400: {...}", body)      # None, or {"action", "learn", "note", ...action keys}

Actions (checked in this order): "tls", "no_tools", "schema_subset", "tool_ids", "strict_roles", "max_out",
"max_tokens_param", "drop_field". "learn" holds ModelCaps fields to remember (core/modelcaps.py learn()); "note" is one
plain sentence for the user. Everything except "tls" applies only to HTTP 4xx messages. A real context overflow (the
input itself does not fit) is not diagnosed here: core/context.py owns it."""
from __future__ import annotations

import json
import re

KNOWN = ("temperature", "top_p", "tool_choice", "parallel_tool_calls", "stream_options", "reasoning_effort",
         "response_format", "seed", "presence_penalty", "frequency_penalty", "prompt_cache_key", "user", "store",
         "metadata", "service_tier", "logprobs")
_NEVER_DROP = {"model", "messages", "tools", "stream", "max_tokens", "max_completion_tokens"}

_TLS = re.compile(r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|self[- ]signed certificate|hostname mismatch|"
                  r"WRONG_VERSION_NUMBER|SSLError|\[SSL", re.I)
_HTTP4 = re.compile(r"HTTP 4\d\d")

_NO_TOOLS = re.compile(
    r"does not support tools|auto\W{1,3} tool choice requires|--enable-auto-tool-choice|tools param requires --jinja|"
    r"--jinja|does not support function calling|function calling is not (?:supported|enabled)|"
    r"tool use is not supported|tools are not supported|does not support tool", re.I)

_SCHEMA_WHERE = re.compile(r"tools\[|function_declarations|\bparameters\b|\bschema\b", re.I)
_SCHEMA_KW = re.compile(r"additionalProperties|\$ref|\$defs|\bformat\b|patternProperties|\banyOf\b|\boneOf\b|\ballOf\b|"
                        r"\benum\b|unknown name|invalid schema|unsupported (?:keyword|schema)|not supported for", re.I)

_TOOL_IDS = re.compile(
    r"tool[ _]call[ _]id was .{0,60}but must be|tool_call_id\b[^\n]{0,100}(?:must match|pattern|invalid|must be)|"
    r"(?:maximum length 40|max(?:imum)? length of 40)[^\n]{0,120}tool_call|tool_call[^\n]{0,120}maximum length 40|"
    r"duplicate value for ['\"`]?tool_call_id|tool_use ids must be unique|unexpected [`'\"]?tool_use_id|"
    r"tool_call_id[^\n]{0,100}not found", re.I)

_ROLES = re.compile(
    r"roles must alternate|successive user or assistant messages|consecutive (?:user|assistant) messages|"
    r"unexpected role|system role not supported|system message (?:must|should) be (?:at the beginning|first)|"
    r"only (?:one|a single) system message|expected last role", re.I)

_PARAM_MAXTOK = re.compile(r"unsupported parameter:?\s*['\"`]?max_tokens['\"`]?[^\n]*max_completion_tokens", re.I)

_REFUSAL_WORD = re.compile(r"not supported|unsupported|unknown|unrecognized|not allowed|not permitted|extra inputs|"
                           r"extra_forbidden|invalid|does not support|cannot find field|unexpected", re.I)
_NOUN = r"(?:parameters?|arguments?|fields?|propert(?:y|ies)|keys?|names?)"


def is_tls_error(msg: str) -> bool:
    return bool(_TLS.search(msg or ""))


def _json_part(msg: str):
    i = msg.find("{")
    j = msg.find("[")
    starts = [x for x in (i, j) if x >= 0]
    if not starts:
        return None
    try:
        return json.JSONDecoder().raw_decode(msg[min(starts):])[0]
    except ValueError:
        return None


def _max_out(msg: str) -> dict | None:
    # context forms that state the input: when it fits, only the reply is too large
    win = re.search(r"maximum context length is (\d+) tokens", msg, re.I)
    if win:
        window = int(win.group(1))
        inp = re.search(r"your request has (\d+) input tokens", msg, re.I) \
            or re.search(r"\((\d+) in the messages", msg, re.I) \
            or re.search(r"\(\d+ > \d+ - (\d+)\)", msg)
        if not inp:
            return None
        n_in = int(inp.group(1))
        if n_in + 256 < window:
            return {"action": "max_out", "max_output": window - n_in - 64, "context_window": window,
                    "learn": {"window": window},
                    "note": f"The model's context is {window:,} tokens, so replies are now limited to what is left "
                            "after your conversation."}
        return None                                       # the input itself does not fit: a real overflow
    cap = (re.search(r"supports at most (\d+) completion tokens", msg, re.I)
           or re.search(r"max(?:_completion)?_tokens[^\n]{0,40}?(?:must be )?(?:less than or equal to|<=) ?(\d+)", msg, re.I)
           or re.search(r"max(?:_completion)?_tokens: \d+ > (\d+), which is the maximum", msg, re.I)
           or re.search(r"(?:must be|should be) (?:less than or equal to|<=) ?(\d+)[^\n]{0,40}(?:tokens)", msg, re.I))
    if cap and int(cap.group(1)) > 0:
        n = int(cap.group(1))
        return {"action": "max_out", "max_output": n, "learn": {"max_output": n},
                "note": f"This model replies with at most {n:,} tokens; the limit is now set to that."}
    return None


def _drop_field(msg: str, body: dict | None) -> dict | None:
    allowed = (set(body) if isinstance(body, dict) else set(KNOWN)) - _NEVER_DROP
    if not allowed:
        return None
    found: list[str] = []

    def add(name):
        name = str(name or "").split(".")[0].strip()
        if name in allowed and name not in found:
            found.append(name)

    data = _json_part(msg)
    detail = data.get("detail") if isinstance(data, dict) else (data if isinstance(data, list) else None)
    if isinstance(detail, list):                          # pydantic: loc is ["body", "<field>", ...]
        for it in detail:
            if isinstance(it, dict) and isinstance(it.get("loc"), list):
                loc = [x for x in it["loc"] if isinstance(x, str)]
                if loc and loc[0] == "body":
                    loc = loc[1:]
                if loc and (it.get("type") == "extra_forbidden" or _REFUSAL_WORD.search(str(it.get("msg") or ""))):
                    add(loc[0])
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        err = data["error"]
        if err.get("param") and _REFUSAL_WORD.search(str(err.get("message") or "")):
            add(err["param"])
    if not found and not _REFUSAL_WORD.search(msg):
        return None
    if not found:
        for m in re.finditer(r"unknown name ['\"`]([\w.]+)['\"`]", msg, re.I):
            add(m.group(1))
        for m in re.finditer(r"(?:unrecognized|unknown|unsupported) (?:request )?(?:arguments?|parameters?|fields?|"
                             r"propert(?:y|ies))(?: supplied)?:\s*([^\n]+)", msg, re.I):
            for tok in re.split(r"[\s,;]+", m.group(1)):
                add(tok.strip("'\"`.()"))
    if not found:
        for n in sorted(allowed, key=len, reverse=True):
            q = re.escape(n)
            if re.search(rf"\b{_NOUN}\b[^\n]{{0,40}}?(?<![\w.]){q}(?!\w)|(?<![\w.]){q}(?!\w)[^\n]{{0,25}}?\b"
                         rf"(?:parameter|argument|field|property|key)\b", msg, re.I):
                add(n)
    if not found:
        return None
    return {"action": "drop_field", "field": found[0], "fields": found, "learn": {"refused": list(found)},
            "note": f"This endpoint does not accept the '{found[0]}' setting; it is left out from now on."}


def diagnose(msg: str, body: dict | None = None) -> dict | None:
    m = msg or ""
    if is_tls_error(m):
        return {"action": "tls", "learn": {},
                "note": "The endpoint's TLS certificate is not trusted; for a local server use http://, otherwise add "
                        "its CA to the system trust store or point SSL_CERT_FILE at a CA bundle."}
    if not _HTTP4.search(m):
        return None
    if _NO_TOOLS.search(m):
        return {"action": "no_tools", "learn": {"no_tools": True},
                "note": "This model or server cannot call tools, so tool use is described in text instead."}
    if _SCHEMA_WHERE.search(m) and _SCHEMA_KW.search(m) and not _PARAM_MAXTOK.search(m):
        return {"action": "schema_subset", "learn": {"schema_subset": True},
                "note": "This endpoint accepts only a simple subset of tool schemas; the schemas are simplified."}
    if _TOOL_IDS.search(m):
        return {"action": "tool_ids", "learn": {"tool_id_style": "alnum9"},
                "note": "This endpoint wants short letters-and-digits tool call ids; they are rewritten."}
    if _ROLES.search(m):
        return {"action": "strict_roles", "learn": {"strict_roles": True},
                "note": "This endpoint needs strictly alternating roles; messages are merged to fit."}
    d = _max_out(m)
    if d:
        return d
    if _PARAM_MAXTOK.search(m):
        return {"action": "max_tokens_param", "learn": {"max_tokens_param": "max_completion_tokens"},
                "note": "This model wants max_completion_tokens instead of max_tokens; it is used from now on."}
    return _drop_field(m, body)


ERROR_SAMPLES = [
    ("tls_selfsigned", "HTTP 0: connection problem (<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify "
     "failed: self-signed certificate (_ssl.c:1006)>)", "tls"),
    ("tls_wrong_version", "HTTP 0: connection problem (<urlopen error [SSL: WRONG_VERSION_NUMBER] wrong version number>)",
     "tls"),
    ("tls_hostname", "HTTP 0: connection problem (hostname mismatch, certificate is not valid for 'x')", "tls"),
    ("ollama_no_tools", 'HTTP 400: {"error":"registry.ollama.ai/library/gemma:2b does not support tools"}', "no_tools"),
    ("vllm_no_tools", 'HTTP 400: {"object":"error","message":"\\"auto\\" tool choice requires --enable-auto-tool-choice '
     'and --tool-call-parser to be set","type":"BadRequestError","param":null,"code":400}', "no_tools"),
    ("llamacpp_no_tools", 'HTTP 400: {"error":{"message":"tools param requires --jinja flag"}}',
     "no_tools"),
    ("fc_unsupported", 'HTTP 400: {"error":{"message":"this model does not support function calling"}}', "no_tools"),
    ("fc_not_enabled", 'HTTP 400: {"error":{"message":"Function calling is not enabled for this deployment"}}',
     "no_tools"),
    ("tool_use_unsupported", 'HTTP 400: {"error":"tool use is not supported by this model"}', "no_tools"),
    ("tools_not_supported", 'HTTP 400: {"error":{"message":"tools are not supported"}}', "no_tools"),
    ("gemini_additional", "HTTP 400: {\"error\":{\"message\":\"Invalid JSON payload received. Unknown name "
     "\\\"additionalProperties\\\" at 'tools[0].function_declarations[1].parameters': Cannot find field.\"}}",
     "schema_subset"),
    ("gemini_format", "HTTP 400: {\"error\":{\"message\":\"...function_declarations[0].parameters.properties[url].format: "
     "only 'enum' and 'date-time' are supported for STRING type\"}}", "schema_subset"),
    ("invalid_schema_fn", 'HTTP 400: {"error":{"message":"Invalid schema for function \'read\': $ref is not allowed"}}',
     "schema_subset"),
    ("ref_defs", 'HTTP 400: {"error":{"message":"$defs are not supported in the tools schema"}}', "schema_subset"),
    ("mistral_ids", "HTTP 400: {\"object\":\"error\",\"message\":\"Tool call id was call_abc123def456 but must be a-z, "
     "A-Z, 0-9, with a length of 9.\"}", "tool_ids"),
    ("tool_call_id_pattern", "HTTP 400: tool_call_id 'x:1' does not match pattern ^[a-zA-Z0-9_-]+$ (invalid)", "tool_ids"),
    ("tool_call_id_len", "HTTP 400: messages[3].tool_calls[0].id: Expected a string with maximum length 40, but got "
     "a string with length 55 (tool_calls)", "tool_ids"),
    ("tool_call_id_dup", "HTTP 400: Duplicate value for 'tool_call_id' in messages", "tool_ids"),
    ("tool_use_unique", "HTTP 400: messages: tool_use ids must be unique", "tool_ids"),
    ("tool_use_unexpected", "HTTP 400: unexpected `tool_use_id` found in `tool_result` blocks", "tool_ids"),
    ("tool_call_id_missing", "HTTP 400: tool_call_id call_9 not found in previous assistant message", "tool_ids"),
    ("roles_alternate", "HTTP 400: {\"error\":{\"message\":\"Conversation roles must alternate user/assistant\"}}",
     "strict_roles"),
    ("roles_deepseek", "HTTP 400: deepseek-reasoner does not support successive user or assistant messages", "strict_roles"),
    ("roles_consecutive", "HTTP 400: consecutive user messages are not allowed", "strict_roles"),
    ("roles_unexpected", "HTTP 400: Unexpected role 'tool' after role 'user'", "strict_roles"),
    ("system_first", "HTTP 400: System message must be at the beginning.", "strict_roles"),
    ("system_one", "HTTP 400: only one system message is allowed", "strict_roles"),
    ("system_unsupported", "HTTP 400: System role not supported for this model", "strict_roles"),
    ("last_role", "HTTP 400: Expected last role User or Tool but got assistant", "strict_roles"),
    ("openai_cap", "HTTP 400: {\"error\":{\"message\":\"max_tokens is too large: 8192. This model supports at most 4096 "
     "completion tokens, whereas you provided 8192.\"}}", "max_out"),
    ("cap_le", "HTTP 400: max_tokens must be less than or equal to 4096", "max_out"),
    ("cap_le_sym", "HTTP 400: max_tokens must be <= 4096", "max_out"),
    ("anthropic_cap", "HTTP 400: {\"error\":{\"message\":\"max_tokens: 8192 > 4096, which is the maximum allowed number of "
     "output tokens for claude-x\"}}", "max_out"),
    ("vllm_new", "HTTP 400: {\"object\":\"error\",\"message\":\"'max_tokens' or 'max_completion_tokens' is too large: "
     "8192. This model's maximum context length is 4096 tokens and your request has 120 input tokens (8192 > 4096 - "
     "120).\"}", "max_out"),
    ("vllm_old", "HTTP 400: This model's maximum context length is 4096 tokens. However, you requested 8312 tokens "
     "(120 in the messages, 8192 in the completion). Please reduce the length of the messages or completion.", "max_out"),
    ("max_tokens_param", "HTTP 400: {\"error\":{\"message\":\"Unsupported parameter: 'max_tokens' is not supported with "
     "this model. Use 'max_completion_tokens' instead.\"}}", "max_tokens_param"),
    ("gemini_unknown_field", "HTTP 400: {\"error\":{\"message\":\"Invalid JSON payload received. Unknown name "
     "\\\"prompt_cache_key\\\": Cannot find field.\"}}", "drop_field"),
    ("azure_unrecognized", "HTTP 400: {\"error\":{\"message\":\"Unrecognized request argument supplied: "
     "prompt_cache_key\"}}", "drop_field"),
    ("azure_unrecognized_plural", "HTTP 400: Unrecognized request arguments supplied: prompt_cache_key, user", "drop_field"),
    ("pydantic_422", "HTTP 422: {\"detail\":[{\"type\":\"extra_forbidden\",\"loc\":[\"body\",\"prompt_cache_key\"],"
     "\"msg\":\"Extra inputs are not permitted\",\"input\":\"x\"}]}", "drop_field"),
    ("generic_quoted", "HTTP 400: parameter 'seed' is not supported by this model", "drop_field"),
    ("generic_unknown", "HTTP 400: Unknown parameter: 'reasoning_effort'.", "drop_field"),
]
