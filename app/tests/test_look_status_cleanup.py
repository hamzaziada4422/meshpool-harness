"""Transient status lines are gone when the turn ends (ui/app.js `endLook`, the status handling).

Persona testers found "Looking at the result before finishing..." left in a finished reply (Priya) and "Starting the
office tools..." left at the top of the first chat (Maya). A second review round wrote a new note and lost the handle
of the first, so that note stayed for good."""
from __future__ import annotations

import pytest

from tests.test_ui_bugs018 import _fake_chat, app, page  # noqa: F401  (the fixtures start the real server and a browser)

pytest.importorskip("playwright.sync_api")

LINE = "Checked as a customer on her phone: fixed 1 thing: the form error. Nothing left open."
LOOK = {"type": "status", "look": True, "text": "Looking at the result before finishing..."}
AGAIN = {"type": "status", "look": True, "text": "Looking at the result again before finishing..."}
WHO = {"type": "status", "look": True, "persona": "a customer on her phone",
       "text": "Checking it as: a customer on her phone..."}
OFFICE = {"type": "status", "text": "Starting the office tools..."}
END = {"type": "turn_end", "turn": {}}


def _reply(text, **kw):
    return dict({"type": "assistant", "text": text, "streamed": False, "has_tools": False}, **kw)


def _play(page, evs):
    _fake_chat(page)
    return page.evaluate("""evs => {
        const cur = S.cur; cur.q = evs.map((e, i) => Object.assign({i}, e)); drainQ(cur, true);
        const txt = e => e.textContent.replace(/\\s+/g, ' ').trim();
        return {notes: Array.from(document.querySelectorAll('#col .note')).map(txt)
                    .filter(t => t !== 'This chat is gone.'),
                ai: Array.from(document.querySelectorAll('#col .msg-ai')).map(txt)};
    }""", evs)


def _left(got):
    return [n for n in got["notes"] if n.startswith(("Looking at the result", "Checking it as", "Starting the office"))]


def test_a_first_round_note_does_not_outlive_a_second_round(page):
    """Round 1 writes a note and the builder is sent back; round 2 writes its own note. Only that one may be left, and it
    goes with the Checked line."""
    got = _play(page, [{"type": "user", "text": "make a page"}, _reply("Made it."), LOOK, WHO,
                       {"type": "review_round", "round": 1}, _reply("Fixed it."), AGAIN,
                       _reply(LINE, append=True, look=True), END])
    assert _left(got) == [], got
    assert page.errors == [], page.errors


def test_the_note_is_gone_while_the_builder_works_on_the_fix(page):
    got = _play(page, [{"type": "user", "text": "make a page"}, _reply("Made it."), LOOK, WHO,
                       {"type": "review_round", "round": 1}])
    assert _left(got) == [], got


def test_two_looks_in_a_row_leave_one_note_at_most(page):
    got = _play(page, [{"type": "user", "text": "x"}, _reply("Made it."), LOOK, AGAIN])
    assert _left(got) == ["Looking at the result again before finishing..."], got
    done = _play(page, [{"type": "user", "text": "x"}, _reply("Made it."), LOOK, AGAIN, END])
    assert _left(done) == [], done


def test_a_checked_line_that_did_not_join_the_reply_still_ends_the_note(page):
    """No reply above to merge into (a restored chat, a reply that was folded): the look line is over all the same."""
    got = _play(page, [{"type": "user", "text": "x"}, LOOK, _reply(LINE, append=True, look=True)])
    assert _left(got) == [], got
    assert got["ai"] == [LINE], got


def test_starting_the_office_tools_goes_when_the_message_starts(page):
    """Maya: the line stood above her first message for good."""
    got = _play(page, [OFFICE, {"type": "user", "text": "make me a deck"}, _reply("Here it is."), END])
    assert _left(got) == [], got
    assert page.errors == [], page.errors


def test_starting_the_office_tools_goes_when_the_turn_ends(page):
    got = _play(page, [{"type": "user", "text": "make me a deck"}, OFFICE, _reply("Here it is.")])
    assert _left(got) == ["Starting the office tools..."], got            # while the wait goes on, it is shown
    done = _play(page, [{"type": "user", "text": "make me a deck"}, OFFICE, _reply("Here it is."), END])
    assert _left(done) == [], done
