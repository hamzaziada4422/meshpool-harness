"""Release review item 6: office_work kept pulling code requests out of Code and Auto chats when they named an office
file. Code cues win now; the office file has to be the deliverable or the input."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshpool_harness.office_intent import office_work  # noqa: E402
from test_rescore3_behaviour import DANIEL, PRIYA, SARA  # noqa: E402

CODE = [
    "fix the bug where report.xlsx export crashes",
    "implement generate_report so it writes report.docx",
    "Our Excel importer drops rows, fix it",
    "Add support for .pptx uploads in the upload handler",
    "add a slide-in animation to the sidebar component",
    "fix the slides carousel",
    "Add PDF export to the app",
    "fix the bug in export.py that writes report.xlsx",
]

OFFICE = [PRIYA, *SARA, *DANIEL,
          "make a 6 slide deck from report.pdf",
          "add Q4 to Budget.xlsx",
          "convert the deck to PDF",
          "Use the VLOOKUP function in Budget.xlsx to fill the prices"]


@pytest.mark.parametrize("text", CODE)
def test_code_requests_that_name_an_office_file_stay_code(text):
    assert office_work(text) is False


@pytest.mark.parametrize("text", OFFICE)
def test_office_deliverables_and_inputs_are_still_office_work(text):
    assert office_work(text) is True
