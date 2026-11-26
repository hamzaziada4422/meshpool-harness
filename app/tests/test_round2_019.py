"""Round 2 of the persona runs (0.0.19): what Maya and Priya still hit in the web app. Each test drives the real app in
Chromium (the fixtures of tests/test_ux018.py) or calls the server code directly, and failed before its fix. Skipped
when no Chromium can start here (outside CI)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.test_ux018 import _fake_chat, app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))


def _chat(page, workdir: str, kind: str | None = None, task: str = "hello") -> str:
    return page.evaluate("""async ([w, k, t]) => { const v = await post('/api/agent', Object.assign({workdir: w, task: t}, k ? {kind: k} : {}));
        await loadThreads(); openThread(v.id); return v.id; }""", [workdir, kind, task])


def _step(page, i, name, summary, ok=True, mcp=False, output="ok"):
    """One step as the server's view() sends it (with its plain title), drawn into the open chat."""
    page.evaluate("""async ([i, name, summary, ok, mcp, output]) => {
        const pl = await post('/api/_plain_probe', {name, summary, mcp}).catch(() => null);
        applyEv({type: 'tool_start', id: 'x' + i, name, summary, mcp, plain: pl && pl.plain || undefined});
        applyEv({type: 'tool_end', id: 'x' + i, name, ok, output}); }""", [i, name, summary, ok, mcp, output])


# ------------------------------------------------------------------------------------------------------ item 1
HEREDOC = "cat > open-house.html <<'EOF'\n<!DOCTYPE html>\n<html><body><h1>Open house</h1></body></html>\nEOF"
CASES = [
    ("exec_command", "ls -la", False, "Looked at the files in the folder"),
    ("exec_command", "find . -maxdepth 2 -type f", False, "Looked at the files in the folder"),
    ("exec_command", HEREDOC, False, "Created open-house.html"),
    ("exec_command", "sed -i 's/a/b/' open-house.html", False, "Changed open-house.html"),
    ("mcp__meshpool_office__check_document", "Use the office tool check document on client-update.docx", True, "Checked client-update.docx"),
    ("mcp__meshpool_office__make_document", "Make a Word document, client-update.docx", True, "Made client-update.docx"),
    ("mcp__meshpool_office__read_document", '{"path": "/tmp/mh-scratch/x/client-update.docx"}', True, "Read client-update.docx"),
]


@pytest.mark.parametrize("name,summary,mcp,want", CASES)
def test_plain_step_titles_come_from_the_server(name, summary, mcp, want):
    """The step rows read "Ran a command: ls -la", a whole heredoc and "Used a connector /tmp/...". The server gives each
    step the plain title the approval cards use, and no title holds file text."""
    from meshpool_harness.agent.core import plain_step
    p = plain_step(name, summary, mcp)
    assert p and p["done"] == want, p
    assert "<" not in p["done"] and "\n" not in p["done"] and "EOF" not in p["done"]
    assert p["run"] and p["base"] and p["run"] != p["done"]


def test_the_view_carries_the_plain_title():
    from meshpool_harness.ui import agent_api as A
    e = A._with_plain({"type": "tool_start", "id": "1", "name": "exec_command", "summary": HEREDOC, "mcp": False})
    assert e["plain"]["done"] == "Created open-house.html", e


def test_chat_mode_rows_are_plain_and_code_mode_is_unchanged(app, page):
    _chat(page, app["work"], None, "plain steps")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    page.evaluate("""() => { S.cur.view = Object.assign(S.cur.view || {}, {code_mode: false, kind: 'cowork'}); }""")
    from meshpool_harness.agent.core import plain_step
    import json
    for i, (name, summary, mcp, _w) in enumerate(CASES):
        pl = plain_step(name, summary, mcp)
        page.evaluate("""([i, name, summary, mcp, pl]) => {
            for (const e of [{type: 'tool_start', id: 'x' + i, name, summary, mcp, plain: pl}, {type: 'tool_end', id: 'x' + i, name, ok: true, output: 'ok'}]) { S.cur.log.push(e); applyEv(e); } }""", [i, name, summary, mcp, pl])
    rows = page.evaluate("() => [...document.querySelectorAll('#col .tool')].map(t => [t.querySelector('.tn').textContent, t.querySelector('.ts').textContent])")
    assert [r[0] for r in rows] == [c[3] for c in CASES], rows
    assert all(r[1] == "" for r in rows), rows
    # the raw command stays in the details
    assert "<!DOCTYPE html>" in page.evaluate("() => document.querySelectorAll('#col .tool')[2].querySelector('pre.script').textContent")
    # the Progress list reads the same way
    steps = page.evaluate("() => progressSteps().items.map(i => i.text)")
    want = [c[3] for c in CASES]
    want = [want[0] + " (2 times)"] + want[2:]                      # the same step twice is one row (round 3, item 2)
    assert steps == want, steps
    # a failed connector step is not "Connector step ."
    page.evaluate("""() => { const pl = {base: 'Connector step', run: 'Using a connector', done: 'Used a connector'};
        applyEv({type: 'tool_start', id: 'y', name: 'mcp__foo__bar', summary: '/tmp/mh-scratch/x', mcp: true, plain: pl});
        applyEv({type: 'tool_end', id: 'y', name: 'mcp__foo__bar', ok: false, output: 'ERROR nope'}); }""")
    last = page.evaluate("() => { const t = [...document.querySelectorAll('#col .tool')].pop(); return [t.querySelector('.tn').textContent, t.querySelector('.ts').textContent]; }")
    assert last == ["Connector step", ""], last
    # Code mode: the row keeps the command
    page.evaluate("""() => { S.cur.view.code_mode = true; S.cur.view.kind = 'code'; $('#col').replaceChildren(); S.cur.tools = {}; S.cur.group = null;
        applyEv({type: 'tool_start', id: 'c1', name: 'exec_command', summary: 'ls -la', mcp: false, plain: {base: 'Look', run: 'Looking', done: 'Looked at the files in the folder'}});
        applyEv({type: 'tool_end', id: 'c1', name: 'exec_command', ok: true, output: 'ok'}); }""")
    row = page.evaluate("() => [document.querySelector('#col .tool .tn').textContent, document.querySelector('#col .tool .ts').textContent]")
    assert row == ["Ran a command", "ls -la"], row
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 2
def _turn(page, fails_last: bool, reply: str):
    page.evaluate("""([failsLast, reply]) => { $('#col').replaceChildren(); S.cur.tools = {}; S.cur.group = null; S.cur.view.code_mode = false;
        applyEv({type: 'user', text: 'go', i: 0});
        const ok = [true, false, true];
        const mk = (id, good) => { applyEv({type: 'tool_start', id, name: 'exec_command', summary: 'ls ' + id, mcp: false});
                                   applyEv({type: 'tool_end', id, name: 'exec_command', ok: good, output: good ? 'ok' : 'ERROR x'}); };
        mk('a', true); mk('b', false); mk('c', !failsLast);
        applyEv({type: 'assistant', text: reply, streamed: false});
        applyEv({type: 'turn_end'}); }""", [fails_last, reply])
    return page.evaluate("() => document.querySelector('#col .work .wl').textContent")


def test_a_harmless_failed_step_is_not_counted_as_a_problem(app, page):
    """"3 steps, 1 had a problem" for a find that found nothing. Only a failure the turn ended with, or one the reply
    reports, is counted."""
    _chat(page, app["work"], None, "count steps")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    assert _turn(page, False, "All done. The folder has two files.") == "3 steps"
    assert _turn(page, True, "All done.") == "3 steps, 2 had a problem"      # the last two failed: the turn ended with them
    assert _turn(page, False, "I could not open the second file, so I skipped it.") == "3 steps, 1 had a problem"
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 3
@pytest.fixture()
def ckhome(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    yield tmp_path
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)


def test_totals_are_named_only_for_a_workbook_that_has_them(ckhome):
    from meshpool_harness import checks as K
    for run, totals, want in (("a", False, "Checked the formulas, and that the file opens cleanly."),
                              ("b", True, "Checked the totals and formulas, and that the file opens cleanly.")):
        K.begin(run)
        K.passed("office_structure", "the file opens and reads correctly", None, "p.xlsx")
        K.rules_ran("workbook_rules", "workbook rules", {"X1": "pass", "X2": "pass"}, "p.xlsx")
        K.fact("workbook_totals", totals, "p.xlsx")
        K.fact("workbook_formulas", True, "p.xlsx")
        assert K.summary(run)["line"] == want
        K.end(run)


def test_the_real_validator_records_whether_a_sheet_has_totals(ckhome, tmp_path):
    from engines.forge import validate_xlsx as VX
    from meshpool_harness import checks as K
    from openpyxl import Workbook
    for name, rows in (("flat.xlsx", [["Item", "Price"], ["Rose", 4], ["Tulip", 3]]),
                       ("sum.xlsx", [["Item", "Price"], ["Rose", 4], ["Total", "=SUM(B2:B2)"]])):
        wb = Workbook()
        for r in rows:
            wb.active.append(r)
        p = tmp_path / name
        wb.save(p)
        K.begin(name)
        VX.validate(str(p), do_recalc=False)
        line = K.summary(name)["line"]
        assert ("totals" in line) == (name == "sum.xlsx"), (name, line)
        K.end(name)


def test_a_failing_deck_rule_is_named_in_the_one_line(ckhome):
    from meshpool_harness import checks as K
    K.begin("d")
    K.passed("office_structure", "the file opens and reads correctly", None, "d.pptx")
    K.rules_ran("deck_rules", "design rules", {"P1": "pass", "P23": "fail"}, "d.pptx")
    K._detail("deck_rules", "d.pptx", ["Chart labels clear of the bars: Slide 1: a label sits on a bar of the chart: 'Target 47%'."])
    line = K.summary("d")["line"]
    assert line == "Checked that the file opens cleanly. One thing to look at: a chart label sits on a bar on slide 1.", line


def test_deck_problem_names_match_the_validators():
    from engines.forge.validate_pptx import RULE_NAMES
    from engines.forge.visual_check import NAMES
    from meshpool_harness import checks as K
    for rule in ("P0", "P1", "P5", "P8", "P14", "P20", "P23", "P24", "P25", "P26", "P27", "P28"):
        assert {**RULE_NAMES, **NAMES}[rule] in K._REAL_DECK, rule


def test_one_line_when_both_the_look_and_the_checks_ran():
    from meshpool_harness import checks as K
    m = K.merge_line("Checked as a florist shop owner: found nothing to fix.", "Checked the formulas, and that the file opens cleanly.")
    assert m == "Checked it the way a florist shop owner would, and the formulas, and that the file opens: all good.", m
    m = K.merge_line("Checked as someone opening this file: found nothing to fix.", "Checked that the file opens cleanly.")
    assert m == "Checked it, and that the file opens: all good.", m           # the persona adds nothing
    m = K.merge_line("Checked as a cook: fixed 2 things: a; b. Nothing left open.", "Checked the slide layout and text. One thing to look at: a title leaves one word alone on its last line on slide 2.")
    assert m.startswith("Checked it the way a cook would, and the slide layout and text: fixed 2 things: a; b. Nothing left open. One thing to look at:"), m
    assert K.merge_line("not a look line", "Checked it.") == ""


def test_an_also_noticed_line_is_never_about_our_own_check():
    from meshpool_harness.agent import lookreview as LR
    assert LR._also_line("the scripted 'look at date' check could not find that text by label, but the confirmation shows it") == ""
    assert LR._also_line("the footer link is a different colour from the rest") != ""
    st = {"evals": [{"problems": [], "also": LR._also_line("a selector for the date field did not match")}]}
    assert LR._also_offer(st) == ""


def test_the_page_drops_the_look_line_that_the_merged_line_replaced(app, page):
    _chat(page, app["work"], None, "merge")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    got = page.evaluate("""() => { $('#col').replaceChildren(); applyEv({type: 'user', text: 'go', i: 0});
        applyEv({type: 'assistant', text: 'Done. Prices are up 10%.', streamed: false});
        applyEv({type: 'assistant', text: 'Checked as a florist: found nothing to fix.', streamed: false, append: true, merged: true, look: true});
        applyEv({type: 'turn_end'});
        S.cur.lastFoot = document.querySelector('#col .msg-ai').insertAdjacentElement('afterend', Object.assign(document.createElement('div'), {className: 'foot'}));
        ev010({type: 'checks_line', text: 'Checked it the way a florist would, and the formulas: all good.', items: [], details: [], look: 'Checked as a florist: found nothing to fix.'});
        return [...document.querySelectorAll('#col .msg-ai')].map(x => x.textContent).join('|') + '#' + document.querySelectorAll('.chkline').length; }""")
    assert "Checked as" not in got and got.endswith("#1") and "Prices are up 10%" in got, got
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 4
def _root(page, folder: str):
    page.evaluate("""async w => { await post('/api/projects', {action: 'add', path: w});
        S.cur.view = Object.assign(S.cur.view || {}, {workdir: w, kind: 'cowork'}); }""", folder)


def _prices(path, factor):
    from openpyxl import Workbook
    wb = Workbook()
    a = wb.active
    a.title = "Bouquets"
    a.append(["Bouquet", "Price"])
    for i in range(1, 80):
        a.append([f"Bouquet {i}", round(10 * i * factor, 2)])
    b = wb.create_sheet("Notes")
    b.append(["Note"])
    for i in range(1, 80):
        b.append([f"Price {i}: {round(10 * i * factor, 2)}"])
    wb.save(path)


def test_an_open_spreadsheet_preview_refreshes_after_an_edit_and_keeps_its_place(app, page):
    """A spreadsheet preview kept the old prices after a 10% rise until the file card was clicked again. When a turn's
    checkpoint names the open file the preview reads it again, on the same sheet tab and at the same scroll."""
    path = Path(app["work"]) / "Bouquet Price List.xlsx"
    _prices(path, 1.0)
    _fake_chat(page, "t-pvrefresh")
    _root(page, app["work"])
    page.evaluate("w => openPreview(w, 'Bouquet Price List.xlsx')", app["work"])
    page.wait_for_selector(".pv-sheet table")
    page.click("#pvTabs [role=tab]:nth-child(2)")
    page.wait_for_function("() => document.querySelector('.pv-sheet td') && document.querySelector('.pv-sheet').textContent.includes('Price 3:')")
    page.evaluate("() => { document.querySelector('.pv-sheet').scrollTop = 300; }")
    before = page.evaluate("() => document.querySelector('.pv-sheet').scrollTop")
    assert before > 100
    _prices(path, 1.1)                                            # the turn raised the prices by 10%
    page.evaluate("""() => applyEv({type: "checkpoint", i: 5, turns: [{n: 1, changed: [{path: "Bouquet Price List.xlsx", status: "modified"}]}]})""")
    page.wait_for_function("() => document.querySelector('.pv-sheet').textContent.includes('Price 3: 33')", timeout=10000)
    got = page.evaluate("""() => ({tab: [...document.querySelectorAll('#pvTabs [role=tab]')].findIndex(b => b.getAttribute('aria-selected') === 'true'),
        top: document.querySelector('.pv-sheet').scrollTop})""")
    assert got["tab"] == 1 and abs(got["top"] - before) <= 2, (got, before)
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 5
def test_file_cards_show_the_name_in_two_lines_with_the_extension_and_a_type_icon(app, page):
    """"Bouquet Price Li..." with one generic icon. A name may take two lines, ends in an ellipsis only when it is longer
    than that, keeps its extension, and each type of file has its own icon."""
    names = ["Bouquet Price List.xlsx", "Spring wedding flowers proposal for the Hargreaves family and guests.docx",
             "pitch.pptx", "open-house.html", "photo.png", "notes.pdf"]
    for n in names:
        (Path(app["work"]) / n).write_bytes(b"x")
    _chat(page, app["work"], None, "cards")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    _root(page, app["work"])
    page.evaluate("""n => { applyEv({type: 'user', text: 'make files', i: 0});
        const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = n; pvTurnCards(S.cur, foot, 0); }""", names)
    page.wait_for_selector(".pvcard")
    got = page.evaluate("""() => [...document.querySelectorAll('.pvcard')].map(c => { const b = c.querySelector('b'), r = document.createRange(); r.selectNodeContents(b);
        const lh = parseFloat(getComputedStyle(b).lineHeight) || 18;
        return {text: b.textContent, icon: (c.querySelector('.pvi svg') || {innerHTML: 'img'}).innerHTML, lines: Math.round(r.getBoundingClientRect().height / lh), clipped: b.scrollHeight > b.clientHeight + 1,
                title: b.title}; })""")
    by = {g["title"]: g for g in got}
    first = by["Bouquet Price List.xlsx"]
    assert first["text"] == "Bouquet Price List.xlsx" and not first["clipped"], first
    long = by[names[1]]
    assert long["text"].endswith(".docx") and "…" in long["text"] and not long["clipped"] and long["lines"] <= 2, long
    icons = [by[n]["icon"] for n in ("Bouquet Price List.xlsx", names[1], "pitch.pptx", "open-house.html", "photo.png", "notes.pdf")]
    assert len(set(icons[:4])) == 4, "sheet, doc, deck and web page each have their own icon"
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 6
def test_chat_mode_shows_no_engine_names_but_code_mode_keeps_them(app, page):
    """"Codex on DeepSeek V4.1 Flash, chosen for documents" in the footer, "Using Codex / Use meshpool Code instead" in the
    thread and the model name on the home screen. Chat mode shows the model in the picker's list and in the receipt fold
    only. Code mode is unchanged."""
    _chat(page, app["work"], None, "no engines")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    page.evaluate("() => { S.cur.view = Object.assign(S.cur.view || {}, {code_mode: false, kind: 'cowork'}); }")
    got = page.evaluate("""() => { $('#col').replaceChildren(); applyEv({type: 'user', text: 'go', i: 0});
        applyEv({type: 'status', using: true, text: 'Using Codex', back: {name: 'meshpool Code', harness: 'meshpool'}});
        applyEv({type: 'assistant', text: 'Done.', streamed: false}); applyEv({type: 'turn_end'});
        S.cur.lastFoot = document.querySelector('#col .msg-ai').insertAdjacentElement('afterend', Object.assign(document.createElement('div'), {className: 'foot'}));
        ev010({type: 'task_receipt', text: '3 min 45 s · <$0.01 · Codex on DeepSeek V4.1 Flash, chosen for documents', secs: 225, cost: 0.004,
               harness_name: 'Codex', model: 'deepseek-v4.1-flash', reason: 'docs words (1)'});
        renderSel();
        return {col: document.querySelector('#col').textContent, pill: document.querySelector('#mPill .lbl').textContent,
                foot: document.querySelector('.rcpt010 summary').textContent, fold: document.querySelector('.rcfold div').textContent}; }""")
    assert "Codex" not in got["col"] and "Use meshpool Code" not in got["col"] and "chosen for" not in got["col"], got
    assert "DeepSeek" not in got["pill"] and got["pill"] == "Model", got
    assert got["foot"] == "3 min 45 s · <$0.01" and got["fold"] == "Model: DeepSeek V4.1 Flash", got
    page.evaluate("() => { S.cur.view.code_mode = true; S.cur.view.kind = 'code'; S.mode = 'code'; }")
    assert page.evaluate("() => { renderSel(); return document.querySelector('#mPill .lbl').textContent; }") != "Model"
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 7
def test_settings_keeps_technical_pages_under_advanced_and_rule_syntax_behind_advanced_rules(app, page):
    """"Models and providers", "API keys" and "Code" sat in the main list; Permissions showed "bash:git push*", "Always
    allow" and "Linux does not need extra permissions". The technical pages are under Advanced, the rule syntax is behind
    "Advanced rules", and the Linux line is gone."""
    page.evaluate("() => openSettings('permissions')")
    page.wait_for_selector("#mbody .advrules")
    main = page.evaluate("() => [...document.querySelectorAll('.mnav > .mgrp:not(.madv) [data-g]')].map(b => b.textContent.trim())")
    adv = page.evaluate("() => [...document.querySelectorAll('.mnav .madv [data-g], .mnav .madv .mext')].map(b => b.textContent.trim())")
    assert not {"Models and providers", "Code", "API keys"} & set(main), main
    assert {"Models and providers", "Code", "API keys"} <= set(adv), adv
    assert page.evaluate("() => document.querySelector('#madv').open") is False
    txt = page.inner_text("#mbody")
    for bad in ("bash:git push", "Always allow", "Never allow", "Linux does not need extra permissions", "Always ask"):
        assert bad not in txt, (bad, txt)
    assert "Advanced rules" in txt
    page.click(".advrules summary")
    page.wait_for_function("() => document.querySelector('.advrules').open")
    assert "bash:git push" in page.inner_text("#mbody")
    # a search for a technical page finds it under Advanced and opens the group
    page.fill("#mnavq", "api key")
    assert page.evaluate("() => document.querySelector('#madv').open && !document.querySelector('#madv [data-g=models]').hidden")
    assert page.errors == [], page.errors


def test_permissions_has_no_linux_line():
    from meshpool_harness.ui import desktop
    if sys.platform.startswith("linux"):
        assert desktop.permissions()["note"] == ""


# ------------------------------------------------------------------------------------------------------ item 8
def test_onboarding_marks_a_folder_choice_only_when_it_happened(app, page):
    """"Folders I choose" looked selected before anything was added, and stayed selected after a cancelled picker."""
    page.evaluate("() => { S.projects = []; S.pj = []; permStep(); }")
    page.wait_for_selector("#onbPerm [data-scope=pick]")
    state = "() => [...document.querySelectorAll('#onbPerm [data-scope]')].map(b => b.getAttribute('aria-checked'))"
    assert "true" not in page.evaluate(state), page.evaluate(state)
    page.evaluate("() => { window.pickProject = async () => null; }")          # the person closes the folder picker
    page.click("#onbPerm [data-scope=pick]")
    page.wait_for_function("() => document.querySelector('#oScope').textContent.includes('No folder added yet')")
    assert "true" not in page.evaluate(state), page.evaluate(state)
    page.click("#onbPerm [data-scope=later]")
    assert page.evaluate(state) == ["false", "false", "true"], page.evaluate(state)
    assert page.errors == [], page.errors


AGENTS = {"agents": [
    {"id": "claude", "name": "Claude Code", "credentials_not_read": [], "counts": {"skill": 1, "hook": 1}, "items": [
        {"key": "skill:claude:unslop", "kind": "skill", "name": "unslop", "preview": "Turns vibecoded UI into a designed interface."},
        {"key": "hook:claude:Stop:0", "kind": "hook", "name": "Stop: bash ~/.claude/stop.sh", "preview": "bash ~/.claude/stop.sh"}]},
    {"id": "hermes", "name": "Hermes", "credentials_not_read": [], "counts": {"instructions": 1}, "items": [
        {"key": "instructions:hermes:SOUL.md", "kind": "instructions", "name": "SOUL.md", "preview": "You are Hermes Agent. Be direct."}]}]}
CHATS = {"claude": {"name": "Claude Code", "found": 12, "imported": 0, "default": True}}


def test_onboarding_step_two_uses_plain_labels_and_one_control_for_past_chats(app, page):
    """Step two read "Skill", "Instructions" and "SOUL.md", and had both an Import button and a tick box for the same chats."""
    import json
    import re

    def fake(route, request):
        url = request.url
        body = AGENTS if "import-agents" in url else CHATS if "import-chats" in url else {"prompt": "x", "links": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    page.route(re.compile(r"/api/import-(agents|chats|context)"), fake)
    page.evaluate("() => { S.projects = []; permStep(); }")
    page.wait_for_selector("#onbPerm")
    page.evaluate("() => setupStep(document.querySelector('#onb'), document.querySelector('#onbPerm'))")
    page.wait_for_selector("#onbSetup .impag")
    page.wait_for_function("() => !document.querySelector('#oLocalRow').hidden && document.querySelector('#oContext h3')")
    txt = page.inner_text("#onbSetup")
    for bad in ("SOUL.md", "Instructions", "Stop: bash", "Hook", "Skill\n"):
        assert bad not in txt, (bad, txt)
    assert "Notes from Hermes" in txt and "Saved skill" in txt and "unslop" in txt, txt
    assert page.locator("#onbSetup [data-local]").count() == 0, "an Import button next to the tick box"
    assert page.locator("#oLocal").count() == 1
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 9
def test_search_shows_a_snippet_and_no_developer_key_hints_for_chat_users(app, page):
    """Results had no snippet (the one line was hidden on the marked row), and the footer showed "Compose Tab" and
    "Actions Alt+Enter" to people who never use Code."""
    cid = page.evaluate("""async w => { const v = await post('/api/agent', {workdir: w, harness: 'meshpool-work', kind: 'cowork', mode: 'auto', task: 'Order notes'});
        await post('/api/agent/' + v.id + '/send', {text: 'Please find the brass lantern invoice from March and tell me what it says'}); return v.id; }""", app["work"])
    page.wait_for_function("""id => api('/api/agent/' + id + '?since=0').then(v => !['starting', 'working', 'waiting'].includes(v.state))""", arg=cid, timeout=60000, polling=500)
    page.evaluate("() => loadThreads()")
    page.evaluate("() => openSearch('all', '')")
    page.wait_for_selector("#srchIn")
    page.fill("#srchIn", "brass lantern")
    page.wait_for_selector("#srchList .ssn", timeout=15000)
    got = page.evaluate("""() => ({snip: document.querySelector('#srchList .ssn').textContent, marked: [...document.querySelectorAll('#srchList .ssn mark')].map(m => m.textContent.toLowerCase()),
        act: document.querySelector('#srchList .sres.act') === document.querySelector('#srchList .ssn').closest('.sres'), keys: document.querySelector('.srch-keys').textContent})""")
    assert "brass lantern" in got["snip"].lower() and "brass" in got["marked"] and "lantern" in got["marked"], got
    assert got["act"], "the snippet stays on the marked row"
    assert "Compose" not in got["keys"] and "Actions" not in got["keys"] and "Alt" not in got["keys"], got
    assert "Open" in got["keys"] and "Close" in got["keys"]
    # a Code user keeps the hints
    page.evaluate("() => { closeSearch(); S.mode = 'code'; openSearch('all', ''); }")
    page.wait_for_selector(".srch-keys")
    assert "Compose" in page.inner_text(".srch-keys")
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 10
def test_undo_details_hold_nothing_technical():
    from meshpool_harness.ui import agent_api as A
    msg, detail = A.undo_message({"restored": ["a.xlsx"], "outside": ["/home/maya/Documents/other/sales.csv"],
                                  "backup": "/tmp/x/sessions/s1/checkpoints/undone/20261126-103934"})
    assert "checkpoints" not in detail and "/tmp" not in detail and "/home" not in detail and "undone/" not in detail, detail
    assert "sales.csv" in detail and "kept a copy" in detail, detail


def test_the_page_drops_a_checkpoint_path_from_undo_details_even_in_a_saved_chat(app, page):
    _chat(page, app["work"], None, "undo detail")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    page.evaluate("""() => applyEv({type: "status", i: 0, text: "Undone. Restored 1 file (deck.pptx).",
        detail: "Copies of the files Undo replaced are in /tmp/x/checkpoints/undone/20261126-104217\\nA second line stays."})""")
    html = page.evaluate("() => document.querySelector('#col .note').textContent")
    assert "checkpoints" not in html and "/tmp" not in html and "A second line stays." in html, html


def test_the_folder_picker_shows_the_last_three_parts_with_an_ellipsis(app, page):
    deep = Path(app["work"]) / "Clients" / "Greenfield" / "Spring" / "Invoices"
    deep.mkdir(parents=True, exist_ok=True)
    page.evaluate("p => { window.__fb = browseFolder(p); }", str(deep))
    page.wait_for_function("() => document.querySelector('#fbCrumbs .here')")
    labels = page.evaluate("() => [...document.querySelectorAll('#fbCrumbs button')].map(b => b.textContent)")
    assert labels == ["…", "Greenfield", "Spring", "Invoices"], labels
    assert page.evaluate("() => document.querySelector('#fbCrumbs .more').title") == str(deep.parent.parent.parent)
    page.click("#fbCrumbs .more")
    page.wait_for_function("() => document.querySelector('#fbCrumbs .here').textContent === 'Clients'")
    short = page.evaluate("() => [...document.querySelectorAll('#fbCrumbs button')].map(b => b.textContent)")
    assert short[0] != "…" or len(short) == 4, short
    page.keyboard.press("Escape")
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ item 11, 12
def _wide_page(app, pw, width, height=800):
    from tests.test_ux018 import _open
    return _open(app, pw, width=width, height=height)


@pytest.mark.parametrize("width", [1280, 1366, 1440, 1600])
def test_preview_and_progress_fold_the_sidebar_by_the_same_rule(app, width):
    """Opening a preview folded the sidebar and slid the chat about 460 px, while Progress kept it at the same width, and
    "New" shrank to a small "+". Both fold the sidebar only when the chat would go under 640 px, and "New chat" stays a
    labelled button in the top bar while it is folded."""
    with sync_api.sync_playwright() as pw:
        pg = _wide_page(app, pw, width)
        try:
            _chat(pg, app["work"], None, "layout")
            pg.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir")
            pg.evaluate("() => { S.cur.view.code_mode = false; S.cur.view.kind = 'cowork'; renderTop(); }")
            pg.evaluate("() => togglePanel('progress', true)")
            pg.wait_for_selector("#panel:not([hidden])")
            pg.wait_for_timeout(450)
            prog = pg.evaluate("""() => ({fold: document.querySelector('#app').classList.contains('autoside'),
                chat: document.querySelector('#main').getBoundingClientRect().width})""")
            pg.evaluate("() => togglePanel('progress', false)")
            pg.wait_for_timeout(300)
            pg.evaluate("() => openPreviewCode('html', '<p>hello</p>')")
            pg.wait_for_selector("#pvp:not([hidden])")
            pg.wait_for_timeout(450)
            prev = pg.evaluate("""() => ({fold: document.querySelector('#app').classList.contains('autoside'),
                chat: document.querySelector('#main').getBoundingClientRect().width})""")
            assert prog["fold"] == prev["fold"], (width, prog, prev)
            assert prog["chat"] >= 640 - 1 and prev["chat"] >= 640 - 1, (width, prog, prev)
            if prev["fold"]:
                new = pg.evaluate("""() => { const b = document.querySelector('#topNew'), r = b.getBoundingClientRect();
                    return {text: b.innerText.trim(), w: r.width}; }""")
                assert new["text"] == "New chat" and new["w"] > 60, (width, new)
            assert pg.errors == [], pg.errors
        finally:
            pg.browser_.close()


def test_one_click_on_progress_shows_it_while_a_preview_is_open(app):
    """At 1024x700 with a preview open, the first click on Progress only marked the button: the panel was open in the
    page's state but hidden under the preview."""
    with sync_api.sync_playwright() as pw:
        pg = _wide_page(app, pw, 1024, 700)
        try:
            _chat(pg, app["work"], None, "progress click")
            pg.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir")
            pg.evaluate("() => { S.cur.view.code_mode = false; S.cur.view.kind = 'cowork'; S.panel = null; renderPanel(); renderTop(); }")
            pg.evaluate("() => openPreviewCode('html', '<p>hello</p>')")
            pg.wait_for_selector("#pvp:not([hidden])")
            pg.wait_for_selector("#panelBtn")
            assert pg.get_attribute("#panelBtn", "aria-pressed") == "false"
            pg.click("#panelBtn")
            pg.wait_for_function("() => { const p = document.querySelector('#panel'); return p && !p.hidden && p.getBoundingClientRect().width > 100; }", timeout=5000)
            got = pg.evaluate("() => ({pv: PVX.open, pressed: document.querySelector('#panelBtn').getAttribute('aria-pressed'), shown: getComputedStyle(document.querySelector('#panel')).display})")
            assert got == {"pv": False, "pressed": "true", "shown": "flex"}, got
            assert pg.errors == [], pg.errors
        finally:
            pg.browser_.close()


# ------------------------------------------------------------------------------------------------------ item 13, 14
def _vd(problems, ok=False, also=""):
    return {"persona": "a board member", "can_do_the_job": ok, "pass": ok, "also": also,
            "problems": [{"what": w, "where": "", "severity": s} for w, s in problems]}


def test_the_checked_line_claims_only_what_the_last_verdict_states():
    from meshpool_harness.agent import lookreview as LR
    # a guess about a typo that was fixed, and a bare "section 3": no claims at all
    probs = [("the typo 'teh' looks swapped for 'the'", "major"), ("section 3", "minor"), ("the totals may be off", "minor")]
    st = {"persona": "a board member", "evals": [_vd(probs), _vd(probs)]}
    assert LR.summary_line("doc", st) == "Checked it as a board member.", LR.summary_line("doc", st)
    # a named problem is still open: said whole, and with no "(and N more)"
    solid = [("the Q4 total shows an error", "blocker"), ("the chart has no title", "major"), ("a third problem here", "minor")]
    st = {"persona": "a board member", "evals": [_vd(solid), _vd(solid)]}
    line = LR.summary_line("doc", st)
    assert "(and" not in line and "Still open: the Q4 total shows an error; the chart has no title." in line, line


def test_no_also_noticed_offer_for_what_was_fixed():
    from meshpool_harness.agent import lookreview as LR
    first = _vd([("the chart is repeated on page 2", "major"), ("a missing title on page 1", "major")])
    last = _vd([], ok=True, also="the chart is repeated on page 2 with no new content")
    st = {"persona": "a board member", "evals": [first, last]}
    line = LR.summary_line("doc", st)
    assert "I also noticed" not in line, line
    last = _vd([], ok=True, also="the footer font differs from the body")
    assert "I also noticed the footer font differs from the body" in LR.summary_line("doc", {"persona": "a board member", "evals": [first, last]})


def test_a_bare_look_line_merges_with_the_checks_line():
    from meshpool_harness import checks as K
    assert K.merge_line("Checked it as a board member.", "Checked that the file opens cleanly.") == \
        "Checked it the way a board member would, and that the file opens.", K.merge_line("Checked it as a board member.", "Checked that the file opens cleanly.")


REQ = "add a Q4 column and extend the chart to include it"
CHANGE = [("the chart fill colours do not match the theme", "minor"), ("no landscape print setup", "minor"),
          ("column widths are too narrow, showing ### in the Q4 column", "major"), ("the Q4 total formula shows #REF!", "major")]


def test_a_change_request_is_not_held_to_style_it_did_not_ask_for():
    from meshpool_harness.agent import lookreview as LR
    v = LR.normalize_verdict({"persona": "a finance person", "problems": [{"what": w, "where": "", "severity": s} for w, s in CHANGE],
                              "pass": False}, REQ, "doc", "a finance person")
    got = LR.scope_filter(v, REQ, False, [])                     # with pictures: style goes to one offer, breakage stays
    assert [p["what"] for p in got["problems"]] == ["the Q4 total formula shows #REF!"], got
    assert got["also"] and "column widths" in got["also"], got


def test_a_reviewer_without_pictures_makes_no_visual_claims_unless_measured():
    from meshpool_harness.agent import lookreview as LR
    probs = [("the chart shows no bars", "major"), ("the Q4 column shows ###", "major"), ("the Q4 total formula shows #REF!", "major")]
    v = LR.normalize_verdict({"persona": "a finance person", "problems": [{"what": w, "where": "", "severity": s} for w, s in probs],
                              "pass": False}, REQ, "doc", "a finance person")
    got = LR.scope_filter(v, REQ, True, [])
    assert [p["what"] for p in got["problems"]] == ["the Q4 total formula shows #REF!"], got
    kept = LR.scope_filter(v, REQ, True, ["Sheet1!E2: shows ### (column too narrow for the value)"])
    assert "the Q4 column shows ###" in [p["what"] for p in kept["problems"]], kept
    assert "never problems" in LR.SYSTEM and "cannot see pictures" in LR.SYSTEM


def test_steps_the_server_marks_retried_are_not_counted_as_problems(app, page):
    """The server sends {"type": "steps_retried", "ids": [...]} just before turn_end for failed steps that a later step
    redid. Their rows read "retried" and the group does not count them."""
    _chat(page, app["work"], None, "retried")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    got = page.evaluate("""() => { $('#col').replaceChildren(); S.cur.tools = {}; S.cur.group = null; S.cur.view.code_mode = false; S.cur.log = [];
        const ev = e => { S.cur.log.push(e); applyEv(e); };
        ev({type: 'user', text: 'make the sheet', i: 0});
        ev({type: 'tool_start', id: 'a', name: 'exec_command', summary: 'ls', mcp: false}); ev({type: 'tool_end', id: 'a', name: 'exec_command', ok: true, output: 'x'});
        ev({type: 'tool_start', id: 'b', name: 'exec_command', summary: 'python make.py', mcp: false}); ev({type: 'tool_end', id: 'b', name: 'exec_command', ok: false, output: 'ERROR missing file'});
        const before = document.querySelector('#col .work .wl').textContent;
        ev({type: 'steps_retried', ids: ['b']});
        ev({type: 'assistant', text: 'Made it.', streamed: false}); ev({type: 'turn_end'});
        return {before, after: document.querySelector('#col .work .wl').textContent, st: document.querySelector('#tool-b .st').textContent,
                bad: document.querySelectorAll('#col .st.bad').length, prog: progressSteps().items.map(i => i.status)}; }""")
    assert got["before"] == "2 steps, 1 had a problem", got
    assert got["after"] == "2 steps" and got["st"] == "retried" and got["bad"] == 0, got
    assert got["prog"] == ["completed", "completed"], got
    assert page.errors == [], page.errors
