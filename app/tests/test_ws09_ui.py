"""Turn state in the page (ws09): reopened chats, Send while starting, retry notices, the Working status.

Runs the real app server with the mock model and a real Chromium, as test_ui_new_chat.py does.
"""
from __future__ import annotations

import glob
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

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]


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
    tmp = tmp_path_factory.mktemp("ws09ui")
    home, mh, proj = tmp / "home", tmp / "mh", tmp / "proj"
    for d in (home, mh, proj):
        d.mkdir()
    (proj / "notes.txt").write_text("hello\n")
    mport, uport = _port(), _port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")        # never the real slot folder (conftest checks it)
    mock = subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = open(tmp / "ui.log", "w")
    base = f"http://127.0.0.1:{uport}"
    st = {"ui": None, "base": base, "proj": str(proj), "mh": mh, "log": tmp / "ui.log"}

    def start():
        st["ui"] = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                    cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
        st["token"] = re.search(r'token: "([^"]+)"', _wait(base + "/")).group(1)

    def stop():
        if st["ui"]:
            st["ui"].kill()
            st["ui"].wait(timeout=15)
            st["ui"] = None

    st["start"], st["stop"] = start, stop
    try:
        start()
        req = urllib.request.Request(base + "/api/projects", data=json.dumps({"action": "add", "path": str(proj)}).encode(),
                                     headers={"X-Meshpool-Token": st["token"], "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        yield st
    finally:
        stop()
        mock.kill()
        log.close()


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")     # a Chromium of another Playwright version, for local runs
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise                                 # CI installs Chromium: a missing browser must not pass quietly
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.console_errors = []
        pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.errors = errors
        pg.browser = b
        yield pg
        b.close()


def _api(app, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(app["base"] + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Meshpool-Token": app["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        pytest.fail(f"{path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:500]}")


def _make_chat(app, text="Say hello") -> str:
    """A finished one-message chat, made the way the page makes it (sendNow, app.js)."""
    v = _api(app, "/api/agent", {"workdir": app["proj"], "project_id": "", "harness": "meshpool-code", "kind": "code",
                                 "model": "", "mode": "auto", "plan": False, "style": "", "task": text,
                                 "incognito": False, "memory_exclude": []})
    _api(app, f"/api/agent/{v['id']}/send", {"text": text, "images": [], "since": 1e9, "interrupt": False})
    end = time.time() + 90
    while time.time() < end:
        s = _api(app, f"/api/agent/{v['id']}?since=0")
        if s.get("state") == "idle" and any(e.get("type") == "turn_end" for e in s.get("events", [])):
            return v["id"]
        time.sleep(0.5)
    pytest.fail("the first chat did not finish\n" + app["log"].read_text(errors="replace")[-2000:])


def _open(page, cid):
    page.evaluate("id => openThread(id)", cid)
    page.wait_for_function("id => S.cur && S.cur.id === id && S.cur.view && S.cur.view.state && !S.cur.view.prefill", arg=cid, timeout=30000)
    try:
        page.wait_for_selector("#col .msg-user", timeout=30000)
    except Exception:  # noqa: BLE001
        pytest.fail(page.evaluate("JSON.stringify({col: (document.querySelector('#col')||{}).innerHTML.slice(0,500), total: S.cur.total, q: S.cur.q.length, stage: !!S.cur.stage, st: S.cur.view.state})")
                    + str(page.errors))


def test_reopened_chat_has_no_live_cards_or_spinners(page, app):
    cid = _make_chat(app)
    found = lambda: [Path(p) for p in glob.glob(str(app["mh"] / "**" / cid / "transcript.json"), recursive=True)]  # noqa: E731
    end = time.time() + 30
    while not found() and time.time() < end:         # the transcript is saved a moment after the turn
        time.sleep(0.3)
    time.sleep(1.5)
    app["stop"]()                                    # the app exits with things left open
    tr = found()[0]
    data = json.loads(tr.read_text(encoding="utf-8"))
    sys.path.insert(0, str(REPO / "app"))
    from meshpool_harness.agent import ask as ASK
    qs = ASK.normalize({"questions": [{"question": "Which one?", "options": ["A", "B"]}]})
    extra = [{"type": "approval", "id": n, "kind": "bash", "title": "Run a command", "detail": f"ls {n}"} for n in range(1, 37)]
    extra += [{"type": "tool_start", "id": f"t{n}", "name": "bash", "args": {"command": f"ls {n}"}} for n in range(30)]
    extra += [{"type": "question", "id": "qq1", "questions": qs, "timeout_s": 600}]
    data["events"] = data["events"] + extra
    data["running"] = False
    tr.write_text(json.dumps(data), encoding="utf-8")
    app["start"]()
    page.goto(app["base"] + "/")
    page.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
    _open(page, cid)
    page.wait_for_selector("#col .appr", timeout=30000)
    page.wait_for_timeout(2500)
    got = page.evaluate("""() => ({
        approvals: document.querySelectorAll('.appr').length,
        liveBtns: [...document.querySelectorAll('.appr button')].filter(b => !b.disabled).length,
        running: document.querySelectorAll('.st.run').length,
        liveQ: document.querySelectorAll('.qcard:not(.done)').length,
        state: S.cur.view.state, open: JSON.stringify(S.cur.view.open), tail: S.cur.log.slice(-6).map(e => e.type + ':' + (e.id||'') + (e.closed?'c':'')).join(' '), q: S.cur.q.length, total: S.cur.total})""")
    diag = lambda: f"{got}\npage errors: {page.errors}\nconsole errors: {page.console_errors}\napp log tail:\n" + "\n".join(app["log"].read_text(errors="replace").splitlines()[-40:])  # noqa: E731
    assert got["approvals"] == 36 and got["state"] == "idle", diag()
    assert got["liveBtns"] == 0 and got["running"] == 0 and got["liveQ"] == 0, diag()
    # Nothing in the conversation may still animate (the dots of an open work group, the spinners of steps that never ended).
    # Animations outside the chat column (the page's own chrome) are not what this checks.
    anims = page.evaluate("""() => document.getAnimations().filter(a => a.playState === 'running').map(a => {
        const t = a.effect && a.effect.target; return {name: a.animationName || a.id || a.constructor.name,
        inChat: !!(t && t.closest && t.closest('#conv, #col')), el: t && t.className}; })""")
    print("running animations:", anims)
    assert [a for a in anims if a["inChat"]] == [], anims
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")
    task = lambda: next(m["value"] for m in cdp.send("Performance.getMetrics")["metrics"] if m["name"] == "TaskDuration")  # noqa: E731
    t0 = task()
    page.wait_for_timeout(3000)
    busy = task() - t0
    print(f"idle CPU (TaskDuration over 3 s): {busy:.3f} s")
    assert busy < 0.3, busy
    assert page.errors == [], page.errors


def test_old_poll_does_not_close_a_newer_card(page, app):
    cid = _make_chat(app, "Say ok")
    _open(page, cid)
    page.evaluate("""() => applyEv({type: "approval", id: "ap-x1", i: 100, kind: "bash", title: "Run?", detail: "ls"})""")
    live = "() => [...document.querySelectorAll('#appr-ap-x1 button')].filter(b => !b.disabled).length"
    assert page.evaluate(live) > 0
    page.evaluate("""() => reconcileOpen(S.cur, {open: {approvals: [], question: null}, total: 50, state: "working"})""")
    assert page.evaluate(live) > 0                      # an older view did not include the card: it stays
    page.evaluate("""() => reconcileOpen(S.cur, {open: {approvals: [], question: null}, total: 101, state: "idle"})""")
    assert page.evaluate(live) == 0                     # a view that includes it and no longer lists it closes it
    assert page.evaluate("() => document.querySelector('#appr-ap-x1').classList.contains('done')")
    assert page.errors == [], page.errors


def test_send_enabled_while_starting(page, app):
    cid = _make_chat(app, "Say hi")
    _open(page, cid)
    page.evaluate("() => { S.cur.view.state = 'starting'; renderBar(); }")
    assert page.evaluate("cfg().starting") is True
    assert page.evaluate("document.querySelector('#sendBtn').disabled") is False
    assert page.errors == [], page.errors


def test_retry_notice_cleared(page, app):
    cid = _make_chat(app, "Say hey")
    _open(page, cid)
    n = lambda: page.evaluate("document.querySelectorAll('#col .note.retry').length")  # noqa: E731
    ap = lambda ev: page.evaluate("ev => applyEv(ev)", ev)  # noqa: E731
    ap({"type": "status", "text": "The model is busy. Trying again (2 of 5)…", "retry": True})
    assert n() == 1
    ap({"type": "retry_ok"})
    assert n() == 0
    # an old transcript has no flag: the text gives it away, and the reply that follows clears it
    ap({"type": "status", "text": "Rate limited by the endpoint. Waiting 10 s."})
    ap({"type": "status", "text": "The model is busy. Trying again (1 of 5)…"})
    assert n() == 2
    ap({"type": "assistant", "text": "Done."})
    assert n() == 0
    # a step that starts means the call went through
    ap({"type": "status", "text": "Trying again without streaming."})
    assert n() == 1
    ap({"type": "tool_start", "id": "tx1", "name": "bash", "args": {"command": "ls"}})
    assert n() == 0
    # a turn that ends in an error keeps the notices
    ap({"type": "status", "text": "The model is busy. Trying again (5 of 5)…", "retry": True})
    ap({"type": "error", "text": "The model did not answer."})
    assert n() == 1
    assert page.errors == [], page.errors


def test_stale_card_does_not_hide_working(page, app):
    cid = _make_chat(app, "Say ok")
    _open(page, cid)
    page.evaluate("""() => { const d = document.createElement('div'); d.className = 'qcard'; d.id = 'q-stale';
        colEl().appendChild(d); S.sendHold = 0;
        S.cur.working = true; S.cur.view.state = 'working'; workTick(S.cur); }""")
    assert page.evaluate("!!document.querySelector('#working')")
    # a chat the server says is waiting on the user shows no Working line
    page.evaluate("() => { S.cur.working = false; S.cur.view.state = 'waiting'; workTick(S.cur); }")
    assert not page.evaluate("!!document.querySelector('#working')")
    assert page.errors == [], page.errors
