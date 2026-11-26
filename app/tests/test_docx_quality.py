"""Word and PDF file quality (Priya). Her client update showed its title twice at the top (a Title paragraph, then a
Heading 1 with the same words) and came out on US Letter pages in the UK. The title is written once; the page size
is A4 unless the user's region is the US or Canada or they ask for Letter. No model calls."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import office_mcp_server as O  # noqa: E402

MD = ("# Library Extension: Client Update\n\n**To:** Harbourside Council\n\n## Summary\n\n"
      "The design is on schedule.\n")


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "_project", lambda: tmp_path)
    monkeypatch.setenv("MH_CLARITY", "0")
    for k in ("LC_ALL", "LC_PAPER", "LANG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("locale.getlocale", lambda *a: (None, None))
    return tmp_path


def test_the_title_is_written_once(proj):
    msg, err = O.make_document(MD, "update.docx", title="Library Extension: Client Update")
    assert not err, msg
    texts = [(p.style.name, p.text) for p in Document(str(proj / "update.docx")).paragraphs if p.text.strip()]
    assert [t for _, t in texts].count("Library Extension: Client Update") == 1, texts
    assert texts[0] == ("Title", "Library Extension: Client Update")
    assert ("Heading 2", "Summary") in texts


def test_a_different_first_heading_is_kept(proj):
    O.make_document("# Summary\n\nText.", "b.docx", title="Client update")
    texts = [p.text for p in Document(str(proj / "b.docx")).paragraphs if p.text.strip()]
    assert texts[:2] == ["Client update", "Summary"]


@pytest.mark.parametrize("lang,paper,want", [("en_GB.UTF-8", None, "A4"), ("de_DE.UTF-8", None, "A4"),
                                             ("en_US.UTF-8", None, "Letter"), ("fr_CA.UTF-8", None, "Letter"),
                                             ("en_GB.UTF-8", "Letter", "Letter"), ("en_US.UTF-8", "A4", "A4"),
                                             ("", None, "A4")])
def test_page_size_follows_the_region_unless_asked(proj, monkeypatch, lang, paper, want):
    if lang:
        monkeypatch.setenv("LANG", lang)
    msg, err = O.make_document("Hello.", "p.docx", paper=paper)
    assert not err, msg
    sec = Document(str(proj / "p.docx")).sections[0]
    mm = (round(sec.page_width.mm), round(sec.page_height.mm))
    assert mm == ((210, 297) if want == "A4" else (216, 279)), (lang, paper, mm)


def test_a_pdf_also_has_one_title_and_the_regions_paper(proj, monkeypatch):
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    msg, err = O.make_pdf(MD, "update.pdf", title="Library Extension: Client Update")
    assert not err, msg
    from pypdf import PdfReader
    r = PdfReader(str(proj / "update.pdf"))
    box = r.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (595, 842)          # A4 in points
    assert r.pages[0].extract_text().count("Library Extension: Client Update") == 1
