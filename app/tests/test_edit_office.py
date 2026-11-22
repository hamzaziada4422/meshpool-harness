import openpyxl
import pytest
from docx import Document
from pptx import Presentation

from meshpool_harness.agent import office_mcp_server as O


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))


def test_word_text_comment_and_append_keep_formatting(tmp_path):
    d = Document()
    para = d.add_paragraph()
    para.add_run("Total due: ").bold = True
    para.add_run("100 EGP")
    d.save(str(tmp_path / "a.docx"))
    msg, err = O.edit_office_file("a.docx", [{"op": "replace_text", "find": "100 EGP", "replace": "120 EGP"},
                                             {"op": "add_comment", "find": "Total", "text": "Check VAT"},
                                             {"op": "append_paragraph", "text": "Thanks"},
                                             {"op": "replace_text", "find": "nothing here", "replace": "x"}])
    assert not err and "a (edited).docx" in msg and "text not found" in msg
    out = Document(str(tmp_path / "a (edited).docx"))
    assert out.paragraphs[0].text == "Total due: 120 EGP" and out.paragraphs[0].runs[0].bold
    assert out.paragraphs[-1].text == "Thanks" and len(list(out.comments)) == 1
    assert Document(str(tmp_path / "a.docx")).paragraphs[0].text == "Total due: 100 EGP"      # the original is untouched


def test_excel_cells_formulas_and_comments(tmp_path):
    wb = openpyxl.Workbook()
    wb.active["A1"] = 5
    wb.save(str(tmp_path / "b.xlsx"))
    msg, err = O.edit_office_file("b.xlsx", [{"op": "set_cell", "cell": "A2", "value": 7}, {"op": "set_cell", "cell": "A3", "value": "=SUM(A1:A2)"},
                                             {"op": "add_comment", "cell": "A3", "text": "total"}, {"op": "add_row", "values": [1, 2]}], in_place=True)
    assert not err
    ws = openpyxl.load_workbook(str(tmp_path / "b.xlsx")).active
    assert ws["A3"].value == "=SUM(A1:A2)" and ws["A3"].comment.text == "total" and ws["A4"].value == 1


def test_powerpoint_text_and_notes(tmp_path):
    prs = Presentation()
    sl = prs.slides.add_slide(prs.slide_layouts[5])
    sl.shapes.title.text = "Q3 results"
    prs.save(str(tmp_path / "c.pptx"))
    msg, err = O.edit_office_file("c.pptx", [{"op": "replace_text", "find": "Q3", "replace": "Q4"}, {"op": "set_notes", "slide": 1, "text": "Say hello"}])
    out = Presentation(str(tmp_path / "c (edited).pptx"))
    assert out.slides[0].shapes.title.text == "Q4 results" and out.slides[0].notes_slide.notes_text_frame.text == "Say hello"


def test_the_tool_is_registered_and_refuses_other_files(tmp_path):
    assert "edit_office_file" in O.TOOLS
    (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
    assert O.edit_office_file("x.txt", [{"op": "replace_text"}])[1] is True


def test_word_tracked_changes_are_real_revisions(tmp_path):
    d = Document()
    d.add_paragraph().add_run("The fee is 100 EGP per month and renews yearly.")
    d.save(str(tmp_path / "t.docx"))
    msg, err = O.edit_office_file("t.docx", [{"op": "tracked_replace", "find": "100 EGP", "replace": "120 EGP", "author": "Lawyer"},
                                             {"op": "tracked_delete", "find": " and renews yearly"},
                                             {"op": "tracked_insert_after", "find": "per month", "text": " (excluding VAT)"}])
    assert not err and "Not done" not in msg
    xml = Document(str(tmp_path / "t (edited).docx")).element.xml
    assert xml.count("<w:del ") == 2 and xml.count("<w:ins ") == 2 and 'w:author="Lawyer"' in xml
    assert "<w:delText" in xml and "100 EGP" in xml and "120 EGP" in xml
    assert "The fee is " in Document(str(tmp_path / "t (edited).docx")).paragraphs[0].text
    import subprocess
    from engines.forge.common import find_soffice
    if find_soffice():                                         # Word's own engine can open it
        subprocess.run([find_soffice(), "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(tmp_path / "t (edited).docx")], capture_output=True, timeout=120)
        assert (tmp_path / "t (edited).pdf").exists()
