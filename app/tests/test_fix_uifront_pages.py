"""Front-end fixes (pages.js, settings2.js, app.js, shell.js): checked in a real browser against the real app server."""
from __future__ import annotations

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
    tmp = tmp_path_factory.mktemp("uifront")
    home, mh = tmp / "home", tmp / "mh"
    for d in (home, mh):
        d.mkdir()
    uport = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{uport}"
    try:
        _wait(base + "/")
        yield {"base": base}
    finally:
        p.kill()
        log.close()


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
        pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def test_page_search_keeps_the_caret_while_typing(page):
    page.evaluate("goPage('artifacts')")
    page.wait_for_selector("#gSearch", timeout=15000)
    page.click("#gSearch")
    page.keyboard.type("abc", delay=250)
    page.wait_for_timeout(400)
    assert page.input_value("#gq") == "abc"


def test_settings_group_ids_open_their_first_page(page):
    for grp in ("privacy", "account", "code"):
        page.evaluate("g => openSettings(g)", grp)
        page.wait_for_function("() => document.querySelector('#mbody') && !document.querySelector('#mbody').classList.contains('loading') && document.querySelector('#mbody').children.length > 0", timeout=15000)
        page.evaluate("closeSettings()")
    assert page.errors == [], page.errors


def test_leaving_a_page_drops_its_column_handlers(page):
    page.evaluate("() => { $('#col').ondrop = () => {}; $('#col').onkeydown = () => {}; }")
    page.evaluate("newThread()")
    assert page.evaluate("() => [$('#col').ondrop, $('#col').onkeydown, $('#col').onclick]") == [None, None, None]


def test_markdown_dollar_pairs_and_parenthesised_links(page):
    html = page.evaluate("md('costs $$5 and $$10 and [A](https://en.wikipedia.org/wiki/A_(b)) ok')")
    assert "mathsrc" not in html
    assert 'href="https://en.wikipedia.org/wiki/A_(b)"' in html and "</a> ok" in html


def test_edit_task_sends_only_what_changed(page):
    page.evaluate("""() => { window._p = taskDialog({id: 'x1', name: 'N', prompt: 'P', when: 'weekly', time: '16:45', day: 'fri', only_on_change: true}).then(v => v); }""")
    page.click(".dlg [data-a='1']")
    v = page.evaluate("window._p")
    assert v["name"] == "N" and v["prompt"] == "P"
    assert "time" not in v and "day" not in v and "at" not in v and "minutes" not in v
    page.evaluate("""() => { window._p = taskDialog({name: 'T', prompt: 'P', when: 'daily', time: '09:00', kind: 'code'}); }""")
    page.click(".dlg [data-a='1']")
    assert page.evaluate("window._p")["kind"] == "code"
