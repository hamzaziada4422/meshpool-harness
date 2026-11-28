"""Resizable panes (0.0.20): the owner could not move the sidebar, the Progress panel or the preview ("I need to be able to
move them, change the size, and not have it warp"). A handle on each edge drags, takes the arrow keys and resets on a
double-click; widths are clamped (sidebar 200 to 480 px, right pane 320 px to 60% of the window, chat 420 px at the least),
kept per pane and clamped again when the window changes. Each test drives the real app in Chromium (the fixtures of
tests/test_ux018.py) and failed before the handles existed. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from tests.test_round3_020 import _chat_view
from tests.test_ux018 import _chats, app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)

TOL = 2      # px

BOXES = """() => {
  const r = s => { const e = document.querySelector(s); if (!e || e.hidden || getComputedStyle(e).display === 'none') return null;
    const b = e.getBoundingClientRect(); return {x: b.left, w: b.width, r: b.right}; };
  const se = document.scrollingElement;
  return {vw: innerWidth, side: r('#side'), main: r('#main'), panel: r('#panel'), pvp: r('#pvp'), hs: r('#rzSide'), hr: r('#rzRight'),
    fold: document.querySelector('#app').classList.contains('autoside'),
    sideIn: innerWidth > 760 && !document.querySelector('#app').matches('.noside, .autoside'),
    hscroll: Math.max(se.scrollWidth - se.clientWidth, document.body.scrollWidth - document.body.clientWidth,
                      document.querySelector('#app').scrollWidth - document.querySelector('#app').clientWidth)};
}"""


def _boxes(page):
    return page.evaluate(BOXES)


def _right(b):
    return b["pvp"] or b["panel"]


def _size(page, width, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_timeout(350)          # the window's resize handler, and fitPanel after it settles


def _progress(page, app, width=1600, height=900):
    _size(page, width, height)
    _chat_view(page, app["work"])
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#panel:not([hidden])")
    page.wait_for_timeout(350)


def _preview(page, app, width=1600, height=900):
    _size(page, width, height)
    _chat_view(page, app["work"])
    page.evaluate("() => openPreviewCode('html', '<p>hello</p>')")
    page.wait_for_selector("#pvp:not([hidden]) .pv-frame")
    page.wait_for_timeout(350)


def _grab(page, sel):
    page.wait_for_selector(sel, state="visible")
    b = page.locator(sel).bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def _drag(page, sel, dx, steps=12):
    x, y = _grab(page, sel)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y, steps=steps)
    page.mouse.up()
    page.wait_for_timeout(60)


def _close(got, want, what=""):
    assert abs(got - want) <= TOL, (what, got, want)


def _glued(b):
    """The chat sits exactly between the panes: no gap, no overlap, no page scroll sideways."""
    side = b["side"]["w"] if b["sideIn"] else 0
    _close(b["main"]["x"], side, "chat left edge")
    r = _right(b)
    _close(b["main"]["r"], r["x"] if r else b["vw"], "chat right edge")
    assert b["hscroll"] <= 0, b


# ------------------------------------------------------------------------------------------------------ drag by 150 px
def test_dragging_the_sidebar_edge_resizes_it_by_150_px_each_way(app, page):
    _size(page, 1600)
    b = _boxes(page)
    _close(b["side"]["w"], 288, "default")
    _drag(page, "#rzSide", 150)
    b = _boxes(page)
    _close(b["side"]["w"], 438, "+150"); _glued(b)
    _drag(page, "#rzSide", -150)
    b = _boxes(page)
    _close(b["side"]["w"], 288, "-150"); _glued(b)
    _drag(page, "#rzSide", -50)
    _close(_boxes(page)["side"]["w"], 238, "-50")
    assert page.evaluate("() => JSON.parse(localStorage.getItem('mp_sw'))") == pytest.approx(238, abs=TOL)
    assert page.errors == [], page.errors


@pytest.mark.parametrize("pane", ["progress", "preview"])
def test_dragging_the_right_edge_resizes_progress_and_the_preview_by_150_px_each_way(app, page, pane):
    (_progress if pane == "progress" else _preview)(page, app)
    b = _boxes(page)
    w0 = _right(b)["w"]
    _close(w0, 460 if pane == "progress" else 640, "default at 1600")
    _drag(page, "#rzRight", -150)               # the edge moves left: the pane gets wider
    b = _boxes(page)
    _close(_right(b)["w"], w0 + 150, "+150"); _glued(b)
    _drag(page, "#rzRight", 150)
    b = _boxes(page)
    _close(_right(b)["w"], w0, "-150"); _glued(b)
    _drag(page, "#rzRight", 100)
    b = _boxes(page)
    _close(_right(b)["w"], w0 - 100, "narrower than the default"); _glued(b)
    key = "mp_pw" if pane == "progress" else "mp_pvw"
    assert page.evaluate(f"() => JSON.parse(localStorage.getItem('{key}'))") == pytest.approx(w0 - 100, abs=TOL)
    assert page.evaluate("() => JSON.parse(localStorage.getItem('mp_sw'))") is None      # the other panes keep theirs
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ clamps
def test_the_widths_stop_at_their_bounds_at_both_ends(app, page):
    _progress(page, app)
    _drag(page, "#rzSide", -400)
    _close(_boxes(page)["side"]["w"], 200, "sidebar narrowest")
    _drag(page, "#rzSide", 600)
    b = _boxes(page)
    _close(b["side"]["w"], 480, "sidebar widest"); _glued(b)
    _drag(page, "#rzRight", 700)
    _close(_boxes(page)["panel"]["w"], 320, "panel narrowest")
    _drag(page, "#rzRight", -1400)
    b = _boxes(page)
    _close(b["main"]["w"], 420, "the chat keeps 420 px"); _glued(b)         # 1600 - 480 - 700: the chat is the limit here
    _close(b["panel"]["w"], 1600 - 480 - 420, "panel widest beside a wide sidebar")
    # with the sidebar hidden, 60% of the window is the limit
    page.evaluate("() => toggleSide()")
    page.wait_for_timeout(400)
    _drag(page, "#rzRight", -1400)
    b = _boxes(page)
    _close(b["panel"]["w"], 960, "panel widest: 60% of 1600"); _glued(b)
    assert b["main"]["w"] >= 420 - TOL, b
    # and the sidebar, back, gives way to the chat instead of squeezing it
    page.evaluate("() => toggleSide()")
    page.wait_for_timeout(400)
    b = _boxes(page)
    assert b["main"]["w"] >= 420 - TOL and b["side"]["w"] >= 200 - TOL and b["panel"]["w"] >= 320 - TOL, b
    _close(b["side"]["w"] + b["main"]["w"] + b["panel"]["w"], 1600, "the three fill the window"); _glued(b)
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ kept
def test_the_widths_come_back_after_a_reload(app, page):
    _progress(page, app)
    _drag(page, "#rzSide", 150)
    _drag(page, "#rzRight", -150)
    page.evaluate("() => closePreview()")
    page.reload()
    page.wait_for_selector("#newThread", state="attached", timeout=30000)
    page.wait_for_function("() => typeof S === 'object' && S.threads", timeout=30000)
    page.wait_for_timeout(400)
    _close(_boxes(page)["side"]["w"], 438, "sidebar after reload")
    _chat_view(page, app["work"])
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#panel:not([hidden])")
    page.wait_for_timeout(350)
    b = _boxes(page)
    _close(b["side"]["w"], 438, "sidebar"); _close(b["panel"]["w"], 610, "panel after reload"); _glued(b)
    assert page.errors == [], page.errors


def test_a_smaller_window_clamps_the_widths_again_and_a_larger_one_brings_them_back(app, page):
    _progress(page, app)
    _drag(page, "#rzSide", 150)                 # 438
    _drag(page, "#rzRight", -300)               # 760, less what the chat needs: 1600 - 438 - 420 = 742
    b = _boxes(page)
    side, panel = b["side"]["w"], b["panel"]["w"]
    _close(side, 438); _close(panel, 742); _glued(b)
    _size(page, 1100)
    b = _boxes(page)
    assert b["fold"], b                         # at 1100 a panel folds the sidebar, as before (the default rule)
    _close(b["panel"]["w"], 660, "60% of 1100"); _glued(b)
    assert b["main"]["w"] >= 420 - TOL
    page.evaluate("() => toggleSide()")         # the person keeps the sidebar: both give way so the chat keeps 420
    page.wait_for_timeout(400)
    b = _boxes(page)
    _close(b["panel"]["w"], 320, "panel at its least"); _close(b["side"]["w"], 1100 - 320 - 420, "sidebar gives the rest")
    _close(b["main"]["w"], 420, "chat"); _glued(b)
    # the stored widths are the ones the person chose: a larger window shows them again
    _size(page, 1600)
    b = _boxes(page)
    _close(b["side"]["w"], side, "sidebar back"); _close(b["panel"]["w"], panel, "panel back"); _glued(b)
    # every frame of a resize, not only once it settles: no sideways scroll at any size on the way
    for w in (1500, 1300, 1150, 1000, 990):
        page.set_viewport_size({"width": w, "height": 900})
        # the first frame drawn at the new size: the browser runs the resize handler before any frame callback, but
        # set_viewport_size can return before that frame, and a loaded machine measured the old widths (30 px)
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => r()))")
        assert _boxes(page)["hscroll"] <= 0, w
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ no warping
def test_nothing_in_the_chat_moves_but_its_width_while_a_handle_is_dragged(app, page):
    ids = _chats(app, page, 1)
    _size(page, 1920, 1080)            # the chat stays wider than its 656px text column all through: no reflow to tell apart
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_selector("#col .msg-ai")
    page.wait_for_function("() => !document.querySelector('#col .rv')", timeout=30000)     # the reply has finished easing in
    page.wait_for_timeout(300)
    page.evaluate("() => togglePanel('progress', true)")
    page.wait_for_selector("#panel:not([hidden])")
    page.wait_for_timeout(500)
    page.evaluate("() => { const c = document.querySelector('#conv'); c.scrollTop = Math.floor((c.scrollHeight - c.clientHeight) / 2); }")
    page.wait_for_timeout(200)
    probe = """() => { const c = document.querySelector('#conv'), col = document.querySelector('#col'), cb = col.getBoundingClientRect();
      return {top: [...col.children].slice(0, 40).map(e => { const b = e.getBoundingClientRect(); return [Math.round(b.top * 10) / 10, Math.round(b.height * 10) / 10]; }),
        scroll: c.scrollTop, colw: cb.width, centred: Math.abs((cb.left + cb.right) / 2 - (c.getBoundingClientRect().left + c.clientWidth / 2)),
        anims: document.getAnimations().filter(a => a.playState === 'running' && !(a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('.pane-rz'))).map(a => [a.id, a.animationName || a.transitionProperty || '', a.effect && a.effect.target ? a.effect.target.className : '']),
        panel: !document.querySelector('#panel').hidden, drag: document.querySelector('#app').classList.contains('pane-drag')}; }"""
    for sel, dx in (("#rzRight", -150), ("#rzSide", 120), ("#rzRight", 140), ("#rzSide", -120)):
        before = page.evaluate(probe)
        w0 = _boxes(page)["panel" if sel == "#rzRight" else "side"]["w"]
        x, y = _grab(page, sel)
        page.mouse.move(x, y)
        page.mouse.down()
        for i in range(1, 11):
            page.mouse.move(x + dx * i / 10, y)
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            now = page.evaluate(probe)
            b = _boxes(page)
            assert now["drag"] and now["panel"] and not b["fold"], (sel, i, now, b)      # no collapse, no fold
            _glued(b)
            assert now["top"] == before["top"], (sel, i)                   # no message moved up or down or changed height
            assert now["scroll"] == before["scroll"], (sel, i, now["scroll"], before["scroll"])
            assert now["colw"] == before["colw"], (sel, i)                 # the text column keeps its width here: no reflow
            assert now["centred"] <= 1, (sel, i, now)
            assert now["anims"] == before["anims"], (sel, i, now)                        # nothing glides or flips during a drag
            moved = dx * i / 10
            _close(b["panel"]["w"] if sel == "#rzRight" else b["side"]["w"], w0 - moved if sel == "#rzRight" else w0 + moved,
                   "the pane follows the pointer on every frame, no jump")
            edge, handle = (b["main"]["r"], b["hr"]) if sel == "#rzRight" else (b["side"]["w"] - 1, b["hs"])
            _close(handle["x"], edge, "the handle starts on the edge")
        page.mouse.up()
        page.wait_for_timeout(60)
    assert page.errors == [], page.errors


# The reading position: the deepest block (a paragraph, a list item) at the top of the chat, and how far into it the top is.
READING = """() => { const c = document.querySelector('#conv'), top = c.getBoundingClientRect().top;
  let el = null;
  for (let kids = document.querySelector('#col').children, d = 0; kids.length && d < 8; d++) {
    let hit = null;
    for (const k of kids) { const r = k.getBoundingClientRect(); if (r.height && r.bottom > top && !/^inline/.test(getComputedStyle(k).display)) { hit = k; break; } }
    if (!hit) break; el = hit; kids = hit.children; }
  if (!window._readEl) window._readEl = el;
  const r = window._readEl.getBoundingClientRect();
  return {same: el === window._readEl, tag: el.tagName, frac: (top - r.top) / r.height, h: r.height, text: el.textContent.slice(0, 40),
          colw: document.querySelector('#col').getBoundingClientRect().width}; }"""


def _reading_chat(app, page, width, panel=True):
    ids = _chats(app, page, 1)
    _size(page, width, 800)
    page.evaluate("id => openThread(id)", ids[0])
    page.wait_for_selector("#col .msg-ai")
    page.wait_for_function("() => !document.querySelector('#col .rv')", timeout=30000)
    page.wait_for_timeout(300)
    if panel:
        page.evaluate("() => togglePanel('progress', true)")
        page.wait_for_selector("#panel:not([hidden])")
        page.wait_for_timeout(500)
    # a place in the middle of the reply, a few lines into a paragraph
    page.evaluate("""() => { const c = document.querySelector('#conv'), ps = [...document.querySelectorAll('#col .msg-ai p')];
        const p = ps[Math.floor(ps.length / 2)], r = p.getBoundingClientRect();
        c.scrollTop += r.top - c.getBoundingClientRect().top + Math.min(40, r.height / 3); window._readEl = null; }""")
    page.wait_for_timeout(250)
    return page.evaluate(READING)


def test_a_drag_that_wraps_the_text_again_keeps_the_line_being_read_at_the_top(app, page):
    """T3 #322: a width change made the chat jump. The chat here is narrower than its text column, so every frame of the
    drag wraps the text again; the paragraph being read stays at the top, at the same place in it."""
    start = _reading_chat(app, page, 1366)
    assert start["same"] and start["tag"] in ("P", "LI"), start
    widths = set()
    for sel, dx in (("#rzRight", -150), ("#rzRight", 150), ("#rzSide", 80), ("#rzSide", -80)):
        x, y = _grab(page, sel)
        page.mouse.move(x, y)
        page.mouse.down()
        for i in range(1, 9):
            page.mouse.move(x + dx * i / 8, y)
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            now = page.evaluate(READING)
            widths.add(round(now["colw"]))
            assert now["same"], (sel, i, start, now)                       # the same paragraph is at the top
            assert abs(now["frac"] - start["frac"]) * now["h"] <= 3, (sel, i, start, now)      # the same place in it, to a few px
        page.mouse.up()
        page.wait_for_timeout(60)
    assert len(widths) > 4, widths                                  # the text column did change width: the text wrapped again
    assert page.errors == [], page.errors


def test_a_window_resize_keeps_the_line_being_read_at_the_top(app, page):
    """T3 #322: resizing the window made the chat jump. A sidebar dragged wide is clamped again as the window narrows and
    the text wraps again; the paragraph being read stays at the top."""
    _size(page, 1600, 800)
    _drag(page, "#rzSide", 160)                                     # 448
    start = _reading_chat(app, page, 1600, panel=False)
    assert start["same"], start
    widths = set()
    for w in (1400, 1200, 1050, 900, 1000, 1300, 1600):
        page.set_viewport_size({"width": w, "height": 800})
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        now = page.evaluate(READING)
        widths.add(round(now["colw"]))
        assert now["same"], (w, start, now)
        assert abs(now["frac"] - start["frac"]) * now["h"] <= 3, (w, start, now)
        assert _boxes(page)["hscroll"] <= 0, w
    assert len(widths) > 1, widths                                  # the text did wrap again on the way
    _close(_boxes(page)["side"]["w"], 448, "the dragged sidebar is back at 1600")
    assert page.errors == [], page.errors


def test_the_handles_stay_clear_of_the_scrollbars_and_out_of_the_scrolling_chat(app, page):
    """T3 #958 and #42836: a drag rail took the chat's scrolling, or covered its scrollbar."""
    _reading_chat(app, page, 1366)
    page.evaluate("() => { const l = document.querySelector('#sideList'); for (let i = 0; i < 60; i++) { const d = document.createElement('div'); d.style.height = '24px'; d.textContent = 'row ' + i; l.appendChild(d); } }")
    got = page.evaluate("""() => { const c = document.querySelector('#conv'), cr = c.getBoundingClientRect(), l = document.querySelector('#sideList'), lr = l.getBoundingClientRect();
        const hs = document.querySelector('#rzSide'), hr = document.querySelector('#rzRight'), a = hs.getBoundingClientRect(), b = hr.getBoundingClientRect(), y = cr.top + cr.height / 2;
        return {inside: [hs, hr].some(h => c.contains(h) || l.contains(h) || h.closest('#main, #side, #panel, #pvp')),
          convBar: cr.right - cr.left - c.clientWidth, listBar: lr.width - l.clientWidth,
          hrLeft: b.left, convRight: cr.right, hsLeft: a.left, listRight: lr.right,
          atConvBar: document.elementFromPoint(cr.right - 3, y).closest('#conv') === c,
          atListBar: document.elementFromPoint(lr.right - 3, lr.top + lr.height / 2).closest('#sideList') === l}; }""")
    assert not got["inside"], got                                    # the handles are not in a scrolling box
    assert got["convBar"] > 0 and got["listBar"] > 0, got           # both have a scrollbar here
    assert got["hrLeft"] >= got["convRight"] - 0.5, got             # the right handle starts where the chat's scrollbar ends
    assert got["hsLeft"] >= got["listRight"] - 0.5, got             # the sidebar handle starts where the list's scrollbar ends
    assert got["atConvBar"] and got["atListBar"], got               # a click on either scrollbar reaches the scrollbar
    # the wheel right next to each handle still scrolls the chat and the list
    for sel, box in (("#conv", "#conv"), ("#sideList", "#sideList")):
        r = page.locator(box).bounding_box()
        before = page.evaluate(f"() => document.querySelector('{sel}').scrollTop")
        page.mouse.move(r["x"] + r["width"] - 4, r["y"] + r["height"] / 2)
        page.mouse.wheel(0, 200 if sel == "#sideList" else -200)
        page.wait_for_timeout(300)
        assert page.evaluate(f"() => document.querySelector('{sel}').scrollTop") != before, sel
    assert page.errors == [], page.errors


def _png(path: Path, w: int, h: int) -> None:
    raw = (b"\x00" + bytes(v for x in range(w) for v in (x * 255 // w, 90, 120))) * h       # a left-to-right ramp, RGB

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def test_a_picture_in_the_preview_keeps_its_shape_while_the_preview_is_resized(app, page):
    _png(Path(app["work"]) / "wide-chart.png", 1500, 900)
    _size(page, 1600)
    _chat_view(page, app["work"])
    page.evaluate("w => openPreview(w, 'wide-chart.png')", app["work"])
    page.wait_for_function("() => { const i = document.querySelector('#pvStage .pv-img img'); return i && i.complete && i.naturalWidth > 0; }")
    page.wait_for_timeout(300)
    shape = """() => { const i = document.querySelector('#pvStage .pv-img img'), b = i.getBoundingClientRect(), s = document.querySelector('#pvStage').getBoundingClientRect();
      return {ratio: b.width / b.height, natural: i.naturalWidth / i.naturalHeight, w: b.width, stage: s.width}; }"""
    first = page.evaluate(shape)
    assert first["w"] < 1500 and abs(first["ratio"] - first["natural"]) < 0.01, first       # scaled down to fit, not stretched
    widths = [first["w"]]
    for dx in (-150, 300, -250):
        x, y = _grab(page, "#rzRight")
        page.mouse.move(x, y)
        page.mouse.down()
        for i in range(1, 7):
            page.mouse.move(x + dx * i / 6, y)
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            s = page.evaluate(shape)
            assert abs(s["ratio"] - s["natural"]) < 0.01, (dx, i, s)                          # mid-drag too
            assert s["w"] <= s["stage"] + 0.5, (dx, i, s)
        page.mouse.up()
        page.wait_for_timeout(60)
        s = page.evaluate(shape)
        assert abs(s["ratio"] - s["natural"]) < 0.01, (dx, s)
        widths.append(s["w"])
    assert len({round(w) for w in widths}) == len(widths), widths                              # it scaled with the pane each time
    assert page.errors == [], page.errors


def test_a_page_preview_frame_lets_the_drag_through_and_fills_the_pane(app, page):
    _preview(page, app)
    frame = "() => { const f = document.querySelector('#pvStage .pv-frame'), b = f.getBoundingClientRect(), s = document.querySelector('#pvStage').getBoundingClientRect(); return {pe: getComputedStyle(f).pointerEvents, w: b.width, h: b.height, sw: s.width}; }"
    assert page.evaluate(frame)["pe"] == "auto"
    w0 = _boxes(page)["pvp"]["w"]
    x, y = _grab(page, "#rzRight")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 20, y)
    got = page.evaluate(frame)
    assert got["pe"] == "none", got                    # the frame does not take the pointer while the drag runs
    page.mouse.move(x + 200, y, steps=10)             # the pointer now sits over the page in the frame
    _close(_boxes(page)["pvp"]["w"], w0 - 200, "followed the pointer over the frame")
    page.mouse.up()
    page.wait_for_timeout(60)
    got = page.evaluate(frame)
    assert got["pe"] == "auto", got
    _close(got["w"], got["sw"], "the frame fills the pane's width")
    _close(_boxes(page)["pvp"]["w"], w0 - 200)
    assert page.errors == [], page.errors


# ------------------------------------------------------------------------------------------------------ reset, keys, look
def test_a_double_click_on_a_handle_brings_back_the_default_width(app, page):
    _progress(page, app)
    _drag(page, "#rzSide", 150)
    _drag(page, "#rzRight", -150)
    b = _boxes(page)
    _close(b["side"]["w"], 438); _close(b["panel"]["w"], 610)
    page.dblclick("#rzSide")
    page.wait_for_timeout(60)
    _close(_boxes(page)["side"]["w"], 288, "sidebar default")
    page.dblclick("#rzRight")
    page.wait_for_timeout(60)
    b = _boxes(page)
    _close(b["panel"]["w"], 460, "panel default"); _glued(b)
    assert page.evaluate("() => [localStorage.getItem('mp_sw'), localStorage.getItem('mp_pw')]") == [None, None]
    assert page.errors == [], page.errors


def test_the_arrow_keys_move_a_focused_handle(app, page):
    _progress(page, app)
    page.focus("#rzSide")
    page.keyboard.press("ArrowRight")
    _close(_boxes(page)["side"]["w"], 304, "ArrowRight")
    page.keyboard.press("Shift+ArrowRight")
    _close(_boxes(page)["side"]["w"], 368, "Shift+ArrowRight")
    page.keyboard.press("ArrowLeft")
    _close(_boxes(page)["side"]["w"], 352, "ArrowLeft")
    assert page.get_attribute("#rzSide", "aria-valuenow") == "352"
    page.keyboard.press("Home")
    _close(_boxes(page)["side"]["w"], 200, "Home")
    page.keyboard.press("End")
    _close(_boxes(page)["side"]["w"], 480, "End")
    page.keyboard.press("Enter")
    _close(_boxes(page)["side"]["w"], 288, "Enter resets")
    page.focus("#rzRight")
    page.keyboard.press("ArrowLeft")                     # the edge moves left: the panel grows
    _close(_boxes(page)["panel"]["w"], 476, "ArrowLeft on the right edge")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    b = _boxes(page)
    _close(b["panel"]["w"], 444, "ArrowRight twice"); _glued(b)
    assert page.evaluate("() => JSON.parse(localStorage.getItem('mp_pw'))") == 444
    assert page.errors == [], page.errors


def test_the_handles_are_easy_to_find_and_named(app, page):
    _progress(page, app)
    for sel, label in (("#rzSide", "Resize the sidebar"), ("#rzRight", "Resize the side panel")):
        got = page.evaluate("""s => { const h = document.querySelector(s), b = h.getBoundingClientRect(), cs = getComputedStyle(h);
            return {role: h.getAttribute('role'), label: h.getAttribute('aria-label'), orient: h.getAttribute('aria-orientation'),
              tab: h.tabIndex, w: b.width, cursor: cs.cursor, line: getComputedStyle(h, '::after').backgroundColor}; }""", sel)
        assert got["role"] == "separator" and got["label"] == label and got["orient"] == "vertical" and got["tab"] == 0, got
        assert 6 <= got["w"] <= 8 and got["cursor"] == "col-resize", got
        x, y = _grab(page, sel)
        page.mouse.move(x, y)
        page.wait_for_timeout(250)
        hover = page.evaluate("s => getComputedStyle(document.querySelector(s), '::after').backgroundColor", sel)
        assert hover != got["line"] and hover not in ("transparent", "rgba(0, 0, 0, 0)"), (sel, got, hover)
        page.mouse.move(800, 300)
    page.evaluate("() => { togglePanel('progress', false); openPreviewCode('html', '<p>hi</p>'); }")
    page.wait_for_selector("#pvp:not([hidden])")
    page.wait_for_timeout(300)
    assert page.get_attribute("#rzRight", "aria-label") == "Resize the preview"
    assert page.get_attribute("#rzRight", "aria-controls") == "pvp"
    assert page.errors == [], page.errors


@pytest.mark.parametrize("width", [1366, 1024, 900, 700])
def test_the_breakpoints_keep_their_layout_with_the_handles(app, page, width):
    _progress(page, app, width, 768)
    b = _boxes(page)
    if width == 1366:                                # the sidebar stays open beside a panel, both edges have a handle
        assert not b["fold"] and b["hs"] and b["hr"], b
        assert b["main"]["w"] >= 656, b
        _drag(page, "#rzRight", -150)
        b = _boxes(page)
        assert not b["fold"] and b["main"]["w"] >= 420 - TOL, b
        _glued(b)
    elif width == 1024:                              # usable: the panel is docked and resizable, the chat keeps its room
        assert b["hr"] and b["main"]["w"] >= 560, b
        _drag(page, "#rzRight", -400)
        b = _boxes(page)
        side = 0 if b["fold"] else b["side"]["w"]
        _close(b["panel"]["w"], min(614, 1024 - side - 420), "1024: 60% of the window or what the chat leaves")
        assert b["main"]["w"] >= 420 - TOL, b
        _glued(b)
    else:                                            # an overlay as before, with no handle on it
        assert b["hr"] is None, b
        assert getComputedStyle_position(page, "#panel") == "fixed"
        if width <= 760:
            assert b["hs"] is None, b
        # the handles are in the page, only put away: nothing on an overlay is draggable (the panel had its own strip before)
        got = page.evaluate("""() => ({there: !!document.querySelector('#rzSide') && !!document.querySelector('#rzRight'),
            shown: [...document.querySelectorAll('.pane-rz, .rz, [role=separator]')].filter(e => e.offsetParent).map(e => e.id || e.className)})""")
        assert got["there"], got
        assert got["shown"] == ([] if width <= 760 or b["fold"] else ["rzSide"]), (got, b)    # at most the docked sidebar's
        if width <= 760:                             # and a window back over 980 px brings the handle with the docked panel
            _size(page, 1280, 768)
            assert _boxes(page)["hr"], "no handle once the panel docks again"
    assert b["hscroll"] <= 0, b
    assert page.errors == [], page.errors


def getComputedStyle_position(page, sel):
    return page.evaluate("s => getComputedStyle(document.querySelector(s)).position", sel)
