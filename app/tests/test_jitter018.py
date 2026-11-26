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


