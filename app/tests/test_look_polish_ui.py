"""The look review in the web UI: the reply written before the check folds into a "First answer, before the check"
detail, and the grey "Looking at the result..." line never stays in the thread when no Checked line follows."""
from __future__ import annotations

import pytest

from tests.test_ui_bugs018 import _fake_chat, app, page  # noqa: F401  (the fixtures start the real server and a browser)

pytest.importorskip("playwright.sync_api")

LINE = "Checked: looked at the page at desktop and phone width; fixed the form."
LOOK = {"type": "status", "look": True, "text": "Looking at the result before finishing..."}


def _play(page, evs):
    _fake_chat(page)
    return page.evaluate("""evs => {
        const cur = S.cur; cur.q = evs.map((e, i) => Object.assign({i}, e)); drainQ(cur, true);
        const txt = e => e.textContent.replace(/\\s+/g, ' ').trim();
        return {ai: Array.from(document.querySelectorAll('#col .msg-ai')).map(txt),
                open: Array.from(document.querySelectorAll('#col > .msg-ai')).map(txt),
                folded: Array.from(document.querySelectorAll('#col details.notedet')).map(txt),
                notes: Array.from(document.querySelectorAll('#col .note')).map(txt)
                    .filter(t => t !== 'This chat is gone.'),   // the fake chat is not on the server; its load may say so
                log: cur.log.filter(e => e.type === 'assistant' && !e.folded).map(e => e.text)};
    }""", evs)


def _reply(text, **kw):
    return dict({"type": "assistant", "text": text, "streamed": False, "has_tools": False}, **kw)


def test_the_first_answer_folds_and_the_last_one_carries_the_checked_line(page):
    got = _play(page, [{"type": "user", "text": "make a page"}, _reply("Made index.html."), LOOK,
                       {"type": "review_round", "round": 1}, _reply("Fixed it. The page is index.html."),
                       _reply(LINE, append=True), {"type": "turn_end", "turn": {}}])
    assert len(got["open"]) == 1 and got["open"][0].startswith("Fixed it.") and got["open"][0].endswith(LINE), got
    assert len(got["folded"]) == 1 and got["folded"][0].startswith("First answer, before the check"), got
    assert got["log"] == ["Fixed it. The page is index.html.\n\n" + LINE], got        # copy and export read the last one
    assert got["notes"] == [], got
    assert page.errors == [], page.errors


@pytest.mark.parametrize("tail,note", [([{"type": "turn_end", "turn": {}}], None),
                                       ([{"type": "error", "text": "The model service had a problem."},
                                         {"type": "turn_end", "turn": {}}],
                                        "Could not finish the check: The model service had a problem."),
                                       ([{"type": "interrupted", "text": "Stopped."}, {"type": "turn_end", "turn": {}}],
                                        "Could not finish the check: it was stopped")])
def test_looking_line_does_not_stay_without_a_checked_line(page, tail, note):
    got = _play(page, [{"type": "user", "text": "x"}, _reply("Made it."), LOOK] + tail)
    assert not any(n.startswith("Looking at the result") for n in got["notes"]), got
    assert (note in got["notes"]) if note else not any("Could not finish" in n for n in got["notes"]), got
    assert page.errors == [], page.errors


def test_the_looking_note_names_who_is_checking_it(page):
    """The reviewer's persona replaces the words in the same grey note, and the note still goes when the line arrives."""
    who = "a customer on her phone ordering a birthday cake from the bakery"
    persona = {"type": "status", "look": True, "persona": who, "text": "Checking it as: " + who + "..."}
    got = _play(page, [{"type": "user", "text": "x"}, _reply("Made it."), LOOK, persona])
    assert got["notes"] == ["Checking it as: " + who + "..."], got
    assert page.evaluate("S.cur.activity") == "Checking it as: " + who
    done = _play(page, [{"type": "user", "text": "x"}, _reply("Made it."), LOOK, persona, _reply(LINE, append=True),
                        {"type": "turn_end", "turn": {}}])
    assert done["notes"] == [], done
    assert page.errors == [], page.errors
