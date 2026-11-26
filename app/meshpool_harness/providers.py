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
from meshpool_harness import fsio

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


def _settings(raw: bytes) -> dict:
    return C._parse(C.decode_settings(raw)[0].replace("\ufeff", ""))


def _read() -> dict:
    """The settings file, parsed once while it is unchanged. One model call or relay request asks for the base, key,
    kind and api-version, and each of those read and parsed the whole file again, some more than once. The value is
    shared: callers must not change it."""
    return fsio.parse_cached(C.config_path(), _settings, "settings", missing={})


def _cfg(k: str) -> str:
    env = os.environ.get(k)
    if env and k not in C._INJECTED:                  # a value the shell set wins over the saved file
        return env.strip()
    return (_read().get(k) or env or "").strip()


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


DEFAULT = "https://llm.meshpool.ai/v1"


def _shell(k: str) -> str:
    """The value the user's own shell gave (never one Meshpool copied in from the settings file)."""
    if k in C._INJECTED:
        return (C.SHELL.get(k) or "").strip()
    return (os.environ.get(k) or "").strip()


def _file(k: str) -> str:
    env = os.environ.get(k) if k in C._INJECTED else ""
    return (_read().get(k) or env or "").strip()


def endpoint_pair() -> tuple[str, str, str]:
    """(base URL, key, source): the key endpoint decided once, so a key is never paired with an address that came from
    somewhere else. source is "environment" (the shell's key, with the shell's base or the default), "settings" (the
    saved file's key, with its base or the default) or "none" (no key anywhere: an explicit base, else the default)."""
    sk = _shell("MESHPOOL_API_KEY")
    if sk:
        return (_shell("MESHPOOL_BASE_URL") or DEFAULT).rstrip("/"), sk, "environment"
    fk = _file("MESHPOOL_API_KEY")
    if fk:
        return (_file("MESHPOOL_BASE_URL") or DEFAULT).rstrip("/"), fk, "settings"
    ek, eb, _esrc = _env_pair()                    # OPENAI_API_KEY+OPENAI_BASE_URL, or the AZURE_OPENAI_* pair: each
    if ek:                                         # key only with the address set next to it, never with another
        return eb.rstrip("/"), ek, "environment"
    return (_shell("MESHPOOL_BASE_URL") or _file("MESHPOOL_BASE_URL") or DEFAULT).rstrip("/"), "", "none"


def _ecfg(k: str) -> str:
    """A per-endpoint setting (wire, auth header, scheme, Azure version) from the same place the key came from."""
    src = endpoint_pair()[2]
    if src == "environment":
        return _shell(k)
    if src == "settings":
        return _file(k)
    return _cfg(k)


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
        return k, EP.parse_pasted(e, "azure-classic",
                                  os.environ.get("AZURE_OPENAI_API_VERSION", "")).base
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
    return endpoint_pair()[0]


def key() -> str:
    return endpoint_pair()[1]


def key_kind() -> str:
    """The kind of provider the key endpoint is (core/endpoint.py CAPS): what the user saved, else read from the
    address. A kind saved for one address is ignored when the address comes from the environment."""
    from meshpool_harness.core import endpoint as EP
    src = _env_pair()[2]
    saved = "" if src else _ecfg("MH_API_KIND")
    if src == "azure":
        try:
            return EP.parse_pasted(os.environ.get("AZURE_OPENAI_ENDPOINT", ""), "azure-classic",
                                   os.environ.get("AZURE_OPENAI_API_VERSION", "")).kind
        except EP.EndpointError:
            pass
    return saved if saved in EP.CAPS else EP.detect(key_base())


def key_api_version() -> str:
    """The api-version the user chose for an Azure endpoint ("" = the kind's default)."""
    if _env_pair()[2] == "azure":
        return os.environ.get("AZURE_OPENAI_API_VERSION", "").strip()
    return "" if _env_pair()[2] else _ecfg("MH_API_VERSION")


def env_deployment() -> str:
    """AZURE_OPENAI_DEPLOYMENT when the Azure environment pair is what is in use: the model to run."""
    return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip() if _env_pair()[2] == "azure" else ""


def key_wire() -> str:
    """The wire the key endpoint speaks: "openai" (chat completions) or "anthropic" (Messages). MH_API_WIRE when set
    (provider_setup saves it); otherwise guessed from the kind, so settings saved by older versions keep working."""
    from meshpool_harness.core import endpoint as EP
    w = _ecfg("MH_API_WIRE").lower()
    if w in ("openai", "anthropic"):
        return w
    return "anthropic" if host(key_base()).endswith("anthropic.com") or EP.wire_of(key_kind()) == "anthropic" \
        else "openai"


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
    v = _ecfg("MESHPOOL_AZURE_API_VERSION")
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
    header = (_ecfg("MH_AUTH_HEADER") if header is None else header).strip()
    scheme = (_ecfg("MH_AUTH_SCHEME") if scheme is None else scheme).strip().lower()
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
        v = version or _ecfg("MESHPOOL_AZURE_API_VERSION")
        return f"{origin}{p}/{path}" + (f"?api-version={quote(v)}" if v else "")
    if p.startswith("/models"):
        return f"{origin}{p}/{path}?api-version={quote(version or _ecfg('MESHPOOL_AZURE_API_VERSION') or AZURE_MODELS_API_VERSION)}"
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
    mine = is_key_endpoint(base)
    h = auth_headers(key_, base, wire_for(base), None if mine else "", None if mine else "")
    return chat_url(base, model, path), h


def _norm_base(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


def is_key_endpoint(base: str) -> bool:
    """Whether `base` is the key endpoint: the one in use now, or the saved or environment one (they can differ
    when the shell overrides the saved address)."""
    b = _norm_base(base)
    return bool(b) and b in {_norm_base(key_base()), _norm_base(_read().get("MESHPOOL_BASE_URL", "")),
                             _norm_base(os.environ.get("MESHPOOL_BASE_URL", "")),
                             _norm_base(os.environ.get("OPENAI_BASE_URL", ""))}


def wire_for(base: str) -> str:
    """The wire to speak to `base`, decided from the address the request really goes to: the key endpoint's wire
    for the key endpoint (whatever spelling of its address), else Anthropic for an anthropic.com host, else OpenAI."""
    if is_key_endpoint(base):
        return key_wire()
    return "anthropic" if host(base).endswith("anthropic.com") else "openai"


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
    if hid == "codex" and kind in ("azure-foundry", "azure-anthropic"):
        return ("Codex needs the OpenAI Responses API, which this Azure address does not serve. Use an "
                "https://NAME.openai.azure.com address or the /openai/v1 address for Codex, or another agent here.")
    return ""


def name(prov: str) -> str:
    return {"key": key_name(), "chatgpt": "ChatGPT subscription"}[prov]


DEFAULT_MODEL = "deepseek-v4.1-flash"


def configured_model(explicit: str | None = None) -> str:
    """The one resolver for every engine: the explicit model, else MH_MODEL (environment, then saved settings), else the default."""
    saved = ""
    if not ((explicit or "").strip() or os.environ.get("MH_MODEL")):
        try:
            saved = (_read().get("MH_MODEL") or "").strip()
        except OSError:                          # an unreadable settings file must not stop an engine
            saved = ""
    return (explicit or "").strip() or (os.environ.get("MH_MODEL") or "").strip() or saved or DEFAULT_MODEL


RETIRED = ("claude", "anthropic", "google", "gemini")   # providers of older builds; their saved model ids mean nothing now


def split(model: str | None) -> tuple[str, str]:
    """(provider, model id the provider knows) for a model id from the picker or settings."""
    m = (model or "").strip()
    head, sep, rest = m.partition(":")
    if sep and head in PREFIXED:
        return head, rest
    if sep and head in RETIRED:                  # a model saved for a provider Meshpool no longer has: use the default
        m = DEFAULT_MODEL
    if (os.environ.get("MH_PROVIDER") or _read().get("MH_PROVIDER")) == "chatgpt" and not key():
        return "chatgpt", m                      # set up with only a ChatGPT sign-in
    if m in ("", DEFAULT_MODEL) and env_deployment():
        m = env_deployment()                     # Azure from the environment: the deployment is the model
    return "key", m


def join(prov: str, model: str) -> str:
    return model if prov == "key" else f"{prov}:{model}"


def ready(prov: str) -> bool:
    from meshpool_harness import harnesses as HR
    return {"key": lambda: bool(key()) or keyless_ok(),
            "chatgpt": lambda: HR.signed_in("codex")}[prov]()


def setup(prov: str) -> str:
    return {"key": f"Add your {key_name()} key: Settings, Account.",
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
            CG.forget_models()
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


def custom_auth(hdrs: dict, base: str, key_: str) -> dict:
    """`hdrs` (the standard headers for the endpoint's kind) with the saved custom key header and scheme applied, when
    the user set one for this endpoint (MH_AUTH_HEADER, MH_AUTH_SCHEME). Otherwise `hdrs` unchanged."""
    if not key_ or not is_key_endpoint(base) or not (_ecfg("MH_AUTH_HEADER") or _ecfg("MH_AUTH_SCHEME")):
        return hdrs
    out = {k: v for k, v in hdrs.items() if k.lower() not in ("authorization", "api-key", "x-api-key")}
    out.update(auth_headers(key_, base, "openai"))
    return out
