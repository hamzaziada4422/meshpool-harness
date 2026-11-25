"""Release review (UI), in a real browser against the real app server with the mock model (testkit/mock_llm.py writes
the file a message names): Edit and Try again go back to the message they are on, also after an earlier Edit (checkpoint
numbers are never reused, so a count of messages names the wrong turn); Edit, Rewind and Undo keep a draft in the box;
a lone surrogate in maths does not stop the live reply; the home cards draw before the settings arrive."""
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
    tmp = tmp_path_factory.mktemp("editturns")
    home, mh, proj = tmp / "home", tmp / "mh", tmp / "proj"
    for d in (home, mh, proj):
        d.mkdir()
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


def _send(pg, app, text, n_users):
    pg.fill("#input", text)
    pg.press("#input", "Enter")
    _idle(pg, app, n_users)


def _idle(pg, app, n_users):
    try:
        pg.wait_for_function("n => S.cur && S.cur.view && S.cur.view.state === 'idle' && "
                             "document.querySelectorAll('#col .msg-user').length === n && document.querySelector('#working') === null",
                             arg=n_users, timeout=90000)
    except Exception:  # noqa: BLE001
        pytest.fail("the chat did not finish\n" + app["log"].read_text(errors="replace")[-2000:])


def _users(pg):
    return pg.eval_on_selector_all("#col .msg-user .b", "els => els.map(e => e.innerText.trim())")


def _files(app):
    return sorted(p.name for p in Path(app["proj"]).iterdir())


def _msg_action(pg, i, act, confirm):
    m = pg.locator("#col .msg-user").nth(i)
    m.hover()
    m.locator(f"[data-ma='{act}']").click()
    if confirm:
        pg.click(".dlg [data-a='1']")


def test_edit_and_try_again_go_back_to_their_own_message(page, app):
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    page.fill("#input", "make the file a.txt")
    page.press("#input", "Enter")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    _idle(page, app, 1)
    _send(page, app, "make the file b.txt", 2)
    _send(page, app, "make the file c.txt", 3)
    assert _files(app) == ["a.txt", "b.txt", "c.txt"]

    # Edit the 3rd message (the last one: no question asked): its turn goes, the text is in the box
    _msg_action(page, 2, "edit", confirm=False)
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 2", timeout=30000)
    assert page.input_value("#input") == "make the file c.txt"
    assert _files(app) == ["a.txt", "b.txt"]

    # two more messages, then Edit the 4th: only its turn may go (checkpoint 5 here, not "the 4th")
    page.fill("#input", "")
    _send(page, app, "make the file c2.txt", 3)
    _send(page, app, "make the file d.txt", 4)
    assert _files(app) == ["a.txt", "b.txt", "c2.txt", "d.txt"]
    _msg_action(page, 3, "edit", confirm=False)
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 3", timeout=30000)
    time.sleep(0.5)
    assert _users(page) == ["make the file a.txt", "make the file b.txt", "make the file c2.txt"]
    assert _files(app) == ["a.txt", "b.txt", "c2.txt"]
    assert page.input_value("#input") == "make the file d.txt"

    # Try again under the last reply redoes that reply only
    page.fill("#input", "")
    ep = page.evaluate("S.cur.view.epoch")
    page.locator("#col .racts010 [data-ra='retry']").last.click(force=True)
    page.wait_for_function("e => S.cur.view.epoch !== e", arg=ep, timeout=30000)        # the rewind happened
    page.wait_for_function("() => (S.cur.view.turns || []).some(t => t.title === 'make the file c2.txt' && t.done && t.changed && t.changed.length)",
                           timeout=60000)                                                   # and the message ran again
    _idle(page, app, 3)
    assert _users(page) == ["make the file a.txt", "make the file b.txt", "make the file c2.txt"]
    assert _files(app) == ["a.txt", "b.txt", "c2.txt"]
    assert page.errors == [], page.errors


def test_edit_rewind_and_undo_keep_a_draft(page, app):
    page.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    page.fill("#input", "make the file e1.txt")
    page.press("#input", "Enter")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    _idle(page, app, 1)
    _send(page, app, "make the file e2.txt", 2)
    _send(page, app, "make the file e3.txt", 3)
    _send(page, app, "make the file e4.txt", 4)

    # Undo under the last reply, with a draft in the box: the message goes after the draft
    page.fill("#input", "draft one")
    page.locator("#col .turnfoot [data-undo]").last.click()
    page.click(".dlg [data-a='1']")
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 3", timeout=30000)
    page.wait_for_function("() => document.querySelector('#input').value !== 'draft one'", timeout=10000)
    assert page.input_value("#input") == "draft one\n\nmake the file e4.txt"

    # Rewind to the 3rd message
    page.fill("#input", "draft two")
    _msg_action(page, 2, "rewind", confirm=True)
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 2", timeout=30000)
    page.wait_for_function("() => document.querySelector('#input').value !== 'draft two'", timeout=10000)
    assert page.input_value("#input") == "draft two\n\nmake the file e3.txt"

    # Edit the 1st message (asks first, as later replies go)
    page.fill("#input", "draft three")
    _msg_action(page, 0, "edit", confirm=True)
    page.wait_for_function("() => document.querySelectorAll('#col .msg-user').length === 0", timeout=30000)
    page.wait_for_function("() => document.querySelector('#input').value !== 'draft three'", timeout=10000)
    assert page.input_value("#input") == "draft three\n\nmake the file e1.txt"
    assert not (Path(app["proj"]) / "e1.txt").exists()
    assert page.errors == [], page.errors


def test_a_lone_surrogate_in_maths_does_not_stop_the_live_reply(page):
    r = page.evaluate("""() => {
      const out = {};
      try { out.md = md('$$a\\uD800b$$ and \\\\(x\\uDC00\\\\)').includes('mathsrc'); } catch (e) { out.md = 'ERR ' + e; }
      // the live reply: flushLive draws it; a reply that cannot be drawn shows as text and the loop goes on
      const c = {live: document.createElement('div'), liveDirty: true, liveText: 'sum $$\\uD800$$'};
      try { flushLive(c); out.live = !c.liveDirty && c.live.innerHTML.length > 0; } catch (e) { out.live = 'ERR ' + e; }
      const keep = mdRender;
      mdRender = () => { throw new Error('boom'); };
      const d = {live: document.createElement('div'), liveDirty: true, liveText: 'plain words'};
      try { flushLive(d); out.guard = !d.liveDirty && d.live.textContent === 'plain words'; } catch (e) { out.guard = 'ERR ' + e; }
      finally { mdRender = keep; }
      // the same base64 as before for good text (unb64 reads it back)
      out.round = unb64(b64utf8(' x² + 😀 ')) === 'x² + 😀';
      return out;
    }""")
    assert r == {"md": True, "live": True, "guard": True, "round": True}, r
    assert page.errors == [], page.errors


def test_home_cards_draw_before_the_settings_arrive(page):
    r = page.evaluate("""() => {
      const s = S.settings, pj = S.pj, cur = S.cur;
      S.settings = null; S.pj = undefined; S.cur = null;
      try { CARDS(); renderHero(); return 'ok'; } catch (e) { return 'ERR ' + e; }
      finally { S.settings = s; S.pj = pj; S.cur = cur; }
    }""")
    assert r == "ok", r
