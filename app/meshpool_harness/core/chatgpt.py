"""ChatGPT subscription as a model provider for Meshpool's own agents (MH_PROVIDER=chatgpt).

The user signs in once with the official Codex sign-in (`codex login`, which writes ~/.codex/auth.json). Meshpool
then sends its model calls to the ChatGPT Codex endpoint with that sign-in, the way open-source harnesses such as
Codex CLI (Apache-2.0), OpenCode and Hermes Agent (MIT) do:

  POST https://chatgpt.com/backend-api/codex/responses     Responses API, stream: true, store: false
  Authorization: Bearer <access_token>                     from ~/.codex/auth.json
  ChatGPT-Account-ID: <chatgpt_account_id>                 from the token's "https://api.openai.com/auth" claim
  originator: meshpool, User-Agent: Meshpool/<version>     OpenAI asks third-party harnesses to name themselves

Token refresh follows Codex: refresh shortly before the access token expires, and once on a 401, at
https://auth.openai.com/oauth/token with the refresh token. Refresh tokens are single-use and shared with the Codex
CLI, so the refresh runs under a file lock, re-reads auth.json first, adopts a pair another program already rotated,
and writes the new pair back to auth.json so Codex keeps working too.

Scope: OpenAI allows this for local and open-source apps and asks them to move to "Sign in with ChatGPT" (SIWC);
it has never been allowed for hosted or commercial services. Meshpool runs it only on the user's own computer, with
the user's own sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from meshpool_harness.core import http as _HTTP
from meshpool_harness import fsio

BASE_URL = "https://chatgpt.com/backend-api/codex"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"        # the public Codex CLI OAuth client its auth.json tokens belong to
REFRESH_SKEW_S = 120                               # refresh this long before the access token expires
REFRESH_EVERY_S = 8 * 24 * 3600                    # Codex also refreshes a token this old, whatever its expiry
DEFAULT_MODEL = "gpt-6-luna"                      # used only when the plan's model list cannot be read
# The model list only shows models for clients at or above each model's minimum Codex version, and needs a version in
# Codex's format. Meshpool sends the Codex release whose request format this wire matches (checked live with Codex
# 0.169.0); it still names itself with originator: meshpool.
CODEX_COMPAT_VERSION = "0.169.0"
# Pi and Hermes reach the sign-in through the local relay (chatgpt_relay.py), so this process stays its only refresher.
LOCK_TIMEOUT_S = 60                                # longest wait for another Meshpool process that is refreshing
REUSED_WAIT_S = 3.0                                # how long to wait for another program's rotated pair to land
TORN_WAIT_S = 2.0                                  # how long to retry a half-written auth.json
EXPIRED_MSG = "Your ChatGPT sign-in expired. Sign in again: Settings, Account, Runs on."
NOT_SIGNED_MSG = "Not signed in to ChatGPT on this computer. Sign in: Settings, Account, Runs on."
API_KEY_MSG = ("Codex on this computer is signed in with an API key, not a ChatGPT account. Sign in "
               "with ChatGPT: Settings, Account, Runs on.")


class NotSignedIn(RuntimeError):
    pass


class SignInBusy(RuntimeError):
    """The sign-in file is mid-rewrite or another program holds the refresh. Transient ("HTTP 0"): retry, not sign in."""


class UsageLimit(RuntimeError):
    """The ChatGPT plan's usage limit is reached. Not retried: it resets in hours or days, not seconds."""
    def __init__(self, msg: str, resets_at: float | None = None, plan: str = ""):
        super().__init__(msg)
        self.resets_at, self.plan = resets_at, plan


_LIMIT: dict = {}                                  # account -> {"resets_at", "plan"}: the last usage limit seen


def _when(ts: float | None) -> str:
    if not ts:
        return "later"
    left = ts - time.time()
    if left <= 0:
        return "now"
    day = time.strftime("%d %b", time.localtime(ts)).lstrip("0")
    if left < 3600:
        return f"in {max(1, int(left // 60))} minutes"
    if left < 86400:
        return f"in {int(left // 3600)} hours"
    return f"in {int(left // 86400)} days, on {day}"


def _note(ent: dict | None) -> str:
    ts = (ent or {}).get("resets_at")
    if not ts or ts <= time.time():
        return ""
    plan = f"ChatGPT {ent['plan'].title()}" if ent.get("plan") else "your ChatGPT plan"
    return f"Usage limit reached on {plan}. It resets {_when(ts)}."


def limit_note() -> str:
    """A short note for the model menu while the signed-in account's limit is reached, else ''. Per account, so
    signing in to another account does not inherit the first one's limit."""
    return _note(_LIMIT.get(_current_account()))


def _usage_limit(code: int, detail: str, account: str = "") -> UsageLimit | None:
    if code != 429:
        return None
    try:
        err = (json.loads(detail) or {}).get("error") or {}
    except (ValueError, AttributeError):
        return None
    if err.get("type") != "usage_limit_reached":
        return None
    ts = err.get("resets_at") or (time.time() + err["resets_in_seconds"] if err.get("resets_in_seconds") else None)
    ent = _LIMIT[account] = {"resets_at": ts, "plan": str(err.get("plan_type") or "")}
    return UsageLimit(_note(ent) + " Pick another provider in the model menu to keep working.", ts, ent["plan"])


def base_url() -> str:
    return (os.environ.get("MH_CHATGPT_BASE_URL") or BASE_URL).rstrip("/")


def token_url() -> str:
    return os.environ.get("MH_CHATGPT_TOKEN_URL") or TOKEN_URL


def auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("meshpool-harness")
    except Exception:  # noqa: BLE001
        return "0"


def _claims(jwt: str) -> dict:
    try:
        part = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        return {}


def account_headers(access_token: str, tokens: dict | None = None) -> dict:
    auth = _claims(access_token).get("https://api.openai.com/auth") or {}
    if not auth and tokens and tokens.get("id_token"):
        auth = _claims(tokens["id_token"]).get("https://api.openai.com/auth") or {}
    out = {}
    acct = auth.get("chatgpt_account_id") or (tokens or {}).get("account_id")
    if acct:
        out["ChatGPT-Account-ID"] = str(acct)
    res = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    if res:
        out["x-openai-internal-codex-residency"] = str(res).strip()   # residency-bound workspaces 401 without it
    return out


def _account(tokens: dict | None) -> str:
    """The ChatGPT account a token pair belongs to ('' if it cannot tell)."""
    tokens = tokens or {}
    for jwt in (tokens.get("access_token"), tokens.get("id_token")):
        acct = (_claims(jwt).get("https://api.openai.com/auth") or {}).get("chatgpt_account_id") if jwt else None
        if acct:
            return str(acct)
    return str(tokens.get("account_id") or "")


def _current_account() -> str:
    """The account in auth.json now, '' if not signed in or unreadable (never raises, never waits)."""
    d = _peek()
    tok = d.get("tokens") if isinstance(d, dict) else None
    return _account(tok) if isinstance(tok, dict) else ""


# ---------- the sign-in in ~/.codex/auth.json ------------------------------------------------------------------

def _peek():
    """auth.json read once, without waiting: a dict, None if the file is missing, {} if it is damaged."""
    try:
        d = fsio.read_json(auth_path())
    except fsio.CorruptFile:
        return {}
    return d if d is None or isinstance(d, dict) else {}


def _load() -> dict:
    """auth.json as a dict. Missing -> NotSignedIn. Half-written (Codex or another Meshpool is replacing it) ->
    retried for TORN_WAIT_S, then SignInBusy: a transient error, never a reason to ask the user to sign in."""
    deadline = time.monotonic() + TORN_WAIT_S
    while True:
        try:
            d = fsio.read_json(auth_path())
            if d is None:
                raise NotSignedIn(NOT_SIGNED_MSG)
            if isinstance(d, dict):
                return d
            why = "not a JSON object"
        except fsio.CorruptFile as e:
            why = str(e.cause)
        if time.monotonic() >= deadline:
            raise SignInBusy(f"chatgpt HTTP 0: the ChatGPT sign-in file could not be read ({why}). "
                             "Try again in a moment.")
        time.sleep(0.1)


def _tokens(d: dict) -> dict:
    tokens = d.get("tokens")
    return tokens if isinstance(tokens, dict) else {}


def _read() -> dict:
    d = _load()
    t = _tokens(d)
    if not t.get("access_token") or not t.get("refresh_token"):
        raise NotSignedIn(API_KEY_MSG)
    return d


def _revoked_path() -> Path:
    return auth_path().with_name("auth.json.meshpool-revoked")


def _fingerprint(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:32]


def _mark_revoked(refresh_token: str) -> None:
    """Remember that ChatGPT rejected this refresh token, by fingerprint only (the token itself is never written), so
    the app can say 'sign in again' instead of showing the sign-in as ready. A new sign-in has a new token."""
    try:
        fsio.atomic_write_json(_revoked_path(), {"refresh_fp": _fingerprint(refresh_token)}, private=True)
    except OSError:
        pass


def _is_revoked(refresh_token: str) -> bool:
    try:
        rec = fsio.read_json(_revoked_path())
    except fsio.CorruptFile:
        return False
    return isinstance(rec, dict) and rec.get("refresh_fp") == _fingerprint(refresh_token)


def signin_state() -> dict:
    """{"ok", "reason"}: whether the ChatGPT sign-in can work, without refreshing or sending anything."""
    try:
        d = _load()
    except NotSignedIn:
        return {"ok": False, "reason": "not signed in"}
    except SignInBusy:
        return {"ok": False, "reason": "The ChatGPT sign-in file could not be read right now."}
    t = _tokens(d)
    if not t.get("access_token") or not t.get("refresh_token"):
        return {"ok": False, "reason": API_KEY_MSG}
    if _is_revoked(t["refresh_token"]):
        return {"ok": False, "reason": EXPIRED_MSG}
    return {"ok": True, "reason": ""}


def _stale(d: dict) -> bool:
    exp = _claims(d["tokens"]["access_token"]).get("exp")
    if isinstance(exp, (int, float)):
        return exp - time.time() < REFRESH_SKEW_S
    last = str(d.get("last_refresh") or "")
    try:
        from datetime import datetime
        t = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        return time.time() - t > REFRESH_EVERY_S
    except ValueError:
        return False


def _adopt_rotated(sent: str, code: int, detail: str) -> dict:
    """The token endpoint refused our refresh token. Another program (the Codex CLI does not take our lock) may have
    rotated the pair just before: poll auth.json briefly and take its pair. Unchanged -> the sign-in is really dead."""
    end = time.monotonic() + REUSED_WAIT_S
    while True:
        cur = _peek()
        if cur is None:
            raise NotSignedIn("You signed out of ChatGPT. Sign in again: Settings, Account, Runs on.")
        t = _tokens(cur)
        if t.get("access_token") and t.get("refresh_token") and t["refresh_token"] != sent:
            return cur
        if time.monotonic() >= end:
            break
        time.sleep(0.2)
    _mark_revoked(sent)
    raise NotSignedIn(f"Your ChatGPT sign-in expired or was used up by another program. Sign in again: "
                      f"Settings, Account, Runs on. ({code}: {detail})")


def _refresh(d: dict) -> dict:
    """Refresh under the lock; d was read after taking it. Compare-and-swap: the new pair is written only if
    auth.json still holds the refresh token we sent, so a sign-out or a sign-in to another account during the
    request is never undone."""
    sent = d["tokens"]["refresh_token"]
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": sent,
                                   "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(token_url(), data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        "User-Agent": f"Meshpool/{_version()}"})
    try:
        with _HTTP.open(req, 30) as r:
            new = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (400, 401, 403):
            return _adopt_rotated(sent, e.code, detail)
        raise RuntimeError(f"chatgpt HTTP {e.code}: token refresh failed: {detail}")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError, ValueError) as e:
        raise RuntimeError(f"chatgpt HTTP 0: could not reach the ChatGPT sign-in server to refresh the sign-in: {e}")
    if not isinstance(new, dict) or not new.get("access_token"):
        raise RuntimeError("chatgpt HTTP 0: the ChatGPT sign-in server returned no access token")
    try:
        cur = _read()
    except NotSignedIn:
        if _peek() is None:
            raise NotSignedIn("You signed out of ChatGPT while Meshpool was refreshing it. "
                              "Sign in again: Settings, Account, Runs on.")
        raise
    if cur["tokens"]["refresh_token"] != sent:
        return cur                                      # another program or account got there first: keep theirs
    tok = dict(cur["tokens"], access_token=new["access_token"])
    for k in ("refresh_token", "id_token"):
        if new.get(k):
            tok[k] = new[k]
    cur = dict(cur, tokens=tok, last_refresh=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    fsio.atomic_write_json(auth_path(), cur, indent=2, private=True)   # owner-only from the first byte
    return cur


def _locked_refresh(force: bool, failed: str) -> dict:
    with fsio.file_lock(auth_path().with_name("auth.json.meshpool"), timeout=LOCK_TIMEOUT_S):
        d = _read()                                   # someone may have refreshed while we waited
        cur = d["tokens"]["access_token"]
        if (force and failed and cur != failed) or (not force and not _stale(d)):
            return d
        return _refresh(d)


def credentials(force: bool = False, failed: str = "") -> tuple[str, dict]:
    """(access_token, tokens) ready to use. force: the endpoint said 401 for `failed`, refresh unless another
    program already did."""
    d = _read()
    if not force and not _stale(d):
        return d["tokens"]["access_token"], d["tokens"]
    try:
        d = _locked_refresh(force, failed)
    except TimeoutError:                              # never refresh without the lock: a second refresher burns the pair
        d = _read()
        cur = d["tokens"]["access_token"]
        if not ((force and failed and cur != failed) or (not force and not _stale(d))):
            raise SignInBusy("chatgpt HTTP 0: another program is refreshing the ChatGPT sign-in. "
                             "Try again in a moment.")
    return d["tokens"]["access_token"], d["tokens"]


# ---------- the wire --------------------------------------------------------------------------------------------

def _parts(content) -> list | str:
    """Chat-completions content -> Responses input content (text and images)."""
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if c.get("type") == "text":
            out.append({"type": "input_text", "text": c.get("text", "")})
        elif c.get("type") == "image_url":
            url = c.get("image_url")
            out.append({"type": "input_image", "image_url": url.get("url") if isinstance(url, dict) else url})
    return out


def to_request(body: dict, model: str) -> dict:
    """A chat-completions request -> the ChatGPT Codex Responses request. System messages become `instructions`
    (the endpoint rejects system items); output limits and temperature are not accepted there."""
    instructions, items = [], []
    for m in body.get("messages", []):
        role = m.get("role")
        if role == "system":
            instructions.append(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""),
                          "output": m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))})
            continue
        if m.get("content"):
            if role == "assistant":
                text = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
                items.append({"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": text}]})
            else:
                items.append({"type": "message", "role": "user" if role != "developer" else "developer",
                              "content": _parts(m["content"])})
        for c in m.get("tool_calls") or []:
            f = c.get("function", {})
            items.append({"type": "function_call", "call_id": c.get("id", ""), "name": f.get("name", ""),
                          "arguments": f.get("arguments") or "{}"})
    out = {"model": model, "instructions": "\n\n".join(instructions) or "You are a helpful assistant.",
           "input": items, "store": False, "stream": True}
    if body.get("tools"):
        out["tools"] = [{"type": "function", "name": t["function"]["name"],
                         "description": t["function"].get("description", ""),
                         "parameters": t["function"].get("parameters", {})} for t in body["tools"]]
        out["tool_choice"] = body.get("tool_choice", "auto")
    if body.get("prompt_cache_key"):
        out["prompt_cache_key"] = body["prompt_cache_key"]
    if body.get("reasoning_effort"):
        out["reasoning"] = {"effort": body["reasoning_effort"]}
    if (body.get("response_format") or {}).get("type") == "json_object":
        out["text"] = {"format": {"type": "json_object"}}
    return out


def read_stream(lines, on_text=None, data_idle=None, check=None) -> dict:
    """SSE events -> the final Responses object (from response.completed, or rebuilt from the output items).
    on_text gets each piece of reply text as it arrives."""
    done, items, text, err, ended = None, [], [], None, False
    from meshpool_harness.core.stream import iter_sse
    for _name, data in iter_sse(lines, data_idle=data_idle, check=check):
        data = data.strip()
        if data == "[DONE]":
            ended = True
            break
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type", "")
        if t == "response.output_text.delta":
            text.append(ev.get("delta", ""))
            if on_text and ev.get("delta"):
                on_text(ev["delta"])
        elif t == "response.output_item.done" and ev.get("item"):
            items.append(ev["item"])
        elif t in ("response.completed", "response.done", "response.incomplete"):
            done = ev.get("response") or {}
        elif t in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
    if err and not done:
        body = json.dumps(err)[:400]
        low = body.lower()
        code = 500                                   # an error event after HTTP 200: its code says whether a retry can help
        if any(w in low for w in ("rate_limit", "usage_limit", "insufficient_quota", "quota_exceeded")):
            code = 429
        elif any(w in low for w in ("context_length", "invalid_request", "invalid_prompt", "string_above_max")):
            code = 400
        raise RuntimeError(f"chatgpt HTTP {code}: {body}")
    if done is None and not ended:                   # closed mid-reply: never pass a cut-off reply on as whole
        from meshpool_harness.core.stream import StreamCut
        raise StreamCut("ChatGPT sent no response.completed")
    done = done or {}
    if not done.get("output"):
        done["output"] = items or ([{"type": "message", "content": [{"type": "output_text", "text": "".join(text)}]}]
                                   if text else [])
    return done


_MODELS: dict = {}                                 # account -> {"t", "rows"}: the plan's model ids


def forget_models() -> None:
    _MODELS.clear()


def _known() -> list:
    return (_MODELS.get(_current_account()) or {}).get("rows") or []


def models() -> list[str]:
    """Model ids the user's ChatGPT plan offers (the Codex model list), cached for five minutes per account."""
    ent = _MODELS.get(_current_account())
    if ent and ent["rows"] and time.time() - ent["t"] < 300:
        return ent["rows"]
    acct = ""
    try:
        tok, tokens = credentials()
        acct = _account(tokens)
        req = urllib.request.Request(f"{base_url()}/models?client_version={CODEX_COMPAT_VERSION}", headers={
            "Authorization": f"Bearer {tok}", "originator": "meshpool", "User-Agent": f"Meshpool/{_version()}",
            **account_headers(tok, tokens)})
        with _HTTP.open(req, 20) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data.get("models") or data.get("data") or []
        ids = [m.get("slug") or m.get("id") for m in rows if isinstance(m, dict)
               and m.get("visibility", "list") != "hide" and (m.get("slug") or m.get("id"))]
    except Exception:  # noqa: BLE001
        ids = []
    if ids:
        _MODELS[acct] = {"t": time.time(), "rows": ids}
    return ids


def default_model() -> str:
    """The model chosen in the picker if the plan still offers it, else the first model the plan lists."""
    from meshpool_harness import config as C
    m = (C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or "").strip()
    want = m[8:] if m.startswith("chatgpt:") else (C.read().get("MH_CHATGPT_MODEL") or os.environ.get("MH_CHATGPT_MODEL") or "")
    known = _known()
    if want and (not known or want in known):
        return want.strip()
    return known[0] if known else DEFAULT_MODEL


def resolve_model(model: str | None) -> str:
    """A Meshpool or other provider's model id means nothing to ChatGPT: use the ChatGPT default instead."""
    m = (model or "").strip()
    known = _known()
    if m and (m in known or (not known and m.lower().startswith(("gpt-", "o1", "o3", "o4", "codex")))):
        return m
    return default_model()


def chat(body: dict, task_id: str = "", timeout: int = 600, idle: int = 120, on_text=None) -> dict:
    """One model call on the ChatGPT sign-in. Returns the Responses object; core.wire turns it into chat shape."""
    req_body = to_request(body, resolve_model(body.get("model")))
    data = json.dumps(req_body).encode()
    session = (task_id.split("-esc")[0] if task_id else "") or str(uuid.uuid4())
    failed = ""
    for attempt in range(2):
        tok, tokens = credentials(force=bool(failed), failed=failed)
        account = _account(tokens)
        req = urllib.request.Request(f"{base_url()}/responses", data=data, method="POST", headers={
            "Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "text/event-stream",
            "originator": "meshpool", "User-Agent": f"Meshpool/{_version()}", "session_id": session,
            **account_headers(tok, tokens)})
        try:
            with _HTTP.open(req, timeout) as r:
                from meshpool_harness.core.wire import _first_then_idle
                out = read_stream(_first_then_idle(r, idle), on_text, data_idle=idle)
            _LIMIT.pop(account, None)                 # a call went through: the plan's limit is over
            return out
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:2000]
            lim = _usage_limit(e.code, detail, account)
            if lim:
                raise lim
            if e.code == 401 and attempt == 0:
                failed = tok                          # refresh once, unless another program already did
                continue
            if e.code == 401:
                raise NotSignedIn(f"ChatGPT did not accept the sign-in. Sign in again: Settings, Account, Runs on. "
                                  f"({detail})")
            raise RuntimeError(f"chatgpt HTTP {e.code}: {detail[:400]}")
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
            raise RuntimeError(f"chatgpt HTTP 0: timed out or connection lost: {e}")
    raise RuntimeError("chatgpt HTTP 401")
