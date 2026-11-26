"""Web UI items left over from the 0.0.18 ledger (IDs LQ-04 and LQ-05). Each test drives the real app
(python -m meshpool_harness ui) in Chromium, as test_ui_bugs018.py does. No model is called. Skipped when no Chromium
can start here (outside CI)."""
from __future__ import annotations

import re

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_ui_bugs018 import _launch, app, page  # noqa: E402,F401  (fixtures: the app, a page on it)

_HIDE = """h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => h ? 'hidden' : 'visible'});
    document.dispatchEvent(new Event('visibilitychange')); }"""


# ---------------------------------------------------------------------------------------------------- LQ-04
def test_lq04_the_update_status_poll_stops_while_the_tab_is_hidden(app):  # noqa: F811
    """LQ-04: updStatus asked /api/update/status every 60 s for as long as the page was open, hidden tab included.
    It now stops while the tab is hidden and asks again at once when the tab comes back."""
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.set_default_timeout(15000)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.clock.install()                               # page time that the test moves on
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); "
                           "} catch (e) {}")
        hits = []
        pg.on("request", lambda r: hits.append(r.url) if re.search(r"/api/update/status$", r.url) else None)
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.clock.run_for(5000)                           # the start-up checks (1 s and 3 s) are done
        pg.wait_for_timeout(1500)
        assert hits, "the page never asked for the update status"
        pg.evaluate(_HIDE, True)
        pg.wait_for_timeout(300)
        hits.clear()
        for _ in range(5):                               # five minutes in a hidden tab
            pg.clock.run_for(60000)
            pg.wait_for_timeout(300)
        assert hits == [], f"{len(hits)} update status requests in 5 minutes while hidden"
        with pg.expect_request(re.compile(r"/api/update/status$"), timeout=5000):
            pg.evaluate(_HIDE, False)                    # back to the tab: asked at once
        pg.wait_for_timeout(500)
        hits.clear()
        pg.clock.run_for(61000)
        pg.wait_for_timeout(800)
        assert len(hits) == 1, f"{len(hits)} requests in the minute after the tab came back"
        assert errors == [], errors
        b.close()


# ---------------------------------------------------------------------------------------------------- LQ-05
def test_lq05_an_insight_card_whose_id_has_a_quote_still_shows_once(page):  # noqa: F811
    """LQ-05: insightCard010 built `.nudge010[data-id="..."]` from the card id without escaping it. An id with a quote
    made querySelector throw, so the card never showed and the event handler stopped there."""
    page.route(re.compile(r".*/api/insights/act$"), lambda route, request: route.fulfill(json={"ok": True}))
    card = {"id": 'ins_a"b] c\\d', "type": "tip", "title": "Try the quote card", "body": "b"}
    got = page.evaluate("""c => { const out = [];
        for (let i = 0; i < 2; i++) { try { insightCard010(Object.assign({}, c)); out.push('ok'); } catch (e) { out.push(String(e)); } }
        return out; }""", card)
    assert got == ["ok", "ok"], got
    page.wait_for_selector(".nudge010")
    assert page.evaluate("() => $$('.nudge010').length") == 1   # the second call saw the first card
    assert page.evaluate("() => $('.nudge010').dataset.id") == card["id"]
    assert page.errors == [], page.errors
