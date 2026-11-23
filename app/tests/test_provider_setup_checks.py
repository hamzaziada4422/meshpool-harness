"""Setup test checks: key verified without /models, no OK for error bodies, wire found, non-HTTP ports, TLS, 16 tokens."""
import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meshpool_harness import provider_setup as PS
from tests.test_provider_setup import home  # noqa: F401  (fixture)

KEY = "sk-good-key-1234"
BAD = "sk-wrong-key-9999"


class Srv:
    """A scriptable server: get(path, headers) and post(path, body, headers) return (status, obj-or-html-string)."""

    def __init__(self, get, post):
        outer = self
        self.posts = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html" if isinstance(obj, str) else "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send(*get(self.path.split("?")[0], {k.lower(): v for k, v in self.headers.items()}))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                outer.posts.append((self.path, body))
                self._send(*post(self.path.split("?")[0], body, {k.lower(): v for k, v in self.headers.items()}))

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def srv():
    made = []

    def mk(get, post):
        s = Srv(get, post)
        made.append(s)
        return s
    yield mk
    for s in made:
        s.close()


def _chat_nolist(path, body, h):
    if (h.get("authorization") or "") != "Bearer " + KEY:
        return 401, {"error": {"message": "bad key"}}
    if body.get("model") != "real":
        return 404, {"error": {"message": "model not found"}}
    return 200, {"choices": [{"message": {"content": "pong"}}]}


def test_wrong_key_on_endpoint_without_models_is_rejected(home, srv):                     # F-14
    s = srv(lambda p, h: (404, {"error": "nope"}), _chat_nolist)
    r = PS.test(s.url + "/v1", BAD)
    assert not r["ok"] and "rejected" in r["error"]
    r = PS.test(s.url + "/v1", KEY)
    assert r["ok"] and r["wire"] == "openai" and "type a model name" in r["error"]
    assert PS.test(s.url + "/v1", KEY, model="real")["ok"]


def test_lmstudio_200_error_body_is_not_ok(home, srv):                                    # W-06
    s = srv(lambda p, h: (200, {"error": "Unexpected endpoint or method. (GET /v1/models)"}),
            lambda p, b, h: (200, {"error": "Unexpected endpoint or method. (POST /v1/chat/completions)"}))
    r = PS.test(s.url + "/v1", KEY)
    assert not r["ok"] and "Unexpected endpoint" in r["error"]


def test_html_200_is_not_ok(home, srv):
    s = srv(lambda p, h: (200, "<html>portal</html>"), lambda p, b, h: (200, "<html>portal</html>"))
    r = PS.test(s.url + "/v1", KEY)
    assert not r["ok"] and "not like a model API" in r["error"]


def test_connection_rejects_200_error_body(home, srv):
    from meshpool_harness.ui import server as SV
    s = srv(lambda p, h: (404, {}), lambda p, b, h: (200, {"error": "Model not loaded"}))
    r = SV.test_connection({"MESHPOOL_BASE_URL": s.url + "/v1", "MESHPOOL_API_KEY": KEY, "MH_MODEL": "m"})
    assert not r["ok"] and "Model not loaded" in r["detail"]
    s2 = srv(lambda p, h: (404, {}), lambda p, b, h: (200, {"choices": [{"message": {"content": "hi"}}]}))
    assert SV.test_connection({"MESHPOOL_BASE_URL": s2.url + "/v1", "MESHPOOL_API_KEY": KEY, "MH_MODEL": "m"})["ok"]


def test_anthropic_endpoint_without_models_detected(home, srv):                           # W-17
    def post(path, body, h):
        if path.endswith("/messages"):
            if h.get("x-api-key") != KEY:
                return 401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            return 200, {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "pong"}]}
        return 404, {"error": "nope"}
    s = srv(lambda p, h: (404, {"error": "nope"}), post)
    r = PS.test(s.url + "/v1", KEY)
    assert r["ok"] and r["wire"] == "anthropic"
    assert not PS.test(s.url + "/v1", BAD)["ok"]
    PS.save(s.url + "/v1", KEY, wire=r["wire"], default_model="claude-x", allow_custom=True)
    from meshpool_harness import config as C
    assert C.read().get("MH_API_WIRE") == "anthropic"


def _post_test(SV, httpd, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/provider/test",
                                 data=json.dumps(payload).encode(),
                                 headers={"X-Meshpool-Token": SV.TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def test_non_http_port_reports_not_http(home):                                            # W-18
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(5)
    port = ls.getsockname()[1]

    def run():
        while True:
            try:
                c, _ = ls.accept()
            except OSError:
                return
            c.sendall(b"SSH-2.0-OpenSSH_9\r\n")
            c.close()
    threading.Thread(target=run, daemon=True).start()
    try:
        r = PS.test(f"http://127.0.0.1:{port}/v1", KEY)
        assert not r["ok"] and "not a web API" in r["error"]
        from meshpool_harness.ui import server as SV
        httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            st, j = _post_test(SV, httpd, {"base_url": f"http://127.0.0.1:{port}/v1", "api_key": KEY})
            assert st == 200 and j["ok"] is False
        finally:
            httpd.shutdown()
    finally:
        ls.close()


def test_route_turns_an_unexpected_exception_into_a_200(home, monkeypatch):
    from meshpool_harness.ui import server as SV

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(PS, "test", boom)
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        st, j = _post_test(SV, httpd, {})
        assert st == 200 and not j["ok"] and "RuntimeError: boom" in j["error"]
    finally:
        httpd.shutdown()


def test_zero_host_and_lan_get_http():                                                    # W-16
    n = PS.normalise_base
    assert n("0.0.0.0:1234") == "http://0.0.0.0:1234"
    assert n("192.168.1.20:8080/v1") == "http://192.168.1.20:8080/v1"
    assert n("10.0.0.5:11434") == "http://10.0.0.5:11434"
    assert n("mybox.local:8000") == "http://mybox.local:8000"
    assert n("api.example.com/v1") == "https://api.example.com/v1"
    assert n("8.8.8.8:80") == "https://8.8.8.8:80"


def test_https_to_plain_http_local_server_falls_back_or_says_http(home, srv, monkeypatch):
    s = srv(lambda p, h: (200, {"data": [{"id": "m1"}]}), lambda p, b, h: (404, {}))
    r = PS.test(f"https://127.0.0.1:{s.port}/v1", KEY)
    assert r["ok"] and r["base_url"].startswith("http://127.0.0.1") and r["models"][0]["id"] == "m1"
    monkeypatch.setattr(PS.PV, "keyless_ok", lambda *a, **k: False)                    # not a local host: say it
    r = PS.test(f"https://127.0.0.1:{s.port}/v1", KEY)
    assert not r["ok"] and "does not speak HTTPS" in r["error"]


def test_probe_uses_16_tokens(home, srv):                                                 # X-07
    def post(path, body, h):
        if body.get("max_tokens", 0) < 16:
            return 400, {"error": {"message": "max_tokens must be at least 16"}}
        return 200, {"choices": [{"message": {"content": "ok"}}]}
    s = srv(lambda p, h: (404, {}), post)
    st, data, err = PS.probe_chat(s.url + "/v1", KEY, "m", "openai")
    assert st == 200 and not err
    PS.probe_chat(s.url + "/v1", KEY, "m", "anthropic")
    assert s.posts[-1][1]["max_tokens"] == 16
