"""Web UI bugs from a new user's first hour with the released 0.0.18 app. Each test drives the real app in Chromium, as
test_ui_bugs018.py does. No model is called. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_ui_bugs018 import _launch, app, page  # noqa: E402,F401  (fixtures: the app, a page on it)


def test_no_project_in_the_picker_takes_the_project_off_the_heading(page):  # noqa: F811
    """The new-chat page kept "Q4work" (and the folder's cards) as its heading after the picker was set to No project;
    the chat it started then had no project."""
    page.evaluate("""() => { S.pj = [{id: 'p_fh1', name: 'Q4work', pinned: true}]; S.draft.pid = 'p_fh1'; S.cur = null;
        S.view = 'chat'; S.mode = 'chat'; renderHero(); }""")
    assert page.inner_text("#col .hero h1").strip() == "Q4work"
    page.evaluate("() => projectSelector(document.querySelector('#col .hero h1'))")
    page.get_by_text("No project", exact=True).click()
    page.wait_for_timeout(200)
    assert page.inner_text("#col .hero h1").strip() == "New chat"
    page.evaluate("() => projectSelector(document.querySelector('#col .hero h1'))")
    page.get_by_text("Q4work", exact=True).last.click()
    page.wait_for_timeout(200)
    assert page.inner_text("#col .hero h1").strip() == "Q4work"
    assert page.errors == [], page.errors
