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
    """This computer or its LAN (loopback, private ranges, .local names, a bare host name like `gpu-box`). A public
    host never counts, whatever its port: nothing Ollama-style is ever asked of a remote gateway."""
    try:
        u = urlparse(base or "")
    except ValueError:
        return False
    h = (u.hostname or "").lower()
    if not h:
        return False
    if ":" in h:                                           # an IPv6 literal: loopback, link-local or unique-local
        return h == "::1" or h.startswith(("fe80:", "fc", "fd"))
    return (h == "localhost" or h.startswith(("127.", "10.", "192.168.", "169.254."))
            or re.match(r"172\.(1[6-9]|2\d|3[01])\.", h) is not None
            or h.endswith((".local", ".lan", ".internal", ".home.arpa")) or "." not in h)


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


PROBE_TIMEOUT_S = 8.0
RETRY_AFTER_S = 60.0              # a failed lookup is tried again after this long, never remembered for good


def _http_json(url: str, body: dict | None = None, key: str = "", timeout: float | None = None):
    import urllib.request
    h = {"Content-Type": "application/json"}
    if key and "anthropic.com" in url:
        h.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif key:
        h["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=h,
                                 method="POST" if body is not None else "GET")
    from meshpool_harness.core import http as _mhttp   # keyed: no cross-host redirect, no proxy for loopback
    with _mhttp.open(req, timeout or PROBE_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


_SEEN_SERVER: set = set()          # local endpoints that answered an Ollama / LM Studio route with real data
_NO_SHOW: set = set()             # endpoints that answered a plain 404 to Ollama/LM Studio style routes


def _mc():
    from meshpool_harness.core import modelcaps as MC
    return MC


def _route_absent(base: str, kind: str) -> bool:
    if (base, kind) in _NO_SHOW:
        return True
    try:
        if _mc()._mem_get(base, "*", kind):
            _NO_SHOW.add((base, kind))
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _mark_absent(base: str, kind: str) -> None:
    _NO_SHOW.add((base, kind))
    try:
        _mc()._mem_mark(base, "*", kind)           # remembered on disk: the next run does not ask again
    except Exception:  # noqa: BLE001
        pass


def _plain_404(e) -> bool:
    """A 404/405 that is the route missing (an HTML or empty page), not a JSON "model not found" from a real Ollama."""
    import urllib.error
    if not isinstance(e, urllib.error.HTTPError) or e.code not in (404, 405):
        return False
    try:
        d = json.loads(e.read().decode("utf-8", "replace"))
        return not (isinstance(d, dict) and d.get("error"))
    except Exception:  # noqa: BLE001
        return True


def _meta_window(it: dict) -> int:
    """vLLM reports max_model_len and llama.cpp meta.n_ctx in /v1/models: the window the server really serves."""
    v = it.get("max_model_len")
    if isinstance(v, int) and v > 0:
        return v
    v = (it.get("meta") or {}).get("n_ctx") if isinstance(it.get("meta"), dict) else None
    return v if isinstance(v, int) and v > 0 else 0


def probe(base: str, model: str, key: str = "") -> int | None:
    """The window a LOCAL endpoint reports for this model: Ollama /api/show (the num_ctx it will really use) or LM Studio
    /api/v0/models. Never asks a remote host; asks an endpoint that has no such route once, then remembers that (on disk
    too). None if unknown."""
    if not base:
        return None
    if not _is_local(base):                # a remote gateway: only its OpenAI-style /models list, never /api/*
        try:
            d = _http_json(base.rstrip("/") + "/models", None, key)
        except Exception:  # noqa: BLE001
            return None
        for it in (d.get("data") if isinstance(d, dict) else d) or []:
            if isinstance(it, dict) and str(it.get("id") or "") in (model, model.split("/")[-1]):
                for k in ("max_input_tokens", "context_length", "context_window", "max_context_length", "max_model_len"):
                    if isinstance(it.get(k), int) and it[k] > 0:
                        return it[k]
                if _meta_window(it):
                    return _meta_window(it)
        return None
    root = re.sub(r"/v\d+/?$", "", base.rstrip("/"))
    if not _route_absent(base, "no_show"):
        try:
            d = _http_json(root + "/api/show", {"model": model, "name": model}, key)
            if isinstance(d, dict) and (d.get("parameters") or d.get("model_info") or d.get("details")):
                _SEEN_SERVER.add(base)
            params = str(d.get("parameters") or "")
            m = re.search(r"\bnum_ctx\s+(\d+)", params)
            if m:
                return int(m.group(1))
            mi = d.get("model_info") or {}
            full = next((int(v) for k, v in mi.items() if k.endswith(".context_length") and isinstance(v, int)), None)
            if full:
                return min(full, 4096)     # no num_ctx set: Ollama serves its small default, not the model's maximum
        except Exception as e:  # noqa: BLE001 - not Ollama
            if _plain_404(e):
                _mark_absent(base, "no_show")
    if not _route_absent(base, "no_v0"):
        try:
            d = _http_json(root + "/api/v0/models/" + model, None, key)
            if isinstance(d, dict) and (d.get("loaded_context_length") or d.get("max_context_length") or d.get("arch")):
                _SEEN_SERVER.add(base)
            for k in ("loaded_context_length", "context_length", "max_context_length"):
                if isinstance(d.get(k), int) and d[k] > 0:
                    return d[k]
        except Exception as e:  # noqa: BLE001
            if _plain_404(e):
                _mark_absent(base, "no_v0")
    return None


_LISTS: dict[str, dict] = {}
_FAILED: dict = {}                # key -> time of the last failed lookup


def _recently_failed(k) -> bool:
    import time
    with _LOCK:
        t = _FAILED.get(k)
    return t is not None and time.time() - t < RETRY_AFTER_S


def _failed(k) -> None:
    import time
    with _LOCK:
        _FAILED[k] = time.time()


def _note_tools(base: str, it: dict) -> None:
    """An entry whose supported_parameters (OpenRouter style) lack "tools": no tools for it, before the first request."""
    try:
        sp = it.get("supported_parameters")
        mid = str(it.get("id") or it.get("name") or "")
        if mid and isinstance(sp, list) and sp and "tools" not in sp:
            from meshpool_harness.core import modelcaps as MC
            MC.learn(base, mid, no_tools=True)
    except Exception:  # noqa: BLE001 - metadata only
        pass


def note_models(base: str, key: str = "") -> None:
    """Read the endpoint's /models list once before the first request (what _listed learns is applied from then on)."""
    try:
        if base and os.environ.get("MH_PROBE_CONTEXT", "1") != "0":
            _listed(base, key)
    except Exception:  # noqa: BLE001
        pass


def _listed(base: str, key: str) -> dict:
    """{model id: context tokens} from the endpoint's /models list. Kept once it has been read; a failed read is not
    remembered (it is retried after a minute), so a slow start never pins the wrong windows for the whole run."""
    with _LOCK:
        if base in _LISTS:
            return _LISTS[base]
    if _recently_failed(("list", base)):
        return {}
    out: dict = {}
    try:
        d = _http_json(base.rstrip("/") + "/models?limit=1000", None, key)
        items = d.get("data") if isinstance(d, dict) else d
        for it in items or []:
            if not isinstance(it, dict):
                continue
            _note_tools(base, it)
            for k in ("max_input_tokens", "loaded_context_length", "context_length", "context_window", "max_context",
                      "max_context_length", "inputTokenLimit", "max_model_len"):
                v = it.get(k) or (it.get("top_provider") or {}).get(k)
                if isinstance(v, int) and v > 0:
                    out[str(it.get("id") or it.get("name") or "")] = v
                    break
            else:
                if _meta_window(it):
                    out[str(it.get("id") or it.get("name") or "")] = _meta_window(it)
        if not isinstance(items, list):
            raise ValueError("no model list")
    except Exception:  # noqa: BLE001 - unreachable, slow or no list: try again later
        _failed(("list", base))
        return {}
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
        if known is None and not _recently_failed(k):
            lst = _listed(base, key)
            got = (probe(base, bare, key) if local else 0) or lst.get(bare) or lst.get(bare.split("/")[-1]) or 0
            if got:
                with _LOCK:
                    _PROBED[k] = known = got
            elif lst or local:                      # the endpoint answered but does not list this model: ask again later
                _failed(k)
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
    # A small default is for a model server on this computer (Ollama, LM Studio: seen answering their routes, or on
    # their usual ports). A local proxy or mock in front of a hosted model says nothing about the window: assume large.
    try:
        port = urlparse(base or "").port
    except ValueError:
        port = None
    server = local and (base in _SEEN_SERVER or port in (11434, 1234))
    return (DEFAULT_LOCAL if server else DEFAULT_HOSTED), "default"


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


LEARN_FLOOR = 16_384             # a window below this is only ever believed when the server NAMED it in an error


def learn(model: str, tokens: int, explicit: bool = False) -> int:
    """Remember a window the server showed us. explicit: an error message named it (parse_limit); anything else (a
    guess from usage numbers) is ignored below LEARN_FLOOR. Only ever shrinks, and only in this process."""
    bare = (model or "").split(":", 1)[-1] if str(model).startswith(("chatgpt:", "meshpool:")) else (model or "")
    tokens = int(tokens)
    if tokens < (512 if explicit else LEARN_FLOOR):
        return window(model, probe_endpoint=False)
    with _LOCK:
        cur = _LEARNED.get(bare)
        if cur is None or tokens < cur:
            _LEARNED[bare] = tokens
        return _LEARNED[bare]


_SILENT: dict[str, list] = {}


def observe_silent_cut(model: str, sent_tokens: int, usage: dict, win: int, base: str | None = None) -> bool:
    """A local server that silently cuts a too-long prompt (Ollama) reports far fewer prompt tokens than were sent.
    Believed only when: the endpoint is local, the window is a guess (unknown model), never one the table or provider states, the TOTAL prompt tokens
    (fresh plus cached) stay far under what was sent twice in a row, and the implied window is a sane size. A gateway
    that reports only fresh tokens, or a metering proxy in front, therefore never shrinks the window."""
    try:
        u = usage or {}
        used = int(u.get("prompt_tokens") or 0)
        used += int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        used += int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        used = max(used, int(u.get("total_tokens") or 0) - int(u.get("completion_tokens") or 0))
        if base is None:
            from meshpool_harness import providers as PV
            base = PV.key_base()
        if not _is_local(base or "") or window_info(model, base, probe_endpoint=False)[1] not in ("default", "learned"):   # never below a size the table or provider states
            return False
        implied = used + max_output(win, None)
        if used < 512 or sent_tokens < 1500 or sent_tokens <= used * 1.6 or implied < LEARN_FLOOR:
            _SILENT.pop(model, None)
            return False
        seen = _SILENT.setdefault(model, [])
        seen.append(implied)
        del seen[:-2]
        if len(seen) < 2 or abs(seen[0] - seen[1]) > 0.15 * max(seen):
            return False
        learn(model, int(min(seen)))
        return True
    except Exception:  # noqa: BLE001
        return False


class CompactGuard:
    """Compaction must not become a loop: it may run again only after the conversation has grown 30% since the last
    one, and after two compactions that shrank nothing much and changed no file it stops (the task carries on or finishes)."""

    def __init__(self):
        self.last_size = 0
        self.last_edits = None
        self.idle = 0

    def allow(self, size: int, edits: int = 0) -> bool:
        if self.last_size and size < self.last_size * 1.3:
            return False
        if self.idle >= 2 and edits == self.last_edits:
            return False
        return True

    def record(self, size_after: int, edits: int = 0, size_before: int | None = None) -> None:
        """A compaction that cut the conversation by less than 30% and changed no file counts as idle."""
        useless = size_before is None or size_after > 0.7 * size_before
        self.idle = self.idle + 1 if (edits == self.last_edits and useless) else 0
        self.last_size, self.last_edits = size_after, edits


def forget() -> None:
    with _LOCK:
        _LEARNED.clear()
        _PROBED.clear()
        _LISTS.clear()
        _FAILED.clear()
        _NO_SHOW.clear()
        _SEEN_SERVER.clear()
        _SILENT.clear()
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
    floor = 1500 if win <= 16_384 else 12_000          # a window that holds a real conversation never compacts at 1.5k
    return int(max(floor, min(limit, room * CHARS_PER_TOKEN)))


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
        sizes = [len(json.dumps(m, ensure_ascii=False)) for m in msgs]      # once: no re-serialising per dropped group
        total = sum(sizes) + 2 * len(sizes)
        drop, dropped = set(), 0
        for a, b in groups[:-1]:                                            # oldest first; the newest group always stays
            if total <= limit_chars:
                break
            for k in range(a, b):
                drop.add(k)
                total -= sizes[k] + 2
            dropped += b - a
        msgs = [m for i, m in enumerate(msgs) if i not in drop]
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
