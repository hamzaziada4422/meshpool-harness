"""review_interact.exercise: try small pages the way a user would and check what it reports.

Each test writes a tiny HTML page to a temp folder and runs exercise() on it with a real headless Chromium.
Env for a local run: PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers MH_TEST_CHROMIUM=<a chrome binary>.
"""
from __future__ import annotations

import http.server
import os
import threading
import time

import pytest

psutil = pytest.importorskip("psutil")
pytest.importorskip("playwright.sync_api")

from meshpool_harness import review_interact as RI  # noqa: E402


def _chromium_ok() -> str:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            RI.launch_browser(pw).close()
        return ""
    except Exception as e:  # noqa: BLE001
        return str(e)[:200]


_WHY = None


@pytest.fixture(autouse=True)
def _need_chromium():
    global _WHY
    if _WHY is None:
        _WHY = _chromium_ok()
    if _WHY:
        if os.environ.get("CI"):
            raise RuntimeError(_WHY)
        pytest.skip(f"no Chromium for Playwright here: {_WHY}")


def _browser_procs() -> list:
    """Chromium or Playwright driver processes started by this test process."""
    out = []
    for p in psutil.Process().children(recursive=True):
        try:
            name = (p.name() + " " + " ".join(p.cmdline()[:3])).lower()
        except Exception:  # noqa: BLE001
            continue
        if p.status() == psutil.STATUS_ZOMBIE:
            continue
        if "oosplash" in name or "soffice" in name or "libreoffice" in name:
            continue                 # an office session another test left open runs --headless too: not a browser
        if "chrom" in name or "headless" in name or "run-driver" in name or "playwright" in name:
            out.append(p)
    return out


def _left_running(wait: float = 5.0) -> list:
    """The Chromium or driver processes of this test process still running, after up to `wait` s for them to exit (a
    killed process can take a moment to go on a loaded machine). Only this process's own tree is counted."""
    end = time.monotonic() + wait
    while True:
        left = _browser_procs()
        if not left or time.monotonic() > end:
            return left
        time.sleep(0.2)


def _in_time(took: float, timeout: float) -> bool:
    """Back close to the time limit: the limit plus a margin that grows with it (launching and killing a browser on a
    loaded machine take a few seconds of their own)."""
    return took < timeout + max(6.0, 0.5 * timeout)


def _page(tmp_path, body: str, head: str = "", name: str = "index.html") -> str:
    f = tmp_path / name
    f.write_text("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                 "<meta name='viewport' content='width=device-width, initial-scale=1'><title>T</title>"
                 f"<style>body{{font-family:sans-serif;margin:0;background:#fff}}</style>{head}</head><body>{body}</body></html>",
                 encoding="utf-8")
    return str(f)


def _run(tmp_path, page: str, request: str = "", **kw) -> dict:
    kw.setdefault("timeout_s", 30)
    res = RI.exercise(page, request, str(tmp_path / "shots"), **kw)
    assert set(res) == {"steps", "findings", "shots", "console_errors", "ms"}
    for s in res["steps"]:
        assert set(s) == {"action", "ok", "note", "shot"}
    assert len(res["shots"]) <= RI.MAX_SHOTS
    for f in res["shots"]:
        assert os.path.isfile(f) and f.startswith(str(tmp_path / "shots"))
    return res


def _has(findings, *words) -> bool:
    return any(all(w.lower() in f.lower() for w in words) for f in findings)


BTN = "<style>.cta{background:#c0662b;color:#fff;border:0;padding:14px 28px;font-size:18px}.cta:hover{background:#933}</style>"


# ---------- a dead button ----------------------------------------------------------------------------------------------
def test_dead_button_is_reported(tmp_path):
    page = _page(tmp_path, "<main><h1>Pizza</h1><p>Hot and fresh.</p><button class='cta'>Order now</button>"
                           "<button id='ok' onclick=\"document.getElementById('o').textContent='Added'\">Add topping</button>"
                           "<p id='o'></p></main>", BTN)
    res = _run(tmp_path, page, "a pizza page with an Order now button", widths=(1280,))
    assert _has(res["findings"], "'Order now' button does nothing when clicked"), res["findings"]
    assert not _has(res["findings"], "Add topping"), res["findings"]
    first = [s for s in res["steps"] if s["action"].startswith("click")][0]
    assert "Order now" in first["action"]                       # the primary button named in the request goes first
    assert any(s["shot"] for s in res["steps"] if "Order now" in s["action"])
    assert res["console_errors"] == []


# ---------- dialogs ----------------------------------------------------------------------------------------------------
MODAL = """<main><h1>Spa</h1><button class='cta' id='open'>Book a massage</button></main>
<div id='m' role='dialog' aria-modal='true' hidden><h2>Book</h2><p>Pick a time.</p><button id='x'>Close</button></div>
<script>
const m = document.getElementById('m');
document.getElementById('open').onclick = () => { m.hidden = false; document.getElementById('x').focus(); };
document.getElementById('x').onclick = () => { m.hidden = true; };
%s
</script>"""


def test_working_modal_opens_and_escape_closes_it(tmp_path):
    page = _page(tmp_path, MODAL % "document.addEventListener('keydown', e => { if (e.key === 'Escape') m.hidden = true; });", BTN)
    res = _run(tmp_path, page, "a spa page where Book opens a booking modal", widths=(1280,))
    step = [s for s in res["steps"] if s["action"].startswith("open dialog")]
    assert step and step[0]["ok"], res["steps"]
    assert "Escape closes it" in step[0]["note"]
    assert step[0]["shot"] and os.path.isfile(step[0]["shot"])
    assert not _has(res["findings"], "dialog"), res["findings"]
    assert not _has(res["findings"], "modal"), res["findings"]


def test_modal_without_escape_is_reported(tmp_path):
    page = _page(tmp_path, MODAL % "", BTN)
    res = _run(tmp_path, page, "a spa page where Book opens a booking modal", widths=(1280,))
    assert _has(res["findings"], "'Book' dialog does not close when Escape is pressed"), res["findings"]
    assert not _has(res["findings"], "no working way to close"), res["findings"]   # its Close button works


# ---------- forms ------------------------------------------------------------------------------------------------------
def test_form_without_validation_is_reported(tmp_path):
    page = _page(tmp_path, """<h2>Contact us</h2><form id='c'><label>Email <input type='email' name='email'></label>
      <label>Message <textarea name='msg'></textarea></label><button>Send</button></form>
      <script>document.getElementById('c').addEventListener('submit', e => e.preventDefault());</script>""")
    res = _run(tmp_path, page, "a contact form", widths=(1280,))
    assert _has(res["findings"], "'Contact us' form submits with empty fields and shows no message"), res["findings"]
    assert _has(res["findings"], "'Contact us' form shows nothing after it is sent"), res["findings"]


def test_form_with_validation_passes(tmp_path):
    page = _page(tmp_path, """<h2>Newsletter</h2><form id='c' novalidate><label>Email <input type='email' name='email'></label>
      <button type='submit'>Subscribe</button><p id='msg'></p></form>
      <script>document.getElementById('c').addEventListener('submit', e => { e.preventDefault();
        const v = e.target.email.value.trim(), m = document.getElementById('msg');
        m.textContent = v.includes('@') ? 'Thanks, you are subscribed.' : 'Please enter your email address.'; });</script>""")
    res = _run(tmp_path, page, "a newsletter signup form", widths=(1280,))
    assert not _has(res["findings"], "form"), res["findings"]
    empty = [s for s in res["steps"] if s["action"].endswith("form empty")]
    filled = [s for s in res["steps"] if s["action"].startswith("fill and submit")]
    assert empty and empty[0]["ok"] and "Please enter" in empty[0]["note"], res["steps"]
    assert filled and filled[0]["ok"] and "subscribed" in filled[0]["note"], res["steps"]


def test_native_required_fields_count_as_a_clear_error(tmp_path):
    page = _page(tmp_path, """<h2>Sign up</h2><form id='c'><input name='name' required placeholder='Name'>
      <button>Join</button></form><p id='o'></p>
      <script>document.getElementById('c').onsubmit = e => { e.preventDefault(); document.getElementById('o').textContent = 'Welcome aboard'; };</script>""")
    res = _run(tmp_path, page, "a sign up form", widths=(1280,))
    empty = [s for s in res["steps"] if s["action"].endswith("form empty")]
    assert empty and empty[0]["ok"] and "browser blocks it" in empty[0]["note"], res["steps"]
    assert not _has(res["findings"], "form"), res["findings"]


# ---------- an external link and other hosts ---------------------------------------------------------------------------
class _Count(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802, D102
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<p>external</p>")

    do_POST = do_GET  # noqa: N815


def test_external_link_is_not_followed_and_other_hosts_are_not_loaded(tmp_path):
    _Count.hits = []
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.2", 0), _Count)     # stands in for another host
    except OSError as e:
        pytest.skip(f"cannot bind 127.0.0.2 here: {e}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ext = f"http://127.0.0.2:{srv.server_address[1]}"
    try:
        page = _page(tmp_path, f"""<h1>Shop</h1><a class='cta' href='{ext}/buy'>Buy on our partner site</a>
          <img src='{ext}/pixel.png' alt='partner'><script src='{ext}/track.js'></script>
          <form action='{ext}/signup' method='post'><input type='email' name='e' required><button>Sign up</button></form>
          <a href='#top'>Top</a>""", BTN)
        res = _run(tmp_path, page, "buy on the partner site and sign up", widths=(1280,))
    finally:
        srv.shutdown()
        srv.server_close()
    assert _Count.hits == [], _Count.hits
    assert not any("Buy on our partner" in s["action"] for s in res["steps"]), res["steps"]
    assert not _has(res["findings"], "pixel"), res["findings"]          # blocked on purpose, not a broken image
    sent = [s for s in res["steps"] if s["action"].startswith("fill and submit")]
    assert sent and "not allowed" in sent[0]["note"], res["steps"]


def test_non_local_target_is_refused_without_raising(tmp_path):
    res = RI.exercise("https://example.com/", "x", str(tmp_path))
    assert res["findings"] and res["findings"][0].startswith("could not exercise the page: only local pages")
    res = RI.exercise(str(tmp_path / "missing.html"), "x", str(tmp_path))
    assert res["findings"][0].startswith("could not exercise the page: the file")
    assert res["steps"] == [] and isinstance(res["ms"], int)


# ---------- keyboard ---------------------------------------------------------------------------------------------------
def test_invisible_focus_is_reported(tmp_path):
    page = _page(tmp_path, """<nav><a href='#a'>Home</a> <a href='#b'>Prices</a> <a href='#c'>Team</a></nav>
      <section id='a'><h1>A</h1></section><section id='b'>B</section><section id='c'>C</section>""",
                 "<style>*:focus{outline:none}</style>")
    res = _run(tmp_path, page, "", widths=(1280,), max_steps=1)
    assert _has(res["findings"], "Keyboard focus is invisible on the nav links"), res["findings"]


def test_visible_focus_and_order_pass(tmp_path):
    page = _page(tmp_path, """<nav><a href='#a'>Home</a> <a href='#b'>Prices</a></nav><section id='a'>A</section><section id='b'>B</section>""")
    res = _run(tmp_path, page, "", widths=(1280,), max_steps=1)
    assert not _has(res["findings"], "focus"), res["findings"]
    tab = [s for s in res["steps"] if s["action"].startswith("Tab through")]
    assert tab and tab[0]["ok"]


def test_focus_jumping_back_up_is_reported(tmp_path):
    page = _page(tmp_path, """<a href='#x'>Top link</a><div style='height:1500px'></div><a href='#y'>Bottom link</a>
      <button tabindex='0'>Low button</button><div style='height:900px'></div><a href='#z' tabindex='0'>Last</a>
      <div id='x'></div><div id='y'></div><div id='z'></div>
      <script>document.querySelector("a[href='#x']").tabIndex = 2; document.querySelector("a[href='#y']").tabIndex = 1;</script>""")
    res = _run(tmp_path, page, "", widths=(1280,), max_steps=1)
    assert _has(res["findings"], "Keyboard focus jumps back up the page"), res["findings"]


# ---------- the phone menu ---------------------------------------------------------------------------------------------
NAV = """<header><b>Logo</b> %s<nav id='n'><a href='#a'>Work</a> <a href='#b'>About</a> <a href='#c'>Contact</a></nav></header>
<section id='a'>A</section><section id='b'>B</section><section id='c'>C</section>%s"""
NAV_CSS = "<style>@media (max-width:700px){nav{display:none}nav.open{display:block}}.t{display:none}@media (max-width:700px){.t{display:inline}}</style>"


def test_mobile_nav_toggle_opens_the_menu(tmp_path):
    page = _page(tmp_path, NAV % ("<button class='t' aria-label='Open menu' aria-controls='n' aria-expanded='false'>&#9776;</button>",
                                  "<script>document.querySelector('.t').onclick = () => document.getElementById('n').classList.toggle('open');</script>"),
                 NAV_CSS)
    res = _run(tmp_path, page, "a portfolio site", max_steps=2)
    step = [s for s in res["steps"] if s["action"] == "open the menu at 390px"]
    assert step and step[0]["ok"], res["steps"]
    assert "Open menu" in step[0]["note"] and step[0]["shot"]
    assert not _has(res["findings"], "390 px"), res["findings"]


def test_mobile_nav_without_a_toggle_is_reported(tmp_path):
    page = _page(tmp_path, NAV % ("", ""), NAV_CSS)
    res = _run(tmp_path, page, "a portfolio site", max_steps=2)
    assert _has(res["findings"], "At 390 px wide the navigation links are hidden and there is no menu button"), res["findings"]


def test_mobile_nav_toggle_that_does_not_open_is_reported(tmp_path):
    page = _page(tmp_path, NAV % ("<button class='t' aria-label='Menu'>&#9776;</button>", ""), NAV_CSS)
    res = _run(tmp_path, page, "a portfolio site", max_steps=2)
    assert _has(res["findings"], "menu button 'Menu' does not show the navigation links"), res["findings"]


# ---------- errors -----------------------------------------------------------------------------------------------------
def test_console_error_is_reported(tmp_path):
    page = _page(tmp_path, """<h1>Hi</h1><button id='b' class='cta'>Go</button><p id='o'></p>
      <script>console.error('price table failed'); document.getElementById('b').onclick = () => { undefinedThing(); };</script>""", BTN)
    res = _run(tmp_path, page, "", widths=(1280,))
    assert any("price table failed" in c for c in res["console_errors"]), res["console_errors"]
    assert any("undefinedThing" in c for c in res["console_errors"]), res["console_errors"]
    assert _has(res["findings"], "logs an error in the console", "price table failed"), res["findings"]
    assert _has(res["findings"], "Clicking the 'Go' button causes a script error", "undefinedThing"), res["findings"]


# ---------- time limits and processes ---------------------------------------------------------------------------------
class _Slow(http.server.BaseHTTPRequestHandler):
    page = b""
    stop = threading.Event()

    def log_message(self, *a):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802, D102
        if self.path.startswith("/hang"):
            type(self).stop.wait(20)                    # never answers while the test runs
            return
        body = type(self).page if self.path in ("/", "/index.html") else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def slow_server():
    _Slow.stop = threading.Event()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    _Slow.stop.set()
    srv.shutdown()
    srv.server_close()


def test_page_that_never_settles_returns_in_time(slow_server, tmp_path):
    _Slow.page = b"<!doctype html><title>t</title><h1>Waiting</h1><button>Start</button><img src='/hang.png' alt='x'>"
    t = time.monotonic()
    res = _run(tmp_path, slow_server, "a start button", timeout_s=8)
    took = time.monotonic() - t
    assert _in_time(took, 8), took
    assert _in_time(res["ms"] / 1000, 8), res["ms"]
    assert _has(res["findings"], "did not finish loading"), res["findings"]
    assert any(s["action"] == "load at 1280px" for s in res["steps"])
    assert _left_running() == []


def test_page_that_keeps_polling_is_reported(slow_server, tmp_path):
    _Slow.page = b"<!doctype html><title>t</title><h1>Live</h1><script>setInterval(() => fetch('/ping?' + Date.now()), 150);</script>"
    res = _run(tmp_path, slow_server, "", widths=(1280,), max_steps=1, timeout_s=10)
    assert _has(res["findings"], "never settles"), res["findings"]


def test_frozen_page_is_reported_and_nothing_is_left_running(tmp_path):
    page = _page(tmp_path, "<h1>Busy</h1><script>setTimeout(() => { for (;;) {} }, 50);</script>")
    t = time.monotonic()
    res = _run(tmp_path, page, "", timeout_s=12)
    assert _in_time(time.monotonic() - t, 12)
    assert _has(res["findings"], "freezes"), res["findings"]
    assert _left_running() == []


def test_no_browser_process_left_after_a_normal_run(tmp_path):
    page = _page(tmp_path, "<h1>Hi</h1><button class='cta' onclick=\"this.textContent='Done'\">Try it</button>", BTN)
    res = _run(tmp_path, page, "a try it button")
    assert not _has(res["findings"], "could not exercise"), res["findings"]
    assert _left_running() == []


def test_runs_from_inside_an_asyncio_loop(tmp_path):
    import asyncio
    page = _page(tmp_path, "<h1>Hi</h1><button class='cta' onclick=\"this.textContent='Done'\">Try it</button>", BTN)

    async def main():
        return RI.exercise(page, "a try it button", str(tmp_path / "shots"), widths=(1280,), timeout_s=30)
    res = asyncio.run(main())
    assert not _has(res["findings"], "could not exercise"), res["findings"]
    assert any(s["action"] == "click 'Try it'" and s["ok"] for s in res["steps"]), res["steps"]


def test_hard_time_limit_kills_the_browser(tmp_path, monkeypatch):
    def hang(self, page, width):            # a Playwright call that never returns
        page.evaluate("() => new Promise(() => {})")
    monkeypatch.setattr(RI._Run, "main_width", hang)
    page = _page(tmp_path, "<h1>Hi</h1>")
    t = time.monotonic()
    res = RI.exercise(page, "", str(tmp_path / "shots"), timeout_s=4)
    took = time.monotonic() - t
    assert _in_time(took, 4), took
    assert _has(res["findings"], "could not exercise the page: it did not finish within 4 s"), res["findings"]
    assert _left_running() == []


def test_passed_in_browser_is_not_closed(tmp_path):
    from playwright.sync_api import sync_playwright
    page = _page(tmp_path, "<h1>Hi</h1><button class='cta' onclick=\"this.textContent='Done'\">Try it</button>", BTN)
    frozen = _page(tmp_path, "<h1>Busy</h1><script>setTimeout(() => { for (;;) {} }, 50);</script>", name="busy.html")
    with sync_playwright() as pw:
        b = RI.launch_browser(pw)
        try:
            assert _browser_procs()                 # the process check sees a running browser
            res = _run(tmp_path, page, "a try it button", browser=b)
            assert not _has(res["findings"], "could not exercise"), res["findings"]
            assert b.is_connected()
            res = _run(tmp_path, frozen, "", browser=b, timeout_s=10)
            assert _has(res["findings"], "freezes"), res["findings"]
            assert b.is_connected() and b.contexts == []
            p = b.new_page()
            p.set_content("<p>still here</p>")
            assert p.inner_text("p") == "still here"
        finally:
            b.close()
