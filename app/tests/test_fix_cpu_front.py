"""Front-end CPU use: idle polling, animation frames, and typing in a long chat: checked in a real browser against the real app server."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests import cpu_harness as H

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, secs: int = 60) -> str:
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    pytest.fail(f"nothing answered at {url} within {secs} s")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("uifront")
    home, mh = tmp / "home", tmp / "mh"
    for d in (home, mh):
        d.mkdir()
    uport = _port()
    (mh / "meshpool.config").write_text("MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:9/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|GITHUB_|GH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(REPO / "app"), PYTHONUTF8="1", MH_CLARITY="0")
    env.pop("MESHPOOL_CONFIG", None)
    env["MH_SLOTS_DIR"] = str(tmp / "slots")
    log = open(tmp / "ui.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                         cwd=str(REPO / "app"), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{uport}"
    try:
        _wait(base + "/")
        yield {"base": base}
    finally:
        p.kill()
        log.close()


@pytest.fixture()
def page(app):
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("MH_TEST_CHROMIUM")
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as e:  # noqa: BLE001
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium for Playwright here: {e}")
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(H.COUNTERS)
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_selector("#newThread", timeout=30000)
        pg.wait_for_function("() => typeof S === 'object' && S.settings && S.settings.ready !== undefined", timeout=30000)
        pg.errors = errors
        yield pg
        b.close()




def _cdp_cpu(page):
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")
    return lambda: {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}


def measure(page, seconds, hidden=False):
    metrics = _cdp_cpu(page)
    if hidden:
        page.evaluate("Object.defineProperty(document, 'hidden', {get: () => true, configurable: true}); Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true}); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(500)
    a = metrics(); c0 = page.evaluate("({...window.__c})")
    page.wait_for_timeout(int(seconds * 1000))
    b = metrics(); c1 = page.evaluate("({...window.__c})")
    out = {"task_s": b["TaskDuration"] - a["TaskDuration"], "script_s": b["ScriptDuration"] - a["ScriptDuration"],
           "layout_s": b["LayoutDuration"] - a["LayoutDuration"], "raf": c1["raf"] - c0["raf"], "timeouts": c1["timeouts"] - c0["timeouts"],
           "nodes": b["Nodes"]}
    return out


def _open_chats(page, n=5):
    for k in range(n):
        page.evaluate("id => openThread(id)", f"c{k}")
        page.wait_for_function("id => S.cur && S.cur.id === id && S.cur.ready && !S.cur.stage", arg=f"c{k}", timeout=120000)


@pytest.fixture()
def longchats(page):
    counts = H.install(page)
    page.reload()
    page.wait_for_function("() => typeof S === 'object' && S.settings && S.threads && S.threads.length >= 5", timeout=30000)
    t0 = time.time()
    _open_chats(page)
    page.opened_s = time.time() - t0
    page.counts = counts
    return page


def test_idle_chat_costs_little_cpu(longchats):
    page = longchats
    page.wait_for_timeout(3000)
    n0 = page.counts["req"]
    m = measure(page, 20)
    reqs = page.counts["req"] - n0
    print("IDLE 20s:", {k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}, "requests", reqs, page.counts["by"], "open_s", round(page.opened_s, 1))
    assert m["raf"] <= 40, m          # was ~60 per second while idle
    assert reqs <= 8, reqs
    assert m["task_s"] < 1.0, m


def test_hidden_tab_is_quiet(longchats):
    page = longchats
    page.wait_for_timeout(2000)
    n0 = page.counts["req"]
    m = measure(page, 15, hidden=True)
    reqs = page.counts["req"] - n0
    print("HIDDEN 15s:", {k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}, "requests", reqs)
    assert m["raf"] <= 5, m
    assert reqs <= 2, reqs


def test_typing_in_a_long_chat_stays_cheap(longchats):
    page = longchats
    page.focus("#input")
    ms = page.evaluate("""() => { const i = document.querySelector('#input'); const t0 = performance.now();
      for (let k = 0; k < 20; k++) { i.value += 'a'; i.dispatchEvent(new Event('input', {bubbles: true})); document.body.getBoundingClientRect(); }
      return performance.now() - t0; }""")
    print("TYPING 20 keys ms:", round(ms), "nodes", page.evaluate("document.querySelectorAll('*').length"))
    assert ms < 1500, ms


# ---- 0.0.17 CPU audit (app area): streaming into a long chat, reasoning, code blocks, the Session panel

def test_stream_frames_do_not_redraw_the_header(longchats):
    # A text-only stream frame has no goal; `prevGoal !== v.goal` was always true, so the header (and, in a code chat,
    # a /api/git request) was redrawn on every frame: ~80 times a second while a reply streamed.
    page = longchats
    page.evaluate("() => { window.__rt = 0; const o = window.renderTop; window.renderTop = function () { window.__rt++; return o.apply(this, arguments); }; }")
    # One synchronous evaluate: ingest calls renderTop synchronously, and nothing else (the app's own poll of the live
    # server, whose idle state differs from this fake 'working' one) can run between the frames. With separate
    # evaluates a poll landed in a gap on a slow CI runner and counted 1 (tests run 104, Python 3.12).
    n = page.evaluate("(k) => { ingest(S.cur, Object.assign({}, S.cur.view, {state: 'working', events: [], total: S.cur.total}));"
                      " const r0 = window.__rt; (" + H.STREAM_FRAMES.strip() + ")(k); return window.__rt - r0; }", 50)
    print("HEADER redraws for 50 stream frames:", n)
    assert n == 0, n                              # was 50


def test_long_chat_history_is_folded_into_blocks(longchats):
    # 2400 items directly in #col: every frame of a streaming reply laid out, hit-tested and paint-walked all of them
    # (a whole core in a 3000-event chat). Older items now sit in blocks the browser skips as a unit.
    page = longchats
    r = page.evaluate("""() => { const col = document.querySelector('#col');
      const qs = [...col.querySelectorAll('.msg-user .b')].map(b => +(/number (\\d+)/.exec(b.textContent) || [0, 0])[1]);
      return {kids: col.children.length, outer: col.querySelectorAll(':scope > .hist').length, blocks: col.querySelectorAll('.hist > .hist').length,
              users: qs.length, ordered: qs.every((x, i) => !i || x === qs[i - 1] + 1), enter: col.querySelectorAll('.hist.enter').length}; }""")
    ms = min(page.evaluate(H.LAYOUT_PER_CHANGE) for _ in range(3))
    print("FOLD:", r, "layout per change ms", round(ms, 3))
    assert r["kids"] < 300, r                     # was 2400
    assert r["outer"] == 1 and r["blocks"] >= 10, r
    assert r["users"] == 600 and r["ordered"] and not r["enter"], r
    assert ms < 0.3, ms                           # was ~0.9 ms per frame (2400 items); ~0.03 ms folded


def test_reasoning_stream_is_cheap(longchats):
    # Each streamed piece of reasoning rewrote the box's text and read scrollHeight (a forced layout of the whole chat),
    # three pieces a frame. Now pieces are appended and the box scrolls once per frame.
    page = longchats
    page.evaluate(H.SCROLL_READS, ".think")
    try:
        r = page.evaluate("""() => { const c = S.cur;
          for (let k = 0; k < 300; k++) c.q.push({type: 'thought', delta: true, text: 'step' + k + ' '});
          drainQ(c); const oneFrame = 300 - c.q.length; flushQ(c);
          const box = [...document.querySelectorAll('#col .think')].pop();
          return {oneFrame, reads: window.__reads, text: box ? box.querySelector('div').textContent : ''}; }""")
    finally:
        page.evaluate("window.__readsOff()")
    print("THINK:", {k: v for k, v in r.items() if k != "text"})
    assert r["reads"] <= 2, r["reads"]            # was 300
    assert r["oneFrame"] == 300, r["oneFrame"]    # was 3 a frame: reasoning fell behind the stream
    assert r["text"].startswith("step0 step1 ") and r["text"].endswith("step299 ")


def test_open_code_block_is_not_recoloured_every_delta(page):
    # The code block a reply is streaming was tokenized and rebuilt span by span on every frame.
    r = page.evaluate("""() => { let calls = 0; const o = window.edTokens; window.edTokens = function () { calls++; return o.apply(this, arguments); };
      try {
        const code = Array.from({length: 200}, (_, i) => `def f${i}(x):\\n    return x * ${i}  # line ${i}`).join('\\n');
        const full = 'Here:\\n\\n```python\\n' + code + '\\n```\\n\\nDone.';
        const n = document.createElement('div'); document.body.appendChild(n);
        const t0 = performance.now(); let steps = 0;
        for (let k = 20; k < full.length; k += 6) { mdRender(n, full.slice(0, k)); steps++; }
        mdRender(n, full);
        const ms = performance.now() - t0, fresh = document.createElement('div'); mdRender(fresh, full);
        const same = fresh.innerHTML === n.innerHTML, spans = n.querySelectorAll('pre code span').length; n.remove();
        return {calls, steps, ms, same, spans};
      } finally { window.edTokens = o; } }""")
    print("CODE:", r)
    assert r["same"] and r["spans"] > 100, r      # the finished block is exactly what a fresh render gives, coloured
    assert r["calls"] <= r["ms"] / 300 + 12, r    # was one tokenizer pass per step (~1000)


def test_session_panel_polls_rarely_when_idle_and_never_when_hidden(longchats):
    # The Session panel asked the server every 4 s (six git processes and a diff each time in a repository) even while
    # the chat was idle and the tab hidden.
    page = longchats
    calls = H.count_requests(page, r"/api/session/[^/]+/panel")
    page.evaluate("() => togglePanel('session', true)")
    page.wait_for_timeout(1500)
    n0 = calls["n"]
    page.wait_for_timeout(12000)
    idle = calls["n"] - n0
    H.hide(page)
    n1 = calls["n"]
    page.wait_for_timeout(9000)
    hidden = calls["n"] - n1
    H.show(page)
    page.wait_for_timeout(1500)
    back = calls["n"] - n1 - hidden
    print("SESSION PANEL calls: idle 12s", idle, "hidden 9s", hidden, "on return", back)
    assert idle <= 1, idle                        # was 3
    assert hidden == 0, hidden                    # was 2
    assert back >= 1, back                        # coming back refreshes at once
