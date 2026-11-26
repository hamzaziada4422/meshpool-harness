"""Omar (developer persona), round 3, the web app: the tip card has a "Never show tips" link that turns the setting
off, and the Session tab folds the connector list and says what "Working as" means. Drives the real app in Chromium."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_ui_persona019 import _chat  # noqa: E402
from tests.test_ux018 import app, page, sync_api  # noqa: E402,F401  (the fixtures start the real server and a browser)


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


def test_6_a_card_that_is_not_a_tip_has_no_never_link(app, page):
    _chat(page, app["work"], "code", "not a tip")
    page.evaluate("() => { S.cur.busy = false; }")
    page.evaluate("() => insightCard010({id: 'sug-1', type: 'suggestion', title: 'Open the report', body: 'b', local: () => {}})")
    page.wait_for_selector(".nudge010")
    assert page.locator(".nudge010 .n-never").count() == 0


def test_7_the_session_tab_folds_connectors_and_explains_the_route(app, page):
    _chat(page, app["work"], "code", "session tab")
    page.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir")
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
