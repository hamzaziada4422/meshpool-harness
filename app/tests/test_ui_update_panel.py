"""The update bar and the About panel's update section, in a real browser, with the update endpoints faked.

The Python back end is real (the page loads and talks to it), but /api/update/* and /healthz are answered by
page.route() so a test can put the updater in any state and count what the page asked for.
"""
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
    tmp = tmp_path_factory.mktemp("updpanel")
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
    proc = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                            cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{uport}"
    try:
        _wait(base + "/")
        yield {"base": base, "log": tmp / "ui.log"}
    finally:
        proc.kill()
        log.close()


def _status(**kw) -> dict:
    d = {"state": "idle", "tag": "", "latest": "", "url": "", "mode": "code", "done": 0, "total": 0, "error": "",
         "check_error": "", "checked_at": 0, "check": None, "install_failures": 0, "fail": None, "next_try": 0,
         "manual": False, "settings": {"auto_download": True, "install_on_quit": True}, "kind": "pip",
         "current": "0.0.13", "last_result": None}
    d.update(kw)
    return d


def _check(newer: bool, at: int, **kw) -> dict:
    d = {"ok": True, "current": "0.0.13", "latest": "9.9.9" if newer else "0.0.13", "tag": "v9.9.9", "newer": newer,
         "kind": "pip", "url": "https://example.test/rel", "checked_at": at, "pending_restart": False, "prerelease": False}
    d.update(kw)
    return d


@pytest.fixture()
def fake(app):
    """A page whose update endpoints and /healthz come from `f` (change f[...] during a test; f['calls'] counts hits)."""
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
        f = {"status": _status(), "check": _check(False, 100), "install": {"ok": True, "action": "none", "message": "ok"},
             "apply": {"ok": True, "mode": "mac-swap"}, "restart": {"ok": True, "boot": "old"},
             "boots": ["old"], "calls": {}, "errors": errors, "page": pg}

        def answer(name, key):
            def h(route):
                f["calls"][name] = f["calls"].get(name, 0) + 1
                route.fulfill(status=200, content_type="application/json", body=json.dumps(f[key]))
            return h

        pg.route("**/api/update/status", answer("status", "status"))
        pg.route("**/api/update/check", answer("check", "check"))
        pg.route("**/api/update/install", answer("install", "install"))
        pg.route("**/api/update/apply", answer("apply", "apply"))
        pg.route("**/api/update/restart", answer("restart", "restart"))
        pg.route("**/api/update/download", lambda r: (f["calls"].__setitem__("download", f["calls"].get("download", 0) + 1),
                                                       r.fulfill(status=200, content_type="application/json", body='{"ok":true}'))[1])

        def healthz(route):
            n = f["calls"]["healthz"] = f["calls"].get("healthz", 0) + 1
            boots = f["boots"]
            boot = boots[min(n - 1, len(boots) - 1)]
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "app": "meshpool", "pid": 1, "boot": boot}))

        pg.route("**/healthz", healthz)
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.settings && typeof openSettings === 'function'", timeout=30000)
        yield f
        b.close()


def _open_about(f):
    pg = f["page"]
    pg.evaluate("openSettings('about')")
    pg.wait_for_selector("#updCheck", timeout=15000)
    pg.wait_for_function("() => document.getElementById('updMore') !== null")


def _poll(f):
    f["page"].evaluate("updStatus()")
    f["page"].wait_for_timeout(150)


def test_background_release_shows_without_reload(fake):
    f, pg = fake, fake["page"]
    _open_about(f)
    pg.click("#updCheck")
    pg.wait_for_function("() => /latest version/.test(document.getElementById('updRes').textContent)", timeout=10000)
    assert pg.is_hidden("#updBar")
    # the background check finds a release; the page asks the updater once more and nothing reloads
    pg.evaluate("window.__mark = 1")
    f["status"] = _status(state="available", latest="9.9.9", tag="v9.9.9", url="https://example.test/rel", check=_check(True, 200))
    _poll(f)
    assert pg.evaluate("window.__mark") == 1
    assert "is available" in pg.inner_text("#updBar") and pg.is_visible("#updBar")
    res = pg.inner_text("#updRes")
    assert "is available" in res and "latest version" not in res, res
    assert pg.is_visible("#updGo") and "Update now" in pg.inner_text("#updGo")
    assert f["errors"] == [], f["errors"]


def test_installing_hides_update_now(fake):
    f, pg = fake, fake["page"]
    f["status"] = _status(state="installing", latest="9.9.9", tag="v9.9.9", check=_check(True, 200))
    _open_about(f)
    _poll(f)
    assert pg.locator("#updGo").count() == 0
    assert "Installing update" in pg.inner_text("#updBar")
    assert "Installing Meshpool 9.9.9" in pg.inner_text("#updRes")
    assert "restarts when it is done" in pg.inner_text("#updRes")
    pg.evaluate("updatePanel(curUpd())")          # a re-render must not bring the button back either
    pg.evaluate("updatePanel(S.upd)")
    assert pg.locator("#updGo").count() == 0
    assert pg.locator("button:enabled", has_text=re.compile("Update now|Restart to update")).count() == 0


def test_update_now_stays_disabled_until_the_request_answers(fake):
    f, pg = fake, fake["page"]
    f["status"] = _status(state="available", latest="9.9.9", check=_check(True, 200))
    pending = []
    pg.route("**/api/update/install", lambda route: pending.append(route))
    _open_about(f)
    pg.wait_for_selector("#updGo:not([disabled])")
    pg.click("#updGo")
    pg.wait_for_function("() => document.getElementById('updGo').disabled && /Installing/.test(document.getElementById('updGo').textContent)")
    _poll(f)                                       # a poll while the request is out must not re-enable it
    assert pg.evaluate("document.getElementById('updGo').disabled") is True
    assert "Installing" in pg.inner_text("#updGo")
    for r in pending:
        r.fulfill(status=200, content_type="application/json", body='{"ok":false,"error":"nope"}')
    pg.wait_for_function("() => /nope/.test(document.getElementById('updOut').textContent)", timeout=10000)
    assert pg.evaluate("document.getElementById('updGo').disabled") is False


def test_restart_waits_for_new_boot(fake):
    f, pg = fake, fake["page"]
    f["boots"] = ["old", "old", "old", "old", "new"]   # call 1 is the read before the request
    pg.evaluate("window.__mark = 1")
    pg.evaluate("restartApp()")
    pg.wait_for_function("() => true")
    deadline = time.time() + 3.4
    while time.time() < deadline:
        pg.wait_for_timeout(200)
        if f["calls"].get("healthz", 0) >= 4:
            break
    assert f["calls"].get("healthz", 0) >= 3
    assert f["calls"].get("healthz", 0) < 5, "healthz answered with the new boot too early"
    assert pg.evaluate("window.__mark") == 1, "the page reloaded while the old server still answered"
    assert f["calls"].get("restart") == 1
    end = time.time() + 15
    reloaded = False
    while time.time() < end:
        try:
            if pg.evaluate("window.__mark") is None:
                reloaded = True
                break
        except Exception:  # noqa: BLE001
            reloaded = True      # the context went away with the navigation
            break
        pg.wait_for_timeout(250)
    assert reloaded, "the page never reloaded after the new boot"
    assert f["calls"]["healthz"] >= 5


def test_git_checkout_has_no_update_bar(fake):
    f, pg = fake, fake["page"]
    f["check"] = _check(True, 100, kind="dev")
    f["status"] = _status(state="available", kind="dev", latest="9.9.9", check=_check(True, 200, kind="dev"))
    _open_about(f)
    _poll(f)
    assert pg.is_hidden("#updBar")
    res = pg.inner_text("#updRes")
    assert "is available" in res and "git pull" in res, res
    assert pg.locator("#updCopy").count() == 1 and pg.locator("#updGo").count() == 0
    assert pg.locator("#updMore a", has_text="What's new").count() == 1
    pg.click("#updCopy")
    pg.wait_for_timeout(200)
    assert pg.is_hidden("#updBar")


def test_failed_state_shows_retry_and_next_try(fake):
    f, pg = fake, fake["page"]
    nxt = 1893456000      # a fixed time in the future (2030-01-01 UTC)
    f["status"] = _status(state="failed", latest="9.9.9", tag="v9.9.9", error="The download did not match its checksum.",
                          fail={"tag": "v9.9.9", "count": 2, "next_try": nxt, "error": "The download did not match its checksum."},
                          next_try=nxt, check=_check(True, 200))
    _open_about(f)
    _poll(f)
    assert "did not match its checksum" in pg.inner_text("#updRes")
    assert "tries again at" in pg.inner_text("#updMore")
    assert "2029" in pg.inner_text("#updMore") or "2030" in pg.inner_text("#updMore")
    assert pg.locator("#updGo", has_text="Try again").count() == 1
    pg.click("#updGo")
    pg.wait_for_timeout(300)
    assert f["calls"].get("download") == 1
