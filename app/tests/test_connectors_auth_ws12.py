"""Connector sign-in hardening (ws12): refresh that only expires on a real refusal (M14-01), without the app-wide lock
(M14-03), once per rotation (M14-04), a disconnect that cancels a sign-in still open in a browser tab (M14-05), the
GitHub device flow (M14-06) and the repo picker never using the computer's own GitHub login (F-01).

The token endpoint is a local server whose refresh tokens are single-use, like the real ones."""
import json
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
from meshpool_harness import mcp_apps as CN  # noqa: E402

A, B = "linear", "notion"        # two OAuth connectors from the catalog


class TokenServer:
    """/token: refresh_token grants rotate (a reused token answers 400 invalid_grant); authorization_code grants call
    `on_code` first. `mode` "503"/"400" forces that answer, `delay` makes it slow."""

    def __init__(self):
        self.valid = set()
        self.refreshes = []
        self.mode = ""
        self.delay = 0.0
        self.on_code = None
        self.n = 0
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
                code, body = outer.answer(form)
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/token"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def answer(self, form):
        if form.get("grant_type") == "authorization_code":
            if self.on_code:
                self.on_code()
            return 200, self._issue()
        with self.lock:
            self.refreshes.append(form.get("refresh_token"))
        if self.delay:
            time.sleep(self.delay)
        if self.mode == "503":
            return 503, {"error": "temporarily_unavailable"}
        with self.lock:
            if self.mode == "400" or form.get("refresh_token") not in self.valid:
                return 400, {"error": "invalid_grant"}
            self.valid.discard(form["refresh_token"])
        return 200, self._issue()

    def _issue(self):
        with self.lock:
            self.n += 1
            n = self.n
            self.valid.add(f"ref-{n}")
        return {"access_token": f"acc-{n}", "refresh_token": f"ref-{n}", "expires_in": 3600}

    def close(self):
        self.httpd.shutdown()


def _closed_port_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/token"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    CN._FLOWS.clear()
    getattr(CN, "_CANCELLED", {}).clear()
    CN._DEVICE.clear()
    return tmp_path


@pytest.fixture()
def srv():
    s = TokenServer()
    yield s
    s.close()


def _entry(token_url, access="old", refresh="ref-0", expires_in=-5):
    return {"client": {"client_id": "cid"}, "redirect": "http://127.0.0.1:1/cb", "issuer": "i",
            "token_endpoint": token_url, "resource": "r", "access_token": access, "refresh_token": refresh,
            "expires_at": time.time() + expires_in, "connected": time.time()}


def _put(home, **entries):
    (home / "mcp-auth.json").write_text(json.dumps(entries))


def _store(home):
    return json.loads((home / "mcp-auth.json").read_text())


def _row(cid):
    return next(r for r in CN.listing() if r["id"] == cid)


# ---- M14-01 ------------------------------------------------------------------------------------------------------

def _not_expired_by(home, srv, url, label):
    srv.valid.add("ref-0")
    _put(home, **{A: _entry(url)})
    spec = CN.spec_for(A)                                    # unforced: the old token is handed out, the server decides
    assert spec and spec["headers"]["Authorization"] == "Bearer old", label
    assert not _store(home)[A].get("expired"), label
    row = _row(A)
    assert row["connected"] and not row["expired"], label
    assert CN._fresh_token(A, force=True) is None            # forced: nothing better to send ...
    assert not _store(home)[A].get("expired"), label         # ... but still not marked expired
    assert CN.refresh_headers(A) is None
    assert _store(home)[A]["access_token"] == "old"


def test_m14_01_network_down_does_not_expire(home, srv):
    _not_expired_by(home, srv, _closed_port_url(), "closed port")


def test_m14_01_5xx_does_not_expire(home, srv):
    srv.mode = "503"
    _not_expired_by(home, srv, srv.url, "503")
    assert srv.refreshes                                     # it really asked
    # a definitive refusal does mark it
    srv.mode = "400"
    assert CN._fresh_token(A) is None
    assert _store(home)[A]["expired"] is True
    row = _row(A)
    assert row["expired"] and not row["connected"]


def test_m14_01_refusal_marks_expired(home, srv):
    srv.valid.add("ref-0")
    _put(home, **{A: _entry(srv.url)})
    srv.mode = "400"
    assert CN._fresh_token(A) is None and _store(home)[A]["expired"] is True


# ---- M14-03 ------------------------------------------------------------------------------------------------------

def test_m14_03_slow_refresh_does_not_block_other_actions(home, srv):
    srv.valid.add("ref-0")
    srv.delay = 3
    _put(home, **{A: _entry(srv.url), B: _entry(srv.url, access="b-old", refresh="ref-b", expires_in=3000)})
    out = []
    t = threading.Thread(target=lambda: out.append(CN.spec_for(A)))
    t.start()
    try:
        deadline = time.time() + 2
        while not srv.refreshes and time.time() < deadline:
            time.sleep(0.01)
        assert srv.refreshes, "the refresh did not start"

        def quick(label, fn):
            t0 = time.time()
            fn()
            assert time.time() - t0 < 1, f"{label} waited for the refresh"
        quick("listing", CN.listing)
        quick("set_enabled", lambda: CN.set_enabled(B, False))
        quick("spec_for", lambda: CN.spec_for(B))
        quick("disconnect", lambda: CN.disconnect(B))
    finally:
        t.join(10)
    assert out and out[0]["headers"]["Authorization"] == "Bearer acc-1"
    st = _store(home)
    assert B not in st and st[A]["access_token"] == "acc-1"


# ---- M14-04 ------------------------------------------------------------------------------------------------------

def test_m14_04_parallel_401s_refresh_once(home, srv):
    srv.valid.add("ref-0")
    srv.delay = 0.4
    _put(home, **{A: _entry(srv.url, expires_in=3000)})       # not expired by the clock: the server refused it
    start = threading.Barrier(8)
    got = []

    def one():
        start.wait()
        got.append(CN.refresh_headers(A, {"Authorization": "Bearer old"}))
    ts = [threading.Thread(target=one) for _ in range(8)]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    assert len(srv.refreshes) == 1
    assert len(got) == 8 and all(g == {"Authorization": "Bearer acc-1"} for g in got)
    # a client that was refused the new token itself still forces a refresh
    assert CN.refresh_headers(A, {"Authorization": "Bearer acc-1"}) == {"Authorization": "Bearer acc-2"}
    assert len(srv.refreshes) == 2


def test_m14_04_second_process_does_not_refresh_again(home, srv):
    srv.valid.add("ref-0")
    srv.delay = 2
    _put(home, **{A: _entry(srv.url, expires_in=3000)})
    first = []
    t = threading.Thread(target=lambda: first.append(CN.refresh_headers(A, {"Authorization": "Bearer old"})))
    t.start()
    deadline = time.time() + 3
    while not srv.refreshes and time.time() < deadline:
        time.sleep(0.01)
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]);"
            "from meshpool_harness import mcp_apps as CN;"
            "print(json.dumps(CN.refresh_headers('linear', {'Authorization': 'Bearer old'})))")
    env = dict(__import__("os").environ, MESHPOOL_HOME=str(home), MESHPOOL_CONFIG=str(home / "meshpool.config"))
    p = subprocess.run([sys.executable, "-c", code, str(APP)], capture_output=True, text=True, timeout=60, env=env)
    t.join(20)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip().splitlines()[-1]) == {"Authorization": "Bearer acc-1"}
    assert first == [{"Authorization": "Bearer acc-1"}]
    assert len(srv.refreshes) == 1


# ---- M14-05 ------------------------------------------------------------------------------------------------------

def _flow(srv_url, cid=A, state="st1", age=0.0):
    CN._FLOWS[state] = {"cid": cid, "verifier": "v", "client": {"client_id": "cid"}, "meta": {"token_endpoint": srv_url,
                        "issuer": "i"}, "redirect": "http://127.0.0.1:1/cb", "app_redirect": "http://127.0.0.1:1/cb",
                        "resource": "r", "t": time.time() - age}
    return state


def _not_connected(home, cid=A):
    assert cid not in (json.loads((home / "mcp-auth.json").read_text()) if (home / "mcp-auth.json").exists() else {})
    row = _row(cid)
    assert not row["connected"] and not row["enabled"]


def test_m14_05_disconnect_cancels_pending_flow(home, srv):
    st = _flow(srv.url)
    CN.disconnect(A)
    with pytest.raises(ValueError, match="disconnected while it was signing in"):
        CN.finish(st, "code")
    _not_connected(home)


def test_m14_05_disconnect_during_code_exchange_writes_nothing(home, srv):
    st = _flow(srv.url)
    srv.on_code = lambda: CN.disconnect(A)                   # lands while the token request is being answered
    with pytest.raises(ValueError, match="disconnected while it was signing in"):
        CN.finish(st, "code")
    _not_connected(home)


def test_m14_05_disconnect_in_another_process_is_honoured(home, srv):
    st = _flow(srv.url, age=5)
    (home / "connectors.json").write_text(json.dumps({"disconnected": {A: time.time()}}))   # what the CLI writes
    with pytest.raises(ValueError, match="disconnected while it was signing in"):
        CN.finish(st, "code")
    _not_connected(home)
    # signing in again after that disconnect works
    st2 = _flow(srv.url, state="st2")
    assert CN.finish(st2, "code") == CN.get(A)["name"]
    assert _row(A)["connected"] and _row(A)["enabled"]


def test_m14_05_removed_custom_connector_gives_a_clean_error(home, srv):
    (home / "connectors.json").write_text(json.dumps(
        {"custom": [{"id": "x-t", "name": "T", "url": "https://example.com/mcp", "auth": "oauth"}]}))
    st = _flow(srv.url, cid="x-t")
    CN.remove_custom("x-t")
    with pytest.raises(ValueError):
        CN.finish(st, "code")
    st = _flow(srv.url, cid="x-t", state="st2")               # a stray flow for a connector that no longer exists
    with pytest.raises(ValueError, match="removed|disconnected"):
        CN.finish(st, "code")
    assert not (home / "mcp-auth.json").exists() or "x-t" not in _store(home)


# ---- M14-06 ------------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now


def test_m14_06_device_slow_down_and_single_start(home, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(CN, "time", clock)
    monkeypatch.setenv("MESHPOOL_GITHUB_CLIENT_ID", "client")
    calls = []
    replies = {"poll": [(200, {}, {"error": "slow_down"})]}

    def fake_http(url, data=None, form=None, headers=None, timeout=20):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/device/code"):
            n = calls.count("code")
            return 200, {}, {"device_code": f"dev{n}", "user_code": f"CODE-{n}", "interval": 5, "expires_in": 900}
        return replies["poll"].pop(0) if replies["poll"] else (200, {}, {"error": "authorization_pending"})
    monkeypatch.setattr(CN, "_http", fake_http)

    a = CN.github_device_start()
    b = CN.github_device_start()
    assert a == b and a["user_code"] == "CODE-1" and a["interval"] == 5
    assert calls == ["code"]
    assert CN.github_device_start(restart=True)["user_code"] == "CODE-2"
    assert calls == ["code", "code"]

    assert CN.github_device_poll() == {"status": "waiting", "interval": 5}      # too soon: GitHub is not asked
    assert calls == ["code", "code"]
    clock.now += 6
    assert CN.github_device_poll() == {"status": "waiting", "interval": 10}     # slow_down: 5 + 5
    assert calls[-1] == "access_token" and len(calls) == 3
    clock.now += 6
    assert CN.github_device_poll() == {"status": "waiting", "interval": 10}     # inside the new interval: no request
    assert len(calls) == 3
    clock.now += 5
    assert CN.github_device_poll() == {"status": "waiting", "interval": 10}     # authorization_pending
    assert len(calls) == 4
    # a network error or a busy GitHub keeps waiting; a refusal ends it
    replies["poll"] = [(0, {}, {"error": "boom"}), (503, {}, {"error": "x"}), (200, {}, {"error": "access_denied"})]
    clock.now += 11
    assert CN.github_device_poll()["status"] == "waiting"
    clock.now += 11
    assert CN.github_device_poll()["status"] == "waiting"
    clock.now += 11
    r = CN.github_device_poll()
    assert r["status"] == "failed" and not CN._DEVICE
    assert CN.github_device_poll() == {"status": "none"}


def test_m14_06_slow_down_uses_the_interval_github_gives(home, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(CN, "time", clock)
    monkeypatch.setenv("MESHPOOL_GITHUB_CLIENT_ID", "client")

    def fake_http(url, data=None, form=None, headers=None, timeout=20):
        if url.endswith("/device/code"):
            return 200, {}, {"device_code": "d", "user_code": "U", "interval": 5}
        return 200, {}, {"error": "slow_down", "interval": 12}
    monkeypatch.setattr(CN, "_http", fake_http)
    CN.github_device_start()
    clock.now += 6
    assert CN.github_device_poll() == {"status": "waiting", "interval": 12}


# ---- F-01 --------------------------------------------------------------------------------------------------------

def test_f01_repo_picker_never_uses_local_github_login(home, monkeypatch):
    from meshpool_harness.ui import workbench as W
    seen = []
    monkeypatch.setattr(CN, "github_found", lambda: seen.append(1) or {"token": "local-login", "source": "GitHub CLI"})
    assert CN.github_token() == ""
    with pytest.raises(ValueError, match="Connect GitHub"):
        W.github_repos("")
    assert not seen, "the computer's own GitHub login was consulted"
    # the explicit action saves it, and then it is Meshpool's own token
    monkeypatch.setattr(CN, "_github_check", lambda tok: "me")
    assert CN.github_use_found() == {"login": "me", "source": "GitHub CLI"}
    assert seen == [1]
    assert CN.github_token() == "local-login"
