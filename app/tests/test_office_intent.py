"""office_intent.styled_deliverable: which Work requests get the office engines under MH_OFFICE_DIRECT=1 (a new deck,
report or memo, financial model or dashboard) and which are done by hand with openpyxl, python-docx or python-pptx
(edits and derivations of a file the user has, small builds). Plain rules, no model call."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.office_intent import styled_deliverable  # noqa: E402

ENGINES = [
    "Make a 10-slide pitch deck for investors about our Q3 results",
    "Create a presentation from notes.docx",
    "Turn the findings in notes.docx into a deck",
    "Write a memo to the leadership team about the reorg",
    "Build a three-statement financial model for ACME",
    "Build a DCF model for Nvidia",
    "Make a sales dashboard workbook from sales.csv",
    "Prepare a board report on Q3 performance using q3.xlsx",
    "I need a presentation for Monday's all-hands",
    "Update q3.xlsx with October numbers and then make a deck from it",
]

BY_HAND = [
    "Sort score.xlsx descending by midterm1 into sorted_score.xlsx",
    "Filter orders.xlsx to rows where region is East and save as east.xlsx",
    "Add a column Total to grades.xlsx that sums the scores",
    "Fill the empty cells in column C of data.xlsx with 0",
    "Convert report.docx to PDF",
    "Extract the tables from annual_report.pdf into an xlsx",
    "Rename Sheet1 in data.xlsx to Sales",
    "Recompute the averages in score.xlsx",
    "Add a bar chart of revenue to slide 3 of deck.pptx",
    "Add 2 slides to deck.pptx summarizing the pricing",
    "Make the report in q3.docx shorter",
    "Update the dashboard in kpi.xlsx with October numbers",
    "Make a small spreadsheet with three rows of test data",
    "Write a docx with the meeting notes below",
    "Report the total revenue in sales.xlsx",
    "What is the average midterm score?",
]


@pytest.mark.parametrize("text", ENGINES)
def test_new_styled_deliverables_get_the_engines(text):
    assert styled_deliverable(text)


@pytest.mark.parametrize("text", BY_HAND)
def test_edits_derivations_and_small_builds_do_not(text):
    assert not styled_deliverable(text)


def test_hidden_notes_after_the_separator_do_not_count():
    assert not styled_deliverable("Sort score.xlsx by name\n\n​[Meshpool] Make a deck if asked.")
