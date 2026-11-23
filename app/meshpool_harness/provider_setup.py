"""Setting up the API key endpoint: base URL + key -> test (wire auto-detected, model list) -> pick a model -> save.

  test(...)    GET {base}/models with the key on the OpenAI wire (Bearer) and the Anthropic wire (x-api-key), answer
               {ok, wire, base_url, models, latency_ms, error}. The key goes only to the base URL given, redirects are
               never followed, and nothing here prints or returns a key.
  save(...)    one atomic settings write: base URL, key (kept when omitted), wire, default model. The default model is
               never checked against a list from another endpoint, only against a fresh test of this very endpoint+key.
  logins()     what is already signed in on this computer (read-only, no network): the Codex CLI's ChatGPT sign-in,
               an installed Claude Code CLI, API keys in the environment.
  use(...)     switch to one of them.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

from meshpool_harness import config as C
from meshpool_harness import providers as PV
from meshpool_harness.core import anthropic_wire as AW
from meshpool_harness.core import http as _mhttp

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
OPENAI_BASE = "https://api.openai.com/v1"
FRESH_S = 600                       # how long a test result may vouch for a model list
_TESTS: dict = {}                   # (base, key fingerprint) -> {"t", "models"}
_LOCK = threading.Lock()
LOCAL = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


class SetupError(ValueError):
    pass


# ---------- helpers ------------------------------------------------------------------------------------------------

clean_key = C.clean_key


def _fp(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""


def is_local(url: str) -> bool:
    return PV.keyless_ok(url)


def normalise_base(url: str) -> str:
    """https://host[/v1]: scheme added, trailing slash and a pasted endpoint path (/chat/completions, /models,
    /messages) removed, /v1 added for the hosts that always need it."""
    u = (url or "").strip().strip("'\"")
    if not u:
        raise SetupError("Enter the endpoint address, for example https://api.openai.com/v1.")
    if "://" not in u:
        u = ("http://" if PV.keyless_ok("http://" + u) else "https://") + u
    p = urlparse(u)
    if not p.hostname or p.scheme not in ("http", "https"):
        raise SetupError("That does not look like a web address. Use the form https://api.example.com/v1.")
    h = p.hostname.lower()
    if h.endswith(PV.AZURE_SUFFIXES):                  # Azure OpenAI / Foundry: keep /openai/deployments/DEP, /openai/v1, /models
        path = re.sub(r"/(?:chat/completions|completions|responses|messages)/?$", "", p.path.rstrip("/"))
        path = re.sub(r"/models/?$", "", path) if path != "/models" else path
        path = re.sub(r"/openai/?$", "", path).rstrip("/")
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))
    path = re.sub(r"/(?:chat/completions|completions|responses|models|messages)/?$", "", p.path.rstrip("/"))
    path = path.rstrip("/")
    if not path and (h in ("api.openai.com", "api.anthropic.com") or h.endswith(".anthropic.com")):
        path = "/v1"
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def _candidates(base: str) -> list[str]:
    """The base itself, and with /v1 when the address has no path at all (many servers serve /v1/models only)."""
    p = urlparse(base)
    return [base] if p.path else [base, base + "/v1"]


def _tls_kind(exc) -> str:
    """"cert" (certificate not trusted), "tls" (the server does not speak TLS here) or "" for anything else."""
    chain, e = [], exc
    for _ in range(4):
        if e is None or isinstance(e, str):
            break
        chain.append(e)
        e = getattr(e, "reason", None)
    if any(isinstance(t, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(t) for t in chain):
        return "cert"
    if any(isinstance(t, ssl.SSLError) or re.search(r"WRONG_VERSION_NUMBER|record layer failure|UNKNOWN_PROTOCOL",
                                                    str(t)) for t in chain):
        return "tls"
    return ""


def _net_error(err: str, base: str) -> str:
    """The sentence for a failed connection kind ("" when err is not one)."""
    if err == "refused":
        return "No server at that address. Check the address and your connection."
    if err == "timeout":
        return "The server did not answer in time."
    if err == "not_http":
        return "Something answers at that address, but it is not a web API. Check the address and port."
    if err == "tls":
        return "That address does not speak HTTPS. Try http://" + base.split("://", 1)[-1]
    if err == "cert":
        return _mhttp.explain(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"), base)
    return ""


def _is_reply(wire: str, data) -> bool:
    """Does a chat probe's JSON body look like a model's reply (not an error body, not a page)?"""
    if not isinstance(data, dict):
        return False
    if wire == "anthropic":
        return isinstance(data.get("content"), list) or data.get("type") == "message"
    if wire == "responses":
        return "output" in data
    return isinstance(data.get("choices"), list)


def _not_reply_msg(data) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    txt = (err.get("message") if isinstance(err, dict) else err) if err else ""
    if txt:
        return str(txt)[:200]
    return "The address answered, but not like a model API."


def _get(url: str, headers: dict, timeout: float = 15.0, body: dict | None = None):
    """(status, parsed JSON or None, error kind). error kind: "" | "refused" | "timeout". With `body`: a JSON POST."""
    hdr = {"User-Agent": "Meshpool", "Accept": "application/json", **headers}
    if body is not None:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=hdr, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 method="POST" if body is not None else "GET")
    try:
        with _mhttp.open(req, timeout, refuse_redirects=True) as r:
            raw = r.read(4_000_000)
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(200_000), e.code
    except (socket.timeout, TimeoutError):
        return 0, None, "timeout"
    except http.client.HTTPException:
        return 0, None, "not_http"
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            return 0, None, "timeout"
        return 0, None, _tls_kind(e) or "refused"
    except (OSError, ValueError) as e:
        return 0, None, _tls_kind(e) or "refused"
    try:
        return status, json.loads(raw.decode("utf-8", "replace")), ""
    except ValueError:
        return status, None, ""


def _headers(wire: str, key: str, base: str = "", header: str = "", scheme: str = "") -> dict:
    return PV.auth_headers(key, base, wire, header, scheme)


def explain(status: int, data, *, base: str, model: str = "", version: str = "") -> str:
    """What a failed call means, naming the real problem (wrong key, no permission, wrong deployment, wrong
    api-version, wrong address)."""
    err = data.get("error") if isinstance(data, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else
           (data.get("message") if isinstance(data, dict) else "")) or ""
    code = str(err.get("code") or "") if isinstance(err, dict) else ""
    low = f"{msg} {code}".lower()
    host = PV.host(base)
    az = PV.is_azure(base)
    tail = f" Server said: {str(msg)[:200]}" if msg else ""
    if status == 401:
        if az:
            return (f"Azure rejected the key (401) for {host}. Use a key from that resource's Keys and Endpoint page "
                    "(a key of another resource fails here), or an Entra access token." + tail)
        return "The key was rejected (401). Check it and try again." + tail
    if status == 403:
        if az:
            return (f"The key is recognised but not allowed (403) on {host}. Check the resource's Networking "
                    "(firewall, private endpoint) and, for an Entra token, the role Cognitive Services OpenAI User." + tail)
        return f"The key is valid but not allowed to do this (403). Check its permissions and the address." + tail
    if status == 404:
        if az and ("deployment" in low or code == "DeploymentNotFound"):
            return (f"No deployment named '{model}' on {host}. Type the deployment name from Azure AI Foundry, "
                    "Deployments (the name you chose, not the model name)." + tail)
        if az:
            return (f"Azure did not find that route (404): check the address {host}, the deployment name"
                    f"{(' ' + repr(model)) if model else ''} and the api-version ({version or PV.azure_version()}); "
                    "set MESHPOOL_AZURE_API_VERSION to change it." + tail)
        if "model" in low:
            return f"The model {model!r} was not found (404) on {host}. Check its name." + tail
        return (f"Not found (404): check the address (most services end in /v1)"
                f"{' and the model name ' + repr(model) if model else ''}." + tail)
    if status == 400 and az and ("api version" in low or "api-version" in low or "apiversion" in low):
        return (f"Azure does not accept api-version {version or PV.azure_version()}. Set another in the Azure "
                "api-version field (or MESHPOOL_AZURE_API_VERSION), for example 2024-10-21." + tail)
    if status == 402:
        return "The server asks for payment (402): add credit or check the billing for this key." + tail
    if status == 429:
        return "The server is limiting requests right now (429). Try again in a moment." + tail
    if status >= 500:
        return f"The server answered with an error ({status}). Try again in a moment." + tail
    return f"The server answered {status}." + tail


def probe_chat(base: str, key: str, model: str, wire: str = "openai", header: str = "", scheme: str = "",
               version: str = "") -> tuple[int, object, str]:
    """One tiny chat call: (status, JSON or None, error kind). It exercises the real URL, key header and model."""
    hdr = _headers(wire, key, base, header, scheme)
    if wire == "anthropic":
        body = AW.to_request({"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "ping"}]})
        return _get(base.rstrip("/") + "/messages", hdr, 30.0, body)
    body = PV.adapt_body(base, {"model": model, "max_tokens": 16,
                                "messages": [{"role": "user", "content": "ping"}]})
    return _get(PV.chat_url(base, model, "chat/completions", version), hdr, 30.0, body)


def _test_azure(base: str, key: str, model: str, header: str, scheme: str, version: str, t0: float, out: dict) -> dict:
    dep = PV.azure_deployment(base) or model
    ver = version or PV.azure_version()
    hdr = _headers("openai", key, base, header, scheme)
    if dep:
        status, data, err = probe_chat(base, key, dep, "openai", header, scheme, version)
    else:                                    # no deployment yet: the key is still checked, on a read-only route
        p = urlparse(base).path
        status, data, err = _get(PV.chat_url(base, "", "info" if p.startswith("/models") else "models", version), hdr)
    out["latency_ms"] = int((time.time() - t0) * 1000)
    if err == "refused":
        out["error"] = f"No server at {PV.host(base)}. Check the resource name in the address and your connection."
    elif err == "timeout":
        out["error"] = "The server did not answer in time."
    elif status == 200 and dep and not _is_reply("openai", data):
        out["error"] = _not_reply_msg(data)
    elif status in (200, 429) and dep:
        out.update(ok=True, wire="openai", models=[{"id": dep}])
        if status == 429:
            out["error"] = "The key works, but the deployment is rate limited right now."
    elif status == 200:
        out.update(ok=True, wire="openai")
        out["error"] = ("The key is accepted. Azure does not list your deployments: type the deployment name "
                        "(Azure AI Foundry, Deployments) as the model.")
    elif not dep and status in (404, 405):
        out.update(ok=True, wire="openai")
        out["error"] = "Connected. Azure does not list deployments here: type the deployment name as the model."
    else:
        out["error"] = explain(status, data, base=base, model=dep, version=ver)
    if out["ok"]:
        _remember(base, key, out["models"])
    return out


def _items(data) -> list | None:
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(data, dict) and items is None and isinstance(data.get("models"), list):
        items = data["models"]
    return items if isinstance(items, list) else None


def _rows(items: list) -> list[dict]:
    out, seen = [], set()
    for it in items:
        meta = it if isinstance(it, dict) else {}
        mid = str(meta.get("id") or meta.get("name") or "") if meta else str(it)
        mid = re.sub(r"^models/", "", mid)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        row = {"id": mid}
        nm = meta.get("display_name") or meta.get("name")
        if nm and nm != mid:
            row["name"] = str(nm)
        ctx = meta.get("context_length") or meta.get("context_window") or meta.get("max_input_tokens") \
            or meta.get("inputTokenLimit")
        if isinstance(ctx, int):
            row["context"] = ctx
        out.append(row)
    return sorted(out, key=lambda r: r["id"].lower())


def _looks_anthropic(data, items) -> bool:
    if isinstance(data, dict) and ("has_more" in data or "first_id" in data):
        return True
    return bool(items) and isinstance(items[0], dict) and items[0].get("type") == "model" \
        and "display_name" in items[0]


# ---------- test ---------------------------------------------------------------------------------------------------

def resolve_key(base: str, api_key=None, env: str = "") -> str:
    """The key to use: the one typed, the named environment variable, or (same endpoint) the saved one."""
    k = clean_key(api_key)
    if k:
        return k
    if env:
        if env not in ENV_KEYS:
            raise SetupError("Unknown environment variable.")
        return clean_key(os.environ.get(env, ""))
    cur_base, cur_key = PV.key_base(), PV.key()
    if cur_key and _same_endpoint(base, cur_base):
        return cur_key
    return ""


def _same_endpoint(a: str, b: str) -> bool:
    try:
        return normalise_base(a).lower() == normalise_base(b).lower()
    except SetupError:
        return False


def test(base_url: str, api_key=None, wire: str = "auto", env: str = "", model: str = "", auth_header: str = "",
         auth_scheme: str = "", api_version: str = "") -> dict:
    t0 = time.time()
    base = normalise_base(base_url)
    api_version = (api_version or "").strip() or (PV.azure_version(base_url) if "api-version=" in str(base_url) else "")
    model = re.sub(r"[\r\n]", "", str(model or "")).strip()
    out = {"ok": False, "wire": "", "base_url": base, "models": [], "latency_ms": 0, "error": ""}
    key = resolve_key(base, api_key, env)
    host = PV.host(base)
    if not key and not is_local(base):
        out["error"] = f"Enter the API key for {host}." if not PV.key() or _same_endpoint(base, PV.key_base()) \
            else f"Enter the API key for {host}: your saved key is for {PV.host(PV.key_base())} and is not sent " \
                 "anywhere else."
        return out
    prob = PV.key_problem(key)
    if prob:
        out["error"] = prob
        return out
    if PV.is_azure(base):
        return _test_azure(base, key, model, auth_header.strip(), auth_scheme.strip().lower(), api_version, t0, out)
    wire = (wire or "auto").lower()
    if wire not in ("openai", "anthropic"):
        wire = "auto"
    order = [wire] if wire != "auto" else (["anthropic", "openai"] if host.endswith("anthropic.com")
                                           else ["openai", "anthropic"])
    auth_code = 0
    missing_base = ""
    last_status = 0
    hdr_name, hdr_scheme = auth_header.strip(), auth_scheme.strip().lower()
    for b in _candidates(base):
        for w in order:
            status, data, err = _get(b.rstrip("/") + "/models", _headers(w, key, b, hdr_name, hdr_scheme))
            if err == "tls" and urlparse(b).scheme == "https" and PV.keyless_ok(b):
                plain = "http://" + base.split("://", 1)[1]           # a local server speaking plain http
                return test(plain, api_key, wire, env, model, auth_header, auth_scheme, api_version)
            if err:
                out["error"] = _net_error(err, b)
                out["latency_ms"] = int((time.time() - t0) * 1000)
                return out
            last_status = status or last_status
            items = _items(data) if status == 200 else None
            if status == 200 and items is not None:
                real = "anthropic" if (w == "openai" and _looks_anthropic(data, items)) else w
                out.update(ok=True, wire=real, base_url=b.rstrip("/"), models=_rows(items),
                           latency_ms=int((time.time() - t0) * 1000))
                if not out["models"]:
                    out["error"] = "Connected, but this endpoint does not list models: type a model name."
                _remember(out["base_url"], key, out["models"])
                _remember(base, key, out["models"])
                return out
            if status in (401, 403):
                auth_code = auth_code or status
            elif status in (404, 405) or status == 200:               # no model list here: look at the chat route
                missing_base = missing_base or b
    out["latency_ms"] = int((time.time() - t0) * 1000)
    if auth_code:
        out["error"] = explain(auth_code, None, base=base)
    elif missing_base:
        # something answers but lists no models: a tiny real call per wire finds the wire and checks the key
        pm = model or "meshpool-connection-test"
        note = ""
        for w in order:
            st, dt, er = probe_chat(missing_base, key, pm, w, hdr_name, hdr_scheme)
            if er:
                out["error"] = _net_error(er, missing_base) or str(er)
                return out
            msg = ((dt.get("error") or {}).get("message") if isinstance(dt.get("error"), dict) else dt.get("error")) \
                if isinstance(dt, dict) else ""
            if st in (401, 403):
                out["error"] = explain(st, dt, base=base, model=pm)
                return out
            named_model = st in (400, 404, 422) and "model" in str(msg or "").lower()
            if (st == 200 and _is_reply(w, dt)) or st == 429 or named_model:
                out.update(ok=True, wire=w, base_url=missing_base.rstrip("/"),
                           error="Connected, but this endpoint does not list models: type a model name.")
                return out
            if st == 200 and not note:
                note = _not_reply_msg(dt)
        out["error"] = (note + " " if note and not note.startswith("The address answered") else "") + \
            f"The address answered, but not like a model API ({last_status}). Check the address: it should end in " \
            "/v1 for most services."
    elif last_status >= 500:
        out["error"] = f"The server answered with an error ({last_status}). Try again in a moment."
    elif last_status == 402:
        out["error"] = "The server asks for payment (402): add credit or check the billing for this key."
    elif last_status == 429:
        out["error"] = "The server is limiting requests right now (429). Try again in a moment."
    elif last_status:
        out["error"] = (f"The server answered {last_status}, not a model list. Check the address: it should end in "
                        "/v1 for most services.")
    else:
        out["error"] = "No server at that address. Check the address and your connection."
    return out


def _remember(base: str, key: str, models: list) -> None:
    with _LOCK:
        _TESTS[(base.rstrip("/").lower(), _fp(key))] = {"t": time.time(), "models": [m["id"] for m in models]}
        if len(_TESTS) > 20:
            for k in sorted(_TESTS, key=lambda k: _TESTS[k]["t"])[:-20]:
                _TESTS.pop(k, None)


# ---------- save ---------------------------------------------------------------------------------------------------

ENV_KEYS = {"ANTHROPIC_API_KEY": ("anthropic", ANTHROPIC_BASE), "OPENAI_API_KEY": ("openai", OPENAI_BASE)}


def current() -> dict:
    k = PV.key()
    return {"base_url": PV.key_base(), "wire": PV.key_wire(),
            "default_model": (C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or "").strip(),
            "key_set": bool(k), "key_hint": k[-4:] if len(k) >= 8 else "",
            "provider": (C.read().get("MH_PROVIDER") or os.environ.get("MH_PROVIDER") or "meshpool"),
            "host": PV.host(PV.key_base()), "azure": PV.is_azure(PV.key_base()),
            "auth_header": PV._cfg("MH_AUTH_HEADER"), "auth_scheme": PV._cfg("MH_AUTH_SCHEME"),
            "api_version": PV._cfg("MESHPOOL_AZURE_API_VERSION")}


def save(base_url: str, api_key=None, wire: str = "auto", default_model: str = "", allow_custom: bool = False,
         env: str = "", confirm_env: bool = False, auth_header: str | None = None, auth_scheme: str | None = None,
         api_version: str | None = None) -> dict:
    base = normalise_base(base_url)
    if auth_header is not None:
        auth_header = auth_header.strip()
        if auth_header and not PV._HEADER_NAME.match(auth_header):
            raise SetupError("The key header name may only hold letters, digits and - _ . (for example api-key).")
    if auth_scheme is not None:
        auth_scheme = auth_scheme.strip().lower()
        if auth_scheme not in PV.SCHEMES:
            raise SetupError("The key scheme must be bearer, basic, token or raw.")
    if api_version is None and "api-version=" in str(base_url):
        api_version = PV.azure_version(str(base_url))
    model = re.sub(r"[\r\n]", "", str(default_model or "")).strip()
    if not model:
        raise SetupError("Pick the default model first.")
    if env and not confirm_env:
        raise SetupError(f"Confirm that Meshpool may copy {env} into its own settings.")
    key = resolve_key(base, api_key, env)
    if not key and not is_local(base):
        raise SetupError(f"Enter the API key for {PV.host(base)}.")
    prob = PV.key_problem(key)
    if prob:
        raise SetupError(prob)
    fresh = _TESTS.get((base.rstrip("/").lower(), _fp(key)))
    if fresh and time.time() - fresh["t"] < FRESH_S and fresh["models"] and model not in fresh["models"] \
            and not allow_custom:
        raise SetupError(f"{model} is not in this endpoint's model list. Pick one from the list, or confirm that "
                         "you typed it on purpose.")
    wire = (wire or "auto").lower()
    if wire not in ("openai", "anthropic"):
        wire = "anthropic" if PV.host(base).endswith("anthropic.com") else "openai"
    changed = (not _same_endpoint(base, PV.key_base())) or (key and key != PV.key())
    vals = {"MESHPOOL_BASE_URL": base, "MH_API_WIRE": wire, "MH_MODEL": model,
            "MH_PROVIDER": "meshpool" if "meshpool" in PV.host(base) else "custom"}
    if key:
        vals["MESHPOOL_API_KEY"] = key
    for k, v in (("MH_AUTH_HEADER", auth_header), ("MH_AUTH_SCHEME", auth_scheme),
                 ("MESHPOOL_AZURE_API_VERSION", None if api_version is None else api_version.strip())):
        if v is not None:
            vals[k] = v
    if changed and (C.read().get("MH_SUBAGENT_MODEL") or "").strip():
        vals["MH_SUBAGENT_MODEL"] = ""                  # named a model of the old endpoint
    C.write(vals)                                        # one atomic replace of the settings file
    for k, v in vals.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    try:                                                 # the picker's cached list belonged to the old endpoint
        from meshpool_harness.ui import workbench as W
        W._MODELS.update(t=0, key="", rows=[], error="")
    except Exception:  # noqa: BLE001
        pass
    with _LOCK:
        for k in [k for k in _TESTS if k[0] != base.rstrip("/").lower() or k[1] != _fp(key)]:
            _TESTS.pop(k, None)
    return current()


# ---------- logins found on this computer (read-only, no network) -----------------------------------------------------

def _claims(jwt: str) -> dict:
    from meshpool_harness.core import chatgpt as CG
    return CG._claims(jwt) if isinstance(jwt, str) and jwt.count(".") >= 2 else {}


def chatgpt_login() -> dict:
    from meshpool_harness.core import chatgpt as CG
    p = CG.auth_path()
    out = {"kind": "chatgpt", "found": False, "usable": False, "account": None, "plan": None,
           "active": PV.default_provider() == "chatgpt", "source": "Codex CLI"}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(d, dict):
        return out
    out["found"] = True
    tok = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
    if not (tok.get("access_token") and tok.get("refresh_token")):
        out["note"] = "Codex is signed in with an API key, not a ChatGPT account. Sign in with ChatGPT instead."
        return out
    out["usable"] = True
    idc = _claims(tok.get("id_token", ""))
    acc = _claims(tok.get("access_token", ""))
    out["account"] = idc.get("email") or (acc.get("https://api.openai.com/profile") or {}).get("email") or None
    auth = idc.get("https://api.openai.com/auth") or acc.get("https://api.openai.com/auth") or {}
    out["plan"] = auth.get("chatgpt_plan_type") or None
    return out


_VERSIONS: dict = {}


def claude_code() -> dict:
    out = {"kind": "claude_code", "found": False, "version": None, "path": None,
           "active": (C.read().get("MH_HARNESS_CODE") or "") == "claude-agent"}
    from meshpool_harness import harnesses as HR
    if HR.unsupported("claude-agent"):            # the installed app does not carry its adapter yet: not offered
        return out
    exe = shutil.which("claude")
    if not exe:
        return out
    out.update(found=True, path=exe)
    try:
        mt = os.stat(exe).st_mtime
        if _VERSIONS.get(exe, (0, ""))[0] != mt:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=8)
            m = re.search(r"\d+(?:\.\d+)+", r.stdout or "")
            _VERSIONS[exe] = (mt, m.group(0) if m else "")
        out["version"] = _VERSIONS[exe][1] or None
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def env_keys() -> list[dict]:
    rows = []
    for var, (wire, base) in ENV_KEYS.items():
        k = clean_key(os.environ.get(var, ""))
        base_env = os.environ.get("OPENAI_BASE_URL", "").strip() if var == "OPENAI_API_KEY" else ""
        rows.append({"kind": "env_key", "env": var, "found": bool(k), "key_hint": k[-4:] if len(k) >= 8 else "",
                     "wire": wire, "base_url": base_env or base,
                     "active": bool(k) and k == PV.key()})
    return rows


def logins() -> list[dict]:
    return [chatgpt_login(), claude_code(), *env_keys()]


def use(kind: str, **kw) -> dict:
    from meshpool_harness import harnesses as HR
    if kind == "chatgpt":
        info = chatgpt_login()
        if not info["found"]:
            raise SetupError("No ChatGPT sign-in found on this computer. Sign in with ChatGPT (it opens the Codex "
                             "sign-in), then try again.")
        if not info["usable"]:
            raise SetupError(info.get("note") or "That sign-in cannot be used.")
        HR.use_provider("chatgpt")
        from meshpool_harness.core import chatgpt as CG
        return {"ok": True, "kind": "chatgpt", "provider": "chatgpt", "account": info["account"],
                "default_model": "chatgpt:" + CG.default_model()}
    if kind == "claude_code":
        info = claude_code()
        if not info["found"]:
            raise SetupError("Claude Code is not installed on this computer (no `claude` command). Install it, sign "
                             "in once with `claude`, then try again.")
        C.write({"MH_HARNESS_CODE": "claude-agent"})
        os.environ["MH_HARNESS_CODE"] = "claude-agent"
        return {"ok": True, "kind": "claude_code", "harness": "claude-agent", "version": info["version"]}
    raise SetupError("Unknown sign-in. Use kind chatgpt or claude_code; for an API key from your environment, "
                     "test and save with env.")
