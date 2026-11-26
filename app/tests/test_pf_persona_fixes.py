"""Persona tester fixes for 0.0.18 (PF-nn): Office files open in another app, chat titles, the keep-awake process,
memory categories and the onboarding import."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from docx import Document

from meshpool_harness.agent import office_mcp_server as O


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / ".home"))
    return tmp_path


def _memo(p: Path, text: str = "The budget is 4,000.") -> bytes:
    d = Document()
    d.add_paragraph(text)
    d.save(str(p))
    return p.read_bytes()


def _text(p: Path) -> str:
    return "\n".join(x.text for x in Document(str(p)).paragraphs)


# ---------- PF-02: an in-place Office edit wrote over a file another app had open -----------------------------------

@pytest.mark.parametrize("lock, app", [(".~lock.Halden-memo.docx#", "LibreOffice"),
                                       ("~$Halden-memo.docx", "Microsoft Office"),
                                       ("~$lden-memo.docx", "Microsoft Office")])     # Word shortens long names
def test_pf02_open_file_gets_a_copy_not_an_in_place_write(project, lock, app):
    memo = project / "Halden-memo.docx"
    before = _memo(memo)
    (project / lock).write_text("user,host,123;")
    msg, err = O.edit_office_file("Halden-memo.docx", [{"op": "replace_text", "find": "4,000", "replace": "4,500"}],
                                  in_place=True)
    assert not err, msg
    assert memo.read_bytes() == before                          # the open file is left alone
    copy = project / "Halden-memo (edited).docx"
    assert "4,500" in _text(copy)
    assert f"open in {app}" in msg and "Halden-memo.docx is unchanged" in msg and str(copy) in msg


def test_pf02_excel_owner_file_and_a_named_output_that_is_open(project):
    from openpyxl import Workbook, load_workbook
    wb = Workbook()
    wb.active["A1"] = 1
    wb.save(str(project / "Budget.xlsx"))
    wb.save(str(project / "Out.xlsx"))
    (project / "~$Budget.xlsx").write_bytes(b"x")
    msg, err = O.edit_office_file("Budget.xlsx", [{"op": "set_cell", "sheet": "Sheet", "cell": "A1", "value": 2}],
                                  in_place=True)
    assert not err and "open in Microsoft Office" in msg, msg
    assert load_workbook(str(project / "Budget.xlsx"))["Sheet"]["A1"].value == 1
    assert load_workbook(str(project / "Budget (edited).xlsx"))["Sheet"]["A1"].value == 2
    (project / ".~lock.Out.xlsx#").write_text("x")             # an explicit output that is open elsewhere
    msg, err = O.edit_office_file("Budget.xlsx", [{"op": "set_cell", "sheet": "Sheet", "cell": "A1", "value": 3}],
                                  output="Out.xlsx")
    assert not err and "open in LibreOffice" in msg, msg
    assert load_workbook(str(project / "Out.xlsx"))["Sheet"]["A1"].value == 1
    assert load_workbook(str(project / "Out (copy).xlsx"))["Sheet"]["A1"].value == 3


def test_pf02_no_lock_still_edits_in_place(project):
    memo = project / "Halden-memo.docx"
    _memo(memo)
    (project / "~$other.docx").write_text("x")                  # another file's lock does not count
    msg, err = O.edit_office_file("Halden-memo.docx", [{"op": "replace_text", "find": "4,000", "replace": "4,500"}],
                                  in_place=True)
    assert not err and msg.startswith("Changed"), msg
    assert "4,500" in _text(memo)


# ---------- PF-03: chat titles cut a path in the middle and stopped at "Hi" -----------------------------------------

@pytest.mark.parametrize("first, title", [
    ("Summarise the PDF at /home/daniel/Reports/q3.pdf", "Summarise the PDF at q3.pdf"),
    ("Hi! The file budget.xlsx has errors, fix them", "The file budget.xlsx has errors, fix them"),
    ("Hi!\nPlease tidy ~/Documents/notes.md", "Tidy notes.md"),
    ("Hello, can you read C:\\Users\\dan\\Docs\\memo.docx", "Read memo.docx"),
    ('Hey Sam, what is in "/home/d/My Reports/q 3.pdf"?', "What is in q 3.pdf"),
    ("Summarise /home/daniel/Reports/2024/Quarterly-financial-statements-final.pdf please",
     "Summarise Quarterly-financial-statements-final.pdf…"),
    ("hi there", "Hi there"),
    ("/plan build a thing", "Build a thing"),
    ("Compare this and/or that", "Compare this and/or that"),
])
def test_pf03_titles_keep_paths_whole_and_skip_greetings(first, title):
    from meshpool_harness.ui.agent_api import nice_title
    assert nice_title(first) == title


def test_pf03_agent_title_does_not_cut_a_path_at_80_characters(tmp_path):
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.ui.agent_api import nice_title
    from test_agent_robustness import _script
    msg = "Summarise the PDF at /home/daniel/Reports/2024/Board/Finance/Quarterly/statements/final-version.pdf"
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [{"role": "assistant", "content": "Done."}])
    a.send(msg)
    assert a.title == msg
    assert nice_title(a.title) == "Summarise the PDF at final-version.pdf"
    b = Agent(str(tmp_path), mode="auto")
    _script(b, [{"role": "assistant", "content": "Done."}])
    b.send("Hi!\nThe file budget.xlsx has errors")
    assert nice_title(b.title) == "The file budget.xlsx has errors"
