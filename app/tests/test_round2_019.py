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
    ("mcp__meshpool_office__read_document", '{"path": "/tmp/claude-0/x/client-update.docx"}', True, "Read client-update.docx"),
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
    assert steps == [c[3] for c in CASES], steps
    # a failed connector step is not "Connector step ."
    page.evaluate("""() => { const pl = {base: 'Connector step', run: 'Using a connector', done: 'Used a connector'};
        applyEv({type: 'tool_start', id: 'y', name: 'mcp__foo__bar', summary: '/tmp/claude-0/x', mcp: true, plain: pl});
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
