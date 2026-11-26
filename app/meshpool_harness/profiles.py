"""Model profiles: data files in profiles/<family>.yaml, chosen by model id, overridable by flag."""
from __future__ import annotations

from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parents[1] / "profiles"


def _yaml(raw: bytes):
    return yaml.safe_load(raw.decode("utf-8", "replace"))


def load_all() -> dict:
    """Every profile by name. for_model runs on every model request (the request body, the client, the context
    window, model capabilities), and parsing the YAML files each time was most of its cost: each file's parse is kept
    while the file is unchanged (fsio.parse_cached). Callers get their own copy."""
    import copy

    from meshpool_harness import fsio
    return {p.stem: copy.deepcopy(fsio.parse_cached(p, _yaml, "profile-yaml")) for p in DIR.glob("*.yaml")}


def for_model(model: str, override: str | None = None) -> dict:
    allp = load_all()
    if override and override in allp:        # MH_PROFILE can also name a harness profile (hrouter/profiles.py): ignored here
        return allp[override]
    for name, p in allp.items():
        if any(m in model for m in p.get("match", [])):
            return p
    return allp.get("default") or allp["deepseek"]       # unknown model: provider-default sampling, not greedy


def apply(profile: dict, messages: list, body: dict, role: str = "worker") -> tuple[list, dict]:
    """Return (messages, extra body) adjusted for the family: caching markers, sampling, output param."""
    s = (profile.get("sampling") or {}).get(role) or {}
    if "temperature" in s:
        if s["temperature"] is None:
            body.pop("temperature", None)
        else:
            body["temperature"] = s["temperature"]
    lim = profile.get("limits") or {}
    if lim.get("param") == "max_completion_tokens" and "max_tokens" in body:
        body["max_completion_tokens"] = body.pop("max_tokens")
    if (profile.get("caching") or {}).get("mode") == "explicit_breakpoints" and messages and messages[0]["role"] == "system":
        sysm = messages[0]
        text = sysm["content"] if isinstance(sysm["content"], str) else sysm["content"][0]["text"]
        messages = [{"role": "system", "content": [{"type": "text", "text": text,
                                                     "cache_control": {"type": "ephemeral"}}]}] + messages[1:]
    return messages, body
