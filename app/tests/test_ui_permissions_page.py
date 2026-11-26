"""Settings > Permissions, as a first-time user finds it (persona runs, 0.0.18: Maya could not find how to stop the
questions or how to turn on Full access; Daniel could not find Permissions at all). Drives the real app in Chromium;
no model is called. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from test_ui_bugs018 import app, page  # noqa: E402,F401  (the real app and a page on it)


def _vals(page):
    return page.evaluate("""() => Object.fromEntries((S.settings.fields || []).filter(f =>
        ["MH_SANDBOX_AUTO", "MH_AGENT_PERMISSIONS"].includes(f.key)).map(f => [f.key, String(f.value ?? "")]))""")


def test_permissions_is_its_own_page_and_search_finds_it(page):
    page.evaluate("() => openSettings('permissions')")
    page.wait_for_selector("#permAuto")
    opts = page.eval_on_selector_all("#permAuto option", "os => os.map(o => o.textContent)")
    assert opts == ["Only for risky actions (recommended)", "Before every command and change", "Never (Full access)"]
    assert page.eval_on_selector("#permAuto", "s => s.value") == "1"            # the default: ask only for risky ones
    # the page drew to the end: one rules panel (allow, ask, deny, per-folder) and a working Add a folder button.
    # Before, a leftover second rules box threw on every open and the folder button never got its handler.
    page.wait_for_selector("#rulesBox :text('Saved rules')")
    assert page.eval_on_selector_all("#mbody #rulesBox", "xs => xs.length") == 1
    assert page.eval_on_selector("#addP", "x => typeof x.onclick") == "function"
    assert page.errors == [], page.errors
    page.evaluate("() => openSettings()")                                     # search finds the page by its words
    for q in ("approvals", "full access", "allow"):
        page.fill("#mnavq", q)
        assert page.eval_on_selector("[data-g='permissions']", "b => !b.hidden"), q


def test_the_three_choices_save_what_they_say(page):
    page.evaluate("() => openSettings('permissions')")
    page.wait_for_selector("#permAuto")

    page.select_option("#permAuto", "full")
    page.wait_for_selector(".toast:has-text('Full access')")
    page.wait_for_selector("#mbody :text('New chats start in Full access')")
    assert _vals(page)["MH_AGENT_PERMISSIONS"] == "auto"
    assert "without asking" in page.inner_text("#mbody")

    page.select_option("#permAuto", "0")                                        # leaving Full access sets Ask again
    page.wait_for_selector("#mbody :text('asks before every command')")
    v = _vals(page)
    assert v["MH_AGENT_PERMISSIONS"] == "ask" and v["MH_SANDBOX_AUTO"] == "0"
    assert "asks before every command" in page.inner_text("#mbody")

    page.select_option("#permAuto", "1")
    page.wait_for_selector("#mbody :text('goes outside the folder')")
    assert _vals(page)["MH_SANDBOX_AUTO"] == "1"
    assert page.errors == [], page.errors
