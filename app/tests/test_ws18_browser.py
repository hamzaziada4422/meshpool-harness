"""ws18: browser use. A cancelled or timed-out call is dropped from the queue (M32-01), one browser per profile with a
clear message (M32-02), the ctl daemon restarts when its browser died and gives each task a clean page (M32-03/04),
a popup is followed (M32-05). Real Chromium; skipped when none launches here."""
import importlib
import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_helpers as H  # noqa: E402

CHANNEL, WHY = H.find_chromium()
needs_browser = pytest.mark.skipif(bool(WHY), reason=WHY or "browser available")


def _serve_pages(pages: dict) -> str:
    import http.server

    class Q(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = pages.get(self.path.split("?")[0])
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            if callable(body):
                body = body(self)
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Q)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


# ---------- M32-01: a call the client gave up on never runs later ----------

def test_cancelled_queued_call_is_dropped(monkeypatch):
    from meshpool_harness.agent import browser_mcp_server as S
    from meshpool_harness.agent import mcp_stdio
    b = S.Browser()
    ran = []
    gate = threading.Event()
    first = threading.Thread(target=lambda: b.run(lambda: gate.wait(5)), daemon=True)
    first.start()
    time.sleep(0.2)                                    # the browser thread is busy with the first call

    ev = threading.Event()
    out = {}

    def second():
        mcp_stdio._CALL.cancel = ev                    # as serve() sets it for the call's thread
        try:
            b.run(lambda: ran.append("second") or "x")
        except S.ToolFailed as e:
            out["e"] = str(e)
    t = threading.Thread(target=second, daemon=True)
    t.start()
    time.sleep(0.3)
    ev.set()                                           # notifications/cancelled for the queued call
    t.join(3)
    assert "Cancelled" in out["e"]
    gate.set()
    first.join(3)
    time.sleep(0.5)
    assert ran == [], "the cancelled call still ran when the browser got to it"


def test_timed_out_queued_call_is_dropped():
    from meshpool_harness.agent import browser_mcp_server as S
    b = S.Browser()
    ran = []
    gate = threading.Event()
    threading.Thread(target=lambda: b.run(lambda: gate.wait(5)), daemon=True).start()
    time.sleep(0.2)
    with pytest.raises(S.ToolFailed, match="did not respond"):
        b.run(lambda: ran.append("late"), timeout=0.5)
    gate.set()
    time.sleep(0.5)
    assert ran == []


def test_serve_does_not_start_a_cancelled_queued_call():
    """mcp_stdio.serve(serial=True): a call cancelled while it waits for the lock is answered, never run."""
    import io
    from meshpool_harness.agent import mcp_stdio as MS
    ran = []
    gate = threading.Event()

    def slow():
        gate.wait(5)
        return "slow"

    def late():
        ran.append(1)
        return "late"
    tools = {"slow": MS.Tool(slow, "s", {}), "late": MS.Tool(late, "l", {})}
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "slow", "arguments": {}}}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "late", "arguments": {}}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}})]

    class In(io.StringIO):
        def __iter__(self):
            for ln in lines:
                yield ln + "\n"
                time.sleep(0.3)
            gate.set()
            time.sleep(0.8)

    out, old_in, old_out = io.StringIO(), sys.stdin, sys.stdout
    try:
        sys.stdin, sys.stdout = In(), out
        MS.serve("t", tools, serial=True)
        time.sleep(0.5)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    assert ran == []


# ---------- M32-02: one browser per profile, said plainly ----------

def test_second_user_of_the_profile_gets_a_clear_message(tmp_path):
    from meshpool_harness.agent import browser_mcp_server as S
    a = S.ProfileLock(tmp_path / "chromium.lock")
    assert a.acquire("chat-1") == ""
    other = S.ProfileLock(tmp_path / "chromium.lock")
    msg = other.acquire("chat-2")
    assert "another chat" in msg and "chat-1" in msg and "browser_close" in msg
    a.release()
    assert S.ProfileLock(tmp_path / "chromium.lock").acquire("chat-3") == ""


def test_a_second_browser_server_on_the_profile_is_refused_with_that_message(tmp_path, monkeypatch):
    from meshpool_harness.agent import browser_mcp_server as S
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_CHAT_ID", "mine")
    held = S.ProfileLock(tmp_path / "browser-profile" / "chromium.lock")
    assert held.acquire("other-chat") == ""
    b = S.Browser()

    class FakePW:
        class chromium:
            @staticmethod
            def launch_persistent_context(*a, **k):
                raise AssertionError("Chromium must not be started against a profile another chat holds")
    b.pw = FakePW()
    with pytest.raises(S.ToolFailed, match="another chat"):
        b._launch(tmp_path / "browser-profile" / "chromium", headless=True)
    held.release()


# ---------- M32-05: a popup is followed ----------

@needs_browser
def test_popup_from_a_script_is_followed(tmp_path):
    base = _serve_pages({
        "/": "<title>Main</title><h1>Main</h1><button id=b onclick=\"setTimeout(()=>window.open('/pop'),200)\">Go</button>",
        "/pop": "<title>Popup page</title><h1>I am the popup</h1>"})
    s = H.server_for(tmp_path / "home", tmp_path, CHANNEL)
    try:
        snap = s.call("browser_open", {"url": base + "/"})[0]
        ref = H.ref(snap, r"Go")
        s.call("browser_press", {"key": "Tab"})
        s.call("browser_press", {"key": "Enter"})              # opens the popup without browser_click's new-tab check
        time.sleep(1.0)
        snap = s.call("browser_snapshot", {})[0]
        assert "I am the popup" in snap, snap
        assert ref
    finally:
        s.close()


# ---------- M32-03 / M32-04: the ctl daemon ----------

def _ctl(monkeypatch):
    monkeypatch.delenv("MH_BROWSER_PORT", raising=False)
    monkeypatch.setenv("MESHPOOL_HOME", tempfile.mkdtemp())
    monkeypatch.delenv("MH_TASK_ID", raising=False)
    from engines.browser import ctl
    importlib.reload(ctl)
    return ctl


def _descendants(pid: int) -> list[int]:
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                ppid = int((d / "stat").read_text().split(") ")[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            if ppid == pid:
                out += [int(d.name)] + _descendants(int(d.name))
    return out


@needs_browser
def test_ctl_restarts_when_its_browser_died_and_does_not_say_already_running(monkeypatch, tmp_path):
    ctl = _ctl(monkeypatch)
    base = _serve_pages({"/": "<title>One</title><h1>One</h1>"})
    d1, d2 = tmp_path / "t1", tmp_path / "t2"
    d1.mkdir()
    d2.mkdir()
    assert ctl.main(["start", str(d1)]) == 0
    try:
        pid = json.loads(ctl.state_path().read_text())["pid"]
        for p in _descendants(pid):                          # Chromium crashes
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(1.5)
        assert ctl.main(["start", str(d2)]) == 0             # a new daemon, not "browser already running"
        assert json.loads(ctl.state_path().read_text())["pid"] != pid
        assert "One" in ctl.call("goto", base + "/")["title"]
    finally:
        try:
            ctl.call("stop")
        except Exception:  # noqa: BLE001
            pass


@needs_browser
def test_ctl_new_task_gets_clean_cookies_page_and_folder(monkeypatch, tmp_path):
    ctl = _ctl(monkeypatch)
    base = _serve_pages({"/": "<title>Site</title><h1>Site</h1><div id=c></div>"
                              "<script>document.getElementById('c').textContent='cookie='+document.cookie</script>"})
    d1, d2 = tmp_path / "t1", tmp_path / "t2"
    d1.mkdir()
    d2.mkdir()
    assert ctl.main(["start", str(d1)]) == 0
    try:
        ctl.call("goto", base + "/")
        ctl.call("_eval", "document.cookie = 'who=task1; path=/'")
        assert "who=task1" in ctl.call("goto", base + "/")["text"]
        assert ctl.main(["start", str(d1)]) == 0             # the same task again: nothing is reset
        assert "who=task1" in ctl.call("snapshot")["text"]
        assert ctl.main(["start", str(d2)]) == 0             # another task
        res = ctl.call("goto", base + "/")
        assert "who=task1" not in res["text"], "cookies of the previous task leaked into the new one"
        assert ctl.call("screenshot", "a.png")["saved"] == str(d2 / "a.png")
    finally:
        ctl.call("stop")


@needs_browser
def test_ctl_parallel_tasks_do_not_share_a_page(monkeypatch, tmp_path):
    ctl = _ctl(monkeypatch)
    base = _serve_pages({"/a": "<title>Page A</title><h1>A</h1>", "/b": "<title>Page B</title><h1>B</h1>"})
    assert ctl.main(["start", str(tmp_path)]) == 0
    try:
        monkeypatch.setenv("MH_TASK_ID", "task-a")
        ctl.call("goto", base + "/a")
        monkeypatch.setenv("MH_TASK_ID", "task-b")
        ctl.call("goto", base + "/b")
        monkeypatch.setenv("MH_TASK_ID", "task-a")
        assert ctl.call("snapshot")["title"] == "Page A"
        monkeypatch.setenv("MH_TASK_ID", "task-b")
        assert ctl.call("snapshot")["title"] == "Page B"
    finally:
        monkeypatch.delenv("MH_TASK_ID", raising=False)
        ctl.call("stop")


@needs_browser
def test_ctl_follows_a_new_tab(monkeypatch, tmp_path):
    ctl = _ctl(monkeypatch)
    base = _serve_pages({"/": "<title>Main</title><a href='/n' target='_blank'>Open</a>",
                         "/n": "<title>New tab</title><h1>new</h1>"})
    assert ctl.main(["start", str(tmp_path)]) == 0
    try:
        ctl.call("goto", base + "/")
        ctl.call("click", "1")
        time.sleep(0.8)
        assert ctl.call("snapshot")["title"] == "New tab"
    finally:
        ctl.call("stop")
