"""Bug hunt 0.0.18, area SV: the local HTTP server and its APIs (ui/server.py, ui/agent_api.py, ui/features_api.py,
ui/previews.py, ui/workbench.py, the Office listener). Each test sends real HTTP requests to a test server and fails
when its fix is undone."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import server as S


@pytest.fixture
def httpd():
    srv = S._Server(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    S.HTTPD[0] = srv
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(port: int, path: str, headers: dict | None = None, timeout: float = 10.0):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body, r


def _post(port: int, path: str, body: dict | bytes, token: str | None = None, timeout: float = 30.0):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("POST", path, body=data, headers={"Content-Type": "application/json",
                                                "X-Meshpool-Token": token or S.TOKEN})
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, out


# ---------------------------------------------------------------------------------------------------- SV-01
def test_files_route_refuses_a_run_id_that_leaves_the_runs_folder(httpd, tmp_path, monkeypatch):
    """SV-01: /files/<run>/<path> joined the run id onto the runs folder unchecked, so a run id of ".." (sent as %2e%2e)
    served any file under <runs>/../out. Only a plain run id inside the runs folder may be used."""
    runs = tmp_path / "home" / "runs"
    (runs / "r1" / "out").mkdir(parents=True)
    (runs / "r1" / "out" / "ok.txt").write_text("fine")
    (tmp_path / "home" / "out").mkdir()
    (tmp_path / "home" / "out" / "secret.txt").write_text("not for the page")
    monkeypatch.setattr(S, "RUNS", runs)
    st, body, _ = _get(httpd, f"/files/r1/ok.txt?t={S.TOKEN}")
    assert st == 200 and body == b"fine"
    for bad in ("%2e%2e", "..", "%2E%2E", ".", "%2e"):
        st, body, _ = _get(httpd, f"/files/{bad}/secret.txt?t={S.TOKEN}")
        assert st == 404 and b"not for the page" not in body, bad


# ---------------------------------------------------------------------------------------------------- SV-02
def test_memory_exclude_cannot_write_meta_json_outside_the_chats_folder(httpd, tmp_path, monkeypatch):
    """SV-02: POST /api/memory/exclude handed its "chat" field to write_meta unchecked. "../victim" wrote
    victim/meta.json next to the chats folder (any existing folder, given enough ".."), and "" wrote meta.json into
    the chats folder itself. A chat id must be a plain id."""
    sessions = tmp_path / "runs" / "sessions"
    sessions.mkdir(parents=True)
    victim = tmp_path / "runs" / "victim"
    victim.mkdir()
    (victim / "meta.json").write_text('{"keep": 1}')
    monkeypatch.setattr(A, "SESSIONS", sessions)
    for cid in ("../victim", "", "..", str(victim)):
        st, out = _post(httpd, "/api/memory/exclude", {"chat": cid, "ids": ["f1"]})
        assert json.loads((victim / "meta.json").read_text()) == {"keep": 1}, cid
        assert not (sessions / "meta.json").exists(), cid
        assert not (tmp_path / "runs" / "meta.json").exists(), cid
    (sessions / "20260101-000000-abcd").mkdir()                        # a real chat still gets its list
    st, out = _post(httpd, "/api/memory/exclude", {"chat": "20260101-000000-abcd", "ids": ["f1"]})
    assert st == 200
    assert json.loads((sessions / "20260101-000000-abcd" / "meta.json").read_text())["memory_exclude"] == ["f1"]


# ---------------------------------------------------------------------------------------------------- SV-03
def test_an_idle_connection_does_not_hold_a_server_thread_for_good(httpd, monkeypatch):
    """SV-03: the app's listener had no socket timeout (only the Office one had), so a connection that sends nothing
    (a browser's speculative preconnect, a stuck client, a local process opening many) held a server thread until the
    app quit. The server now closes a connection that stays silent past its idle limit."""
    monkeypatch.setattr(S, "IDLE_TIMEOUT_S", 1, raising=False)
    before = threading.active_count()
    socks = [socket.create_connection(("127.0.0.1", httpd), timeout=10) for _ in range(5)]
    try:
        time.sleep(0.3)
        assert threading.active_count() >= before + 5               # one thread per connection while it waits
        for s in socks:
            s.settimeout(8)
            assert s.recv(100) == b""                               # the server hung up on its own
        end = time.time() + 5
        while threading.active_count() > before and time.time() < end:
            time.sleep(0.05)
        assert threading.active_count() <= before
    finally:
        for s in socks:
            s.close()
    st, body, _ = _get(httpd, "/healthz")                           # and a normal request still works
    assert st == 200


class _FakeChat:
    """Just enough of a Session for the event stream: events, state, a lock and its condition, view()."""

    def __init__(self, sid: str):
        self.id, self.state, self.epoch = sid, "working", 1
        self.events = [{"type": "user", "text": "hi", "i": 0}]
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.touched = time.time()

    def view(self, since: int) -> dict:
        with self.lock:
            return {"id": self.id, "state": self.state, "epoch": self.epoch, "total": len(self.events),
                    "events": self.events[since:]}

    def close(self):
        pass


# ---------------------------------------------------------------------------------------------------- SV-04
def test_event_stream_of_a_chat_deleted_meanwhile_ends_cleanly(httpd, monkeypatch):
    """SV-04: when a chat was deleted while its event stream was open, the stream's fallback called A.view, which
    raised KeyError for the deleted chat; do_GET then wrote a whole "HTTP/1.0 404" response into the middle of the
    open text/event-stream body. The stream must end with its own `end` event instead."""
    sid = "20260101-000000-sse1"
    chat = _FakeChat(sid)
    monkeypatch.setitem(A.LIVE, sid, chat)
    c = http.client.HTTPConnection("127.0.0.1", httpd, timeout=10)
    c.request("GET", f"/api/agent/{sid}/events?t={S.TOKEN}&since=0")
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    first = b""
    while b"data:" not in first:
        first += r.fp.readline()
    A.DELETED.add(sid)                                    # deleted in another tab
    with chat.lock:
        chat.events.append({"type": "assistant", "text": "x", "i": 1})
        chat.changed.notify_all()
    rest = r.read()
    c.close()
    assert b"HTTP/1." not in rest and b"Not found" not in rest
    assert rest.rstrip().endswith(b"event: end\ndata: {}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _served_cert(port: int) -> str:
    import ssl
    return ssl.get_server_certificate(("127.0.0.1", port), timeout=10).strip()


# ---------------------------------------------------------------------------------------------------- SV-05
def test_office_install_from_settings_serves_the_new_certificate(httpd, monkeypatch):
    """SV-05: Settings' Office "Install" (POST /api/office) renews a certificate that is about to expire (and untrusts
    the old authority), but the HTTPS listener was only started when none ran, so it kept presenting the old
    certificate until the app restarted and Office refused the panel. A changed certificate restarts the listener."""
    pytest.importorskip("cryptography")
    import ssl
    from meshpool_harness import office as OF
    monkeypatch.setenv("MH_OFFICE_PORT", str(_free_port()))
    monkeypatch.delenv("MH_OFFICE", raising=False)
    monkeypatch.setattr(S, "OFFICE_HTTPD", [])
    OF.make_certs()
    (OF.folder() / "manifest.xml").write_text("<x/>")
    try:
        assert S._start_office()
        old = (OF.folder() / "localhost.crt").read_text().strip()
        assert ssl.PEM_cert_to_DER_cert(_served_cert(OF.port())) == ssl.PEM_cert_to_DER_cert(old)

        def renew(apps=None, confirm=None):              # what install() does near expiry: a new certificate
            OF.make_certs()
            return ["The certificate was about to expire, so meshpool makes a new one."]
        monkeypatch.setattr(OF, "install", renew)
        st, out = _post(httpd, "/api/office", {"action": "install"})
        assert st == 200, out
        new = (OF.folder() / "localhost.crt").read_text().strip()
        assert new != old
        assert ssl.PEM_cert_to_DER_cert(_served_cert(OF.port())) == ssl.PEM_cert_to_DER_cert(new)
        assert json.loads(out)["running"] is True
    finally:
        for srv in list(S.OFFICE_HTTPD):
            srv.shutdown()
            srv.server_close()


# ---------------------------------------------------------------------------------------------------- SV-07
@pytest.mark.parametrize("damage", ["mismatched key", "garbled certificate"])
def test_office_install_replaces_a_certificate_the_listener_cannot_load(httpd, monkeypatch, damage):
    """SV-07: make_certs writes three files one after another, so a crash between them (or a damaged file) leaves a
    certificate and key that do not load. The listener then stays off ("its certificate did not load"), but Install
    only renewed a certificate near its expiry date: it said "Using the certificate at ..." and kept the broken one
    for good. Install now makes a new certificate when the saved pair cannot be loaded."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from meshpool_harness import office as OF
    monkeypatch.setenv("MH_OFFICE_PORT", str(_free_port()))
    monkeypatch.delenv("MH_OFFICE", raising=False)
    monkeypatch.setattr(S, "OFFICE_HTTPD", [])
    OF.make_certs()
    if damage == "mismatched key":
        other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        (OF.folder() / "localhost.key").write_bytes(other)
    else:
        crt = (OF.folder() / "localhost.crt").read_text()
        (OF.folder() / "localhost.crt").write_text(crt[: len(crt) // 2])
    (OF.folder() / "manifest.xml").write_text("<x/>")
    try:
        assert S._start_office() == ""                       # the damaged pair does not load: the panel is off
        st, out = _post(httpd, "/api/office", {"action": "install"})
        assert st == 200, out
        got = json.loads(out)
        assert got["running"] is True, got["lines"]
        served = _served_cert(OF.port())
        assert served and "BEGIN CERTIFICATE" in served
    finally:
        for srv in list(S.OFFICE_HTTPD):
            srv.shutdown()
            srv.server_close()


def _serve_quietly(monkeypatch, tmp_path, desktop: bool):
    """Run the real serve() on a free port with its background helpers stubbed; returns (port, stop)."""
    from meshpool_harness import autoupdate as AU
    from meshpool_harness import harnesses as HR
    from meshpool_harness.agent import mcp as M
    for name in ("purge_due", "scheduler_loop", "probe_mcp", "recover_worktrees", "close_all"):
        monkeypatch.setattr(A, name, lambda *a, **k: None)
    monkeypatch.setattr(S, "_start_office", lambda: "")
    monkeypatch.setattr(S, "_watchdog", lambda *a: None)
    monkeypatch.setattr(S, "ON_QUIT", [])
    monkeypatch.setattr(S, "HTTPD", [None])
    monkeypatch.setattr(S, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(HR, "prune_codex_homes", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(HR, "ensure_defaults", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(M, "start_reaper", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(AU, "start_background", lambda *a, **k: None, raising=False)
    monkeypatch.delenv("MH_UI_OPEN_PAGE", raising=False)
    ready = threading.Event()
    box = {}
    t = threading.Thread(target=lambda: S.serve(_free_port(), open_browser=False, desktop=desktop,
                                                on_ready=lambda p: (box.update(port=p), ready.set())), daemon=True)
    t.start()
    assert ready.wait(20)
    time.sleep(0.3)                                       # the banner is printed right after on_ready

    def stop():
        S.HTTPD[0].shutdown()
        t.join(10)
    return box["port"], stop


# ---------------------------------------------------------------------------------------------------- SV-06
def test_desktop_app_log_does_not_carry_the_launch_key(monkeypatch, tmp_path, capfd):
    """SV-06: the desktop app sends its output to app.log in the data folder, and serve() printed the full app URL
    with the launch key into it. A sandboxed command may read every file, so it could read the key from the log, load
    the page with it, take the API token and run /api/term outside the sandbox. The desktop app opens its window
    itself, so its log gets the address without the key."""
    port, stop = _serve_quietly(monkeypatch, tmp_path, desktop=True)
    try:
        out = capfd.readouterr().out
        assert "Meshpool is running" in out
        assert S.LAUNCH_KEY not in out
        st, body, _ = _get(port, "/")                     # what the log shows opens no token
        assert st == 403 and S.TOKEN.encode() not in body
        st, body, _ = _get(port, f"/?k={S.LAUNCH_KEY}")    # the window's own address still works
        assert st == 200 and S.TOKEN.encode() in body
    finally:
        stop()


def test_browser_mode_still_prints_the_link_that_opens_the_app(monkeypatch, tmp_path, capfd):
    """SV-06 (the other half): `meshpool ui` in a terminal keeps printing the keyed link, since the user may need to
    open it by hand when no browser opened."""
    port, stop = _serve_quietly(monkeypatch, tmp_path, desktop=False)
    try:
        out = capfd.readouterr().out
        assert f"http://127.0.0.1:{port}/?k={S.LAUNCH_KEY}" in out
    finally:
        stop()
