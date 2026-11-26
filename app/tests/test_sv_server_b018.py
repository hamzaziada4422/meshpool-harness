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


def _session_post(port: int, cookie: str | None, page: str | None, origin: str | None = "own"):
    """POST /session as the boot page sends it. origin "own" is meshpool's own origin; None sends no Origin."""
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{port}" if origin == "own" else origin
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/session", body=json.dumps({} if page is None else {"page": page}).encode(), headers=headers)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, out


def _session(port: int, cookie: str, page: str) -> str:
    """The one-time address POST /session hands the boot page."""
    st, out = _session_post(port, cookie, page)
    assert st == 200, out
    url = json.loads(out)["url"]
    assert url.startswith("/?k=")
    return url


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


# ---------------------------------------------------------------------------------------------------- SV-08
def test_account_and_search_build_the_chat_list_once(httpd, tmp_path, monkeypatch):
    """SV-08: the sidebar asks for /api/hub/account every 8 s while a chat runs. It built the whole chat list twice
    (once itself, once again inside scheduled() just to count unread tasks) and read projects.json once per
    scheduled task with a folder. The empty search built the list three times. Each answer now builds it once and
    reads the project list at most once."""
    from meshpool_harness import assistant as AS
    from meshpool_harness.ui import hub as HUB
    from meshpool_harness.ui import workbench as W
    folders = []
    for i in range(6):
        d = tmp_path / f"p{i}"
        d.mkdir()
        W.add_project(str(d), f"Project {i}")
        folders.append(str(d.resolve()))
    tasks = [{"id": f"t{i}", "name": f"Task {i}", "prompt": "x", "enabled": True, "when": "daily", "time": "09:00",
              "folder": folders[i], "last_thread": "c1" if i == 0 else ""} for i in range(6)]
    monkeypatch.setattr(AS, "tasks", lambda: [dict(t) for t in tasks])
    rows = [{"id": "c1", "title": "Chat", "workdir": folders[0], "updated": 1, "state": "saved", "kind": "cowork",
             "unread": True}]
    calls = {"listing": 0, "projects": 0}
    real_projects = W.projects

    def listing():
        calls["listing"] += 1
        return [dict(r) for r in rows]

    def projects():
        calls["projects"] += 1
        return real_projects()
    monkeypatch.setattr(A, "listing", listing)
    monkeypatch.setattr(W, "projects", projects)
    st, body, _ = _get(httpd, "/api/hub/account", {"X-Meshpool-Token": S.TOKEN})
    assert st == 200 and json.loads(body)["scheduled_unread"] == 1
    assert calls == {"listing": 1, "projects": 1}, calls
    calls.update(listing=0, projects=0)
    st, body, _ = _get(httpd, "/api/hub/scheduled", {"X-Meshpool-Token": S.TOKEN})
    got = json.loads(body)["tasks"]
    assert st == 200 and [t["project"] for t in got] == [f"Project {i}" for i in range(6)]
    assert calls["projects"] == 1, calls
    calls.update(listing=0, projects=0)
    st, body, _ = _get(httpd, "/api/hub/search?q=", {"X-Meshpool-Token": S.TOKEN})
    assert st == 200 and calls["listing"] == 1, calls
    assert HUB.TABS                                       # the module under test, imported for the patch above


@pytest.fixture
def live_chat(tmp_path, monkeypatch):
    """A real built-in chat in an isolated data folder, open in LIVE (no MCP servers, no model)."""
    from meshpool_harness import config as C
    sessions = tmp_path / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    proj = tmp_path / "proj"
    proj.mkdir()
    s = A.Session(workdir=str(proj), harness="meshpool-code", kind="code")
    monkeypatch.setitem(A.LIVE, s.id, s)
    return s, proj


# ---------------------------------------------------------------------------------------------------- SV-09
def test_chat_view_does_not_reread_the_trust_files_every_time(httpd, live_chat, monkeypatch):
    """SV-09: every chat view (each poll, each event-stream frame that is not plain text, each action's answer)
    worked out the folder's trust note twice from scratch: the trusted-folders files and up to four project files,
    read and parsed, plus path resolves, twice per view because message() ran needs_prompt() again. The note now comes
    from a cache that is checked against those files' stamps, so a change still shows at once."""
    from meshpool_harness import trust as TR
    s, proj = live_chat
    n = {"risky": 0}
    real = TR.risky_files

    def counted(root):
        n["risky"] += 1
        return real(root)
    monkeypatch.setattr(TR, "risky_files", counted)
    hdr = {"X-Meshpool-Token": S.TOKEN}
    for _ in range(10):
        st, body, _ = _get(httpd, f"/api/agent/{s.id}?since=0", hdr)
        assert st == 200 and json.loads(body)["trust"] is None
    assert n["risky"] <= 1, n
    (proj / ".meshpool").mkdir()                          # a hooks file appears: the next view says so
    (proj / ".meshpool" / "hooks.json").write_text('{"PreToolUse": [{"command": "echo hi"}]}')
    st, body, _ = _get(httpd, f"/api/agent/{s.id}?since=0", hdr)
    assert json.loads(body)["trust"]["files"] == [".meshpool/hooks.json"]
    st, out = _post(httpd, "/api/trust", {"root": str(proj), "action": "trust"})
    assert st == 200
    st, body, _ = _get(httpd, f"/api/agent/{s.id}?since=0", hdr)
    assert json.loads(body)["trust"] is None              # trusted: the note goes at once


# ---------------------------------------------------------------------------------------------------- SV-10
@pytest.mark.parametrize("err", [PermissionError(13, "Access is denied"), OSError(28, "No space left on device")])
def test_editor_save_that_cannot_write_is_a_plain_400(httpd, tmp_path, monkeypatch, err):
    """SV-10: POST /api/fs/save caught a permission error only while reading the file. Writing it could still fail:
    on Windows the final rename is refused while Excel or Word has the file open, and a full disk or a read-only
    folder fails the temporary file. The server then answered 500 with the raw "PermissionError: [Errno 13] ...
    .x.csv.mp-123.tmp". It is now a 400 in plain words, and the file and folder are left as they were."""
    from meshpool_harness.ui import files_api as FA
    from meshpool_harness.ui import workbench as W
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.csv").write_text("a,b\n")
    W.add_project(str(proj))

    def refuse(src, dst):
        raise err
    monkeypatch.setattr(FA.os, "replace", refuse)
    st, out = _post(httpd, "/api/fs/save", {"root": str(proj), "path": "x.csv", "text": "a,b\n1,2\n"})
    msg = json.loads(out)["error"]
    assert st == 400, out
    assert "Errno" not in msg and ".tmp" not in msg and "PermissionError" not in msg and "OSError" not in msg
    assert "x.csv" in msg
    assert (proj / "x.csv").read_text() == "a,b\n"
    assert sorted(p.name for p in proj.iterdir()) == ["x.csv"]          # no temporary file left behind


# ---------------------------------------------------------------------------------------------------- SV-11
def test_the_address_handed_to_a_browser_or_window_works_once(httpd, monkeypatch):
    """SV-11: the app opened its window or browser with the one launch key that stays valid for the life of the app:
    webbrowser.open puts it on the browser's command line (readable by every local user in /proc/<pid>/cmdline while
    that browser runs), on Windows the window process gets it as an argument, and the browser keeps it in its history
    file. Any of them could load the page and take the API token. The address the app hands out now carries a key of
    its own that opens the page once (a short grace covers a double load); the window keeps its session cookie."""
    import webbrowser
    monkeypatch.delenv("MH_UI_OPEN_PAGE", raising=False)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(S, "ON_SHOW", [])
    st, _ = _post(httpd, "/show", b"{}", token="none")    # a second launch, no window of ours: a browser opens
    assert st == 200 and len(opened) == 1
    url = opened[0]
    assert S.LAUNCH_KEY not in url and "?k=" in url
    path = url.split(f":{httpd}", 1)[1]
    st, body, r = _get(httpd, path)
    assert st == 200 and S.TOKEN.encode() in body
    cookie = r.getheader("Set-Cookie").split(";")[0]
    monkeypatch.setattr(S, "ONCE_GRACE_S", 0, raising=False)
    time.sleep(0.05)
    st, body, _ = _get(httpd, path)                       # the same address later, from history or ps: refused
    assert st == 403 and S.TOKEN.encode() not in body
    st, body, _ = _get(httpd, path, {"Cookie": cookie})   # the window that opened it reloads with its cookie: SV-13
    assert st == 200 and S.TOKEN.encode() not in body     # gives it the boot page, which trades cookie and PAGE
    url = _session(httpd, cookie, S.PAGE)
    st, body, _ = _get(httpd, url)
    assert st == 200 and S.TOKEN.encode() in body
    st, body, _ = _get(httpd, f"/?k={S.LAUNCH_KEY}")      # the link printed in the terminal still opens the app
    assert st == 200


# ---------------------------------------------------------------------------------------------------- SV-12
def test_a_blank_folder_is_not_the_apps_own_working_folder(httpd, tmp_path, monkeypatch):
    """SV-12: POST /api/projects {"action": "add", "path": ""} (or only spaces) turned into Path("") which is ".": the
    folder the app process happened to start in (the install folder, the home folder, "/") was added as a project,
    and every route then took root="" as that folder: an empty attach wrote a file named "file" into it. A blank
    folder is now refused, both when adding a project and as the root of a file route."""
    from meshpool_harness.ui import workbench as W
    start = tmp_path / "where-the-app-started"
    start.mkdir()
    monkeypatch.chdir(start)
    for blank in ("", "   "):
        st, out = _post(httpd, "/api/projects", {"action": "add", "path": blank})
        assert st == 400, out
        assert str(start) not in [p["path"] for p in W.projects()]
    W.add_project(str(start))                             # even when that folder is a project, "" does not name it
    import base64
    st, out = _post(httpd, "/api/attach", {"root": "", "name": "", "data": base64.b64encode(b"x").decode()})
    assert st == 400, out
    assert list(start.iterdir()) == []
    st, body, _ = _get(httpd, "/api/files?root=&path=", {"X-Meshpool-Token": S.TOKEN})
    assert st == 400


def _printed(capfd, needle: str, secs: float = 20) -> str:
    """Everything serve() printed, read once its banner has reached needle. The banner comes right after on_ready,
    later under load: a fixed 0.3 s wait failed in a busy full run. Polling capfd while the server thread prints can
    drop text (it reads, then truncates), so the wait watches serve()'s own print calls and capfd is read once after."""
    end = time.time() + secs
    while not any(needle in line for line in _BANNER) and time.time() < end:
        time.sleep(0.05)
    return capfd.readouterr().out


_BANNER: list = []


def _serve_quietly(monkeypatch, tmp_path, desktop: bool, open_browser: bool = False):
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
    _BANNER.clear()
    import builtins

    def _print(*a, **k):
        _BANNER.append(" ".join(str(x) for x in a))
        builtins.print(*a, **k)
    monkeypatch.setattr(S, "print", _print, raising=False)     # serve()'s own prints, seen as they happen
    ready = threading.Event()
    box = {}
    t = threading.Thread(target=lambda: S.serve(_free_port(), open_browser=open_browser, desktop=desktop,
                                                on_ready=lambda p: (box.update(port=p), ready.set())), daemon=True)
    t.start()
    assert ready.wait(20)

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
        out = _printed(capfd, "to stop Meshpool")           # the banner's last line: everything before it is in
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
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    port, stop = _serve_quietly(monkeypatch, tmp_path, desktop=False, open_browser=True)
    try:
        out = _printed(capfd, f"http://127.0.0.1:{port}/?k={S.LAUNCH_KEY}")
        assert f"http://127.0.0.1:{port}/?k={S.LAUNCH_KEY}" in out
        end = time.time() + 5
        while not opened and time.time() < end:
            time.sleep(0.05)
        assert opened and S.LAUNCH_KEY not in opened[0]   # SV-11: the browser's command line gets a one-time key
        st, body, _ = _get(port, opened[0].split(f":{port}", 1)[1])
        assert st == 200 and S.TOKEN.encode() in body
    finally:
        stop()


# ---------------------------------------------------------------------------------------------------- SV-13
def _window_cookie(port: int, monkeypatch) -> str:
    """The session cookie the app's own window holds: it loaded the one-time address the app opened it with."""
    monkeypatch.delenv("MH_UI_OPEN_PAGE", raising=False)
    st, body, r = _get(port, S.open_url(port).split(f":{port}", 1)[1])
    assert st == 200 and S.TOKEN.encode() in body and S.PAGE.encode() in body
    return r.getheader("Set-Cookie").split(";")[0]


def test_the_session_cookie_alone_does_not_get_the_token(httpd, monkeypatch):
    """SV-13: cookies are not scoped by port, so any other server on 127.0.0.1 the user opens in the same browser (a
    dev server the agent started) receives mp_s. It could send GET / to meshpool's port with that cookie and read the
    API token from the page. The cookie alone now gets a boot page without the token, and POST /session gives a
    one-time address only for the cookie together with PAGE, which only the meshpool page holds (in localStorage,
    scoped to meshpool's own origin and port)."""
    cookie = _window_cookie(httpd, monkeypatch)
    other = f"http://127.0.0.1:{_free_port()}"            # the origin of the dev server that received the cookie
    for headers in ({"Cookie": cookie}, {"Cookie": cookie, "Origin": other, "Referer": other + "/"},
                    {"Cookie": cookie, "Sec-Fetch-Site": "same-origin", "Origin": f"http://127.0.0.1:{httpd}"}):
        for path in ("/", "/index.html", "/?k=", "/?k=" + cookie.split("=", 1)[1]):
            st, body, _ = _get(httpd, path, headers)
            assert S.TOKEN.encode() not in body and S.PAGE.encode() not in body, (path, headers)
    for page, origin in ((None, other), (None, "own"), ("", "own"), ("guess", "own"), ("guess", None), ("p\u00e4ge", "own"),
                         (S.SESSION, "own"), (cookie, "own")):
        st, out = _session_post(httpd, cookie, page, origin)
        assert st == 403 and b"/?k=" not in out, (page, origin)


def test_session_needs_both_the_cookie_and_the_page_secret(httpd, monkeypatch):
    """SV-13: PAGE without the cookie, or with a forged one, or sent from another origin, buys nothing either."""
    cookie = _window_cookie(httpd, monkeypatch)
    for c, origin in ((None, "own"), ("mp_s=forged", "own"), (cookie, "http://127.0.0.1:3000"),
                      (cookie, "http://localhost:3000"), (cookie, "https://example.com")):
        st, out = _session_post(httpd, c, S.PAGE, origin)
        assert st == 403 and b"/?k=" not in out, (c, origin)
    c = http.client.HTTPConnection("127.0.0.1", httpd, timeout=10)     # a form post: not application/json
    c.request("POST", "/session", body=b"page=" + S.PAGE.encode(),
              headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
    r = c.getresponse()
    assert r.status == 403 and b"/?k=" not in r.read()
    c.close()


def test_the_window_reloads_with_its_cookie_and_page_secret(httpd, monkeypatch):
    """SV-13 (the window keeps working): a reload sends the cookie and gets the boot page; the boot page sends the
    cookie with PAGE and gets a one-time address that opens the full page. Each address works once."""
    cookie = _window_cookie(httpd, monkeypatch)
    st, body, _ = _get(httpd, "/", {"Cookie": cookie})
    assert st == 200 and b"/session" in body and b"mp_page" in body
    url = _session(httpd, cookie, S.PAGE)
    assert _session(httpd, cookie, S.PAGE) != url          # a new key each time
    st, body, _ = _get(httpd, url, {"Cookie": cookie})
    assert st == 200 and S.TOKEN.encode() in body
    monkeypatch.setattr(S, "ONCE_GRACE_S", 0)
    time.sleep(0.05)
    st, body, _ = _get(httpd, url)                         # the address from history later: used up
    assert st == 403 and S.TOKEN.encode() not in body


def test_testkit_token_still_reads_the_token(httpd, monkeypatch):
    """SV-13: testkit/ui_token.py loads the page with the launch key, which still carries the token."""
    import importlib.util
    monkeypatch.delenv("MH_UI_OPEN_PAGE", raising=False)
    path = Path(__file__).resolve().parents[2] / "testkit" / "ui_token.py"
    spec = importlib.util.spec_from_file_location("ui_token_sv13", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("MESHPOOL_UI_URL", S.app_url(httpd))
    assert mod.token(f"http://127.0.0.1:{httpd}", wait=0) == S.TOKEN


def test_a_dev_server_in_the_same_browser_cannot_take_the_token(httpd, monkeypatch, tmp_path):
    """SV-13 end to end in Chromium: the meshpool page and a dev server on another port are open in one browser. The
    dev server records the cookies it is sent (mp_s among them) and replays them to meshpool: no token. The meshpool
    tab itself still gets its token back after a reload."""
    pw = pytest.importorskip("playwright.sync_api")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    real = (Path(S.__file__).parent / "index.html").read_text(encoding="utf-8")
    boot = next(line for line in real.splitlines() if "window.MP = " in line)   # the real page's own token script
    (tmp_path / "index.html").write_text("<!doctype html><title>meshpool</title>" + boot +
                                         "<p id=ok>page</p>", encoding="utf-8")
    monkeypatch.setattr(S, "HERE", tmp_path)
    seen = []

    class Dev(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(self.headers.get("Cookie") or "")
            body = b"<title>dev</title><p>dev server</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    dev = ThreadingHTTPServer(("127.0.0.1", 0), Dev)
    threading.Thread(target=dev.serve_forever, daemon=True).start()
    monkeypatch.delenv("MH_UI_OPEN_PAGE", raising=False)
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"no Chromium here: {e}")
            ctx = browser.new_context()
            app = ctx.new_page()
            app.goto(S.open_url(httpd))
            assert app.evaluate("window.MP.token") == S.TOKEN
            other = ctx.new_page()
            other.goto(f"http://127.0.0.1:{dev.server_address[1]}/")
            assert other.evaluate("(() => { try { return localStorage.getItem('mp_page'); } catch (e) { return null; } })()") is None
            stolen = next(c for c in seen if "mp_s=" in c)          # the browser did hand the dev server mp_s
            _, body, _ = _get(httpd, "/", {"Cookie": stolen})
            assert S.TOKEN.encode() not in body
            for origin in ("own", f"http://127.0.0.1:{dev.server_address[1]}", None):
                assert _session_post(httpd, stolen, None, origin)[0] == 403
            app.reload()                                                # the window itself: boot page, then the app
            app.wait_for_selector("#ok", timeout=10000)
            assert app.evaluate("window.MP.token") == S.TOKEN
            assert "k=" in app.url
            browser.close()
    finally:
        dev.shutdown()
        dev.server_close()
