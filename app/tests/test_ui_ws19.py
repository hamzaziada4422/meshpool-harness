"""ws19 front-end bugs, driven in a real browser against the real app server with the mock model (testkit/mock_llm.py):
B1 B3 B5 B7/B8 B9 B10 F-20 F-36 OSS-12, plus proofs for B2 and H-24."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]
UI = REPO / "app" / "meshpool_harness" / "ui"


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
    tmp = tmp_path_factory.mktemp("ws19")
    home, mh, proj = tmp / "home", tmp / "mh", tmp / "proj"
    for d in (home, mh, proj):
        d.mkdir()
    (proj / "notes.txt").write_text("hello\n")
    (proj / "song.mp3").write_bytes(b"ID3" + b"\0" * 64)
    (proj / "clip.mp4").write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 64)
    (proj / "empty.jpg").write_bytes(b"")
    mport, uport = _port(), _port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0")
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
        req = urllib.request.Request(base + "/api/projects", data=json.dumps({"action": "add", "path": str(proj)}).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        yield {"base": base, "proj": str(proj), "log": tmp / "ui.log", "tok": tok}
    finally:
        for p in procs:
            p.kill()
        log.close()


@pytest.fixture()
def make_page(app):
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright here: {e}")

        def make(width=1280, height=860, **ctx):
            c = b.new_context(viewport={"width": width, "height": height}, **ctx)
            pg = c.new_page()
            pg.errors = []
            pg.on("pageerror", lambda e: pg.errors.append(str(e)))
            pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
            pg.goto(app["base"] + "/")
            pg.wait_for_selector("#newThread", state="attached", timeout=30000)
            pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
            return pg
        yield make
        b.close()


def _idle(pg, app, n_users):
    try:
        pg.wait_for_function("n => S.cur && S.cur.view && S.cur.view.state === 'idle' && "
                             "document.querySelectorAll('#col .msg-user').length === n && document.querySelector('#working') === null",
                             arg=n_users, timeout=90000)
    except Exception:  # noqa: BLE001
        pytest.fail("the chat did not finish\n" + app["log"].read_text(errors="replace")[-2000:])


def test_b1_f36_oss12_undo_survives_a_missing_preview_helper_and_export_keeps_its_name(make_page, app):
    pg = make_page()
    # preview.js is loaded before app.js (B1) ...
    order = pg.evaluate("[...document.scripts].map(s => s.src.split('/').pop().split('?')[0]).filter(Boolean)")
    assert order.index("preview.js") < order.index("app.js")
    # ... and even when its helper throws, the turn's Undo and "files changed" buttons are still drawn
    pg.evaluate("() => { window.pvTurnCards = () => { throw new Error('boom'); }; }")
    pg.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    pg.fill("#input", "make the file one.txt")
    pg.press("#input", "Enter")
    pg.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir", timeout=60000)
    _idle(pg, app, 1)
    pg.wait_for_function("() => document.querySelectorAll('#col .turnfoot [data-undo]').length === 1", timeout=30000)
    assert pg.locator("#col .turnfoot [data-diff]").count() == 1
    # F-36: the Export button keeps its accessible name, also at a width where its text is hidden
    pg.set_viewport_size({"width": 700, "height": 800})
    assert pg.get_attribute("#shareBtn", "aria-label") == "Export chat"
    # OSS-12: one item that cannot be drawn leaves a note and the page keeps working
    pg.evaluate("applyEv({type: 'assistant', get text() { throw new Error('bad renderer'); }})")
    assert "Could not show this item" in pg.inner_text("#col")
    assert not [e for e in pg.errors if "bad renderer" in e]


def test_b5_message_actions_are_visible_on_touch_screens(make_page):
    pg = make_page(width=390, height=800, has_touch=True, is_mobile=True)
    assert pg.evaluate("matchMedia('(hover: none)').matches")
    op = pg.evaluate("""() => { const col = document.querySelector('#col') || document.body;
      const d = document.createElement('div'); d.className = 'msg-user'; d.innerHTML = '<div class="macts"><button>x</button></div><div class="bubble">hi</div>';
      col.appendChild(d); return getComputedStyle(d.querySelector('.macts')).opacity; }""")
    assert float(op) == 1.0


@pytest.mark.parametrize("width", [390, 1000])
def test_b7_b8_the_panel_close_button_stays_on_screen(make_page, width):
    pg = make_page(width=width, height=800)
    pg.evaluate("togglePanel(panelSet()[0], true)")
    pg.wait_for_selector("#pclose", timeout=10000)
    box = pg.locator("#pclose").bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
    # the strip scrolls on its own instead of pushing Close away
    assert pg.evaluate("getComputedStyle(document.querySelector('#ptabs .pstrip')).overflowX") in ("auto", "scroll")


def test_b9_the_page_behind_a_modal_is_inert_and_focus_comes_back(make_page):
    pg = make_page()
    pg.focus("#newThread")
    pg.evaluate("openSettings()")
    pg.wait_for_selector(".modal", timeout=10000)
    assert pg.evaluate("document.getElementById('app').inert") is True
    assert pg.evaluate("!!document.activeElement.closest('.scrim')")
    pg.keyboard.press("Tab")
    assert pg.evaluate("!!document.activeElement.closest('.scrim')")        # focus never leaves the dialog
    pg.evaluate("closeSettings()")
    pg.wait_for_function("() => !document.querySelector('.scrim')", timeout=5000)
    assert pg.evaluate("document.getElementById('app').inert") is False


def test_b10_add_another_app_with_no_address_says_what_is_missing(make_page):
    pg = make_page()
    pg.evaluate("openSettings('connectors')")
    pg.wait_for_selector("#cxAdd", timeout=15000)
    pg.click("#cxAdd")
    assert "address" in pg.inner_text("#cnRes").lower()


def test_f20_duplicate_model_names_show_where_they_come_from(make_page):
    pg = make_page()
    out = pg.evaluate("""() => { const l = [{id: 'chatgpt:gpt-4o'}, {id: 'gpt-4o', provider: 'Acme gateway'}, {id: 'qwen-max'}];
      return [modelLabel('chatgpt:gpt-4o', l), modelLabel('gpt-4o', l), modelLabel('qwen-max', l)]; }""")
    assert out[0] != out[1] and "chatgpt" in out[0] and "Acme gateway" in out[1] and out[2] == "Qwen Max"


def test_b3_audio_and_video_play_from_raw_with_a_show_in_folder_button(make_page, app):
    pg = make_page()
    root = app["proj"]
    pg.evaluate("r => openPreview(r, 'song.mp3')", root)
    pg.wait_for_selector("#pvStage audio[controls]", timeout=15000)
    assert "/raw?" in pg.get_attribute("#pvStage audio", "src")
    assert pg.locator("#pvStage button", has_text="Show in folder").count() == 1
    pg.evaluate("r => openPreview(r, 'clip.mp4')", root)
    pg.wait_for_selector("#pvStage video[controls]", timeout=15000)
    req = urllib.request.Request(f"{app['base']}/raw?t={app['tok']}&root={urllib.parse.quote(root)}&path=clip.mp4")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200 and r.headers["Content-Type"].startswith("video/")


def test_b4_an_empty_jpg_never_gets_a_save_button(make_page, app):
    pg = make_page()
    pg.evaluate("r => openPreview(r, 'empty.jpg')", app["proj"])
    pg.wait_for_selector("#pvStage", timeout=10000)
    time.sleep(1)
    assert pg.locator("#edSave").count() == 0 and pg.locator("#edTa").count() == 0


def test_b2_a_broken_image_shows_a_note_not_a_blank_pane():
    js = (UI / "preview.js").read_text(encoding="utf-8")
    assert re.search(r'img\.onerror = \(\) => \{ if \(id === PVX\.id\) pvNote\("This image could not be drawn\."\)', js)


def test_h24_enter_in_an_input_method_composition_does_not_send():
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    assert "e.key === \"Enter\" && !e.shiftKey && !e.isComposing" in app_js
    assert "e.isComposing" in (UI / "preview.js").read_text(encoding="utf-8")
