"""The Office panel pairs with the app (ui/office_pair.py), so fetching the panel page no longer gives a working token.

0.0.18 ledger, SV, found not fixed: the panel token was baked into https://localhost:<port>/office/taskpane.html, which
anything on the computer can fetch without a token. A sandboxed command could read the token, start Work chats and
answer approvals "yes" through the panel's routes. The page now carries no token. A panel asks to pair and shows a
code; the user allows that code in the app (which only the app's own window can drive); only then does that panel
get a session token. Each test sends real HTTP(S) requests to the app's listener and to the Office listener, in the
style of test_sv_server_b018.py, and fails when the old baked-in token comes back."""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading

import pytest

from meshpool_harness.ui import office_pair as OP
from meshpool_harness.ui import server as S


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _no_panels():
    OP.forget()
    yield
    OP.forget()


@pytest.fixture
def httpd():
    """The app's own listener: what the user's window talks to, with the launch-key token."""
    srv = S._Server(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    S.HTTPD[0] = srv
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def office(monkeypatch):
    """The real HTTPS Office listener on a free port, with a certificate made the way `meshpool office install` does."""
    pytest.importorskip("cryptography")
    from meshpool_harness import office as OF
    monkeypatch.setenv("MH_OFFICE_PORT", str(_free_port()))
    monkeypatch.delenv("MH_OFFICE", raising=False)
    monkeypatch.setattr(S, "OFFICE_HTTPD", [])
    monkeypatch.setattr(S, "OFFICE_CHATS", set())
    made = OF.make_certs()
    (OF.folder() / "manifest.xml").write_text("<x/>")
    assert S._start_office()
    ctx = ssl.create_default_context(cafile=made["ca"])
    try:
        yield OF.port(), ctx
    finally:
        for srv in list(S.OFFICE_HTTPD):
            srv.shutdown()
            srv.server_close()


def _req(port, method, path, body=None, headers=None, ctx=None):
    """One request; HTTPS to the Office listener when ctx is given, plain HTTP to the app's listener otherwise."""
    if ctx is not None:
        c = http.client.HTTPSConnection("localhost", port, context=ctx, timeout=15)
    else:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    try:
        return r.status, json.loads(out) if out else {}
    except ValueError:
        return r.status, out


def _panel(port, ctx, path, body=None, token=None, method=None):
    return _req(port, method or ("POST" if body is not None else "GET"), path, body,
                {"X-Meshpool-Token": token} if token is not None else None, ctx)


def _app(port, method, path, body=None, token=None):
    return _req(port, method, path, body, {"X-Meshpool-Token": S.TOKEN if token is None else token})


# ---------------------------------------------------------------------------------------------------- the page alone
def test_fetching_the_panel_page_gives_no_working_token(office):
    """What any local process can do: fetch taskpane.html (and the panel's script) with no secret at all. Nothing in
    them works as a token on the panel's routes: not reading settings, not starting a chat, not answering an
    approval. Before the fix the page carried the token in <meta name="mp-token">, and every call below answered 200."""
    port, ctx = office
    st, html = _panel(port, ctx, "/office/taskpane.html")
    assert st == 200 and isinstance(html, bytes)
    st, js = _panel(port, ctx, "/office/taskpane.js")
    assert st == 200
    candidates = set(re.findall(rb"[A-Za-z0-9_-]{20,}", html + js)) | {b""}
    for tok in sorted(candidates):
        t = tok.decode()
        assert _panel(port, ctx, "/api/settings", token=t)[0] == 401, t
        assert _panel(port, ctx, "/api/agent", {"kind": "cowork", "task": "x"}, token=t)[0] == 401, t
        assert _panel(port, ctx, "/api/agent/any/answer", {"id": 1, "decision": "yes"}, token=t)[0] == 401, t
    assert OP.sessions() == 0                                     # and nothing got connected on the way
    assert b"mp-token\" content" not in html and b"__MESHPOOL_TOKEN__" not in html
    assert S.TOKEN.encode() not in html + js


# ---------------------------------------------------------------------------------------------------- a pairing of its own
def test_a_pairing_started_by_another_process_gets_nothing_until_the_user_allows_its_code(office, httpd):
    """A process can start a pairing of its own, but all it gets is a pairing id and a code. The id is not a token,
    the request waits for the user, and the user allowing the real panel's code does not let the other one in."""
    port, ctx = office
    st, intruder = _panel(port, ctx, "/office/pair", {"host": "Excel"})
    assert st == 200 and intruder["code"] and intruder["pair"]
    st, panel = _panel(port, ctx, "/office/pair", {"host": "Excel"})
    assert st == 200 and panel["code"] != intruder["code"]
    for guess in (intruder["pair"], intruder["code"]):                  # neither works as a token
        assert _panel(port, ctx, "/api/settings", token=guess)[0] == 401
    st, c = _panel(port, ctx, "/office/pair/check", {"pair": intruder["pair"]})
    assert st == 200 and c == {"state": "pending", "code": intruder["code"]} and "token" not in c

    st, waiting = _app(httpd, "GET", "/api/office/pairs")               # the app shows both codes to the user
    assert st == 200 and {p["code"] for p in waiting["pending"]} == {intruder["code"], panel["code"]}
    assert all("pair" not in p for p in waiting["pending"])             # the app never sees a pairing id

    assert _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": panel["code"]})[0] == 200
    st, c = _panel(port, ctx, "/office/pair/check", {"pair": intruder["pair"]})
    assert c["state"] == "pending" and "token" not in c                 # still nothing for the other process
    assert _app(httpd, "POST", "/api/office/pairs", {"action": "deny", "code": intruder["code"]})[0] == 200
    st, c = _panel(port, ctx, "/office/pair/check", {"pair": intruder["pair"]})
    assert c == {"state": "denied"}
    st, c = _panel(port, ctx, "/office/pair/check", {"pair": "not-a-pairing-id"})
    assert c == {"state": "expired"}
    assert OP.sessions() == 1                                           # only the panel the user allowed


def test_a_panel_the_user_allowed_works_and_gets_its_token_once(office, httpd):
    """The real panel's path: pair, the user allows the code in the app, the panel reads its token once and uses it.
    A second read of the same pairing hands out nothing (a process that learns the id later is too late)."""
    port, ctx = office
    st, r = _panel(port, ctx, "/office/pair", {"host": "Word"})
    assert st == 200
    assert _panel(port, ctx, "/api/settings", token="")[0] == 401
    st, out = _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": r["code"]})
    assert st == 200 and out["sessions"] == 1 and out["pending"] == []
    st, c = _panel(port, ctx, "/office/pair/check", {"pair": r["pair"]})
    assert st == 200 and c["state"] == "allowed" and c["host"] == "Word" and len(c["token"]) >= 32
    assert _panel(port, ctx, "/office/pair/check", {"pair": r["pair"]})[1] == {"state": "expired"}
    st, s = _panel(port, ctx, "/api/settings", token=c["token"])
    assert st == 200 and "fields" in s
    assert _panel(port, ctx, "/api/settings", token=c["token"][:-1] + "x")[0] == 401
    assert _panel(port, ctx, "/api/agent/someone-elses-chat", token=c["token"])[0] == 403     # not 401: no re-pairing
    assert _app(httpd, "GET", "/api/settings", token=c["token"])[0] == 403    # never a token for the app itself


# ---------------------------------------------------------------------------------------------------- who may allow
def test_only_the_apps_own_window_can_allow_a_panel(office, httpd):
    """Allowing a code is an app route behind the launch-key token. It is not on the Office listener at all, so a
    panel (or a process holding a pairing id or a panel's token) cannot allow itself or another one."""
    port, ctx = office
    _, r = _panel(port, ctx, "/office/pair", {"host": "Excel"})
    body = {"action": "allow", "code": r["code"]}
    assert _app(httpd, "POST", "/api/office/pairs", body, token="")[0] == 403
    assert _app(httpd, "POST", "/api/office/pairs", body, token=r["pair"])[0] == 403
    assert _app(httpd, "GET", "/api/office/pairs", token="")[0] == 403
    assert _panel(port, ctx, "/api/office/pairs", body, token=r["pair"])[0] == 404
    _, other = _panel(port, ctx, "/office/pair", {"host": "Excel"})        # a panel already let in cannot do it either
    assert _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": other["code"]})[0] == 200
    tok = _panel(port, ctx, "/office/pair/check", {"pair": other["pair"]})[1]["token"]
    assert _panel(port, ctx, "/api/office/pairs", body, token=tok)[0] == 404
    assert _panel(port, ctx, "/office/pair/check", {"pair": r["pair"]})[1]["state"] == "pending"
    st, out = _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": "000 000"})
    assert st == 404


def test_a_web_page_cannot_start_a_pairing(office):
    """The pairing routes take no token, so they also check that the request is not a browser page's: the media type
    must be application/json (a page cannot send that cross-site without a preflight, which is never answered) and an
    Origin, when sent, must be the panel's own."""
    port, ctx = office
    assert _req(port, "POST", "/office/pair", b"{}", {"Content-Type": "text/plain"}, ctx)[0] == 403
    for origin in ("https://evil.example", "http://localhost:%d" % port, "https://localhost:1", "null"):
        assert _req(port, "POST", "/office/pair", {}, {"Origin": origin}, ctx)[0] == 403, origin
    assert _req(port, "POST", "/office/pair", {}, {"Origin": f"https://localhost:{port}"}, ctx)[0] == 200
    assert _req(port, "POST", "/office/pair", {}, {"Host": "evil.example"}, ctx)[0] == 403
    assert _req(port, "POST", "/office/pair", b"x" * 5000, None, ctx)[0] == 400
    assert _req(port, "GET", "/office/pair", None, None, ctx)[0] == 404


# ---------------------------------------------------------------------------------------------------- limits
def test_waiting_requests_are_capped_and_expire(office, httpd, monkeypatch):
    """A process cannot pile up requests in the app's prompt: past MAX_PENDING the panel is told to wait, and a
    request nobody answered is gone after PAIR_TTL_S (its code can no longer be allowed)."""
    port, ctx = office
    made = [_panel(port, ctx, "/office/pair", {"host": "Excel"}) for _ in range(OP.MAX_PENDING)]
    assert all(st == 200 for st, _ in made)
    st, out = _panel(port, ctx, "/office/pair", {"host": "Excel"})
    assert st == 429 and "Too many" in out["error"]
    monkeypatch.setattr(OP, "PAIR_TTL_S", -1)
    assert _app(httpd, "GET", "/api/office/pairs")[1]["pending"] == []
    st, _ = _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": made[0][1]["code"]})
    assert st == 404
    assert _panel(port, ctx, "/office/pair/check", {"pair": made[0][1]["pair"]})[1] == {"state": "expired"}
    assert OP.sessions() == 0


def test_disconnecting_panels_ends_their_sessions(office, httpd):
    """Settings' "Disconnect Office panels" (and removing the add-in) ends every panel's session at once."""
    port, ctx = office
    _, r = _panel(port, ctx, "/office/pair", {"host": "PowerPoint"})
    _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": r["code"]})
    tok = _panel(port, ctx, "/office/pair/check", {"pair": r["pair"]})[1]["token"]
    assert _panel(port, ctx, "/api/settings", token=tok)[0] == 200
    st, out = _app(httpd, "POST", "/api/office/pairs", {"action": "forget"})
    assert st == 200 and out["sessions"] == 0
    assert _panel(port, ctx, "/api/settings", token=tok)[0] == 401


# ---------------------------------------------------------------------------------------------------- review, 0.0.18
def _paired(port, ctx, httpd, host="Excel"):
    _, r = _panel(port, ctx, "/office/pair", {"host": host})
    _app(httpd, "POST", "/api/office/pairs", {"action": "allow", "code": r["code"]})
    return _panel(port, ctx, "/office/pair/check", {"pair": r["pair"]})[1]["token"]


def test_a_panel_reaches_only_the_chats_it_started_not_another_panels(office, httpd):
    """OFFICE_CHATS was one set for every paired panel, so a second session the user allowed by mistake (a local
    process asking with a code of its own) could read the real panel's chats and answer their approvals. Each panel's
    session now owns the chats it started; Disconnect drops them with the session."""
    port, ctx = office
    tok_a, tok_b = _paired(port, ctx, httpd), _paired(port, ctx, httpd, "Word")
    S.OFFICE_CHATS.add("chat-a")
    OP.add_chat(OP.session_of(tok_a), "chat-a")              # what _send does when panel A starts a chat
    assert _panel(port, ctx, "/api/agent/chat-a", token=tok_b)[0] == 403
    assert _panel(port, ctx, "/api/agent/chat-a/answer", {"id": 1, "decision": "yes"}, token=tok_b)[0] == 403
    assert _panel(port, ctx, "/api/agent/chat-a", token=tok_a)[0] != 403    # past the gate (no such chat here)
    _app(httpd, "POST", "/api/office/pairs", {"action": "forget"})
    tok_c = _paired(port, ctx, httpd)
    assert _panel(port, ctx, "/api/agent/chat-a", token=tok_c)[0] == 403    # a new session owns none of the old ones


def test_non_ascii_pairing_ids_and_tokens_are_refused_not_crashed(office, httpd):
    """compare_digest raises on str with non-ASCII characters: the connection dropped with a traceback."""
    port, ctx = office
    _panel(port, ctx, "/office/pair", {"host": "Excel"})
    st, out = _panel(port, ctx, "/office/pair/check", {"pair": "é" * 32})
    assert st == 200 and out == {"state": "expired"}
    assert not OP.session_ok("é" * 40) and OP.session_of("ü") == ""
