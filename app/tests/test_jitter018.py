"""Jitter and layout weak spots measured by the independent look at the 0.0.18 design pass (J-01 to J-06). Each test drives
the real app (python -m meshpool_harness ui) in Chromium against the mock model and measures what a person sees: layout
shift (PerformanceObserver layout-shift), the rows of the panel tabs, the computed type of the review fold, the edge of
the dark settings modal and where the onboarding buttons sit. Each one failed before its fix. The rules of
test_unslop018.py still hold: transform and opacity only, one easing, 200 ms or less, reduced motion respected. Skipped
when no Chromium can start here (outside CI)."""
from __future__ import annotations

import pytest

from tests.test_ux018 import _chats, _fake_chat, _lum, _open, app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)

pytest.importorskip("playwright.sync_api")

CLS = """() => { window.__cls = 0; window.__cn = 0; try { new PerformanceObserver(l => { for (const e of l.getEntries()) { __cls += e.value; __cn++; } })
    .observe({type: 'layout-shift'}); } catch (e) {} }"""


def _shift(page, js: str, settle: int = 700) -> float:
    """Layout shift summed over one change made from script (no input, so the browser counts all of it)."""
    page.evaluate("() => { window.__cls = 0; }")
    page.evaluate(js)
    page.wait_for_timeout(settle)
    return page.evaluate("() => +window.__cls.toFixed(4)")


# ---------------------------------------------------------------------------------------------------- J-01
@pytest.mark.parametrize("width,height", [(1280, 800), (1024, 700)])
def test_the_chat_does_not_jump_when_the_panel_or_the_sidebar_opens_or_closes(app, page, width, height):
    """J-01: the chat column snapped to its new width: layout shift 0.12 to 0.23 on every open and close of the side panel
    or the sidebar. The change is committed in one frame and what moved glides from where it was by transform (FLIP), and
    the sidebar folds before the column would have to shrink, so the text keeps its width. Each change now shifts under 0.02."""
    ids = _chats(app, page, 1)
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_timeout(1500)
    page.evaluate(CLS)
    got = {
        "panel open": _shift(page, "() => togglePanel('progress', true)"),
        "panel close": _shift(page, "() => togglePanel(S.panel)"),
        "sidebar hide": _shift(page, "() => toggleSide()"),
        "sidebar show": _shift(page, "() => toggleSide()"),
        "both": _shift(page, "() => { toggleSide(); togglePanel('progress', true); }"),
        "panel close, sidebar hidden": _shift(page, "() => togglePanel(S.panel)"),
        "sidebar back": _shift(page, "() => toggleSide()"),
    }
    assert all(v < 0.02 for v in got.values()), got
    assert page.errors == [], page.errors


def test_the_column_keeps_its_width_while_the_panel_opens_at_1280(app, page):
    """J-01: the shift came from the text of the column wrapping again at a narrower width. With the sidebar folded while
    the panel is open, the column is as wide open as shut."""
    ids = _chats(app, page, 1)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_timeout(1200)
    w = "() => Math.round(document.querySelector('#col').offsetWidth)"
    before = page.evaluate(w)
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_timeout(500)
    assert page.evaluate(w) == before, (before, page.evaluate(w))
    page.evaluate("() => togglePanel(S.panel)")
    page.wait_for_timeout(500)
    assert page.evaluate(w) == before


def test_the_move_is_a_transform_that_ends_and_reduced_motion_commits_at_once(app, page):
    """J-01: the glide animates transform only, is over within the panel time (200 ms), leaves no animation or timer
    behind, and is skipped for people who asked for less motion."""
    ids = _chats(app, page, 1)
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_timeout(1200)
    page.evaluate("() => toggleSide()")
    got = page.evaluate("""() => document.getAnimations().filter(a => a.id === 'flip').map(a => ({
        props: [...new Set(a.effect.getKeyframes().flatMap(k => Object.keys(k)).filter(k => !['offset', 'easing', 'composite', 'computedOffset'].includes(k)))],
        dur: a.effect.getTiming().duration}))""")
    assert got and all(g["props"] == ["transform"] and g["dur"] <= 200 for g in got), got
    page.wait_for_timeout(500)
    assert page.evaluate("() => document.getAnimations().filter(a => a.id === 'flip').length") == 0
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("() => toggleSide()")
    assert page.evaluate("() => document.getAnimations().filter(a => a.id === 'flip').length") == 0


# ---------------------------------------------------------------------------------------------------- J-02
def test_opening_a_chat_shifts_the_layout_by_0_01_or_less(app, page):
    """J-02: opening a chat shifted the layout by about 0.04 while its first messages came in: the newest items were drawn
    at their real size with no containment for two frames, and when that was lifted the margins inside no longer collapsed
    through them, moving the text by 27 px. The first bold and the first code text also swapped their font in. Settled items
    keep the containment, and the two fonts are preloaded."""
    ids = _chats(app, page, 2)
    page.evaluate(CLS)
    got = [_shift(page, f"() => openThread('{i}')", 1100) for i in (ids[0], ids[1], ids[0])]
    assert all(v <= 0.01 for v in got), got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- J-03
def test_the_panel_tabs_are_one_row_without_cut_text_and_the_changes_select_fits_at_1024(app, page):
    """J-03: at 1024px the seven Code tabs wrapped to two or three rows, and the Changes select showed "All changes in t".
    The tabs are one row that scrolls sideways, each tab whole; the select fits or ends in an ellipsis with its text in the
    tooltip."""
    page.set_viewport_size({"width": 1024, "height": 700})
    page.evaluate("""async w => { const v = await post('/api/agent', {workdir: w, kind: 'code', task: 'tabs'}); await loadThreads(); openThread(v.id); }""", app["work"])
    page.wait_for_timeout(1200)
    page.evaluate("() => togglePanel('diff', true)")
    page.wait_for_selector("#dturn")
    page.wait_for_timeout(400)
    got = page.evaluate("""() => { const t = Array.from(document.querySelectorAll('#ptabs [data-t]')), st = document.querySelector('#ptabs .pstrip'), s = document.querySelector('#dturn');
        const o = s.options[s.selectedIndex].text, cs = getComputedStyle(s);
        return {n: t.length, rows: new Set(t.map(x => Math.round(x.getBoundingClientRect().top))).size,
                cut: t.filter(x => x.scrollWidth > x.clientWidth + 1).map(x => x.textContent),
                scrolls: st.scrollWidth >= st.clientWidth && getComputedStyle(st).overflowX === 'auto',
                select: {text: o, fits: s.scrollWidth <= s.clientWidth + 1, title: s.title, ellipsis: cs.textOverflow === 'ellipsis'}}; }""")
    assert got["n"] >= 7 and got["rows"] == 1 and got["cut"] == [] and got["scrolls"], got
    assert got["select"]["fits"] or (got["select"]["title"] == got["select"]["text"] and got["select"]["ellipsis"]), got


def test_the_open_tab_is_brought_into_view_in_the_scrolling_tab_row(app, page):
    """J-03: a tab off the edge of the row is scrolled to when it opens."""
    page.set_viewport_size({"width": 1024, "height": 700})
    page.evaluate("""async w => { const v = await post('/api/agent', {workdir: w, kind: 'code', task: 'tabs'}); await loadThreads(); openThread(v.id); }""", app["work"])
    page.wait_for_timeout(1200)
    page.evaluate("() => togglePanel('preview', true)")
    page.wait_for_timeout(400)
    got = page.evaluate("""() => { const st = document.querySelector('#ptabs .pstrip').getBoundingClientRect(), on = document.querySelector('#ptabs [aria-selected="true"]').getBoundingClientRect();
        return {on: on.left >= st.left - 1 && on.right <= st.right + 1, label: document.querySelector('#ptabs [aria-selected="true"]').textContent}; }""")
    assert got["on"], got


# ---------------------------------------------------------------------------------------------------- J-04
def test_the_review_fold_is_a_quiet_note_and_its_text_reads_at_the_reply_size(app, page):
    """J-04: "First answer, before the check" was louder than the grey "Looking at the result" line (ink coloured,
    underlined, 14px) and its opened text was 12px. The summary is muted, not underlined, in the size of a note; the opened
    reply is in the body size of replies."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'make it', i: 0});
        applyEv({type: 'assistant', text: 'The first answer, written before the check.', i: 1});
        applyEv({type: 'review_round', i: 2});
        applyEv({type: 'assistant', text: 'The final answer.', i: 3});
        const col = document.querySelector('#col'), d = col.querySelector('details.notedet'), sm = d.querySelector('summary');
        const note = document.createElement('div'); note.className = 'note'; note.textContent = 'Looking at the result'; col.appendChild(note);
        d.open = true;
        const inner = d.querySelector('.msg-ai'), final = Array.from(col.querySelectorAll(':scope > .msg-ai')).pop();
        const c = e => getComputedStyle(e);
        return {sum: {color: c(sm).color, line: c(sm).textDecorationLine, size: c(sm).fontSize}, note: {color: c(note).color, size: c(note).fontSize},
                inner: c(inner).fontSize, reply: c(final).fontSize, text: sm.textContent}; }""")
    assert got["text"] == "First answer, before the check", got
    assert got["sum"]["color"] == got["note"]["color"] and got["sum"]["line"] == "none" and got["sum"]["size"] == got["note"]["size"], got
    assert got["inner"] == got["reply"] and got["reply"] == "14px", got


