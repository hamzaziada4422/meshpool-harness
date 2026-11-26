"""Problems "Priya" (a non-technical office manager) found in the 0.0.18 release build: the office check wording, jargon in
the chat and the UI, the Open house.html card, the sidebar's empty state, the folded sidebar and the preview panel,
suggestion chips that had nothing to do with the reply, the "2 files changed" count and an approval card for an empty
call. Server parts are plain pytest; the page parts drive the real app (python -m meshpool_harness ui) in Chromium,
the way tests/test_ui_bugs018.py does. No model is called. The page tests are skipped when no Chromium can start here."""
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))


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
    tmp = tmp_path_factory.mktemp("priya018")
    home, mh, work = tmp / "home", tmp / "mh", tmp / "work"
    for d in (home, mh, work):
        d.mkdir()
    (work / "house.html").write_text("<!doctype html><title>Open house</title><h1>Open house</h1><p>Saturday 6 December</p>")
    (work / "rooms.xlsx").write_bytes(b"PK\x03\x04 not a real workbook")
    port = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"), MH_UI_OPEN_PAGE="1")
    env.pop("MESHPOOL_CONFIG", None)
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait(base + "/")
        yield {"base": base, "work": str(work), "home": str(home), "mh": str(mh)}
    finally:
        p.kill()
        p.wait(10)
        log.close()


def _launch(pw):
    """Playwright's own Chromium; else MH_TEST_CHROMIUM; else any Chromium in the browsers folder."""
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


def _page(app, width=1280, height=860):
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    b = _launch(pw)
    pg = b.new_page(viewport={"width": width, "height": height})
    pg.set_default_timeout(15000)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); "
                       "localStorage.removeItem('mp_last_chat'); } catch (e) {}")
    pg.goto(app["base"] + "/")
    pg.wait_for_selector("#newThread", timeout=30000)
    pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)
    pg.errors = errors
    pg.app = app
    pg._pw, pg._b = pw, b
    return pg


def _close(pg):
    try:
        pg._b.close()
    finally:
        pg._pw.stop()


@pytest.fixture()
def page(app):
    pg = _page(app)
    yield pg
    _close(pg)


def _fake_chat(page, cid: str = "t-priya018", view: dict | None = None):
    """An open chat with no server behind it: applyEv draws into it as a live chat would."""
    page.evaluate("""([cid, view]) => { openThread(cid, true); if (S.cur.pollT) { clearTimeout(S.cur.pollT); clearInterval(S.cur.pollT); }
        S.cur.pollT = null; stopStream(S.cur); S.cur.stage = null; $('#col').replaceChildren(); $('#col').classList.remove('loading');
        if (view) S.cur.view = Object.assign(S.cur.view || {}, view); }""", [cid, view])


LONG = " ".join(["The room areas add up to 255.27 square metres across eight rooms, and the open studio is the largest."] * 6)


def _reading(page, turns=6):
    """A chat with enough text to scroll, drawn as a live one is, with the project folder known to the app."""
    page.evaluate("w => post('/api/projects', {action: 'add', path: w})", page.app["work"])
    _fake_chat(page, view={"workdir": page.app["work"]})
    page.evaluate("""([n, text]) => { const evs = []; for (let i = 0; i < n; i++) { evs.push({type: 'user', text: 'Question ' + i, i: i * 2}); evs.push({type: 'assistant', text, i: i * 2 + 1}); evs.push({type: 'turn_end', i: i * 2 + 1.5}); }
        for (const e of evs) applyEv(e); renderTop(); }""", [turns, LONG])
    page.wait_for_timeout(300)


def _shift_probe(page):
    page.evaluate("""() => { window.__cls = 0; window.__clsFree = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) { window.__cls += e.value; if (!e.hadRecentInput) window.__clsFree += e.value; } }).observe({type: 'layout-shift', buffered: false}); }""")


def _col_box(page):
    return page.evaluate("""() => { const r = document.querySelector('#col').getBoundingClientRect(); const c = document.querySelector('#composer').getBoundingClientRect();
        return {left: Math.round(r.left), width: Math.round(r.width), composerLeft: Math.round(c.left), composerWidth: Math.round(c.width)}; }""")



def _panel_run(app, width):
    """Open the preview from a file card at this window width, then close it. The chat column's box and the layout
    shifts (all, and the ones not tied to the click) at each step."""
    page = _page(app, width)
    try:
        _reading(page)
        page.evaluate("""() => { const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = ['house.html']; pvTurnCards(S.cur, foot, 0); }""")
        page.wait_for_selector(".pvcard")
        page.wait_for_timeout(400)
        out = {"width": width, "before": _col_box(page)}
        _shift_probe(page)
        page.click(".pvcard")
        page.wait_for_selector("#pvp:not([hidden])")
        page.wait_for_timeout(900)
        out["open"] = _col_box(page)
        out["shift_open"] = page.evaluate("[window.__cls, window.__clsFree]")
        page.evaluate("window.__cls = 0; window.__clsFree = 0")
        page.evaluate("() => closePreview()")
        page.wait_for_timeout(900)
        out["closed"] = _col_box(page)
        out["shift_close"] = page.evaluate("[window.__cls, window.__clsFree]")
        return out
    finally:
        _close(page)


@pytest.mark.parametrize("width", [1366, 1280, 1024, 1920])
def test_the_chat_column_only_narrows_when_there_is_no_room(app, width):
    """At 1366 the sidebar stayed and the chat column went from 656 to 550 px when the preview opened, so the text
    visibly re-wrapped (layout shift 0.154 open, 0.209 close). The sidebar now folds whenever the chat would get
    under about 640 px with it kept, whatever the window width. Where 640 is out of reach (1024) the column narrows."""
    r = _panel_run(app, width)
    print("MEASURE", width, "col before/open/closed", r["before"]["width"], r["open"]["width"], r["closed"]["width"],
          "left", r["before"]["left"], r["open"]["left"], "shift open (all, not tied to the click)", [round(x, 3) for x in r["shift_open"]],
          "close", [round(x, 3) for x in r["shift_close"]])
    assert r["closed"] == r["before"]                       # closing puts everything back
    if width in (1366, 1280, 1920):
        assert r["open"]["width"] == r["before"]["width"], r      # the text keeps its line length: no re-wrap
    else:
        assert r["open"]["width"] >= 540, r                     # no room for more: a narrower column, not a squeezed one


def test_a_new_chat_button_stays_in_the_top_bar_while_the_sidebar_is_folded(app):
    """Opening the preview folds the sidebar, and with it went "New": her next new-chat attempt missed and the next
    request went into the wrong chat. The top bar now has a New chat button while the sidebar is folded."""
    page = _page(app, 1280)
    try:
        _reading(page)
        assert page.evaluate("() => !document.querySelector('#topNew')")      # sidebar open: the sidebar has New
        page.evaluate("""() => { const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = ['house.html']; pvTurnCards(S.cur, foot, 0); }""")
        page.click(".pvcard")
        page.wait_for_selector("#pvp:not([hidden])")
        assert page.evaluate("() => document.querySelector('#app').classList.contains('autoside')")
        page.wait_for_selector("#topNew", state="visible")
        before = page.evaluate("() => S.cur && S.cur.id")
        page.click("#topNew")
        page.wait_for_function("() => !S.cur || S.view !== 'chat'")
        assert page.evaluate("() => !!document.querySelector('#col .hero, #col .home')") or page.evaluate("() => !S.cur")
        assert before and page.evaluate("() => !S.cur || S.cur.id !== '%s'" % before)
        assert page.errors == [], page.errors
    finally:
        _close(page)


def test_the_reader_keeps_their_place_when_a_panel_opens(app):
    """Opening the preview scrolled the chat and left a "Jump to latest" pill over the text. A reader at the end stays
    at the end (no pill); a reader in the middle keeps the same line at the same height."""
    page = _page(app, 1024)                                 # the width where the column does narrow and the text re-wraps
    try:
        _reading(page, turns=10)
        page.evaluate("""() => { const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = ['house.html']; pvTurnCards(S.cur, foot, 0); }""")
        page.wait_for_timeout(300)
        page.evaluate("() => { const c = document.querySelector('#conv'); c.scrollTop = c.scrollHeight; }")
        page.wait_for_timeout(300)
        page.click(".pvcard")
        page.wait_for_selector("#pvp:not([hidden])")
        page.wait_for_timeout(700)
        end = page.evaluate("""() => { const c = document.querySelector('#conv'); return {gap: c.scrollHeight - c.scrollTop - c.clientHeight, pill: !document.querySelector('.jumpdown').hidden}; }""")
        assert end["gap"] < 80 and not end["pill"], end
        page.evaluate("() => closePreview()")
        page.wait_for_timeout(500)
        mid = page.evaluate("""() => { const c = document.querySelector('#conv'); S.settling = false; c.scrollTop = Math.round((c.scrollHeight - c.clientHeight) / 2); return 1; }""")
        page.wait_for_timeout(300)
        probe = """() => { const c = document.querySelector('#conv'), top = c.getBoundingClientRect().top;
            for (const k of document.querySelector('#col').children) { const r = k.getBoundingClientRect(); if (r.bottom > top + 1) return [k.textContent.slice(0, 40), Math.round(r.top - top)]; } }"""
        was = page.evaluate(probe)
        page.evaluate("() => document.querySelector('.pvcard').click()")      # a real click would scroll the card into view
        page.wait_for_selector("#pvp:not([hidden])")
        page.wait_for_timeout(700)
        now = page.evaluate(probe)
        assert now[0] == was[0] and abs(now[1] - was[1]) <= 3, (was, now)     # the same block, at the same height
        assert page.errors == [], page.errors
    finally:
        _close(page)


# ---------------------------------------------------------------------------------------------------- item 3
def test_an_html_file_card_opens_the_preview_and_has_a_picture(page):
    """The "Open house.html" card did nothing when clicked (only the link in the text opened the preview) and had a
    plain page icon. Now the card opens the preview like the others, and shows a small drawing of the page."""
    page.evaluate("w => post('/api/projects', {action: 'add', path: w})", page.app["work"])   # a folder the app may open
    _fake_chat(page, view={"workdir": page.app["work"]})
    page.evaluate("""() => { const foot = document.createElement('div'); $('#col').appendChild(foot);
        S.cur.tpaths = ['house.html']; pvTurnCards(S.cur, foot, 0); }""")
    page.wait_for_selector(".pvcard")
    page.evaluate("() => { const r = document.querySelector('.pvcards'); r.outerHTML = r.outerHTML; }")   # a rebuilt chat: handlers set on the buttons are gone
    page.click(".pvcard")
    page.wait_for_timeout(1500)
    got = page.evaluate("""() => ({open: !!document.querySelector('#pvp:not([hidden])'), frame: !!document.querySelector('#pvp iframe'),
        name: document.querySelector('#pvName')?.textContent, thumb: !!document.querySelector('.pvcard .pvi.html svg')})""")
    assert got["open"] and got["frame"] and got["name"] == "house.html", got
    assert got["thumb"], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- item 1
def test_the_word_file_check_says_nothing_about_repair_or_schemas(tmp_path, monkeypatch):
    """check_document told her "Word may still offer to repair the file" and "Schema validation did not run" about a
    file that was fine. A good file now gets one plain sentence; a real problem is named in plain words."""
    docx = pytest.importorskip("docx")
    from engines.forge import ooxml_check as OX
    from engines.forge.tools import check_document
    p = tmp_path / "update.docx"
    d = docx.Document()
    d.add_paragraph("Hello")
    d.save(p)
    for schemas_run in (False, True):                       # whether or not the schema files are installed here
        orig = OX.check
        monkeypatch.setattr("engines.forge.ooxml_check.check", lambda *a, _o=orig, _s=schemas_run, **k: dict(_o(*a, **k), schema_validation="run" if _s else "not_run"))
        msg, err = check_document(str(p))
        assert not err and "update.docx" in msg
        low = msg.lower()
        assert "repair" not in low and "schema" not in low and "not installed" not in low and "couldn" not in low, msg
        assert "opens and reads correctly" in low, msg
    monkeypatch.undo()
    import zipfile
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(bad, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "word/document.xml":
                data = data.replace(b"</w:body>", b"")        # an unclosed tag: a part that cannot be read
            zout.writestr(n, data)
    msg, _ = check_document(str(bad))
    assert "problem" in msg and "cannot be read" in msg, msg
    assert "schema" not in msg.lower()


def test_the_checked_line_says_what_was_checked_in_plain_words(tmp_path, monkeypatch):
    """"Checked: Office file structure, 20 workbook rules (4 did not pass)" is now "Checked the workbook: 16 of 20
    checks passed. Checked the file opens and reads correctly." The failing ones are named in a details list."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    from meshpool_harness import checks as K
    K.begin("priya")
    K.passed("office_structure", "the file opens and reads correctly", None, "rooms.xlsx")
    st = {f"X{i}": "pass" for i in range(16)} | {f"X{i}": "fail" for i in range(16, 20)}
    K.rules_ran("workbook_rules", "workbook rules", st, "rooms.xlsx",
                names={"X16": "Lengths are typed in, not linked", "X17": "No checks sheet"})
    sm = K.summary("priya")
    K.end("priya")
    assert sm["line"] == ("Checked the workbook: 16 of 20 checks passed. Checked the file opens and reads correctly."), sm["line"]
    assert "rules" not in sm["line"] and "did not pass" not in sm["line"]
    assert sm["failed"] == 4 and any("Lengths are typed in" in d for d in sm["details"]), sm
    assert all("workbook rules" not in i["label"] for i in sm["items"]), sm["items"]
    ev = K.events_for("priya")
    assert [e["text"] for e in ev if e["type"] == "check"] == ["the file opens and reads correctly", "16 checks on the workbook passed",
                                                              "4 checks on the workbook did not pass"], ev
    K.begin("all")
    K.rules_ran("deck_rules", "design rules", {"P1": "pass", "P2": "pass"}, "a.pptx")
    assert K.summary("all")["line"] == "Checked the deck: all 2 checks passed."


# ---------------------------------------------------------------------------------------------------- item 6
def test_chips_are_only_offered_when_they_relate_to_the_reply():
    """"Sensitivity table", "Chart the trend" and "One-slide summary" came up after a plain list of room sizes."""
    from meshpool_harness import insights as I
    rooms = {"texts": ["Here is the client's list of room sizes. Please make a summary workbook."], "outputs": ["rooms.xlsx"],
             "reply": "The summary lists eight rooms and the total floor area, 255.27 square metres."}
    assert I.followup_chips("workbook", rooms) == []
    letter = {"texts": ["Write a one-page update for our client."], "reply": "The one-page client update is saved."}
    assert I.followup_chips("document", letter) == [i for i in I.followup_chips("document") if i["id"] == "cover"]
    money = {"texts": ["Build a 5 year revenue forecast model."], "reply": "Revenue grows 8 percent a year in the model."}
    assert [c["id"] for c in I.followup_chips("workbook", money)] == ["sens", "chart"]
    assert [c["id"] for c in I.followup_chips("deck")] == ["notes", "memo"]       # no chat given: the plain table


def test_a_copy_of_an_attached_file_is_not_a_change(tmp_path):
    """"2 files changed" listed rooms.xlsx, which was not modified: the sheets engine had staged a copy of it in the
    job's folder. Compared by content hash: an identical copy of a file already in the folder is not a change; a
    file with different content is."""
    from meshpool_harness.ui import workbench as W
    (tmp_path / "rooms.xlsx").write_bytes(b"the same bytes")
    job = tmp_path / "Meshpool outputs" / "job1"
    job.mkdir(parents=True)
    (job / "rooms.xlsx").write_bytes(b"the same bytes")
    (tmp_path / "Room areas summary.xlsx").write_bytes(b"new work")
    (job / "other.xlsx").write_bytes(b"different")
    (tmp_path / "other.xlsx").write_bytes(b"not the same as the copy")
    turn = {"n": 1, "changed": [
        {"path": "Meshpool outputs/job1/rooms.xlsx", "status": "added"},
        {"path": "Meshpool outputs/job1/other.xlsx", "status": "added"},
        {"path": "Room areas summary.xlsx", "status": "added"}]}
    got = W.own_changes([turn], tmp_path)[0]["changed"]
    assert [c["path"] for c in got] == ["Meshpool outputs/job1/other.xlsx", "Room areas summary.xlsx"], got
    (job / "rooms.xlsx").write_bytes(b"edited by the job")          # content differs now: it counts
    assert len(W.own_changes([turn], tmp_path)[0]["changed"]) == 3


# ---------------------------------------------------------------------------------------------------- item 7
def test_an_empty_office_call_is_not_put_to_the_user(tmp_path, monkeypatch):
    """The approval card read "Make a Word document? {}": the model had sent an empty call. It is failed back to the
    model, with a plain reason, and the user is not asked."""
    from meshpool_harness.agent import core
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_s")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    (tmp_path / "mh").mkdir()
    asked, ran = [], []

    class Mcp:
        last_images: list = []

        def has(self, n):
            return n.startswith("mcp__")

        def call(self, n, args):
            ran.append((n, args))
            return "made", False

        def schemas(self):
            return []
    a = core.Agent(str(tmp_path), mode="ask", mcp=Mcp(), approve=lambda *x, **k: (asked.append(x[1]), ("yes", ""))[1])
    a.emit = lambda e: None
    out = a._run_tool({"id": "c1", "function": {"name": "mcp__meshpool_office__make_document", "arguments": "{}"}})
    assert asked == [] and ran == [], (asked, ran)
    assert "no content" in out and "make_document" in out, out
    out = a._run_tool({"id": "c2", "function": {"name": "mcp__meshpool_office__make_document",
                                                "arguments": json.dumps({"markdown": "# Hi", "filename": "a.docx"})}})
    assert len(asked) == 1 and asked[0].startswith("Make a Word document") and ran, (asked, ran)   # a real call still asks


# ---------------------------------------------------------------------------------------------------- item 2
def test_jargon_stays_out_of_the_chat(page):
    """The chat showed "context 1,049k (from the model table); older steps are summarized above 120k tokens", and a
    group with a step that failed read "1 did not work". The first is a model detail and stays out of the chat; the
    group says "had a problem"."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0});
        applyEv({type: 'status', text: 'context 1,049k (from the model table); older steps are summarized above 120k tokens', i: 1});
        applyEv({type: 'tool_start', id: 'a', name: 'read_file', summary: 'a.txt', i: 2}); applyEv({type: 'tool_end', id: 'a', ok: false, output: 'no', i: 3});
        applyEv({type: 'tool_start', id: 'b', name: 'read_file', summary: 'b.txt', i: 4}); applyEv({type: 'tool_end', id: 'b', ok: true, output: 'x', i: 5});
        applyEv({type: 'tool_start', id: 'c', name: 'read_file', summary: 'c.txt', i: 6}); applyEv({type: 'tool_end', id: 'c', ok: true, output: 'x', i: 7});
        return {text: document.querySelector('#col').textContent, group: document.querySelector('#col .work .wl').textContent}; }""")
    assert "older steps" not in got["text"] and "model table" not in got["text"], got
    assert got["group"] == "3 steps, 1 had a problem" and "did not work" not in got["text"], got
    assert page.errors == [], page.errors


def test_the_checks_line_has_a_details_list_for_what_did_not_pass(page):
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'go', i: 0}); applyEv({type: 'assistant', text: 'Done.', i: 1}); applyEv({type: 'turn_end', i: 2});
        applyEv({type: 'checks_line', text: 'Checked the workbook: 16 of 20 checks passed.', items: [{label: '16 checks on the workbook passed'}],
                 details: ['X16: Lengths are typed in, not linked'], i: 3});
        const d = document.querySelector('#col .chkline'); return d ? {summary: d.querySelector('summary').textContent, det: d.querySelector('ul.chkdet')?.textContent || ''} : null; }""")
    assert got and got["summary"] == "Checked the workbook: 16 of 20 checks passed." and "Lengths are typed in" in got["det"], got


def test_the_code_sessions_group_is_only_for_code_chats(page):
    """"Code sessions" was the Search heading over Priya's office chats, which were made in a folder from the chat
    screen. A chat made there is a chat; a session started from the Code tab is a code session."""
    got = page.evaluate("""async w => { const pr = await post('/api/pj', {action: 'create', name: 'Studio', link: {type: 'folder', path: w}}); const pid = pr.id || (pr.project && pr.project.id);
        const office = await post('/api/agent', {workdir: w, project_id: pid, harness: 'meshpool-auto', task: 'Please make a simple web page for our open house with a sign-up form'});
        const search = async () => (await api('/api/hub/search?q=&tab=all')).groups;
        const g1 = (await api('/api/hub/search?q=chat&tab=all')).groups.map(g => g.key);
        const code = await post('/api/agent', {workdir: w, kind: 'code', task: 'tabs'});
        const g2 = (await api('/api/hub/search?q=chat&tab=all')).groups.map(g => g.label);
        await loadThreads();
        const row = id => { const t = S.threads.find(x => x.id === id); return t && {kind: t.kind, code_mode: t.code_mode, isCode: isCodeThread(t)}; };
        return {g1, g2, office: row(office.id), code: row(code.id)}; }""", page.app["work"])
    assert got["office"] == {"kind": "code", "code_mode": False, "isCode": False}, got
    assert got["code"]["isCode"] is True, got
    assert "code" not in got["g1"] and "sessions" in got["g1"], got
    assert "Code sessions" in got["g2"] and "Chats" in got["g2"], got


def test_saved_rules_begin_with_a_plain_sentence(page):
    page.evaluate("""() => { const b = document.createElement('div'); b.id = 'rulesBoxT'; document.body.appendChild(b);
        drawRules(b, {allow: [], ask: [], deny: [], folders: [], path: '/x/permissions.json'}); }""")
    got = page.evaluate("() => [...document.querySelectorAll('#rulesBoxT > p')].map(p => p.textContent)")
    assert got[0].startswith("These are the things you told meshpool to always allow") and "bash:git push*" in got[1], got


# ---------------------------------------------------------------------------------------------------- item 4
def test_recents_say_nothing_about_starting_a_chat_once_chats_exist(page):
    """After two chats existed, "Recents" still said "Your chats show up here. Start one with New.": chats made in a
    folder from the chat screen counted as code sessions, so the chat list left them out."""
    got = page.evaluate("""async w => { const pr = await post('/api/pj', {action: 'create', name: 'Studio', link: {type: 'folder', path: w}}); const pid = pr.id || (pr.project && pr.project.id);
        S.mode = 'chat';
        await post('/api/agent', {workdir: w, project_id: pid, harness: 'meshpool-auto', task: 'Please make a simple web page for our open house'}); await post('/api/agent', {workdir: w, project_id: pid, harness: 'meshpool-auto', task: 'Please make a simple web page for our sale'});
        await loadThreads(); await loadPj?.(); renderSide();
        const side = document.querySelector('#sideList');
        const sec = [...side.querySelectorAll('.sec')].find(x => /Recents/.test(x.textContent));
        return {empty: [...side.querySelectorAll('.empty-side')].map(x => x.textContent), rows: side.querySelectorAll('ul.threads:not(.prj):not(.sub) > li.th').length}; }""", page.app["work"])
    assert not any("Your chats show up here" in t for t in got["empty"]) and got["rows"] >= 2, got
    page.evaluate("() => { S.threads = []; renderSide(); }")                      # with no chats at all it is the hint
    assert "Your chats show up here" in page.inner_text("#sideList")


# ---------------------------------------------------------------------------------------------------- front-end review
def test_the_jump_to_latest_pill_is_not_moved_by_the_layout_slide(app):
    """flipLayout slid every moving element with translateX(from) -> none, which replaced the pill's own
    translateX(-50%) for 200 ms: toggling the sidebar while scrolled up made it jump by half its width."""
    page = _page(app, 1440)
    try:
        _reading(page, turns=10)
        page.evaluate("() => { S.settling = false; const c = document.querySelector('#conv'); c.scrollTop = 200; }")
        page.wait_for_function("() => !document.querySelector('.jumpdown').hidden")
        got = page.evaluate("""() => { const jd = document.querySelector('.jumpdown'); const before = getComputedStyle(jd).transform;
            toggleSide(); const anim = jd.getAnimations().filter(a => a.id === 'flip').length; return {before, anim, now: getComputedStyle(jd).transform}; }""")
        assert got["before"] != "none" and got["anim"] == 0 and got["now"] == got["before"], got
    finally:
        _close(page)


def test_a_markdown_image_inside_inline_code_stays_text(page):
    _fake_chat(page, view={"workdir": page.app["work"]})
    got = page.evaluate("""() => inline('write `![chart](chart.png)` to show it, and ![chart](chart.png) here')""")
    assert got.count("<img") == 1, got
    assert "<code" in got and "![chart](chart.png)</code>" in got.replace('class="mdimg"', ""), got


def test_front_end_nits_are_gone():
    ui = REPO / "app" / "meshpool_harness" / "ui"
    shell_js, shell_css = (ui / "shell.js").read_text(), (ui / "shell.css").read_text()
    assert 'classList.add("kbd")' not in shell_js and 'classList.remove("kbd")' not in shell_js and "kbdnav" in shell_js
    assert not re.search(r"(^|\s)\.kbd\s+\.", shell_css) and "html.kbd," not in shell_css and ".kbdnav .composer:focus-within" in shell_css
    assert "memAddEmpty" not in (ui / "memory.js").read_text()
    s2 = (ui / "settings2.js").read_text()
    i = s2.index("// Base URL and key -> Test connection")
    assert s2[i:].split("\n")[2].startswith("async function providerPage"), s2[i:i + 400]
    assert "// Plan and billing: what this computer runs on" in s2 and s2.index("// Plan and billing") < s2.index("function billingText")
    assert "text-decoration" not in ""      # (keeps the module's imports used)
