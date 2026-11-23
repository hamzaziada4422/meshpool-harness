"""Inference providers: where a model runs. Picked per thread in the model picker, next to the harness (which agent
runs).

A model id carries its provider as a prefix; an id without one runs on the API key endpoint in settings (the
Meshpool endpoint, or another OpenAI-compatible endpoint the user set):

  deepseek-v4.1-flash          key        the Meshpool key (or the custom endpoint) in settings
  chatgpt:gpt-6.1-sol          chatgpt    the user's ChatGPT sign-in (core/chatgpt.py)
"""
from __future__ import annotations

import base64
import os
import re
from urllib.parse import quote, urlparse

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
    env = os.environ.get(k)
    if env and k not in C._INJECTED:                  # a value the shell set wins over the saved file
        return env.strip()
    return (C.read().get(k) or env or "").strip()


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _openai_pair() -> tuple[str, str]:
    """(key, base URL) from OPENAI_API_KEY and OPENAI_BASE_URL, only when there is no Meshpool key and both are set:
    an OpenAI key alone must never be sent to the Meshpool endpoint."""
    if _cfg("MESHPOOL_API_KEY"):
        return "", ""
    k, b = os.environ.get("OPENAI_API_KEY", "").strip(), os.environ.get("OPENAI_BASE_URL", "").strip()
    return (k, b) if k and b else ("", "")


def key_base() -> str:
    return (_openai_pair()[1] or _cfg("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")


def key() -> str:
    return _cfg("MESHPOOL_API_KEY") or _openai_pair()[0]


def key_wire() -> str:
    """The wire the key endpoint speaks: "openai" (chat completions) or "anthropic" (Messages). MH_API_WIRE when set
    (provider_setup saves it); otherwise guessed from the host, so settings saved by older versions keep working."""
    w = _cfg("MH_API_WIRE").lower()
    if w in ("openai", "anthropic"):
        return w
    return "anthropic" if host(key_base()).endswith("anthropic.com") else "openai"


# ---------- how a request to the key endpoint is addressed and authorised ---------------------------------------------
# One place decides the URL and the headers for every call to the key endpoint (chat, model list, probes), so Azure
# OpenAI, Foundry, Anthropic-style and custom-header endpoints work the same way everywhere.
AZURE_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com")
AZURE_API_VERSION = "2024-10-21"             # the current GA data-plane version of Azure OpenAI chat completions
AZURE_MODELS_API_VERSION = "2024-05-01-preview"   # the Foundry /models (model inference) route
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
SCHEMES = ("", "bearer", "basic", "token", "raw")


def keyless_ok(base: str | None = None) -> bool:
    """A local or LAN server (localhost, 127.x, 10.x, 192.168.x, 172.16-31.x, *.local, *.lan, a bare host name) may be
    used with no key at all."""
    h = host(base if base is not None else key_base())
    if not h:
        return False
    if h in ("localhost", "0.0.0.0", "::1") or h.endswith((".local", ".lan", ".localhost", ".internal")):
        return True
    m = re.match(r"^(\d+)\.(\d+)\.\d+\.\d+$", h)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return a in (10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254)
    return "." not in h and ":" not in h


def is_azure(base: str | None = None) -> bool:
    h = host(base if base is not None else key_base())
    return h.endswith(AZURE_SUFFIXES)


def azure_version(base: str = "") -> str:
    """The api-version to send: MESHPOOL_AZURE_API_VERSION (settings or environment), else the one pasted in the base
    URL's query, else the current GA version."""
    v = _cfg("MESHPOOL_AZURE_API_VERSION")
    if not v and base and "api-version=" in base:
        m = re.search(r"api-version=([^&#\s]+)", base)
        v = m.group(1) if m else ""
    return v or AZURE_API_VERSION


def azure_deployment(base: str) -> str:
    """The deployment named in an address like https://RES.openai.azure.com/openai/deployments/DEP, else ""."""
    m = re.match(r"^/openai/deployments/([^/]+)", urlparse(base).path)
    return m.group(1) if m else ""


def looks_jwt(k: str) -> bool:
    return k.count(".") == 2 and k.startswith("eyJ")


def auth_headers(k: str, base: str | None = None, wire: str | None = None, header: str | None = None,
                 scheme: str | None = None) -> dict:
    """The authentication header(s) for the key endpoint. No key, no header (a local or LAN server). A custom header
    name and scheme (MH_AUTH_HEADER, MH_AUTH_SCHEME: bearer, basic, token or raw) win; else Anthropic-style endpoints
    send x-api-key with anthropic-version, Azure sends api-key (or Authorization: Bearer for an Entra token) and
    everything else Authorization: Bearer. The key is sent exactly as saved."""
    k = k or ""
    base = base if base is not None else key_base()
    wire = wire or key_wire()
    header = (_cfg("MH_AUTH_HEADER") if header is None else header).strip()
    scheme = (_cfg("MH_AUTH_SCHEME") if scheme is None else scheme).strip().lower()
    if header and not _HEADER_NAME.match(header):
        header = ""
    if scheme not in SCHEMES:
        scheme = ""
    out = {}
    if wire == "anthropic":
        from meshpool_harness.core import anthropic_wire as AW
        out["anthropic-version"] = AW.VERSION
    if not k:
        return out
    if not header and not scheme:
        if wire == "anthropic":
            header, scheme = "x-api-key", "raw"
        elif is_azure(base) and not looks_jwt(k):
            header, scheme = "api-key", "raw"
        else:
            header, scheme = "Authorization", "bearer"
    header = header or "Authorization"
    if not scheme:
        scheme = "bearer" if header.lower() == "authorization" else "raw"
    if scheme == "bearer":
        v = f"Bearer {k}"
    elif scheme == "token":
        v = f"Token {k}"
    elif scheme == "basic":
        v = "Basic " + (base64.b64encode(k.encode("utf-8")).decode() if ":" in k else k)
    else:
        v = k
    out[header] = v
    return out


def chat_url(base: str, model: str = "", path: str = "chat/completions", version: str = "") -> str:
    """The URL of a call. Azure OpenAI puts the deployment (the model name) in the path and an api-version in the
    query; the /openai/v1 route and every other endpoint use {base}/{path}."""
    base = (base or "").rstrip("/")
    if "?" in base:
        base = base.split("?", 1)[0].rstrip("/")
    if not is_azure(base):
        return f"{base}/{path}"
    u = urlparse(base)
    origin, p = f"{u.scheme}://{u.netloc}", u.path.rstrip("/")
    if p.startswith("/openai/v1"):
        v = version or _cfg("MESHPOOL_AZURE_API_VERSION")
        return f"{origin}{p}/{path}" + (f"?api-version={quote(v)}" if v else "")
    if p.startswith("/models"):
        return f"{origin}{p}/{path}?api-version={quote(version or _cfg('MESHPOOL_AZURE_API_VERSION') or AZURE_MODELS_API_VERSION)}"
    dep = azure_deployment(base) or model
    if path in ("models", "info") or not dep:
        return f"{origin}/openai/{path}?api-version={quote(version or azure_version())}"
    return f"{origin}/openai/deployments/{quote(dep, safe='')}/{path}?api-version={quote(version or azure_version())}"


def adapt_body(base: str, body: dict) -> dict:
    """Azure's chat completions take max_completion_tokens (every current model, the reasoning ones only that)."""
    if is_azure(base) and "max_tokens" in body and "max_completion_tokens" not in body:
        body = dict(body)
        body["max_completion_tokens"] = body.pop("max_tokens")
    return body


def chat_target(base: str, key_: str, model: str, path: str = "chat/completions") -> tuple[str, dict]:
    """(URL, auth headers) for a call to `base`. The saved custom header and scheme apply to the saved endpoint only:
    the local ChatGPT relay or another host gets the standard form for its kind."""
    mine = base.rstrip("/") == key_base()
    h = auth_headers(key_, base, key_wire() if mine else "openai", None if mine else "", None if mine else "")
    return chat_url(base, model, path), h


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
    return "key", m


def join(prov: str, model: str) -> str:
    return model if prov == "key" else f"{prov}:{model}"


def ready(prov: str) -> bool:
    from meshpool_harness import harnesses as HR
    return {"key": lambda: bool(key()) or keyless_ok(),
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
