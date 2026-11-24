"""Files attached to the first message of a new chat go into that chat's own folder; a folder that cannot be written
gives one friendly message and no empty chat; a delete finishes by itself after the page is gone.
F-34, X-03, F-35, M06-02."""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- unit
def _bare_session(workdir: str, monkeypatch):
    s = A.Session.__new__(A.Session)
    s.workdir, s.state, s.queue, s.events = workdir, "idle", [], []
    s.lock = threading.RLock()
    s.agent = object()
    s.harness = {"type": "x", "id": "x"}
    started, emitted = [], []
    monkeypatch.setattr(A.Session, "_start_turn", lambda self, t, i, *a: started.append(t))   # *a: the turn token (ws09)
    s.emit = lambda e: emitted.append(e)
    return s, started, emitted


def _b64(t: str) -> str:
    return base64.b64encode(t.encode()).decode()


def test_send_saves_files_into_the_scratch_folder_and_lists_them(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness import project_store as PS
    wd = Path(PS.new_scratch())
    s, started, _ = _bare_session(str(wd), monkeypatch)
    s.send("look", files=[{"name": "a.txt", "data": _b64("hello")}])
    assert (wd / "a.txt").read_text() == "hello"            # S-05: attachments sit at the folder root
    assert started and "look" in started[0]
    assert "a.txt" in started[0] and "Attached files" in started[0]


def test_send_into_a_read_only_folder_is_a_friendly_error_and_nothing_starts(tmp_path, monkeypatch):
    ro = tmp_path / "ro"
    ro.mkdir()
    s, started, emitted = _bare_session(str(ro), monkeypatch)
    real_open = open

    def deny(p, mode="r", *a, **k):
        if str(p).startswith(str(ro)) and "x" in mode:
            raise PermissionError(13, "Permission denied")
        return real_open(p, mode, *a, **k)
    monkeypatch.setattr("builtins.open", deny)
    with pytest.raises(ValueError) as e:
        s.send("hi", files=[{"name": "a.txt", "data": _b64("x")}])
    assert "read-only" in str(e.value) and "Traceback" not in str(e.value) and "Errno" not in str(e.value)
    assert not started and not emitted


def test_attach_other_oserror_is_friendly(tmp_path, monkeypatch):
    import errno
    real_open = open

    def full(p, mode="r", *a, **k):
        if "x" in mode:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(p, mode, *a, **k)
    monkeypatch.setattr("builtins.open", full)
    with pytest.raises(ValueError) as e:
        W.attach_to(tmp_path, "b.txt", b"x")
    assert str(e.value).startswith("Couldn't save b.txt: No space")


# ---------------------------------------------------------------- browser
sync_api = pytest.importorskip("playwright.sync_api")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> str:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    pytest.fail(f"nothing answered at {url} within {secs} s")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("attfirst")
    home, mh, proj, ro = tmp / "home", tmp / "mh", tmp / "proj", tmp / "ro"
    for d in (home, mh, proj, ro):
        d.mkdir()
    mport, uport = _port(), _port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_DELETE_GRACE_S="1.5")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")
    procs = [subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    log = open(tmp / "ui.log", "w")
    procs.append(subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                  cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT))
    base = f"http://127.0.0.1:{uport}"
    try:
        tok = re.search(r'token: "([^"]+)"', _wait(base + "/")).group(1)

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"{}")
        call("/api/projects", {"action": "add", "path": str(proj)})
        yield {"base": base, "proj": str(proj), "ro": str(ro), "mh": mh, "call": call, "log": tmp / "ui.log"}
    finally:
        for p in procs:
            p.kill()
        log.close()
        subprocess.run(["chattr", "-i", str(ro)], capture_output=True)
        os.chmod(ro, 0o755)


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def _threads(app):
    return app["call"]("/api/agent")


def _rows(r):
    return r["sessions"]


def _attach_text(pg, name="note.txt", body="hello attach"):
    pg.evaluate("""([n, b]) => addAttachments([new File([b], n, {type: 'text/plain'})])""", [name, body])
    pg.wait_for_function("() => S.attachments.length === 1")


def test_file_on_the_first_message_lands_in_the_new_chats_own_folder(page, app):
    before = len(_rows(_threads(app)))
    page.click("#newThread")
    _attach_text(page)
    page.fill("#input", "Read my note")
    page.press("#input", "Enter")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    wd = Path(page.evaluate("S.cur.view.workdir"))
    page.wait_for_function("() => document.querySelector('#conv, #col') && document.body.innerText.includes('note.txt')", timeout=30000)
    assert (wd / "note.txt").read_text() == "hello attach"     # S-05: attachments sit at the folder root
    assert wd.parent.name == "chats"
    page.wait_for_timeout(800)
    assert len(_rows(_threads(app))) == before + 1
    assert page.errors == []


def test_unwritable_folder_gives_one_toast_no_empty_chat_and_keeps_the_text(page, app):
    os.chmod(app["ro"], 0o555)
    immutable = False
    if os.geteuid() == 0:                          # root ignores mode bits: make the folder immutable instead
        immutable = subprocess.run(["chattr", "+i", app["ro"]], capture_output=True).returncode == 0
        if not immutable:
            pytest.skip("root can write to a read-only folder here")
    before = len(_rows(_threads(app)))
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["ro"])
    _attach_text(page)
    page.evaluate("window.__toasts = []; new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { if (n.className && String(n.className) === 'toast') window.__toasts.push(n.innerText); }))).observe(document.body, {childList: true, subtree: true})")
    page.fill("#input", "Read my note")
    page.press("#input", "Enter")
    page.wait_for_function("() => window.__toasts.length > 0", timeout=30000)
    page.wait_for_timeout(1500)
    toasts = page.evaluate("window.__toasts")
    assert len(toasts) == 1 and "read-only" in toasts[0], toasts
    assert page.input_value("#input") == "Read my note"
    assert len(_rows(_threads(app))) == before
    assert page.evaluate("S.cur") is None
    if immutable:
        subprocess.run(["chattr", "-i", app["ro"]])


def test_delete_finishes_on_the_server_after_a_reload(page, app):
    r = app["call"]("/api/agent", {"workdir": "", "harness": "meshpool-auto", "task": "x"})
    sid, wd = r["id"], Path(r["workdir"])
    sess = Path(app["mh"]) / "sessions" / sid
    page.reload()
    page.wait_for_function("() => typeof S === 'object' && S.threads", timeout=30000)
    page.evaluate("id => threadAct(id, 'delete')", sid)
    page.wait_for_timeout(300)
    page.reload()
    end = time.time() + 6
    while time.time() < end and (sess.exists() or wd.exists()):
        time.sleep(0.3)
    assert not sess.exists() and not wd.exists()
    assert sid not in [t["id"] for t in _rows(_threads(app))]
