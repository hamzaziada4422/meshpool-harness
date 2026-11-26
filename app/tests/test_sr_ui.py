"""Web UI fixes from the finance and first-time user persona runs (SR ids). Each test drives the real app (python -m
meshpool_harness ui) in Chromium, as test_ui_bugs018.py does. No model is called. Skipped when no Chromium can start
here (outside CI)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_sr_sheet_preview import budget  # noqa: E402
from test_ui_bugs018 import _fake_chat, _launch, app, page  # noqa: E402,F401  (fixtures: the app, a page on it)

INIT = ("try { localStorage.setItem('mp_onbDone', '1'); localStorage.setItem('mp_permsAsked', '1'); "
        "localStorage.removeItem('mp_last_chat'); } catch (e) {}")


def _home(app) -> Path:  # noqa: F811
    return Path(app["work"]).parent / "mh"            # the app's own data folder: a root the preview may read


# ---------------------------------------------------------------------------------------------------- SR-02 to SR-04
@pytest.mark.parametrize("width", [1024, 1366])
def test_sheet_preview_fits_and_shows_values_as_formatted(app, width):  # noqa: F811
    """SR-02..04, at the two widths the tester used: every column of the budget is in view (only Q1 was), numbers
    have their format, a total shows its value with the formula on hover, and the hidden sheet says so."""
    budget(_home(app) / "Budget_sr.xlsx", cached={"B6": 466000, "C6": 472500, "D6": 487000, "E6": 1425500})
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_page(viewport={"width": width, "height": 760})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(INIT)
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.evaluate("r => openPreview(r, 'Budget_sr.xlsx')", str(_home(app)))
        pg.wait_for_selector(".pv-sheet table tbody tr")
        got = pg.evaluate("""() => {
            const w = document.querySelector('.pv-sheet'), box = w.getBoundingClientRect();
            const heads = [...w.querySelectorAll('thead th')].slice(1);
            const rows = [...w.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')]);
            const total = rows.find(r => r[0] && r[0].textContent === 'Total');
            return {inView: heads.map(h => h.getBoundingClientRect().right <= box.right + 1),
                    salaries: rows.find(r => r[0] && r[0].textContent === 'Salaries').map(td => td.textContent),
                    total: total.map(td => td.textContent), totalTip: total[1].title,
                    tabs: [...document.querySelectorAll('#pvTabs button')].map(t => t.textContent)};
        }""")
        b.close()
    assert got["inView"] == [True] * 5, got
    assert got["salaries"][1:4] == ["412,000", "418,500", "431,200"], got
    assert got["total"][1] == "466,000" and got["totalTip"] == "=SUM(B4:B5)", got
    assert got["tabs"] == ["Budget", "Assumptions (hidden)"], got
    assert errors == [], errors


# ---------------------------------------------------------------------------------------------------- SR-07
LINE = "\n\nAttached files (in this chat's folder): Budget_2026.xlsx"


def test_message_put_back_by_undo_has_no_internal_line_and_keeps_its_files(page):  # noqa: F811
    """SR-07: Undo put "Attached files (in this chat's folder): ..." in the message box, and a second Undo ran two
    messages together. The line is gone, the file is a chip, a second fill replaces the first, and the file is named
    again (in the same words) when the message is sent."""
    _fake_chat(page)
    sent = []
    page.route(re.compile(r".*/api/agent/t-ui018/send$"),
               lambda r: (sent.append(json.loads(r.request.post_data or "{}")), r.fulfill(json=({"sent": {}})))[1])
    got = page.evaluate("""t => { const i = $('#input'); i.value = '';
        fillInput('Please turn the deck into a PDF.');
        fillInput('The Total row looks wrong. Please fix it.' + t);
        return {box: i.value, chips: [...document.querySelectorAll('#attachRow .att-chip')].map(c => c.textContent.replace('×', '').trim())}; }""",
                        LINE)
    assert got == {"box": "The Total row looks wrong. Please fix it.", "chips": ["Budget_2026.xlsx"]}, got
    page.evaluate("() => sendNow(false)")
    page.wait_for_timeout(500)
    assert sent and sent[0]["text"] == "The Total row looks wrong. Please fix it." + LINE, sent
    assert sent[0]["files"] == [], sent
    kept = page.evaluate("""() => { const i = $('#input'); i.value = 'my own draft'; fillInput('An older message'); return i.value; }""")
    assert kept == "my own draft\n\nAn older message"                 # what the user typed is never replaced
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- SR-12
def test_user_bubble_shows_attachments_as_chips(page):  # noqa: F811
    """SR-12: the chat showed the internal line under the user's message. It shows the file as a chip instead."""
    _fake_chat(page)
    got = page.evaluate("""t => { applyEv({type: 'user', text: 'What is on this invoice?' + t, i: 0});
        const b = document.querySelector('#col .msg-user .b');
        return {text: b.textContent, chips: [...b.querySelectorAll('.att .flink')].map(x => [x.textContent, x.dataset.open]),
                copy: userText(b.closest('.msg-user'))}; }""", LINE.replace("Budget_2026.xlsx", "Invoice March.pdf"))
    assert "Attached files" not in got["text"] and got["text"].startswith("What is on this invoice?"), got
    assert got["chips"] == [["Invoice March.pdf", "Invoice March.pdf"]], got
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- SR-11
def test_model_service_down_is_one_line_and_the_status_says_so(page):  # noqa: F811
    """SR-11: every check while the service was down added a line (7 for one outage) and the status said Thinking."""
    _fake_chat(page)
    got = page.evaluate("""() => { applyEv({type: 'user', text: 'a nice winter flower?', i: 0});
        for (let n = 1; n <= 3; n++) applyEv({type: 'status', down: true, attempt: n, i: n,
            text: `The model endpoint isn't answering (nothing answered at that address). Checking again in ${n * 10} s, attempt ${n + 1}, for up to 30 min. Press Stop to end.`});
        const notes = [...document.querySelectorAll('#col .note')].filter(x => /not answering/.test(x.textContent));
        const last = notes[0]?.textContent;
        S.cur.working = true; S.cur.since = Date.now(); S.cur.view = Object.assign(S.cur.view || {}, {state: 'working'}); workTick(S.cur);
        const status = document.querySelector('#working .wt')?.textContent;
        applyEv({type: 'status', down: false, i: 9, text: 'The model endpoint is answering again. Picking up where it left off.'});
        workTick(S.cur);
        return {n: notes.length, last, status, after: document.querySelector('#working .wt')?.textContent,
                back: [...document.querySelectorAll('#col .note')].filter(x => /answering again/.test(x.textContent)).length,
                down: [...document.querySelectorAll('#col .note')].filter(x => /not answering/.test(x.textContent)).length}; }""")
    assert got["n"] == 1 and "Trying again in 30 s" in got["last"] and "attempt" not in got["last"], got   # plain words since the persona pass (test_ui_persona019)
    assert got["status"] == "Waiting for the model service", got
    assert got["after"] != "Waiting for the model service" and got["back"] == 1 and got["down"] == 0, got   # the same line
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- SR-10
def test_onboarding_stops_the_chatgpt_sign_in_when_another_option_is_picked(app):  # noqa: F811
    """SR-10: a ChatGPT sign-in started in onboarding kept running after the user picked an API key instead."""
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.add_init_script("try { localStorage.setItem('mp_permsAsked', '1'); } catch (e) {}")
        stops, starts = [], []

        def harnesses(route):
            r = route.fetch()
            d = r.json()
            for h in d.get("harnesses", []):
                if h.get("id") == "codex":
                    h.update(available=True, signed_in=False, unsupported="")
            route.fulfill(response=r, json=d)
        pg.route(re.compile(r".*/api/harnesses(\?.*)?$"), harnesses)
        pg.route(re.compile(r".*/api/harness-login$"),
                 lambda r: (starts.append(1), r.fulfill(json={"ok": True, "message": "Your browser opens."}))[1])
        pg.route(re.compile(r".*/api/harness-login/stop$"), lambda r: (stops.append(1), r.fulfill(json={"ok": True}))[1])
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.evaluate("() => showOnboarding()")
        pg.click("[data-prov=chatgpt]")
        pg.wait_for_selector("#oSign:not([hidden])")
        pg.click("#oSign")
        pg.wait_for_timeout(300)
        assert starts == [1] and stops == []
        pg.click("[data-prov=custom]")
        pg.wait_for_timeout(300)
        b.close()
    assert stops == [1]


# ---------------------------------------------------------------------------------------------------- SR-13
def test_billing_text_matches_the_provider(page):  # noqa: F811
    """SR-13: Plan and billing said "A Meshpool key is saved on this computer" to someone on another provider."""
    got = page.evaluate("""() => { const s0 = S.settings; const out = {};
        for (const p of ['meshpool', 'custom', 'chatgpt']) {
          S.settings = Object.assign({}, s0, {provider: p, fields: [{key: 'MESHPOOL_BASE_URL', value: 'https://api.openai.com/v1'}]});
          out[p] = billingText({set: true}); }
        S.settings = s0; return out; }""")
    assert got["meshpool"].startswith("A meshpool key is saved"), got   # the product name is lowercase in the UI (0.0.18)
    assert "Meshpool key is saved" not in got["custom"] and "api.openai.com" in got["custom"], got
    assert "Meshpool key is saved" not in got["chatgpt"] and "ChatGPT" in got["chatgpt"], got
    assert page.errors == [], page.errors
