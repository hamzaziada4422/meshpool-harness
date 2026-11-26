"""Daniel, 0.0.19, item 7: the slide preview kept "2 / 6" after the deck grew to 7 slides, until it was reopened.
Drives the real app in Chromium (fixtures from test_wf_ui018.py); /api/preview is answered by page.route."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api")

from test_wf_ui018 import _fake_chat, app, page  # noqa: F401,E402


def test_an_open_slide_preview_takes_the_new_page_count_when_the_turn_changes_the_deck(page):
    work = page.app["work"]
    pages = {"n": 6}

    def meta(route, request):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"state": "ready", "kind": "pages", "ext": "pptx", "pages": pages["n"], "total": pages["n"],
             "mtime": pages["n"]}))
    page.route(re.compile(r"^https?://[^/]+/api/preview\?"), meta)
    _fake_chat(page, view={"workdir": work})
    page.evaluate("w => openPreview(w, 'deck.pptx')", work)
    page.wait_for_function("() => PVX.open && PVX.m && PVX.m.pages === 6")
    page.evaluate("() => pvGo(1)")
    assert page.inner_text("#pvCount") == "2 / 6"
    pages["n"] = 7                                             # the turn added a slide
    page.evaluate("""() => applyEv({type: "checkpoint", i: 9, turns: [{n: 1, changed: [{path: "deck.pptx",
        status: "modified"}]}]})""")
    page.wait_for_function("() => document.querySelector('#pvCount').textContent === '2 / 7'", timeout=8000)
    assert page.evaluate("document.querySelectorAll('#pvThumbs .pv-th').length") == 7
    page.evaluate("""() => applyEv({type: "checkpoint", i: 10, turns: [{n: 2, changed: [{path: "other.pptx",
        status: "modified"}]}]})""")                          # another file: the preview is left as it is
    assert page.inner_text("#pvCount") == "2 / 7"
    assert page.errors == [], page.errors
