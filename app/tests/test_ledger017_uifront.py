"""Ledger 0.0.17 gap rows for the UI front end (pages.js, app.js, chat010.js, shell.js, home010.js, composer.js,
company.js, memory.js, app.css): each test runs the audit's trigger in a real Chromium page against the real app
server (python -m meshpool_harness ui), with page.route standing in for GitHub and other outside answers. No network,
no model. Skipped when no Chromium can start here (outside CI)."""
from __future__ import annotations

import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> None:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    pytest.fail(f"nothing answered at {url} within {secs} s")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ledger017front")
    home, mh = tmp / "home", tmp / "mh"
    for d in (home, mh):
        d.mkdir()
    port = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp / "slots"))
    env.pop("MESHPOOL_CONFIG", None)
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(port)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait(base + "/")
        yield {"base": base}
    finally:
        p.kill()
        p.wait(10)
        log.close()


def _launch(pw):
    """Playwright's own Chromium; else MH_TEST_CHROMIUM; else any Chromium in the browsers folder (its version may
    differ from the one this Playwright release expects)."""
    try:
        return pw.chromium.launch()
    except Exception as first:  # noqa: BLE001
        roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers",
                 str(Path.home() / ".cache" / "ms-playwright")]
        cands = [os.environ.get("MH_TEST_CHROMIUM") or ""]
        for r in filter(None, roots):
            cands += sorted(glob.glob(os.path.join(r, "chromium_headless_shell-*", "*", "chrome-headless-shell")))
            cands += sorted(glob.glob(os.path.join(r, "chromium-*", "chrome-linux*", "chrome")))
        for exe in filter(None, cands):
            try:
                return pw.chromium.launch(executable_path=exe)
            except Exception:  # noqa: BLE001
                continue
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no Chromium for Playwright here: {first}")


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        b = _launch(pw)
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.set_default_timeout(15000)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()


def _json_route(page, pattern: str, body, status: int = 200, seen: list | None = None):
    """Answer requests whose path matches `pattern` with JSON; POST bodies are kept in `seen`."""
    def handle(route, request):
        if seen is not None:
            seen.append({"method": request.method, "url": request.url,
                         "body": json.loads(request.post_data) if request.post_data else None})
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
    page.route(re.compile(r"^https?://[^/]+" + pattern), handle)


def _fake_chat(page, cid: str = "t-ledger"):
    """An open chat with no server behind it: applyEv draws into it as a live chat would."""
    page.evaluate("""cid => { openThread(cid, true); if (S.cur.pollT) { clearTimeout(S.cur.pollT); clearInterval(S.cur.pollT); }
        S.cur.pollT = null; }""", cid)


# ---------------------------------------------------------------------------------------------------- PA-uifront-02
def test_code_template_creates_a_code_task(page):
    """PA-uifront-02: Scheduled > Templates > Repository health check > Create task. The template's kind (code) and
    folder go into the task_add request, so the task is not created as a Work task."""
    tpl = {"name": "Repository health check", "prompt": "Check the repository for failing tests.", "when": "weekly",
           "day": "mon", "time": "09:00", "kind": "code", "folder": "/work/repo"}
    _json_route(page, r"/api/hub/scheduled(\?.*)?$", {"tasks": [], "templates": [tpl]})
    sent = []
    _json_route(page, r"/api/assistant(\?.*)?$", {"ok": True}, seen=sent)
    page.evaluate("() => { G.tab.scheduled = 'templates'; goPage('scheduled'); }")
    page.wait_for_selector(".gcard.tpl", timeout=15000)
    page.click(".gcard.tpl")
    page.wait_for_selector(".dlg [data-a='1']")
    page.click(".dlg [data-a='1']")
    page.wait_for_function("() => !document.querySelector('.dlg')")
    page.wait_for_timeout(300)
    add = [s["body"] for s in sent if s["body"] and s["body"].get("action") == "task_add"]
    assert len(add) == 1, sent
    assert add[0]["kind"] == "code" and add[0]["folder"] == "/work/repo"
    assert add[0]["prompt"] == tpl["prompt"]
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- memory rows
def _add_memory(page, text: str) -> str:
    page.evaluate("() => post('/api/memory', {action: 'settings', memory_on: true}).catch(() => null)")
    r = page.evaluate("t => post('/api/memory', {action: 'add', text: t})", text)
    return r["fact"]["id"]


def _fact(page, fid: str) -> dict:
    d = page.evaluate("() => api('/api/memory?q=&category=&scope=&kind=&unused=')")
    return next(f for f in d["facts"] if f["id"] == fid)


def test_memory_forget_on_date_can_be_removed(page):
    """PA-uifront-07: Memory > clock icon on a fact with an expiry > clear the text > Set. The empty answer is sent
    as "remove the expiry" (ask() returns "" with allowEmpty; only Cancel is null)."""
    fid = _add_memory(page, "Works from the Cairo office until the move")
    page.evaluate("id => post('/api/memory', {action: 'expiry', id, until: '2031-12-31'})", fid)
    assert _fact(page, fid)["valid_until"]
    page.evaluate("id => { memExpiry(id); }", fid)
    page.wait_for_selector("#dlgIn")
    page.fill("#dlgIn", "")
    page.click(".dlg [data-a='1']")
    page.wait_for_function("id => api('/api/memory?q=&category=&scope=&kind=&unused=').then(d => !d.facts.find(f => f.id === id).valid_until)",
                           arg=fid, timeout=10000)
    assert not _fact(page, fid)["valid_until"]
    # Cancel still leaves it alone
    page.evaluate("id => post('/api/memory', {action: 'expiry', id, until: '2031-12-31'})", fid)
    page.evaluate("id => { memExpiry(id); }", fid)
    page.wait_for_selector("#dlgIn")
    page.click(".dlg [data-a='0']")
    page.wait_for_timeout(300)
    assert _fact(page, fid)["valid_until"]
    assert page.errors == [], page.errors


def test_memory_restore_keeps_old_text_with_slash_and_bar(page):
    """PA-uifront-16: edit a memory whose old text contains "a/b | c", then Restore it from History. The old text
    travels in data-before, so it comes back whole instead of being cut at "/" or "|"."""
    old = "Deck size a/b | c: always 16:9 / never 4:3"
    fid = _add_memory(page, old)
    page.evaluate("id => post('/api/memory', {action: 'edit', id, text: 'Deck size is whatever fits'})", fid)
    assert _fact(page, fid)["text"] == "Deck size is whatever fits"
    page.evaluate("() => { MEM.tab = 'history'; goPage('memory'); }")
    sel = f"[data-restore='edited|{fid}']"
    page.wait_for_selector(sel, timeout=15000)
    page.click(sel)
    page.wait_for_function("([id, t]) => api('/api/memory?q=&category=&scope=&kind=&unused=').then(d => d.facts.find(f => f.id === id).text === t)",
                           arg=[fid, old], timeout=10000)
    assert _fact(page, fid)["text"] == old
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-08
def test_xray_with_a_long_draft_does_not_fail_with_414(page):
    """PA-uifront-08: paste more than 70 KB into the box and click the eye icon. The draft sent in the X-ray query is
    cut to 4000 characters, so the request is not refused as too long (414)."""
    reqs = []
    page.on("response", lambda r: reqs.append((r.url, r.status)) if "/api/xray" in r.url else None)
    page.evaluate("() => { const i = $('#input'); i.value = 'x'.repeat(80000); i.dispatchEvent(new Event('input')); }")
    page.evaluate("() => xray010(document.querySelector('#input'))")
    page.wait_for_function("() => document.querySelector('.mem-pop.xray') || document.querySelector('.toast')",
                           timeout=10000)
    assert reqs, "no X-ray request was made"
    url, status = reqs[-1]
    from urllib.parse import parse_qs, urlparse
    assert len(parse_qs(urlparse(url).query)["text"][0]) == 4000
    assert status != 414 and status < 500, (status, url[:120])
    assert page.evaluate("() => !!document.querySelector('.mem-pop.xray')")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-09
def test_copy_reply_leaves_out_the_goal_marker(page):
    """PA-uifront-09: a turn under /goal ends with a "GOAL: DONE" line. The text Copy reply copies has no marker."""
    _fake_chat(page)
    page.evaluate("""() => {
        applyEv({type: "user", text: "Make the report", i: 1});
        applyEv({type: "assistant", text: "The report is in report.docx.\\n\\nGOAL: DONE"});
    }""")
    assert page.evaluate("S.cur.lastText") == "The report is in report.docx."
    page.evaluate("""() => { applyEv({type: "assistant", text: "GOAL: CONTINUE"}); }""")     # a bare marker keeps the reply
    assert page.evaluate("S.cur.lastText") == "The report is in report.docx."
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-10
def test_tool_row_stops_saying_needs_approval_after_allow(page):
    """PA-uifront-10: approve a long-running command. When the approval is answered the tool row no longer says
    "Needs your approval"."""
    _fake_chat(page)
    page.evaluate("""() => {
        applyEv({type: "user", text: "Run the build", i: 1});
        applyEv({type: "tool_start", id: "tl1", name: "bash", args: {command: "make all"}});
        applyEv({type: "approval", id: "ap1", i: 3, kind: "bash", title: "Run this command?", detail: "make all"});
    }""")
    st = "() => document.querySelector('#tool-tl1 .st')?.textContent"   # the row may render a frame later
    page.wait_for_function(f"({st})() === 'Needs your approval'", timeout=5000)
    page.evaluate("""() => applyEv({type: "approval_done", id: "ap1", decision: "yes"})""")
    page.wait_for_function(f"({st})() === ''", timeout=5000)
    assert page.evaluate("() => document.querySelector('#appr-ap1').classList.contains('done')")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-11
@pytest.mark.parametrize("title,want", [("تقرير المبيعات الشهري", "تقرير المبيعات الشهري.md"),
                                        ("销售报告", "销售报告.md"), ("a/b: c?", "ab c.md"), ("///", "chat.md")])
def test_export_keeps_a_non_latin_chat_title_as_the_file_name(page, title, want):
    """PA-uifront-11: export a chat titled in Arabic or Chinese. The file is named after the title, not ".md";
    only characters a file name cannot hold are dropped, and an empty name falls back to "chat"."""
    _fake_chat(page)
    page.evaluate("""t => { S.cur.view = Object.assign(S.cur.view || {}, {title: t});
        window._dl = []; HTMLAnchorElement.prototype.click = function () { if (this.download) window._dl.push(this.download); };
        const b = document.createElement('button'); b.id = 'expBtn'; document.body.appendChild(b);
        exportMenu({currentTarget: b}); }""", title)
    page.get_by_text("Save as a file").click()
    assert page.evaluate("window._dl") == [want]
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-12
def test_first_run_starters_from_scanned_files_fill_the_box(page):
    """PA-uifront-12: a new user with files in the scanned folders clicks a starter. The click is handled (a
    delegated handler on the new buttons) and the prompt goes into the message box."""
    prompt = "Summarise the numbers in Budget 2026.xlsx."
    _json_route(page, r"/api/firstrun/scan(\?.*)?$", {"starters": [{"prompt": prompt}], "files": 3})
    _json_route(page, r"/api/away(\?.*)?$", {"cards": [], "runs": []})
    page.evaluate("() => { localStorage.removeItem('mp_firstrun_done'); S.threads = []; S.mode = 'cowork'; newThread(); }")
    page.wait_for_selector(".starters button[data-s]", timeout=15000)
    page.click(".starters button[data-s]")
    assert page.input_value("#input") == prompt
    assert page.evaluate("store('firstrun_done')") is True
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-13
@pytest.mark.parametrize("status,body,want", [
    (200, {"repos": []}, "No repositories found"),
    (400, {"error": "Connect GitHub (Settings, Connectors) first."}, "Connect GitHub"),
])
def test_repo_picker_does_not_say_loading_forever(page, status, body, want):
    """PA-uifront-13: Code mode > repository selector with GitHub not connected or no repositories. Once the list
    has loaded the picker says so instead of "Loading repositories…" for ever."""
    _json_route(page, r"/api/github/repos(\?.*)?$", body, status=status)
    page.evaluate("() => repoSelector(document.querySelector('#input'))")
    page.wait_for_function("t => document.body.innerText.includes(t)", arg=want, timeout=10000)
    page.wait_for_timeout(300)
    assert "Loading repositories" not in page.evaluate("document.body.innerText")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-14
def test_company_delete_uses_the_in_app_dialog(page):
    """PA-uifront-14: Settings > Company and brand > Delete. The confirmation is the app's own dialog (ask), which a
    webview always shows; window.confirm is never called."""
    native = []
    page.on("dialog", lambda d: (native.append(d.type), d.dismiss()))
    page.evaluate("() => openSettings('company')")
    page.wait_for_selector("#coBlank", timeout=15000)
    page.click("#coBlank")
    page.wait_for_selector("#coDel", timeout=10000)
    page.click("#coDel")
    page.wait_for_selector(".dlg [data-a='1']", timeout=5000)
    assert native == []
    assert "Delete" in page.inner_text(".dlg")
    page.click(".dlg [data-a='1']")
    page.wait_for_selector("#coBlank", timeout=10000)
    assert page.evaluate("() => api('/api/company').then(d => (d.profiles || []).length)") == 0
    assert native == []
    assert page.errors == [], page.errors


def test_no_ui_script_calls_native_confirm_alert_or_prompt():
    """PA-uifront-14: source check over every UI script (a webview may never show native dialogs; the browser test
    above covers the Company page, this covers the rest without driving every page)."""
    ui = REPO / "app" / "meshpool_harness" / "ui"
    bad = []
    for f in sorted(ui.glob("*.js")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(?<![\w.$])(window\.)?(confirm|alert|prompt)\s*\(", line) and "function " not in line:
                bad.append(f"{f.name}:{i}: {line.strip()[:100]}")
    assert bad == []


# ---------------------------------------------------------------------------------------------------- PA-uifront-15
def _pr(verdict: str) -> dict:
    return {"number": 5, "title": "Fix the parser", "url": "https://github.com/o/r/pull/5", "state": "open",
            "draft": False, "head": "fix", "base": "main", "sha": "abcdef1234", "author": "ann", "commits": 1,
            "updated": "2026-11-01T00:00:00Z", "additions": 3, "deletions": 1, "changed_files": 1, "connected": True,
            "can_merge": True, "mergeable": True, "verdict": verdict, "preview_url": "", "body": "", "files": [],
            "runs": [], "reviews": [], "deployments": [], "comments": [],
            "checks": [] if verdict == "none" else [{"name": "tests", "status": "completed", "conclusion": "success"}]}


@pytest.mark.parametrize("verdict,want,never", [("none", "No checks have run.", "Its checks passed"),
                                                ("passing", "Its checks passed.", "No checks have run")])
def test_merge_dialog_tells_the_truth_about_checks(page, verdict, want, never):
    """PA-uifront-15: merge a pull request that has no checks. The dialog says "No checks have run." instead of
    "Its checks passed."."""
    _json_route(page, r"/api/git/pr\?.*$", _pr(verdict))
    page.evaluate("""() => { const m = document.createElement('div'); m.id = 'prvMain'; document.body.appendChild(m);
        PRV.root = '/repo'; PRV.n = 5; return prDetail(); }""")
    page.wait_for_selector("#prvMerge", timeout=10000)
    page.click("#prvMerge")
    page.wait_for_selector(".dlg p")
    text = page.inner_text(".dlg p")
    assert want in text and never not in text
    page.click(".dlg [data-a='0']")
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-18
def _lum(rgb: str) -> float:
    vals = [float(x) for x in re.findall(r"[\d.]+", rgb)[:3]]
    out = []
    for v in vals:
        v /= 255
        out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def test_route_menu_hover_is_readable_in_dark_mode(page):
    """PA-uifront-18: dark mode > Session panel > Working as > Change > hover an item. Every colour token the route
    menu uses is defined, and the hovered item's text has readable contrast on its background."""
    page.evaluate("""() => { document.documentElement.dataset.theme = 'dark';
        const a = document.createElement('div'); a.id = 'rmA'; a.style.cssText = 'position:fixed;left:40px;top:40px;width:10px;height:10px';
        document.body.appendChild(a); routeMenu(a); }""")
    page.wait_for_selector(".routemenu button")
    used = page.evaluate("""() => { const names = new Set();
        for (const sh of document.styleSheets) { let rules; try { rules = sh.cssRules; } catch (e) { continue; }
          for (const r of rules) if (r.selectorText && r.selectorText.includes('routemenu'))
            for (const m of r.cssText.matchAll(/var\\((--[\\w-]+)\\s*\\)/g)) names.add(m[1]); }
        const cs = getComputedStyle(document.documentElement);
        return [...names].map(n => [n, cs.getPropertyValue(n).trim()]); }""")
    assert used and all(v for _, v in used), used
    page.hover(".routemenu button")
    fg, bg, menu = page.evaluate("""() => { const b = document.querySelector('.routemenu button');
        return [getComputedStyle(b).color, getComputedStyle(b).backgroundColor,
                getComputedStyle(document.querySelector('.routemenu')).backgroundColor]; }""")
    alpha = re.findall(r"[\d.]+", bg)
    eff = bg if len(alpha) < 4 or float(alpha[3]) > 0.5 else menu          # a light tint over the menu
    a, b = sorted([_lum(fg), _lum(eff)], reverse=True)
    assert (a + 0.05) / (b + 0.05) >= 4.5, (fg, bg, menu)
    assert _lum(menu) < 0.2, menu                                          # the menu itself is dark in dark mode
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-20
_COUNT = """() => { if (!window._lc) { window._lc = new Set(); const add = document.addEventListener.bind(document),
    rem = document.removeEventListener.bind(document);
    document.addEventListener = (t, f, o) => { if (t === 'mousedown' || t === 'keydown') window._lc.add(f); return add(t, f, o); };
    document.removeEventListener = (t, f, o) => { window._lc.delete(f); return rem(t, f, o); }; }
    return window._lc.size; }"""


def test_popovers_and_route_menu_leave_no_document_listeners(page):
    """PA-uifront-20: open and re-open the X-ray / memory chip popover (and the route menu) many times, slowly and
    in quick succession, then close it. No mousedown or keydown listener is left on the document."""
    base = page.evaluate(_COUNT)
    page.evaluate("() => { for (let i = 0; i < 5; i++) popAt(document.querySelector('#input'), '<b>x</b>'); }")   # quick
    page.wait_for_timeout(50)
    for _ in range(5):                                                                                              # slow
        page.evaluate("() => popAt(document.querySelector('#input'), '<b>x</b>')")
        page.wait_for_timeout(30)
    page.evaluate("() => document.querySelector('.mem-pop').close()")       # closed by its own close(): no key press
    page.wait_for_timeout(50)                                               # that would also fire leaked listeners
    assert page.evaluate("() => document.querySelectorAll('.mem-pop').length") == 0
    assert page.evaluate(_COUNT) == base
    page.evaluate("""() => { const a = document.createElement('div'); a.id = 'rmB'; document.body.appendChild(a);
        for (let i = 0; i < 5; i++) routeMenu(a); }""")
    page.wait_for_timeout(50)
    for _ in range(3):
        page.evaluate("() => routeMenu(document.querySelector('#rmB'))")
        page.wait_for_timeout(30)
    page.evaluate("() => document.querySelector('.routemenu').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    page.wait_for_timeout(50)
    assert page.evaluate("() => document.querySelectorAll('.routemenu').length") == 0
    assert page.evaluate(_COUNT) == base
    assert page.errors == [], page.errors


# ---------------------------------------------------------------------------------------------------- PA-uifront-21
def test_custom_connector_with_a_bad_url_keeps_the_plugins_page(page):
    """PA-uifront-21: a custom connector saved with a malformed URL. The Plugins page still draws, showing the
    connector's name where its host would be."""
    conns = [{"id": "c1", "name": "Ledger Tool", "custom": True, "connected": True, "enabled": True,
              "url": "not a url at all", "description": "Our own tool"},
             {"id": "c2", "name": "Good Tool", "custom": True, "connected": True, "enabled": True,
              "url": "https://tools.example.com/mcp", "description": ""}]
    _json_route(page, r"/api/connectors(\?.*)?$", {"connectors": conns})
    _json_route(page, r"/api/skills(\?.*)?$", {"skills": []})
    _json_route(page, r"/api/plugins(\?.*)?$", {"plugins": []})
    page.evaluate("() => { G.plg = null; goPage('plugins'); }")
    page.wait_for_selector(".prow[data-k='connector:c1']", timeout=15000)
    assert "Connector · Ledger Tool" in page.inner_text(".prow[data-k='connector:c1']")
    assert "Connector · tools.example.com" in page.inner_text(".prow[data-k='connector:c2']")
    assert page.errors == [], page.errors
