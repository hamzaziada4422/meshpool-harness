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


def _local(base: str) -> bool:
    from meshpool_harness.core import context as CX
    return CX._is_local(base or "")


def resolve(base: str, model: str, profile: dict | None = None) -> ModelCaps:
    bare = (model or "").split(":", 1)[-1] if str(model).startswith(("chatgpt:", "meshpool:")) else (model or "")
    key = (_host(base), bare, json.dumps((profile or {}).get("caps") or {}, sort_keys=True), os.environ.get("MH_CAPS", ""),
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
    h = key[0]
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
    """Remember what a reply showed (usage_cached, usage_reasoning, reasoning "none" after a refused parameter)."""
    bare = (model or "").split(":", 1)[-1]
    with _LOCK:
        d = _LEARNED.setdefault((_host(base), bare), {})
        changed = any(d.get(k) != v for k, v in fields.items())
        d.update(fields)
        if changed:
            _CACHE.clear()


def observe_usage(base: str, model: str, usage: dict) -> None:
    """Note whether a reply reports cached and reasoning tokens, so budgets know which numbers to trust."""
    u = usage or {}
    cached = bool((u.get("prompt_tokens_details") or {}).get("cached_tokens") is not None or "prompt_cache_hit_tokens" in u
                  or "cache_read_input_tokens" in u)
    reasoning = bool((u.get("completion_tokens_details") or {}).get("reasoning_tokens") is not None
                     or (u.get("output_tokens_details") or {}).get("reasoning_tokens") is not None)
    learn(base, model, usage_cached=cached, usage_reasoning=reasoning)


# What an endpoint refused for a model, kept on disk so engine subprocesses and later runs know it too: "this model is not
# deployed here" (a 404), "this parameter is not supported" (a 400) and "this model cannot read images". A fixed list of
# reviewer models and a retry with the same parameters used to cost one billed failed call per request (S-03).
_MEM_TTL_S = 24 * 3600.0                     # a model deployed (or a setting changed) later is tried again after a day
_MEM: dict = {"path": None, "mtime": None, "data": {}}
_FALLBACK: dict = {}                         # marks that could not be written to disk


def _ttl() -> float:
    try:
        return float(os.environ.get("MH_MODEL_MEMORY_TTL_S", "") or _MEM_TTL_S)
    except ValueError:
        return _MEM_TTL_S


def _mem_path():
    from meshpool_harness import config as C            # the data folder (MESHPOOL_HOME, else the install's own)
    return C.home() / "cache" / "endpoint_models.json"


def endpoint_key(base: str, model: str) -> tuple[str, str]:
    """(endpoint, bare model id): a provider-prefixed model id ("chatgpt:...") is its provider's endpoint, any other
    model lives on the key endpoint (host and port, so two local servers stay apart)."""
    from meshpool_harness import providers as PV
    prov, bare = PV.split(model)
    if prov != "key":
        return prov, bare
    try:
        net = urlparse(base or PV.key_base()).netloc.lower()
    except ValueError:
        net = ""
    return net or "key", bare


def _mem_read() -> dict:
    """The memory file, re-read when another process changed it (a mark made by an engine subprocess shows within
    seconds). A damaged or unreadable file counts as empty; it is overwritten by the next mark."""
    try:
        p = _mem_path()
        mt = p.stat().st_mtime_ns if p.exists() else None
    except OSError:
        return _MEM["data"] if _MEM["path"] else {}
    if _MEM["path"] == str(p) and _MEM["mtime"] == mt:
        return _MEM["data"]
    data: dict = {}
    if mt is not None:
        try:
            from meshpool_harness import fsio
            d = fsio.read_json(p, default={})
            data = d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001 - CorruptFile or unreadable
            data = {}
    _MEM.update(path=str(p), mtime=mt, data=data)
    return data


def _mem_mark(base: str, model: str, kind: str, param: str = "") -> None:
    """Record one fact (kind: "missing" | "no_vision" | "refused" with a param) with the time. Never raises."""
    import time
    ep, bare = endpoint_key(base, model)
    now = time.time()

    def put(cur):
        cur = cur if isinstance(cur, dict) else {}
        row = cur.setdefault(ep, {}).setdefault(bare, {})
        if kind == "refused":
            row.setdefault("refused", {})[param] = now
        else:
            row[kind] = now
        for e in list(cur):                             # drop expired rows so the file stays small
            for m in list(cur[e]):
                r = cur[e][m]
                r2 = {k: v for k, v in r.items() if k != "refused" and now - v < _ttl()}
                ref = {k: v for k, v in (r.get("refused") or {}).items() if now - v < _ttl()}
                if ref:
                    r2["refused"] = ref
                if r2:
                    cur[e][m] = r2
                else:
                    del cur[e][m]
            if not cur[e]:
                del cur[e]
        return cur
    try:
        from meshpool_harness import fsio
        try:
            data = fsio.update_json(_mem_path(), put, default={})
        except fsio.CorruptFile:                        # damaged: start over (the facts are cheap to learn again)
            data = put({})
            fsio.atomic_write_json(_mem_path(), data)
        _MEM.update(path=str(_mem_path()), mtime=_mem_path().stat().st_mtime_ns, data=data)
    except Exception:  # noqa: BLE001 - a read-only home must not break a model call
        put(_FALLBACK)                                  # this process still remembers it


def _mem_get(base: str, model: str, kind: str, param: str = "") -> bool:
    import time
    ep, bare = endpoint_key(base, model)
    for src in (_mem_read(), _FALLBACK):
        row = (src.get(ep) or {}).get(bare) or {}
        ts = (row.get("refused") or {}).get(param) if kind == "refused" else row.get(kind)
        if isinstance(ts, (int, float)) and time.time() - ts < _ttl():
            return True
    return False


def learn_missing(base: str, model: str) -> None:
    """The endpoint answered that this model is not deployed (404 DeploymentNotFound, "model does not exist")."""
    _mem_mark(base, model, "missing")


def is_missing(base: str, model: str) -> bool:
    return _mem_get(base, model, "missing")


def learn_refused(base: str, model: str, param: str) -> None:
    """The endpoint refused this request field for this model (400 "Unsupported parameter: 'temperature'")."""
    _mem_mark(base, model, "refused", param)


def refused(base: str, model: str) -> set:
    import time
    ep, bare = endpoint_key(base, model)
    out = set()
    for src in (_mem_read(), _FALLBACK):
        row = ((src.get(ep) or {}).get(bare) or {}).get("refused") or {}
        out |= {k for k, ts in row.items() if isinstance(ts, (int, float)) and time.time() - ts < _ttl()}
    return out


def learn_no_vision(base: str, model: str) -> None:
    _mem_mark(base, model, "no_vision")


def no_vision(base: str, model: str) -> bool:
    return _mem_get(base, model, "no_vision")


def reset() -> None:
    with _LOCK:
        _CACHE.clear()
        _LEARNED.clear()
    _MEM.update(path=None, mtime=None, data={})
    _FALLBACK.clear()
