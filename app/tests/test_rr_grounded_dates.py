"""Release review item 9: a date the user gave as 2027-01-29, as 29/01/2027 or as "29th of January 2027" was read as
a bare year (or a month), so ground() took "29 Jan 2027" out of the next steps and P29 flagged it as invented."""
from __future__ import annotations

import datetime as dt

import pytest
from pptx import Presentation
from pptx.util import Inches

from engines.forge import grounded as G

WHEN = dt.date(2026, 11, 26)
FORMS = ["2027-01-29", "29/01/2027", "29.01.2027", "29th of January 2027", "29 of January 2027", "01/29/2027"]


def _request(form):
    return f"Make a next steps slide. The business case is due on {form}; the CFO owns the case."


@pytest.mark.parametrize("form", FORMS)
def test_the_users_date_is_a_whole_date(form):
    k = G.Known(_request(form), _request(form), WHEN)
    assert k.date_ok((2027, 1, 29)) and k.date_ok((2027, 1, None)), k.dates
    assert not k.date_ok((2027, 4, 30))


@pytest.mark.parametrize("form", FORMS)
def test_ground_keeps_the_users_date(form):
    s = {"archetype": "next_steps", "title": "Approve the business case by 29 Jan 2027",
         "steps": [{"action": "Write the business case by 29 January 2027", "owner": "CFO", "timing": "29 Jan 2027"},
                   {"action": "Train the plant teams by 30 Apr 2027", "timing": "30 Apr 2027"}]}
    done = G.ground([s], _request(form), _request(form), WHEN)
    assert s["title"] == "Approve the business case by 29 Jan 2027", (s, done)
    st = s["steps"]
    assert st[0]["timing"] == "29 Jan 2027" and st[0]["action"] == "Write the business case by 29 January 2027", st
    assert "timing" not in st[1] and st[1]["action"] == "Train the plant teams", st      # a date nobody gave goes


@pytest.mark.parametrize("form", FORMS)
def test_p29_passes_the_users_date_on_a_slide(form, tmp_path):
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    for i, text in enumerate(("Business case due 29 Jan 2027", f"Due {form}")):
        sl.shapes.add_textbox(Inches(1), Inches(1 + i), Inches(6), Inches(1)).text_frame.text = text
    f = tmp_path / "d.pptx"
    prs.save(str(f))
    got = G.check(str(f), _request(form), _request(form), None, WHEN)
    assert not got, [x["message"] for x in got]


def test_an_ambiguous_numeric_date_reads_both_ways_in_the_sources_only():
    k = G.Known("Kick off on 05/03/2027.", "", WHEN)
    assert k.date_ok((2027, 3, 5)) and k.date_ok((2027, 5, 3))
    assert [key for _, key in G._dates("on 05/03/2027")] == [(2027, 3, 5)]       # on a slide: one date, day first
    assert [key for _, key in G._dates("version 2027-13-40 and 31/31/2027")] == [(2027, None, None)] * 2
