"""Web Code chat bugs from the 0.0.18 persona Omar (OM-05, OM-07, OM-10). Each test drives the real app (python -m
meshpool_harness ui) in Chromium, with page.route standing in for answers that need a model. No model is called.
Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ui_bugs018 import _code_chat, _fake_chat, _launch, _port, _wait  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("uiom018")
    home, mh, work = tmp / "home", tmp / "mh", tmp / "work"
    for d in (home, mh, work):
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
        yield {"base": base, "work": str(work), "mh": mh}
    finally:
        p.kill()
        p.wait(10)
        log.close()


def _page(pw, app, width=1280, height=860):
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
    return b, pg


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        b, pg = _page(pw, app)
        yield pg
        b.close()


@pytest.fixture()
def narrow(app):
    with sync_api.sync_playwright() as pw:
        b, pg = _page(pw, app, width=1024, height=700)
        yield pg
        b.close()


LONG = "    return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, \"0\")} ${currency}` // " + "x" * 160
PATCH = ("--- a/web/src/format.ts\n+++ b/web/src/format.ts\n@@ -1,3 +1,3 @@\n export function formatCents() {\n"
         "-" + LONG.replace("String(abs % 100).padStart(2, \"0\")", "abs % 100") + "\n+" + LONG + "\n }\n")


def _fake_diff(page, cid):
    """The Changes tab's answer for chat cid: one file with a line far wider than the panel."""
    body = json.dumps({"files": [{"path": "web/src/format.ts", "status": "modified", "add": 1, "del": 1}],
                       "patch": PATCH})
    page.route(re.compile(r"^https?://[^/]+/api/agent/" + re.escape(cid) + r"/diff"),
               lambda route, request: route.fulfill(status=200, content_type="application/json", body=body))


def _fits(page, sel):
    """For each element matching sel: its scroll and client widths, its right edge and the window's width."""
    return page.evaluate("""sel => [...document.querySelectorAll(sel)].map(el => { const r = el.getBoundingClientRect();
        return {sw: el.scrollWidth, cw: el.clientWidth, right: Math.round(r.right), vw: innerWidth}; })""", sel)


# ---------------------------------------------------------------------------------------------------- OM-10
def test_om10_a_long_line_in_the_changes_diff_wraps_instead_of_being_cut(page):
    """OM-10: the Changes diff cut long lines at the panel's edge (shots/018/08_tab_1_changes.png). They wrap now."""
    cid = _code_chat(page, page.app["work"])
    _fake_diff(page, cid)
    page.evaluate("() => togglePanel('diff', true)")
    page.wait_for_selector("#patch .l.a")
    page.wait_for_timeout(300)                  # the panel finishes sliding in (its edge is past the window until then)
    got = _fits(page, "#patch")
    assert got
    for el in got:
        assert el["sw"] <= el["cw"] + 1 and el["right"] <= el["vw"], el
    assert "padStart" in page.inner_text("#patch .l.a")
    assert page.errors == [], page.errors


def test_om10_a_long_command_in_the_approval_card_wraps(page):
    """OM-10: the approval card cut a long command or diff line. It wraps now."""
    _fake_chat(page, "t-om10")
    page.evaluate("""cmd => { applyEv({type: "user", text: "fix it", i: 0});
        applyEv({type: "tool_start", id: "b", name: "bash", summary: "python3 -c", i: 1});
        applyEv({type: "approval", id: "ap1", kind: "bash", title: "Run this command?", detail: cmd, i: 2}); }""",
                  "python3 -c \"" + LONG + "\"")
    page.wait_for_selector(".appr pre")
    got = _fits(page, ".appr pre")
    assert got
    for el in got:
        assert el["sw"] <= el["cw"] + 1 and el["right"] <= el["vw"], el
    assert page.errors == [], page.errors


def test_om10_at_1024_px_every_panel_tab_shows_whole_and_only_the_open_one_looks_open(narrow):
    """OM-10: at 1024 px "Git and CI" was cut in half and the tabs after it could not be reached; and a tab under the
    mouse looked exactly like the open one, so two tabs looked open (shots/018/14_1024_panel.png). The tabs are one row that
    scrolls sideways now (J-03): none is cut in half and each one can be reached."""
    page = narrow
    _code_chat(page, page.app["work"])
    page.evaluate("() => togglePanel('session', true)")
    page.wait_for_selector(".ptabs .pstrip button[aria-selected=true]")
    tabs = page.evaluate("""() => { const close = document.querySelector('#pclose').getBoundingClientRect();
        const strip = document.querySelector('.ptabs .pstrip'), sr = strip.getBoundingClientRect();
        return [...strip.querySelectorAll('button')].map(b => { const r = b.getBoundingClientRect();
          return {t: b.textContent.trim(), l: r.left, r: r.right, sl: sr.left, sr: sr.right, close: close.left,
                  h: r.height, top: Math.round(r.top), cut: b.scrollWidth > b.clientWidth + 1, on: b.getAttribute('aria-selected') === 'true'}; }); }""")
    assert any(t["t"] == "Git and CI" for t in tabs)
    assert len({t["top"] for t in tabs}) == 1, tabs             # one row (J-03): it scrolls sideways, tabs are never wrapped to three rows
    for t in tabs:                                              # no tab cut in half; the open one is whole inside the strip, never under the close button
        assert not t["cut"] and t["h"] > 10, t
        if t["on"]:
            assert t["l"] >= t["sl"] - 1 and t["r"] <= t["sr"] + 1 and t["r"] <= t["close"] + 1, t
    page.hover(".ptabs .pstrip button:has-text('Terminal')")
    looks = page.evaluate("""() => [...document.querySelectorAll('.ptabs .pstrip button')].map(b => ({
        t: b.textContent.trim(), sel: b.getAttribute('aria-selected') === 'true', bg: getComputedStyle(b).backgroundColor,
        w: getComputedStyle(b).fontWeight}))""")
    open_ = next(x for x in looks if x["sel"])
    hov = next(x for x in looks if x["t"] == "Terminal")
    assert (hov["bg"], hov["w"]) != (open_["bg"], open_["w"]), looks
    page.click(".ptabs .pstrip button:has-text('Git and CI')")
    assert page.evaluate("() => S.panel") == "git"
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- OM-05
def test_om05_the_changes_tab_of_an_undone_turn_says_it_was_undone(page):
    """OM-05: Undo takes the turn out of the chat (a rewind, by design: the message goes back into the box). The
    Changes tab still asked for that turn and showed "No such turn.". It now says the turn was undone and shows the
    chat's remaining changes."""
    cid = _code_chat(page, page.app["work"])

    def diff(route, request):
        one = "turn=" in request.url
        route.fulfill(status=400 if one else 200, content_type="application/json",
                      body=json.dumps({"error": "No such turn."} if one else
                                      {"files": [], "patch": "", "note": "No changes recorded yet."}))
    page.route(re.compile(r"^https?://[^/]+/api/agent/" + re.escape(cid) + r"/diff.*"), diff)
    page.route(re.compile(r"^https?://[^/]+/api/agent/" + re.escape(cid) + r"/revert"),
               lambda route, request: route.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"undone": {"deleted": [], "restored": []}})))
    page.evaluate("() => { S.cur.view = Object.assign(S.cur.view || {}, {turns: [{n: 3, title: 'add a docstring', "
                  "changed: [{path: 'a.py'}]}]}); S.diffTurn = 3; togglePanel('diff', true); }")
    page.wait_for_selector("#dbody .pnote")
    page.evaluate("() => { window.ask = async () => true; return undoTurn(3); }")
    page.wait_for_selector("#dgone")
    body = page.inner_text("#pbody")
    assert "This turn was undone" in body and "No such turn" not in body, body
    assert page.evaluate("() => S.diffTurn") is None
    # undone elsewhere (in the terminal): the server's "No such turn." becomes the same note
    page.evaluate("() => { S.cur.view.turns = [{n: 5, title: 'x', changed: []}]; S.diffTurn = 5; renderPanel(); }")
    page.wait_for_selector("#dgone")
    assert "No such turn" not in page.inner_text("#pbody")
    assert page.errors == [], page.errors
