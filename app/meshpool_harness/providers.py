"""Inference providers: where a model runs. Picked per thread in the model picker, next to the harness (which agent
runs).

A model id carries its provider as a prefix; an id without one runs on the API key endpoint in settings (the
Meshpool endpoint, or another OpenAI-compatible endpoint the user set):

  deepseek-v4.1-flash          key        the Meshpool key (or the custom endpoint) in settings
  chatgpt:gpt-6.1-sol          chatgpt    the user's ChatGPT sign-in (core/chatgpt.py)
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from meshpool_harness import config as C

PREFIXED = ("chatgpt",)
PROVIDERS = ("key", "chatgpt")

# Which providers each built-in harness can run on, in order of preference.
SUPPORTS = {
    "meshpool-code": ["key", "chatgpt"],
    "meshpool-work": ["key", "chatgpt"],
    "meshpool-auto": ["key", "chatgpt"],
    "pi": ["key", "chatgpt"],         # ChatGPT through the local relay (chatgpt_relay.py)
    "hermes": ["key", "chatgpt"],
    "codex": ["key", "chatgpt"],
    "opencode": ["key", "chatgpt"],   # ChatGPT through the local relay, like Pi and Hermes
}


def _cfg(k: str) -> str:
    return (C.read().get(k) or os.environ.get(k) or "").strip()


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _openai_pair() -> tuple[str, str]:
    """(key, base URL) from OPENAI_API_KEY and OPENAI_BASE_URL, only when there is no Meshpool key and both are set:
    an OpenAI key alone must never be sent to the Meshpool endpoint."""
    if _cfg("MESHPOOL_API_KEY"):
        return "", ""
    k, b = os.environ.get("OPENAI_API_KEY", "").strip(), os.environ.get("OPENAI_BASE_URL", "").strip()
    return (k, b) if k and b else ("", "")


def _azure_pair() -> tuple[str, str]:
    """(key, base address) from AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT, with the same rule: no Meshpool key,
    and both set. The endpoint is split the way a pasted address is (a full call address works too)."""
    if _cfg("MESHPOOL_API_KEY"):
        return "", ""
    k, e = os.environ.get("AZURE_OPENAI_API_KEY", "").strip(), os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    if not (k and e):
        return "", ""
    from meshpool_harness.core import endpoint as EP
    try:
        return k, EP.parse_pasted(e, "azure-classic" if "azure" not in e.lower() else "").base
    except EP.EndpointError:
        return "", ""


def _env_pair() -> tuple[str, str, str]:
    """(key, base, source) from the environment when no Meshpool key is saved. Order: the OpenAI pair, then the Azure
    pair; each key goes only with the address set next to it, never with another."""
    k, b = _openai_pair()
    if k:
        return k, b, "openai"
    k, b = _azure_pair()
    return (k, b, "azure") if k else ("", "", "")


def key_base() -> str:
    return (_env_pair()[1] or _cfg("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")


def key() -> str:
    return _cfg("MESHPOOL_API_KEY") or _env_pair()[0]


def key_kind() -> str:
    """The kind of provider the key endpoint is (core/endpoint.py CAPS): what the user saved, else read from the
    address. A kind saved for one address is ignored when the address comes from the environment."""
    from meshpool_harness.core import endpoint as EP
    src = _env_pair()[2]
    saved = "" if src else _cfg("MH_API_KIND")
    return saved if saved in EP.CAPS else EP.detect(key_base())


def key_api_version() -> str:
    """The api-version the user chose for an Azure endpoint ("" = the kind's default)."""
    if _env_pair()[2] == "azure":
        return os.environ.get("AZURE_OPENAI_API_VERSION", "").strip()
    return "" if _env_pair()[2] else _cfg("MH_API_VERSION")


def env_deployment() -> str:
    """AZURE_OPENAI_DEPLOYMENT when the Azure environment pair is what is in use: the model to run."""
    return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip() if _env_pair()[2] == "azure" else ""


def key_wire() -> str:
    """The wire the key endpoint speaks: "openai" (chat completions) or "anthropic" (Messages). MH_API_WIRE when set
    (provider_setup saves it); otherwise guessed from the kind, so settings saved by older versions keep working."""
    from meshpool_harness.core import endpoint as EP
    w = _cfg("MH_API_WIRE").lower()
    if w in ("openai", "anthropic"):
        return w
    return "anthropic" if host(key_base()).endswith("anthropic.com") or EP.wire_of(key_kind()) == "anthropic" \
        else "openai"


def key_problem(k: str | None) -> str:
    """Why a key cannot work, in words the user can act on; "" when it looks usable. A key copied from a dashboard
    that masks it ("sk-43Ux7•••••") carries characters no HTTP header can hold, and each agent then failed with its
    own cryptic message ('latin-1' codec..., ByteString..., invalid header value)."""
    k = (k or "").strip()
    if not k:
        return ""
    bad = sorted({ch for ch in k if ord(ch) > 126 or ord(ch) < 33})
    if not bad:
        return ""
    if set(bad) & set("\u2022\u25cf\u2026\u00b7*"):
        return ("The API key looks masked (it contains \u2022 or \u2026), so it was copied as shown, not the key "
                "itself. Copy the full key with your provider's Copy button, then paste it in Settings, Endpoint and "
                "key.")
    return ("The API key contains characters a key never has (" + " ".join(repr(c) for c in bad[:4]) + "). Paste it "
            "again in Settings, Endpoint and key, straight from your provider.")


def key_name() -> str:
    h = host(key_base())
    return "Meshpool" if "meshpool" in h else (h or "Your endpoint")


def agent_endpoint(prov: str = "key") -> tuple[str, str]:
    """(base URL, key) for an agent process Meshpool starts (Pi, Hermes, OpenCode). They all send a plain Bearer key
    to {base}/chat/completions. Where the key endpoint is not that (Azure wants an api-key header and a deployment
    path), they get this process's local relay instead, which rebuilds the call (core/key_relay.py) and is the only
    thing that ever holds the real key."""
    base, k = endpoint(prov)
    if prov != "key":
        return base, k
    from meshpool_harness.core import endpoint as EP
    if k and EP.caps(key_kind())["relay"] and EP.wire_of(key_kind()) == "openai":
        from meshpool_harness.core import key_relay
        return key_relay.ensure()
    return base, k


def direct_agent_ok(hid: str) -> str:
    """"" when the agent can run on the key endpoint as set up; otherwise why not, in words for the settings page."""
    from meshpool_harness.core import endpoint as EP
    kind = key_kind()
    if hid == "codex" and EP.is_azure(kind) and kind != "azure-v1":
        return ("Codex talks to the Responses API, which Meshpool can only reach directly on the Azure v1 address "
                "(https://NAME.openai.azure.com/openai/v1). Use that address, or another agent for this endpoint.")
    return ""


def name(prov: str) -> str:
    return {"key": key_name(), "chatgpt": "ChatGPT subscription"}[prov]


DEFAULT_MODEL = "deepseek-v4.1-flash"
RETIRED = ("claude", "anthropic", "google", "gemini")   # providers of older builds; their saved model ids mean nothing now


def split(model: str | None) -> tuple[str, str]:
    """(provider, model id the provider knows) for a model id from the picker or settings."""
    m = (model or "").strip()
    head, sep, rest = m.partition(":")
    if sep and head in PREFIXED:
        return head, rest
    if sep and head in RETIRED:                  # a model saved for a provider Meshpool no longer has: use the default
        m = DEFAULT_MODEL
    if (os.environ.get("MH_PROVIDER") or C.read().get("MH_PROVIDER")) == "chatgpt" and not key():
        return "chatgpt", m                      # set up with only a ChatGPT sign-in
    if m in ("", DEFAULT_MODEL) and env_deployment():
        m = env_deployment()                     # Azure from the environment: the deployment is the model
    return "key", m


def join(prov: str, model: str) -> str:
    return model if prov == "key" else f"{prov}:{model}"


def ready(prov: str) -> bool:
    from meshpool_harness import harnesses as HR
    return {"key": lambda: bool(key()),
            "chatgpt": lambda: HR.signed_in("codex")}[prov]()


def setup(prov: str) -> str:
    return {"key": "Add your Meshpool key: Settings, Account.",
            "chatgpt": "Sign in with ChatGPT: Settings, Account."}[prov]


def endpoint(prov: str) -> tuple[str, str]:
    """(base URL, key) of an OpenAI-compatible provider. ChatGPT: this process's local relay, for agents that speak
    chat completions; the sign-in itself never leaves Meshpool."""
    if prov == "chatgpt":
        from meshpool_harness.core import chatgpt_relay
        return chatgpt_relay.ensure()
    return key_base(), key()


def for_harness(hid: str, model: str | None = None) -> str | None:
    """The provider a harness runs on: the model's provider when the harness supports it and it is ready, else the
    first ready one it supports (the provider chosen at sign-up first)."""
    sup = SUPPORTS.get(hid)
    if not sup:
        return None
    if model:
        p, _ = split(model)
        if p in sup and ready(p):
            return p
    chosen = default_provider()
    order = ([chosen] if chosen in sup else []) + sup
    return next((p for p in order if ready(p)), None)


def default_provider() -> str:
    """The provider of the default model (the sign-up choice): key or chatgpt."""
    p = _cfg("MH_PROVIDER") or "meshpool"
    return "chatgpt" if p == "chatgpt" else "key"


# ---------- model lists for the picker ----------------------------------------------------------------------------

def list_models(prov: str, refresh: bool = False) -> tuple[list[str], str]:
    """(model ids without prefix, error) for a provider other than the key endpoint (workbench lists that one)."""
    if prov == "chatgpt":
        from meshpool_harness.core import chatgpt as CG
        if refresh:
            CG._MODELS.update(t=0.0)
        ids = CG.models()
        d = CG.default_model()
        note = CG.limit_note() or ("" if ids else "Showing the default model; the plan's list did not load.")
        return list(dict.fromkeys([d] + ids)), note
    return [], ""


def groups(key_rows: list[dict], key_error: str, refresh: bool = False) -> list[dict]:
    """The model picker's sections, one per provider, ready or not."""
    out = []
    for prov in PROVIDERS:
        ok = ready(prov)
        g = {"id": prov, "name": name(prov), "ready": ok, "setup": "" if ok else setup(prov),
             "models": [], "error": ""}
        if prov == "key":
            g["models"], g["error"] = (key_rows, key_error) if ok else ([], "")
        elif ok:
            ids, err = list_models(prov, refresh)
            g["models"] = [{"id": join(prov, i), "name": i, "provider": "OpenAI", "caps": ["vision"]} for i in ids]
            g["error"] = err
        out.append(g)
    return out
