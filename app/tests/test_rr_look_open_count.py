"""Release review item 4: an open problem whose text is too short to print ("section 3") was dropped from the count, so
the line made no claim and the footer said "Checked · all good" while a major problem was still open."""
from __future__ import annotations

from meshpool_harness import checks as CK
from meshpool_harness.agent import lookreview as LR

WHO = "a board member"


def _v(probs, ok=False):
    return {"persona": WHO, "can_do_the_job": ok, "pass": ok, "also": "",
            "problems": [{"what": w, "where": "", "severity": s} for s, w in probs]}


def test_a_short_open_problem_is_counted_and_the_footer_is_not_all_good():
    st = {"persona": WHO, "evals": [_v([("major", "section 3")])]}
    line = LR.summary_line("doc", st)
    assert line == "Checked as a board member: fixed nothing. Still open: 1 thing.", line
    short, _ = CK.footer(line, {})
    assert short == "Checked · 1 to look at", short


def test_a_fix_and_a_short_problem_left_open():
    st = {"persona": WHO, "evals": [_v([("major", "the Q4 total shows an error"), ("major", "section 3")]),
                                    _v([("major", "section 3")])]}
    line = LR.summary_line("doc", st)
    assert line == ("Checked as a board member: fixed 1 thing: the Q4 total shows an error. Still open: 1 thing."), line
    assert "Nothing left open" not in line
    assert CK.footer(line, {})[0] == "Checked · fixed 1 thing · 1 to look at"


def test_several_short_problems_are_all_counted():
    st = {"persona": WHO, "evals": [_v([("major", "section 3"), ("minor", "page 2"), ("minor", "the title")])]}
    line = LR.summary_line("doc", st)
    assert line.endswith("Still open: 3 things."), line
    assert CK.footer(line, {})[0] == "Checked · 3 to look at"


def test_a_whole_open_problem_is_still_said_in_words():
    st = {"persona": WHO, "evals": [_v([("major", "the chart has no title")])]}
    assert LR.summary_line("doc", st) == "Checked as a board member: fixed nothing. Still open: the chart has no title."
