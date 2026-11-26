"""The "Checked: ..." line of the look review (agent/look.py) is part of the reply in the web UI, live as well as after
a reload: one reply bubble, one log entry, one piece of copied text. It used to arrive as a second, short assistant
message and joined the reply only after a reload."""
from __future__ import annotations

import pytest

from tests.test_ui_bugs018 import _fake_chat, app, page  # noqa: F401  (the fixtures start the real server and a browser)

pytest.importorskip("playwright.sync_api")

LINE = "Checked: looked at the page at desktop and phone width; fixed the form."


def _events(append: bool):
    last = {"type": "assistant", "text": LINE, "streamed": False, "has_tools": False, "i": 4}
    if append:
        last["append"] = True
    return [{"type": "user", "text": "Make a bakery page", "i": 0},
            {"type": "tool_start", "id": "w", "name": "write_file", "summary": "index.html", "i": 1},
            {"type": "tool_end", "id": "w", "ok": True, "output": "ok", "i": 2},
            {"type": "assistant", "text": "I made index.html with a menu and an order form.", "streamed": False,
             "has_tools": False, "i": 3},
            last]


def _play(page, evs):
    _fake_chat(page)
    return page.evaluate("""evs => {
        const cur = S.cur; cur.q = evs.slice(); drainQ(cur, true);
        const ai = Array.from(document.querySelectorAll('#col .msg-ai')).map(e => e.textContent.replace(/\s+/g, ' ').trim());
        return {ai, log: cur.log.filter(e => e.type === 'assistant').map(e => e.text), last: cur.lastText};
    }""", evs)


def test_checked_line_joins_the_reply_live(page):
    got = _play(page, _events(append=True))
    assert len(got["ai"]) == 1, got
    assert got["ai"][0].startswith("I made index.html") and got["ai"][0].endswith(LINE), got
    assert len(got["log"]) == 1 and got["log"][0].endswith("\n\n" + LINE), got        # copy, retry and export read this
    assert got["last"].endswith(LINE), got
    assert page.errors == [], page.errors


def test_checked_line_is_the_same_as_a_reopened_chat(page):
    """The saved history (agent_api rebuild) has the line inside the reply's own text: replayed, it reads the same."""
    live = _play(page, _events(append=True))
    merged = [dict(e) for e in _events(append=False)[:4]]
    merged[3]["text"] += "\n\n" + LINE
    reopened = _play(page, merged)
    assert live["ai"] == reopened["ai"], (live, reopened)
    assert live["log"] == reopened["log"]


def test_a_line_with_no_reply_before_it_still_shows(page):
    _fake_chat(page)
    got = page.evaluate("""line => {
        const cur = S.cur; cur.q = [{type: "user", text: "x", i: 0}, {type: "assistant", text: line, append: true, i: 1}];
        drainQ(cur, true);
        return Array.from(document.querySelectorAll('#col .msg-ai')).map(e => e.textContent.replace(/\s+/g, ' ').trim());
    }""", LINE)
    assert got == [LINE], got
