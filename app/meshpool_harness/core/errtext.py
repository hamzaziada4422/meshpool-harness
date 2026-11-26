"""A failed model call in plain words, for the person reading the chat, the terminal or a Work result.

The transports (agent/core.py, core/wire.py, core/chatgpt.py) raise errors whose text starts with a status ("HTTP 0:",
"meshpool HTTP 502:", "chatgpt HTTP 400:"), because the retry policy (core/client.classify_error) reads that text.
That text is not for people: it can hold a raw JSON error body, an HTML page, a Python exception or the bytes repr of
an empty reply. This module turns it into what went wrong, the service's own words, where (host and model) and what
to do next. It only reads the text the failed call already holds: it never sends a request.

Keys never reach the result: the caller's key is cut out (endpoint.redact_key) and any other key-like text is hidden
(redact.redact)."""
from __future__ import annotations

import html
import json
import re

_STATUS = re.compile(r"^\s*(?:meshpool |chatgpt )?HTTP (\d{1,3})\b:?\s*", re.I)
_TAIL = re.compile(r"\s*\[(?:retry-after(?:-ms)?|request id|rate limit(?:ed)?)[^\]]*\]", re.I)
_REASON = {400: "bad request", 401: "unauthorized", 402: "payment required", 403: "forbidden", 404: "not found",
           405: "method not allowed", 407: "proxy sign-in required", 408: "request timeout", 409: "conflict",
           413: "request too large", 415: "unsupported media type", 422: "unprocessable request",
           429: "too many requests", 500: "internal server error", 502: "bad gateway", 503: "service unavailable",
           504: "gateway timeout"}


def _redact(text: str, key: str = "") -> str:
    from meshpool_harness.core import endpoint as EP
    from meshpool_harness.redact import redact
    return redact(EP.redact_key(str(text or ""), key))


def _one_line(text: str, n: int = 220) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    cut = t[:n - 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n // 2 else cut).rstrip(" ,;:") + "…"


def _html_text(body: str) -> str:
    """The title of an HTML page, else its first heading, else its visible text."""
    for rx in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(rx, body, re.I | re.S)
        if m and re.sub(r"<[^>]+>", " ", m.group(1)).strip():
            return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)).strip())
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.I | re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def is_html(body: str) -> bool:
    return bool(re.match(r"\s*(?:<!doctype html|<html|<head|<body|<\?xml|<h1|<title)", str(body or ""), re.I))


def _from_json(d) -> str:
    """The message in an error body of any common shape: OpenAI {"error": {"message"}}, {"error": "text"}, LiteLLM and
    FastAPI {"detail": "text" | [{"msg", "loc"}]}, Azure and gateways {"message": ...}, Anthropic {"type": "error",
    "error": {...}}, Gemini [{"error": {...}}], OpenRouter's {"error": {"metadata": {"raw": ...}}}."""
    if isinstance(d, list):
        return next((m for m in (_from_json(x) for x in d) if m), "")
    if isinstance(d, str):
        return d
    if not isinstance(d, dict):
        return ""
    for k in ("error", "detail", "message", "msg", "error_description", "title"):
        v = d.get(k)
        if isinstance(v, dict):
            m = _from_json(v)
            raw = (v.get("metadata") or {}).get("raw") if isinstance(v.get("metadata"), dict) else None
            if m and isinstance(raw, str) and raw.strip() and raw.strip() not in m:
                m += f" ({raw.strip()})"
            if m:
                return m
        elif isinstance(v, list) and v:
            parts = []
            for x in v[:3]:
                if isinstance(x, dict) and (x.get("msg") or x.get("message")):
                    loc = ".".join(str(p) for p in x.get("loc") or [] if p != "body")
                    parts.append((f"{loc}: " if loc else "") + str(x.get("msg") or x.get("message")))
                elif isinstance(x, str):
                    parts.append(x)
            if parts:
                return "; ".join(parts)
        elif isinstance(v, str) and v.strip():
            return v
    return ""


def said(body, key: str = "") -> str:
    """The service's own words in a reply body (JSON of any common shape, an HTML page, plain text), one line, keys
    hidden. "" when the body says nothing (empty, or JSON without a message)."""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if isinstance(body, (dict, list)):
        return _one_line(_redact(_from_json(body), key))
    s = _TAIL.sub("", str(body or "")).strip()
    m = re.fullmatch(r"b(['\"])(.*)\1", s, re.S)                  # a bytes repr from a reply that could not be read
    if m:
        s = m.group(2).encode("utf-8", "replace").decode("unicode_escape", "replace").strip()
    if not s:
        return ""
    if s[0] in "{[":
        try:
            return _one_line(_redact(_from_json(json.loads(s)), key))
        except ValueError:                                           # a body cut to length: pick the message out
            m = re.search(r'"(?:message|detail|msg|error)"\s*:\s*"((?:[^"\\]|\\.)*)', s)
            if m:
                try:
                    txt = json.loads('"' + m.group(1) + '"')
                except ValueError:
                    txt = m.group(1)
                return _one_line(_redact(txt, key))
            return ""
    if is_html(s):
        return _one_line(_redact(_html_text(s), key), 160)
    return _one_line(_redact(s, key))


def _quote(text: str) -> str:
    return f'The service said: "{text.rstrip(".")}."' if text else ""


def _net_reason(text: str) -> tuple[str, str]:
    """(what happened, what to do) for a connection that failed before any reply."""
    low = text.lower()
    if re.search(r"tunnel connection failed: 407|proxy authentication required", low):
        return ("the proxy asked for a sign-in (407 proxy authentication required)",
                "Put your proxy user name and password in HTTPS_PROXY (http://user:password@proxy:port), or ask your "
                "IT team which proxy to use.")
    if re.search(r"tunnel connection failed|proxy", low):
        m = re.search(r"tunnel connection failed: ([^)>\]]+)", text, re.I)
        return (f"the proxy would not connect to it ({m.group(1).strip()})" if m else "the proxy refused the connection",
                "Check the HTTPS_PROXY setting, or ask your IT team whether this address is allowed.")
    if re.search(r"name or service not known|nodename nor servname|getaddrinfo failed|name resolution|"
                 r"no address associated|errno -[235]\b|errno 1100[14]\b", low):
        return ("its name could not be found (DNS lookup failed)",
                "Check the endpoint address in Settings for typing mistakes, and your internet connection.")
    if re.search(r"connection refused|errno 111\b|errno 61\b|winerror 10061", low):
        return ("nothing answered at that address (connection refused)",
                "Check the endpoint address and port in Settings; a local server (Ollama, LM Studio) must be running.")
    if re.search(r"network is unreachable|no route to host|errno 101\b|errno 113\b|winerror 1005[01]", low):
        return ("this computer has no network route to it", "Check your internet connection or VPN.")
    if re.search(r"timed out|timeout", low):
        return ("it did not answer in time", "Try again in a moment; if it keeps happening, check the endpoint address "
                                            "and your connection.")
    if re.search(r"remote end closed|connection reset|connection aborted|broken pipe|incompleteread", low):
        return ("the connection was closed before the reply arrived", "Try again in a moment.")
    if "did not come back" in low:
        return ("it did not answer when it was checked again", "Check the network and the endpoint address in "
                                                                "Settings; `meshpool check --live` tests it.")
    return ("the connection failed", "Check the network and the endpoint address in Settings; "
                                     "`meshpool check --live` tests it.")


def reason(raw: str) -> str:
    """Why a probe or call failed, a few plain words for a status line ("nothing answered at that address ...")."""
    status, rest = parse(raw)
    if status in (None, 0):
        return _net_reason(rest)[0]
    return f"{status} {_REASON.get(status, '')}".strip()


def _where(host: str, model: str) -> str:
    return f"{host or 'the endpoint'}" + (f" (model {model})" if model else "")


# what agent/core.py adds to a failure it retried (and its "QUOTA: " marker): not part of the service's reply
_AGENT_NOTE = re.compile(r"\s*\(the endpoint answers small requests[^)]*\)\s*$|^QUOTA:\s*")


def parse(raw: str) -> tuple[int | None, str]:
    """(status or None, the text after the status prefix) of a transport error text."""
    m = _STATUS.match(str(raw or ""))
    if not m:
        return None, str(raw or "")
    return int(m.group(1)), str(raw)[m.end():]


def explain(raw: str, *, host: str = "", model: str = "", key: str = "") -> str:
    """The whole message for a model call that failed: what happened (with host and model), the service's own words
    and the next step. A text that is already plain words (no status prefix) is kept, with any raw body inside it
    replaced by the service's words."""
    raw = _AGENT_NOTE.sub("", str(raw or ""))
    status, rest = parse(raw)
    where = _where(host, model)
    if status is None:
        return _clean_inline(raw, key)
    rest_l = rest.lower()

    # a 200 that is not a model reply (an error body, a web page, nothing at all)
    m = re.match(r"(?:but not a model reply|the endpoint answered without a model reply|the endpoint sent an unreadable "
                 r"reply|the model endpoint sent no usable reply)\s*:?\s*\(?", rest, re.I)
    if status == 200 or m:
        body = rest[m.end():] if m else rest
        body = re.sub(r"\)\s*$", "", body) if m and rest[m.end() - 1:m.end()] == "(" else body
        if re.fullmatch(r"\s*(?:b''|b\"\"|no choices|no message|not a json object)?\s*", body, re.I):
            return (f"{where} answered, but the reply was empty: no model text came back. Check the endpoint address in "
                    "Settings (it usually ends in /v1) and that the model is loaded, then send your message again.")
        if re.match(r"\s*blocked by a content filter", body, re.I):
            return (f"{where} blocked this reply with its content filter and sent nothing back. Rephrase the request, "
                    "or pick another model in Settings.")
        if re.match(r"\s*error:\s*", body, re.I):
            body = re.sub(r"^\s*error:\s*", "", body, flags=re.I)
        text = said(body, key)
        if is_html(body) or (body.startswith("b'") and "<" in body):
            page = f' ("{text}")' if text else ""
            return (f"{where} sent a web page{page} instead of a model reply. This usually means a sign-in page, a "
                    "company proxy or an address that is not the model API. Check the endpoint address in Settings "
                    "(it usually ends in /v1).")
        return (f"{where} answered without a model reply. {_quote(text) or 'It sent no error message.'} "
                + _next_for(text, 200, host))

    if status == 0:
        if "stream ended before the reply was complete (the reply was empty)" in rest_l:
            return (f"{where} answered, but the reply was empty: no model text came back. Check the endpoint address in "
                    "Settings (it usually ends in /v1) and that the model is loaded, then send your message again.")
        if "stream ended before the reply was complete" in rest_l:
            return (f"The reply from {where} stopped before it was complete: the connection closed in the middle of "
                    "the answer. Send your message again; if it keeps happening, a proxy or the server may be cutting "
                    "long replies.")
        what, todo = _net_reason(rest)
        return f"Couldn't reach {where}: {what}. {todo}"

    text = said(rest, key)
    if status == 407:
        return (f"The proxy between this computer and {where} asked for a sign-in (407 proxy authentication required). "
                "Put your proxy user name and password in HTTPS_PROXY (http://user:password@proxy:port), or ask your "
                "IT team which proxy to use.")
    if text and not _looks_raw(rest) and not is_html(rest):
        # core/wire.py already explained it (endpoint.explain): keep its words, add where it happened
        body = _TAIL.sub("", rest).strip()
        return f"{where}: {_redact(body, key)}"
    reason = _REASON.get(status, "")
    head = f"{where} refused the request ({status}{' ' + reason if reason else ''})."
    if status >= 500:
        head = f"{where} had a problem on its side ({status}{' ' + reason if reason else ''})."
    elif status == 429:
        head = f"{where} is limiting requests (429 too many requests)."
    elif status in (401, 403):
        head = f"{where} did not accept the API key ({status}{' ' + reason if reason else ''})."
    return " ".join(x for x in (head, _quote(text), _next_for(text, status, host)) if x)


_PERMANENT = re.compile(
    r"model\b.{0,60}(?:not found|does not exist|doesn't exist|unknown|invalid|not available|not supported)|"
    r"(?:invalid|unknown|unsupported|no such) model|model_not_found|deployment ?not ?found|"
    r"api.?key|unauthori[sz]ed|authenticat|invalid token|bad token|forbidden|permission denied|"
    r"invalid request|invalid_request|malformed|field required|must not be empty|unrecognized request argument", re.I)


def permanent_200(raw: str) -> bool:
    """A 200 that carried an error body whose words name a cause a retry cannot fix (an unknown model, a refused key,
    a malformed request). Waiting and sending the same request again only delays the message by half a minute."""
    status, rest = parse(_AGENT_NOTE.sub("", str(raw or "")))
    if status != 200 and not re.match(r"(?:the endpoint answered without a model reply|the model endpoint sent no "
                                       r"usable reply \(error:)", rest, re.I):
        return False
    return bool(_PERMANENT.search(said(re.sub(r"^.*?reply\s*(?:\(error:)?:?\s*", "", rest, count=1), "") or rest))


def for_terminal(text: str, one_shot: bool = False) -> str:
    """The same message for the terminal: it has no Settings page, and a one-shot run (meshpool -p) has no chat to
    send "continue" in."""
    t = str(text or "")
    t = re.sub(r"\b[Oo]pen Settings in the app\b", "run `meshpool setup`", t)
    t = re.sub(r"\bin [Ss]ettings\b", "with `meshpool setup` (or in the settings file)", t)
    if one_shot:
        t = re.sub(r'\b[Ss]end "continue"(?: in a moment)?', lambda m: ("R" if m.group(0)[0] == "S" else "r")
                   + "un the command again with -c", t)
        t = re.sub(r"\b([Ss])end (?:your|a) message (?:again|to try again)", lambda m: ("R" if m.group(1) == "S"
                   else "r") + "un the command again", t)
    return t


def detail(raw: str, *, host: str = "", model: str = "", key: str = "") -> str:
    """A short line for after a plain message: status, host, model and the service's own words, keys hidden."""
    raw = _AGENT_NOTE.sub("", str(raw or ""))
    status, rest = parse(raw)
    if status is None:
        m = re.search(r"(?:meshpool |chatgpt )?HTTP \d{3}:", raw)     # "The endpoint rejected the key. HTTP 401: {...}"
        if not m:
            return _one_line(_clean_inline(raw, key), 160)
        status, rest = parse(raw[m.start():])
    where = (host or "the endpoint") + (f", model {model}" if model else "")
    if status == 0:
        what, _ = _net_reason(rest)
        if "stream ended before the reply was complete" in rest.lower():
            what = "the reply stream ended early"
        return f"{where}: {what}"
    if status == 200:                                        # an error body on a 200: the words, not the code
        body = re.sub(r"^(?:but not a model reply|the endpoint answered without a model reply)\s*:?\s*", "", rest,
                      flags=re.I)
        text = said(body, key)
        return f"{where} answered without a model reply" + (f"; the service said: {text}" if text else "")
    text = said(rest, key)
    code = f"HTTP {status}" + (f" {_REASON[status]}" if status in _REASON else "")
    if "(an error event in the reply stream)" in rest:      # core/wire.py: the stream named no status
        code, text = "an error event in the reply", text.replace(" (an error event in the reply stream)", "")
    wait = re.search(r"\[retry-after:\s*([\d.]+)\s*\]", rest)
    tail = f"; it asked to wait {_secs(float(wait.group(1)))}" if wait else ""
    return f"{code} from {where}" + (f"; the service said: {text}" if text else "") + tail


def _secs(s: float) -> str:
    return f"{int(s)} s" if s < 120 else f"{round(s / 60)} min" if s < 7200 else f"{s / 3600:.1f} h"


def _looks_raw(rest: str) -> bool:
    s = _TAIL.sub("", rest).strip()
    return not s or s[0] in "{[<" or s.startswith("b'")


def _next_for(text: str, status: int, host: str) -> str:
    low = (text or "").lower()
    if status in (401, 403) or re.search(r"api.?key|unauthori[sz]ed|authenticat|invalid token|bad token|"
                                         r"permission|forbidden", low):
        return f"Check the API key for {host or 'this endpoint'} in Settings, then send your message again."
    if status == 429 or re.search(r"rate.?limit|too many requests|quota", low):
        return "Wait a minute, then send \"continue\"; everything done so far is kept."
    if status == 404 or re.search(r"model\b.{0,60}(?:not found|does not exist|unknown|invalid|not available|not loaded)|"
                                  r"(?:invalid|unknown) model|deployment", low):
        return ("Check the model name and the endpoint address in Settings (the address usually ends in /v1), or "
                "pick another model.")
    if status == 405:
        return "This address does not take chat requests. Check the endpoint address in Settings (it usually ends in /v1)."
    if status >= 500:
        return "Try again in a moment, or pick another model in Settings."
    if status == 200:
        return "Check the endpoint address, key and model in Settings (`meshpool check --live` tests them)."
    return ("The endpoint did not take the request as sent. Check the model in Settings or pick another one, then "
            "send your message again.")


_INLINE = re.compile(r"(?:meshpool |chatgpt )?HTTP (\d{3}):\s*([\[{<b].*?)(?=\)\s*$|$)", re.S)


def _clean_inline(raw: str, key: str) -> str:
    """A plain message that quotes a raw reply ("... (HTTP 404: {json})"): the raw part becomes the service's words."""
    def sub(m):
        text = said(m.group(2), key)
        code = int(m.group(1))
        return f"HTTP {code}" + (f" {_REASON[code]}" if code in _REASON else "") + (f": {text}" if text else "")
    from meshpool_harness.core import endpoint as EP
    return _INLINE.sub(sub, EP.redact_key(raw, key))      # the service's words are redacted in said()


# ---------- an outside engine's or harness's own error text (Codex, Pi, Hermes, an office engine) --------------------
# They write errors their own way: "unexpected status 404 Not Found: not found, url: http://127.0.0.1:44615/v1/responses",
# "stream disconnected before completion: error sending request for url (...)", "exceeded retry limit, last status:
# 500 Internal Server Error", a JSON-RPC error, a Python exception. None of that is for the person reading the chat.
_URL = re.compile(r"\bhttps?://[^\s)\]>'\"]+", re.I)
_CODE = re.compile(r"\b(?:unexpected status|last status:?|status(?: code)?:?|HTTP(?:/\d(?:\.\d)?)?|error code:?)\s*"
                   r"([1-5]\d\d)\b|\b([45]\d\d) (?:Not Found|Bad Request|Unauthori[sz]ed|Forbidden|Payment Required|"
                   r"Method Not Allowed|Request Timeout|Conflict|Payload Too Large|Unprocessable|Too Many Requests|"
                   r"Internal Server Error|Bad Gateway|Service Unavailable|Gateway Time-?out)\b", re.I)
_REFUSED = re.compile(r"connection refused|errno 111\b|errno 61\b|winerror 10061|econnrefused|actively refused|"
                      r"name or service not known|getaddrinfo|dns error|failed to lookup address|"
                      r"network is unreachable|no route to host", re.I)
_CUT = re.compile(r"connection reset|econnreset|reset by peer|broken pipe|remote end closed|connection aborted|"
                  r"stream disconnected|stream closed before|error sending request|error decoding response|"
                  r"incomplete ?message|unexpected eof|incompleteread", re.I)
_SLOW = re.compile(r"timed out|timeout|deadline exceeded", re.I)
_RAWISH = re.compile(r"\bunexpected status\b|\bTraceback\b|\b[A-Z]\w*(?:Error|Exception)\b:|\[Errno|\bErrno\b|"
                     r"\bJSON-?RPC\b|\{\s*\"|<html|\bstack ?trace\b|\bpanicked at\b", re.I)


def _code_of(text: str) -> int | None:
    m = _CODE.search(str(text or ""))
    return int(m.group(1) or m.group(2)) if m else None


def is_raw(text: str) -> bool:
    """The text carries what people should not have to read: a URL, a status code, "unexpected status", an exception
    name or a raw body."""
    t = str(text or "")
    return bool(_URL.search(t) or _CODE.search(t) or _RAWISH.search(t))


def engine_failed(text: str) -> bool:
    """An outside engine's error that means the work never started: the AI service answered with an HTTP 4xx or 5xx,
    or nothing answered (connection refused, a reset, a name that does not resolve)."""
    t = str(text or "")
    code = _code_of(t)
    return bool((code is not None and 400 <= code < 600) or _REFUSED.search(t) or _CUT.search(t))


def outside(raw: str) -> tuple[str, str]:
    """(the plain sentence the chat shows, the raw text for its Details fold) for an outside engine's or harness's
    error. A text that is already plain words comes back unchanged, with no details. The plain sentence names no
    engine, URL or status code. Keys never reach either part."""
    t = str(raw or "").strip()
    if not t or not is_raw(t):
        return t, ""
    code = _code_of(t)
    low = t.lower()
    if code in (401, 403) or re.search(r"api.?key|unauthori[sz]ed|invalid.{0,20}token|authenticat", low):
        plain = "The AI service did not accept the API key. Check it in Settings, then send your message again."
    elif code == 429 or re.search(r"rate.?limit|too many requests|quota", low):
        plain = "The AI service is limiting requests right now. Wait a minute, then send your message again."
    elif code == 404:
        plain = ("The AI service did not recognize the request. Check the model and the endpoint address in Settings, "
                 "or pick another model.")
    elif code is not None and code >= 500:
        plain = "The AI service had a problem on its side. Try again in a moment."
    elif code is not None and code >= 400:
        plain = ("The AI service refused the request. Check the model in Settings or pick another one, then send your "
                 "message again.")
    elif _REFUSED.search(t):
        plain = ("The AI service is not answering. Check that it is running and its address in Settings, then send "
                 "your message again.")
    elif _CUT.search(t):
        plain = "The connection to the AI service was cut off. Send your message again."
    elif _SLOW.search(t):
        plain = "The AI service did not answer in time. Try again in a moment."
    else:
        plain = "Something went wrong while working on this. Send your message again; the details are below."
    return plain, _redact(t)
