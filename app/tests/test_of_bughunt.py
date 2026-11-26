"""Bug hunt 0.0.18, office side (OF-nn): edits that changed the wrong text, reads that lost text, files that gave a
misleading error, and work that never ended."""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from meshpool_harness import docread
from meshpool_harness.agent import office_mcp_server as O


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    monkeypatch.setenv("MH_HOME", str(tmp_path / ".home"))


def _read(p: Path, **kw) -> str:
    return docread.extract(p, ocr="never", **kw)


# ---------- OF-01: replace_text after a hyperlink edited the wrong run -----------------------------------------------

def test_of01_replace_after_a_hyperlink_changes_the_matched_text(tmp_path):
    d = Document()
    p = d.add_paragraph()
    p.add_run("See ")
    p._p.append(parse_xml(f'<w:hyperlink {nsdecls("w")}><w:r><w:t>the website</w:t></w:r></w:hyperlink>'))
    p.add_run(" for the price of 100 dollars.")
    d.save(str(tmp_path / "a.docx"))
    msg, err = O.edit_office_file("a.docx", [{"op": "replace_text", "find": "100", "replace": "200"}])
    assert not err, msg
    assert Document(str(tmp_path / "a (edited).docx")).paragraphs[0].text == "See the website for the price of 200 dollars."


# ---------- OF-02: linked headers and merged cells were replaced twice -----------------------------------------------

def test_of02_linked_header_and_merged_cell_are_replaced_once(tmp_path):
    d = Document()
    d.add_paragraph("Body")
    d.sections[0].header.paragraphs[0].text = "Draft"
    d.add_section()                                       # its header is linked to the first one
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).merge(t.cell(0, 1))
    t.cell(0, 0).text = "Total"
    d.save(str(tmp_path / "a.docx"))
    msg, err = O.edit_office_file("a.docx", [{"op": "replace_text", "find": "Draft", "replace": "Draft v2"},
                                             {"op": "replace_text", "find": "Total", "replace": "Total due"}])
    assert not err, msg
    assert "'Draft': 1 place(s)" in msg and "'Total': 1 place(s)" in msg
    out = Document(str(tmp_path / "a (edited).docx"))
    assert out.sections[0].header.paragraphs[0].text == "Draft v2"
    assert out.tables[0].cell(0, 0).text == "Total due"


def test_of02_edit_adds_no_header_part_to_a_document_without_one(tmp_path):
    d = Document()
    d.add_paragraph("Hello")
    d.save(str(tmp_path / "a.docx"))
    O.edit_office_file("a.docx", [{"op": "replace_text", "find": "Hello", "replace": "Hi"}])
    names = zipfile.ZipFile(tmp_path / "a (edited).docx").namelist()
    assert not [n for n in names if "header" in n or "footer" in n]


# ---------- OF-14: a failed edit left a half-made copy; a failed in-place save could truncate the file -------------

def test_of14_failed_edit_leaves_no_copy_and_keeps_the_original(tmp_path, monkeypatch):
    import docx.document
    d = Document()
    d.add_paragraph("Keep me")
    d.save(str(tmp_path / "a.docx"))
    before = (tmp_path / "a.docx").read_bytes()

    def broken_save(self, path):
        Path(path).write_bytes(b"PK half a file")
        raise OSError("disk full")
    monkeypatch.setattr(docx.document.Document, "save", broken_save)
    msg, err = O.edit_office_file("a.docx", [{"op": "replace_text", "find": "Keep", "replace": "Lose"}], in_place=True)
    assert err and "disk full" in msg
    assert (tmp_path / "a.docx").read_bytes() == before
    msg, err = O.edit_office_file("a.docx", [{"op": "replace_text", "find": "Keep", "replace": "Lose"}])
    assert err
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["a.docx"]


# ---------- OF-16: PowerPoint replace_text missed tables and grouped shapes ------------------------------------------

def test_of16_pptx_replace_reaches_tables_and_groups(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tbl = s.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    tbl.cell(1, 1).text = "FY23 revenue"
    grp = s.shapes.add_group_shape()
    grp.shapes.add_textbox(Inches(1), Inches(3), Inches(3), Inches(1)).text_frame.text = "FY23 outlook"
    prs.save(str(tmp_path / "d.pptx"))
    msg, err = O.edit_office_file("d.pptx", [{"op": "replace_text", "find": "FY23", "replace": "FY24"}])
    assert not err, msg
    out = Presentation(str(tmp_path / "d (edited).pptx")).slides[0]
    texts = []
    for sh in out.shapes:
        if sh.has_table:
            texts += [c.text for r in sh.table.rows for c in r.cells]
        elif sh.shape_type == 6:
            texts += [x.text_frame.text for x in sh.shapes]
    assert "FY24 revenue" in texts and "FY24 outlook" in texts and not any("FY23" in t for t in texts)


def test_of16_pptx_replace_after_a_line_break_keeps_offsets(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tf = s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame
    tf.paragraphs[0].add_run().text = "Line one"
    tf.paragraphs[0].add_line_break()
    tf.paragraphs[0].add_run().text = "costs 100 now"
    prs.save(str(tmp_path / "d.pptx"))
    msg, err = O.edit_office_file("d.pptx", [{"op": "replace_text", "find": "100", "replace": "250"}])
    assert not err, msg
    tf = Presentation(str(tmp_path / "d (edited).pptx")).slides[0].shapes[0].text_frame
    assert [r.text for r in tf.paragraphs[0].runs] == ["Line one", "costs 250 now"]
