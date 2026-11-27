"""Omar (developer persona), round 3, the web app: the tip card has a "Never show tips" link that turns the setting
off, and the Session tab folds the connector list and says what "Working as" means. Drives the real app in Chromium."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_ui_persona019 import _chat  # noqa: E402
from tests.test_ux018 import app, page, sync_api  # noqa: E402,F401  (the fixtures start the real server and a browser)
from tests.uiwait import wait_js  # noqa: E402


def _tip(page, id_="tip-x"):
    page.evaluate("""id => insightCard010({id, title: 'Tip:', body: 'turn on Plan first', action: {label: 'Try it'}, local: () => {}})""", id_)


def test_6_a_tip_card_has_a_never_show_tips_link_that_turns_tips_off(app, page):
    _chat(page, app["work"], "code", "tips")
    page.wait_for_function("() => S.cur && S.cur.view")
    page.evaluate("() => { S.cur.busy = false; }")
    _tip(page, "tip-a")
    page.wait_for_selector(".nudge010")
    link = page.locator(".nudge010 .n-never")
    assert link.inner_text() == "Never show tips"
    link.click()
    page.wait_for_selector(".nudge010", state="detached")
    # the card goes at once and the setting is saved after it (a POST): wait for the save itself, not a guessed time
    page.wait_for_function("() => (S.caps || []).some(f => f.id === 'tips' && f.on === false)")
    wait_js(page, "async () => (await api('/api/capabilities')).features.some(f => f.id === 'tips' && f.on === false)", timeout=15)
    caps = page.evaluate("async () => (await api('/api/capabilities')).features")
    tips = next(f for f in caps if f["id"] == "tips")
    assert tips["name"] == "Show tips" and tips["on"] is False, tips
    _tip(page, "tip-b")                                       # off: no tip card is drawn again
    page.wait_for_timeout(400)
    assert page.locator(".nudge010").count() == 0
    page.evaluate("async () => { await post('/api/capabilities', {id: 'tips', on: true}); await loadCaps(); }")   # back on
    _tip(page, "tip-c")
    page.wait_for_selector(".nudge010")
    assert page.errors == [], page.errors


def test_6_a_tip_that_comes_while_never_show_tips_is_saving_is_not_drawn(app, page):
    """"Never show tips" removed the card at once but turned the setting off on the page only when the save answered:
    a tip that came in between (a slow disk, a busy computer) was drawn right after the person said never."""
    _chat(page, app["work"], "code", "tips while saving")
    page.wait_for_function("() => S.cur && S.cur.view")
    page.evaluate("() => { S.cur.busy = false; }")
    held = []
    page.route("**/api/capabilities", lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
    try:
        _tip(page, "tip-save-a")
        page.wait_for_selector(".nudge010")
        page.locator(".nudge010 .n-never").click()
        page.wait_for_selector(".nudge010", state="detached")
        for _ in range(300):                           # the save reaches the route above (it is held there)
            if held:
                break
            page.wait_for_timeout(50)
        assert len(held) == 1, held
        _tip(page, "tip-save-b")                       # arrives while the save is still on its way
        page.wait_for_timeout(300)
        assert page.locator(".nudge010").count() == 0
    finally:
        for r in held:
            r.continue_()
        page.unroute("**/api/capabilities")
    wait_js(page, "async () => (await api('/api/capabilities')).features.some(f => f.id === 'tips' && f.on === false)", timeout=15)
    page.evaluate("async () => { await post('/api/capabilities', {id: 'tips', on: true}); await loadCaps(); }")   # back on
    assert page.errors == [], page.errors


def test_6_a_card_that_is_not_a_tip_has_no_never_link(app, page):
    _chat(page, app["work"], "code", "not a tip")
    page.evaluate("() => { S.cur.busy = false; }")
    page.evaluate("() => insightCard010({id: 'sug-1', type: 'suggestion', title: 'Open the report', body: 'b', local: () => {}})")
    page.wait_for_selector(".nudge010")
    assert page.locator(".nudge010 .n-never").count() == 0


def test_7_the_session_tab_folds_connectors_and_explains_the_route(app, page):
    _chat(page, app["work"], "code", "session tab")
    # the chat's own first turn runs on the server and sends its own route ("Build · Change"): set ours once that turn is
    # over and drawn, or its late event replaces ours (a busy computer)
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir && !S.cur.stage && S.cur.ready"
                           " && !(S.cur.q || []).length && !['starting', 'working', 'waiting'].includes(S.cur.view.state)", timeout=60000, polling=100)
    page.evaluate("() => { S.cur.route = {label: 'Tests · Python', why: ''}; localStorage.removeItem('sp_open'); }")
    page.evaluate("() => togglePanel('session', true)")
    page.wait_for_selector("#pbody .spw")
    page.wait_for_selector("#pbody details[data-k=conn]")
    conn = page.locator("#pbody details[data-k=conn]")
    assert conn.get_attribute("open") is None                      # folded: the list does not fill the panel
    head = conn.locator("summary").inner_text()
    assert head.startswith("Connectors (") and head.split("(")[1].split(")")[0].isdigit(), head
    assert not conn.locator(".spbody").is_visible()
    conn.locator("summary").click()
    assert conn.locator(".spbody").is_visible()
    txt = page.inner_text("#pbody")
    assert "Working as" not in txt
    route = page.locator("#pbody details[data-k=route] summary")
    assert route.inner_text().startswith("Kind of task")
    assert "took your request to be" in (route.get_attribute("title") or "")
    assert "Tests, Python" in page.inner_text("#pbody details[data-k=route]")
    assert page.errors == [], page.errors
