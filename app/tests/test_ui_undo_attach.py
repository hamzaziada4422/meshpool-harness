"""Undo and Rewind (with "also undo files") delete the copy of a file attached to the message. The message goes back to
the box without a chip for the deleted copy, and a note says it was removed (release review, item 14). Real browser,
real app server, mock model."""
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
    (proj / "data.csv").write_text("a,b\n1,2\n")
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


def _undo_case(page, app, how):
    proj = Path(app["proj"])
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    page.evaluate("""() => addAttachments([new File(["a,b\\n9,9\\n"], "data.csv", {type: "text/csv"})])""")
    page.wait_for_function("() => S.attachments.length === 1")
    _send(page, f"make the file {how}.txt")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    _idle(page, app, 1)
    # round 3 (Lina): the dropped file is kept with the chat, never copied into the folder next to his own data.csv
    assert not (proj / "data (2).csv").exists() and (proj / f"{how}.txt").exists()
    page.wait_for_function("() => document.querySelectorAll('#col .turnfoot [data-undo]').length === 1", timeout=30000)
    if how == "undo":
        page.locator("#col .turnfoot [data-undo]").click()
    else:
        page.locator("#col .msg-user").nth(0).hover()
        page.locator("#col .msg-user").nth(0).locator("[data-ma='rewind']").click()
    page.click(".dlg [data-a='1']")
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 0", timeout=30000)
    assert not (proj / f"{how}.txt").exists() and (proj / "data.csv").read_text() != "a,b\n9,9\n"
    assert page.evaluate("document.querySelector('#input').value") == f"make the file {how}.txt"
    assert page.evaluate("S.attachments.map(a => a.name)") == ["data.csv"]    # still kept with the chat: its chip is back
    assert "Attached files" not in page.evaluate("document.querySelector('#input').value")
    assert "was removed with the undo" not in page.evaluate("document.querySelector('#toasts')?.innerText || ''")
    assert page.errors == [], page.errors


def test_undo_does_not_put_back_a_chip_for_the_copy_it_deleted(page, app):
    _undo_case(page, app, "undo")


def test_rewind_with_files_does_not_put_back_a_chip_for_the_copy_it_deleted(page, app):
    _undo_case(page, app, "rewind")
