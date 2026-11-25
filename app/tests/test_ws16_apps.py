"""ws16: tokens never follow a redirect to another host (W-01), a damaged connector file is not an empty one,
a non-MCP address is named as such (F-23)."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness import mcp_apps as CN  # noqa: E402
from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.connectors import base as CB  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture()
def hops():
    """(first server, URL of a second server on another host name); the second records what reaches it."""
    got = []

    class Second(BaseHTTPRequestHandler):
        def do_GET(self):
            got.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"leaked": true}')
        do_POST = do_GET

        def log_message(self, *a):
            pass

    b = _serve(Second)
    target = f"http://localhost:{b.server_address[1]}/x"

    class First(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(307)
            self.send_header("Location", target)
            self.end_headers()
        do_POST = do_GET

        def log_message(self, *a):
            pass

    a = _serve(First)
    yield f"http://127.0.0.1:{a.server_address[1]}/mcp", got
    a.shutdown()
    b.shutdown()


def test_mcp_apps_http_does_not_follow_to_another_host(hops):
    url, got = hops
    st, _, _ = CN._http(url, headers={"Authorization": "Bearer secret"})
    assert got == [] and st in (0, 307)


def test_connector_http_json_does_not_follow_to_another_host(hops):
    url, got = hops
    with pytest.raises(urllib.error.HTTPError):
        CB.http_json("GET", url, {"Authorization": "Bearer secret"})
    assert got == []


def test_mcp_client_http_does_not_follow_to_another_host(hops, tmp_path):
    url, got = hops
    s = M.Server("x", {"url": url, "headers": {"Authorization": "Bearer secret"}}, tmp_path, [str(tmp_path)])
    s.start(5)
    assert s.status == "error" and got == []


def test_chatgpt_token_refresh_does_not_follow_to_another_host(hops, monkeypatch):
    from meshpool_harness.core import chatgpt as CG
    url, got = hops
    assert CG._HTTP.open.__module__.endswith("core.http")
    req = urllib.request.Request(url, data=b"x", headers={"Authorization": "Bearer secret"}, method="POST")
    with pytest.raises(urllib.error.HTTPError):
        CG._HTTP.open(req, 5)
    assert got == []


def test_damaged_connector_file_is_not_read_as_empty(home):
    (home / "connectors.json").write_text('{"custom": [ {"id": "x-a", "name"')
    with pytest.raises(ValueError, match="damaged"):
        CN._read(CN._state_file())
    with pytest.raises(ValueError, match="damaged"):
        CN._all()


def test_missing_connector_file_is_empty(home):
    assert CN._read(CN._state_file()) == {}


@pytest.fixture()
def web():
    class H(BaseHTTPRequestHandler):
        def _send(self, code, ctype, body, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(404, "text/html", b"<html>not here</html>")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            if self.path == "/login":
                self._send(401, "text/html", b"<html><body>Please sign in with your password</body></html>")
            elif self.path == "/api":
                self._send(403, "application/json", b'{"error":"bad api key"}')
            elif self.path == "/mcp-auth":
                self._send(401, "application/json", b'{"error":"unauthorized"}',
                           {"WWW-Authenticate": 'Bearer resource_metadata="http://127.0.0.1:1/x"'})
            else:
                self._send(404, "text/html", b"<html>nothing</html>")

        def log_message(self, *a):
            pass

    h = _serve(H)
    yield f"http://127.0.0.1:{h.server_address[1]}"
    h.shutdown()


def test_probe_says_a_web_page_is_not_mcp(web):
    assert CN.probe_mcp(web + "/login") is False
    assert CN.probe_mcp(web + "/api") is True            # a JSON refusal may be an MCP server that wants a key
    assert CN.probe_mcp(web + "/nothing") is False
    assert CN.probe_mcp(web + "/mcp-auth") is True


def test_adding_a_login_page_says_it_is_not_an_mcp_server(home, web):
    with pytest.raises(ValueError) as e:
        CN.add_custom("page", web + "/login")
    assert "not an MCP server" in str(e.value) and "token or API key" not in str(e.value)


def test_a_real_mcp_server_that_wants_a_key_still_asks_for_one(home, web):
    with pytest.raises(ValueError, match="token or API key"):
        CN.add_custom("real", web + "/mcp-auth")
