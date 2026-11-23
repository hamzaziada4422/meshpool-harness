"""The model's context window: how big it is, and keeping every request inside it.

Works for 4k to 1M windows. window() finds the size (MESHPOOL_CONTEXT_WINDOW / MH_CONTEXT_WINDOW, what the endpoint
reports, what an error message once said, the model's name), budget() derives the per-tool-output cap and the
compaction trigger from it, and fit() makes a request fit: compact tool schemas, a capped system prompt, elided old
tool output, head+tail truncation of any single huge message, and as a last resort the oldest steps dropped, always as
whole assistant-call + tool-result groups.
Tokens are estimated as characters / 3.5, on purpose a little high."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from urllib.parse import urlparse

CHARS_PER_TOKEN = 3.5
DEFAULT_LOCAL = 8192            # an unknown model on this computer or LAN: small until the server says otherwise
DEFAULT_HOSTED = 200_000        # an unknown hosted model (defaults/model_windows.json: default_hosted)
TARGET_CAP = 120_000            # working-context cap in tokens (MH_CONTEXT_TARGET): every call resends it
SMALL = 32_768                  # at or under this, Meshpool runs in its small-window mode
TRIGGER = 0.70                  # compaction starts at this share of the window
FIT = 0.88                      # a request is trimmed to this share of the room left after the reply

_LOCK = threading.Lock()
_LEARNED: dict[str, int] = {}   # model -> window learned from an error, silent truncation or the endpoint
_PROBED: dict[tuple, int] = {}
_TOLD: set = set()

_TABLE: list | None = None


def _table() -> list:
    """defaults/model_windows.json (a copy in the Meshpool data folder is read first): [(regex, tokens, uncertain)]."""
    global _TABLE
    if _TABLE is None:
        rows: list = []
        try:
            from meshpool_harness import config as C
            paths = [C.home() / "model_windows.json", C.APP / "defaults" / "model_windows.json"]
        except Exception:  # noqa: BLE001
            paths = []
        for p in paths:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                for m in d.get("models") or []:
                    rows.append((re.compile(m["match"], re.I), int(m["window"]), bool(m.get("uncertain"))))
            except (OSError, ValueError, KeyError, re.error):
                continue
        _TABLE = rows
    return _TABLE


def estimate(obj) -> int:
    """Tokens in a message list, a tool list or a string (characters / 3.5)."""
    if isinstance(obj, str):
        return int(len(obj) / CHARS_PER_TOKEN) + 1
    return int(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN) + 1


def _is_local(base: str) -> bool:
    try:
        u = urlparse(base or "")
    except ValueError:
        return False
    h = (u.hostname or "").lower()
    return (h in ("localhost", "::1") or h.startswith(("127.", "10.", "192.168.", "169.254.")) or h.endswith(".local")
            or re.match(r"172\.(1[6-9]|2\d|3[01])\.", h) is not None or u.port in (11434, 1234, 8080, 8000, 5000, 5001))


def _name_hint(model: str) -> int | None:
    low = (model or "").lower()
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s?([mk])(?![a-z0-9])", low.replace("-", " ").replace(":", " ").replace("_", " "))
    if m:                                   # "qwen2.5-coder:7b-8k": the size in the name
        n = int(float(m.group(1)) * (1_000_000 if m.group(2) == "m" else 1024))
        if 1024 <= n <= 4_000_000:
            return n
    for rx, n, _unsure in _table():
        if rx.search(low):
            return n
    return None


def _http_json(url: str, body: dict | None = None, key: str = "", timeout: float = 2.0):
    import urllib.request
    h = {"Content-Type": "application/json"}
    if key and "anthropic.com" in url:
        h.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif key:
        h["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=h,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def probe(base: str, model: str, key: str = "") -> int | None:
    """The window the endpoint reports for this model: Ollama /api/show (the num_ctx it will really use), LM Studio
    /api/v0/models, or context_length in an OpenAI-style /models list (OpenRouter and others). None if unknown."""
    if not base:
        return None
    root = re.sub(r"/v\d+/?$", "", base.rstrip("/"))
    try:
        d = _http_json(root + "/api/show", {"model": model, "name": model}, key)
        params = str(d.get("parameters") or "")
        m = re.search(r"\bnum_ctx\s+(\d+)", params)
        if m:
            return int(m.group(1))
        mi = d.get("model_info") or {}
        full = next((int(v) for k, v in mi.items() if k.endswith(".context_length") and isinstance(v, int)), None)
        if full:
            return min(full, 4096)         # no num_ctx set: Ollama serves its small default, not the model's maximum
    except Exception:  # noqa: BLE001 - not Ollama
        pass
    for url in (root + "/api/v0/models/" + model, base.rstrip("/") + "/models"):
        try:
            d = _http_json(url, None, key)
        except Exception:  # noqa: BLE001
            continue
        items = [d] if isinstance(d, dict) and ("id" in d or "loaded_context_length" in d) else \
            (d.get("data") if isinstance(d, dict) else d) or []
        for it in items:
            if not isinstance(it, dict) or str(it.get("id") or it.get("name") or "") not in (model, model.split("/")[-1], ""):
                if not (isinstance(it, dict) and model in str(it.get("id") or "")):
                    continue
            for k in ("loaded_context_length", "context_length", "context_window", "max_context_length",
                      "max_input_tokens", "inputTokenLimit"):
                if isinstance(it.get(k), int) and it[k] > 0:
                    return it[k]
    return None


_LISTS: dict[str, dict] = {}


def _listed(base: str, key: str) -> dict:
    """{model id: context tokens} from the endpoint's /models list, fetched once per endpoint ({} when it has none)."""
    with _LOCK:
        if base in _LISTS:
            return _LISTS[base]
    out: dict = {}
    try:
        d = _http_json(base.rstrip("/") + "/models?limit=1000", None, key)
        items = d.get("data") if isinstance(d, dict) else d
        for it in items or []:
            if not isinstance(it, dict):
                continue
            for k in ("loaded_context_length", "context_length", "context_window", "max_context", "max_context_length",
                      "max_input_tokens", "inputTokenLimit"):
                v = it.get(k) or (it.get("top_provider") or {}).get(k)
                if isinstance(v, int) and v > 0:
                    out[str(it.get("id") or it.get("name") or "")] = v
                    break
    except Exception:  # noqa: BLE001 - no list, or not reachable
        pass
    with _LOCK:
        _LISTS[base] = out
    return out


def window_info(model: str, base: str | None = None, key: str = "", probe_endpoint: bool = True) -> tuple[int, str]:
    """(tokens, source) for this model. Source: override, learned, provider, profile, table, default."""
    for var in ("MESHPOOL_CONTEXT_WINDOW", "MH_CONTEXT_WINDOW"):
        try:
            if os.environ.get(var):
                return max(1024, int(float(os.environ[var]))), "override"
        except ValueError:
            pass
    bare = (model or "").split(":", 1)[-1] if str(model).startswith(("chatgpt:", "meshpool:")) else (model or "")
    with _LOCK:
        if bare in _LEARNED:
            return _LEARNED[bare], "learned"
    if base is None:
        try:
            from meshpool_harness import providers as PV
            base = PV.key_base()
        except Exception:  # noqa: BLE001
            base = ""
    if str(model).startswith("chatgpt:"):
        return 400_000, "default"
    local = _is_local(base or "")
    if probe_endpoint and base and os.environ.get("MH_PROBE_CONTEXT", "1") != "0":
        k = (base, bare)
        with _LOCK:
            known = _PROBED.get(k)
        if known is None:
            got = (probe(base, bare, key) if local else 0) or _listed(base, key).get(bare) \
                or _listed(base, key).get(bare.split("/")[-1]) or 0
            with _LOCK:
                _PROBED[k] = known = got
        if known:
            return known, "provider"
    try:                                                     # a profile that states its model family's window
        from meshpool_harness.profiles import for_model
        w = ((for_model(bare).get("limits") or {}).get("context_window"))
        if isinstance(w, int) and w > 0 and any(m in bare for m in for_model(bare).get("match", [])):
            return w, "profile"
    except Exception:  # noqa: BLE001
        pass
    hint = _name_hint(bare)
    if hint:
        return hint, "table"
    return (DEFAULT_LOCAL if local else DEFAULT_HOSTED), "default"


def window(model: str, base: str | None = None, key: str = "", probe_endpoint: bool = True) -> int:
    """Tokens this model can hold (input plus reply)."""
    return window_info(model, base, key, probe_endpoint)[0]


def describe(model: str | None = None, base: str | None = None, key: str = "") -> str:
    """"context 1,000k (from provider), working set 120k" for the status line, `meshpool check` and Settings."""
    model = model or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    win, src = window_info(model, base, key)
    where = {"override": "your setting", "learned": "learned from the server", "provider": "from provider",
             "profile": "from the profile", "table": "from the model table", "default": "assumed"}[src]
    k = lambda n: f"{n / 1000:,.0f}k" if n >= 10_000 else f"{n:,}"          # noqa: E731
    return f"context {k(win)} ({where}); older steps are summarized above {k(target(win))} tokens"


def target(win: int) -> int:
    """The working context to aim for, in tokens: MH_CONTEXT_TARGET, else min(70% of the window, 120k).
    A call resends the whole conversation, so a 1M window would make every call expensive; compaction therefore
    keeps the conversation near this size, while the hard window still lets one huge read or reply through."""
    raw = os.environ.get("MH_CONTEXT_TARGET", "").strip().lower()
    try:
        if raw:
            n = float(raw[:-1]) * (1000 if raw.endswith("k") else 1_000_000 if raw.endswith("m") else 1) \
                if raw[-1] in "km" else float(raw)
            return max(1000, min(int(n), int(win * 0.9)))
    except ValueError:
        pass
    return int(min(win * TRIGGER, TARGET_CAP))


def learn(model: str, tokens: int) -> int:
    """Remember a window the server showed us (an error naming it, or a prompt it silently cut). Only ever shrinks."""
    bare = (model or "").split(":", 1)[-1] if str(model).startswith(("chatgpt:", "meshpool:")) else (model or "")
    tokens = int(tokens)
    if tokens < 512:
        return window(model, probe_endpoint=False)
    with _LOCK:
        cur = _LEARNED.get(bare)
        if cur is None or tokens < cur:
            _LEARNED[bare] = tokens
        return _LEARNED[bare]


def forget() -> None:
    with _LOCK:
        _LEARNED.clear()
        _PROBED.clear()
        _LISTS.clear()
        _TOLD.clear()


_LIMIT_RX = re.compile(
    r"maximum context length is (\d+)|context length of only (\d+)|context (?:window|size|length)(?: is| of| =|:)? ?(\d{3,7})\b|"
    r"\bn_ctx(?:_slot)?[ =:]+(\d{3,7})|(\d{3,7})[- ]token context|limit of (\d{3,7}) tokens|max(?:imum)? (?:input )?tokens? (?:is|of|:) ?(\d{3,7})",
    re.I)
CONTEXT_ERROR_RX = re.compile(
    r"maximum context length|context_length_exceeded|context length (?:of only|is|exceeded)|prompt is too long|"
    r"exceeds? the (?:available )?context (?:size|window|length)|\bn_ctx\b|context (?:window|size) (?:is )?(?:full|exceeded)|"
    r"input is too long|reduce the length of the messages|too many tokens in the (?:prompt|request)|"
    r"request (?:too large|exceeds)", re.I)


def parse_limit(msg: str) -> int | None:
    """The window an error message names ("maximum context length is 4096 tokens"), or None."""
    m = _LIMIT_RX.search(msg or "")
    if not m:
        return None
    n = next((int(g) for g in m.groups() if g), 0)
    return n if 512 <= n <= 4_000_000 else None


def is_context_error(msg: str) -> bool:
    return bool(CONTEXT_ERROR_RX.search(msg or ""))


# ---------- budgets -----------------------------------------------------------------------------------------------
def max_output(win: int, wanted: int | None) -> int:
    """The reply limit: what was asked for, but never more than a quarter of a small window."""
    wanted = int(wanted or 8192)
    if win >= 65_536:
        return wanted
    return max(256, min(wanted, win // 4))


def tool_output_chars(win: int, default: int = 8000) -> int:
    """Characters one tool result may keep: the usual 8000, but at most ~10% of the window."""
    return int(max(600, min(default, win * 0.10 * CHARS_PER_TOKEN)))


def compact_chars(win: int, fixed_tokens: int, limit: int) -> int:
    """The conversation size (chars) at which compaction starts: 70% of the window less the schemas, system prompt
    and reply; never above `limit` (the profile's own number)."""
    room = target(win) - fixed_tokens - max_output(win, None) * 0.5
    return int(max(1500, min(limit, room * CHARS_PER_TOKEN)))


def is_small(win: int) -> bool:
    return win <= SMALL


def notice(model: str, win: int) -> str:
    return (f"Small context window: {model or 'this model'} holds about {win:,} tokens, so Meshpool trims tool output, "
            f"shortens its instructions and summarizes earlier steps sooner. Set MESHPOOL_CONTEXT_WINDOW if that is wrong.")


def tell_once(model: str, win: int, say, source: str | None = None) -> None:
    """say(text) once per model and window: the small-window notice, or (with a source) the resolved window."""
    k = (model, win)
    with _LOCK:
        if k in _TOLD:
            return
        _TOLD.add(k)
    try:
        if is_small(win):
            say(notice(model, win))
        elif source is not None:
            say(describe(model))
    except Exception:  # noqa: BLE001
        pass


# ---------- tool schemas ------------------------------------------------------------------------------------------
ESSENTIAL = ("read_file", "edit", "write_file", "apply_patch", "bash", "grep", "glob", "finish", "run_tests",
             "diagnostics")
MINIMAL = ("read_file", "edit", "write_file", "bash", "finish")


def compact_tools(tools: list | None, win: int) -> list | None:
    """Smaller schemas for a small window: shorter descriptions at 32k and under, only the essential tools at 16k and
    under, the bare minimum at 6k and under. Tools the conversation already called stay (the request names them)."""
    if not tools or win > SMALL:
        return tools
    keep = MINIMAL if win <= 6144 else ESSENTIAL if win <= 16_384 else None
    out = []
    for t in tools:
        f = (t.get("function") or {})
        if keep is not None and f.get("name") not in keep:
            continue
        t = copy.deepcopy(t)
        f = t["function"]
        cap = 90 if win <= 6144 else 140 if win <= 16_384 else 260
        d = (f.get("description") or "").strip().replace("\n", " ")
        f["description"] = d if len(d) <= cap else d[:cap].rsplit(" ", 1)[0] + "."
        if win <= 16_384:
            for p in (f.get("parameters") or {}).get("properties", {}).values():
                if isinstance(p, dict) and "description" in p:
                    p["description"] = str(p["description"])[:60]
        out.append(t)
    return out or tools


# ---------- fitting a request -------------------------------------------------------------------------------------
def head_tail(text: str, limit: int, why: str = "to fit the model's context window") -> str:
    if len(text) <= limit:
        return text
    limit = max(200, limit)
    h = int(limit * 0.6)
    t = limit - h
    return text[:h] + f"\n... [{len(text) - h - t} characters cut {why}] ...\n" + text[-t:]


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
    return ""


def _size(messages: list) -> int:
    return len(json.dumps(messages, ensure_ascii=False))


def _groups(messages: list, start: int) -> list[tuple[int, int]]:
    """Index ranges [a, b) of whole turns after `start`: a user message, or an assistant message with its tool results."""
    out, i, n = [], start, len(messages)
    while i < n:
        j = i + 1
        if messages[i].get("role") == "assistant":
            while j < n and messages[j].get("role") == "tool":
                j += 1
        out.append((i, j))
        i = j
    return out


def fit(messages: list, tools: list | None, win: int, max_tokens: int | None = None, tighten: float = 1.0):
    """(messages, tools, max_tokens) that fit `win`. Copies what it changes; the caller's list is left as it was.
    tighten < 1 trims harder (a retry after the server said the request was too long)."""
    tools = compact_tools(tools, win)
    out_tok = max_output(win, max_tokens)
    room = max(512, win - out_tok - 48)
    limit_chars = int((room * FIT * tighten) * CHARS_PER_TOKEN) - len(json.dumps(tools or [], ensure_ascii=False))
    limit_chars = max(limit_chars, 1200)
    if _size(messages) <= limit_chars:
        return messages, tools, out_tok
    msgs = [dict(m) for m in messages]

    def over() -> bool:
        return _size(msgs) > limit_chars
    # 1. the system prompt and any single message may not take more than a third of the room
    third = max(600, limit_chars // 3)
    for m in msgs:
        if m.get("role") == "system" and isinstance(m.get("content"), str) and len(m["content"]) > third:
            m["content"] = head_tail(m["content"], third, "from the instructions to fit the context window")
    # 2. older tool output out, newest kept
    tool_idx = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    for keep in (4, 2, 1, 0):
        if not over():
            break
        for i in tool_idx[:max(0, len(tool_idx) - keep)]:
            c = msgs[i].get("content")
            if isinstance(c, str) and len(c) > 300 and not c.startswith("[elided"):
                msgs[i]["content"] = "[elided to fit the context window] " + c[:200]
    # 3. long arguments of past calls
    if over():
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                f = tc.get("function") or {}
                raw = f.get("arguments") or ""
                if len(raw) > 600:
                    try:
                        a = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(a, dict):
                        for k, v in a.items():
                            if isinstance(v, str) and len(v) > 300:
                                a[k] = v[:200] + f" ...[{len(v) - 200} chars elided]"
                        tc["function"] = dict(f, arguments=json.dumps(a, ensure_ascii=False))
    # 4. any one huge message cut head+tail
    if over():
        per = max(500, limit_chars // 3)
        for m in msgs:
            c = m.get("content")
            if isinstance(c, str) and len(c) > per and m.get("role") != "system":
                m["content"] = head_tail(c, per)
            elif isinstance(c, list):
                m["content"] = [dict(p, text=head_tail(str(p.get("text")), per)) if isinstance(p, dict)
                                and p.get("type") == "text" and len(str(p.get("text"))) > per else p for p in c]
    # 5. last resort: drop the oldest turns after the system message and the task, whole groups at a time
    if over():
        first = 1 if msgs and msgs[0].get("role") == "system" else 0
        head_n = first + (1 if len(msgs) > first and msgs[first].get("role") == "user" else 0)
        groups = _groups(msgs, head_n)
        dropped = 0
        while over() and len(groups) > 1:
            a, b = groups.pop(0)
            dropped += b - a
            for k in range(a, b):
                msgs[k] = None
            msgs = [m for m in msgs if m is not None]
            groups = _groups(msgs, head_n)
        if dropped:
            msgs.insert(head_n, {"role": "user", "content": f"[{dropped} earlier messages were dropped to fit the "
                                                            "model's context window; continue from the messages below.]"})
        if over():                       # the newest group alone is still too big: shorten what is in it
            per = max(400, (limit_chars - _size(msgs[:head_n])) // max(1, len(msgs) - head_n + 1))
            for m in msgs[head_n:]:
                c = m.get("content")
                if isinstance(c, str) and len(c) > per:
                    m["content"] = head_tail(c, per)
            if over() and head_n and isinstance(msgs[head_n - 1].get("content"), str):
                m = msgs[head_n - 1]
                m["content"] = head_tail(m["content"], max(400, limit_chars // 4))
    return msgs, tools, out_tok
