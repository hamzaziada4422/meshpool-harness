"""ws12: the ChatGPT sign-in file (auth.json) under concurrent use: single-use refresh tokens, other programs rotating
or removing the pair, half-written files, a lock that cannot be taken, and per-account caches. The fake token endpoint
behaves like OpenAI's: a refresh token works once, a second use answers 400 refresh_token_reused."""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import fsio  # noqa: E402
from meshpool_harness.core import chatgpt as CG  # noqa: E402
from meshpool_harness.core.client import classify_error  # noqa: E402


def jwt(exp: float, acct: str = "acct-A", n: int = 0) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp) + n, 'https://api.openai.com/auth': {'chatgpt_account_id': acct}})}.sig"


def pair(acct="acct-A", refresh="rt-1", exp=None, n=0):
    return {"OPENAI_API_KEY": None, "last_refresh": "2026-11-13T00:00:00Z", "tokens": {
        "id_token": jwt(time.time() + 3600, acct), "access_token": jwt(exp if exp is not None else time.time() + 3600, acct, n),
        "refresh_token": refresh, "account_id": acct}}


class Fake:
    """The token endpoint (single-use refresh tokens) and the Codex endpoint, with hooks for each test."""

    def __init__(self):
        self.lock = threading.Lock()
        self.live = {"rt-1"}                     # refresh tokens the endpoint still accepts
        self.token_calls = []
        self.issued = 0
        self.on_token = None                     # called with (form) before the answer, may touch auth.json
        self.token_status = None                 # force this status (with an error body)
        self.models = {"acct-A": ["a-model"], "acct-B": ["b-model"]}
        self.limit_for = set()                   # accounts whose /responses answers usage_limit_reached

    def rotate(self):
        with self.lock:
            self.issued += 1
            n = self.issued
            self.live.add(f"rt-{n + 1}")
            return n, jwt(time.time() + 3600, n=n), f"rt-{n + 1}"


@pytest.fixture
def env(tmp_path, monkeypatch):
    f = Fake()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            acct = self.headers.get("ChatGPT-Account-ID", "")
            if self.path.startswith("/codex/models"):
                return self._reply(200, json.dumps({"models": [{"slug": s} for s in f.models.get(acct, [])]}).encode())
            self._reply(404, b"{}")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path == "/oauth/token":
                form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                with f.lock:
                    f.token_calls.append(form)
                if f.on_token:
                    f.on_token(form)
                if f.token_status:
                    return self._reply(f.token_status, b'{"error": "boom"}')
                with f.lock:
                    ok = form["refresh_token"] in f.live
                    f.live.discard(form["refresh_token"])
                if not ok:
                    return self._reply(400, b'{"error": "refresh_token_reused"}')
                n, access, rt = f.rotate()
                return self._reply(200, json.dumps({"access_token": access, "refresh_token": rt,
                                                    "id_token": jwt(time.time() + 3600)}).encode())
            if self.path == "/codex/responses":
                acct = self.headers.get("ChatGPT-Account-ID", "")
                if acct in f.limit_for:
                    return self._reply(429, json.dumps({"error": {
                        "type": "usage_limit_reached", "plan_type": "go", "resets_at": int(time.time()) + 90000}}).encode())
                ev = {"type": "response.completed", "response": {"id": "r", "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}], "usage": {}}}
                return self._reply(200, f"data: {json.dumps(ev)}\n\n".encode(), "text/event-stream")
            self._reply(404, b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    codex = tmp_path / "codex"
    codex.mkdir()
    monkeypatch.setenv("MH_CHATGPT_BASE_URL", url + "/codex")
    monkeypatch.setenv("MH_CHATGPT_TOKEN_URL", url + "/oauth/token")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    CG.forget_models()
    CG._LIMIT.clear()
    f.url, f.auth = url, codex / "auth.json"
    yield f
    srv.shutdown()


def save(env, d):
    env.auth.write_text(json.dumps(d))


def read(env):
    return json.loads(env.auth.read_text())


def stale(**kw):
    return pair(exp=time.time() - 10, **kw)


# ---------- M13-01: adopt a pair another program rotated ----------------------------------------------------------

def test_m13_01_adopts_pair_rotated_during_refresh(env):
    old = stale()
    save(env, old)
    rotated = pair(refresh="rt-other", n=7)

    def other_program(form):
        save(env, rotated)                       # Codex rotated the pair between our re-read and our POST
    env.on_token = other_program
    env.live.clear()                             # so the endpoint answers 400 refresh_token_reused
    tok, tokens = CG.credentials(force=True, failed=old["tokens"]["access_token"])
    assert tok == rotated["tokens"]["access_token"] and tokens["refresh_token"] == "rt-other"
    assert read(env) == rotated                  # adopted, not rewritten


def test_m13_01_waits_for_late_rotation(env):
    old = stale()
    save(env, old)
    rotated = pair(refresh="rt-late", n=9)
    env.live.clear()
    env.on_token = lambda form: threading.Timer(0.5, lambda: save(env, rotated)).start()
    tok, _ = CG.credentials(force=True, failed=old["tokens"]["access_token"])
    assert tok == rotated["tokens"]["access_token"]


# ---------- M13-02: compare-and-swap ------------------------------------------------------------------------------

def test_m13_02_logout_during_refresh_not_resurrected(env):
    save(env, stale())

    def logout(form):
        env.auth.unlink()
    env.on_token = logout
    with pytest.raises(CG.NotSignedIn, match="signed out"):
        CG.credentials(force=True)
    assert not env.auth.exists()


def test_m13_02_other_account_not_overwritten(env):
    save(env, stale())
    b = pair(acct="acct-B", refresh="rt-b", n=3)
    env.on_token = lambda form: save(env, b)     # the user signed in to another account meanwhile
    tok, tokens = CG.credentials(force=True)
    assert read(env) == b
    assert tok == b["tokens"]["access_token"] and tokens["refresh_token"] == "rt-b"


# ---------- M13-03: network failures are transient ----------------------------------------------------------------

def test_m13_03_network_error_is_transient(env, monkeypatch):
    save(env, stale())
    monkeypatch.setenv("MH_CHATGPT_TOKEN_URL", "http://127.0.0.1:9/oauth/token")      # nothing listens there
    with pytest.raises(RuntimeError) as ei:
        CG.credentials()
    assert not isinstance(ei.value, (urllib.error.URLError, CG.NotSignedIn))
    assert classify_error(str(ei.value)) == "transient"


def test_m13_03_server_errors_are_transient_and_429_is_rate_limit(env):
    save(env, stale())
    env.token_status = 503
    with pytest.raises(RuntimeError) as ei:
        CG.credentials()
    assert classify_error(str(ei.value)) == "transient" and not isinstance(ei.value, CG.NotSignedIn)
    env.token_status = 429
    with pytest.raises(RuntimeError) as ei:
        CG.credentials()
    assert classify_error(str(ei.value)) == "rate_limit"


def _relay(path, body, token=None):
    from meshpool_harness.core import chatgpt_relay as R
    url, tok = R.ensure()
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token or tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_m13_03_relay_answers_when_refresh_fails(env, monkeypatch):
    from meshpool_harness.core import chatgpt_relay as R
    save(env, stale())
    monkeypatch.setenv("MH_CHATGPT_TOKEN_URL", "http://127.0.0.1:9/oauth/token")
    body = {"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]}
    try:
        for stream in (False, True):
            code, raw = _relay("/chat/completions", dict(body, stream=stream))
            assert code >= 500 and json.loads(raw)["error"]["message"]
        # an exception nobody planned for is still an answer, not a dropped connection
        monkeypatch.setattr(CG, "chat", lambda *a, **k: (_ for _ in ()).throw(ValueError("odd")))
        for stream in (False, True):
            code, raw = _relay("/chat/completions", dict(body, stream=stream))
            assert code == 502 and "odd" in json.loads(raw)["error"]["message"]
    finally:
        R.stop()


# ---------- M13-04: one refresher, the lock is never skipped ------------------------------------------------------

def test_m13_04_windows_lock_timeout_does_not_refresh(env, monkeypatch):
    real_os, real_sleep = os, time.sleep

    class NtOs:
        name = "nt"

        def __getattr__(self, k):
            return getattr(real_os, k)

    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.LK_NBLCK, fake_msvcrt.LK_UNLCK = 1, 0

    def locking(*a):
        raise OSError("locked by someone else")
    fake_msvcrt.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(fsio, "os", NtOs())
    monkeypatch.setattr(CG, "os", NtOs())
    monkeypatch.setattr(CG, "LOCK_TIMEOUT_S", 0.3, raising=False)
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(min(s, 0.005)))
    save(env, stale())
    with pytest.raises(RuntimeError) as ei:
        CG.credentials()
    assert env.token_calls == []                         # never refreshed without the lock
    assert classify_error(str(ei.value)) == "transient"


_CHILD = """
import sys, json, time
sys.path.insert(0, {root!r})
from meshpool_harness.core import chatgpt as CG
t0 = float(sys.argv[2])
while time.time() < t0: time.sleep(0.001)
tok, _ = CG.credentials(force=True, failed=sys.argv[1])
print(tok)
"""


def test_m13_04_two_processes_refresh_once(env):
    old = stale()
    save(env, old)
    root = str(Path(__file__).resolve().parents[1])
    start = time.time() + 1.5
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD.format(root=root), old["tokens"]["access_token"], str(start)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
             for _ in range(2)]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert len(env.token_calls) == 1
    assert outs[0][0].strip() == outs[1][0].strip() == read(env)["tokens"]["access_token"]


# ---------- M13-05: a half-written auth.json is retried, never "signed out" ---------------------------------------

def test_m13_05_torn_read_is_retried(env):
    good = pair()
    env.auth.write_text(json.dumps(good)[:40])
    threading.Timer(0.3, lambda: save(env, good)).start()
    tok, _ = CG.credentials()
    assert tok == good["tokens"]["access_token"]


def test_m13_05_permanently_damaged_file_is_transient_not_signed_out(env, monkeypatch):
    monkeypatch.setattr(CG, "TORN_WAIT_S", 0.3, raising=False)
    env.auth.write_text('{"tokens": {"access_tok')
    with pytest.raises(RuntimeError) as ei:
        CG.credentials()
    assert not isinstance(ei.value, CG.NotSignedIn) and isinstance(ei.value, CG.SignInBusy)
    assert classify_error(str(ei.value)) == "transient"


# ---------- M13-06: limit and model list belong to an account -----------------------------------------------------

def test_m13_06_limit_and_models_follow_account(env):
    save(env, pair(acct="acct-A"))
    env.limit_for = {"acct-A"}
    assert CG.models() == ["a-model"]
    with pytest.raises(CG.UsageLimit):
        CG.chat({"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]})
    assert CG.limit_note()
    save(env, pair(acct="acct-B", refresh="rt-b"))
    assert CG.limit_note() == ""
    assert CG.models() == ["b-model"]
    assert CG.default_model() == "b-model"
    save(env, pair(acct="acct-A"))
    assert CG.limit_note()                                # A's limit is still A's
    env.limit_for = set()
    CG.chat({"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]})   # limit over: forgotten
    assert CG.limit_note() == ""


# ---------- M15-02: a sign-in that cannot work is not "ready" -----------------------------------------------------

def test_m15_02_api_key_login_not_ready(env):
    from meshpool_harness import harnesses as HR, providers as PV
    save(env, {"OPENAI_API_KEY": "sk-x", "tokens": None})
    assert HR.signed_in("codex") is False
    assert PV.ready("chatgpt") is False
    assert "API key" in CG.signin_state()["reason"]


def test_m15_02_rejected_refresh_token_not_ready(env, monkeypatch):
    from meshpool_harness import harnesses as HR, providers as PV, provider_setup as PS
    monkeypatch.setattr(CG, "REUSED_WAIT_S", 0.3, raising=False)
    save(env, stale())
    assert HR.signed_in("codex") is True
    env.token_status = 400                                 # ChatGPT refuses and nobody rotated the pair
    with pytest.raises(CG.NotSignedIn):
        CG.credentials()
    assert HR.signed_in("codex") is False and PV.ready("chatgpt") is False
    st = CG.signin_state()
    assert st["ok"] is False and "Sign in again" in st["reason"]
    info = PS.chatgpt_login()
    assert info["usable"] is False and "Sign in again" in info["note"]
    marker = (env.auth.parent / "auth.json.meshpool-revoked").read_text()
    assert "rt-1" not in marker                            # only a fingerprint is stored
    save(env, pair(refresh="rt-new"))                      # signing in again gives a new refresh token
    assert HR.signed_in("codex") is True and PS.chatgpt_login()["usable"] is True
