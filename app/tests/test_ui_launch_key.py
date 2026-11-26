"""Security review (0.0.16): GET / handed the API token to any local process, so a sandboxed command could curl the
page, read the token and run /api/term outside the sandbox. The page now carries the token only for the app's own
window: its launch key (in memory only) sets an HttpOnly, SameSite=Strict session cookie."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from meshpool_harness.ui import server as SV


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.delenv("MH_UI_OPEN_PAGE", raising=False)
    httpd = SV.ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def test_page_without_the_key_carries_no_token(srv):
    code, body, _ = _get(srv, "/")
    assert code == 403 and SV.TOKEN not in body
    code, body, _ = _get(srv, "/?k=wrong")
    assert code == 403 and SV.TOKEN not in body


def test_launch_key_sets_a_session_cookie_that_opens_the_page(srv):
    code, body, h = _get(srv, "/?k=" + SV.LAUNCH_KEY)
    assert code == 200 and SV.TOKEN in body
    cookie = h.get("Set-Cookie", "")
    assert cookie.startswith("mp_s=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    code, body, _ = _get(srv, "/", {"Cookie": cookie.split(";")[0]})
    assert code == 200 and SV.TOKEN not in body and "/session" in body     # the boot page (SV-13 in 0.0.18)
    code, body, _ = _get(srv, "/", {"Cookie": "mp_s=forged"})
    assert code == 403 and SV.TOKEN not in body


def test_app_url_carries_the_key():
    assert SV.app_url(8765) == f"http://127.0.0.1:8765/?k={SV.LAUNCH_KEY}"


def test_agent_browser_refuses_files_outside_the_project_and_the_app_window(tmp_path, monkeypatch):
    from meshpool_harness.agent import browser_mcp_server as BM
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_UI_PORT", "8765")
    (tmp_path / "index.html").write_text("<p>hi</p>")
    assert BM._refuse_url((tmp_path / "index.html").as_uri()) == ""
    assert "outside the project" in BM._refuse_url("file:///etc/passwd")
    assert "own window" in BM._refuse_url("http://127.0.0.1:8765/")
    assert BM._refuse_url("http://localhost:3000/") == ""             # the user's own dev server is fine


def test_fetch_checks_the_address_it_connects_to(monkeypatch):
    """DNS rebinding: the name resolves to a public address when checked and to 127.0.0.1 when connecting."""
    import socket
    from meshpool_harness import web as W
    answers = iter(["93.184.216.34", "127.0.0.1"])

    def fake(host, port, *a, **k):
        ip = next(answers, "127.0.0.1")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    monkeypatch.setattr(W.urllib.request, "getproxies", lambda: {})
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("MH_ALLOW_PRIVATE_FETCH", raising=False)
    with pytest.raises(W.WebError) as e:
        W._get("http://rebind.example.com/", public_only=True, timeout=3)
    assert "127.0.0.1" in str(e.value) or "refused" in str(e.value)


def test_agent_cannot_write_its_own_hooks_or_rules(tmp_path):
    from meshpool_harness.core import tools as T
    ws = T.Workspace(str(tmp_path), str(tmp_path / "run"))
    for name in (".meshpool/hooks.json", ".meshpool/permissions.json", ".Meshpool/HOOKS.json"):
        with pytest.raises(T.ToolError):
            ws.write_file(name, "{}")
    assert "wrote" in ws.write_file(".meshpool/commands/review.md", "Review the diff.")


def test_file_browser_hides_browser_profiles_in_the_meshpool_home(monkeypatch):
    from meshpool_harness.ui import files_api as FA
    root = FA.C.home().resolve()
    assert FA._hidden_name(root, "browser-profile") and FA._hidden_name(root, "Cookies")
    assert FA._hidden_name(root, "window-profile") and not FA._hidden_name(root, "runs")
