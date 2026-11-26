"""Problems the persona testers (Maya, Priya, Omar, Lina, Daniel) found in the 0.0.19 release candidate: the file preview,
pictures in the reply, the side panel in Chat mode, jargon, noisy steps, nagging tips, chips, small layout moves,
onboarding, finding files, the Undo dialog, retitled chats and the Your work page. Each test drives the real app
(python -m meshpool_harness ui) in Chromium against the mock model, the way tests/test_ui_bugs018.py does, or calls the
server code directly. Each one failed before its fix. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import base64
import os
import re
import sys
import time
from pathlib import Path

import pytest

from tests.test_jitter018 import CLS, _shift
from tests.test_ux018 import _fake_chat, app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)

pytest.importorskip("playwright.sync_api")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

def _png(w: int = 900, h: int = 450) -> bytes:
    """A plain grey picture of the given size (no imaging library needed)."""
    import struct
    import zlib

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xc8" * (w * 3) for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PNG = _png()


def _root(page, folder: str):
    """The fake chat works in the test folder (the page reads the folder from the open chat's view)."""
    page.evaluate("""async w => { await post('/api/projects', {action: 'add', path: w});          // a folder the server knows, so /raw serves its files
        S.cur.view = Object.assign(S.cur.view || {}, {workdir: w, kind: 'cowork'}); }""", folder)


def _chat(page, workdir: str, kind: str | None = None, task: str = "hello") -> str:
    """A real chat on the server, in a folder, open in the page. No kind: a chat in a folder that was not started from the
    Code tab (Chat mode); kind 'code': from the Code tab."""
    return page.evaluate("""async ([w, k, t]) => { const v = await post('/api/agent', Object.assign({workdir: w, task: t}, k ? {kind: k} : {}));
        await loadThreads(); openThread(v.id); return v.id; }""", [workdir, kind, task])


# ------------------------------------------------------------------------------------------------------ item 1
def test_html_preview_runs_forms_but_stays_isolated(app, page):
    """The preview frame had sandbox="allow-scripts" only, so a sign-up form could not be submitted in it. It allows forms
    now, never allow-same-origin together with scripts, and a submit stays in the frame."""
    url = page.url
    page.evaluate("""() => openPreviewCode('html', '<form id=f><input name=who id=who><button id=go>Sign up</button></form><p id=out></p>'
        + '<script>document.getElementById("f").addEventListener("submit", e => { e.preventDefault(); document.getElementById("out").textContent = "Thanks " + document.getElementById("who").value; });<\\/script>')""")
    page.wait_for_selector(".pv-frame")
    sb = page.get_attribute(".pv-frame", "sandbox")
    assert "allow-forms" in sb and "allow-scripts" in sb and "allow-same-origin" not in sb and "allow-top-navigation" not in sb, sb
    fr = page.frame_locator(".pv-frame")
    fr.locator("#who").fill("Priya")
    fr.locator("#go").click()
    fr.locator("#out").wait_for()
    assert fr.locator("#out").inner_text() == "Thanks Priya"
    assert page.url == url and page.locator("#newThread").count() == 1       # the app did not navigate
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 2
def test_a_picture_asked_for_shows_large_in_the_reply(app, page):
    """A chart "shown in the chat" was only a small thumbnail card. The picture is shown large, no wider than the column,
    lazy, opens the preview on a click, and the file card stays."""
    Path(app["work"], "chart.png").write_bytes(PNG)
    Path(app["work"], "notes.pdf").write_bytes(b"%PDF-1.4 x")
    _fake_chat(page)
    _root(page, app["work"])
    page.evaluate("""() => { applyEv({type: 'user', text: 'Make a bar chart of the sales and show it here in the chat', i: 0});
        const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = ['chart.png', 'notes.pdf']; pvTurnCards(S.cur, foot, 0); }""")
    page.wait_for_selector(".pvbig", state="attached")
    page.wait_for_function("() => document.querySelector('.pvbig img').naturalWidth > 0")
    got = page.evaluate("""() => { const i = document.querySelector('.pvbig img'), c = document.querySelector('#col');
        return {lazy: i.loading, w: document.querySelector('.pvbig').getBoundingClientRect().width, col: c.getBoundingClientRect().width,
                cards: document.querySelectorAll('.pvcard').length}; }""")
    assert got["lazy"] == "lazy" and got["w"] <= got["col"] + 1 and got["cards"] == 2, got
    page.click(".pvbig")
    page.wait_for_selector("#pvp:not([hidden])")
    assert page.evaluate("() => PVX.path") == "chart.png"
    # not asked, and a file that is not a picture was made too: only the card
    page.evaluate("() => { closePreview(); $('#col').replaceChildren(); }")
    page.evaluate("""() => { applyEv({type: 'user', text: 'Summarise the numbers', i: 1});
        const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = ['chart.png', 'notes.pdf']; pvTurnCards(S.cur, foot, 0); }""")
    page.wait_for_selector(".pvcard")
    assert page.locator(".pvbig").count() == 0
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 3
def test_chat_mode_side_panel_is_plain_and_code_mode_keeps_its_tabs(app, page):
    """Maya's Progress panel showed Terminal, Git and CI, Connectors, "used 5x", "Working as", "Not a Git repository"
    and a context percentage. In Chat mode the tabs and lines for developers are gone; Code mode keeps them."""
    _chat(page, app["work"], None, "chat mode panel")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir")
    page.evaluate("() => togglePanel('session', true)")
    page.wait_for_selector("#pbody .spw")
    tabs = page.evaluate("() => [...document.querySelectorAll('#ptabs [data-t]')].map(b => b.textContent.trim())")
    assert not {"Terminal", "Git and CI", "Changes", "Files"} & set(tabs), tabs
    txt = page.inner_text("#pbody")
    for bad in ("Connectors", "Working as", "Not a Git repository", "Context", "used 5", "Terminal"):
        assert bad not in txt, (bad, txt)
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#pgSteps")
    assert "Connectors" not in page.inner_text("#pbody") and page.locator("#pgHarness").count() == 0
    _chat(page, app["work"], "code", "code mode panel")
    page.evaluate("() => togglePanel('session', true)")
    page.wait_for_selector("#pbody .spw")
    tabs = page.evaluate("() => [...document.querySelectorAll('#ptabs [data-t]')].map(b => b.textContent.trim())")
    assert {"Terminal", "Git and CI", "Changes"} <= set(tabs), tabs
    assert "Connectors" in page.inner_text("#pbody")
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 4
def test_a_connector_step_is_not_named_by_its_internal_name(app, page):
    """"Using ui smoke" named the connector's tool the way its code does. It reads "Using a connector"."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'tool_start', id: 'm1', mcp: true, name: 'mcp__uisrv__ui_smoke', summary: '', i: 1});
        const run = document.querySelector('#tool-m1 .tn').textContent;
        applyEv({type: 'tool_end', id: 'm1', ok: true, output: 'fine', i: 2});
        return [run, document.querySelector('#tool-m1 .tn').textContent]; }""")
    assert got == ["Using a connector", "Used a connector"], got


def test_a_step_that_worked_on_retry_is_not_counted_as_a_problem(app, page):
    """"3 steps, 1 had a problem" counted a step that failed and then worked when it was tried again. It says retried."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'run it', i: 0});
        applyEv({type: 'tool_start', id: 'a', name: 'bash', summary: 'python3 run.py', i: 1}); applyEv({type: 'tool_end', id: 'a', ok: false, output: 'Traceback', i: 2});
        applyEv({type: 'tool_start', id: 'b', name: 'bash', summary: 'python3 run.py', i: 3}); applyEv({type: 'tool_end', id: 'b', ok: true, output: 'done', i: 4});
        return {label: document.querySelector('.work .wl').textContent, first: document.querySelector('#tool-a .st').textContent}; }""")
    assert got["label"] == "2 steps" and got["first"] == "retried", got


def test_the_reply_footer_does_not_say_code_mode(app, page):
    """"DeepSeek V4.1 Flash, code mode": the router's note is not for people."""
    from meshpool_harness import receipts as R
    assert R.plain_reason("Code mode") == ""
    bits = page.evaluate("""() => receiptBits010({type: 'task_receipt', secs: 8, cost: 0.004, model: 'deepseek-v4.1-flash', harness: 'meshpool-auto',
        harness_name: 'meshpool', reason: 'Code mode', text: '8 s · <$0.01 · DeepSeek V4.1 Flash, code mode'})""")
    assert " ".join(bits) == "8 s <$0.01 DeepSeek V4.1 Flash", bits


def test_service_down_line_is_plain_has_stop_and_never_shows_still_waiting(app, page):
    """"The model endpoint isn't answering (connection refused). Checking again in 30 s, attempt 4..." read like a log and
    came with "Still waiting for the model" at the same time."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'a nice winter flower?', i: 0});
        S.cur.working = true; S.cur.since = Date.now() - 60000; S.cur.lastEvAt = Date.now() - 60000; S.cur.view.state = 'working';
        applyEv({type: 'status', down: true, attempt: 3, i: 1, text: "The model endpoint isn't answering (connection refused). Checking again in 30 s, attempt 4, for up to 10 min. Press Stop to end."});
        workTick(S.cur);
        const n = [...document.querySelectorAll('#col .note.retry')];
        return {text: n.map(x => x.querySelector('.dn')?.textContent), stop: !!n[0]?.querySelector('[data-dstop]'), wait: !!document.querySelector('#waitNote'), all: document.querySelector('#col').textContent}; }""")
    assert got["text"] == ["The AI service is not answering. Trying again in 30 s."] and got["stop"] and not got["wait"], got
    assert "endpoint" not in got["all"] and "attempt" not in got["all"] and "refused" not in got["all"], got


def test_top_bar_computer_menu_and_search_tabs_use_plain_words(app, page):
    """The computer menu showed the machine's name ("vm"), "Connected" and "Open in meshpool editor"; the search tabs said
    "Sessions" and showed Code to people with no Code chats."""
    _fake_chat(page)
    page.evaluate("() => { S.acct = {device: {name: 'vm'}}; S.cur.view = Object.assign(S.cur.view || {}, {workdir: '/x/y', kind: 'cowork'}); renderTop(); }")
    page.click("#devBtn")
    menu = page.inner_text("#ctx")
    assert "vm" not in menu and "Connected" not in menu and "meshpool editor" not in menu and "This computer" in menu, menu
    page.keyboard.press("Escape")
    page.evaluate("() => { S.threads = (S.threads || []).filter(t => !isCodeThread(t)); openSearch(); }")
    page.wait_for_selector(".srch-tabs [data-tab]")
    tabs = page.evaluate("() => [...document.querySelectorAll('.srch-tabs [data-tab]')].map(b => b.textContent.trim())")
    assert "Chats" in tabs and "Sessions" not in tabs and "Code" not in tabs, tabs
    page.keyboard.press("Escape")
    page.evaluate("() => { S.threads = [...S.threads, {id: 'zz1', kind: 'code', title: 'x', scratch: false, code_mode: true}]; openSearch(); }")
    page.wait_for_selector(".srch-tabs [data-tab]")
    assert "Code" in page.evaluate("() => [...document.querySelectorAll('.srch-tabs [data-tab]')].map(b => b.textContent.trim())")
    page.keyboard.press("Escape")


def test_temp_folder_paths_show_as_file_names_in_toasts_and_replies(app, page):
    """A full temp-folder path in a toast or a reply is the file's name, with a click to open it."""
    page.evaluate("() => toast('Could not open /tmp/pytest-9/abc123/Quarter report.docx. Try again.')")
    assert page.inner_text(".toast") == "Could not open Quarter report.docx. Try again."
    _fake_chat(page)
    _root(page, app["work"])
    got = page.evaluate("""() => { const w = curRoot(); const html = linkFiles(md('Saved ' + w + '/Meshpool outputs/q3/Budget.xlsx and /tmp/zz9/other/Plan.pdf'));
        const d = document.createElement('div'); d.innerHTML = html; return [...d.querySelectorAll('.flink:not(.rimgb)')].map(b => [b.textContent, b.dataset.open, b.dataset.root || '']); }""")
    assert got[0][0] == "Budget.xlsx" and got[0][1] == "Meshpool outputs/q3/Budget.xlsx", got
    assert got[1][0] == "Plan.pdf" and got[1][2] == "/tmp/zz9/other", got


# ------------------------------------------------------------------------------------------------------ item 5
def test_command_steps_show_a_short_title_and_clean_output(app, page):
    """Lina and Omar saw "Chunk ID", "Wall time", "Original token count" in the step view and a whole script on the header
    line. The header is a short title; the script and the command's own output are in the body."""
    _fake_chat(page)
    script = "set -e\ncd data\n" + "\n".join(f"echo line {i} >> out.txt" for i in range(12))
    got = page.evaluate("""s => { applyEv({type: 'tool_start', id: 'x1', name: 'exec_command', summary: s, i: 1});
        applyEv({type: 'tool_end', id: 'x1', ok: true, output: 'Chunk ID: a1b2c3\\nWall time: 0.0421 seconds\\nProcess exited with code 0\\nOriginal token count: 4\\nOutput:\\nhello world\\n', i: 2});
        const d = document.querySelector('#tool-x1'); return {title: d.querySelector('.ts').textContent, script: d.querySelector('pre.script')?.textContent, out: d.querySelector('pre.out')?.textContent}; }""", script)
    assert len(got["title"]) <= 80 and "\n" not in got["title"], got
    assert got["script"] == script and got["out"].strip() == "hello world", got
    for bad in ("Chunk ID", "Wall time", "Original token count", "Output:"):
        assert bad not in got["out"], got


# ------------------------------------------------------------------------------------------------------ item 6
def test_no_tips_on_a_fresh_code_page_and_a_dismissed_tip_stays_gone(app, page):
    """Omar saw the Plan tip and "Save this routine as a skill?" stacked above the composer on a fresh Code page. Nothing
    shows before the first message; after a reply at most one tip shows; a dismissed one never comes back."""
    page.evaluate("() => { try { localStorage.removeItem('mp_tips_off'); localStorage.removeItem('mp_nudge_off'); } catch (e) {} }")
    page.route(re.compile(r".*/api/away.*"), lambda r: r.fulfill(json={"cards": [{"id": "skill-1", "type": "skill", "title": "Save this routine as a skill?",
              "body": "Ran in 3 chats", "action": {"label": "Save"}}], "runs": [], "quiet": []}))
    page.evaluate("() => { S.mode = 'code'; syncMode(); newThread(); }")
    page.wait_for_timeout(2500)
    assert page.locator(".tipbar, .nudge010").count() == 0, page.inner_html(".composer-wrap")[:400]
    _fake_chat(page)
    page.evaluate("() => { S.mode = 'code'; S.cur.ready = true; applyEv({type: 'user', text: 'fix it', i: 0}); applyEv({type: 'turn_end', i: 1}); }")
    page.evaluate("() => insightCard010({id: 'skill-1', type: 'skill', title: 'Save this routine as a skill?', body: 'Ran in 3 chats', action: {label: 'Save'}})")
    page.wait_for_timeout(500)
    assert page.locator(".nudge010").count() == 1, page.locator(".nudge010").count()      # one at a time, though two were offered
    shown = page.get_attribute(".nudge010", "data-id")
    page.click(".nudge010 [data-ic=dismissed]")
    page.wait_for_timeout(900)
    page.evaluate("""() => { insightCard010({id: 'skill-1', type: 'skill', title: 'Save this routine as a skill?', body: 'x', action: {label: 'Save'}});
        insightCard010({id: 'tip-plan', title: 'Tip:', body: 'x', action: {label: 'Try it'}, local() {}}); applyEv({type: 'turn_end', i: 3}); }""")
    page.wait_for_timeout(900)
    assert page.locator(f'.nudge010[data-id="{shown}"]').count() == 0 and page.locator(".nudge010, .tipbar").count() <= 1


# ------------------------------------------------------------------------------------------------------ item 7
def test_workbook_chips_need_numbers_over_time_and_a_used_chip_goes(app, page, tmp_path):
    """"Sensitivity table" and "Chart the trend" were offered for a flower shop's price list; "Draft a cover note" stayed
    after it was used."""
    openpyxl = pytest.importorskip("openpyxl")
    from meshpool_harness import insights as I
    prices = tmp_path / "Bouquet Price List.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Bouquet", "Price"]); ws.append(["Roses", 24]); ws.append(["Tulips", 18])
    wb.save(prices)
    chat = {"texts": ["Make a price list for my flower shop"], "reply": "The price list has monthly specials and a price for each bouquet.",
            "outputs": [prices.name], "workdir": str(tmp_path)}
    assert I.followup_chips("workbook", chat) == []
    sales = tmp_path / "Sales.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Month", "Revenue"]); ws.append(["Jan", 100]); ws.append(["Feb", 120])
    wb.save(sales)
    chat2 = {"texts": ["Build a revenue forecast"], "reply": "Revenue grows each month.", "outputs": [sales.name], "workdir": str(tmp_path)}
    assert [c["id"] for c in I.followup_chips("workbook", chat2)] == ["sens", "chart"]
    _fake_chat(page)
    got = page.evaluate("""() => { S.cur.ready = true; applyEv({type: 'user', text: 'write a letter', i: 0}); applyEv({type: 'turn_end', i: 1});
        const card = i => ({type: 'insight', card: {type: 'followup', id: 'f' + i, title: 'Draft a cover note', action: {label: 'Draft a cover note', payload: {text: 'Draft it'}}}});
        ev010(card(1)); const before = document.querySelectorAll('.schip').length;
        document.querySelector('.schip').click();
        const after = document.querySelectorAll('.schip').length; ev010(card(2));
        return [before, after, document.querySelectorAll('.schip').length]; }""")
    assert got == [1, 0, 0], got


# ------------------------------------------------------------------------------------------------------ item 8
def test_attaching_a_file_does_not_move_the_home_block(app, page):
    """Attaching a file on the home screen moved the whole block up by 28 px."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("() => newThread()")
    page.wait_for_timeout(900)
    top = "() => Math.round(document.querySelector('.hero h1').getBoundingClientRect().top * 10) / 10"
    before = page.evaluate(top)
    page.evaluate(CLS)
    s = _shift(page, "() => { S.attachments.push({name: 'Bouquet Price List.xlsx', mime: 'x', data: '', image: false}); renderAttach(); }")
    assert abs(page.evaluate(top) - before) < 1 and s < 0.005, (before, page.evaluate(top), s)
    page.evaluate("() => { S.attachments = []; renderAttach(); }")


def test_the_folder_picker_keeps_its_place_and_size_as_the_path_changes(app, page):
    """The folder picker jumped in position and height as the path grew (the path wrapped onto more lines)."""
    w = Path(app["work"])
    deep = w / "a_long_folder_name_one" / "another_quite_long_folder_name_two" / "yet_another_long_folder_name_three" / "four_four_four_four_four"
    deep.mkdir(parents=True, exist_ok=True)
    page.evaluate("() => { window.__bf = browseFolder(''); }")
    page.wait_for_selector(".dlg.fb")
    page.wait_for_timeout(500)
    box = "() => { const r = document.querySelector('.dlg.fb').getBoundingClientRect(); return [Math.round(r.top), Math.round(r.height)]; }"
    seen = [page.evaluate(box)]
    for p in (str(w), str(deep), str(deep.parent), str(w / "a_long_folder_name_one")):
        page.fill("#fbIn", p)
        page.press("#fbIn", "Enter")
        page.wait_for_timeout(450)
        seen.append(page.evaluate(box))
    assert len(set(map(tuple, seen))) == 1, seen
    page.keyboard.press("Escape")


def test_two_toasts_from_one_undo_are_one_toast(app, page):
    """After Undo two toasts stacked at the top right ("Your message is back..." and "x was removed...") and covered the page."""
    _fake_chat(page)
    page.evaluate("() => { document.querySelectorAll('.toast').forEach(t => t.remove()); S.attachments = []; }")
    page.evaluate("""() => { fillInput("Add the line\\n\\nAttached files (in this chat's folder): a.xlsx", {back: true, removed: ['a.xlsx']});
        PVX.open = true; PVX.root = '/r'; PVX.path = 'b.html'; afterUndo('/r', {deleted: ['b.html']}); }""")
    page.wait_for_timeout(300)
    got = page.evaluate("() => [...document.querySelectorAll('.toast')].map(t => t.textContent)")
    assert len(got) == 1 and "back in the box" in got[0] and "b.html was removed" in got[0] and "a.xlsx was removed" in got[0], got


def test_the_sheet_preview_has_no_sideways_scrollbar_and_the_changes_picker_keeps_its_words_at_1024(app, page):
    """At 1024 the preview of a sheet that fits had a horizontal scrollbar (its columns added up to a pixel too many), and
    the Changes reply picker shrank to a bare chevron."""
    openpyxl = pytest.importorskip("openpyxl")
    w = app["work"]
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Room", "Length (m)", "Width (m)", "Area (m2)"])
    for i, n in enumerate(["Reception", "Open studio", "Model workshop", "Meeting room A", "Meeting room B", "Kitchen", "Library", "Print room"]):
        ws.append([n, 5 + i, 4 + i / 2, f"=B{i + 2}*C{i + 2}"])
    wb.save(os.path.join(w, "rooms4.xlsx"))
    page.set_viewport_size({"width": 1024, "height": 700})
    _chat(page, w, "code", "tabs")
    page.evaluate("([w, n]) => openPreview(w, n)", [w, "rooms4.xlsx"])
    page.wait_for_selector(".pv-sheet table")
    page.wait_for_timeout(600)
    assert page.evaluate("() => { const s = document.querySelector('.pv-sheet'); return s.scrollWidth - s.clientWidth; }") <= 0
    page.evaluate("() => closePreview()")
    page.evaluate("""() => { S.cur.view.turns = [{n: 1, title: 'Raise all the prices in my bouquet spreadsheet by ten percent please', changed: [{path: 'a.txt', status: 'modified'}]}];
        S.diffTurn = 1; togglePanel('diff', true); }""")
    page.wait_for_selector("#dturn")
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.querySelector('#dturn').getBoundingClientRect().width") >= 120


# ------------------------------------------------------------------------------------------------------ item 9
def test_bring_your_setup_ticks_nothing_by_default(app, page):
    """Step 2 pre-ticked other tools' skills. Nothing from other tools is ticked until the person ticks it."""
    page.route(re.compile(r".*/api/import-agents$"), lambda r: r.fulfill(json={"agents": [
        {"id": "cc", "name": "Claude Code", "counts": {"skill": 2}, "credentials_not_read": [], "items": [
            {"key": "k1", "kind": "skill", "name": "unslop", "exists": False, "preview": "x"},
            {"key": "k2", "kind": "skill", "name": "session-start-hook", "exists": False, "preview": "y"}]},
        {"id": "h", "name": "Hermes", "counts": {"instructions": 1}, "credentials_not_read": [], "items": [
            {"key": "k3", "kind": "instructions", "name": "SOUL.md", "exists": False, "preview": "z"}]}]}))
    page.evaluate("() => { const b = document.createElement('div'); b.id = 'impBox'; document.body.appendChild(b); importPanel(b); }")
    page.wait_for_selector("#impBox [data-key]")
    assert page.evaluate("() => [...document.querySelectorAll('#impBox [data-key]')].filter(x => x.checked).length") == 0


def test_folder_picker_has_new_folder_and_starts_in_documents(app, page):
    """Maya could not make "Maya's Flowers" in the picker, and it started in the home folder, not Documents."""
    home = Path(app["work"]).parent / "home"
    (home / "Documents").mkdir(parents=True, exist_ok=True)
    page.evaluate("() => { try { localStorage.removeItem('mp_fb_last'); } catch (e) {} window.__bf = browseFolder(''); }")
    page.wait_for_selector(".dlg.fb")
    page.wait_for_function("() => document.querySelector('#fbCrumbs .here')")
    assert page.inner_text("#fbCrumbs .here") == "Documents"
    page.click("#fbNew")
    page.fill("#fbNewIn", "Maya's Flowers")
    page.press("#fbNewIn", "Enter")
    page.wait_for_function("() => document.querySelector('#fbCrumbs .here')?.textContent === \"Maya's Flowers\"")
    assert (home / "Documents" / "Maya's Flowers").is_dir()
    assert "Maya's Flowers" in page.inner_text("[data-a='1']")
    page.keyboard.press("Escape")


def test_onboarding_does_not_preselect_start_without_a_folder(app, page):
    """Onboarding preselected "Start without a folder", so choosing a folder took two clicks and a confirm. (Round 2: it no
    longer marks "Folders I choose" either before a folder is added: see test_round2_019.py.) One click on it opens the list."""
    page.evaluate("() => { S.projects = []; permStep(); }")
    page.wait_for_selector("#onbPerm [data-scope=pick]")
    assert page.get_attribute("[data-scope=later]", "aria-checked") != "true"


# ------------------------------------------------------------------------------------------------------ item 10, 16
def test_project_page_lists_the_folders_files_and_opens_them(app, page):
    """The project page had no file list and no way to open the folder; "Reading the project..." stayed on screen and it
    talked about a branch for a folder that is not a Git repository."""
    w = Path(app["work"]) / "proj_files"
    w.mkdir(exist_ok=True)
    (w / "Price list.txt").write_text("roses 24")
    (w / "Plan.md").write_text("# plan")
    pid = page.evaluate("""async w => { const r = await post('/api/pj', {name: 'ClientWork', link: {type: 'folder', path: w}}); await loadPj(); openProject(r.id); return r.id; }""", str(w))
    page.wait_for_selector("#pjFiles .pjf")
    names = page.evaluate("() => [...document.querySelectorAll('#pjFiles .pjf')].map(b => b.textContent.trim())")
    assert sorted(names) == ["Plan.md", "Price list.txt"], names
    assert "branch" not in page.inner_text(".pjside").lower()
    page.click("#pjFiles .pjf >> text=Plan.md")
    page.wait_for_selector("#pvp:not([hidden])")
    assert page.evaluate("() => PVX.path") == "Plan.md"
    assert page.locator("#pjBrowse").count() == 1


def test_the_project_brief_gives_up_waiting_after_a_few_seconds(app, page):
    """"Reading the project..." stayed on the project page when the summary was slow."""
    page.route(re.compile(r".*/api/summaries/project.*"), lambda r: None)       # never answered
    page.evaluate("""async () => { const r = await post('/api/pj', {name: 'Slow brief'}); await loadPj(); openProject(r.id); }""")
    page.wait_for_selector("#pjBrief")
    page.wait_for_function("() => /taking a while/.test(document.querySelector('#pjBrief').textContent)", timeout=14000)
    assert "Reading the project" not in page.inner_text("#pjBrief")


def test_download_says_so_in_a_toast(app, page):
    """No toast followed Download."""
    page.evaluate("() => pvDownload('/r', 'Budget.xlsx')")
    assert "Downloaded Budget.xlsx" in page.inner_text(".toast")


# ------------------------------------------------------------------------------------------------------ item 11
def test_undo_result_line_lists_each_file_once_with_its_folder():
    from meshpool_harness.ui import agent_api as A
    msg, _ = A.undo_message({"restored": [], "deleted": [], "kept": ["Prices/a.xlsx", "b.xlsx", "Prices/a.xlsx", "b.xlsx"], "outside": [], "notes": []})
    assert msg.count("a.xlsx") == 1 and msg.count("b.xlsx") == 1 and "a.xlsx (in Prices)" in msg, msg
    msg, _ = A.undo_message({"restored": ["x.py", "x.py"], "deleted": [], "kept": [], "outside": []})
    assert "Restored 1 file (x.py)" in msg, msg


def test_undo_dialog_lists_the_files_that_will_change(app, page):
    """The Undo confirm dialog did not say which files would change."""
    _fake_chat(page)
    page.evaluate("""() => { S.cur.view.turns = [{n: 1, changed: [{path: 'Prices/Bouquets.xlsx', status: 'modified'}, {path: 'notes.md', status: 'added'}]},
        {n: 2, changed: [{path: 'notes.md', status: 'modified'}]}]; undoTurn(1); }""")
    page.wait_for_selector(".dlg .asklist")
    items = page.evaluate("() => [...document.querySelectorAll('.dlg .asklist li')].map(x => x.textContent)")
    assert items == ["Bouquets.xlsx (in Prices)", "notes.md"], items
    page.click(".dlg [data-a='0']")


# ------------------------------------------------------------------------------------------------------ item 12
def test_use_the_other_agent_instead_sends_the_last_message_again(app, page):
    """"Use meshpool Code instead" only flipped a label. It sends the last message again with the new agent."""
    _fake_chat(page)
    posts = []
    page.route(re.compile(r".*/api/agent/[^/]+/harness$"), lambda r: (posts.append(("harness", r.request.post_data)), r.fulfill(json={"harness": {"id": "meshpool-code", "name": "meshpool Code"}})))
    page.route(re.compile(r".*/api/agent/[^/]+/send$"), lambda r: (posts.append(("send", r.request.post_data)), r.fulfill(json={"sent": {}})))
    page.evaluate("""() => { S.cur.view.harness = {id: 'codex', name: 'Codex'}; applyEv({type: 'user', text: 'Tidy the budget sheet', i: 0});
        applyEv({type: 'status', using: true, text: 'Using Codex', back: {harness: 'meshpool-code', name: 'meshpool Code'}, i: 1}); }""")
    page.click("[data-back]")
    page.wait_for_function("() => true")
    for _ in range(30):
        if any(k == "send" for k, _ in posts):
            break
        page.wait_for_timeout(100)
    kinds = [k for k, _ in posts]
    assert kinds[:2] == ["harness", "send"] and "Tidy the budget sheet" in posts[1][1], posts


# ------------------------------------------------------------------------------------------------------ item 13
def test_a_chat_is_titled_after_the_deliverable_not_a_go_ahead_message(tmp_path, monkeypatch):
    """Chats were titled by the first sentence ("Go ahead with that memo, ...") and the memo chat took its retry message.
    Once a turn makes a file the chat takes the file's name, unless the user renamed it."""
    from meshpool_harness.ui import agent_api as A
    monkeypatch.setattr(A, "SESSIONS", tmp_path)
    sid = "ab12cd34ef56"
    (tmp_path / sid).mkdir()
    root = tmp_path / "w"
    root.mkdir()
    deck = root / "Ridgeway_PdM_Client_Deck.pptx"
    deck.write_bytes(b"x")
    assert A.retitle_from_work(sid, root, [{"path": deck.name}]) == "Ridgeway PdM Client Deck"
    assert A.read_meta(sid)["title"] == "Ridgeway PdM Client Deck"
    memo = root / "Memo_to_client.docx"
    memo.write_bytes(b"y")
    os.utime(memo, (time.time() + 5, time.time() + 5))
    assert A.retitle_from_work(sid, root, [{"path": deck.name}, {"path": memo.name}]) == "Memo to client"      # the newest deliverable
    A.thread_action(sid, {"action": "rename", "title": "My own name"})
    assert A.retitle_from_work(sid, root, [{"path": deck.name}]) == "" and A.read_meta(sid)["title"] == "My own name"
    assert A.retitle_from_work(sid, root, []) == ""


# ------------------------------------------------------------------------------------------------------ item 14, 15
def test_your_work_lists_each_file_once_newest_first_and_the_usage_is_behind_a_fold(app, page):
    """Your work was a usage dashboard and "Things you made" repeated duplicates. The files come first, one entry each,
    clickable, with their folder; the usage numbers are behind a fold."""
    gal = [{"name": "Deck.pptx", "path": "out/Deck.pptx", "root": app["work"], "type": "Slides", "updated": 2000, "title": "t"},
           {"name": "Memo.docx", "path": "Memo.docx", "root": app["work"], "type": "Documents", "updated": 1000, "title": "t"}]
    data = {"empty": False, "range": 3, "totals": {"chats": 3, "turns": 5, "deliverables": 2}, "usage": {"tokens": 10, "measured_turns": 1, "note": "n"},
            "topics": [], "kinds": [{"label": "Coding", "count": 3}], "agents": [], "models": [], "when": {"hour": [0] * 24, "busiest_hour": None, "busiest_weekday": ""},
            "gallery": gal, "skills": [], "connectors": [], "taught": [], "suggestions": [], "privacy": "p"}
    page.route(re.compile(r".*/api/work\?.*"), lambda r: r.fulfill(json=data))
    page.evaluate("() => goPage('work')")
    page.wait_for_selector(".mw-file")
    got = page.evaluate("""() => ({files: [...document.querySelectorAll('.mw-file')].map(b => b.querySelector('b').textContent), tag: document.querySelector('.mw-file').tagName,
        first: document.querySelector('.mw-sec h2').textContent, usageOpen: document.querySelector('.mw-usage').open, dir: document.querySelector('.mw-file .mw-dir').textContent})""")
    assert got["files"] == ["Deck.pptx", "Memo.docx"] and got["tag"] == "BUTTON" and got["first"] == "Your files" and not got["usageOpen"] and got["dir"] == "in out", got
    page.click(".mw-file >> nth=1")
    page.wait_for_selector("#pvp:not([hidden])")
    assert page.evaluate("() => PVX.path") == "Memo.docx"


def test_artifacts_keep_one_entry_per_path_with_folder_and_date(app, page):
    """Three identical "Ridgeway_PdM_Client_Deck.pptx" cards, generic icons: one entry per path, with its folder and date."""
    w = Path(app["work"])
    (w / "decks").mkdir(exist_ok=True)
    (w / "decks" / "Ridgeway_Deck.pptx").write_bytes(b"PK")
    row = lambda i: {"id": f"i{i}", "name": "Ridgeway_Deck.pptx", "path": "decks/Ridgeway_Deck.pptx", "root": str(w), "ext": "pptx", "type": "Slides",
                     "tint": "orange", "size": 2, "updated": 1700000000 + i, "session_id": f"s{i}", "session_title": "deck", "project_id": "", "kind": "code", "mime": ""}
    from meshpool_harness.ui import hub
    import meshpool_harness.ui.agent_api as A
    # server side: the same path from three chats is one artifact
    old = (A.listing, hub._session_files)
    A.listing = lambda: [{"id": f"s{i}", "workdir": str(w), "title": "deck", "kind": "code", "project_id": ""} for i in range(3)]
    hub._session_files = lambda r: [{"path": "decks/Ridgeway_Deck.pptx", "ext": "pptx", "size": 2}]
    try:
        assert len(hub.artifacts()["artifacts"]) == 1
    finally:
        A.listing, hub._session_files = old
    page.route(re.compile(r".*/api/hub/artifacts$"), lambda r: r.fulfill(json={"artifacts": [row(0)], "types": {"Slides": 1}}))
    page.evaluate("() => { G.art = null; goPage('artifacts'); }")
    page.wait_for_selector(".gcard.art")
    got = page.evaluate("() => ({n: document.querySelectorAll('.gcard.art').length, where: document.querySelector('.gwhere')?.textContent, meta: document.querySelector('.gcard.art .gm small').textContent})")
    assert got["n"] == 1 and got["where"] == "in decks" and "Slides" in got["meta"], got
