"""Bugs found by the 0.0.18 persona testers (Daniel, Lina, Sara), browser side (IDs WF-nn; the server side is in
test_wf_bugs018.py). Each test drives the real app (python -m meshpool_harness ui) in Chromium, with page.route
standing in for the folder dialog and the model. No model is called. Skipped when no Chromium can start here."""
from __future__ import annotations

import base64
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
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> None:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    pytest.fail(f"nothing answered at {url} within {secs} s")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wfui018")
    home, mh, work = tmp / "home", tmp / "mh", tmp / "work"
    for d in (home, mh, work, work / "alpha", work / "beta", home / "ClientWork", home / "Other"):
        d.mkdir(parents=True)
    (work / "deck.pptx").write_bytes(b"PK\x03\x04 not a real deck")
    (work / "chart.png").write_bytes(PNG)
    port = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"), MH_UI_OPEN_PAGE="1", PATH="/usr/bin:/bin")   # no zenity: the in-app picker
    env.pop("MESHPOOL_CONFIG", None)
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait(base + "/")
        yield {"base": base, "work": str(work), "home": str(home)}
    finally:
        p.kill()
        p.wait(10)
        log.close()


def _launch(pw):
    import glob
    try:
        return pw.chromium.launch()
    except Exception as first:  # noqa: BLE001
        roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers",
                 str(Path.home() / ".cache" / "ms-playwright")]
        cands = [os.environ.get("MH_TEST_CHROMIUM") or ""]
        for r in filter(None, roots):
            cands += sorted(glob.glob(os.path.join(r, "chromium_headless_shell-*", "*", "chrome-headless-shell")))
            cands += sorted(glob.glob(os.path.join(r, "chromium-*", "chrome-linux*", "chrome")))
        for exe in filter(None, cands):
            try:
                return pw.chromium.launch(executable_path=exe)
            except Exception:  # noqa: BLE001
                continue
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no Chromium for Playwright here: {first}")


def _boot(pg, app):
    pg.goto(app["base"] + "/")
    pg.wait_for_selector("#newThread", timeout=30000)
    pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        ctx = b.new_context(viewport={"width": 1280, "height": 860}, accept_downloads=True)
        pg = ctx.new_page()
        pg.set_default_timeout(15000)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); "
                           "localStorage.removeItem('mp_last_chat'); } catch (e) {}")
        _boot(pg, app)
        pg.errors = errors
        pg.app = app
        yield pg
        b.close()


def _fake_chat(page, cid: str = "t-wf018", view: dict | None = None):
    """An open chat with no server behind it: applyEv draws into it as a live chat would."""
    page.evaluate("""([cid, view]) => { openThread(cid, true); if (S.cur.pollT) { clearTimeout(S.cur.pollT); clearInterval(S.cur.pollT); }
        S.cur.pollT = null; stopStream(S.cur); S.cur.stage = null; $('#col').replaceChildren(); $('#col').classList.remove('loading');
        if (view) S.cur.view = Object.assign(S.cur.view || {}, view); }""", [cid, view])


def _no_native_picker(page):
    """No native folder dialog here: the server answers "pick" with no_picker, as on Linux without zenity."""
    def handle(route, request):
        try:
            body = json.loads(request.post_data or "{}")
        except ValueError:
            body = {}
        if body.get("action") == "pick":
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"projects": [], "picked": "", "no_picker": True}))
        return route.continue_()
    page.route(re.compile(r"^https?://[^/]+/api/projects$"), handle)


# ---------------------------------------------------------------------------------------------------- WF-01
def test_the_folder_picker_uses_the_folder_you_click_and_never_defaults_to_home(page):
    """WF-01: after "Folders I choose", the picker's default button was "Use the whole home folder", and a click on a
    folder only highlighted it: the button still used the folder shown. The button now names the folder you click,
    and in onboarding it waits for one instead of offering the whole home folder."""
    page.evaluate("() => { window._fb = 'pending'; browseFolder('', {noHome: true}).then(v => { window._fb = v; }); }")
    page.wait_for_selector("#fbList [data-p]")
    btn = ".dlg.fb [data-a='1']"
    assert page.is_disabled(btn) and "home" not in page.inner_text(btn).lower(), page.inner_text(btn)
    page.click("#fbList button:has-text('ClientWork')")
    assert page.inner_text(btn) == "Use ClientWork" and not page.is_disabled(btn)
    page.click(btn)
    page.wait_for_function("() => window._fb !== 'pending'")
    assert os.path.realpath(page.evaluate("window._fb")) == os.path.realpath(os.path.join(page.app["home"], "ClientWork"))
    assert page.errors == [], page.errors


def test_a_folder_picked_in_onboarding_is_a_project_in_the_sidebar_and_new_chats_start_in_it(page):
    """WF-01 / WF-A: the folder picked in onboarding was in no sidebar or project picker, and a new chat (or a reload)
    worked in a hidden app-data folder. It is now a pinned project; new chats start in it, after a reload too, and
    the chat's folder is the picked folder. The sidebar has an "Add a folder" button."""
    _no_native_picker(page)
    page.route(re.compile(r"^https?://[^/]+/api/agent/[^/]+/send$"),
               lambda route, request: route.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"state": "idle", "events": [], "sent": {}})))
    page.evaluate("() => { window._pp = 'pending'; pickProject({project: true, noHome: true}).then(r => { window._pp = r; }); }")
    page.wait_for_selector("#fbList [data-p]")
    page.click("#fbList button:has-text('ClientWork')")
    page.click(".dlg.fb [data-a='1']")
    page.wait_for_function("() => window._pp !== 'pending'")
    pid = page.evaluate("window._pp && window._pp.pid")
    assert pid
    page.evaluate("() => newThread()")
    assert page.evaluate("S.draft.pid") == pid
    assert page.inner_text("#projSel").strip() == "ClientWork"
    assert page.query_selector(f"#sideList .th[data-pid='{pid}']"), "the folder is not in the sidebar"
    assert page.query_selector("#sideList #sideAddFolder")
    page.reload()
    page.wait_for_function("() => typeof S === 'object' && S.pj && S.pj.length && S.draft.pid", timeout=30000)
    assert page.evaluate("S.draft.pid") == pid                     # a reload keeps it
    page.fill("#input", "Summarise the client brief in this folder")
    page.press("#input", "Enter")
    page.wait_for_function("() => S.cur && S.cur.id")
    wd = page.evaluate("async () => (await api(`/api/agent/${S.cur.id}`)).workdir")
    assert os.path.realpath(wd) == os.path.realpath(os.path.join(page.app["home"], "ClientWork"))
    page.evaluate("() => newThread()")
    assert page.evaluate("S.draft.pid") == pid                     # the next new chat starts there too
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- WF-06 / WF-H / WF-08
def test_file_cards_keep_their_names_inside_and_their_thumbnails_after_a_rebuild(page):
    """WF-06: a long name ran over the card's edge with no ellipsis, and after Undo every card lost its thumbnail
    (the chat is rebuilt off-screen, and the thumbnail gave up while the card was not on the page yet). WF-H: each
    card has a Download button."""
    def preview(route, request):
        if "page=" in request.url:
            return route.fulfill(status=200, content_type="image/png", body=PNG)
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"state": "ready", "pages": 1, "mtime": 1, "kind": "pages"}))
    page.route(re.compile(r"^https?://[^/]+/api/preview\?"), preview)
    _fake_chat(page, view={"workdir": page.app["work"]})
    got = page.evaluate("""async () => {
        const cur = S.cur, off = document.createElement("div"), foot = document.createElement("div"); off.appendChild(foot);
        cur.tpaths = ["ViDA e-invoicing small consulting firms and what they need to do.pptx", "notes.md"];
        pvTurnCards(cur, foot, 0);                          // drawn off-screen, as a rebuilt chat is
        await new Promise(r => setTimeout(r, 400));
        $('#col').appendChild(off);                         // and swapped in a moment later
        for (let i = 0; i < 40 && !off.querySelector('.pvth img'); i++) await new Promise(r => setTimeout(r, 100));
        const card = off.querySelector('.pvcard'), b = card.querySelector('.pvcm b'), cr = card.getBoundingClientRect(), br = b.getBoundingClientRect();
        return {thumb: !!card.querySelector('.pvth img'), clipped: b.scrollWidth > b.clientWidth,
                inside: br.right <= cr.right - 8, ellipsis: getComputedStyle(b).textOverflow,
                thumbLeft: card.querySelector('.pvth').getBoundingClientRect().left >= cr.left - 1,
                dl: off.querySelectorAll('[data-pvdl]').length};
    }""")
    assert got["thumb"], got
    assert got["clipped"] and got["inside"] and got["ellipsis"] == "ellipsis" and got["thumbLeft"], got
    assert got["dl"] == 2, got
    assert page.errors == [], page.errors


def test_a_file_in_a_chats_own_folder_says_it_is_a_copy_there(page):
    """WF-H: Sara's dropped Budget_2026.xlsx was edited as a copy in the chat's hidden folder, and the panel named the
    folder "dab817694f87". The card says the file is a copy in this chat; the folder is named after the chat."""
    _fake_chat(page, "t-wfh", view={"workdir": page.app["work"], "scratch": True, "title": "Fix my budget"})
    got = page.evaluate("""() => {
        const cur = S.cur; cur.log = [{type: "user", i: 0, text: "Fix it\\n\\nAttached files (in this chat's folder): Budget_2026.xlsx"}];
        const foot = document.createElement("div"); $('#col').appendChild(foot);
        cur.tpaths = ["Budget_2026.xlsx", "summary.md"]; pvTurnCards(cur, foot, 0);
        return {where: [...document.querySelectorAll('.pvcard .pvwhere')].map(x => x.textContent), label: rootLabel()};
    }""")
    assert got["where"] == ["Copy of your file, in this chat", "In this chat"], got
    assert got["label"] == "Fix my budget (this chat's own folder)", got
    assert page.errors == [], page.errors


def test_the_preview_and_its_menu_download_the_file_through_the_app(page):
    """WF-08: the preview had no Download; its menu offered only Copy path and Show in file manager. Download now
    saves the file with the browser's own download, through /raw (dl=1). Nothing else is opened."""
    work = page.app["work"]
    page.evaluate("w => post('/api/projects', {action: 'add', path: w})", work)   # a folder the app may open
    page.evaluate("w => openPreview(w, 'deck.pptx')", work)
    page.wait_for_selector("#pvDl:not([hidden])")
    with page.expect_download() as d:
        page.click("#pvDl")
    assert d.value.suggested_filename == "deck.pptx"
    assert Path(d.value.path()).read_bytes() == Path(work, "deck.pptx").read_bytes()
    page.click("#pvMore")
    page.wait_for_selector("#ctx")
    assert "Download" in page.inner_text("#ctx")
    page.keyboard.press("Escape")
    r = page.evaluate("""async w => { const x = await fetch(`/raw?t=${encodeURIComponent(MP.token)}&root=${encodeURIComponent(w)}&path=deck.pptx&dl=1`);
        return {status: x.status, disp: x.headers.get('content-disposition'), type: x.headers.get('content-type')}; }""", work)
    assert r["status"] == 200 and r["disp"].startswith("attachment") and r["type"] == "application/octet-stream", r
    bad = page.evaluate("""async w => (await fetch(`/raw?t=wrong&root=${encodeURIComponent(w)}&path=deck.pptx&dl=1`)).status""", work)
    assert bad == 403
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- WF-07 / WF-D / WF-E
def test_undo_shows_the_backup_path_only_in_details_and_puts_back_no_skill_chips(page):
    """WF-07: the Undo line printed the full checkpoints path, and a "Use $invoice-quote" chip appeared: the message
    Undo puts back in the box was sent for skill suggestions as if typed. WF-E: the same for Lina's chips. WF-J: the
    message came back focused, so one Enter sent the undone request again (Maya)."""
    asked = []

    def suggest(route, request):
        asked.append(json.loads(request.post_data or "{}").get("text"))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"suggestions": [{"type": "skill", "id": "invoice-quote", "name": "invoice-quote",
                                                        "label": "Use $invoice-quote"}]}))
    page.route(re.compile(r"^https?://[^/]+/api/suggest$"), suggest)
    _fake_chat(page)
    page.evaluate("""() => applyEv({type: "status", i: 0, text: "Undone. Restored 1 file (deck.pptx).",
        detail: "Copies of the files Undo replaced are in /tmp/x/checkpoints/undone/20261126-104217"})""")
    note = page.inner_text("#col .note")
    assert "/tmp" not in note.split("Details")[0] and page.query_selector("#col .note details"), note
    page.focus("#input")
    page.evaluate("() => fillInput('Change the title on slide 1 to: ViDA e-invoicing for your firm', {back: true})")
    page.wait_for_timeout(900)
    assert page.is_hidden("#sugg") and not asked, asked
    # WF-J: the message put back is a draft: the box is not focused (one Enter must not redo the undone request)
    assert page.evaluate("document.activeElement && document.activeElement.id") != "input"
    assert "back in the box as a draft" in page.inner_text("body")
    page.keyboard.press("Enter")
    assert page.input_value("#input").startswith("Change the title on slide 1") and page.evaluate("S.cur.id") == "t-wf018"
    page.focus("#input")
    page.type("#input", " now")                                      # typed by the person: suggestions as before
    page.wait_for_function("() => !$('#sugg').hidden", timeout=5000)
    assert page.errors == [], page.errors


def test_undo_closes_the_preview_of_a_file_it_removed(page):
    """WF-D: after Undo, the preview still showed the deleted monthly_revenue.png."""
    work = page.app["work"]
    page.evaluate("w => openPreview(w, 'chart.png')", work)
    page.wait_for_function("() => PVX.open && PVX.path === 'chart.png'")
    page.evaluate("w => afterUndo(w, {deleted: ['other.png'], restored: []})", work)
    assert page.evaluate("PVX.open")                                    # another file: the preview stays
    page.evaluate("w => afterUndo(w, {deleted: ['chart.png'], restored: []})", work)
    page.wait_for_function("() => !PVX.open")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- WF-04 / WF-09
def test_build_files_are_not_counted_and_a_long_job_shows_its_stage(page):
    """WF-04: the footer said "12 files changed" for one deck (its qa.json, slides.json... counted). WF-09: a 5-minute
    deck showed only "2 steps"; the engine's stage now shows in the working line."""
    _fake_chat(page, view={"workdir": page.app["work"], "state": "working"})
    page.evaluate("""() => { const cur = S.cur;
        for (const e of [{type: "user", text: "Make the deck", i: 0}, {type: "tool_start", id: "a", name: "mcp__meshpool_office__make_deck", mcp: true, i: 1},
                         {type: "job_stage", text: "Deck: writing 6 slides", i: 2}]) applyEv(e);
        cur.working = true; workTick(cur); }""")
    assert page.inner_text("#working .wt") == "Deck: writing 6 slides"
    page.evaluate("""() => { const cur = S.cur; cur.view.turns = [{n: 1, changed: ["deck.pptx", "Meshpool outputs/j/qa.json",
            "Meshpool outputs/j/slides.json", "Meshpool outputs/j/deck.provenance.json"].map(p => ({path: p, status: "added"}))}];
        for (const e of [{type: "tool_end", id: "a", ok: true, output: "done", i: 3}, {type: "assistant", text: "Done.", i: 4},
                         {type: "turn_end", i: 5}, {type: "checkpoint", turns: [cur.view.turns[0]], i: 6}]) applyEv(e); }""")
    assert "1 file changed" in page.inner_text("#col .turnfoot")
    assert page.evaluate("S.cur.jobStage") == ""
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- WF-G
def test_browse_files_opens_the_folder_in_the_app(page):
    """WF-G: "Browse files" in the Artifacts panel only scrolled to a list already on screen: nothing happened. It
    opens the folder's file list in the app (nothing outside the app is started)."""
    work = page.app["work"]
    page.evaluate("""async w => { const v = await post('/api/agent', {workdir: w, kind: 'cowork', task: 'look'});
        await loadThreads(); openThread(v.id); }""", work)
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir")
    page.evaluate("() => togglePanel('outputs', true)")
    page.wait_for_selector("#oOpen")
    page.click("#oOpen")
    page.wait_for_function("() => PVX.open && PVX.m && PVX.m.kind === 'folder'")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- WF-I
def test_a_projects_sidebar_row_lists_its_code_sessions(page):
    """WF-I: Lina's question in a project became a Code session and was gone from the project in the sidebar."""
    got = page.evaluate("""() => {
        S.pj = [{id: "p_aaaaaaaaaa", name: "LinaData", pinned: true, updated: 2}]; S.pjOpen = new Set(["p_aaaaaaaaaa"]);
        S.threads = [{id: "c1", title: "Add a revenue column", kind: "code", scratch: false, project_id: "p_aaaaaaaaaa", updated: 3},
                     {id: "w1", title: "Plain chat", kind: "cowork", project_id: "p_aaaaaaaaaa", updated: 1}];
        S.mode = "chat"; const el = $("#sideList"); el._html = el._shape = ""; renderSide();
        return [...document.querySelectorAll('#sideList ul.sub .th[data-id]')].map(x => x.dataset.id);
    }""")
    assert got == ["c1", "w1"], got
    assert page.errors == [], page.errors
