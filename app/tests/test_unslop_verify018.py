"""Checks added by the independent look at the 0.0.18 design pass, driven in the real app in Chromium. Each one failed
before its fix. The rules in test_unslop018.py still hold: 4px grid, one accent, short motion."""
from __future__ import annotations

import pytest

from tests.test_ui_bugs018 import _fake_chat, app, page  # noqa: F401  (the fixtures start the real server and a browser)

pytest.importorskip("playwright.sync_api")


def test_the_step_chevron_is_centred_on_its_text(page):
    _fake_chat(page)
    off = page.evaluate("""() => {
        const d = document.createElement('details'); d.className = 'work';
        d.innerHTML = `<summary><span class="chev">${ICON.chevR}</span><span class="wl">1 step</span></summary><div class="rows"></div>`;
        document.querySelector('#col').appendChild(d);
        const s = d.querySelector('summary').getBoundingClientRect(), c = d.querySelector('.chev svg').getBoundingClientRect();
        return (c.top + c.height / 2) - (s.top + s.height / 2);
    }""")
    assert abs(off) <= 1, off


def test_four_panel_tabs_share_one_row_at_1024(page):
    page.set_viewport_size({"width": 1024, "height": 700})
    _fake_chat(page)
    got = page.evaluate("""() => { togglePanel('progress', true);
        const t = Array.from(document.querySelectorAll('#ptabs [data-t]'));
        return {n: t.length, rows: new Set(t.map(x => Math.round(x.getBoundingClientRect().top))).size}; }""")
    assert got["n"] >= 4 and got["rows"] == 1, got


def test_a_link_button_reads_as_hovered(page):
    _fake_chat(page)
    page.evaluate("""() => { const b = document.createElement('button'); b.className = 'linkbtn'; b.id = 'vlink'; b.textContent = 'Manage';
        document.querySelector('#col').appendChild(b); }""")
    snap = "() => { const cs = getComputedStyle(document.querySelector('#vlink')); return cs.color + '|' + cs.textDecorationThickness; }"
    page.mouse.move(2, 2)
    page.wait_for_timeout(300)
    before = page.evaluate(snap)
    page.hover("#vlink")
    page.wait_for_timeout(300)
    assert page.evaluate(snap) != before


def test_the_hero_links_keep_the_full_stop_beside_them(page):
    page.evaluate("""() => { const h = document.createElement('div'); h.className = 'hero'; h.id = 'vhero';
        h.innerHTML = '<p class="sub">Working in <button class="linkbtn" id="vp">Home</button>. <button class="linkbtn">Do not use a folder</button></p>';
        document.body.appendChild(h); }""")
    pad = page.evaluate("() => getComputedStyle(document.querySelector('#vp')).paddingRight")
    assert pad == "0px", pad
