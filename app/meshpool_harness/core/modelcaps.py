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
    keep_reasoning: bool = False                     # send reasoning_content back inside a tool-call turn (DeepSeek, Kimi thinking)
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
            for f in ("reasoning", "parallel_tools", "reliability", "max_output", "keep_reasoning"):
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


def reset() -> None:
    with _LOCK:
        _CACHE.clear()
        _LEARNED.clear()
