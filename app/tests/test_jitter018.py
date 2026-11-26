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


@pytest.mark.parametrize("width,height", [(1366, 768), (1280, 800), (1024, 700)])
def test_the_chat_glides_when_the_file_preview_opens_or_closes(app, page, width, height):
    """The file preview did not go through the glide: the chat and the message box snapped 300 to 460 px in one frame
    (layout shift 0.43 to 0.46) on every open and close. It moves like the side panel now."""
    ids = _chats(app, page, 1)
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_timeout(1500)
    page.evaluate(CLS)
    got = {"preview open": _shift(page, "() => pvShell('notes.txt', 'Text')"),
           "preview close": _shift(page, "() => closePreview()")}
    # at 1024 there is no room for the preview beside a full-width chat, so the text wraps again (narrower): that part
    # is a real change of shape a glide cannot hide; it was 0.45 before. 1366 and 1280 keep the width and only move.
    limit = 0.2 if width <= 1024 else 0.02
    assert all(v < limit for v in got.values()), got
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


# ---------------------------------------------------------------------------------------------------- J-05
def test_the_dark_settings_modal_stands_off_its_scrim_without_its_border(app, page):
    """J-05: in dark the settings modal (#0f0f10) was about 1.03 times as light as the page behind the scrim, so its edge
    read only through the 8% border. It is a step up the ramp now: at least 1.1 times as light as the dimmed page."""
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; openSettings('appearance'); }")
    page.wait_for_selector(".modal")
    page.wait_for_timeout(500)
    got = page.evaluate("""() => { const m = getComputedStyle(document.querySelector('.modal')).backgroundColor, s = getComputedStyle(document.querySelector('.scrim')).backgroundColor;
        return {modal: m, scrim: s, pages: ['#main', '.side'].map(q => getComputedStyle(document.querySelector(q)).backgroundColor)}; }""")
    a = float(got["scrim"].split(",")[3].strip(" )")) if got["scrim"].startswith("rgba") else 1.0
    lm = _lum(got["modal"])
    ratios = []
    for p in got["pages"]:
        under = [float(x) * (1 - a) for x in p[p.index("(") + 1:p.index(")")].split(",")[:3]]
        ratios.append((lm + 0.05) / (_lum("rgb(%f,%f,%f)" % tuple(under)) + 0.05))
    assert min(ratios) >= 1.1, (got, ratios)


# ---------------------------------------------------------------------------------------------------- J-06
CONTRAST = """(() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const a = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return {r: a[0], g: a[1], b: a[2], a: a[3] === undefined ? 1 : a[3]}; };
  const lum = ({r, g, b}) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const over = (t, b) => ({r: t.r * t.a + b.r * (1 - t.a), g: t.g * t.a + b.g * (1 - t.a), b: t.b * t.a + b.b * (1 - t.a), a: 1});
  const bgOf = el => { const st = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { st.push(c); if (c.a >= 1) break; } } let base = {r: 255, g: 255, b: 255, a: 1}; for (let i = st.length - 1; i >= 0; i--) base = over(st[i], base); return base; };
  window.__low = root => { const out = [], w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), seen = new Set(); let n;
    while (n = w.nextNode()) { if (!n.nodeValue.trim()) continue; const el = n.parentElement; if (!el || seen.has(el)) continue; seen.add(el); const cs = getComputedStyle(el), r = el.getBoundingClientRect();
      if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none') continue; let o = 1; for (let e = el; e; e = e.parentElement) o *= +getComputedStyle(e).opacity; if (o < 0.5) continue;
      const bg = bgOf(el), fg = over(parse(cs.color), bg), L1 = lum(fg), L2 = lum(bg), c = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      if (c < 4.5) out.push([n.nodeValue.trim().slice(0, 30), +c.toFixed(2)]); } return out; };
})()"""


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width,height", [(1280, 800), (1024, 700)])
def test_onboarding_steps_2_and_3_are_readable_in_both_themes(app, page, theme, width, height):
    """J-06: light steps 2 and 3, and dark at 1280, were not checked. Text reaches 4.5:1 on both steps in both themes, and
    on step 3 the Start button is on screen at 1024x700 and 1280x800 with a tall list of things to bring."""
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    page.evaluate(CONTRAST)
    page.evaluate("() => { permStep(); }")
    page.wait_for_selector("#onbPerm:not([hidden])")
    page.wait_for_timeout(600)
    assert page.evaluate("() => __low(document.querySelector('#onb'))") == []
    page.click("#oDone")
    page.wait_for_selector("#onbSetup")
    page.wait_for_timeout(800)
    page.evaluate("""() => { const b = document.createElement('div'); b.id = 'tallimport'; b.style.cssText = 'height:1400px'; b.textContent = 'Agents on this computer';
        document.querySelector('#oImport').appendChild(b); }""")
    page.wait_for_timeout(200)
    assert page.evaluate("() => __low(document.querySelector('#onb'))") == []
    btn = page.evaluate("() => { const r = document.querySelector('#oFinish').getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; }")
    assert btn[0] >= 0 and btn[1] <= btn[2], btn
    page.evaluate("() => { document.querySelector('#onb').remove(); }")


def test_onboarding_step_3_buttons_are_reachable_without_scrolling_and_cover_no_field(app, page):
    """J-06: step 3's Start button sat about 800px below the fold at 1024x700. The fields scroll in their own area and the
    button stays under them: on screen at once, and whatever the scroll it overlaps none of the fields."""
    page.set_viewport_size({"width": 1024, "height": 700})
    page.evaluate("() => { permStep(); }")
    page.wait_for_selector("#onbPerm:not([hidden])")
    page.click("#oDone")
    page.wait_for_selector("#onbSetup")
    page.wait_for_timeout(600)
    page.evaluate("""() => { const b = document.querySelector('#onbSetup .onb-body'); for (let i = 0; i < 12; i++) { const s = document.createElement('section'); s.className = 'tallrow';
        s.style.cssText = 'height:96px;margin:8px 0;border:1px solid var(--line)'; s.textContent = 'Item ' + i; b.insertBefore(s, b.querySelector('#oLocalRow')); } }""")
    page.wait_for_timeout(200)
    got = page.evaluate("""() => { const bd = document.querySelector('#onbSetup .onb-body'), go = document.querySelector('#onbSetup .onb-go').getBoundingClientRect(), hits = [], seen = [];
        const btn0 = document.querySelector('#oFinish').getBoundingClientRect();
        for (const top of [0, 300, 700, 100000]) { bd.scrollTop = top; const g = document.querySelector('#onbSetup .onb-go').getBoundingClientRect();
          for (const el of bd.querySelectorAll('.tallrow')) { const r = el.getBoundingClientRect(), bb = bd.getBoundingClientRect();
            const vis = Math.min(r.bottom, bb.bottom) - Math.max(r.top, bb.top) > 0;
            if (vis && g.top < r.bottom && g.bottom > r.top && Math.min(r.bottom, bb.bottom) > g.top) hits.push([top, el.textContent]); } }
        bd.scrollTop = 100000; const last = [...bd.querySelectorAll('.tallrow')].pop(), lr = last.getBoundingClientRect();
        return {btn: [Math.round(btn0.top), Math.round(btn0.bottom)], inner: innerHeight, hits, bodyBottom: Math.round(bd.getBoundingClientRect().bottom), goTop: Math.round(go.top), lastBottom: Math.round(lr.bottom)}; }""")
    assert got["btn"][0] >= 0 and got["btn"][1] <= got["inner"], got
    assert got["hits"] == [], got
    assert got["lastBottom"] <= got["goTop"] + 1, got
