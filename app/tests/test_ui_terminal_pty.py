"""The Terminal tab as a real shell: xterm.js (bundled, served by the app) on a pseudo-terminal. Each test drives the
real app in Chromium, as a person would: typing, colours, a prompt that waits for an answer, the panel dragged wider,
Stop, a tab switch, the slower reading while the tab is closed, and the one-command fallback. No model is called.
Skipped when no Chromium can start here (outside CI), or on Windows (the shell here is bash)."""
from __future__ import annotations

import os
import re
import time

import pytest

pytest.importorskip("ptyprocess")
from tests.test_ui_bugs018 import _code_chat, app, page, sync_api  # noqa: E402,F401  (the fixtures start the app and a browser)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the shell typed into here is bash")

BUF = """() => { const st = Object.values(PTY).find(x => x.wrap.isConnected); if (!st) return "";
  const b = st.term.buffer.active, out = []; for (let i = 0; i < b.length; i++) out.push(b.getLine(i).translateToString(true));
  return out.join("\\n"); }"""
LIVE = "() => { const st = Object.values(PTY).find(x => x.wrap.isConnected); return !!(st && st.alive && st.id); }"


def _open(page) -> None:
    _code_chat(page, page.app["work"])
    page.evaluate("() => togglePanel('term', true)")
    page.wait_for_selector(".pty-wrap .xterm-screen", timeout=15000)
    page.wait_for_function(LIVE, timeout=15000)
    page.wait_for_function(f"() => ({BUF})().trim().length > 0", timeout=15000)      # the first prompt
    page.click(".pty-wrap .xterm-screen")


def _run(page, line: str, want: str, timeout: int = 15000) -> str:
    page.keyboard.type(line)
    page.keyboard.press("Enter")
    page.wait_for_function(f"w => ({BUF})().includes(w)", arg=want, timeout=timeout)
    return page.evaluate(BUF)


def test_the_terminal_is_a_real_shell_with_colours_and_prompts(page):
    asked = []
    page.on("request", lambda r: asked.append(r.url))
    _open(page)
    assert page.query_selector("#tin") is None                       # not the one-command box
    text = _run(page, "echo M$((6*7)); printf '\\033[31mRED\\033[0m\\n'", "M42")
    assert "M42" in text
    page.wait_for_function(f"() => /^RED/m.test(({BUF})())")
    fg = page.evaluate("""() => { const st = Object.values(PTY).find(x => x.wrap.isConnected), b = st.term.buffer.active;
        for (let i = 0; i < b.length; i++) { const l = b.getLine(i); if (l.translateToString(true) === "RED") { const c = l.getCell(0);
          return {fg: c.getFgColor(), palette: c.isFgPalette()}; } } return null; }""")
    assert fg == {"fg": 1, "palette": True}, fg                     # red, from the ANSI palette
    _run(page, "read -p 'Name? ' n; echo \"hi $n\"", "Name? ")
    _run(page, "ann", "hi ann")
    _run(page, "tty", "/dev/pts/")
    assert asked and all(re.match(r"https?://127\.0\.0\.1:\d+/", u) for u in asked), [u for u in asked if "127.0.0.1" not in u]
    assert any(u.endswith("/ui/vendor/xterm.min.js") for u in asked)
    assert page.errors == [], page.errors


def test_the_shell_follows_the_panel_size(page):
    _open(page)
    before = page.evaluate("() => Object.values(PTY).find(x => x.wrap.isConnected).term.cols")
    page.evaluate("() => paneSet('pw', 900)")
    page.wait_for_function("c => Object.values(PTY).find(x => x.wrap.isConnected).term.cols > c + 20", arg=before)
    cols, rows = page.evaluate("() => { const t = Object.values(PTY).find(x => x.wrap.isConnected).term; return [t.cols, t.rows]; }")
    page.wait_for_timeout(300)                                         # the resize request is sent after 60 ms
    _run(page, "stty size", f"{rows} {cols}")
    page.set_viewport_size({"width": 1100, "height": 700})            # the window, and so the panel, gets smaller
    page.wait_for_function("r => Object.values(PTY).find(x => x.wrap.isConnected).term.rows < r", arg=rows)
    cols2, rows2 = page.evaluate("() => { const t = Object.values(PTY).find(x => x.wrap.isConnected).term; return [t.cols, t.rows]; }")
    page.wait_for_timeout(300)
    page.keyboard.type("clear")
    page.keyboard.press("Enter")
    _run(page, "stty size", f"{rows2} {cols2}")
    assert page.errors == [], page.errors


def test_stop_ends_the_shell_and_enter_starts_a_new_one(page):
    _open(page)
    first = page.evaluate("() => Object.values(PTY).find(x => x.wrap.isConnected).id")
    _run(page, "sleep 300 & echo started", "started")
    assert page.is_visible("#ptyStop")
    page.click("#ptyStop")
    page.wait_for_function(f"() => ({BUF})().includes('The shell ended')")
    assert page.is_hidden("#ptyStop") and page.text_content("#ptyNote") == "Not running"
    page.click(".pty-wrap .xterm-screen")
    page.keyboard.press("Enter")
    page.wait_for_function(LIVE)
    assert page.evaluate("() => Object.values(PTY).find(x => x.wrap.isConnected).id") != first
    _run(page, "echo again-$((2+3))", "again-5")
    assert page.errors == [], page.errors


def test_the_shell_keeps_its_output_across_a_tab_switch_and_keeps_its_keys(page):
    _open(page)
    _run(page, "echo before-switch", "before-switch")
    page.evaluate("() => togglePanel('files', true)")
    page.wait_for_selector("#tree")
    page.evaluate("() => togglePanel('term', true)")
    page.wait_for_selector(".pty-wrap .xterm-screen")
    assert "before-switch" in page.evaluate(BUF)
    page.click(".pty-wrap .xterm-screen")
    page.keyboard.type("echo zzz")
    page.keyboard.press("Control+u")                                  # the shell's own key: clears the line
    page.keyboard.press("Control+k")                                  # not the app's search on Windows and Linux
    assert page.query_selector("#srch") is None
    text = _run(page, "echo after-$((1+1))", "after-2")
    assert "\nzzz" not in text
    assert page.errors == [], page.errors


def test_the_shell_is_read_slowly_while_its_tab_is_closed(page):
    _open(page)
    hits = []
    page.on("request", lambda r: hits.append(time.time()) if re.search(r"/api/pty/\w+\?since=", r.url) else None)
    page.evaluate("() => togglePanel(S.panel)")                       # close the panel
    page.wait_for_timeout(1200)
    hits.clear()
    page.wait_for_timeout(4000)
    assert len(hits) <= 3, f"{len(hits)} reads in 4 s with the terminal closed"
    page.evaluate("() => togglePanel('term', true)")
    page.wait_for_selector(".pty-wrap .xterm-screen")
    page.click(".pty-wrap .xterm-screen")
    _run(page, "echo back-$((3+4))", "back-7", timeout=6000)          # and quickly again once it is open
    assert page.errors == [], page.errors


def test_without_a_pty_library_the_tab_runs_one_command_at_a_time(page):
    _code_chat(page, page.app["work"])
    page.evaluate("() => { S.settings.terminal = {pty: null}; togglePanel('term', true); }")
    page.wait_for_selector("#tin")
    assert page.query_selector(".pty-wrap") is None
    page.fill("#tin", "echo one-$((5+5))")
    page.press("#tin", "Enter")
    page.wait_for_function("() => /one-10/.test($('#tout').textContent)", timeout=10000)
    assert page.errors == [], page.errors
