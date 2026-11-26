"""Command line and setup fixes from the 0.0.18 bug hunt (CL rows): each test fails on the code before its fix."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meshpool_harness.core import wire as W

KEY = "abcd1234efgh5678ijkl9012mnop3456qrst"


@pytest.fixture
def server(tmp_path, monkeypatch):
    """server(status, reply) -> (SimpleLedger pointed at a local server, list of request bodies it saw)."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    servers = []

    def make(status, reply):
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                seen.append(self.rfile.read(int(self.headers["Content-Length"])))
                b = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        monkeypatch.setenv("MESHPOOL_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
        monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-1234567890")
        monkeypatch.setenv("MH_STREAM", "0")
        return W.SimpleLedger("t"), seen
    yield make
    for s in servers:
        s.shutdown()


# ---- CL-01: a key cut in two by the 200-character limit was shown in part -----------------------------------------
def test_explain_hides_a_key_that_the_length_limit_cuts():
    from meshpool_harness.core import endpoint as EP
    body = json.dumps({"error": {"message": "Bad request: " + "the header you sent was not accepted. " * 4
                                 + "Value: " + KEY}}).encode()
    out = EP.explain(400, body, key=KEY)
    assert KEY[:12] not in out
    assert "(The service said:" in out


def test_explain_hides_a_cut_key_in_a_plain_text_body():
    from meshpool_harness.core import endpoint as EP
    body = ("<html><body>" + "gateway said no. " * 10 + "key=" + KEY + "</body></html>").encode()
    assert KEY[:12] not in EP.explain(400, body, key=KEY)


# ---- CL-02: the transport retried a 401 (and any other 4xx) the server will refuse again --------------------------
def test_refused_key_is_sent_once_not_retried(server):
    led, seen = server(401, {"error": {"message": "invalid api key"}})
    with pytest.raises(RuntimeError, match="HTTP 401"):
        led.chat(model="m", messages=[{"role": "user", "content": "hi"}], retries=2)
    assert len(seen) == 1


def test_request_timeout_is_still_retried(server):
    led, seen = server(408, {"error": {"message": "request timeout"}})
    with pytest.raises(RuntimeError, match="HTTP 408"):
        led.chat(model="m", messages=[{"role": "user", "content": "hi"}], retries=2)
    assert len(seen) == 3


def test_gateway_random_400_is_still_retried(server):
    led, seen = server(400, {"error": {"message": "The request was rejected as invalid"}})
    with pytest.raises(RuntimeError):
        led.chat(model="m", messages=[{"role": "user", "content": "hi"}], retries=1)
    assert len(seen) == 2


# ---- CL-03: doctor called a settings file saved with a byte-order mark broken ---------------------------------------
def test_doctor_accepts_a_settings_file_with_a_bom(tmp_path, monkeypatch):
    from meshpool_harness import config as C
    from meshpool_harness import doctor as DR
    cfg = tmp_path / "meshpool.config"
    cfg.write_bytes(b"\xef\xbb\xbfMH_MODEL=some-model\n")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(cfg))
    assert C.read(cfg) == {"MH_MODEL": "some-model"}            # meshpool itself reads it fine
    out: list = []
    DR._settings(out)
    assert out == []


# ---- CL-04: doctor crashed on a permissions file whose "projects" is not an object ---------------------------------
def test_doctor_reports_projects_that_is_not_an_object(tmp_path):
    from meshpool_harness import doctor as DR
    f = tmp_path / "permissions.json"
    f.write_text(json.dumps({"allow": ["edit"], "projects": ["x"]}), encoding="utf-8")
    out: list = []
    DR._rules(f, out)
    assert any("projects" in r["problem"] and r["level"] == "error" for r in out)


# ---- CL-05: `meshpool mcp --json` crashed with an IndexError ------------------------------------------------------
def test_mcp_json_lists_the_servers(monkeypatch, capsys):
    from meshpool_harness import chats, cli_app
    monkeypatch.setattr(chats, "mcp_info", lambda: {"servers": [{"name": "web", "enabled": True}]})
    assert cli_app.main(["mcp", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["servers"][0]["name"] == "web"


# ---- CL-06: slash commands split "quoted words" apart (/schedule add --name "Daily news") -------------------------
def test_slash_schedule_keeps_quoted_words_together(monkeypatch):
    from meshpool_harness.agent import tui_session as TS
    seen = []
    cmds = object.__new__(TS.SharedCommands)
    cmds._cli = seen.append
    cmds.cmd_schedule('add --name "Daily news" --prompt "Read the news and sum it up" --when daily')
    assert seen == [["schedule", "add", "--name", "Daily news", "--prompt", "Read the news and sum it up",
                     "--when", "daily"]]


def test_slash_project_keeps_a_windows_folder_whole():
    from meshpool_harness.agent import tui_session as TS
    seen = []
    cmds = object.__new__(TS.SharedCommands)
    cmds._cli = seen.append
    cmds.cmd_project(r'new Taxes --folder "C:\Users\Jo Doe\Taxes"')
    assert seen == [["projects", "new", "Taxes", "--folder", r"C:\Users\Jo Doe\Taxes"]]
    assert TS.split_args('add "unbalanced') == ["add", '"unbalanced']


# ---- CL-07: the key relay read a refused request's body as the next request, and crashed on odd input ---------------
@pytest.fixture
def relay(tmp_path, monkeypatch):
    from meshpool_harness.core import key_relay as KR
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-1234567890abcdef")
    KR.stop()
    url, tok = KR.ensure()
    yield int(url.split(":")[2].split("/")[0]), tok
    KR.stop()


def test_key_relay_does_not_read_an_unread_body_as_the_next_request(relay):
    import http.client
    port, tok = relay
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/v1/nothing", body=json.dumps({"model": "m", "messages": []}),
              headers={"Authorization": "Bearer " + tok})
    r = c.getresponse()
    assert r.status == 404
    r.read()
    try:                                       # the client may reuse the connection; the relay must not misread it
        c.request("GET", "/v1/models", headers={"Authorization": "Bearer " + tok})
        status = c.getresponse().status
    except (http.client.HTTPException, OSError):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/v1/models", headers={"Authorization": "Bearer " + tok})
        status = c.getresponse().status
    assert status == 200


@pytest.mark.parametrize("headers,body", [({"Content-Length": "abc"}, None), ({}, "[1, 2]")])
def test_key_relay_answers_odd_requests_with_400(relay, headers, body):
    import http.client
    port, tok = relay
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("POST", "/v1/chat/completions")
    c.putheader("Authorization", "Bearer " + tok)
    for k, v in headers.items():
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body.encode() if body is not None else None)
    assert c.getresponse().status == 400


def test_tui_box_does_not_read_the_key_and_sign_in_on_every_key(monkeypatch):
    import io

    from meshpool_harness import providers as PV
    from meshpool_harness.agent import tui as TUI
    calls = []
    monkeypatch.setattr(PV, "key", lambda: calls.append("key") or "")
    monkeypatch.setattr(PV, "ready", lambda p: calls.append(p) or False)
    monkeypatch.delenv("MH_PROVIDER", raising=False)
    now = [100.0]
    t = TUI.Terminal(out=io.StringIO(), width=80, interactive=False, clock=lambda: now[0])
    for _ in range(50):                                            # fifty keys typed within a second
        assert ("err", "Not logged in") in t.status_items()
    assert len(calls) == 2                                         # one look at the key, one at the sign-in
    now[0] += 6
    t.status_items()
    assert len(calls) == 4                                         # looked at again a few seconds later


def test_chatgpt_relay_answers_a_json_list_with_400(tmp_path, monkeypatch):
    import http.client
    from meshpool_harness.core import chatgpt_relay as CR
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    CR.stop()
    url, tok = CR.ensure()
    try:
        c = http.client.HTTPConnection("127.0.0.1", int(url.split(":")[2].split("/")[0]), timeout=10)
        c.request("POST", "/v1/chat/completions", body="[1]", headers={"Authorization": "Bearer " + tok})
        assert c.getresponse().status == 400
    finally:
        CR.stop()
