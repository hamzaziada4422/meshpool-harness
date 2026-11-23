"""Onboarding and check keys: 16-token ping, non-reply 200s, ping tests the key (not ChatGPT), one-request web
onboarding with a normalised address, CLI custom onboarding, key cleaning."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from meshpool_harness import cli
from meshpool_harness import config as C
from meshpool_harness.ui import server as S
from tests.test_provider_setup import home  # noqa: F401  (fixture)

KEY = "sk-good-key-1234"


class Mock:
    """fn(path, body, headers) -> (status, obj | str)."""

    def __init__(self, fn):
        outer = self
        self.posts = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.posts.append((self.path, body))
                code, obj = fn(self.path.split("?")[0], body, {k.lower(): v for k, v in self.headers.items()})
                b = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html" if isinstance(obj, str) else "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def mock():
    made = []

    def mk(fn):
        m = Mock(fn)
        made.append(m)
        return m
    yield mk
    for m in made:
        m.srv.shutdown()


OK = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


def _good(path, body, h):
    if (h.get("authorization") or "") != "Bearer " + KEY:
        return 401, {"error": {"message": "bad key"}}
    return (200, OK) if path == "/v1/chat/completions" else (404, {"error": "not found"})


def _only_chat(path, body, h):
    if path != "/v1/chat/completions":
        return 404, {"error": "nope"}
    return _good(path, body, h)


def test_check_live_sends_16_tokens(home, mock):                                       # X-07
    def fn(path, body, h):
        n = body.get("max_tokens", body.get("max_output_tokens", 99))
        return (400, {"error": {"message": "max_tokens must be at least 16"}}) if n < 16 else (200, OK)
    s = mock(fn)
    r = cli.ping("m", base=s.url + "/v1", key=KEY)
    assert r["ok"], r


@pytest.mark.parametrize("stream", ["1", "0"])
@pytest.mark.parametrize("answer,text", [({"error": "No models loaded."}, "No models loaded"),
                                         ("<html><body>Welcome to the router</body></html>", "Welcome to the router")])
def test_simple_ledger_raises_on_non_reply_200(home, mock, monkeypatch, stream, answer, text):   # W-06
    monkeypatch.setenv("MH_STREAM", stream)
    s = mock(lambda p, b, h: (200, answer))
    from meshpool_harness.core.wire import SimpleLedger
    led = SimpleLedger("t")
    led.base_url, led.api_key = s.url + "/v1", KEY
    with pytest.raises(RuntimeError) as e:
        led.chat(model="m", messages=[{"role": "user", "content": "x"}], retries=0)
    assert text in str(e.value)


@pytest.mark.parametrize("answer,text", [({"error": "No models loaded."}, "No models loaded"),
                                         ("<html><body>Welcome to the router</body></html>", "Welcome to the router")])
def test_ping_rejects_non_reply_200(home, mock, answer, text):                          # W-06
    s = mock(lambda p, b, h: (200, answer))
    r = cli.ping("m", base=s.url + "/v1", key=KEY)
    assert not r["ok"] and text in r["detail"]


def test_agent_post_surfaces_200_error_body(home, mock, tmp_path):                      # W-06, agent/core.py
    from meshpool_harness.agent.core import Agent
    s = mock(lambda p, b, h: (200, {"error": "model not loaded: qwen"}))
    a = Agent(str(tmp_path), mode="auto")
    with pytest.raises(RuntimeError, match="answered 200 without a reply: model not loaded: qwen"):
        a._post(s.url + "/v1", "k", {"model": "m", "messages": []}, False)
    with pytest.raises(RuntimeError, match="model not loaded: qwen"):                    # streamed: JSON, no data: events
        a._post(s.url + "/v1", "k", {"model": "m", "messages": []}, True)


def test_ping_tests_the_key_not_chatgpt(home, mock, monkeypatch):                       # F-14
    s = mock(_good)
    monkeypatch.setenv("MH_PROVIDER", "chatgpt")
    monkeypatch.setenv("MH_MODEL", "chatgpt:x")
    from meshpool_harness.core import chatgpt as CG
    monkeypatch.setattr(CG, "chat", lambda *a, **k: pytest.fail("the ChatGPT sign-in was called"))
    r = cli.ping("m", base=s.url + "/v1", key="sk-wrong")
    assert not r["ok"] and "rejected" in r["detail"]
    assert len(s.posts) >= 1                                                             # the key endpoint got the call
    r = cli.ping("chatgpt:x", base=s.url + "/v1", key=KEY)                               # no model of this endpoint known
    assert not r["ok"] and "Choose a model" in r["detail"]


@pytest.mark.parametrize("typed", ["{host}", "http://{host}/v1/chat/completions"])
def test_onboard_normalises_bare_host_and_pasted_path(home, mock, typed):               # W-23
    s = mock(_only_chat)
    host = f"127.0.0.1:{s.port}"
    r = S.onboard({"provider": "custom", "key": KEY, "base_url": typed.format(host=host), "model": "m"})
    assert r == {"ok": True, "base_url": f"http://{host}/v1"}
    saved = C.read()
    assert saved["MESHPOOL_BASE_URL"] == f"http://{host}/v1" and saved["MH_PROVIDER"] == "custom"
    assert saved["MESHPOOL_API_KEY"] == KEY and saved["MH_MODEL"] == "m"


def test_onboard_one_write_with_key_and_provider(home, mock, monkeypatch):               # M15-04
    s = mock(_only_chat)
    calls = []
    real = C.write
    monkeypatch.setattr(C, "write", lambda vals, *a, **k: (calls.append(dict(vals)), real(vals, *a, **k))[1])
    r = S.onboard({"provider": "custom", "key": KEY, "base_url": s.url + "/v1", "model": "m"})
    assert r["ok"] and len(calls) == 1
    assert calls[0]["MESHPOOL_API_KEY"] == KEY and calls[0]["MH_PROVIDER"] == "custom"
    calls.clear()
    r = S.onboard({"provider": "custom", "key": "sk-wrong", "base_url": s.url + "/v1", "model": "m"})
    assert not r["ok"] and r["detail"] and calls == []                                   # a failed check writes nothing


def test_app_js_onboarding_uses_one_request():                                           # M15-04
    js = (Path(S.__file__).parent / "app.js").read_text(encoding="utf-8")
    i = js.index('$("#oGo").onclick')
    block = js[i:js.index("Bring your context", i)]
    assert '"/api/onboard"' in block
    assert 'post("/api/settings"' not in block
    assert block.count('"/api/provider"') == 1          # only the subscription path, not the key path


def test_cli_custom_onboarding_normalises_base(home, mock, monkeypatch):                # W-23 (CLI)
    from meshpool_harness.agent import onboard as OB
    s = mock(_only_chat)
    host = f"127.0.0.1:{s.port}"
    answers = iter([f"http://{host}/v1/chat/completions", "m", host, "m"])
    monkeypatch.setattr(OB, "_ask", lambda st, q, default="": next(answers))
    monkeypatch.setattr(OB.getpass, "getpass", lambda *a, **k: KEY)
    monkeypatch.setattr(OB, "_p", lambda *a, **k: None)
    monkeypatch.setattr(OB, "_use", lambda which: None)

    class St:
        depth = 0

        def __getattr__(self, n):
            return lambda x="": x
    for _ in range(2):                                   # a pasted endpoint path, then a bare host:port
        assert OB._key_step(St(), False, None, True)
        assert C.read()["MESHPOOL_BASE_URL"] == f"http://{host}/v1"
        C.write({"MESHPOOL_BASE_URL": ""})


@pytest.mark.parametrize("typed", ["Bearer sk-abc", "'Bearer sk-abc'", "sk-​abc"])
def test_bearer_prefixed_key_accepted(home, mock, typed):                               # F-38
    s = mock(lambda p, b, h: (200, OK) if h.get("authorization") == "Bearer sk-abc" else (401, {"error": "bad"}))
    r = S.test_connection({"MESHPOOL_BASE_URL": s.url + "/v1", "MESHPOOL_API_KEY": typed})
    assert r["ok"], r
    S.save_settings({"values": {"MESHPOOL_API_KEY": typed}})
    assert C.read()["MESHPOOL_API_KEY"] == "sk-abc"


def test_bad_address_gets_address_message(home):                                        # F-15
    r = S.test_connection({"MESHPOOL_BASE_URL": "ht!tp:/x", "MESHPOOL_API_KEY": "k"})
    assert not r["ok"] and "key" not in r["detail"].lower() and "address" in r["detail"].lower()
