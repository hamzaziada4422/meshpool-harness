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


# ---------- OF-03, OF-04, OF-05, OF-06: Word reads lost content controls, tracked insertions, footers, footnotes ------

def _w(xml: str):
    """A Word XML element; the namespace goes on the outer tag."""
    head, rest = xml.split(">", 1)
    return parse_xml(f"{head} {nsdecls('w')}>{rest}")


def test_of03_text_inside_a_content_control_is_read(tmp_path):
    d = Document()
    d.add_paragraph("Intro.")
    d.element.body.insert(len(d.element.body) - 1, _w(
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>Inside a content control</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell in a control</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:sdtContent></w:sdt>"))
    d.save(str(tmp_path / "a.docx"))
    out = _read(tmp_path / "a.docx")
    assert "Inside a content control" in out and "cell in a control" in out


def test_of04_tracked_insertions_are_read_and_deletions_are_not(tmp_path):
    d = Document()
    p = d.add_paragraph()
    p.add_run("Price is ")
    p._p.append(_w('<w:ins w:id="1" w:author="a" w:date="2024-01-01T00:00:00Z"><w:r><w:t>500</w:t></w:r></w:ins>'))
    p._p.append(_w('<w:del w:id="2" w:author="a" w:date="2024-01-01T00:00:00Z"><w:r><w:delText>400</w:delText></w:r></w:del>'))
    p.add_run(" dollars.")
    d.save(str(tmp_path / "a.docx"))
    out = _read(tmp_path / "a.docx")
    assert "Price is 500 dollars." in out and "400" not in out.split("[tracked")[0]
    assert "1 insertion(s) and 1 deletion(s)" in out


def test_of05_footer_is_read_when_the_header_has_text(tmp_path):
    d = Document()
    d.add_paragraph("Body")
    d.sections[0].header.paragraphs[0].text = "HEADER TEXT"
    d.sections[0].footer.paragraphs[0].text = "Page footer: confidential"
    d.add_section()                                    # linked: the same header is not listed again
    d.save(str(tmp_path / "a.docx"))
    out = _read(tmp_path / "a.docx")
    assert "[header] HEADER TEXT" in out and "[footer] Page footer: confidential" in out
    assert out.count("HEADER TEXT") == 1


def test_of06_footnotes_are_read(tmp_path):
    d = Document()
    d.add_paragraph("Revenue grew 12 percent.")
    d.save(str(tmp_path / "a.docx"))
    notes = (f'<w:footnotes {nsdecls("w")}><w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r>'
             '</w:p></w:footnote><w:footnote w:id="1"><w:p><w:r><w:t>Source: annual report, page 4.</w:t></w:r>'
             '</w:p></w:footnote></w:footnotes>')
    with zipfile.ZipFile(tmp_path / "a.docx", "a") as z:
        z.writestr("word/footnotes.xml", notes)
    out = _read(tmp_path / "a.docx")
    assert "## Footnotes" in out and "[^1] Source: annual report, page 4." in out


# ---------- OF-07: CSV files from Excel on Windows (Windows-1252, UTF-16) read as garbage ----------------------------

def test_of07_windows_1252_semicolon_csv_reads_its_accents_and_euro_sign(tmp_path):
    f = tmp_path / "win.csv"
    f.write_bytes("Name;Price\nCaf\xe9 cr\xe8me;5€\n“Quoted”;7\n".encode("cp1252"))
    out = _read(f)
    assert "| Café crème | 5€ |" in out and "“Quoted”" in out and "�" not in out


def test_of07_utf16_csv_from_excel_reads_as_text(tmp_path):
    f = tmp_path / "u16.csv"
    f.write_bytes("Name\tPrice\nCaf\xe9\t5\n".encode("utf-16"))
    out = _read(f)
    assert "| Café | 5 |" in out and "\x00" not in out


def test_of07_a_cut_utf8_file_does_not_switch_to_another_encoding():
    raw = ("é" * 10).encode("utf-8")[:-1]                  # the read stopped inside the last character
    assert docread.decode_text(raw, cut=True) == "é" * 9


def test_of07_preview_table_reads_windows_1252_and_utf16(tmp_path):
    from meshpool_harness.ui import previews as PV
    f = tmp_path / "win.csv"
    f.write_bytes("Name;Price\nCaf\xe9;5€\n".encode("cp1252"))
    assert PV._csv_sheet(f)[0]["rows"][1] == ["Café", "5€"]
    f.write_bytes("Name,Price\nCaf\xe9,5\n".encode("utf-16"))
    assert PV._csv_sheet(f)[0]["rows"][1] == ["Café", "5"]


# ---------- OF-10: view_output drew a semicolon CSV as one column, showed the BOM, and read the whole file ----------

def test_of10_view_table_splits_semicolons_and_drops_the_bom(tmp_path):
    from meshpool_harness import view as V
    f = tmp_path / "eu.csv"
    f.write_bytes("﻿Region;Revenue\nNorth;1,5\n".encode("utf-8"))
    html = V._table_html(f)
    assert "<th>Region</th><th>Revenue</th>" in html and "﻿" not in html
    assert "<td>North</td><td>1,5</td>" in html


def test_of10_view_table_reads_only_the_start_of_a_big_file(tmp_path, monkeypatch):
    import builtins
    import io as _io
    from meshpool_harness import view as V
    f = tmp_path / "big.csv"
    f.write_bytes(b"a,b\n" + b"1,2\n" * 1_000_000)          # 4 MB
    read = []
    real_open = _io.open

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def __getattr__(self, k):
            return getattr(self.fh, k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

        def __iter__(self):
            for line in self.fh:
                read.append(len(line))
                yield line

        def read(self, n=-1):
            data = self.fh.read(n)
            read.append(len(data))
            return data

    def spy_open(file, *a, **k):
        fh = real_open(file, *a, **k)
        return Spy(fh) if str(file) == str(f) else fh
    monkeypatch.setattr(_io, "open", spy_open)
    monkeypatch.setattr(builtins, "open", spy_open)
    html = V._table_html(f)
    assert html.count("<tr>") == 200
    assert 0 < sum(read) <= 2_000_001
