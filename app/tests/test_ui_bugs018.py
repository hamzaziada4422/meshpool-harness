"""UI bugs found in the 0.0.18 bug hunt (IDs UI-01 to UI-12): the chat column, the side panel tabs, the composer's
drafts, the folder picker and Settings > Models. Each test drives the real app (python -m meshpool_harness ui) in
Chromium, as a user would, with page.route standing in for slow or refusing answers. No model is called. Skipped when
no Chromium can start here (outside CI)."""
from __future__ import annotations

import glob
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
    tmp = tmp_path_factory.mktemp("uibugs018")
    home, mh, work = tmp / "home", tmp / "mh", tmp / "work"
    for d in (home, mh, work, work / "alpha", work / "beta"):
        d.mkdir()
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
        yield {"base": base, "work": str(work)}
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


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_page(viewport={"width": 1280, "height": 860})
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
        yield pg
        b.close()


def _fake_chat(page, cid: str = "t-ui018"):
    """An open chat with no server behind it: applyEv draws into it as a live chat would."""
    page.evaluate("""cid => { openThread(cid, true); if (S.cur.pollT) { clearTimeout(S.cur.pollT); clearInterval(S.cur.pollT); }
        S.cur.pollT = null; stopStream(S.cur); S.cur.stage = null; $('#col').replaceChildren(); $('#col').classList.remove('loading'); }""", cid)


def _code_chat(page, folder: str) -> str:
    """A real chat on the server, in a folder, opened in the page (the Code tabs: Changes, Files, Terminal, Git)."""
    return page.evaluate("""async f => { const v = await post('/api/agent', {workdir: f, kind: 'code', task: 'tabs'});
        await loadThreads(); openThread(v.id); return v.id; }""", folder)


class _Held(list):
    """Requests held back until release(): then they, and any later ones, go through."""
    open = False

    def release(self):
        self.open = True
        while self:
            self.pop(0).continue_()


def _hold(page, pattern: str) -> _Held:
    held = _Held()
    page.route(re.compile(r"^https?://[^/]+" + pattern),
               lambda route, request: route.continue_() if held.open else held.append(route))
    return held


# ---------------------------------------------------------------------------------------------------- UI-01
TURN = """[{type: "user", text: "Look at the files", i: 0},
  {type: "tool_start", id: "a", name: "read_file", summary: "a.txt", i: 1}, {type: "tool_end", id: "a", ok: true, output: "x", i: 2},
  {type: "tool_start", id: "b", name: "bash", summary: "ls", i: 3},
  {type: "approval", id: "ap1", kind: "bash", title: "Run this command?", detail: "ls", i: 4},
  {type: "approval_done", id: "ap1", decision: "yes", i: 5}, {type: "tool_end", id: "b", ok: true, output: "y", i: 6},
  {type: "tool_start", id: "c", name: "grep", summary: "z", i: 7}, {type: "tool_end", id: "c", ok: true, output: "z", i: 8},
  {type: "assistant", text: "Done.", i: 9}, {type: "turn_end", i: 10},
  {type: "user", text: "And again", i: 11}, {type: "tool_start", id: "d", name: "read_file", summary: "d.txt", i: 12}]"""


def test_reopened_chat_keeps_a_turns_steps_in_one_group(page):
    """UI-01: a reopened (or reloaded) chat is built off-screen, where isConnected is false for every node. Each step
    then got its own "1 step" group, the approval card fell out of its group, and the groups of finished turns kept
    their spinning dots. Replayed, the turn reads as it did live: one "3 steps" group, finished, holding the card."""
    _fake_chat(page)
    got = page.evaluate(f"""() => {{
        const evs = {TURN};
        const cur = S.cur; beginStage(cur); for (const e of evs) applyEv(e); commitStage(cur);
        const gs = [...document.querySelectorAll('#col > .work')];
        return gs.map(g => ({{label: g.querySelector('.wl').textContent, done: g.dataset.done || '', dots: !!g.querySelector('.gdots'),
                             appr: !!g.querySelector('.rows > .appr')}}));
    }}""")
    assert len(got) == 2, got                                   # the finished turn's group and the running one
    assert got[0] == {"label": "3 steps", "done": "1", "dots": False, "appr": True}, got
    assert got[1]["label"] == "1 step" and got[1]["dots"], got   # the step that still runs keeps its dots
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-02
def test_reopened_chat_shows_one_sub_agents_box_per_hand_off(page):
    """UI-02: delegate_task progress replayed into the off-screen column looked its box up with getElementById, which
    cannot see it there, so every progress event drew another "Sub-agents" box."""
    _fake_chat(page)
    got = page.evaluate("""() => {
        const evs = [{type: "user", text: "Split it up", i: 0},
          {type: "subagent", delegation: "d1", sid: "s1", title: "Read the code", kind: "start", i: 1},
          {type: "subagent", delegation: "d1", sid: "s2", title: "Write the tests", kind: "start", i: 2},
          {type: "subagent", delegation: "d1", sid: "s1", kind: "tool", tool: "read_file", text: "a.py", i: 3},
          {type: "subagent", delegation: "d1", sid: "s1", kind: "done", status: "completed", text: "done", i: 4}];
        const cur = S.cur; beginStage(cur); for (const e of evs) applyEv(e); commitStage(cur);
        return [...document.querySelectorAll('#col .planbox')].map(b => b.querySelectorAll('li').length);
    }""")
    assert got == [2], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-03
def test_a_draft_typed_on_a_project_page_stays_with_that_project(page):
    """UI-03: the box saved its draft under "new" whenever no chat was open, but a project's page restores the draft
    saved under that project. A draft typed on a project's page showed up in New chat (where Send makes a chat outside
    the project) and was gone from the project after a reload."""
    pid = page.evaluate("""async () => { const r = await post('/api/pj', {action: 'create', name: 'Drafts'}); await loadPj();
        return r.id || (r.project && r.project.id); }""")
    assert pid
    page.evaluate("pid => openProject(pid)", pid)
    page.wait_for_function("() => S.view === 'project'")
    page.click("#input")
    page.keyboard.type("Half written note for the project")
    page.evaluate("() => newThread()")
    assert page.input_value("#input") == ""
    page.reload()
    page.wait_for_selector("#col .hero", timeout=30000)        # booted: the new-chat home is drawn
    page.wait_for_timeout(300)
    page.evaluate("pid => openProject(pid)", pid)
    page.wait_for_function("() => S.view === 'project'")
    page.wait_for_function("() => $('#input').value === 'Half written note for the project'", timeout=5000)
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-04
def test_a_step_cut_short_by_stop_is_not_counted_as_failed(page):
    """UI-04: on Stop the agent closes the step it was in with "Not finished: the turn ended." The row said "failed"
    in red and the group "1 step, 1 failed", although nothing failed; the Progress tab said failed too."""
    _fake_chat(page)
    page.evaluate("""() => {
        for (const e of [{type: "user", text: "Count", i: 0}, {type: "tool_start", id: "t1", name: "bash", summary: "sleep 30", i: 1},
                         {type: "tool_end", id: "t1", name: "bash", ok: false, denied: false, output: "Not finished: the turn ended.", i: 2}]) {
          S.cur.log.push(e); applyEv(e); }
    }""")
    row = page.evaluate("() => { const st = document.querySelector('#tool-t1 .st'); return [st.textContent, st.className]; }")
    assert row == ["not finished", "st "], row
    assert "failed" not in page.text_content("#col .work .wl")
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#pgSteps li")
    assert page.evaluate("() => document.querySelector('#pgSteps li').className") != "failed"
    # a real failure still says so
    page.evaluate("""() => { applyEv({type: "tool_start", id: "t2", name: "bash", summary: "make", i: 3});
        applyEv({type: "tool_end", id: "t2", ok: false, output: "[exit 2] make: no rule", i: 4}); }""")
    assert page.text_content("#tool-t2 .st") == "failed"
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-05
def test_terminal_output_keeps_coming_after_a_tab_switch(page):
    """UI-05: run a command in the Terminal tab, look at Files, come back. The command wrote into the old, detached
    terminal, so nothing it printed after the switch showed, and its Stop button was gone while it still ran. This is
    the one-command mode, the panel's fallback without a PTY library (test_ui_terminal_pty.py has the real shell)."""
    _code_chat(page, page.app["work"])
    page.evaluate("() => { S.settings.terminal = {pty: null}; togglePanel('term', true); }")
    page.wait_for_selector("#tin")
    page.fill("#tin", "for i in 1 2 3 4 5; do echo tick$i; sleep 0.4; done")
    page.press("#tin", "Enter")
    page.wait_for_function("() => /tick1/.test($('#tout').textContent)", timeout=10000)
    page.evaluate("() => togglePanel('files', true)")
    page.evaluate("() => togglePanel('term', true)")
    assert page.evaluate("() => !$('#tstop').hidden"), "Stop is hidden while the command still runs"
    page.wait_for_function("() => /tick5/.test($('#tout').textContent)", timeout=10000)
    page.wait_for_function("() => $('#tstop').hidden", timeout=5000)
    assert page.text_content("#tout").count("tick3") == 1
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-06
def test_a_long_terminal_command_is_polled_slowly_once_its_panel_is_closed(page):
    """UI-06: a command that keeps running (a dev server) was read four times a second for as long as it ran, with
    the panel closed and the chat left. With its output off screen it is read every 2 s. (The one-command mode; the
    real shell's polling has its own test in test_ui_terminal_pty.py.)"""
    _code_chat(page, page.app["work"])
    page.evaluate("() => { S.settings.terminal = {pty: null}; togglePanel('term', true); }")
    page.wait_for_selector("#tin")
    page.fill("#tin", "sleep 6")
    page.press("#tin", "Enter")
    page.wait_for_function("() => S.term && S.term.id && !$('#tstop').hidden", timeout=10000)
    hits = []
    page.on("request", lambda r: hits.append(time.time()) if re.search(r"/api/term/[^/?]+\?since=", r.url) else None)
    page.evaluate("() => togglePanel(S.panel)")                   # close the panel
    page.wait_for_timeout(500)
    hits.clear()
    page.wait_for_timeout(3000)
    assert len(hits) <= 3, f"{len(hits)} polls in 3 s with the terminal closed"
    page.evaluate("() => post(`/api/term/${S.term.id}/stop`).catch(() => {})")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-07
def test_a_slow_git_tab_does_not_draw_over_the_tab_opened_after_it(page):
    """UI-07: click Git and CI, then Files before git answers. The late git answer was drawn into the panel body under
    the Files tab ("This folder is not a Git repository" in Files)."""
    _code_chat(page, page.app["work"])
    held = _hold(page, r"/api/git\?root=")
    page.evaluate("() => togglePanel('git', true)")
    page.wait_for_timeout(500)
    assert held, "the git tab asked for nothing"
    page.evaluate("() => togglePanel('files', true)")
    page.wait_for_selector("#tree")
    held.release()                                              # the tab's request, and the branch chip's
    page.wait_for_timeout(800)
    assert page.evaluate("() => S.panel") == "files"
    assert page.query_selector("#pbody #tree"), page.text_content("#pbody")
    assert "Git" not in page.text_content("#pbody")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-08
def test_a_slow_changes_tab_does_not_throw_after_the_tab_is_left(page):
    """UI-08: click Changes, then Files before the diff answers. The late answer wrote to #dbody, which was gone:
    "Cannot set properties of null" on the page, and the tab's work was lost."""
    _code_chat(page, page.app["work"])
    held = _hold(page, r"/api/agent/[^/]+/diff")
    page.evaluate("() => togglePanel('diff', true)")
    page.wait_for_timeout(500)
    assert held, "the Changes tab asked for nothing"
    page.evaluate("() => togglePanel('files', true)")
    page.wait_for_selector("#tree")
    held.release()
    page.wait_for_timeout(800)
    assert page.evaluate("() => S.panel") == "files" and page.query_selector("#pbody #tree")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-09
def test_enter_on_the_folder_pickers_buttons_does_what_the_button_says(page):
    """UI-09: in Choose a folder, a folder picked with the arrow keys stayed picked when Tab moved to Cancel or
    "Use ...", and Enter there opened that folder instead: Cancel could not be pressed from the keyboard."""
    work = page.app["work"]
    page.evaluate("f => { window._fb = 'pending'; browseFolder(f).then(v => { window._fb = v; }); }", work)
    page.wait_for_selector("#fbList [data-p]")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.focus(".dlg.fb [data-a='0']")
    page.keyboard.press("Enter")
    page.wait_for_function("() => window._fb !== 'pending'", timeout=5000)
    assert page.evaluate("window._fb") == ""
    assert not page.query_selector(".dlg.fb")
    # "Use this folder" by Enter uses the folder shown, not the one highlighted in the list
    page.evaluate("f => { window._fb = 'pending'; browseFolder(f).then(v => { window._fb = v; }); }", work)
    page.wait_for_selector("#fbList [data-p]")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.focus(".dlg.fb [data-a='1']")
    page.keyboard.press("Enter")
    page.wait_for_function("() => window._fb !== 'pending'", timeout=5000)
    assert os.path.realpath(page.evaluate("window._fb")) == os.path.realpath(work)
    # arrows and Enter in the list still open a folder
    page.evaluate("f => { window._fb = 'pending'; browseFolder(f).then(v => { window._fb = v; }); }", work)
    page.wait_for_selector("#fbList [data-p]")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.wait_for_function("() => /alpha|beta/.test($('#fbSel').textContent)", timeout=5000)
    page.keyboard.press("Escape")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-10
def test_progress_keeps_the_steps_from_before_a_message_added_while_working(page):
    """UI-10: the Progress tab started the turn at the last "user" event, and a message added while the agent works
    is one. The steps done before it vanished from the list."""
    _fake_chat(page)
    page.evaluate("""() => {
        S.cur.log = [{type: "user", text: "Fix the build", i: 0},
          {type: "tool_start", id: "a", name: "read_file", summary: "Makefile", i: 1}, {type: "tool_end", id: "a", ok: true, output: "x", i: 2},
          {type: "user", text: "also check the tests", steer: true, i: 3},
          {type: "tool_start", id: "b", name: "bash", summary: "make test", i: 4}];
        S.cur.view.state = "working";
        togglePanel('progress', true);
    }""")
    page.wait_for_selector("#pgSteps li")
    steps = page.evaluate("() => [...document.querySelectorAll('#pgSteps li .stx')].map(x => x.textContent)")
    assert len(steps) == 2 and "Makefile" in steps[0] and "make test" in steps[1], steps
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-11
def test_copy_and_edit_of_a_message_with_a_picture_leave_out_its_file_name(page):
    """UI-11: a message sent with a picture shows the picture's name in its bubble. Copy and Edit read the bubble's
    text, so "describe this" came back as "describe this photo.png" (and Edit sent that name as words)."""
    _fake_chat(page)
    page.evaluate("""() => {
        navigator.clipboard.writeText = t => { window._copied = t; return Promise.resolve(); };
        applyEv({type: "user", text: "describe this\\nin two lines", images: ["photo.png"], plan: true, i: 0});
        S.cur.busy = true; S.cur.view.state = "working";
    }""")
    page.hover("#col .msg-user")
    page.click("#col .msg-user [data-ma='copy']")
    page.wait_for_function("() => window._copied !== undefined")
    assert page.evaluate("window._copied") == "describe this\nin two lines"
    page.click("#col .msg-user [data-ma='edit']")
    assert page.input_value("#input") == "describe this\nin two lines"
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-12
def test_models_settings_says_why_make_default_was_refused(page):
    """UI-12: Settings > Models > Make default (and the star) had no error handling: a refusal from the server was an
    unhandled promise rejection and nothing on screen changed or explained it."""
    page.evaluate("""() => { S.models = {models: [{id: "m-one", provider: "Mock"}, {id: "m-two", provider: "Mock"}],
        favorites: [], default: "m-one", base_url: "http://127.0.0.1:9/v1", groups: []}; }""")
    page.route(re.compile(r"^https?://[^/]+/api/models/(default|favorite)$"),
               lambda route, request: route.fulfill(status=400, content_type="application/json",
                                                    body='{"error": "That model is no longer offered by your endpoint."}'))
    page.evaluate("() => openSettings('models')")
    page.wait_for_selector("#mbody [data-def='m-two']")
    page.click("#mbody [data-def='m-two']")
    page.wait_for_selector(".toast:has-text('no longer offered')", timeout=5000)
    page.click("#mbody [data-fav='m-two']")
    page.wait_for_timeout(300)
    assert page.evaluate("S.models.default") == "m-one"
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-13
def test_a_project_path_inside_a_link_or_diagram_keeps_the_reply_intact(page):
    """UI-13: paths under the chat's folder become file buttons, but the replacement ran over the reply's HTML,
    attributes included. A path in a link's address or in a Mermaid diagram got a button's markup put inside the
    attribute: the link pointed nowhere, its tag spilled onto the page as text, and the diagram's source was cut."""
    _fake_chat(page)
    got = page.evaluate("""() => {
        S.cur.view.workdir = "/w/proj";
        const src = "```mermaid\\ngraph TD\\n  A[/w/proj/src/main.py] --> B[/w/proj/out/report.pdf]\\n```\\n\\n" +
                    "See [the guide](https://example.com/w/proj/guide.html) and /w/proj/notes.md.";
        applyEv({type: "user", text: "Draw it", i: 0}); applyEv({type: "assistant", text: src, i: 1});
        const ai = document.querySelector("#col .msg-ai"), a = ai.querySelector("a");
        return {mermaid: ai.querySelector(".mermaid-box").dataset.mermaid, href: a.getAttribute("href"), text: a.textContent,
                body: ai.textContent, open: [...ai.querySelectorAll("p .flink")].map(x => x.dataset.open)};
    }""")
    assert got["mermaid"] == "graph TD\n  A[/w/proj/src/main.py] --> B[/w/proj/out/report.pdf]\n", got
    assert got["href"] == "https://example.com/w/proj/guide.html" and got["text"] == "the guide", got
    assert "target=" not in got["body"] and "<button" not in got["body"], got["body"]
    assert got["open"] == ["notes.md"], got                     # a path in plain text still becomes a file button
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- UI-14
def test_header_redraws_do_not_run_git_status_each_time(page):
    """UI-14: every header redraw (renderTop) asked /api/git, which runs git status on the server. A fresh answer is
    now reused for 15 s; a turn ending or a rewind still asks again at once (renderBranch(true))."""
    import subprocess as sp
    work = Path(page.app["work"]) / "alpha"
    if not (work / ".git").exists():
        sp.run(["git", "init", "-q", str(work)], check=True)
    _code_chat(page, str(work))
    page.wait_for_timeout(800)
    seen = []
    page.on("request", lambda r: seen.append(r.url) if "/api/git?root=" in r.url else None)
    page.evaluate("async () => { for (let i = 0; i < 10; i++) { renderTop(); await new Promise(r => setTimeout(r, 30)); } }")
    page.wait_for_timeout(800)
    assert len(seen) <= 1, seen                                  # was one git status per redraw
    page.evaluate("() => renderBranch(true)")
    page.wait_for_timeout(800)
    assert len(seen) >= 1 and len(seen) <= 2, seen               # a forced refresh still asks
    assert page.errors == [], page.errors
