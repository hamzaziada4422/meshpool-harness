"""Errors the web app shows, from the 0.0.18 error hunt (ER-19 to ER-23). Each test drives the real app (python -m
meshpool_harness ui) in Chromium, with page.route standing in for a failing answer, and checks the words a person
reads: never a bare status, an empty "Something went wrong", "Failed to fetch" or an error reply saved as a file. No
model is called. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import base64
import json
import re

import pytest

from tests.test_ui_bugs018 import _code_chat, app, page  # noqa: F401  (fixtures: the real app and a Chromium page on it)

pytest.importorskip("playwright.sync_api")

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _route(page, pattern: str, status: int, body: str, ctype: str = "application/json"):
    page.route(re.compile(r"^https?://[^/]+" + pattern),
               lambda route, request: route.fulfill(status=status, body=body, content_type=ctype))


# ---------------------------------------------------------------------------------------------------- ER-19
def test_er19_a_failed_request_without_an_explanation_still_says_what_happened(page):
    """ER-19: a 5xx with no JSON body showed only "Something went wrong on Meshpool's side. Try again."; a 200 whose
    body was not JSON came back as null, and the caller then failed with "Cannot read properties of null"."""
    _route(page, r"/api/er19/a", 502, "<html><body>Bad Gateway</body></html>", "text/html")
    _route(page, r"/api/er19/b", 200, "<html>proxy login</html>", "text/html")
    _route(page, r"/api/er19/c", 500, json.dumps({"error": "meshpool is not allowed to open report.docx. Check its "
                                                         "permissions, or close the program that has it open, then "
                                                         "try again."}))
    got = page.evaluate("""async () => { const out = [];
        for (const p of ['/api/er19/a', '/api/er19/b', '/api/er19/c']) {
          try { await api(p); out.push('no error'); } catch (e) { out.push(e.message); } }
        return out; }""")
    assert got == [
        "meshpool's background service failed on this request and sent no explanation (status 502). Try again; if it "
        "keeps happening, quit and reopen meshpool.",
        "meshpool sent an answer this window could not read. Reload the window; if it keeps happening, quit and reopen "
        "meshpool.",
        "meshpool is not allowed to open report.docx. Check its permissions, or close the program that has it open, "
        "then try again.",
    ], got
    assert not any(g.startswith("Something went wrong") for g in got)


# ---------------------------------------------------------------------------------------------------- ER-20
def test_er20_a_failed_memory_export_is_shown_not_saved_as_the_file(page):
    """ER-20: the memory export saved whatever came back as what-meshpool-knows.md, so a failed export gave the user a
    file holding {"error": ...} and no message."""
    _route(page, r"/api/memory/export.*", 500, json.dumps({"error": "The memory store could not be read: the disk is "
                                                                    "full. Free some space, then try again."}))
    downloads = []
    page.on("download", lambda d: downloads.append(d.suggested_filename))
    page.evaluate("() => memExport('md')")
    page.wait_for_function("() => [...document.querySelectorAll('.toast')].some(t => /disk is full/.test(t.textContent))")
    page.wait_for_timeout(300)
    assert downloads == []


# ---------------------------------------------------------------------------------------------------- ER-21
def test_er21_connection_check_shows_the_checks_own_words(page):
    """ER-21: the first-run check replaced the server's sentence by a guess from its digits and words: a 400 "rejected
    as invalid" became "didn't accept this key", a 404 "model does not exist" became "isn't a model endpoint"."""
    cases = [
        "127.0.0.1 refused the request (400). Check the model name and this endpoint's settings, or pick another model, "
        "then try again. (The service said: Your request was rejected as invalid)",
        "127.0.0.1 answered 404: no such address or model. Check the base address (it usually ends in /v1) and the model "
        "name against the provider's list. (The service said: The model `m` does not exist)",
    ]
    got = page.evaluate("cs => cs.map(connMsg)", cases)
    assert got == cases, got
    raw = page.evaluate("() => [connMsg('HTTP 0: <urlopen error [Errno 111] Connection refused>'), connMsg('')]")
    assert raw == ["Can't reach that address. Check the base URL and your internet connection.",
                   "The connection test didn't work, and no reason came back. Check the address and the key, then try "
                   "again."], raw


# ---------------------------------------------------------------------------------------------------- ER-22
def test_er22_saving_a_file_while_meshpool_is_down_says_so(page):
    """ER-22: the editor's save went through its own fetch: when the app's server did not answer, the toast read only
    "Failed to fetch"."""
    page.route(re.compile(r"^https?://[^/]+/api/fs/save"), lambda route, request: route.abort())
    got = page.evaluate("() => edFetch('POST', '/api/fs/save', {}).then(() => 'saved', e => e.message)")
    assert got == ("meshpool isn't responding, so nothing was saved. Your text is still here: try again, and if it keeps "
                   "happening, quit and reopen meshpool."), got


# ---------------------------------------------------------------------------------------------------- ER-23
def test_er23_an_image_preview_that_cannot_load_says_why(page, tmp_path):
    """ER-23: an image the preview could not fetch showed only "Could not load the image.", dropping the reason the
    app's server gave."""
    work = page.app["work"]
    with open(f"{work}/pic.png", "wb") as f:
        f.write(PNG)
    _code_chat(page, work)                                     # the folder is a project: its files may be previewed
    _route(page, r"/raw\?.*", 403, json.dumps({"error": "This file is outside the folders meshpool may read."}))
    page.evaluate("w => openPreview(w, 'pic.png')", work)
    page.wait_for_function("() => /outside the folders/.test(document.querySelector('#pvStage')?.textContent || '')")
    assert "Could not load the image." not in page.inner_text("#pvStage")
