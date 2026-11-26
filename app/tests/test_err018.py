"""Errors a user reads, from the 0.0.18 error hunt (IDs ER-01 to ER-16). In 0.0.10 people saw "200" or a raw JSON
body instead of what went wrong. Each test provokes the real failure (a tiny local HTTP server answering each bad
shape, a missing host, a proxy that wants a sign-in, a locked file) and checks the exact words the chat, the
terminal, the connection check or the app's own server show: what went wrong, the service's own words, where (host
and model) and what to do next. Never a bare status code, a raw body or a Python exception, and never the key."""
from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import shutil
import sys
import threading
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402

KEY = "sk-testkey-12345678abcd"
RAW_BITS = ("{", "}", "<html", "<!DOCTYPE", "b''", "urlopen", "Errno", "Traceback", "meshpool HTTP", "RuntimeError")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "slots"))     # no endpoint-down marker from another test
    monkeypatch.setenv("MESHPOOL_API_KEY", KEY)
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "2")
    monkeypatch.setenv("MH_AGENT_IDLE_S", "2")
    monkeypatch.setenv("MH_MODEL", "plain-model")
    monkeypatch.setenv("MESHPOOL_CONTEXT_WINDOW", "128000")
    monkeypatch.setenv("MH_RECOVER_MIN", "0")
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    core._REFUSED.clear()
    return tmp_path


class Srv:
    """An endpoint that answers every request with `handler`."""

    def __init__(self, handler):
        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                handler(self)
            do_GET = do_POST

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.srv.server_address[1]}"
        self.base = f"http://{self.host}/v1"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def srv():
    made = []

    def mk(handler):
        s = Srv(handler)
        made.append(s)
        return s
    yield mk
    for s in made:
        s.close()


def body(code, text, ctype="application/json", headers=None):
    def h(self):
        b = text.encode() if isinstance(text, str) else json.dumps(text).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    return h


def sse(*lines):
    def h(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for ln in lines:
            self.wfile.write(ln.encode())
    return h


def turn(env, base, monkeypatch, stream=True) -> tuple[str, list]:
    """One user message on a fresh agent: (the error text the chat shows, every status line)."""
    monkeypatch.setenv("MESHPOOL_BASE_URL", base)
    monkeypatch.setenv("MH_STREAM", "1" if stream else "0")
    a = Agent(str(env), mode="auto")
    ev: list = []
    a.emit = ev.append
    a.send("hi")
    errs = [e["text"] for e in ev if e.get("type") == "error"]
    assert len(errs) == 1, ev
    return errs[0], [e["text"] for e in ev if e.get("type") == "status"]


def no_raw(text: str) -> None:
    for bit in RAW_BITS:
        assert bit not in text, (bit, text)
    assert KEY not in text


# ---------- ER-01: a 200 that carries an error body says what the service said, where, and what to do -----------

@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("payload,said", [
    ({"error": {"message": "Invalid model name passed in model=plain-model", "type": "invalid_request_error"}},
     "Invalid model name passed in model=plain-model"),                                  # OpenAI and LiteLLM
    ({"detail": "Model plain-model is not loaded"}, "Model plain-model is not loaded"),   # FastAPI / LiteLLM proxy
    ({"message": "Deployment plain-model is paused"}, "Deployment plain-model is paused"),   # Azure-style gateways
])
def test_er01_200_error_body_names_the_service_words(env, srv, monkeypatch, stream, payload, said):
    s = srv(body(200, payload))
    text, _ = turn(env, s.base, monkeypatch, stream)
    assert text == (f'{s.host} (model plain-model) answered without a model reply. The service said: "{said}." '
                    "Check the model name and the endpoint address in Settings (the address usually ends in /v1), "
                    "or pick another model.")
    no_raw(text)


def test_er01_200_auth_error_points_at_the_key(env, srv, monkeypatch):
    s = srv(body(200, {"detail": "Authentication failed: bad token"}))
    text, _ = turn(env, s.base, monkeypatch)
    assert text == (f'{s.host} (model plain-model) answered without a model reply. The service said: "Authentication '
                    f'failed: bad token." Check the API key for {s.host} in Settings, then send your message again.')


# ---------- ER-02: a 200 web page (a proxy sign-in, a router) -----------------------------------------------------

@pytest.mark.parametrize("stream", [True, False])
def test_er02_200_html_page_is_named_as_a_web_page(env, srv, monkeypatch, stream):
    s = srv(body(200, "<!DOCTYPE html><html><head><title>Sign in - Corp Proxy</title></head><body>Log in</body></html>",
                 "text/html"))
    text, _ = turn(env, s.base, monkeypatch, stream)
    assert text == (f'{s.host} (model plain-model) sent a web page ("Sign in - Corp Proxy") instead of a model reply. '
                    "This usually means a sign-in page, a company proxy or an address that is not the model API. "
                    "Check the endpoint address in Settings (it usually ends in /v1).")


# ---------- ER-03: a 200 with an empty body is not "the endpoint answers small requests" ------------------------

def test_er03_200_empty_body_says_the_reply_was_empty(env, srv, monkeypatch):
    s = srv(body(200, ""))
    text, _ = turn(env, s.base, monkeypatch)
    assert text == (f"{s.host} (model plain-model) answered, but the reply was empty: no model text came back. Check "
                    "the endpoint address in Settings (it usually ends in /v1) and that the model is loaded, then "
                    "send your message again.")
    assert "answers small requests" not in text


# ---------- ER-04: a stream that ends without [DONE] or a finish_reason ------------------------------------------

def test_er04_cut_stream_says_the_reply_stopped(env, srv, monkeypatch):
    s = srv(sse('data: {"choices":[{"index":0,"delta":{"content":"hel"}}]}\n\n'))
    text, _ = turn(env, s.base, monkeypatch)
    assert text == (f"The reply from {s.host} (model plain-model) stopped before it was complete: the connection "
                    "closed in the middle of the answer. Send your message again; if it keeps happening, a proxy or "
                    "the server may be cutting long replies.")


# ---------- ER-05: 4xx bodies of every shape become the service's words ------------------------------------------

@pytest.mark.parametrize("code,payload,ctype,head,said,step", [
    (400, {"error": {"message": "Unrecognized request argument supplied: foo", "type": "invalid_request_error"}},
     "application/json", "refused the request (400 bad request).", "Unrecognized request argument supplied: foo",
     "The endpoint did not take the request as sent."),
    (400, {"detail": [{"loc": ["body", "messages"], "msg": "field required", "type": "value_error.missing"}]},
     "application/json", "refused the request (400 bad request).", "messages: field required",
     "The endpoint did not take the request as sent."),
    (422, {"message": "messages must not be empty"}, "application/json",
     "refused the request (422 unprocessable request).", "messages must not be empty",
     "The endpoint did not take the request as sent."),
    (404, "<html><body><h1>404 Not Found</h1></body></html>", "text/html", "refused the request (404 not found).",
     "404 Not Found", "Check the model name and the endpoint address in Settings"),
])
def test_er05_4xx_shows_service_words_host_model_and_next_step(env, srv, monkeypatch, code, payload, ctype, head,
                                                               said, step):
    s = srv(body(code, payload, ctype))
    text, _ = turn(env, s.base, monkeypatch)
    assert text.startswith(f"{s.host} (model plain-model) {head} The service said: \"{said}.\" {step}"), text
    no_raw(text)


# ---------- ER-06: after the plain sentence, the detail is the service's words, never the raw body ---------------

@pytest.mark.parametrize("handler,detail", [
    (body(500, "<html><body>Internal Server Error</body></html>", "text/html"),
     "HTTP 500 internal server error from {host}, model plain-model; the service said: Internal Server Error"),
    (body(503, {"detail": "Model is loading"}),
     "HTTP 503 service unavailable from {host}, model plain-model; the service said: Model is loading"),
    (body(429, {"error": {"message": "Rate limit reached for requests", "type": "requests"}}, headers={"Retry-After": "7200"}),
     "HTTP 429 too many requests from {host}, model plain-model; the service said: Rate limit reached for requests; "
     "it asked to wait 2.0 h"),
    (sse('data: {"choices":[{"index":0,"delta":{"content":"hel"}}]}\n\n',
         'data: {"error": {"message": "The server is overloaded", "type": "server_error"}}\n\n'),
     "an error event in the reply from {host}, model plain-model; the service said: The server is overloaded"),
])
def test_er06_server_errors_carry_service_words(env, srv, monkeypatch, handler, detail):
    s = srv(handler)
    text, _ = turn(env, s.base, monkeypatch)
    assert text.endswith("(" + detail.format(host=s.host) + ")"), text
    no_raw(text)


def test_er06_rejected_key_names_the_host(env, srv, monkeypatch):
    s = srv(body(401, {"error": {"message": f"Incorrect API key provided: {KEY}", "type": "invalid_request_error"}}))
    text, _ = turn(env, s.base, monkeypatch)
    assert text == ("The endpoint rejected the API key. Check it in Settings, then send your message again. "
                    f"(HTTP 401 unauthorized from {s.host}, model plain-model; the service said: Incorrect API key "
                    "provided: ***)")


# ---------- ER-07: a key the endpoint echoes back never reaches the chat -----------------------------------------

def test_er07_key_echoed_in_an_error_body_is_hidden(env, srv, monkeypatch):
    s = srv(body(400, {"error": f"bad header Authorization: Bearer {KEY} for model plain-model"}))
    text, _ = turn(env, s.base, monkeypatch)
    assert KEY not in text and KEY[:12] not in text, text
    assert "bad header" in text


# ---------- ER-08: a proxy that wants a sign-in ------------------------------------------------------------------

def test_er08_proxy_sign_in_is_named(env, srv, monkeypatch):
    p = srv(body(407, "<html>Proxy Authentication Required</html>", "text/html", {"Proxy-Authenticate": "Basic"}))
    monkeypatch.setenv("HTTP_PROXY", f"http://{p.host}")
    monkeypatch.setenv("http_proxy", f"http://{p.host}")
    text, _ = turn(env, "http://api.example-llm.invalid/v1", monkeypatch)
    assert text == ("The proxy between this computer and api.example-llm.invalid (model plain-model) asked for a "
                    "sign-in (407 proxy authentication required). Put your proxy user name and password in HTTPS_PROXY "
                    "(http://user:password@proxy:port), or ask your IT team which proxy to use.")


# ---------- ER-09: a connection that never got an answer says why -------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_er09_connection_refused_is_plain(env, monkeypatch):
    port = _free_port()
    text, _ = turn(env, f"http://127.0.0.1:{port}/v1", monkeypatch)
    assert text == (f"Couldn't reach the model endpoint (127.0.0.1:{port}). Check the network and the endpoint "
                    "address in Settings; `meshpool check --live` tests it. (127.0.0.1:" + str(port)
                    + ", model plain-model: nothing answered at that address (connection refused))")


def test_er09_dns_failure_keeps_its_reason_on_the_next_message(env, monkeypatch):
    """The first failure marks the endpoint down; the next message used to say only "did not come back"."""
    from meshpool_harness.core import errtext as ET
    host = f"no-such-host-{uuid.uuid4().hex[:8]}.invalid"
    raw_dns = "HTTP 0: connection problem (<urlopen error [Errno -2] Name or service not known>)"
    assert ET.reason(raw_dns) == "its name could not be found (DNS lookup failed)"
    monkeypatch.setattr(core, "_probe_once", lambda *a, **k: raw_dns)    # what a probe of a missing name returns
    monkeypatch.setattr(Agent, "_probe", lambda self, b, k, m: raw_dns)
    for _ in range(2):
        text, _ = turn(env, f"http://{host}/v1", monkeypatch)
        assert text.endswith(f"({host}, model plain-model: its name could not be found (DNS lookup failed))"), text


# ---------- ER-10: a ChatGPT stream that stops mid-reply is not a whole reply -----------------------------------

def test_er10_chatgpt_stream_without_completion_is_an_error():
    from meshpool_harness.core import chatgpt as CG
    from meshpool_harness.core.client import classify_error
    cut = ['data: {"type":"response.output_text.delta","delta":"The answer is"}', ""]
    with pytest.raises(RuntimeError) as e:
        CG.read_stream(cut)
    assert str(e.value).startswith("HTTP 0: the stream ended before the reply was complete")
    assert classify_error(str(e.value)) == "transient"                   # retried as before, never passed on as done
    whole = cut + ['data: {"type":"response.completed","response":{"output":[]}}', ""]
    assert CG.read_stream(whole)["output"][0]["content"][0]["text"] == "The answer is"


# ---------- ER-11: `meshpool check --live` on a 200 web page ------------------------------------------------------

def test_er11_check_live_shows_plain_words(env, srv, monkeypatch):
    from meshpool_harness import cli
    monkeypatch.setenv("MH_STREAM", "1")
    s = srv(body(200, "<html><head><title>Welcome to the router</title></head></html>", "text/html"))
    r = cli.ping("plain-model", base=s.base, key=KEY)
    assert not r["ok"]
    assert r["detail"] == (f'{s.host} (model plain-model) sent a web page ("Welcome to the router") instead of a model '
                           "reply. This usually means a sign-in page, a company proxy or an address that is not the "
                           "model API. Check the endpoint address in Settings (it usually ends in /v1).")


# ---------- ER-12: an office engine that stops on a failed model call ---------------------------------------------

def test_er12_engine_failure_reason_is_plain(env, monkeypatch, tmp_path):
    from meshpool_harness import cli

    class Boom:
        @staticmethod
        def solve(*a, **k):
            raise RuntimeError('meshpool HTTP 200 but not a model reply: {"detail": "Model plain-model is not loaded"}')
    monkeypatch.setattr(cli, "_engine", lambda name: Boom)
    monkeypatch.setenv("MESHPOOL_BASE_URL", "http://10.0.0.9:8000/v1")
    res = cli.run_request("write a memo", [], str(tmp_path / "out"), engine="general", workdir=str(tmp_path),
                          model="plain-model")
    assert not res["ok"]
    assert res["reason"] == ('10.0.0.9:8000 (model plain-model) answered without a model reply. The service said: "Model '
                             'plain-model is not loaded." Check the model name and the endpoint address in Settings '
                             "(the address usually ends in /v1), or pick another model.")


# ---------- ER-13: the app's own server never answers a 500 with a bare exception ------------------------------

def test_er13_server_500_is_plain_words(env, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui import server as SV

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", "/home/u/work/report.docx")
    monkeypatch.setattr(A, "view", locked)
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/agent/abc",
                                     headers={"X-Meshpool-Token": SV.TOKEN, "Host": f"127.0.0.1:{httpd.server_address[1]}"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 500
        err = json.loads(e.value.read())["error"]
    finally:
        httpd.shutdown()
    assert err == ("meshpool is not allowed to open report.docx. Check its permissions, or close the program that "
                   "has it open, then try again.")


def test_er13_unexpected_error_text_says_what_to_do():
    from meshpool_harness.errors import unexpected
    assert unexpected(KeyError("turns")) == ("meshpool could not finish this: turns (KeyError). Try again; if it keeps "
                                             "happening, restart meshpool and report it.")
    e = OSError(28, "No space left on device")
    e.errno = 28
    assert unexpected(e) == "The disk is full. Free some space, then try again."


# ---------- ER-14: tool cards show file problems in plain words; the model's text is unchanged -------------------

def test_er14_tool_card_for_a_locked_file(env, monkeypatch):
    from meshpool_harness.core import tools as T
    (env / "notes.txt").write_text("hi")

    def locked(p):
        raise PermissionError(13, "Permission denied", str(p))
    monkeypatch.setattr(T, "read_src", locked)
    a = Agent(str(env), mode="auto")
    ev: list = []
    a.emit = ev.append
    out = a._run_tool({"id": "c1", "function": {"name": "read_file", "arguments": json.dumps({"path": "notes.txt"})}})
    assert out.startswith("ERROR: PermissionError: [Errno 13] Permission denied:")      # the model's text: as before
    card = [e for e in ev if e.get("type") == "tool_end"][-1]["output"]
    assert card == ("ERROR: meshpool is not allowed to open notes.txt. Check its permissions, or close the program that "
                    "has it open, then try again.")


def test_er14_tool_card_for_a_damaged_document_and_on_reopen(env):
    from meshpool_harness.errors import tool_card
    from meshpool_harness.ui import agent_api as A
    raw = "Could not read deck.pptx: PackageNotFoundError: Package not found at '/home/u/deck.pptx'"
    want = ("Could not read deck.pptx: this PowerPoint deck is damaged, empty or not what its name says "
            "(PackageNotFoundError: Package not found at '/home/u/deck.pptx'). Open it in its app to check it.")
    assert tool_card(raw) == want
    win = "ERROR: PermissionError: [WinError 32] The process cannot access the file: 'C:\\\\Users\\\\u\\\\Book.xlsx'"
    assert tool_card(win) == ("ERROR: Book.xlsx is open in another program (for example Word or Excel). Close it "
                              "there, then try again.")
    msgs = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "deck.pptx"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": raw}]
    ends = [e for e in A.events_from_messages(msgs) if e["type"] == "tool_end"]
    assert ends[0]["output"] == want


# ---------- ER-15: the connection check reads every error body shape ---------------------------------------------

def test_er15_connection_check_reads_detail_lists_and_200_bodies(env, srv, monkeypatch):
    from meshpool_harness.ui import server as SV
    s = srv(body(400, {"detail": [{"loc": ["body", "messages"], "msg": "field required"}]}))
    r = SV.test_connection({"MESHPOOL_BASE_URL": s.base, "MESHPOOL_API_KEY": KEY, "MH_MODEL": "m"})
    assert r["detail"] == ("127.0.0.1 refused the request (400). Check the model name and this endpoint's settings, or "
                           "pick another model, then try again. (The service said: messages: field required)")
    s2 = srv(body(200, {"detail": "Authentication failed"}))
    r2 = SV.test_connection({"MESHPOOL_BASE_URL": s2.base, "MESHPOOL_API_KEY": KEY, "MH_MODEL": "m"})
    assert r2["detail"] == ("127.0.0.1 answered, but not with a model reply. The service said: Authentication failed. "
                            "Check the model name and the key, then test again.")


# ---------- ER-16: the Office panel never says only "meshpool answered 500." ---------------------------------------

@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_er16_office_panel_failure_words():
    panel = Path(__file__).resolve().parents[1] / "meshpool_harness" / "office" / "taskpane.js"
    script = ("const m = require(%s); console.log(JSON.stringify([m.failText(500, {}), m.failText(404, null), "
              "m.failText(400, {error: 'That file is open in Word.'})]));") % json.dumps(str(panel))
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out == ["meshpool had a problem answering (status 500) and gave no reason. Try again; if it keeps "
                   "happening, restart the meshpool app.",
                   "meshpool does not know this request: the app may be older than this panel. Update meshpool, "
                   "then open the panel again.",
                   "That file is open in Word."]
    assert "meshpool answered ${r.status}" not in panel.read_text()


# ---------- ER-17: a 200 error body that names a permanent cause is reported at once, not after 5 tries ----------

def _counting(payload):
    hits: list = []

    def h(self):
        if self.command == "POST" and self.path.endswith("/chat/completions"):
            hits.append(self.path)                                           # model calls only, not a model list
        body(200, payload)(self)
    return h, hits


def test_er17_permanent_200_error_is_not_retried(env, srv, monkeypatch):
    h, hits = _counting({"error": {"message": "The model `deepseek-v9-nope` does not exist",
                                   "type": "invalid_request_error"}})
    s = srv(h)
    text, status = turn(env, s.base, monkeypatch)
    assert len(hits) == 1, hits                                              # was 6 requests over about 31 s
    assert not any("Trying again" in x for x in status), status
    assert text.startswith(f'{s.host} (model plain-model) answered without a model reply. The service said: "The model '
                           '`deepseek-v9-nope` does not exist."')


def test_er17_transient_200_error_is_still_retried(env, srv, monkeypatch):
    h, hits = _counting({"error": {"message": "upstream overloaded, try later"}})
    s = srv(h)
    monkeypatch.setattr(Agent, "_probe", lambda self, b, k, m: core._probe_once(b, k, m))   # conftest: offline
    text, status = turn(env, s.base, monkeypatch)
    assert len(hits) > 1, (hits, status, text)


def test_er17_quota_200_detail_has_no_internal_prefix(env, srv, monkeypatch):
    s = srv(body(200, {"error": {"message": "Insufficient credits: budget exceeded"}}))
    text, _ = turn(env, s.base, monkeypatch)
    assert text.endswith(f"({s.host}, model plain-model answered without a model reply; the service said: Insufficient "
                         "credits: budget exceeded)"), text
    assert "QUOTA" not in text and "HTTP 200" not in text and "not a model reply:" not in text


# ---------- ER-18: the terminal has no Settings page, and meshpool -p has no "continue" ---------------------------

def test_er18_terminal_wording():
    import io
    from meshpool_harness.agent import tui as TUI
    msg = ('The model is busy right now (rate limited). Everything done so far is kept: send "continue" to pick up '
           "where it stopped. Check the key in Settings, then send your message again.")
    out, err = io.StringIO(), io.StringIO()
    t = TUI.Terminal(print_mode=True, out=out, err=err, width=400)
    t.emit({"type": "error", "text": msg})
    shown = " ".join((out.getvalue() + err.getvalue()).split())
    assert ("Everything done so far is kept: run the command again with -c to pick up where it stopped. Check the "
            "key with meshpool setup (or in the settings file), then run the command again.") in shown, shown
    out2 = io.StringIO()
    t2 = TUI.Terminal(out=out2, err=out2, width=400, interactive=False)
    t2.emit({"type": "error", "text": msg})
    shown2 = " ".join(out2.getvalue().split())
    assert 'send "continue" to pick up' in shown2 and "with meshpool setup" in shown2


def test_er18_check_does_not_claim_ready_when_live_fails(env, srv, monkeypatch, capsys):
    from meshpool_harness import cli
    s = srv(body(401, {"error": {"message": "Invalid key"}}))
    (env / "mh" / "meshpool.config").write_text(f"MESHPOOL_API_KEY={KEY}\nMESHPOOL_BASE_URL={s.base}\n")
    monkeypatch.setenv("MESHPOOL_BASE_URL", s.base)
    assert cli._check(True) == 1
    out = capsys.readouterr().out
    assert "Ready to use  : not yet: the live test below failed" in out, out
    live = out.split("Live test FAILED: ", 1)[1]
    assert "The key was rejected (401)" in live and "Settings" not in live, out


# ---------- ER-24: an engine's "retrying" notice names the cause, not the raw reply -------------------------------

def test_er24_engine_retry_notice_is_plain(env, srv, monkeypatch):
    import time as _time
    from meshpool_harness.core import client as CL
    seen = {"n": 0}

    def h(self):
        seen["n"] += 1
        if seen["n"] == 1:
            return body(502, "<html><body><h1>502 Bad Gateway</h1></body></html>", "text/html")(self)
        return body(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1}})(self)
    s = srv(h)
    monkeypatch.setenv("MESHPOOL_BASE_URL", s.base)
    monkeypatch.setenv("MH_STREAM", "0")
    monkeypatch.setattr(_time, "sleep", lambda secs: None)
    told: list = []
    c = CL.LiveClient()
    c.notify = told.append
    r = c.chat(model="plain-model", messages=[{"role": "user", "content": "hi"}])
    assert r["choices"][0]["message"]["content"] == "ok"
    assert told == ["The model did not answer (502 bad gateway). Retrying for up to 120 s."], told
