"""Bugs a backend developer persona (Omar: `meshpool -p`, the terminal UI and web Code chats) hit on 0.0.18, IDs
OM-01 to OM-10. Scripted model replies or the mock in testkit; no paid model is called."""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meshpool_harness import capabilities as CAP  # noqa: E402
from meshpool_harness import chats  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402


# ---------------------------------------------------------------------------------------------------- OM-02
@pytest.mark.parametrize("text", [
    "make report.py print the totals by month",
    "make the tests in report.py pass",
    "update the docs for the cli and make the examples run",
    "make the docs build pass",
    "fix docs/index.md and make the links work",
    "add a usage section to README.md and make it short",
    "write docs for dedupe.py",
    "make the src/reports/ folder a package",
])
def test_om02_a_code_file_or_the_word_docs_is_not_a_word_document(text):
    """OM-02: "report" in report.py and the word "docs" counted as a request for a Word document, so a code request
    got a hidden note telling the agent to build one."""
    assert CAP.needs(text) == [], text


@pytest.mark.parametrize("text", ["write a report on Q3 sales", "make a Word doc of the notes", "I need a .docx",
                                  "make me a doc summarising the budget", "make report.docx with the totals",
                                  "export it as report.pdf"])
def test_om02_real_requests_for_a_document_still_count(text):
    assert CAP.needs(text), text
