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
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / ".home"))


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


# ---------- OF-08: RTF lost every accented letter and kept font names and control words ------------------------------

def test_of08_rtf_from_textedit_and_word_reads_clean_text(tmp_path):
    cocoa = ("{\\rtf1\\ansi\\ansicpg1252\\cocoartf2709\n{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
             "{\\colortbl;\\red255\\green255\\blue255;}\n{\\*\\expandedcolortbl;;}\n"
             "\\pard\\tx566\\pardirnatural\\partightenfactor0\n\n\\f0\\fs24 \\cf0 Caf\\'e9 cr\\'e8me \\\n"
             "Second line}")
    (tmp_path / "mac.rtf").write_text(cocoa, encoding="latin-1")
    out = _read(tmp_path / "mac.rtf")
    assert out == "# mac.rtf\nCafé crème \nSecond line"
    word = ("{\\rtf1\\ansi\\ansicpg1252{\\fonttbl{\\f0\\froman Times New Roman;}}{\\*\\generator Word;}"
            "{\\info{\\title Secret title}}\\uc1\\pard Price \\u8364\\'80 5{\\field{\\*\\fldinst HYPERLINK x}"
            "{\\fldrslt link}}\\par\\trowd\\cellx1000 A\\cell B\\cell\\row}")
    (tmp_path / "w.rtf").write_text(word, encoding="latin-1")
    out = _read(tmp_path / "w.rtf")
    assert "Price € 5link" in out and "A | B |" in out
    assert "Times" not in out and "Word" not in out and "Secret" not in out and "HYPERLINK" not in out


# ---------- OF-09: a password-protected Office file read as "Package not found" and previewed as "damaged" ----------

def _cfb(streams=("EncryptionInfo", "EncryptedPackage")) -> bytes:
    """A minimal OLE compound file with empty streams of these names: the shape of an encrypted .docx/.xlsx/.pptx."""
    import struct
    FREE, END, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
    head = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16 + struct.pack(
        "<HHHHH6xIIIIIIIII", 0x3E, 3, 0xFFFE, 9, 6, 0, 1, 1, 0, 4096, END, 0, END, 0)
    head += struct.pack("<I", 0) + struct.pack("<I", FREE) * 108
    fat = struct.pack("<II", FATSECT, END) + struct.pack("<I", FREE) * 126

    def entry(name, typ, child=NOSTREAM, right=NOSTREAM):
        n = (name + "\0").encode("utf-16-le")
        return (n.ljust(64, b"\0") + struct.pack("<HBBIII", len(n), typ, 1, NOSTREAM, right, child)
                + b"\0" * 36 + struct.pack("<III", END, 0, 0))
    ents = [entry("Root Entry", 5, child=1)]
    for i, s in enumerate(streams):
        ents.append(entry(s, 2, right=(i + 2) if i + 1 < len(streams) else NOSTREAM))
    while len(ents) % 4:
        ents.append(b"\0" * 128)
    return head + fat + b"".join(ents)


@pytest.mark.parametrize("name", ["contract.docx", "model.xlsx", "deck.pptx"])
def test_of09_password_protected_office_file_says_so(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(_cfb())
    out = _read(f)
    assert out.startswith(f"{name} is password-protected") and "Package" not in out and str(tmp_path) not in out


def test_of09_old_binary_file_with_a_new_name_says_so(tmp_path):
    f = tmp_path / "old.docx"
    f.write_bytes(_cfb(("WordDocument", "1Table")))
    assert "old binary Office file saved with a .docx name" in _read(f)


def test_of09_preview_of_a_password_protected_file_does_not_say_damaged(tmp_path, monkeypatch):
    from meshpool_harness.ui import previews as PV
    from meshpool_harness.ui import workbench as W
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    for name in ("a.docx", "b.xlsx"):
        (root / name).write_bytes(_cfb())
        with pytest.raises(ValueError) as e:
            PV.meta(str(root), name)
        assert "password-protected" in str(e.value) and "damaged" not in str(e.value)


# ---------- OF-11: read_document loaded a whole plain-text or HTML file to show the first part of it ------------------

def _count_reads(monkeypatch, target: Path) -> list:
    import builtins
    import io as _io
    got = []
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

        def read(self, n=-1):
            data = self.fh.read(n)
            got.append(len(data))
            return data

    def spy_open(file, *a, **k):
        fh = real_open(file, *a, **k)
        return Spy(fh) if str(file) == str(target) else fh
    monkeypatch.setattr(_io, "open", spy_open)
    monkeypatch.setattr(builtins, "open", spy_open)
    return got


def test_of11_a_big_log_file_is_read_only_as_far_as_it_is_shown(tmp_path, monkeypatch):
    f = tmp_path / "server.log"
    f.write_bytes(b"line of log text\n" * 300_000)          # 5 MB
    got = _count_reads(monkeypatch, f)
    out = docread.extract(f, max_chars=1000)
    assert out.startswith("line of log text") and "cut at 1000 characters" in out
    assert got and sum(got) <= 4004


def test_of11_a_huge_html_file_is_read_only_in_part(tmp_path, monkeypatch):
    monkeypatch.setattr(docread, "HTML_MAX", 100_000)
    f = tmp_path / "dump.html"
    f.write_bytes(b"<p>row</p>\n" * 50_000)                 # 550 KB
    got = _count_reads(monkeypatch, f)
    out = docread.extract(f)
    assert out.startswith("row") and "only the first" in out
    assert got and sum(got) <= 100_001


# ---------- OF-12: convert_document shared the user's LibreOffice profile and left soffice running on a timeout -----

def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses /proc and a shell script as a fake soffice")
def test_of12_convert_uses_its_own_profile_and_a_timeout_ends_the_whole_tree(tmp_path, monkeypatch):
    import time
    fake = tmp_path / "bin" / "soffice"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "$@" > "$OF_ARGS"\nsleep 30 &\necho $! > "$OF_CHILD"\nsleep 6\n')
    fake.chmod(0o755)
    monkeypatch.setattr(O, "_soffice", lambda: str(fake))
    monkeypatch.setattr(O, "CONVERT_TIMEOUT", 1, raising=False)
    monkeypatch.setenv("OF_ARGS", str(tmp_path / "args.txt"))
    monkeypatch.setenv("OF_CHILD", str(tmp_path / "child.txt"))
    Document().save(str(tmp_path / "a.docx"))
    t0 = time.time()
    msg, err = O.convert_document("a.docx", "pdf")
    assert err and "took longer than 1 seconds" in msg and time.time() - t0 < 5
    assert "-env:UserInstallation=file://" in (tmp_path / "args.txt").read_text()
    child = int((tmp_path / "child.txt").read_text())
    end = time.time() + 3
    while _alive(child) and time.time() < end:
        time.sleep(0.05)
    assert not _alive(child), "the process soffice started was left running"


# ---------- OF-13: a workbook with a chart sheet could not be read at all; hidden sheets read as visible ------------

def test_of13_a_chart_sheet_does_not_fail_the_whole_workbook(tmp_path):
    import openpyxl
    from openpyxl.chart import BarChart, Reference
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(1, 5):
        ws.append([i, i * 2])
    ch = BarChart()
    ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=4))
    wb.create_chartsheet("Chart1").add_chart(ch)
    wb.save(str(tmp_path / "cs.xlsx"))
    out = _read(tmp_path / "cs.xlsx")
    assert not out.startswith("Could not read"), out
    assert "| 4 | 8 |" in out and "## Chart sheet: Chart1" in out


def test_of13_hidden_sheets_are_marked_and_a_wrong_sheet_name_lists_the_sheets(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = "shown"
    h = wb.create_sheet("Workings")
    h["A1"] = "old numbers"
    h.sheet_state = "hidden"
    wb.save(str(tmp_path / "h.xlsx"))
    out = _read(tmp_path / "h.xlsx")
    assert "Sheets: Sheet, Workings (hidden)" in out
    assert "## Sheet: Workings (1 rows x 1 columns) [hidden sheet: not shown in Excel]" in out
    out = _read(tmp_path / "h.xlsx", sheet="Nope")
    assert out == "h.xlsx has no sheet named 'Nope'. Sheets: Sheet, Workings (hidden)"
    assert "old numbers" in _read(tmp_path / "h.xlsx", sheet="workings")


# ---------- OF-15: the hidden-text check read a UTF-8 reference file in the locale's code page (Windows) -----------

def test_of15_hidden_text_check_reads_the_text_file_as_utf8(tmp_path, monkeypatch):
    import pathlib
    import time
    from meshpool_harness.agent import core
    text = "Zoë Müller and José Álvarez wrote the reference study on café pricing in 2021."
    (tmp_path / "reference.txt").write_text(text, encoding="utf-8")
    since = time.time() - 5
    d = Document()
    d.add_paragraph("Project report.")
    d.add_paragraph(text)
    d.save(str(tmp_path / "project_report.docx"))
    real = pathlib.Path.read_text

    def windows_locale(self, encoding=None, errors=None):    # what open() does on Windows without an encoding
        return real(self, encoding=encoding or "cp1252", errors=errors)
    monkeypatch.setattr(pathlib.Path, "read_text", windows_locale)
    asked = "Retrieve reference text from reference.txt and add it to the end of project_report.docx"
    assert core._docx_hidden_text_miss(asked, tmp_path, since) == ""


# ---------- OF-17: grading each best-of candidate left its PDF and page images in a temp folder for good ------------

def test_of17_best_of_grading_leaves_no_temp_folder(tmp_path, monkeypatch):
    import json
    import tempfile
    app = str(Path(__file__).resolve().parents[1])
    if app not in sys.path:
        sys.path.insert(0, app)
    from engines.forge import common, select
    from reportlab.pdfgen import canvas
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))

    def fake_convert(src, to, out_dir, timeout=240):
        pdf = Path(out_dir) / (Path(src).stem + ".pdf")
        c = canvas.Canvas(str(pdf), pagesize=(720, 405))
        c.drawString(72, 200, "Slide one")
        c.showPage()
        c.save()
        return pdf
    monkeypatch.setattr(common, "soffice_convert", fake_convert)
    (tmp_path / "deck.pptx").write_bytes(b"x")

    class Client:
        def chat(self, **kw):
            assert kw["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
            scores = {k: 7 for k in select.KEYS}
            return {"choices": [{"message": {"content": json.dumps(dict(scores, worst="none"))}}]}
    got = select.judge(str(tmp_path / "deck.pptx"), Client())
    assert got["mean"] == 7.0
    assert list(tmp.iterdir()) == []


# ---------- OF-18: hidden slides read as shown ---------------------------------------------------------------------

def test_of18_hidden_slides_are_marked(tmp_path):
    from pptx import Presentation
    prs = Presentation()
    for t in ("Shown", "Backup"):
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = t
    prs.slides[1]._element.set("show", "0")
    prs.save(str(tmp_path / "d.pptx"))
    out = _read(tmp_path / "d.pptx")
    assert "## Slide 1: Shown\n" in out
    assert "## Slide 2: Backup [hidden slide: not shown in the slide show]" in out


# ---------- OF-19: two requests for the same preview page shared one part file, and one of them failed --------------

def test_of19_two_requests_for_one_page_both_get_it(tmp_path, monkeypatch):
    import pathlib
    import threading
    from reportlab.pdfgen import canvas
    from meshpool_harness.ui import previews as PV
    from meshpool_harness.ui import workbench as W
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    c = canvas.Canvas(str(root / "a.pdf"))
    c.drawString(72, 700, "page")
    c.save()
    rendered, written = threading.Barrier(2, timeout=5), threading.Barrier(2, timeout=5)

    def render(pdf, page, width):
        rendered.wait()                                   # both requests found no cached page
        return b"\x89PNG fake"
    monkeypatch.setattr(PV, "_render", render)
    real = pathlib.Path.write_bytes

    def write_bytes(self, data):
        n = real(self, data)
        if self.suffix == ".tmp":
            written.wait()                                # both wrote their part before either moves it
        return n
    monkeypatch.setattr(pathlib.Path, "write_bytes", write_bytes)
    got, errors = [], []

    def ask():
        try:
            got.append(PV.page_png(str(root), "a.pdf", 0, 400))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=ask) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not errors, errors
    assert got == [b"\x89PNG fake"] * 2
