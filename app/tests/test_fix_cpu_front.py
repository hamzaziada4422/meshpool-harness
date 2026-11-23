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
