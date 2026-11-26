"""Round 4 (Maya) in the web app: the chart came as two file cards, a PDF and a PNG of the same picture. One card now
shows the picture with both formats. Drives the real app in Chromium (the fixtures of tests/test_ux018.py); skipped
when no Chromium can start here."""
from __future__ import annotations

import sys
from pathlib import Path

from tests.test_round2_019 import _chat, _root
from tests.test_ux018 import _fake_chat, app, page, sync_api  # noqa: F401  (the fixtures start the real server and a browser)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))


def test_one_picture_in_two_formats_is_one_card(app, page):
    names = ["Bouquet price chart.pdf", "Bouquet price chart.png", "Bouquet price list.xlsx", "notes.pdf"]
    for n in names:
        (Path(app["work"]) / n).write_bytes(b"x")
    _chat(page, app["work"], None, "cards")
    _fake_chat(page, page.evaluate("() => S.cur.id"))
    _root(page, app["work"])
    page.evaluate("""n => { applyEv({type: 'user', text: 'a coloured chart I can print', i: 0});
        const foot = document.createElement('div'); $('#col').appendChild(foot); S.cur.tpaths = n; pvTurnCards(S.cur, foot, 0); }""", names)
    page.wait_for_selector(".pvcard")
    cards = page.evaluate("""() => [...document.querySelectorAll('.pvcard')].map(c => ({path: c.dataset.pv,
        kind: c.querySelector('small').textContent, alt: [...c.parentElement.querySelectorAll('.pvalt')].map(b => [b.textContent, b.dataset.pvdl])}))""")
    by = {c["path"]: c for c in cards}
    assert sorted(by) == ["Bouquet price chart.png", "Bouquet price list.xlsx", "notes.pdf"], cards
    chart = by["Bouquet price chart.png"]
    assert chart["kind"] == "Image · .png and .pdf", chart
    assert chart["alt"] == [["PDF", "Bouquet price chart.pdf"]], chart
    assert by["notes.pdf"]["alt"] == [] and by["notes.pdf"]["kind"] == "PDF · .pdf"
    assert page.errors == [], page.errors
