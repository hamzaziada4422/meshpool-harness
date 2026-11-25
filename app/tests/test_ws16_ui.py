"""ws16 front end, in a real browser against the real app server: the MCP library's Add (F-29), the per-server colour
of Check servers (F-31)."""
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
    tmp = tmp_path_factory.mktemp("ws16ui")
    home, mh = tmp / "home", tmp / "mh"
    for d in (home, mh):
        d.mkdir()
    uport = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_LOGIN_ENV="0")
    env.pop("MESHPOOL_CONFIG", None)
    env.pop("MESHPOOL_MCP", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{uport}"
    try:
        _wait(base + "/")
        yield {"base": base, "home": mh}
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
        pg.on("pageerror", lambda e: errors.append(str(e) + " :: " + str(getattr(e, "stack", ""))[:600]))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def test_library_add_has_no_page_error(page):
    page.evaluate("openSettings('mcp')")
    page.wait_for_selector("[data-ma]", timeout=15000)
    first = page.locator("[data-ma]").first
    ident = first.get_attribute("data-ma")
    first.click()
    page.wait_for_function("(id) => { const li = document.querySelector(`[data-me='${id}']`); return !li || !li.querySelector('[data-ma]'); }",
                           arg=ident, timeout=15000)
    page.wait_for_timeout(500)
    assert page.errors == [], page.errors
    assert page.locator(f"[data-me='{ident}'] .badge").count() >= 1       # now shown as added


def test_check_servers_colours_each_server(page, app):
    import json
    (app["home"] / "mcp.json").write_text(json.dumps({"mcpServers": {
        "good": {"command": sys.executable, "args": [str(REPO / "app" / "tests" / "ws16_stdio_server.py"), str(app["home"])]},
        "broken": {"command": "definitely-not-a-command-ws16"}}}))
    page.evaluate("openSettings('mcp')")
    page.wait_for_selector("#mTest", timeout=15000)
    page.click("#mTest")
    page.wait_for_selector("#mRes .srv", timeout=60000)
    assert page.locator("#mRes .srv.ok").count() >= 1 and page.locator("#mRes .srv.bad").count() >= 1
    assert "bad" not in (page.get_attribute("#mRes", "class") or "").split()      # the box itself is not painted red
    good = page.locator("#mRes .srv", has_text="good").first
    assert "ok" in good.get_attribute("class")
    assert page.errors == [], page.errors
