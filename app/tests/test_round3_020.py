"""Round 3 of the persona runs (0.0.20): what Priya, Lina and Maya still hit in the web app. Each test drives the real app in
Chromium (the fixtures of tests/test_ux018.py) or calls the server code, and failed before its fix. Skipped when no Chromium
can start here (outside CI)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_ux018 import _chats, _fake_chat, app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))

CHART_PY = "import matplotlib\nmatplotlib.use('Agg')\nimport pandas as pd\nprint('chart saved')\n"
SCRATCH = "/tmp/mh-scratch"
HEREDOC = f"S={SCRATCH}; mkdir -p $S; cat > $S/chart.py <<'EOF'\n{CHART_PY}EOF\npython3 $S/chart.py"
PYSTEP = f'cd "{SCRATCH}/maya/home/Maya\'s Flowers" && python3 -I -c "\nimport openpyxl\nwb=openpyxl.load_workbook(\'price list.xlsx\')\nprint(wb.sheetnames)\n"'


def _chat_view(page, workdir: str, cid: str = "t-r3"):
    """An open Chat-mode chat with no server behind it, in the given folder."""
    _fake_chat(page, cid)
    page.evaluate("""w => { S.cur.view = Object.assign(S.cur.view || {}, {code_mode: false, kind: 'cowork', workdir: w, state: 'idle'}); $('#col').replaceChildren(); S.cur.tools = {}; S.cur.group = null; S.cur.log = []; }""", workdir)


def _ev(page, *evs):
    page.evaluate("""evs => { for (const e of evs) { S.cur.log.push(e); applyEv(e); } }""", list(evs))


# ======================================================================================================== item 1
MD = ("- **Revenue:** Revenue is fairly flat through the year, with no strong seasonal pattern.\n- **Source of the figures:** "
      "They are summed from the `revenue` column as it appears in the file, which has about fifty thousand rows in it.")


def test_a_search_snippet_is_plain_text_that_starts_and_ends_at_a_word():
    """Snippets showed raw Markdown ("…:** Revenue is fairly flat… - **Source…") cut in the middle of a word."""
    from meshpool_harness.ui import agent_api as A
    s = A._plain_snippet("Intro line. " * 6 + MD, "seasonal", ["seasonal"])
    assert "*" not in s and "`" not in s and "- " not in s, s
    body = s.strip("…").strip()
    plain = A.plain_markdown("Intro line. " * 6 + MD)
    assert body in plain, (body, plain)
    i = plain.index(body)
    assert i == 0 or plain[i - 1] == " ", s                         # does not start inside a word
    j = i + len(body)
    assert j == len(plain) or plain[j] == " ", s                    # does not end inside a word
    assert "seasonal" in s


def test_search_finds_a_file_by_its_name_with_spaces_hyphens_and_underscores_alike(monkeypatch):
    """A search for "open house" did not list open-house.html."""
    from meshpool_harness.ui import hub as H
    rows = [{"id": "a1", "name": n, "path": n, "root": "/w", "type": "Web page", "session_id": "s", "session_title": "Chat",
             "ext": "html"} for n in ("open-house.html", "open_house_flyer.pdf", "Open House.docx", "annual-report.pdf")]
    monkeypatch.setattr(H, "artifacts", lambda limit=300, listing=None: {"artifacts": rows, "types": {}})
    for q, want in (("open house", {"open-house.html", "open_house_flyer.pdf", "Open House.docx"}),
                    ("open-house", {"open-house.html", "open_house_flyer.pdf", "Open House.docx"}),
                    ("open_house", {"open-house.html", "open_house_flyer.pdf", "Open House.docx"}),
                    ("annual report", {"annual-report.pdf"})):
        r = H.search(q, "artifacts")
        got = {i["title"] for g in r["groups"] for i in g["items"]}
        assert got == want, (q, got)


def test_search_opens_the_matching_message_with_the_word_marked_and_shows_the_date(app, page):
    ids = _chats(app, page, 1)
    page.set_viewport_size({"width": 1366, "height": 600})
    for _ in range(30):                                                              # the saved chat is searchable a moment after it ends
        if page.evaluate("async () => (await api('/api/hub/search?q=key%20points&tab=all')).groups.length > 0"):
            break
        page.wait_for_timeout(700)
    page.evaluate("() => { S.mode = 'chat'; openSearch('all', 'key points'); }")
    page.wait_for_selector("#srchList .sres .ssn")
    row =page.evaluate("""() => { const r = document.querySelector('#srchList .sres'); return {snip: r.querySelector('.ssn').textContent,
        date: (r.querySelector('.sdate') || {}).textContent || ''}; }""")
    assert not re.search(r"[*`#]|\|", row["snip"]), row
    assert re.match(r"^[A-Z][a-z]{2} \d{1,2}", row["date"]), row                   # the chat's date: "Nov 26"
    page.keyboard.press("Enter")
    page.wait_for_selector("#col .msg-ai")
    page.wait_for_function("() => document.querySelector('#col mark.findhit')", timeout=8000)
    page.wait_for_timeout(500)
    got = page.evaluate("""() => { const m = document.querySelector('#col mark.findhit'), c = document.querySelector('#conv'), a = m.getBoundingClientRect(), b = c.getBoundingClientRect();
        return {text: m.textContent.toLowerCase(), inView: a.top >= b.top && a.bottom <= b.bottom, atEnd: c.scrollHeight - c.scrollTop - c.clientHeight < 40}; }""")
    assert got["text"] == "key points" and got["inView"] and not got["atEnd"], got       # not at the bottom of the chat
    page.wait_for_function("() => !document.querySelector('#col mark.findhit')", timeout=8000)   # the mark is brief
    assert page.errors == [], page.errors


def test_the_empty_search_says_new_chat_and_not_new_session_in_chat_mode(app, page):
    page.evaluate("() => { S.mode = 'chat'; openSearch('all', ''); }")
    page.wait_for_selector("#srchList .sres:not(.sk)")
    labels = page.eval_on_selector_all("#srchList .sres .stt", "xs => xs.map(x => x.textContent)")
    assert "New chat" in labels and "New session" not in labels, labels
    page.keyboard.press("Escape")
    page.evaluate("() => { S.mode = 'code'; openSearch('all', ''); }")
    page.wait_for_selector("#srchList .sres:not(.sk)")
    labels = page.eval_on_selector_all("#srchList .sres .stt", "xs => xs.map(x => x.textContent)")
    assert "New session" in labels, labels                                           # Code mode keeps it
    page.keyboard.press("Escape")
    page.evaluate("() => { S.mode = 'chat'; }")


# ======================================================================================================== item 2
def test_an_outside_agents_step_titles_are_plain_words():
    """"Reading List files in 'rooms-summary.xlsx'" four times: Codex's own title was put after the word Reading."""
    from meshpool_harness.agent.core import plain_step
    p = plain_step("read_file", "List files in 'rooms-summary.xlsx'")
    assert p and p["done"] == "Read rooms-summary.xlsx" and "List files" not in p["run"], p
    q = plain_step("grep", "Search for 'rooms' in the folder")
    assert q and q["done"] == "Looked through the files", q
    assert plain_step("read_file", "notes/a.txt") is None          # a plain path already reads well


def test_progress_collapses_repeats_ticks_everything_at_the_end_and_shows_every_step(app, page):
    _chat_view(page, app["work"])
    pl = lambda t: {"base": t, "run": t, "done": t}  # noqa: E731
    evs = [{"type": "user", "text": "go", "i": 0}]
    for i in range(4):          # the same step four times
        evs += [{"type": "tool_start", "id": f"r{i}", "name": "read_file", "summary": "x", "plain": pl("Read rooms-summary.xlsx")},
                {"type": "tool_end", "id": f"r{i}", "name": "read_file", "ok": True, "output": "ok"}]
    for t in ("Created open-house.html", "Ran a Python step"):      # an agent that numbers every round of calls from 1 again
        evs += [{"type": "tool_start", "id": "call_1", "name": "exec_command", "summary": "y", "plain": pl(t)},
                {"type": "tool_end", "id": "call_1", "name": "exec_command", "ok": True, "output": "ok"}]
    _ev(page, *evs)
    got = page.evaluate("() => progressSteps().items.map(i => i.text)")
    assert got == ["Read rooms-summary.xlsx (4 times)", "Created open-house.html", "Ran a Python step"], got
    # a plan with a step left pending: every step is ticked once the turn is over
    _ev(page, {"type": "plan", "entries": [{"content": "Read the file", "status": "completed"}, {"content": "Search the files", "status": "pending"}]},
        {"type": "turn_end"})
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#pgSteps li")
    assert page.eval_on_selector_all("#pgSteps li", "xs => xs.map(x => x.className)") == ["completed", "completed"]
    assert page.errors == [], page.errors


def test_progress_lists_each_file_once(app, page):
    _chat_view(page, app["work"])
    _ev(page, {"type": "user", "text": "go", "i": 0}, {"type": "tool_start", "id": "w", "name": "exec_command", "summary": "x"},
        {"type": "tool_end", "id": "w", "name": "exec_command", "ok": True, "output": f"Saved {app['work']}/sales_2025_clean.csv"})
    page.evaluate("""w => { S.cur.view.turns = [{n: 1, changed: [{path: w + '/sales_2025_clean.csv', status: 'added'}, {path: 'sales_2025_clean.csv', status: 'added'}]}]; }""", app["work"])
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#pgFiles li")
    got = page.evaluate("() => [...document.querySelectorAll('#pgFiles li')].map(li => li.innerText.trim())")
    assert got == ["sales_2025_clean.csv"], got            # one row, and its name is not printed twice


# ======================================================================================================== item 3 and 9
def test_the_approval_view_shows_the_file_under_its_name_and_no_scratch_path():
    from meshpool_harness.agent.core import approval_view
    v = approval_view("bash", "Write chart.py and run it (a Python step)?", HEREDOC)
    assert v["file"]["name"] == "chart.py" and v["file"]["run"] and "matplotlib" in v["file"]["text"], v
    assert v["sum"] == "Save chart.py in a temporary folder and run it"     # release review: says where it goes
    blob = json.dumps(v)
    assert "/tmp/" not in blob and "mkdir" not in blob and "<<" not in blob, blob
    w = approval_view("bash", "Run a Python step on price?", PYSTEP)
    assert "/tmp/" not in json.dumps(w) and w["sum"].startswith("Run a Python step"), w
    assert approval_view("edit", "Write a.py?", "diff") == {}


def _approval(page, kind, title, detail):
    from meshpool_harness.ui import agent_api as A
    ev = {"type": "approval", "id": "ap-1", "kind": kind, "title": title, "detail": detail, "scope": "", "folder": True, "folder_name": "Maya's Flowers",
          **A._approval_view(kind, title, detail)}
    page.evaluate("ev => applyEv(ev)", ev)
    page.wait_for_selector("#appr-ap-1")
    return page.evaluate("""() => { const d = document.querySelector('#appr-ap-1'), det = d.querySelector('details.apdet'), c = d.cloneNode(true);
        c.querySelectorAll('details, .acts').forEach(x => x.remove());
        return {body: c.innerText, details: det ? det.querySelector('pre').textContent : null, file: d.querySelector('.apfile pre') ? d.querySelector('.apfile pre').textContent : null,
                fn: d.querySelector('.apfn') ? d.querySelector('.apfn').textContent : null}; }""")


def test_a_file_write_card_shows_the_script_under_its_name_and_folds_the_command(app, page):
    _chat_view(page, app["work"])
    r = _approval(page, "bash", "Write chart.py and run it (a Python step)?", HEREDOC)
    assert r["fn"] == "chart.py" and "import matplotlib" in r["file"], r
    assert "/tmp/" not in r["body"] and "mkdir" not in r["body"] and "<<'EOF'" not in r["body"], r["body"]
    assert "Save chart.py in" in r["body"], r["body"]
    assert SCRATCH in r["details"] and "cat >" in r["details"], r["details"]            # the exact command stays in Details


def test_a_python_step_card_reads_in_one_plain_line_and_folds_the_code(app, page):
    _chat_view(page, app["work"])
    r = _approval(page, "bash", "Run a Python step on price?", PYSTEP)
    assert "/tmp/" not in r["body"] and "openpyxl" not in r["body"], r["body"]
    assert r["body"].count("\n") <= 3 and "Run a Python step" in r["body"], r["body"]
    assert "openpyxl" in r["details"] and SCRATCH in r["details"]


# ======================================================================================================== item 4
def test_a_picture_shown_large_is_not_also_a_card(app, page):
    _chat_view(page, app["work"])
    _ev(page, {"type": "user", "text": "Make a chart of revenue and show it here", "i": 0})
    page.evaluate("""() => { S.cur.tpaths = ['chart.png']; applyEv({type: 'turn_end', turn: {}}); }""")
    got = page.evaluate("""() => { const row = document.querySelector('#col .pvcards'); return {big: document.querySelectorAll('#col .pvbig').length,
        cards: row ? [...row.querySelectorAll('.pvcard')].filter(c => c.offsetParent).length : 0}; }""")
    assert got["big"] == 1 and got["cards"] == 0, got
    # a file that is not a picture keeps its card
    page.evaluate("""() => { S.cur.tpaths = ['chart.png', 'report.docx']; pvTurnCards(S.cur, S.cur.lastFoot, 0); }""")
    got = page.evaluate("""() => [...document.querySelectorAll('#col .pvcards .pvcard')].map(c => c.dataset.pv)""")
    assert got == ["report.docx"], got


# ======================================================================================================== item 5
def _box(page, sel):
    return page.evaluate("s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.left, w: r.width, v: !!e.offsetParent}; }", sel)


def test_the_sidebar_stays_beside_progress_and_the_preview_at_1366_with_one_width(app, page):
    ids = _chats(app, page, 1)
    page.set_viewport_size({"width": 1366, "height": 768})
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_timeout(1200)
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_timeout(500)
    prog = _box(page, "#panel")
    assert not page.evaluate("() => document.querySelector('#app').classList.contains('autoside')"), "the sidebar folded"
    assert _box(page, "#side")["v"] and _box(page, "#newThread")["v"]
    assert page.evaluate("() => document.querySelector('#col').offsetWidth") >= 656
    page.evaluate("() => pvShell('notes.txt', 'Text')")
    page.wait_for_timeout(600)
    pv = _box(page, "#pvp")
    assert not page.evaluate("() => document.querySelector('#app').classList.contains('autoside')"), "the sidebar folded for the preview"
    assert abs(pv["w"] - prog["w"]) <= 8, (prog, pv)                           # one width rule: 437 and 628 before
    assert page.evaluate("() => document.querySelector('#col').offsetWidth") >= 656


def test_the_preview_is_wider_than_progress_only_when_the_chat_keeps_its_column(app, page):
    ids = _chats(app, page, 1)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_timeout(1000)
    page.evaluate("() => pvShell('notes.txt', 'Text')")
    page.wait_for_timeout(600)
    room = page.evaluate("() => document.querySelector('#main').offsetWidth")
    assert room >= 672, room                                                   # the chat keeps its column beside the preview


@pytest.mark.parametrize("width", [1366, 1280])
def test_after_a_reload_with_a_panel_open_new_is_still_reachable(app, page, width):
    page.set_viewport_size({"width": width, "height": 768})
    page.evaluate("() => { localStorage.setItem('mp_panel', JSON.stringify('progress')); }")
    page.reload()
    page.wait_for_selector("#newThread", state="attached", timeout=30000)
    page.wait_for_function("() => typeof S === 'object' && S.threads", timeout=30000)
    page.wait_for_timeout(600)
    assert page.evaluate("() => { const b = document.querySelector('#newThread'); return !!b && b.offsetParent !== null && b.getBoundingClientRect().left >= 0; }"), "New is out of reach"
    assert not page.evaluate("() => document.querySelector('#app').classList.contains('autoside')")


# ======================================================================================================== item 6
def test_the_composer_does_not_move_when_a_message_is_sent(app, page):
    page.set_viewport_size({"width": 1366, "height": 768})
    page.evaluate("() => newThread()")
    page.wait_for_timeout(400)
    page.fill("#input", "stream long please. " + "A longer message that takes three lines in the box. " * 4)
    page.wait_for_timeout(400)
    page.evaluate("""() => { window.__tops = []; const c = document.querySelector('#composer'), t0 = performance.now();
        const f = () => { __tops.push(Math.round(c.getBoundingClientRect().top)); if (performance.now() - t0 < 900) requestAnimationFrame(f); }; requestAnimationFrame(f); }""")
    before = page.evaluate("() => Math.round(document.querySelector('#composer').getBoundingClientRect().top)")
    page.click("#sendBtn")
    page.wait_for_timeout(1000)
    tops = page.evaluate("() => __tops")
    assert max(abs(t - before) for t in tops) <= 1, (before, sorted(set(tops)))     # it dropped 27 px before


def test_the_footer_keeps_its_height_and_a_line_under_the_reply_does_not_push_the_card(app, page):
    _chat_view(page, app["work"])
    _ev(page, {"type": "user", "text": "make the report", "i": 0})
    page.evaluate("""() => { S.cur.tpaths = ['report.docx']; applyEv({type: 'turn_end', turn: {}}); }""")
    assert page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('#col .turnfoot')).minHeight)") >= 28
    page.evaluate("() => { S.pinned = false; document.querySelector('#conv').scrollTop = 0; }")      # a reader who is not following the end
    top = lambda: page.evaluate("() => Math.round(document.querySelector('#col .pvcard').getBoundingClientRect().top)")  # noqa: E731
    before = top()
    page.evaluate("() => applyEv({type: 'task_receipt', secs: 62, cost: 0.004, model: 'm'})")
    page.evaluate("() => applyEv({type: 'checks_line', text: 'Checked that the file opens cleanly.', items: []})")
    page.wait_for_timeout(100)
    assert top() == before, (before, top())
    order = page.evaluate("() => [...document.querySelector('#col').children].map(c => c.className.split(' ')[0])")
    assert order.index("pvcards") < order.index("after010"), order


def test_the_status_line_does_not_keep_checking_it_as_after_the_turn(app, page):
    _chat_view(page, app["work"])
    _ev(page, {"type": "user", "text": "go", "i": 0})
    page.evaluate("() => applyEv({type: 'status', look: true, persona: 'a guest on a phone', text: 'Checking it as: a guest on a phone...'})")
    assert page.evaluate("() => /^Checking it as/.test(S.cur.activity || '')")
    page.evaluate("() => applyEv({type: 'turn_end', turn: {}})")
    assert page.evaluate("() => S.cur.activity") == ""


# ======================================================================================================== item 7
def test_chat_mode_puts_the_agent_choice_under_advanced_as_other_agents(app, page):
    page.evaluate("() => { S.mode = 'chat'; newThread(); }")
    page.wait_for_timeout(300)
    page.click("#mPill")
    page.wait_for_selector("#ctx")
    txt = page.inner_text("#ctx")
    assert "Advanced" in txt and "Other agents" in txt and "Harness" not in txt, txt
    assert txt.index("Advanced") < txt.index("Other agents")
    page.keyboard.press("Escape")
    page.evaluate("() => { S.mode = 'code'; newThread(); }")
    page.wait_for_timeout(300)
    page.click("#mPill")
    page.wait_for_selector("#ctx")
    assert "Harness" in page.inner_text("#ctx")                                # Code mode is unchanged
    page.keyboard.press("Escape")
    page.evaluate("() => { S.mode = 'chat'; newThread(); }")


# ======================================================================================================== item 8
def test_paths_inside_home_read_relative_to_home(app, page):
    got = page.evaluate("""() => { const h = S.settings.user_home; return [homePath(h), homePath(h + '/Documents/Maya'), homePath('/srv/data'), h]; }""")
    assert got[:3] == ["Home", "Home/Documents/Maya", "/srv/data"], got


def test_the_folder_picker_starts_its_trail_at_home(app, page):
    page.evaluate("() => { window.__fb = browseFolder(S.settings.user_home); }")
    page.wait_for_selector("#fbCrumbs button")
    crumbs = page.eval_on_selector_all("#fbCrumbs button", "xs => xs.map(x => x.textContent)")
    assert crumbs == ["Home"], crumbs                                         # "rescore3 > priya > Home" before
    page.keyboard.press("Escape")


def test_permissions_shows_folders_relative_to_home(app, page):
    import os
    home = page.evaluate("() => S.settings.user_home")
    d = os.path.join(home, "Documents", "Maya Flowers")
    os.makedirs(d, exist_ok=True)
    page.evaluate("""async d => { await post('/api/projects', {action: 'add', path: d}); await loadProjects(); openSettings('permissions'); }""", d)
    page.wait_for_selector("#addP")
    txt = page.evaluate("() => [...document.querySelectorAll('#mbody .rows2 small')].map(s => s.textContent)")
    assert "Home/Documents/Maya Flowers" in txt, txt
    assert not any(t.startswith(home) for t in txt), txt


# ======================================================================================================== items 10 to 13
def test_a_preview_does_not_follow_you_into_a_new_chat_and_attaching_returns_focus(app, page):
    _chat_view(page, app["work"])
    page.evaluate("() => pvShell('notes.txt', 'Text')")
    page.wait_for_function("() => PVX.open")
    page.evaluate("() => newThread()")
    page.wait_for_timeout(500)
    assert page.evaluate("() => !PVX.open && document.querySelector('#pvp').hidden")
    page.evaluate("() => { document.querySelector('#attBtn').focus(); }")
    page.evaluate("""async () => { await addAttachments([new File(['a,b\\n1,2'], 'x.csv', {type: 'text/csv'})]); }""")
    assert page.evaluate("() => document.activeElement && document.activeElement.id") == "input"


def test_the_new_folder_row_does_not_wrap_and_its_placeholder_is_short(app, page):
    page.set_viewport_size({"width": 1366, "height": 768})
    page.evaluate("() => { window.__fb = browseFolder(S.settings.user_home); }")
    page.wait_for_selector("#fbNew")
    page.click("#fbNew")
    got = page.evaluate("""() => { const r = document.querySelector('.dlg.fb .row'), kids = [...r.children].filter(c => c.offsetParent), i = document.querySelector('#fbNewIn');
        return {tops: new Set(kids.map(c => Math.round(c.getBoundingClientRect().top))).size, ph: i.placeholder, cut: i.scrollWidth > i.clientWidth,
                wrap: kids.filter(c => c.tagName === 'BUTTON').some(b => b.getBoundingClientRect().height > 44)}; }""")
    assert got["tops"] == 1 and got["ph"] == "Folder name" and not got["wrap"], got
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")


def test_the_import_reads_in_plain_words_and_loading_text_waits_300_ms(app, page):
    page.evaluate("""() => { window.__box = document.createElement('div'); document.body.appendChild(__box); const o = api; window.api = async p => { await new Promise(r => setTimeout(r, 120)); return {agents: []}; }; window.__api = o; }""")
    seen = page.evaluate("""async () => { const box = __box; const hit = []; const mo = new MutationObserver(() => hit.push(box.textContent)); mo.observe(box, {childList: true, subtree: true, characterData: true});
        await importPanel(box, () => {}); mo.disconnect(); window.api = __api; return hit; }""")
    assert not any("Looking" in t for t in seen), seen                           # a quick answer shows no loading text
    src = (REPO / "app" / "meshpool_harness" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "Bring in chats from ChatGPT or Claude (export file)" in src and "Drop the .zip here" not in src


def test_toasts_sit_clear_of_the_preview_bar(app, page):
    page.evaluate("() => pvShell('notes.txt', 'Text')")
    page.wait_for_function("() => PVX.open")
    page.evaluate("() => toast('Downloaded report.docx')")
    top = page.evaluate("() => { const t = document.querySelector('#toasts'); return [t.classList.contains('low'), Math.round(t.getBoundingClientRect().top)]; }")
    assert top[0] and top[1] > 300, top
    page.evaluate("() => closePreview(true)")


def test_visible_server_strings_use_the_lowercase_name():
    ui = REPO / "app" / "meshpool_harness" / "ui"
    for f, bad in (("files_api.py", ("Meshpool keeps that", "Meshpool could not save")), ("previews.py", ("Meshpool is not allowed",)),
                   ("workbench.py", ("Meshpool cannot read", "Meshpool can't save", "let Meshpool read")), ("hub.py", ("Meshpool key",))):
        text = (ui / f).read_text(encoding="utf-8")
        for b in bad:
            assert b not in text, (f, b)


def test_a_plain_request_is_not_taken_for_a_standing_preference():
    """"Noted for next time" showed after a plain email request ("don't make it too formal", "make sure it has a date")."""
    from meshpool_harness import learn
    for t in ("Write a short email to Sam about Friday. Don't make it too formal, and make sure it has the date.",
              "Draft a reply, I like the second option, but it is too long."):
        assert not learn.worth_a_look(t), t
    assert learn.worth_a_look("From now on never use em dashes.")
