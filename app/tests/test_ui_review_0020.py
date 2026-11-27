"""Independent review of 0.0.20, the web app's known issues: each test drives the real app in Chromium (the fixtures of
tests/test_ux018.py) and failed before its fix."""
from __future__ import annotations

from pathlib import Path

from tests.test_round3_020 import _chat_view, _ev
from tests.test_ux018 import app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)


def test_the_right_pane_handle_leaves_the_editors_left_edge_to_the_editor(app, page):
    """The 8 px resize handle lies over the right pane's left edge: a click or a drag there in the text editor started
    a resize instead of placing the caret."""
    (Path(app["work"]) / "notes.txt").write_text("".join(f"line {i}\n" for i in range(40)))
    page.evaluate("w => post('/api/projects', {action: 'add', path: w})", app["work"])
    page.set_viewport_size({"width": 1600, "height": 900})
    page.wait_for_timeout(350)
    _chat_view(page, app["work"])
    page.evaluate("w => openPreview(w, 'notes.txt')", app["work"])
    page.wait_for_selector("#edTa")
    page.wait_for_function("() => !document.querySelector('#rzRight').hidden && document.querySelector('#edTa').offsetWidth > 0")
    got = page.evaluate("""() => { const ta = document.querySelector('#edTa').getBoundingClientRect(), h = document.querySelector('#rzRight').getBoundingClientRect(), y = ta.top + ta.height / 2;
        return {taLeft: ta.left, hRight: h.right, hLeft: h.left, hit: [1, 4, 7].map(dx => document.elementFromPoint(ta.left + dx, y)?.id || '')}; }""")
    assert got["taLeft"] >= got["hRight"] - 0.5, got                   # the editor starts where the handle ends
    assert got["hit"] == ["edTa", "edTa", "edTa"], got
    assert page.evaluate("() => document.elementFromPoint(document.querySelector('#rzRight').getBoundingClientRect().left + 4, 300).id") == "rzRight"
    assert page.errors == [], page.errors


def test_a_large_picture_that_does_not_load_comes_back_as_a_file_card(app, page):
    """A picture shown large in the reply is not also a card. When it failed to load it was removed, and the file was
    then shown nowhere."""
    page.evaluate("w => post('/api/projects', {action: 'add', path: w})", app["work"])
    _chat_view(page, app["work"])
    _ev(page, {"type": "user", "text": "Make a chart of revenue and show it here", "i": 0})
    page.evaluate("""() => { S.cur.tpaths = ['gone.png']; applyEv({type: 'turn_end', turn: {}}); }""")   # no such file
    page.wait_for_function("""() => !document.querySelector('#col .pvbig') && [...document.querySelectorAll('#col .pvcards .pvcard')].some(c => c.dataset.pv === 'gone.png' && c.offsetParent)""",
                           timeout=15000)
    got = page.evaluate("""() => ({big: document.querySelectorAll('#col .pvbig').length, rows: document.querySelectorAll('#col .pvcards').length,
        cards: [...document.querySelectorAll('#col .pvcards .pvcard')].map(c => c.dataset.pv)})""")
    assert got == {"big": 0, "rows": 1, "cards": ["gone.png"]}, got
    page.evaluate("() => pvTurnCards(S.cur, S.cur.lastFoot, 0)")      # drawn again (a later event): still a card
    assert page.evaluate("() => [...document.querySelectorAll('#col .pvcards .pvcard')].map(c => c.dataset.pv)") == ["gone.png"]
    assert page.evaluate("() => document.querySelectorAll('#col .pvbig').length") == 0
