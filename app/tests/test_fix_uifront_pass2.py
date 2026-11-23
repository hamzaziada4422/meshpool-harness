"""Front-end fixes, second pass: checked in a real browser against the real app server."""
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


def test_agent_slash_command_opens_the_menu_without_errors(page):
    page.evaluate("runSlash('/agent')")
    page.wait_for_timeout(300)
    assert page.errors == [], page.errors


def test_failed_chat_creation_clears_the_sending_status(page):
    page.route("**/api/agent", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"boom"}') if r.request.method == "POST" else r.continue_())
    page.evaluate("S.settings.ready = true")
    page.fill("#input", "hello")
    page.evaluate("sendNow(false)")
    page.wait_for_timeout(800)
    assert page.evaluate("!document.querySelector('#working')")
    assert page.input_value("#input") == "hello"


def test_composer_is_enabled_again_after_a_read_only_chat(page):
    page.evaluate("$('#input').disabled = true")
    page.evaluate("newThread()")
    assert page.evaluate("$('#input').disabled") is False


def test_new_chat_clears_the_box_and_incognito(page):
    page.fill("#input", "half typed")
    page.evaluate("S.draft.incognito = true; $('#input').value = 'half'; sessionStorage.removeItem('mp_draft_new'); newThread()")
    assert page.input_value("#input") == ""
    assert page.evaluate("S.draft.incognito") is False


def test_message_and_composer_get_auto_direction(page):
    assert page.get_attribute("#input", "dir") == "auto"


def test_side_panel_collapses_the_sidebar_at_tablet_width(page):
    page.set_viewport_size({"width": 800, "height": 800})
    page.evaluate("S.panel = 'progress'; fitPanel()")
    assert page.evaluate("document.querySelector('#app').classList.contains('autoside')")


def test_memory_tabs_are_all_reachable_on_a_phone(page):
    page.set_viewport_size({"width": 390, "height": 800})
    page.reload()
    page.wait_for_selector("#newThread", state="attached", timeout=30000)
    page.wait_for_function("() => typeof S === 'object' && S.settings", timeout=30000)
    page.evaluate("goPage('memory')")
    page.wait_for_selector("[data-mtab]", timeout=15000)
    boxes = page.evaluate("[...document.querySelectorAll('[data-mtab]')].map(b => { const r = b.getBoundingClientRect(); return [r.left, r.right]; })")
    assert boxes and all(l >= 0 and r <= 390 for l, r in boxes), boxes


def test_settings_search_shows_an_empty_state(page):
    page.evaluate("openSettings('profile')")
    page.fill("#mnavq", "zzzzqq")
    assert page.is_visible("#mnavNone")
    page.fill("#mnavq", "")
    assert not page.query_selector("#mnavNone")


def test_tab_stays_inside_the_settings_dialog(page):
    page.evaluate("openSettings('profile')")
    for _ in range(40):
        page.keyboard.press("Tab")
    assert page.evaluate("!!document.activeElement.closest('.scrim')")


def test_delete_needs_no_page_timer(page):
    """The server finishes a delete by itself (test_chat_delete.py): the page keeps no timer that a reload could lose."""
    assert page.evaluate("typeof S.delFlush") == "undefined"


def test_back_returns_to_the_previous_page(page):
    page.evaluate("goPage('artifacts')")
    page.evaluate("goPage('memory')")
    page.go_back()
    page.wait_for_timeout(300)
    assert page.evaluate("S.page") == "artifacts"
