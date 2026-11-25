"""Azure OpenAI and every key shape, end to end against a fake local server: setup test, Settings test, saving and the
real chat calls (streamed, with tools). The fake records each request's path, query and headers."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness import config as C
from meshpool_harness import provider_setup as PS
from meshpool_harness import providers as PV

KEY32 = "0123456789abcdef0123456789abcdef"
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln-nature_1"


class Fake:
    """A server whose behaviour is a function (method, path, headers, body) -> (status, json)."""

    def __init__(self, handler):
        self.log = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode() if n else ""
                try:
                    jb = json.loads(body) if body else None
                except ValueError:
                    jb = None
                rec = {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                       "body": jb}
                outer.log.append(rec)
                status, data = handler(rec)
                if isinstance(data, str):                       # an SSE stream
                    raw = data.encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    raw = json.dumps(data).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            do_GET = do_POST = _do
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    for k in ("MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "MH_AUTH_HEADER", "MH_AUTH_SCHEME", "MESHPOOL_AZURE_API_VERSION",
              "MH_API_WIRE", "OPENAI_API_KEY", "OPENAI_BASE_URL", "MH_PROVIDER"):
        monkeypatch.setenv(k, "")         # recorded, so PS.save()'s os.environ writes are undone after the test
        monkeypatch.delenv(k)
    C.ensure_home()
    return tmp_path


def _ok_chat(rec):
    return 200, {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


# ------------------------------------------------------------------ pure helpers

def test_azure_urls():
    assert PV.chat_url("https://r.openai.azure.com", "gpt4o") == \
        "https://r.openai.azure.com/openai/deployments/gpt4o/chat/completions?api-version=2024-10-21"
    assert PV.chat_url("https://r.cognitiveservices.azure.com/openai/deployments/dd", "ignored").startswith(
        "https://r.cognitiveservices.azure.com/openai/deployments/dd/chat/completions?api-version=")
    assert PV.chat_url("https://r.services.ai.azure.com/openai/v1", "m") == \
        "https://r.services.ai.azure.com/openai/v1/chat/completions"
    assert PV.chat_url("https://r.services.ai.azure.com/models", "m").startswith(
        "https://r.services.ai.azure.com/models/chat/completions?api-version=")
    assert PV.chat_url("https://api.example.com/v1/", "m") == "https://api.example.com/v1/chat/completions"


def test_azure_api_version_override(monkeypatch, home):
    monkeypatch.setenv("MESHPOOL_AZURE_API_VERSION", "2025-01-01-preview")
    assert PV.chat_url("https://r.openai.azure.com", "d").endswith("api-version=2025-01-01-preview")


def test_auth_header_styles():
    az = "https://r.openai.azure.com"
    assert PV.auth_headers(KEY32, az, "openai", "", "") == {"api-key": KEY32}
    assert PV.auth_headers(JWT, az, "openai", "", "") == {"Authorization": f"Bearer {JWT}"}
    assert PV.auth_headers("k", "https://x.example/v1", "openai", "", "") == {"Authorization": "Bearer k"}
    assert PV.auth_headers("k", "https://x.example/v1", "openai", "X-Token", "raw") == {"X-Token": "k"}
    assert PV.auth_headers("k", "https://x.example/v1", "openai", "api-key", "") == {"api-key": "k"}
    assert PV.auth_headers("k", "https://x.example/v1", "openai", "", "token") == {"Authorization": "Token k"}
    assert PV.auth_headers("u:p", "https://x.example/v1", "openai", "", "basic") == {
        "Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    assert PV.auth_headers("k", "https://api.anthropic.com/v1", "anthropic", "", "") == {
        "anthropic-version": PV.auth_headers("", "", "anthropic", "", "")["anthropic-version"], "x-api-key": "k"}
    assert PV.auth_headers("", "http://localhost:1/v1", "openai", "", "") == {}
    assert PV.auth_headers("k", "https://x/v1", "openai", "bad header!", "") == {"Authorization": "Bearer k"}


@pytest.mark.parametrize("raw,want", [
    ('"sk-ab.c_d="', "sk-ab.c_d="), ("Bearer eyJa.b.c", "eyJa.b.c"), ("api-key: " + KEY32 + "\n", KEY32),
    ("abc-def_ghi==", "abc-def_ghi=="), ("  'xyz' ", "xyz"), (KEY32[:16] + "\n" + KEY32[16:], KEY32),
    ("gsk_x", "gsk_x"), ("AIzaSy-abc", "AIzaSy-abc"), ("“abc”", "abc"), ("tok​en", "token")])
def test_clean_key_never_mangles(raw, want):
    assert C.clean_key(raw) == want


def test_keyless_hosts():
    for u in ("http://localhost:11434/v1", "http://192.168.1.5:8000/v1", "http://10.0.0.2/v1", "http://llama.local/v1",
              "http://172.20.1.1/v1", "http://myserver:8080/v1"):
        assert PV.keyless_ok(u), u
    for u in ("https://api.openai.com/v1", "http://172.40.1.1/v1", "https://llm.meshpool.ai/v1"):
        assert not PV.keyless_ok(u), u


def test_normalise_base_azure():
    n = PS.normalise_base
    # 0.0.14: a bare Azure address means the /openai/v1 route (no api-version)
    assert n("https://r.openai.azure.com") == "https://r.openai.azure.com/openai/v1"
    # a pasted deployment address: the base is the resource; the deployment and api-version are read separately
    assert n("r.openai.azure.com/openai/deployments/dd/chat/completions?api-version=2024-10-21") == \
        "https://r.openai.azure.com"
    assert n("https://r.openai.azure.com/openai/v1/") == "https://r.openai.azure.com/openai/v1"
    assert n("https://r.services.ai.azure.com/models") == "https://r.services.ai.azure.com/models"
    assert n("https://r.cognitiveservices.azure.com/openai") == "https://r.cognitiveservices.azure.com/openai/v1"


# ------------------------------------------------------------------ explain

@pytest.mark.parametrize("status,data,needle", [
    (401, {"error": {"message": "Access denied due to invalid subscription key"}}, "key was rejected"),
    (403, {"error": {"message": "firewall"}}, "refused access"),
    (404, {"error": {"code": "DeploymentNotFound", "message": "The API deployment for this resource does not exist"}},
     "no deployment named 'dd'"),
    (404, {"error": {"code": "404", "message": "Resource not found"}}, "deployment name"),
    (400, {"error": {"message": "API version not supported"}}, "api-version"),
])
def test_explain_azure(status, data, needle):
    assert needle in PS.explain(status, data, base="https://r.openai.azure.com", model="dd", version="2024-10-21")


# ------------------------------------------------------------------ setup test against a fake Azure

def _azure_handler(rec):
    if "api-key" not in rec["headers"] or rec["headers"]["api-key"] != KEY32:
        return 401, {"error": {"message": "Access denied due to invalid subscription key or wrong API endpoint."}}
    if "api-version=" not in rec["path"]:
        return 404, {"error": {"code": "404", "message": "Resource not found"}}
    if rec["path"].startswith("/openai/deployments/dd/chat/completions"):
        return _ok_chat(rec)
    if rec["path"].startswith("/openai/models"):
        return 200, {"data": [{"id": "gpt-4o"}]}
    return 404, {"error": {"code": "DeploymentNotFound", "message": "The API deployment for this resource does not exist."}}


@pytest.fixture
def azure(home, monkeypatch):
    f = Fake(_azure_handler)
    port = f.srv.server_address[1]
    # the fake is plain http on localhost: treat that host as Azure for the address and header rules
    monkeypatch.setattr(PV, "AZURE_SUFFIXES", ("127.0.0.1",))
    yield f, f.url
    f.close()
    assert port


def test_azure_test_with_deployment_uses_a_chat_call(azure):
    f, url = azure
    r = PS.test(url, KEY32, model="dd")
    assert r["ok"] and r["models"] == [{"id": "dd"}], r
    rec = f.log[-1]
    assert rec["method"] == "POST" and rec["path"] == "/openai/deployments/dd/chat/completions?api-version=2024-10-21"
    assert rec["headers"]["api-key"] == KEY32 and "authorization" not in rec["headers"]
    assert rec["body"]["max_completion_tokens"] == 16 and "max_tokens" not in rec["body"]


def test_azure_test_without_deployment_checks_the_key_only(azure):
    f, url = azure
    n0 = len(f.log)
    r = PS.test(url, KEY32)                       # 0.0.14: no deployment typed -> ask for it, no call is made
    assert not r["ok"] and r["needs_model"] and not r["models"] and "deployment" in r["error"]
    assert len(f.log) == n0
    bad = PS.test(url, "f" * 32, model="dd")
    assert not bad["ok"] and "key was rejected" in bad["error"]


def test_azure_wrong_deployment_and_version_messages(azure):
    f, url = azure
    r = PS.test(url, KEY32, model="nope")
    assert not r["ok"] and "no deployment named 'nope'" in r["error"]
    r = PS.test(url + "/openai/deployments/dd", KEY32, api_version="")        # deployment from the address
    assert r["ok"]


def test_azure_version_pasted_in_the_address(azure):
    f, url = azure
    PS.test(url + "/openai/deployments/dd/chat/completions?api-version=2025-03-01-preview", KEY32)
    assert f.log[-1]["path"].endswith("api-version=2025-03-01-preview")


def test_azure_entra_token_goes_as_bearer(home, monkeypatch):
    seen = []

    def h(rec):
        seen.append(rec["headers"])
        return _ok_chat(rec)
    f = Fake(h)
    monkeypatch.setattr(PV, "AZURE_SUFFIXES", ("127.0.0.1",))
    try:
        assert PS.test(f.url, JWT, model="dd")["ok"]
    finally:
        f.close()
    assert seen[-1]["authorization"] == f"Bearer {JWT}" and "api-key" not in seen[-1]


def test_save_azure_then_real_calls(azure, home, monkeypatch):
    """Save the settings, then the real transport (SimpleLedger streamed, and the app agent's _post) uses them."""
    f, url = azure
    # 0.0.14: the test call finds the /openai/v1 route missing (404) and falls back to the deployment route; Save keeps it
    assert PS.test(url, KEY32, model="dd")["ok"]
    cur = PS.save(url, KEY32, "auto", "dd", allow_custom=True, api_version="2024-10-21")
    assert cur["azure"] and cur["default_model"] == "dd"
    C.load_into_env(override=True)
    sse = ("data: " + json.dumps({"id": "1", "choices": [{"delta": {"content": "hi"}}]}) + "\n\n"
           "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
               "name": "f", "arguments": "{\"a\":1}"}}]}, "finish_reason": "tool_calls"}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1}}) + "\n\ndata: [DONE]\n\n")
    n0 = len(f.log)
    f.srv.RequestHandlerClass  # noqa: B018
    # replace the handler's behaviour for the chat path with a stream
    orig = _azure_handler

    def stream_handler(rec):
        if rec["path"].startswith("/openai/deployments/dd/chat/completions") and (rec["body"] or {}).get("stream"):
            return 200, sse
        return orig(rec)
    f.close()
    g = Fake(stream_handler)
    monkeypatch.setattr(PV, "key_base", lambda: g.url)
    try:
        from meshpool_harness.core.wire import SimpleLedger
        led = SimpleLedger("t")
        r = led.chat(model="dd", messages=[{"role": "user", "content": "x"}], tools=None, extra={
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}]}, retries=0)
        assert r["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "f"
        rec = g.log[-1]
        assert rec["path"] == "/openai/deployments/dd/chat/completions?api-version=2024-10-21"
        assert rec["headers"]["api-key"] == KEY32 and rec["body"]["stream"] is True
        assert "max_completion_tokens" in rec["body"] and "max_tokens" not in rec["body"]
        # the app agent's request builder
        from meshpool_harness.agent import core
        assert core._probe_once(g.url, KEY32, "dd") == "ok"
        assert g.log[-1]["headers"]["api-key"] == KEY32 and g.log[-1]["path"].startswith("/openai/deployments/dd/")
    finally:
        g.close()


def test_azure_v1_route(home, monkeypatch):
    seen = []

    def h(rec):
        seen.append(rec)
        return _ok_chat(rec)
    f = Fake(h)
    monkeypatch.setattr(PV, "AZURE_SUFFIXES", ("127.0.0.1",))
    try:
        r = PS.test(f.url + "/openai/v1", KEY32, model="gpt-4o-dep")
    finally:
        f.close()
    assert r["ok"] and seen[-1]["path"] == "/openai/v1/chat/completions"
    assert seen[-1]["body"]["model"] == "gpt-4o-dep" and seen[-1]["headers"]["api-key"] == KEY32
