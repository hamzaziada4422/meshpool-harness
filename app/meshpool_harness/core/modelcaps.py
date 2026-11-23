"""What a model on an endpoint can do, in one place, so no optimisation asks "is this GPT or DeepSeek?".

    caps = resolve(base, model)         # a ModelCaps, cached per (endpoint, model)
    caps.reasoning, caps.efforts        # "none" | "effort" | "always", and the effort values it accepts
    caps.cache                          # "auto" (prefix caching by the server) | "explicit" (cache_control) | "none"
    caps.cache_hints                    # may prompt_cache_key / user be sent?
    caps.wire                           # "chat" | "responses" | "messages"
    caps.window, caps.max_output, caps.parallel_tools, caps.reliability
    caps.usage_cached / usage_reasoning # does the reply report cached / reasoning tokens (learned from replies)

Order of precedence for every field: the user's override (MH_CAPS json, MH_REASONING_EFFORT, MH_WIRE, a profile's
"caps" mapping) > what the endpoint's replies taught (learn_*) > the endpoint's kind (host) > the name-pattern table
(app/defaults/model_hints.json, or <data folder>/model_hints.json) > conservative defaults: chat completions, no
reasoning parameter, no cache fields, parallel calls on. An unknown model therefore sends nothing a strict server could
refuse; a field the server refuses with a 400 is dropped and remembered by the caller (agent/core.py _REFUSED)."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse

_LOCK = threading.Lock()
_CACHE: dict[tuple, "ModelCaps"] = {}
_LEARNED: dict[tuple, dict] = {}            # (host, model) -> fields learned from replies
_PERSIST = ("refused", "no_tools", "strict_roles", "tool_id_style", "schema_subset", "max_tokens_param", "first_byte_s",
            "window", "max_output", "reasoning")      # usage_cached / usage_reasoning change every reply: memory only
_DISK = {"path": None, "mtime": None}       # what was last read from model_caps_learned.json (None path: not loaded)


@dataclass
class ModelCaps:
    wire: str = "chat"
    reasoning: str = "none"
    efforts: tuple = ()
    cache: str = "none"
    min_prefix: int = 1024
    cache_hints: bool = False
    window: int = 128_000
    max_output: int = 8192
    parallel_tools: bool = True
    reliability: str = "medium"
    usage_cached: bool = False
    usage_reasoning: bool = False
    refused: tuple = ()               # body fields this endpoint+model refused (dropped from every request)
    no_tools: bool = False            # cannot take tools: tool use goes through a text protocol
    strict_roles: bool = False        # needs strictly alternating roles / one leading system message
    tool_id_style: str = ""           # "alnum9": tool call ids must be 9 letters/digits
    schema_subset: bool = False       # accepts only a simple subset of JSON schema in tools
    max_tokens_param: str = ""        # "max_completion_tokens" when max_tokens is refused
    first_byte_s: float = 0.0         # slowest observed time to the first reply byte (local servers load models)
    source: dict = field(default_factory=dict)       # field -> where it came from (for docs and debugging)

    def effort(self, lane: str = "task", override: str = "") -> str:
        """The reasoning_effort to send, "" when this model takes none. Quick chat answers get the lowest, other work
        the middle one: the provider default (often high) is what makes reasoning models slow and costly."""
        if self.reasoning != "effort" or not self.efforts:
            return ""
        want = (override or os.environ.get("MH_REASONING_EFFORT", "")).strip().lower()
        if want in ("off", "none", "default"):
            return ""
        if want in self.efforts:
            return want
        if os.environ.get("MH_AUTO_EFFORT", "0") != "1":
            return ""                  # MH_AUTO_EFFORT=1: pick low/medium by lane (unmeasured, so off by default)
        if lane == "chat":
            return "low" if "low" in self.efforts else self.efforts[0]
        return "medium" if "medium" in self.efforts else self.efforts[len(self.efforts) // 2]


def _hints() -> list[dict]:
    from meshpool_harness import config as C
    for p in (C.home() / "model_hints.json", C.APP / "defaults" / "model_hints.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return [m for m in d.get("models", []) if isinstance(m, dict) and m.get("match")]
        except (OSError, ValueError):
            continue
    return []


def _host(base: str) -> str:
    try:
        return (urlparse(base or "").hostname or "").lower()
    except ValueError:
        return ""


def _hostkey(base: str) -> str:
    """The key learned facts are stored under: host and port, so two servers on one machine do not share them."""
    try:
        u = urlparse(base or "")
        h = (u.hostname or "").lower()
        return f"{h}:{u.port}" if h and u.port else h
    except ValueError:
        return ""


def _local(base: str) -> bool:
    from meshpool_harness.core import context as CX
    return CX._is_local(base or "")


def is_local(base: str) -> bool:
    return _local(base)


def _bare(model: str) -> str:
    m = model or ""
    return m.split(":", 1)[-1] if m.startswith(("chatgpt:", "meshpool:")) else m


def _learned_path():
    """The learned-facts file, or None when persistence is off (MH_CAPS_LEARNED=off)."""
    raw = os.environ.get("MH_CAPS_LEARNED", "").strip()
    if raw.lower() == "off":
        return None
    if raw:
        return raw
    from meshpool_harness import config as C
    return str(C.home() / "model_caps_learned.json")


def _stamp(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _merge(cur: dict, new: dict) -> None:
    for k, v in new.items():
        if k == "refused":
            cur[k] = tuple(sorted(set(cur.get(k) or ()) | set(v or ())))
        else:
            cur[k] = v


def _ensure_loaded() -> None:
    """Read the persisted facts into _LEARNED (once, and again when the file changed). Never raises."""
    try:
        path = _learned_path()
        if path is None:
            return
        stamp = _stamp(path)
        if _DISK["path"] == path and _DISK["mtime"] == stamp:
            return
        from meshpool_harness import fsio
        try:
            data = fsio.read_json(path, default={})
        except fsio.CorruptFile:
            fsio.quarantine(path)
            data = {}
        changed = False
        with _LOCK:
            for host, models in (data.items() if isinstance(data, dict) else ()):
                for model, fields in (models.items() if isinstance(models, dict) else ()):
                    if not isinstance(fields, dict):
                        continue
                    ok = {k: (tuple(v) if k == "refused" and isinstance(v, list) else v)
                          for k, v in fields.items() if k in _PERSIST}
                    cur = _LEARNED.setdefault((host, model), {})
                    before = dict(cur)
                    _merge(cur, ok)
                    changed = changed or cur != before
            _DISK["path"], _DISK["mtime"] = path, stamp
            if changed:
                _CACHE.clear()
    except Exception:  # noqa: BLE001 - a damaged or unreadable file never stops a request
        pass


def _persist(host: str, bare: str, delta: dict) -> None:
    try:
        path = _learned_path()
        if path is None or not delta:
            return
        from meshpool_harness import fsio

        def fn(cur):
            cur = cur if isinstance(cur, dict) else {}
            slot = cur.setdefault(host, {}).setdefault(bare, {})
            old = tuple(slot.get("refused") or ())
            _merge(slot, delta)
            if "refused" in slot:
                slot["refused"] = sorted(set(old) | set(slot["refused"]))
            return cur
        try:
            fsio.update_json(path, fn, default={})
        except fsio.CorruptFile:
            fsio.quarantine(path)
            fsio.update_json(path, fn, default={})
        _DISK["path"], _DISK["mtime"] = path, _stamp(path)
    except Exception:  # noqa: BLE001
        pass


def first_byte_timeout(base: str, model: str, default: float) -> float:
    """Seconds to wait for the first reply byte: a local server may be loading the model (MH_LOCAL_FIRST_BYTE_S, 900),
    and a model seen to be slow gets three times its slowest first byte. At most an hour."""
    t = float(default)
    if _local(base):
        try:
            t = max(t, float(os.environ.get("MH_LOCAL_FIRST_BYTE_S") or 900))
        except ValueError:
            t = max(t, 900.0)
    _ensure_loaded()
    with _LOCK:
        fb = float((_LEARNED.get((_hostkey(base), _bare(model))) or {}).get("first_byte_s") or 0)
    if fb:
        t = max(t, 3 * fb)
    return min(t, 3600.0)


def probe_timeout(base: str, default: float) -> float:
    """Seconds for a health or model probe: a local server may be busy loading a model (MH_LOCAL_PROBE_S, 120)."""
    if not _local(base):
        return float(default)
    try:
        return max(float(default), float(os.environ.get("MH_LOCAL_PROBE_S") or 120))
    except ValueError:
        return max(float(default), 120.0)


def observe_first_byte(base: str, model: str, seconds: float) -> None:
    """Remember a slow first byte (over 5 s), decaying the old value so one bad call does not stick for good."""
    if not seconds or seconds <= 5:
        return
    _ensure_loaded()
    with _LOCK:
        prev = float((_LEARNED.get((_hostkey(base), _bare(model))) or {}).get("first_byte_s") or 0)
    new = round(max(prev * 0.8, float(seconds)), 1)
    if prev and abs(new - prev) < 0.1 * prev:
        return
    learn(base, model, first_byte_s=new)


def resolve(base: str, model: str, profile: dict | None = None) -> ModelCaps:
    bare = _bare(str(model))
    _ensure_loaded()
    key = (_hostkey(base), bare, json.dumps((profile or {}).get("caps") or {}, sort_keys=True), os.environ.get("MH_CAPS", ""),
           os.environ.get("MH_WIRE", ""))
    with _LOCK:
        hit = _CACHE.get(key)
        learned = dict(_LEARNED.get((key[0], bare), {}))
    if hit is not None and not learned:
        return hit
    c, src = ModelCaps(), {}

    def put(field_: str, value, where: str):
        setattr(c, field_, value)
        src[field_] = where

    # 1. conservative defaults (above), then the name-pattern table
    for row in _hints():
        if re.search(row["match"], bare.lower()):
            for f in ("reasoning", "parallel_tools", "reliability", "max_output"):
                if f in row:
                    put(f, row[f], "name")
            if "efforts" in row:
                put("efforts", tuple(row["efforts"]), "name")
            break
    # 2. the kind of endpoint
    h = _host(base)
    if h.endswith("anthropic.com"):
        put("wire", "messages", "host")
        put("cache", "explicit", "host")
    elif h.endswith(("openai.com", "openai.azure.com", "azure.com", "meshpool.ai", "deepseek.com")):
        put("cache", "auto", "host")
        put("cache_hints", h.endswith(("openai.com", "azure.com", "meshpool.ai", "deepseek.com")), "host")
    elif _local(base):
        put("cache", "none", "host")                 # Ollama, LM Studio, llama.cpp: no server-side prompt cache
        put("reliability", "low", "host")
    # a reasoning parameter is only sent to endpoints that define it (a local server may not)
    if c.reasoning == "effort" and _local(base):
        put("reasoning", "none", "host")
    # 3. what replies taught
    for f, v in learned.items():
        put(f, v, "learned")
    if "window" not in learned:                  # a window the replies taught beats the context module's guess
        try:
            from meshpool_harness.core import context as CX
            put("window", CX.window(model, base, "", probe_endpoint=False), "context")
        except Exception:  # noqa: BLE001
            pass
    # 4. the user: a profile's "caps" mapping, MH_CAPS (json), then the dedicated variables
    over = dict((profile or {}).get("caps") or {})
    try:
        over.update(json.loads(os.environ.get("MH_CAPS", "") or "{}"))
    except ValueError:
        pass
    if os.environ.get("MH_WIRE") in ("chat", "responses", "messages"):
        over["wire"] = os.environ["MH_WIRE"]
    for f, v in over.items():
        if hasattr(c, f) and f != "source":
            put(f, tuple(v) if f == "efforts" else v, "user")
    c.source = src
    with _LOCK:
        _CACHE[key] = c
    return c


def learn(base: str, model: str, **fields) -> None:
    """Remember what a reply showed (usage_cached, usage_reasoning, reasoning "none" after a refused parameter, a refused
    field, no_tools ...). "refused" is merged, not replaced. Persisted per endpoint+model (see _PERSIST)."""
    _ensure_loaded()
    host, bare = _hostkey(base), _bare(model)
    with _LOCK:
        d = _LEARNED.setdefault((host, bare), {})
        delta = {}
        for k, v in fields.items():
            nv = tuple(sorted(set(d.get(k) or ()) | set(v or ()))) if k == "refused" else v
            if d.get(k) != nv:
                d[k] = nv
                if k in _PERSIST:
                    delta[k] = nv
        if delta or any(k not in _PERSIST for k in fields):
            _CACHE.clear()
    if delta:
        _persist(host, bare, delta)


def observe_usage(base: str, model: str, usage: dict) -> None:
    """Note whether a reply reports cached and reasoning tokens, so budgets know which numbers to trust."""
    u = usage or {}
    cached = bool((u.get("prompt_tokens_details") or {}).get("cached_tokens") is not None or "prompt_cache_hit_tokens" in u
                  or "cache_read_input_tokens" in u)
    reasoning = bool((u.get("completion_tokens_details") or {}).get("reasoning_tokens") is not None
                     or (u.get("output_tokens_details") or {}).get("reasoning_tokens") is not None)
    learn(base, model, usage_cached=cached, usage_reasoning=reasoning)


def reset() -> None:
    with _LOCK:
        _CACHE.clear()
        _LEARNED.clear()
        _DISK["path"] = _DISK["mtime"] = None
