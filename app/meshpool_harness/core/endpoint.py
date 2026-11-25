"""One place that turns "this provider, this key, this model" into a URL, headers and a request body.

Every call that reaches a model endpoint (the agent loop, SimpleLedger, the test button, the model list, the agents
Meshpool launches through a local relay) builds its request here, so a provider's rules (which header carries the
key, where the model name goes, which query string is needed, which fields a model refuses) live in one table.

The pieces are pure functions of strings, so they are tested without a network and without real host names:

  detect(url)              kind of provider for an address ("azure-classic", "groq", "generic", ...)
  parse_pasted(url)        split whatever the user pasted into base, kind, deployment and api-version
  CAPS                     the capability table: auth style, wire, token field, model list, defaults
  build(kind, base, ...)   (url, headers, body, is_anthropic) for one chat call
  adapt(kind, body)        the body fields a kind or model needs or refuses
  explain(status, ...)     a 401/403/404/429/5xx in plain words, never a raw JSON dump
  probe(...)               a one-token chat call: the only honest key check where there is no model list

A key is only ever sent to the base address it was given with: nothing here looks at a second host, and redirects are
never followed (OPENER), because urllib's default would copy the key header to the host it is sent on to.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import NamedTuple
from urllib.parse import parse_qs, quote, urlparse, urlunparse

ANTHROPIC_VERSION = "2023-06-01"
AZURE_DEFAULT_VERSION = "2024-10-21"            # GA data-plane version: tools, streaming and stream_options work
AZURE_FOUNDRY_VERSION = "2024-05-01-preview"    # the Foundry /models (model inference) route


class EndpointError(ValueError):
    pass


# ---------- the capability table -----------------------------------------------------------------------------------
# auth: bearer (Authorization: Bearer KEY), api-key (Azure's api-key header), x-api-key (Anthropic's).
# wire: openai (chat completions) or anthropic (Messages).  lists: GET {base}/models returns the model list.
# deployment: the model name is a deployment the user named, so there is no list to pick from.
# reasoning: the o-series / gpt-5 rules apply to the model names this provider serves (see reasoning_model()).
# relay: agents that send a plain Bearer key (Pi, Hermes, OpenCode) cannot call it directly: a local relay can.
_B = dict(auth="bearer", wire="openai", lists=True, deployment=False, reasoning=False, stream_usage=True,
          tools=True, relay=False, keyless=False, hosts=(), path="", version="", label="", key_hint="",
          note="", env=())

CAPS: dict[str, dict] = {
    "generic": {**_B, "label": "OpenAI-compatible endpoint", "key_hint": "The key your service gave you.",
                "note": "Any server that speaks chat completions: vLLM, LiteLLM proxy, a gateway of your own."},
    "openai": {**_B, "label": "OpenAI", "hosts": (r"(^|\.)api\.openai\.com$",), "path": "/v1", "reasoning": True,
               "key_hint": "sk-... from platform.openai.com, API keys.", "env": ("OPENAI_API_KEY",)},
    "anthropic": {**_B, "label": "Anthropic", "auth": "x-api-key", "wire": "anthropic",
                  "hosts": (r"(^|\.)anthropic\.com$",), "path": "/v1", "key_hint": "sk-ant-... from console.anthropic.com.",
                  "env": ("ANTHROPIC_API_KEY",)},
    "openrouter": {**_B, "label": "OpenRouter", "hosts": (r"(^|\.)openrouter\.ai$",), "path": "/api/v1",
                   "key_hint": "sk-or-... from openrouter.ai, Keys.", "env": ("OPENROUTER_API_KEY",)},
    "groq": {**_B, "label": "Groq", "hosts": (r"(^|\.)api\.groq\.com$",), "path": "/openai/v1",
             "key_hint": "gsk_... from console.groq.com.", "env": ("GROQ_API_KEY",)},
    "together": {**_B, "label": "Together AI", "hosts": (r"(^|\.)api\.together\.(xyz|ai)$",), "path": "/v1",
                 "key_hint": "From api.together.ai, Settings, API keys.", "env": ("TOGETHER_API_KEY",)},
    "fireworks": {**_B, "label": "Fireworks AI", "hosts": (r"(^|\.)api\.fireworks\.ai$",), "path": "/inference/v1",
                  "key_hint": "fw_... from fireworks.ai, API keys.", "env": ("FIREWORKS_API_KEY",)},
    "mistral": {**_B, "label": "Mistral", "hosts": (r"(^|\.)api\.mistral\.ai$",), "path": "/v1",
                "key_hint": "From console.mistral.ai, API keys.", "env": ("MISTRAL_API_KEY",)},
    "xai": {**_B, "label": "xAI (Grok)", "hosts": (r"(^|\.)api\.x\.ai$",), "path": "/v1",
            "key_hint": "xai-... from console.x.ai.", "env": ("XAI_API_KEY",)},
    "deepseek": {**_B, "label": "DeepSeek", "hosts": (r"(^|\.)api\.deepseek\.com$",), "path": "",
                 "key_hint": "sk-... from platform.deepseek.com.", "env": ("DEEPSEEK_API_KEY",)},
    "perplexity": {**_B, "label": "Perplexity", "hosts": (r"(^|\.)api\.perplexity\.ai$",), "path": "", "lists": False,
                   "tools": False, "key_hint": "pplx-... from perplexity.ai, API settings.",
                   "note": "Perplexity has no model list and no tool calling: type a model name such as sonar.",
                   "env": ("PERPLEXITY_API_KEY",)},
    "gemini": {**_B, "label": "Google Gemini", "hosts": (r"(^|\.)generativelanguage\.googleapis\.com$",),
               "path": "/v1beta/openai", "key_hint": "AIza... from aistudio.google.com, Get API key.",
               "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY")},
    "bedrock": {**_B, "label": "Amazon Bedrock (OpenAI-compatible)",
                "hosts": (r"^bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com$", r"^bedrock-mantle\.[a-z0-9-]+\.api\.aws$"),
                "path": "/openai/v1", "key_hint": "A Bedrock API key (a long-term or short-term key from the Bedrock console).",
                "note": "Use the Bedrock API key, not an AWS access key and secret."},
    "github": {**_B, "label": "GitHub Models", "hosts": (r"(^|\.)models\.inference\.ai\.azure\.com$",
                                                         r"(^|\.)models\.github\.ai$"),
               "path": "", "reasoning": True, "key_hint": "A GitHub personal access token with the models scope.",
               "note": "models.inference.ai.azure.com looks like Azure but is GitHub Models: the key is a GitHub token."},
    "nvidia": {**_B, "label": "NVIDIA NIM", "hosts": (r"(^|\.)integrate\.api\.nvidia\.com$",), "path": "/v1",
               "key_hint": "nvapi-... from build.nvidia.com.", "env": ("NVIDIA_API_KEY",)},
    "cerebras": {**_B, "label": "Cerebras", "hosts": (r"(^|\.)api\.cerebras\.ai$",), "path": "/v1",
                 "key_hint": "csk-... from cloud.cerebras.ai.", "env": ("CEREBRAS_API_KEY",)},
    "sambanova": {**_B, "label": "SambaNova", "hosts": (r"(^|\.)api\.sambanova\.ai$",), "path": "/v1",
                  "key_hint": "From cloud.sambanova.ai, API keys.", "env": ("SAMBANOVA_API_KEY",)},
    "ollama": {**_B, "label": "Ollama (on this computer)", "keyless": True,
               "key_hint": "No key needed. Leave it empty or type anything.",
               "note": "Ollama serves chat completions under /v1, for example http://localhost:11434/v1."},
    "lmstudio": {**_B, "label": "LM Studio (on this computer)", "keyless": True,
                 "key_hint": "No key needed. Leave it empty or type anything."},
    # Azure. The model name is the deployment name the user chose in Azure; the key travels in the api-key header.
    "azure-classic": {**_B, "label": "Azure OpenAI", "auth": "api-key", "deployment": True, "reasoning": True,
                      "lists": False, "version": AZURE_DEFAULT_VERSION, "relay": True,
                      "key_hint": "Key 1 or Key 2 from the Azure portal, your resource, Keys and Endpoint.",
                      "note": "Address https://NAME.openai.azure.com. The model is your deployment name.",
                      "env": ("AZURE_OPENAI_API_KEY",)},
    "azure-v1": {**_B, "label": "Azure OpenAI (v1 route)", "auth": "api-key", "deployment": True, "reasoning": True,
                 "lists": False,
                 "key_hint": "Key 1 or Key 2 from the Azure portal, your resource, Keys and Endpoint.",
                 "note": "Address https://NAME.openai.azure.com/openai/v1 (or NAME.services.ai.azure.com/openai/v1). "
                         "No api-version needed. The model is your deployment name."},
    "azure-foundry": {**_B, "label": "Azure AI Foundry (model inference)", "auth": "api-key", "deployment": True,
                      "reasoning": True, "lists": False, "version": AZURE_FOUNDRY_VERSION, "relay": True,
                      "key_hint": "The key of the Foundry resource (Keys and Endpoint).",
                      "note": "Address https://NAME.services.ai.azure.com/models. The model is the deployment name."},
    "azure-anthropic": {**_B, "label": "Azure AI Foundry (Claude)", "auth": "x-api-key", "wire": "anthropic",
                        "deployment": True, "lists": False, "stream_usage": False,
                        "key_hint": "The key of the Foundry resource (Keys and Endpoint).",
                        "note": "Address https://NAME.services.ai.azure.com/anthropic. The model is the deployment name."},
}
KINDS = tuple(CAPS)
AZURE_KINDS = ("azure-classic", "azure-v1", "azure-foundry", "azure-anthropic")


def caps(kind: str) -> dict:
    return CAPS.get(kind) or CAPS["generic"]


def is_azure(kind: str) -> bool:
    return kind in AZURE_KINDS


def wire_of(kind: str) -> str:
    return caps(kind)["wire"]


# ---------- recognising an address ---------------------------------------------------------------------------------

_AZURE_HOSTS = (("classic", r"\.openai\.azure\.(com|us|cn)$"), ("cognitive", r"\.cognitiveservices\.azure\.(com|us|cn)$"),
                ("foundry", r"\.services\.ai\.azure\.(com|us|cn)$"))


def _azure_family(host: str) -> str:
    for fam, rx in _AZURE_HOSTS:
        if re.search(rx, host):
            return fam
    from meshpool_harness import providers as PV            # the shared suffix list (providers.is_azure) also counts
    for suf in PV.AZURE_SUFFIXES:
        if host == suf.lstrip(".") or host.endswith(suf if suf.startswith(".") else "." + suf):
            return "foundry" if "services.ai" in suf else "cognitive" if "cognitiveservices" in suf else "classic"
    return ""


def _host_kind(host: str, port: int | None = None) -> str:
    for kind, c in CAPS.items():
        if any(re.search(rx, host) for rx in c["hosts"]):
            return kind
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local"):
        return "ollama" if port == 11434 else "lmstudio" if port == 1234 else "generic"
    return "generic"


def _azure_base(kind: str, origin: str) -> str:
    return origin + {"azure-classic": "", "azure-v1": "/openai/v1", "azure-foundry": "/models",
                     "azure-anthropic": "/anthropic/v1"}[kind]


class Parsed(NamedTuple):
    base: str            # the address calls are built from: no query, no endpoint path
    kind: str
    deployment: str      # a deployment named inside a pasted Azure address, else ""
    api_version: str     # an api-version found in the pasted address, else ""


def _with_scheme(u: str) -> str:
    u = (u or "").strip().strip("'\"")
    if not u:
        raise EndpointError("Enter the endpoint address, for example https://api.openai.com/v1.")
    if "://" not in u:
        # Only this computer (localhost, 127.x, ::1) defaults to plain http: anything else, a LAN address, a *.internal
        # or *.lan name or a bare host name included, is https unless the user types http:// themselves, so a key is
        # never sent in the clear across a network on a guess.
        from meshpool_harness.core import http as H
        try:
            h = urlparse("http://" + u).hostname or ""
        except ValueError:
            h = ""
        u = ("http://" if re.match(r"(localhost|127\.|\[?::1)", u) or H._is_loopback(h) else "https://") + u
    return u


def parse_pasted(url: str, kind: str = "", api_version: str = "") -> Parsed:
    """Understand whatever was pasted into the address box. A full Azure call address such as
    https://x.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21 is split into the
    base address, the deployment (the model name) and the api-version; any other address loses a pasted endpoint
    path (/chat/completions, /models, /messages). `kind` is a provider the user picked: it decides what a bare Azure
    address means."""
    u = _with_scheme(url)
    p = urlparse(u)
    if not p.hostname or p.scheme not in ("http", "https"):
        raise EndpointError("That does not look like a web address. Use the form https://api.example.com/v1.")
    host = (p.hostname or "").lower()
    q = parse_qs(p.query)
    version = (q.get("api-version") or q.get("api_version") or [""])[0].strip() or (api_version or "").strip()
    path = re.sub(r"/+$", "", p.path)
    origin = f"{p.scheme}://{p.netloc}"
    fam = _azure_family(host)
    if fam or is_azure(kind):
        m_dep = re.search(r"/openai/deployments/([^/?#]+)", path)
        if "/anthropic" in path or kind == "azure-anthropic" and not path.startswith(("/openai", "/models")):
            k = "azure-anthropic"
        elif re.search(r"/openai/v1(/|$)", path) or (kind == "azure-v1" and not m_dep):
            k = "azure-v1"
        elif m_dep or (kind == "azure-classic" and version):
            k = "azure-classic"                  # the older route: a pasted deployment address, or a chosen api-version
        elif re.match(r"^/models(/|$)", path) or kind == "azure-foundry":
            k = "azure-foundry"
        else:
            # A bare Azure OpenAI or Foundry address: the /openai/v1 route serves every deployment (OpenAI and
            # DeepSeek, Llama, Grok ...), takes no api-version and is what Microsoft's current docs use. Pinning an
            # old api-version on the older route made newer models fail with "unsupported api version".
            k = "azure-v1"
        if k == "azure-v1":
            m = re.match(r"^(.*?/openai/v1)(?:/|$)", path)
            base = origin + (m.group(1) if m else "/openai/v1")
        else:
            base = _azure_base(k, origin)
        dep = requests_unquote(m_dep.group(1)) if m_dep else ""
        return Parsed(base, k, dep, version)
    path = re.sub(r"/(?:chat/completions|completions|responses|models|messages)/?$", "", path).rstrip("/")
    k = kind if kind in CAPS and not is_azure(kind) else _host_kind(host, p.port)
    if not path:
        d = _default_path(k, host)
        path = d
    return Parsed(urlunparse((p.scheme, p.netloc, path, "", "", "")), k, "", version)


def requests_unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


def _default_path(kind: str, host: str) -> str:
    if kind == "bedrock" and host.startswith("bedrock-mantle"):
        return "/v1"
    if kind == "github" and host.endswith("models.github.ai"):
        return "/inference"
    return caps(kind)["path"]


def detect(url: str) -> str:
    """The kind of provider an address belongs to ("generic" when nothing in it says)."""
    try:
        return parse_pasted(url).kind
    except EndpointError:
        return "generic"


# ---------- model rules --------------------------------------------------------------------------------------------

_REASONING = re.compile(r"(?<![a-z0-9])(o\d+|gpt-?5)(?![a-z])", re.I)


def reasoning_model(model: str) -> bool:
    """o1/o3/o4 and gpt-5 families (also as the deployment names people give them): they take max_completion_tokens,
    and only the default temperature."""
    return bool(_REASONING.search(model or ""))


def _ver_ge(v: str, floor: str) -> bool:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v or "")
    return bool(m) and m.group(1) >= floor


def adapt(kind: str, body: dict, api_version: str = "") -> dict:
    """A copy of the body with what this kind and model need: max_completion_tokens and no temperature for the
    reasoning models, no stream_options where the endpoint does not take them."""
    c, b = caps(kind), dict(body)
    if kind == "azure-classic" and "max_tokens" in b and "max_completion_tokens" not in b:
        b["max_completion_tokens"] = b.pop("max_tokens")   # the deployment route takes it for every current model
    if c["reasoning"] and reasoning_model(str(b.get("model") or "")):
        if "max_tokens" in b:
            b["max_completion_tokens"] = b.pop("max_tokens")
        for f in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            b.pop(f, None)
    if not c["stream_usage"] or (kind == "azure-classic" and api_version and not _ver_ge(api_version, "2024-09-01")):
        b.pop("stream_options", None)
    return b


# ---------- building a request -------------------------------------------------------------------------------------

class Call(NamedTuple):
    url: str
    headers: dict
    body: dict
    anthropic: bool


def _looks_jwt(k: str) -> bool:
    return k.count(".") == 2 and k.startswith("eyJ")


def auth_headers(kind: str, key: str) -> dict:
    c = caps(kind)
    h = {"Content-Type": "application/json"}
    if c["auth"] == "x-api-key":
        h["anthropic-version"] = ANTHROPIC_VERSION
    if key:
        if c["auth"] == "bearer" or (is_azure(kind) and c["auth"] == "api-key" and _looks_jwt(key)):
            # a Microsoft Entra ID token (a JWT) goes as a Bearer token; Azure resource keys go in api-key
            h["Authorization"] = f"Bearer {key}"
        else:
            h["api-key" if c["auth"] == "api-key" else "x-api-key"] = key
    return h


def build(kind: str, base: str, key: str, body: dict, *, api_version: str = "", path: str = "chat") -> Call:
    """(url, headers, body, is_anthropic) of one call. `path` is "chat" or "responses". The Azure classic URL carries
    the deployment (the model name) and the api-version; everything else is {base}/chat/completions."""
    c = caps(kind)
    base = base.rstrip("/")
    model = str(body.get("model") or "")
    if c["wire"] == "anthropic":
        from meshpool_harness.core import anthropic_wire as AW
        h = auth_headers(kind, key)
        return Call(f"{base}/messages", h, AW.to_request(dict(body)), True)
    b = adapt(kind, body, api_version or c["version"])
    ver = api_version or c["version"]
    tail = "responses" if path == "responses" else "chat/completions"
    if kind == "azure-classic":
        if path == "responses":
            url = f"{base}/openai/responses?api-version={quote(ver)}"
        else:
            url = f"{base}/openai/deployments/{quote(model, safe='')}/chat/completions?api-version={quote(ver)}"
    elif kind == "azure-foundry":
        url = f"{base}/{tail}?api-version={quote(ver)}"
    else:
        url = f"{base}/{tail}"
    return Call(url, auth_headers(kind, key), b, False)


# ---------- the saved settings, for the callers that only have a base address ---------------------------------------

def resolve(base: str) -> tuple[str, str]:
    """(kind, api_version) for a base address. The kind and api-version the user saved apply only to the saved key
    endpoint itself; any other address (the ChatGPT relay, a typed-but-unsaved test) is read from its own text."""
    from meshpool_harness import providers as PV
    b = (base or "").rstrip("/")
    if b and b == PV.key_base():
        return PV.key_kind(), PV.key_api_version()
    return detect(b), ""


def chat_request(base: str, key: str, body: dict, *, kind: str = "", api_version: str = "",
                 path: str = "chat") -> tuple[str, dict, dict]:
    """(url, body, headers) for a chat-completions call to `base` with `key`: the call sites that already speak
    chat completions use this one. An Anthropic-wire kind falls back to plain chat completions here (callers that
    translate the wire do it before this point)."""
    k, v = (kind, api_version) if kind else resolve(base)
    if wire_of(k) == "anthropic":
        k = "generic"
    c = build(k, base, key, body, api_version=api_version or v, path=path)
    from meshpool_harness import providers as PV
    return with_query(c.url), c.body, {**extra_headers(), **PV.custom_auth(c.headers, base, key)}   # the user's own key header wins


_NEVER = {"authorization", "api-key", "x-api-key", "host", "content-length", "content-type", "cookie",
          "proxy-authorization", "transfer-encoding"}


def extra_headers() -> dict:
    """MH_EXTRA_HEADERS: headers added to every model request, as JSON ({"X-Team": "data"}) or "Name: value" pairs
    split by ; or new lines. They never replace the key header or the ones the request itself needs."""
    raw = os.environ.get("MH_EXTRA_HEADERS", "").strip()
    if not raw:
        return {}
    pairs = []
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            pairs = list(d.items()) if isinstance(d, dict) else []
        except ValueError:
            pairs = []
    else:
        for part in re.split(r"[;\n]", raw):
            n, sep, val = part.partition(":")
            if sep:
                pairs.append((n, val))
    out = {}
    for n, val in pairs:
        n, val = str(n).strip(), str(val).strip()
        if re.fullmatch(r"[A-Za-z0-9-]{1,64}", n) and n.lower() not in _NEVER and "\r" not in val and "\n" not in val:
            out[n] = val[:1000]
    return out


def with_query(url: str) -> str:
    """MH_EXTRA_QUERY: query parameters added to every model request URL (a=1&b=2)."""
    raw = os.environ.get("MH_EXTRA_QUERY", "").strip().lstrip("?")
    if not raw:
        return url
    from urllib.parse import parse_qsl, urlencode
    q = urlencode([(k, v) for k, v in parse_qsl(raw) if k])
    return (url + ("&" if "?" in url else "?") + q) if q else url


# ---------- never follow a redirect with a key -----------------------------------------------------------------------

def _keyed():
    from meshpool_harness.core import http as _http          # the one keyed-request implementation
    return _http


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A handler that turns every redirect into an HTTPError (the strictest policy core.http offers)."""
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302      # 308 is unknown to Python 3.10's handler
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return _keyed()._KeyedRedirect(refuse=True).redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req, timeout: float = 30, handlers=(), refuse_redirects: bool = True):
    """urllib's urlopen for a request that carries a key: core.http.open (no redirect followed, no proxy for a
    loopback server, the shared TLS context). A redirect surfaces as an HTTP error naming the target host."""
    return _keyed().open(req, timeout, handlers, refuse_redirects=refuse_redirects)


class _Opener:
    """OPENER.open(req, timeout): the setup test's opener. Every redirect is refused (core.http.open with
    refuse_redirects), so a key is never carried to another address."""
    def open(self, req, timeout: float = 30):
        return urlopen(req, timeout, refuse_redirects=True)


OPENER = _Opener()


# ---------- plain-words errors ---------------------------------------------------------------------------------------

_KEY_PREFIXES = (("sk-ant-", "anthropic"), ("sk-or-", "openrouter"), ("gsk_", "groq"), ("AIza", "gemini"),
                 ("xai-", "xai"), ("pplx-", "perplexity"), ("nvapi-", "nvidia"), ("csk-", "cerebras"),
                 ("fw_", "fireworks"), ("ghp_", "github"), ("github_pat_", "github"), ("hf_", "huggingface"))


def key_owner(key: str) -> str:
    """The provider a key's prefix points to ("" when it says nothing): a key for another host is the commonest 401."""
    k = (key or "").strip()
    return next((p for pre, p in _KEY_PREFIXES if k.startswith(pre)), "")


def _msg(body) -> tuple[str, str]:
    """(message, code) out of an error body of any common shape, short and single-line."""
    d = body
    if isinstance(body, (bytes, str)):
        s = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        try:
            d = json.loads(s)
        except ValueError:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()[:200], ""
    if isinstance(d, list) and d:
        d = d[0]
    if isinstance(d, dict) and set(d) == {"raw"} and isinstance(d["raw"], str):
        return _msg(d["raw"])                    # a caller's wrapper around a body that was not JSON
    if not isinstance(d, dict):
        return "", ""
    e = d.get("error", d)
    if isinstance(e, str):
        return e[:200], ""
    if isinstance(e, dict):
        return str(e.get("message") or e.get("detail") or "")[:200], str(e.get("code") or e.get("type") or "")
    return str(d.get("message") or d.get("detail") or "")[:200], str(d.get("code") or "")


def redact_key(text: str, key: str) -> str:
    """`text` with every whole occurrence of `key` replaced by ***. A key of 8+ characters only, and only where it is
    not part of a longer word of letters and digits (a short key must not mangle ordinary words); punctuation next to
    it (a full stop, a dash, an underscore, a quote) does not hide it."""
    key = str(key or "")
    if not isinstance(text, str) or len(key) < 8:
        return text
    return re.sub(r"(?<![A-Za-z0-9])" + re.escape(key) + r"(?![A-Za-z0-9])", "***", text)


def explain(status: int, body=None, *, kind: str = "generic", host: str = "", model: str = "", key: str = "",
            api_version: str = "") -> str:
    """A failed call in words the user can act on: what to check, not the raw reply."""
    msg, code = _msg(body)
    low = f"{msg} {code}".lower()
    c = caps(kind)
    who = c["label"] if kind != "generic" else (host or "The endpoint")
    az = is_azure(kind)
    msg = redact_key(msg, key)
    if status == 401 or (status == 403 and "invalid" in low and "key" in low):
        out = [f"The key was rejected ({status})."]
        owner = key_owner(key)
        if owner and owner != kind and not az:
            out.append(f"This looks like a {CAPS.get(owner, {}).get('label', owner.title())} key, so it will not work on "
                       f"{host or who}. Use the address of that service, or paste the key for {host or who}.")
        elif az:
            out.append("Copy Key 1 or Key 2 from the Azure portal (your resource, Keys and Endpoint) and the address "
                       "shown next to it; a key from another resource or region is refused. Azure keys go in the "
                       "api-key header, which Meshpool does for you.")
        elif c["auth"] == "x-api-key":
            out.append("Check the key starts with sk-ant- and belongs to your Anthropic account.")
        else:
            out.append(f"Check that the key is the one for {host or 'this service'} (not copied from another "
                       "provider), that it is not masked or expired, and that you pasted all of it.")
    elif status == 403:
        out = [f"{who} refused access ({status})."]
        out.append("On Azure this usually means the resource's network rules (firewall or private endpoint) block this "
                   "computer, or key access is switched off for the resource (Microsoft Entra only). Check Networking "
                   "and Keys in the Azure portal." if az else
                   "The key is valid but not allowed to use this model or address: check its permissions, region and "
                   "any IP allow-list on your account.")
    elif status == 404:
        if az and kind in ("azure-classic", "azure-foundry", "azure-v1", "azure-anthropic") and (
                "deployment" in low or "notfound" in low.replace(" ", "") or "does not exist" in low):
            out = [f"Azure has no deployment named '{model}' on this resource (404)." if model else
                   "Azure could not find that deployment (404)."]
            out.append("In Azure the model box takes the deployment name you chose, not the model name. Open the "
                       "resource in Azure AI Foundry, Deployments, and copy the name from there.")
        elif az:
            out = ["Azure did not find that address or deployment (404)."]
            out.append("Check the endpoint address is the one shown under Keys and Endpoint (https://NAME.openai.azure."
                       "com) and that the deployment name is spelled exactly as in Azure.")
        else:
            out = [f"{who} answered 404: no such address or model."]
            out.append("Check the base address (it usually ends in /v1) and the model name against the provider's list.")
    elif status == 400 and ("api version" in low or "api-version" in low or "apiversion" in low):
        out = [f"Azure does not accept api-version {api_version or '(none)'} for this call (400)."]
        out.append(f"Change it in the API version box, for example {AZURE_DEFAULT_VERSION}, or use the /openai/v1 "
                   "address, which needs none.")
    elif status == 400 and "max_tokens" in low and "max_completion_tokens" in low:
        out = [f"{who} wants max_completion_tokens for this model (400)."]
        out.append("Meshpool switches to it for the o-series and gpt-5 models by name; if your deployment has another "
                   "name, the next try fixes it automatically.")
    elif status == 429:
        out = [f"{who} is limiting requests or your quota is used up (429)."]
        out.append("Wait a minute and try again. On Azure, raise the deployment's tokens-per-minute quota; "
                   "elsewhere check the plan's limits and balance." if az else
                   "Wait a minute and try again, and check your plan's limits and balance.")
    elif status == 402:
        out = [f"{who} says the account is out of credit (402). Add credit with the provider."]
    elif status >= 500 or status == 0:
        out = [f"{who} had a problem on its side ({status or 'no answer'}). Try again in a moment."]
    elif 300 <= status < 400 and "did not send your key" in msg:
        return msg                               # a refused redirect (core.http): names the host and what to set
    else:
        out = [f"{who} answered {status}."]
    if msg and status not in (401, 429, 402) and status < 500:
        out.append(f"(The service said: {msg})")
    return " ".join(out)


# ---------- a one-token chat call: the key check where there is no model list ---------------------------------------

def probe(base: str, key: str, model: str, *, kind: str = "", api_version: str = "", timeout: float = 30) -> dict:
    """{ok, status, error}: one tiny chat call with exactly the request the agent will make."""
    k = kind or detect(base)
    ver = api_version or caps(k)["version"]
    body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "ping"}]}
    call = build(k, base, key, body, api_version=ver)
    req = urllib.request.Request(call.url, data=json.dumps(call.body).encode(), method="POST",
                                 headers={"User-Agent": "Meshpool", **call.headers})
    host = (urlparse(base).hostname or "").lower()
    try:
        with urlopen(req, timeout) as r:
            r.read(200_000)
            return {"ok": True, "status": r.status, "error": ""}
    except urllib.error.HTTPError as e:
        raw = e.read(200_000)
        return {"ok": False, "status": e.code, "error": explain(e.code, raw, kind=k, host=host, model=model, key=key,
                                                                api_version=ver)}
    except TimeoutError:
        return {"ok": False, "status": 0, "error": "The server did not answer in time."}
    except (urllib.error.URLError, OSError):
        return {"ok": False, "status": 0, "error": "No server at that address. Check the address and your connection."}


# ---------- keys in the environment ----------------------------------------------------------------------------------
# (kind, key variables, base variables, fixed base). A fixed base means the key belongs to that provider's own host
# and goes nowhere else. A variable base means the key goes only to the address the user put in that variable.

ENV_SOURCES = (
    ("openai", ("OPENAI_API_KEY",), ("OPENAI_BASE_URL",), "https://api.openai.com/v1"),
    ("azure-classic", ("AZURE_OPENAI_API_KEY",), ("AZURE_OPENAI_ENDPOINT",), ""),
    ("anthropic", ("ANTHROPIC_API_KEY",), ("ANTHROPIC_BASE_URL",), "https://api.anthropic.com/v1"),
    ("openrouter", ("OPENROUTER_API_KEY",), (), "https://openrouter.ai/api/v1"),
    ("groq", ("GROQ_API_KEY",), (), "https://api.groq.com/openai/v1"),
    ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), (), "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("mistral", ("MISTRAL_API_KEY",), (), "https://api.mistral.ai/v1"),
    ("xai", ("XAI_API_KEY",), (), "https://api.x.ai/v1"),
    ("together", ("TOGETHER_API_KEY",), (), "https://api.together.xyz/v1"),
    ("fireworks", ("FIREWORKS_API_KEY",), (), "https://api.fireworks.ai/inference/v1"),
    ("deepseek", ("DEEPSEEK_API_KEY",), (), "https://api.deepseek.com"),
    ("perplexity", ("PERPLEXITY_API_KEY",), (), "https://api.perplexity.ai"),
    ("cerebras", ("CEREBRAS_API_KEY",), (), "https://api.cerebras.ai/v1"),
    ("sambanova", ("SAMBANOVA_API_KEY",), (), "https://api.sambanova.ai/v1"),
    ("nvidia", ("NVIDIA_API_KEY",), (), "https://integrate.api.nvidia.com/v1"),
)


def env_sources(environ) -> list[dict]:
    """Every provider key found in an environment mapping, each with the one base it may go to: [{kind, var, key,
    base, api_version, deployment}]. A source whose address comes from a variable needs that variable set; an
    OpenAI key with no OPENAI_BASE_URL is skipped (it must never fall through to another endpoint)."""
    out = []
    for kind, kvars, bvars, fixed in ENV_SOURCES:
        var = next((v for v in kvars if str(environ.get(v) or "").strip()), "")
        if not var:
            continue
        raw = next((str(environ.get(v)).strip() for v in bvars if str(environ.get(v) or "").strip()), "")
        if not fixed and not raw:
            continue
        row = {"kind": kind, "var": var, "key": str(environ[var]).strip(), "base": "", "api_version": "",
               "deployment": ""}
        try:
            if raw:
                p = parse_pasted(raw, kind if kind != "openai" else "",
                                 str(environ.get("AZURE_OPENAI_API_VERSION") or "") if kind == "azure-classic" else "")
                row.update(base=p.base, api_version=p.api_version, deployment=p.deployment)
                if kind == "azure-classic":
                    row["kind"] = p.kind if is_azure(p.kind) else "azure-classic"
                elif kind == "anthropic" or kind == "openai":
                    row["kind"] = p.kind if kind == "openai" else kind
            else:
                row["base"] = fixed
        except EndpointError:
            continue
        if kind == "azure-classic":
            row["api_version"] = row["api_version"] or str(environ.get("AZURE_OPENAI_API_VERSION") or "").strip()
            row["deployment"] = row["deployment"] or str(environ.get("AZURE_OPENAI_DEPLOYMENT") or "").strip()
        out.append(row)
    return out


def catalog() -> list[dict]:
    """The providers the setup page offers: id, label, an example address, what to paste as the key."""
    ex = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
          "openrouter": "https://openrouter.ai/api/v1", "groq": "https://api.groq.com/openai/v1",
          "together": "https://api.together.xyz/v1", "fireworks": "https://api.fireworks.ai/inference/v1",
          "mistral": "https://api.mistral.ai/v1", "xai": "https://api.x.ai/v1", "deepseek": "https://api.deepseek.com",
          "perplexity": "https://api.perplexity.ai",
          "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
          "bedrock": "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1",
          "github": "https://models.inference.ai.azure.com", "nvidia": "https://integrate.api.nvidia.com/v1",
          "cerebras": "https://api.cerebras.ai/v1", "sambanova": "https://api.sambanova.ai/v1",
          "ollama": "http://localhost:11434/v1", "lmstudio": "http://localhost:1234/v1",
          "generic": "https://your-server.example.com/v1",
          "azure-classic": "https://NAME.openai.azure.com", "azure-v1": "https://NAME.openai.azure.com/openai/v1",
          "azure-foundry": "https://NAME.services.ai.azure.com/models",
          "azure-anthropic": "https://NAME.services.ai.azure.com/anthropic"}
    return [{"id": k, "label": c["label"], "base": ex.get(k, ""), "key_hint": c["key_hint"], "note": c["note"],
             "azure": is_azure(k), "deployment": c["deployment"], "keyless": c["keyless"],
             "version": c["version"], "lists": c["lists"]} for k, c in CAPS.items()]
