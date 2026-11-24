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
from meshpool_harness.core import endpoint as EP

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
OPENAI_BASE = "https://api.openai.com/v1"
FRESH_S = 600                       # how long a test result may vouch for a model list
_TESTS: dict = {}                   # (base, key fingerprint) -> {"t", "models"}
_LOCK = threading.Lock()
LOCAL = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


class SetupError(ValueError):
    pass


# ---------- helpers ------------------------------------------------------------------------------------------------

def clean_key(k) -> str:
    """A key the way people paste it: quoted, with 'Bearer ' in front, or with a zero-width space."""
    k = str(k or "").strip().strip("'\" ")
    k = re.sub(r"^(?:bearer\s+)", "", k, flags=re.I)
    return re.sub(r"[\s​-‏⁠﻿]", "", k)


def _fp(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""


def is_local(url: str) -> bool:
    return PV.host(url) in LOCAL or PV.host(url).endswith(".local")


def normalise_base(url: str) -> str:
    """https://host[/v1]: scheme added, trailing slash and a pasted endpoint path (/chat/completions, /models,
    /messages) removed, and the path each known provider needs added (/v1 for OpenAI, /openai/v1 for Groq,
    /v1beta/openai for Gemini ...). A pasted Azure call address is cut back to the resource (core/endpoint.py)."""
    return parse(url).base


def parse(url: str, kind: str = "", api_version: str = "") -> EP.Parsed:
    """The pasted address understood: base, provider kind, an Azure deployment and api-version found inside it."""
    try:
        return EP.parse_pasted(url, kind, api_version)
    except EP.EndpointError as e:
        raise SetupError(str(e)) from None


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
        return __import__("meshpool_harness.core.http", fromlist=["x"]).explain(
            ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"), base)
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
        with EP.urlopen(req, timeout, refuse_redirects=True) as r:
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
    """What a failed call means, in words the user can act on (core/endpoint.py decides by the kind of provider)."""
    kind, ver = EP.resolve(base)
    return EP.explain(status, data, kind=kind, host=PV.host(base), model=model, api_version=version or ver)


def probe_chat(base: str, key: str, model: str, wire: str = "openai", header: str = "", scheme: str = "",
               version: str = "", kind: str = "") -> tuple[int, object, str]:
    """One tiny chat call: (status, JSON or None, error kind). It exercises the real URL, key header and model."""
    if wire == "anthropic":
        body = AW.to_request({"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "ping"}]})
        res = _get(base.rstrip("/") + "/messages", _headers(wire, key, base, header, scheme), 30.0, body)
    else:
        kind, ver = (kind, "") if kind else EP.resolve(base)
        url, body, hdr = EP.chat_request(base, key, {"model": model, "max_tokens": 16,
                                                     "messages": [{"role": "user", "content": "ping"}]},
                                         kind=kind, api_version=version or ver)
        if header or scheme:
            hdr = {k: v for k, v in hdr.items() if k.lower() not in ("authorization", "api-key", "x-api-key")}
            hdr.update(_headers(wire, key, base, header, scheme))
        res = _get(url, hdr, 30.0, body)
    _account_probe(res, base, model)
    return res


def _account_probe(res: tuple, base: str, model: str) -> None:
    """A test call that got an answer is billed like any other: put its usage in the ledger (source "check")."""
    try:
        status, data, _ = res
        if status == 200 and isinstance(data, dict):
            from meshpool_harness.core import usage
            usage.record(model, data.get("usage") or None, base=base, source="check", request_chars=4)
    except Exception:  # noqa: BLE001 - accounting never breaks a connection test
        pass


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
    """The key to use: the one typed, the named environment variable (only for the address it belongs to), or (same
    endpoint) the saved one."""
    k = clean_key(api_key)
    if k:
        return k
    if env:
        if env not in ENV_KEYS:
            raise SetupError("Unknown environment variable.")
        row = next((r for r in EP.env_sources(os.environ) if r["var"] == env), None)
        if row and PV.host(row["base"]) != PV.host(base):
            raise SetupError(f"{env} is the key for {PV.host(row['base'])} and is not sent anywhere else.")
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


def test(base_url: str, api_key=None, wire: str = "auto", env: str = "", model: str = "", api_version: str = "",
         kind: str = "", auth_header: str = "", auth_scheme: str = "") -> dict:
    """Check an address and key. Where the endpoint has a model list (most do) the key is checked by reading it. Azure,
    and any endpoint with no list, has none to read: there the check is a one-token chat call to `model` (for Azure
    the deployment name), and without a model the answer says so (needs_model) rather than calling the key bad."""
    t0 = time.time()
    pp = parse(base_url, kind, api_version)
    base, kind = pp.base, pp.kind
    caps = EP.caps(kind)
    ver = (api_version or pp.api_version or "").strip()
    asked_v1 = "/openai/v1" in (base_url or "").lower()
    model = (model or pp.deployment or "").strip()
    out = {"ok": False, "wire": "", "base_url": base, "models": [], "latency_ms": 0, "error": "", "kind": kind,
           "label": caps["label"], "api_version": ver or caps["version"], "deployment": bool(caps["deployment"]),
           "needs_model": False, "model": model}
    key = resolve_key(base, api_key, env)
    host = PV.host(base)
    if not key and not is_local(base) and not caps["keyless"]:
        out["error"] = f"Enter the API key for {host}." if not PV.key() or _same_endpoint(base, PV.key_base()) \
            else f"Enter the API key for {host}: your saved key is for {PV.host(PV.key_base())} and is not sent " \
                 "anywhere else."
        return out
    prob = PV.key_problem(key)
    if prob:
        out["error"] = prob
        return out

    def probe_now() -> dict:
        pr = EP.probe(base, key, model, kind=kind, api_version=ver)
        if not pr["ok"] and pr["status"] == 404 and kind == "azure-v1" and not asked_v1 \
                and EP._azure_family(host) in ("classic", "cognitive"):
            # An older resource without the /openai/v1 route: the same address works on the deployment route. Try that
            # before telling the user anything is wrong, and remember it so Save uses the route that worked.
            origin = base[:-len("/openai/v1")] if base.endswith("/openai/v1") else base
            old = EP.probe(origin, key, model, kind="azure-classic", api_version=EP.AZURE_DEFAULT_VERSION)
            if old["ok"]:
                with _LOCK:
                    _FALLBACK[(base.rstrip("/").lower(), _fp(key))] = {"kind": "azure-classic", "base": origin,
                                                                      "version": EP.AZURE_DEFAULT_VERSION}
                out.update(kind="azure-classic", base_url=origin, api_version=EP.AZURE_DEFAULT_VERSION,
                           label=EP.caps("azure-classic")["label"])
                pr = old
        out["latency_ms"] = int((time.time() - t0) * 1000)
        if pr["ok"]:
            out.update(ok=True, wire=caps["wire"], models=[{"id": model}], error="")
            _remember(base, key, out["models"])
        else:
            out["error"] = pr["error"]
        return out

    if caps["deployment"] or not caps["lists"]:
        if not model:
            out["needs_model"] = True
            out["error"] = ("Enter the deployment name (the name you gave the model in Azure, not the model's own "
                            "name) so the key can be tested." if caps["deployment"] else
                            f"{caps['label']} has no model list: enter a model name so the key can be tested.")
            return out
        return probe_now()
    wire = (wire or "auto").lower()
    if wire not in ("openai", "anthropic"):
        wire = "auto"
    order = [wire] if wire != "auto" else (["anthropic", "openai"] if host.endswith("anthropic.com")
                                           or caps["wire"] == "anthropic" else ["openai", "anthropic"])
    auth_code = 0
    saw_missing = False
    missing_base = ""
    last_status = 0
    for b in _candidates(base):
        for w in order:
            status, data, err = _get(b.rstrip("/") + "/models", _headers(w, key, b, auth_header.strip(), auth_scheme.strip().lower()))
            if err == "tls" and urlparse(b).scheme == "https" and PV.keyless_ok(b) and not key:
                # a local server speaking plain http, tried over http only with no key: a keyed request is never
                # downgraded unless the user typed http:// (the error below tells them how)
                plain = "http://" + base.split("://", 1)[1]
                return test(plain, api_key, wire, env, model, api_version, kind, auth_header, auth_scheme)
            if err:
                out["error"] = _net_error(err, b) or "No server at that address. Check the address and your connection."
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
            elif status in (404, 405) or (status == 200 and items is None):
                saw_missing = True
                missing_base = missing_base or b
    out["latency_ms"] = int((time.time() - t0) * 1000)
    if auth_code:
        out["error"] = EP.explain(auth_code, None, kind=kind, host=host, key=key)
    elif saw_missing and model:          # no list, but a model was typed: a chat call proves the key
        return probe_now()
    elif saw_missing:
        # Something answers but lists no models (or answered 200 with an error body or a web page): a tiny real call
        # per wire finds the wire and checks the key. A 200 that is not a model's reply never counts as connected.
        pm = "meshpool-connection-test"
        hdr_name, hdr_scheme = auth_header.strip(), auth_scheme.strip().lower()
        note = ""
        for w in order:
            st, dt, er = probe_chat(missing_base, key, pm, w, hdr_name, hdr_scheme, kind=kind)
            if er:
                out["error"] = _net_error(er, missing_base) or str(er)
                return out
            msg = ((dt.get("error") or {}).get("message") if isinstance(dt.get("error"), dict) else dt.get("error")) \
                if isinstance(dt, dict) else ""
            if st in (401, 403):
                out["error"] = EP.explain(st, dt, kind=kind, host=host, key=key)
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


_FALLBACK: dict = {}      # (v1 base, key fingerprint) -> the older Azure route that answered where /openai/v1 did not


def _remember(base: str, key: str, models: list) -> None:
    with _LOCK:
        _TESTS[(base.rstrip("/").lower(), _fp(key))] = {"t": time.time(), "models": [m["id"] for m in models]}
        if len(_TESTS) > 20:
            for k in sorted(_TESTS, key=lambda k: _TESTS[k]["t"])[:-20]:
                _TESTS.pop(k, None)


# ---------- save ---------------------------------------------------------------------------------------------------

ENV_KEYS = {v: (EP.wire_of(kind), fixed) for kind, kvars, _b, fixed in EP.ENV_SOURCES for v in kvars}
ENV_KEYS["OPENAI_API_KEY"] = ("openai", OPENAI_BASE)      # a bare OpenAI key is offered for api.openai.com only


def current() -> dict:
    k = PV.key()
    kind = PV.key_kind()
    return {"base_url": PV.key_base(), "wire": PV.key_wire(),
            "default_model": (C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or "").strip(),
            "key_set": bool(k), "key_hint": k[-4:] if len(k) >= 8 else "",
            "provider": (C.read().get("MH_PROVIDER") or os.environ.get("MH_PROVIDER") or "meshpool"),
            "host": PV.host(PV.key_base()), "azure": EP.is_azure(kind) or PV.is_azure(PV.key_base()), "kind": kind, "label": EP.caps(kind)["label"],
            "api_version": PV.key_api_version(), "deployment": EP.caps(kind)["deployment"],
            "auth_header": PV._ecfg("MH_AUTH_HEADER"), "auth_scheme": PV._ecfg("MH_AUTH_SCHEME"),
            "agent_notes": {h: PV.direct_agent_ok(h) for h in ("codex",) if PV.direct_agent_ok(h)}}


def catalog() -> list[dict]:
    return EP.catalog()


def save(base_url: str, api_key=None, wire: str = "auto", default_model: str = "", allow_custom: bool = False,
         env: str = "", confirm_env: bool = False, api_version: str = "", kind: str = "",
         auth_header: str | None = None, auth_scheme: str | None = None) -> dict:
    if auth_header is not None:
        auth_header = auth_header.strip()
        if auth_header and not PV._HEADER_NAME.match(auth_header):
            raise SetupError("The key header name may only hold letters, digits and - _ . (for example api-key).")
    if auth_scheme is not None:
        auth_scheme = auth_scheme.strip().lower()
        if auth_scheme not in PV.SCHEMES:
            raise SetupError("The key scheme must be bearer, basic, token or raw.")
    pp = parse(base_url, kind, api_version)
    base, kind = pp.base, pp.kind
    ver = (api_version or pp.api_version or "").strip()
    model = re.sub(r"[\r\n]", "", str(default_model or "") or pp.deployment).strip()
    if not model:
        raise SetupError("Pick the default model first.")
    if env and not confirm_env:
        raise SetupError(f"Confirm that Meshpool may copy {env} into its own settings.")
    key = resolve_key(base, api_key, env)
    if not key and not is_local(base) and not EP.caps(kind)["keyless"]:
        raise SetupError(f"Enter the API key for {PV.host(base)}.")
    prob = PV.key_problem(key)
    if prob:
        raise SetupError(prob)
    fresh = _TESTS.get((base.rstrip("/").lower(), _fp(key)))
    if fresh and time.time() - fresh["t"] < FRESH_S and fresh["models"] and model not in fresh["models"] \
            and not allow_custom:
        raise SetupError(f"{model} is not in this endpoint's model list. Pick one from the list, or confirm that "
                         "you typed it on purpose.")
    fb = _FALLBACK.get((base.rstrip("/").lower(), _fp(key)))
    if fb and kind == "azure-v1" and "/openai/v1" not in (base_url or "").lower():
        base, kind, ver = fb["base"], fb["kind"], fb["version"]      # the route the test found working
    wire = (wire or "auto").lower()
    if wire not in ("openai", "anthropic"):
        wire = "anthropic" if PV.host(base).endswith("anthropic.com") else EP.wire_of(kind)
    moved = not _same_endpoint(base, PV.key_base())
    changed = moved or (key and key != PV.key())
    # The kind and api-version belong to this address only: saved for an Azure endpoint, blanked for any other, so
    # an old api-version never follows the user to another host.
    vals = {"MESHPOOL_BASE_URL": base, "MH_API_WIRE": wire, "MH_MODEL": model,
            "MH_PROVIDER": "meshpool" if "meshpool" in PV.host(base) else "custom",
            "MH_API_KIND": "" if kind == "generic" else kind,
            "MH_API_VERSION": ver if kind in ("azure-classic", "azure-foundry") else ""}
    if key:
        vals["MESHPOOL_API_KEY"] = key
    else:                                                # a keyless local endpoint: the old endpoint's key goes
        vals["MESHPOOL_API_KEY"] = ""                    # with it, never to this server
    for k, v in (("MH_AUTH_HEADER", auth_header), ("MH_AUTH_SCHEME", auth_scheme)):
        if v is not None:
            vals[k] = v
        elif moved:                                      # the old endpoint's header and scheme belong to it alone,
            vals[k] = ""                                 # like its api-version: never sent with another key
    if changed and (C.read().get("MH_SUBAGENT_MODEL") or "").strip():
        vals["MH_SUBAGENT_MODEL"] = ""                  # named a model of the old endpoint
    C.write(vals)                                        # one atomic replace of the settings file
    for k, v in vals.items():                            # Meshpool's own copy from now on: a later
        if v:                                            # load_into_env(override=True) must not take these
            os.environ[k] = v                            # for values the user's shell set
            C._INJECTED.add(k)
        else:
            os.environ.pop(k, None)
            C._INJECTED.discard(k)
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
    st = CG.signin_state()
    if not st["ok"]:
        out["note"] = st["reason"]              # e.g. ChatGPT refused the refresh token: sign in again
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
    """Provider keys found in the environment, each with the one address it may go to (never offered for another)."""
    found = {r["var"]: r for r in EP.env_sources(os.environ)}
    rows = []
    for var, (wire, base) in ENV_KEYS.items():
        k = clean_key(os.environ.get(var, ""))
        r = found.get(var)
        if not r and var not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            continue                                          # only the two long-standing rows show when not set
        kind = r["kind"] if r else ("anthropic" if wire == "anthropic" else "openai")
        rows.append({"kind": "env_key", "env": var, "found": bool(k and (r or var in ENV_KEYS)),
                     "key_hint": k[-4:] if len(k) >= 8 else "", "wire": EP.wire_of(kind),
                     "base_url": (r["base"] if r else base), "provider": kind, "label": EP.caps(kind)["label"],
                     "api_version": r["api_version"] if r else "", "deployment": r["deployment"] if r else "",
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
