"""Which models a reviewer or judge may call on this endpoint: the configured one (plus explicit overrides), never a
built-in name.

A fixed fallback list ("claude-..., gpt-..., kimi-...") named models most endpoints do not deploy. On a single-deployment
endpoint every other name was a billed 404, and a reasoning model refused the `temperature` each review sent (S-03: 70 of
130 calls for one deck). Callers pass what the user chose (an env override, a rules entry, a `model` argument); the
configured main model comes last; models the endpoint is remembered to lack (core/modelcaps.py learn_missing, learned
from a 404) are dropped, and for image reviews so are models remembered as text-only. An empty list means "skip the
review and say so": nothing is called."""
from __future__ import annotations

import os
import re

DEFAULT_MODEL = "deepseek-v4.1-flash"
# Request fields a 400 may name; anything else in an error text is not ours to drop.
PARAMS = ("temperature", "top_p", "presence_penalty", "frequency_penalty", "seed", "stop", "logprobs", "top_logprobs",
          "parallel_tool_calls", "response_format", "reasoning_effort", "max_tokens", "stream_options",
          "prompt_cache_key", "user", "tool_choice")


def configured_model() -> str:
    from meshpool_harness import config as C
    return (C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or DEFAULT_MODEL).strip()


def _base() -> str:
    from meshpool_harness import providers as PV
    return PV.key_base()


def _chain(explicit, skip) -> list[str]:
    from meshpool_harness.core import modelcaps as MC
    env = [m.strip() for m in os.environ.get("MH_REVIEWER_MODELS", "").split(",")]
    base, out = _base(), []
    for m in [*explicit, *env, configured_model()]:
        m = (m or "").strip() if isinstance(m, str) else ""
        if m and m not in out and not MC.is_missing(base, m) and not skip(base, m):
            out.append(m)
    return out


def reviewer_models(*explicit) -> list[str]:
    """Ordered, de-duplicated: the explicit models, MH_REVIEWER_MODELS, then the configured model."""
    return _chain(explicit, lambda b, m: False)


def vision_reviewer_models(*explicit) -> list[str]:
    """As reviewer_models, minus models remembered as unable to read images."""
    from meshpool_harness.core import modelcaps as MC
    return _chain(explicit, MC.no_vision)


def unsupported_param(msg: str, body_keys) -> str | None:
    """The request field a 400 says the model does not take, only when it is one this request sent. Structured
    errors ("param"/"code") first, then the wording the agent already knows (agent/core.py _refused_field)."""
    keys = set(body_keys or ())
    m = msg or ""
    cands = []
    j = re.search(r"""["']param["']\s*:\s*["'](\w+)["']""", m)
    if j and re.search(r"unsupported_(?:parameter|value)|not supported|unsupported", m, re.I):
        cands.append(j.group(1))
    try:
        from meshpool_harness.agent.core import _refused_field
        cands.append(_refused_field(m))
    except Exception:  # noqa: BLE001 - agent module unavailable: use the patterns below only
        pass
    for f in PARAMS:
        if re.search(r"(?:unsupported (?:parameter|value)|not support\w*|unknown parameter)[^.\n]{0,80}\W" + f + r"\W|"
                     r"\W" + f + r"\W[^.\n]{0,60}(?:is |are )?(?:not supported|unsupported|does not support)", m, re.I):
            cands.append(f)
    for c in cands:
        if c in PARAMS and c in keys:
            return c
    return None
