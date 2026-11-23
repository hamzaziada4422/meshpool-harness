"""Undo buttons stay on every finished turn that changed files (X-05), and Rewind / Fork from a message (G-10), clicked
in a real browser against the real app server with the mock model (testkit/mock_llm.py writes the file a message names)."""
from __future__ import annotations

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
    tmp = tmp_path_factory.mktemp("undorewind")
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
    procs = [subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    log = open(tmp / "ui.log", "w")
    procs.append(subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                  cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT))
    base = f"http://127.0.0.1:{uport}"
    try:
        tok = re.search(r'token: "([^"]+)"', _wait(base + "/")).group(1)
        req = urllib.request.Request(base + "/api/projects", data=json.dumps({"action": "add", "path": str(proj)}).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        yield {"base": base, "proj": str(proj), "log": tmp / "ui.log"}
    finally:
        for p in procs:
            p.kill()
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
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def _send(pg, text):
    pg.fill("#input", text)
    pg.press("#input", "Enter")


def _idle(pg, app, n_users):
    try:
        pg.wait_for_function("n => S.cur && S.cur.view && S.cur.view.state === 'idle' && "
                             "document.querySelectorAll('#col .msg-user').length === n && document.querySelector('#working') === null",
                             arg=n_users, timeout=90000)
    except Exception:  # noqa: BLE001
        pytest.fail("the chat did not finish\n" + app["log"].read_text(errors="replace")[-2000:])


def _undos(pg):
    return pg.locator("#col .turnfoot [data-undo]").count()


def test_undo_stays_on_every_turn_and_rewind_and_fork(page, app):
    proj = Path(app["proj"])
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    _send(page, "make the file one.txt")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    _idle(page, app, 1)
    sid = page.evaluate("S.cur.id")
    _send(page, "make the file two.txt")
    _idle(page, app, 2)
    page.wait_for_function("() => document.querySelectorAll('#col .turnfoot [data-undo]').length === 2", timeout=30000)
    assert (proj / "one.txt").exists() and (proj / "two.txt").exists()

    # X-05: Undo on the second reply, then the first reply still has its Undo, also after a reload
    page.locator("#col .turnfoot [data-undo]").nth(1).click()
    page.click(".dlg [data-a='1']")
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 1", timeout=30000)
    assert not (proj / "two.txt").exists() and (proj / "one.txt").exists()
    page.wait_for_function("() => document.querySelectorAll('#col .turnfoot [data-undo]').length === 1", timeout=30000)
    assert page.evaluate("document.querySelector('#input').value") == "make the file two.txt"
    page.reload()
    page.wait_for_function("id => S.cur && S.cur.id === id && document.querySelectorAll('#col .msg-user').length === 1",
                           arg=sid, timeout=60000)
    page.wait_for_function("() => document.querySelectorAll('#col .turnfoot [data-undo]').length === 1", timeout=30000)
    assert _undos(page) == 1

    # G-10: Rewind to the second message
    page.fill("#input", "")
    _send(page, "make the file two.txt")
    _idle(page, app, 2)
    assert (proj / "two.txt").exists()
    page.locator("#col .msg-user").nth(1).hover()
    page.locator("#col .msg-user").nth(1).locator("[data-ma='rewind']").click()
    page.click(".dlg [data-a='1']")                                   # "also undo the file changes" is ticked
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 1", timeout=30000)
    assert page.evaluate("document.querySelector('#input').value") == "make the file two.txt"
    assert not (proj / "two.txt").exists() and (proj / "one.txt").exists()
    assert "Made two.txt." not in page.inner_text("#col")
    page.wait_for_function("() => document.querySelectorAll('#col .turnfoot [data-undo]').length === 1", timeout=30000)

    # Fork from the first message: a new chat with the first exchange, the original untouched
    page.locator("#col .msg-user").nth(0).hover()
    page.locator("#col .msg-user").nth(0).locator("[data-ma='fork']").click()
    page.wait_for_function("id => S.cur && S.cur.id && S.cur.id !== id", arg=sid, timeout=60000)
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 1 && "
                           "document.querySelector('#col').innerText.includes('Made one.txt.')", timeout=30000)
    assert (page.evaluate("S.cur.view.title || ''") or "").startswith("Fork of")
    assert (proj / "one.txt").exists()
    assert page.errors == [], page.errors
