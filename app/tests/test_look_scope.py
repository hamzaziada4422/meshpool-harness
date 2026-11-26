"""The look review judges only what the request asked for, and its closing line is built from fields, not cut text.

Persona testers (Sara, Maya, Priya, Nov 2026) found two things that each cost a point or two:
1. Sara asked only for a PDF of her deck. The review round announced "garbled titles" and a "repeated footer", the
   builder rewrote titles, deleted the footer and converted an edited copy. A review that said nothing needed changing
   still listed complaints.
2. The "Checked:" paragraph was stitched from the persona string and cut problem fragments ("... after asking for the
   Total column SUM to looked it over; ... so it's; minor things left").

No network and no paid model: the builder and the reviewer are scripts behind Agent._post."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import look as L, lookreview as LR  # noqa: E402
from tests.test_look_review import (ASK, FIXED, _shots, agent, fake_render, journal, reviews, text_of,  # noqa: E402
                                    write)
from tests.test_look_reviewer import (PERSONA, Both, _env, body_text, proj, two_rounds, verdict)  # noqa: E402,F401

PDF = "%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
BOARD = "a board member reading the Q3 board update PDF before the board meeting"
CONVERT = "Please make the PDF of Board_Update_Q3.pptx now."


def _doc_render(monkeypatch):
    calls = []

    def render_doc(path, out_dir, tag="r1"):
        calls.append(path)
        return {"findings": [], "shots": [], "text": "[page 1 of 8]\nBoard update the quarter\n[page 2 of 8]\nHeadcount the summary"}
    monkeypatch.setattr(L, "render_doc", render_doc)
    return calls


# ---------- 1. what the turn asked for ----------------------------------------------------------------------------------
@pytest.mark.parametrize("request_text,scope", [
    ("Please make the PDF of Board_Update_Q3.pptx now.", "convert"),
    ("Can you make a PDF of my deck?", "convert"),
    ("convert report.docx to PDF", "convert"),
    ("export the budget sheet as a PDF", "convert"),
    ("save this page as a PNG", "convert"),
    ("What is the total budget in Budget_2026.xlsx?", "read"),
    ("summarize the memo", "read"),
    ("how many slides are in the deck", "read"),
    ("convert the deck to PDF and fix the typo on slide 2", "change"),
    ("make a PDF report about Q3 sales", "change"),        # nothing existing to convert: it makes new content
    ("update the numbers in Board_Update_Q3.pptx", "change"),
    ("make a landing page for a flower shop called Petal", "change"),
    ("", "change"),
])
def test_request_scope(request_text, scope):
    assert LR.request_scope(request_text) == scope


def test_a_pdf_conversion_turn_gets_no_review_round_and_no_edit_of_the_source(proj, monkeypatch):
    """Sara: 'make the PDF of my deck'. Nothing but a check that the PDF exists and opens: no reviewer critique of the
    deck, no message to the builder, so no rewritten titles and no '(edited)' copy."""
    drawn = _doc_render(monkeypatch)
    bad = verdict([("slide titles read as broken fragments", "page 1 title", "major"),
                   ("the same footer line repeats on seven slides", "footer", "major")], persona=BOARD, ok=False)
    both = Both(monkeypatch, [write("Board_Update_Q3.pdf", PDF), "Made Board_Update_Q3.pdf."],
                [bad, bad, bad, bad])
    a, ev = agent(proj)
    journal(a, proj)                         # a command's file is the turn's when the checkpoint journal booked it
    final = a.send(CONVERT)
    assert both.r_bodies == [] and reviews(a) == [], "no reviewer call and no review message for a conversion"
    assert drawn == [], "the PDF is not drawn and read for content"
    assert len(both.b_bodies) == 2, "the builder is not sent back"
    assert sorted(p.name for p in proj.iterdir()) == ["Board_Update_Q3.pdf"], "no edited copy of the source"
    assert final.rstrip().endswith("Checked: Board_Update_Q3.pdf was made and opens."), final
    assert not any(e.get("look") and e["type"] == "status" for e in ev), "no 'Looking at the result' line to leave behind"


def test_a_conversion_that_does_not_open_says_so(proj, monkeypatch):
    _doc_render(monkeypatch)
    both = Both(monkeypatch, [write("Board_Update_Q3.pdf", "not a pdf at all"), "Made it."], [])
    a, _ = agent(proj)
    journal(a, proj)
    final = a.send(CONVERT)
    assert both.r_bodies == [] and reviews(a) == []
    assert final.rstrip().endswith("Checked: Board_Update_Q3.pdf is empty or does not open."), final


def test_a_pdf_that_is_new_content_is_still_reviewed(proj, monkeypatch):
    """Only a conversion of an existing file skips the review: a PDF the turn wrote is the turn's own work."""
    _doc_render(monkeypatch)
    both = Both(monkeypatch, [write("summary.pdf", PDF), "Made it."], [verdict(persona=BOARD)])
    a, _ = agent(proj)
    journal(a, proj)
    a.send("write a one page PDF summary of our Q3 results")
    assert len(both.r_bodies) == 1


def test_the_reviewer_and_the_builder_are_told_what_this_turn_asked_for():
    s = LR.SYSTEM
    assert "Judge only what the request asked for in this turn" in s and "also_noticed" in s
    assert "convert, export, read, summarize or answer" in s and "Never list the source's wording" in s
    assert "problems\" is [] and \"pass\" is true" in s
    text, _ = LR.build_user("verdict", "doc", {"label": "Board_Update_Q3.pdf", "files": []}, {"text": "x", "shots": []},
                            CONVERT, True, [], BOARD)
    assert LR.SCOPE_NOTE["convert"] in text
    ask, _ = LR.build_user("verdict", "doc", {"label": "x.docx", "files": []}, {"text": "x", "shots": []},
                           "make a memo", True, [], BOARD)
    assert "only asked to convert" not in ask
    v = {"persona": BOARD, "can_do_the_job": False, "pass": False, "actions": [], "also": "the footer repeats",
         "problems": [{"what": "the PDF has no pages", "where": "", "severity": "blocker"}]}
    msg = L.message("doc", {"label": "Board_Update_Q3.pdf", "files": []}, {"text": "", "shots": []}, CONVERT, True, v)
    body = msg["content"]
    assert L.SCOPE_RULE in body and L.SCOPE_RULE_ASKED["convert"] in body
    assert "Do not edit the source file's content" in body and "footer repeats" not in body
    assert L.SCOPE_RULE_ASKED["convert"] not in L.message("doc", {"label": "x", "files": []}, {"text": "", "shots": []},
                                                          "make a memo", True, v)["content"]


# ---------- 1b. a problem outside the request is one line for the user, never a fix for the builder ------------------------
def test_an_out_of_scope_problem_reaches_the_user_as_one_offer_not_the_builder(proj, tmp_path, monkeypatch):
    first = dict(verdict([("the page scrolls sideways", "banner", "blocker")]),
                 also_noticed="the footer text repeats on every page")
    second = dict(verdict(), also_noticed="the footer text repeats on every page")
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []}, first, second])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert len(reviews(a)) == 1
    assert "sideways" in text_of(reviews(a)[0])
    assert not any("footer text repeats" in body_text(b) for b in both.b_bodies), "never sent to the builder"
    assert "footer text repeats" not in text_of(reviews(a)[0])
    assert final.rstrip().endswith("I also noticed the footer text repeats on every page, want me to fix it?"), final
    assert final.count("I also noticed") == 1


def test_a_problem_the_reviewer_marks_as_outside_the_request_is_not_a_fix(proj, tmp_path, monkeypatch):
    v = verdict([("the page scrolls sideways", "banner", "blocker")])
    v["problems"].append({"what": "the logo looks dated", "where": "header", "severity": "major", "in_request": False})
    both, _ = two_rounds(monkeypatch, tmp_path, [{"persona": PERSONA, "actions": []}, v, verdict()])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert "logo looks dated" not in text_of(reviews(a)[0])
    assert "I also noticed the logo looks dated, want me to fix it?" in final
    d = LR.normalize_verdict(v, ASK, "web", PERSONA)
    assert [p["what"] for p in d["problems"]] == ["the page scrolls sideways"] and d["also"] == "the logo looks dated"


# ---------- 1c. nothing needed means no fix list ------------------------------------------------------------------------
def test_a_pass_with_complaints_sends_no_fix_list_and_says_nothing_is_open(proj, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "render_web", fake_render([[]], _shots(tmp_path)))
    noisy = verdict([("the headline could be punchier", "hero", "minor"), ("the logo is small", "header", "minor")], ok=True)
    both = Both(monkeypatch, [write("index.html", FIXED), "Made it."], [{"persona": PERSONA, "actions": []}, noisy])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert reviews(a) == [] and len(both.b_bodies) == 2
    assert "minor things left" not in final and "Still open" not in final and "still open" not in final
    assert "Checked as a shopper on her phone: found nothing to fix." in final, final
    assert final.rstrip().endswith("I also noticed the headline could be punchier, want me to fix it?"), final


def test_a_verdict_with_no_problems_is_a_pass_whatever_it_says(proj, tmp_path, monkeypatch):
    monkeypatch.setattr(L, "render_web", fake_render([[]], _shots(tmp_path)))
    both = Both(monkeypatch, [write("index.html", FIXED), "Made it."],
                [{"persona": PERSONA, "actions": []}, verdict(ok=False, can=True)])
    a, _ = agent(proj)
    final = a.send(ASK)
    assert reviews(a) == [], "an empty fix list is not sent to the builder"
    assert "nothing wrong that the reviewer could see" not in "\n".join(body_text(b) for b in both.b_bodies)
    assert "found nothing to fix" in final


def test_normalize_verdict_settles_what_needs_no_change():
    minors = {"persona": PERSONA, "pass": True, "can_do_the_job": True,
              "problems": [{"what": "could be nicer", "where": "", "severity": "minor"}]}
    d = LR.normalize_verdict(minors, ASK, "web", PERSONA)
    assert d["problems"] == [] and d["pass"] and d["also"] == "could be nicer"
    d = LR.normalize_verdict({"persona": PERSONA, "pass": False, "problems": []}, ASK, "web", PERSONA)
    assert d["pass"] and d["can_do_the_job"] and d["problems"] == []
    d = LR.normalize_verdict({"persona": PERSONA, "pass": False, "problems": [
        {"what": "cannot order", "severity": "blocker"}]}, ASK, "web", PERSONA)
    assert not d["pass"] and len(d["problems"]) == 1 and d["also"] == ""
    d = LR.normalize_verdict({"persona": PERSONA, "pass": True, "problems": [], "also_noticed": ["none"]}, ASK, "web", PERSONA)
    assert d["also"] == ""                                              # "none" is not a thing noticed


# ---------- 2. the Checked line is built from fields ------------------------------------------------------------------------
def _v(persona, probs, ok=False, also=""):
    return {"persona": persona, "can_do_the_job": ok, "pass": ok, "also": also,
            "problems": [{"what": w, "where": "", "severity": s} for s, w in probs]}


# the persona strings and problem texts are the recorded ones (what the testers' chats showed)
RECORDED = [
    ("a board member reading the Q3 board update PDF before the board meeting",
     [("major", "slide titles read as broken fragments: 'Board update the quarter' and 'Headcount the summary' look like "
                "words are missing (e.g. 'Q3')"),
      ("major", "the same line 'Total budget the year: $2.27M' is repeated on the headcount page where it does not "
                "belong, and it reads as missing words ('for the year')"),
      ("minor", "the headcount page only says '40 people' with no breakdown or context, and the budget line has "
                "nothing to do with headcount"),
      ("minor", "only 2 of 8 pages could be checked, so the other six pages are unverified")]),
    ("a finance person or budget owner opening Budget_2026.xlsx to check the yearly cost totals per line, after asking "
     "for the Total column SUM to",
     [("blocker", "Salaries Total shows ### instead of a number, so the fixed total can't be read"),
      ("blocker", "Grand Total cell in the Total column shows ### instead of a number, so the overall budget can't be seen"),
      ("major", "Column is too narrow for the numbers, so the fix can't be confirmed without widening it by hand"),
      ("minor", "Chart is repeated on page 2 with no new content, and the table has only Q1 to Q3 so it's unclear if Q4 "
                "is missing from the Total")]),
    ("a shop owner (Maya) or her counter staff using a bouquet price list to quote prices to customers buying roses, "
     "tulips, sunflowers, wedding a",
     [("minor", "the price list is buried on page 2 behind a cover/legend page"),
      ("minor", "the first thing seen is a"),
      ("minor", "every row repeats the long 'Illustrative retail price set by shop owner; Maya's Flowers'"),
      ("minor", "'Range low' and 'Range high' columns were not asked for and are unexplained, and the")]),
    ("a business owner checking 2025 monthly revenue from a cleaned data file, reading the chart in the chat and "
     "verifying the totals against a sm",
     [("major", "the chart image appears blank: no text, axes, labels or bars can be seen, so there is no"),
      ("major", "axis labels I asked for cannot be found"),
      ("minor", "no small table of monthly totals to check against and cannot confirm the axes are labeled as I asked "
                "(months, revenue amount and currency).")]),
    ("a prospective visitor on her phone checking when and where the studio's open house is and signing up with her "
     "name and email",
     [("major", "could not find the open house date, time and location details on the page at phone width, so I can't "
                "confirm when and where it is"),
      ("major", "typing an invalid email ('not-an-email') shows 'Please enter your name.' instead of an email error, "
                "which is misleading and suggests the email isn't validated"),
      ("minor", "the confirmation message could not be found by the check when looking for it separately at phone width, "
                "so it may not be clearly visible or labelled")]),
    ("a customer on her phone ordering a bouquet from Maya's Flowers and choosing a pick-up day",
     [("major", "keyboard focus is invisible on the Pick-up day field, so someone tabbing through the form cannot see where "
                "they are"),
      ("major", "entering a closed day like 'Sunday' just gives the generic 'Please fill in your name, pick-up day is a "
                "field where I may have to type a day, with only a hint about opening"),
      ("minor", "confirmation text is cut off in the check ('Maya will be in ...'), so I can't confirm")]),
]
_DANGLE = re.compile(r"(?:^|\s)(?:a|an|the|to|at|of|or|and|for|with|in|on|by|from|is|are|so it's|it's|that|which)"
                     r"(?=[.:;,)]|$)", re.I)


def assert_clean(line: str):
    main = line.split("\n\n")[0]
    assert "  " not in line and not re.search(r"\s[,;:.)]|[,;:]{2,}|\(\s|\.\.|;\s*\.|:\s*\.|,\s*\.", line), line
    assert not _DANGLE.search(main), main                                      # no sentence, item or clause ends on a small word
    assert not re.search(r"(?:^|\s)(?:a|an|the|to|at|of|or)\s*$", main, re.I), main
    assert main.startswith("Checked") and main.endswith("."), main
    assert len(re.split(r"(?<=[a-z0-9)])\.\s", main)) <= 2, main               # one or two sentences
    m = re.match(r"Checked as (.+?): ", main)
    if m:
        assert len(m.group(1).split()) <= 8, m.group(1)
    assert main.count("'") % 2 == 0 and main.count('"') % 2 == 0 and main.count("(") == main.count(")"), main
    assert "looked it over" not in main and "tried it" not in main and "minor things left" not in main
    assert "so it's" not in main


@pytest.mark.parametrize("persona,probs", RECORDED)
@pytest.mark.parametrize("shape", ["fixed", "first-look-only", "changed-after", "all-fixed"])
def test_the_checked_line_is_clean_for_every_recorded_input(persona, probs, shape):
    first = _v(persona, probs)
    st = {"persona": persona, "rounds": 1, "evals": [first]}
    if shape == "fixed":
        st["evals"].append(_v(persona, probs[:1]))
    elif shape == "all-fixed":
        st["evals"].append(_v(persona, [], ok=True))
    elif shape == "changed-after":
        st["changed"] = True
    line = LR.summary_line("doc", st)
    assert_clean(line)


def test_the_checked_line_shape_with_recorded_inputs():
    persona, probs = RECORDED[0]
    st = {"persona": persona, "evals": [_v(persona, probs), _v(persona, [], ok=True)]}
    line = LR.summary_line("doc", st)
    assert line == ("Checked as a board member: fixed 4 things: slide titles read as broken fragments; only 2 of 8 pages "
                    "could be checked. Nothing left open."), line
    persona, probs = RECORDED[1]
    st = {"persona": persona, "evals": [_v(persona, probs), _v(persona, probs[:1])]}
    assert LR.summary_line("doc", st) == (
        "Checked as a finance person or budget owner: fixed 2 things: column is too narrow for the numbers; chart is "
        "repeated on page 2 with no new content. Still open: salaries Total shows ### instead of a number."), \
        LR.summary_line("doc", st)
    persona, probs = RECORDED[3]
    st = {"persona": persona, "evals": [_v(persona, probs)]}
    assert LR.summary_line("doc", st) == (
        "Checked as a business owner: fixed nothing. Still open: axis labels I asked for cannot be found."), \
        LR.summary_line("doc", st)


def test_no_fragment_is_printed_when_a_part_would_be_malformed():
    persona = "a"                                                    # an empty persona once the dangling word goes
    probs = [("minor", "the first thing seen is a"), ("minor", "every row repeats the"), ("minor", "so it's")]
    st = {"persona": persona, "evals": [_v(persona, probs), _v(persona, [], ok=True)]}
    line = LR.summary_line("doc", st)
    assert line == "Checked: fixed 3 things. Nothing left open.", line
    st = {"persona": "", "evals": [_v("", probs)]}                  # no persona at all: the neutral one is used
    line = LR.summary_line("doc", st)
    assert line == "Checked it as someone opening this file for the first time.", line      # nothing whole to say: no claims, no count
    assert_clean(line)


@pytest.mark.parametrize("raw,short", [
    ("a board member reading the Q3 board update deck before the meeting, relying on its figures and wording",
     "a board member"),
    ("a finance person or budget owner opening Budget_2026.xlsx to check the yearly totals", "a finance person or budget owner"),
    ("a customer on her phone ordering a", "a customer on her phone"),
    ("a shop owner (Maya) or her counter staff using a bouquet price list", "a shop owner or her counter staff"),
    ("someone opening this file for the first time", "someone opening this file for the first time"),
    ("A customer on her phone", "a customer on her phone"),
    ("a", ""), ("", ""), ("the", ""), ("to be or not to", "")])
def test_the_short_persona(raw, short):
    got = LR.short_persona(raw)
    assert got == short, got
    assert len(got.split()) <= 8


def test_an_item_is_a_whole_phrase_or_nothing():
    f = lambda w: LR._item({"what": w, "where": "", "severity": "minor"})        # noqa: E731
    assert f("Salaries Total shows ### instead of a number, so the fixed total can't be read") == \
        "salaries Total shows ### instead of a number"
    assert f("could not find the open house date, time and location details on the page at phone width, so I can't "
             "confirm") == "missing open house date, time and location details"
    assert f("the first thing seen is a") == ""                                   # ends on an article
    assert f("every row repeats the long 'Illustrative retail price set by shop owner; Maya's Flowers'") == ""
    assert f("x" * 90) == "" and f("") == ""
    assert f("axis labels I asked for cannot be found") == "axis labels I asked for cannot be found"
    for bad in ("ends on a", "ends on the", "goes to", "is at", "a list of", "this or", "that is so it's", "open (quote",
                "an 'open quote", "trailing comma,", "double  space here", "-dash start"):
        assert not LR._fine(bad), bad
    for ok in ("the page scrolls sideways", "salaries Total shows ###", "it can't be found", "the 'Buy now' button does nothing"):
        assert LR._fine(ok), ok


def test_the_checked_line_ends_with_the_offer_on_its_own_line():
    st = {"persona": PERSONA, "evals": [_v(PERSONA, [("major", "the page scrolls sideways")]),
                                        _v(PERSONA, [], ok=True, also="the footer repeats on seven slides")]}
    line = LR.summary_line("web", st)
    assert line.split("\n\n") == ["Checked as a shopper on her phone: fixed 1 thing: the page scrolls sideways. Nothing left open.",
                                  "I also noticed the footer repeats on seven slides, want me to fix it?"]
    st["evals"][1]["also"] = "the footer is a"                                # a fragment is not offered
    assert "\n\n" not in LR.summary_line("web", st)
