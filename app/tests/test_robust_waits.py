"""Three robustness problems persona testers hit.

1. A turn that stalls with no way out (the app says "Still waiting for the model" for ever, or shows nothing, and the
   notice has no retry). The server now says whether a model request is in flight (view.model_inflight); the page's
   tick (workTick) shows "This is taking longer than it should. Try again?" after 60 s with no request and no
   progress, and "Try again" on a real wait after 3 minutes.
2. `meshpool -p` printed nothing during a long model wait: it now writes "waiting for the model (N min)" to stderr
   every 60 s (MH_WAIT_NOTE_S shortens that for tests); the terminal UI shows it on the spinner line.
3. Two `meshpool ui` servers on one MESHPOOL_HOME ran against the same chats: the second one now stops and says where
   the first one is (ui.lock in the home folder, with the pid); a lock whose pid is gone is taken over.

A stuck fake server (accepts the request, never answers) stands in for the model."""
from __future__ import annotations

import glob
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app"

STUCK = r'''
import sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self.path.endswith(("/chat/completions", "/responses")):     # model-info probes get a quick no
            self.send_response(404); self.end_headers(); return
        with open(sys.argv[2], "a") as f:
            f.write("request " + self.path + "\n")
        time.sleep(600)
    def do_GET(self):
        self.send_response(404); self.end_headers()
    def log_message(self, *a):
        pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _until(fn, secs=30, every=0.2):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


def _env(tmp: Path, mport: int | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_|ANTHROPIC_|CLAUDE_|MESHPOOL_)", k)}
    home = tmp / "mh"
    home.mkdir(exist_ok=True)
    (tmp / "home").mkdir(exist_ok=True)
    if mport:
        (home / "meshpool.config").write_text(
            f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env.update(HOME=str(tmp / "home"), MESHPOOL_HOME=str(home), PYTHONPATH=str(APP), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"), MH_UI_OPEN_PAGE="1")
    return env


@pytest.fixture()
def stuck(tmp_path):
    """A model endpoint that takes every request and never answers. .log has one line per request it received."""
    port, log = _port(), tmp_path / "stuck.log"
    log.write_text("")
    script = tmp_path / "stuck_server.py"
    script.write_text(STUCK)
    p = subprocess.Popen([sys.executable, str(script), str(port), str(log)])
    _until(lambda: _alive(port), 15)
    yield {"port": port, "log": log, "n": lambda: len(log.read_text().splitlines())}
    p.kill()
    p.wait(10)


def _alive(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


# ============================================================ 2. the terminal says it is waiting
def test_the_terminal_names_a_long_wait_on_stderr_in_print_mode():
    from meshpool_harness.agent import tui as T
    err, out = io.StringIO(), io.StringIO()
    term = T.Terminal(print_mode=True, out=out, err=err)
    term.emit({"type": "model_start"})
    term.emit({"type": "model_wait", "secs": 61})
    term.emit({"type": "model_wait", "secs": 125})
    assert err.getvalue().splitlines() == ["waiting for the model (1 min)", "waiting for the model (2 min)"], err.getvalue()
    assert "waiting" not in out.getvalue()                     # stdout carries the answer only


def test_the_terminal_ui_shows_the_wait_on_its_status_line():
    from meshpool_harness.agent import tui as T
    term = T.Terminal(print_mode=False, out=io.StringIO(), err=io.StringIO(), interactive=True)
    term.emit({"type": "model_start"})
    term.emit({"type": "model_wait", "secs": 125})
    assert term._label == "Waiting for the model (2 min)", term._label


def test_print_mode_says_so_while_a_stuck_model_does_not_answer(tmp_path, stuck):
    """The real thing: `meshpool -p` against an endpoint that never answers writes the line to stderr well before any
    retry notice. MH_WAIT_NOTE_S=1 stands in for 60 s."""
    env = _env(tmp_path, stuck["port"])
    env.update(MH_WAIT_NOTE_S="1", MH_RECOVER_MIN="0")
    work = tmp_path / "work"
    work.mkdir()
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "--dir", str(work), "-p", "say hi", "--no-mcp"], cwd=str(work), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _until(lambda: stuck["n"]() >= 1, 60), "the model endpoint never got a request"
        time.sleep(4)
    finally:
        p.kill()
        out, err = p.communicate(timeout=20)
    assert re.search(r"^waiting for the model \(\d+ (min|s)\)$", err, re.M), (out, err)
    assert "waiting for the model" not in out


# ============================================================ 3. one ui server per home
def _ui(env: dict, port: int, tmp: Path, name: str):
    log = open(tmp / f"{name}.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)], cwd=str(APP),
                         env=env, stdout=log, stderr=subprocess.STDOUT)
    p._log = log
    return p


def _up(port: int, secs=60) -> bool:
    def ok():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3):
                return True
        except Exception:  # noqa: BLE001
            return False
    return bool(_until(ok, secs, 0.3))


def _stop(*procs):
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(10)
        p._log.close()


def test_a_second_ui_on_the_same_home_refuses_and_names_the_first(tmp_path):
    env = _env(tmp_path)
    p1p, p2p = _port(), _port()
    p1 = _ui(env, p1p, tmp_path, "first")
    try:
        assert _up(p1p), (tmp_path / "first.log").read_text()
        lock = json.loads((tmp_path / "mh" / "ui.lock").read_text())
        assert lock["pid"] == p1.pid and lock["port"] == p1p, lock
        p2 = _ui(env, p2p, tmp_path, "second")
        try:
            rc = p2.wait(60)
        finally:
            _stop(p2)
        text = (tmp_path / "second.log").read_text()
        assert rc == 1, (rc, text)
        assert f"meshpool is already running at http://127.0.0.1:{p1p}. Open that, or quit it first." in text, text
        assert not _alive(p2p)                                  # the second one never listened
        assert p1.poll() is None and _up(p1p)                   # and the first one is untouched
    finally:
        _stop(p1)


def test_a_lock_left_by_a_crash_is_taken_over(tmp_path):
    env = _env(tmp_path)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(10)
    (tmp_path / "mh" / "ui.lock").write_text(json.dumps({"pid": dead.pid, "port": 8765}))
    port = _port()
    p = _ui(env, port, tmp_path, "takeover")
    try:
        assert _up(port), (tmp_path / "takeover.log").read_text()
        lock = json.loads((tmp_path / "mh" / "ui.lock").read_text())
        assert lock == {"pid": p.pid, "port": port}, lock
    finally:
        _stop(p)
    assert not (tmp_path / "mh" / "ui.lock").exists() or p.returncode is not None


def test_servers_on_different_homes_run_side_by_side(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ea, eb = _env(a), _env(b)
    pa, pb = _port(), _port()
    sa, sb = _ui(ea, pa, a, "a"), _ui(eb, pb, b, "b")
    try:
        assert _up(pa) and _up(pb), ((a / "a.log").read_text(), (b / "b.log").read_text())
        assert json.loads((a / "mh" / "ui.lock").read_text())["port"] == pa
        assert json.loads((b / "mh" / "ui.lock").read_text())["port"] == pb
    finally:
        _stop(sa, sb)


# ============================================================ 1. a turn with no way out
sync_api = pytest.importorskip("playwright.sync_api")


def _launch(pw):
    try:
        return pw.chromium.launch()
    except Exception as first:  # noqa: BLE001
        roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers", str(Path.home() / ".cache" / "ms-playwright")]
        cands = [os.environ.get("MH_TEST_CHROMIUM") or ""]
        for r in filter(None, roots):
            cands += sorted(glob.glob(os.path.join(r, "chromium_headless_shell-*", "*", "chrome-headless-shell")))
            cands += sorted(glob.glob(os.path.join(r, "chromium-*", "chrome-linux*", "chrome")))
        for exe in filter(None, cands):
            try:
                return pw.chromium.launch(executable_path=exe)
            except Exception:  # noqa: BLE001
                continue
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no Chromium for Playwright here: {first}")


@pytest.fixture()
def app(tmp_path, stuck):
    env = _env(tmp_path, stuck["port"])
    port = _port()
    work = tmp_path / "work"
    work.mkdir()
    p = _ui(env, port, tmp_path, "ui")
    try:
        assert _up(port), (tmp_path / "ui.log").read_text()
        yield {"base": f"http://127.0.0.1:{port}", "work": str(work)}
    finally:
        _stop(p)


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_context(viewport={"width": 1280, "height": 820}).new_page()
        pg.set_default_timeout(20000)
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); "
                           "localStorage.removeItem('mp_last_chat'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined && S.threads", timeout=30000)
        pg.app = app
        yield pg
        b.close()


NOTE = """() => { const n = document.querySelector('#waitNote'); if (!n) return null; const r = n.querySelector('[data-retry]');
    return {text: n.querySelector('.wn').textContent, retry: !!r && !r.hidden, stop: !!n.querySelector('[data-stop]')}; }"""


def _fake_chat(page, cid="t-robust"):
    page.evaluate("""cid => { openThread(cid, true); if (S.cur.pollT) { clearTimeout(S.cur.pollT); clearInterval(S.cur.pollT); }
        S.cur.pollT = null; stopStream(S.cur); S.cur.stage = null; $('#col').replaceChildren(); $('#col').classList.remove('loading'); }""", cid)


def _working(page, inflight, quiet_s):
    """A turn that has been working for quiet_s seconds with nothing arriving; `inflight` is what the server reports."""
    page.evaluate("""([inflight, q]) => { const cur = S.cur; cur.log = []; cur.q = []; const u = {type: 'user', text: 'make the memo shorter', i: 0}; logEv(cur, u); applyEv(u);
        cur.view.state = 'working'; cur.view.model_inflight = inflight; cur.working = true;
        cur.since = Date.now() - q * 1000; cur.lastEvAt = 0; workTick(cur); }""", [inflight, quiet_s])


def test_a_turn_with_no_request_and_no_progress_says_so_and_offers_try_again(page):
    _fake_chat(page)
    _working(page, False, 75)
    note = page.evaluate(NOTE)
    assert note and note["text"] == "This is taking longer than it should. Try again?" and note["retry"] and note["stop"], note
    _working(page, False, 45)                               # not yet 60 s: the ordinary line
    note = page.evaluate(NOTE)
    assert note and note["text"].startswith("Still waiting for the model") and not note["retry"], note
    assert page.errors == [], page.errors


def test_a_real_wait_keeps_its_line_and_gets_try_again_after_three_minutes(page):
    _fake_chat(page)
    _working(page, True, 100)
    note = page.evaluate(NOTE)
    assert note and note["text"].startswith("Still waiting for the model (1 min)") and not note["retry"], note
    _working(page, True, 200)
    note = page.evaluate(NOTE)
    assert note and note["text"].startswith("Still waiting for the model (3 min)") and note["retry"], note
    assert page.errors == [], page.errors


def test_try_again_stops_the_turn_then_sends_the_last_message_again(page):
    _fake_chat(page)
    _working(page, False, 75)
    page.evaluate("""() => { window.__calls = []; stopNow = async () => { __calls.push('stop'); };
        sendNow = async () => { __calls.push('send:' + document.querySelector('#input').value); }; }""")
    page.click("#waitNote [data-retry]")
    page.wait_for_function("() => __calls.length === 2")
    assert page.evaluate("() => __calls") == ["stop", "send:make the memo shorter"]


def test_the_server_says_whether_a_model_request_is_in_flight(page, stuck):
    """A chat whose model never answers: view.model_inflight is true while the request is out. Then Try again, on the
    real server, stops it and sends the message again (the endpoint sees a second request)."""
    cid = page.evaluate("""async w => { const v = await post('/api/agent', {workdir: w, harness: 'meshpool-work', kind: 'cowork',
        mode: 'auto', task: 'say hi'});
        await post('/api/agent/' + v.id + '/send', {text: 'say hi', since: 1e9}); return v.id; }""", page.app["work"])
    assert _until(lambda: stuck["n"]() >= 1, 60), "the model endpoint never got a request"
    v = page.evaluate("id => api('/api/agent/' + id + '?since=0')", cid)
    assert v["state"] in ("working", "waiting") and v["model_inflight"] is True, {k: v.get(k) for k in ("state", "model_inflight")}
    page.evaluate("id => openThread(id)", cid)
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.model_inflight === true && S.cur.log.some(e => e.type === 'user')",
                           timeout=30000)
    page.evaluate("() => { const cur = S.cur; cur.since = Date.now() - 200000; cur.lastEvAt = 0; cur.working = true; }")
    page.wait_for_function("() => document.querySelector('#waitNote [data-retry]') && !document.querySelector('#waitNote [data-retry]').hidden",
                           timeout=20000)
    page.click("#waitNote [data-retry]")
    assert _until(lambda: stuck["n"]() >= 2, 60), "Try again did not send the message again"
    assert page.errors == [], page.errors


def test_a_lock_whose_pid_was_reused_but_whose_port_is_dead_is_taken_over(tmp_path, monkeypatch):
    """Release review: after a crash, an unrelated process can get the old pid. If the lock's port does not answer, the
    lock is stale and taken over, instead of refusing until that process ends."""
    import json, os, socket
    from meshpool_harness.ui import instance as I
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); dead = s.getsockname()[1]; s.close()     # a port nothing listens on
    parent = os.getppid()                                                                   # alive, not us
    (tmp_path / "ui.lock").write_text(json.dumps({"pid": parent, "port": dead}))
    assert I.claim() is None
    assert json.loads((tmp_path / "ui.lock").read_text())["pid"] == os.getpid()
    I.release()
