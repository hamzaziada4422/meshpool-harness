"""Teach by editing: consistent edits to files Meshpool made become a card; one-off edits are noise; files Meshpool
did not make are never read."""
import os
import sys
import time
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from meshpool_harness import artifacts_registry as REG  # noqa: E402
from meshpool_harness import assistant as A  # noqa: E402
from meshpool_harness import memstore as MS  # noqa: E402
from meshpool_harness import teach as T  # noqa: E402

TITLES = ["Quarterly Revenue Grew Twelve Percent", "Gross Margin Improved Across Every Region",
          "Customer Retention Reached A Record High", "Operating Costs Fell After The Restructure",
          "Cash Conversion Is Back Above Target", "Headcount Plans For The Next Year",
          "Risks We Are Watching Closely", "Decisions We Need From The Board", "Next Steps And Owners This Quarter"]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_INSIGHTS", raising=False)
    MS._ENSURED.clear()
    MS._CACHE.clear()
    A.save_settings(memory_on=True)
    yield tmp_path


def _deck(path, titles=TITLES, font="Calibri"):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    for t in titles:
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = t
        for r in s.shapes.title.text_frame.paragraphs[0].runs:
            r.font.name = font
        eb = s.shapes.add_textbox(Inches(1), Inches(0.2), Inches(3), Inches(0.3))
        eb.name = "eyebrow 1"
        eb.text_frame.text = "SECTION"
        eb.text_frame.paragraphs[0].runs[0].font.name = font
    prs.save(path)


def _later(path, secs=30):
    t = time.time() + secs
    os.utime(path, (t, t))


def _edit_deck(path, fn):
    from pptx import Presentation
    prs = Presentation(str(path))
    for i, s in enumerate(prs.slides):
        fn(i, s)
    prs.save(str(path))
    _later(path)


def _sentence(t):
    w = t.split()
    return " ".join([w[0]] + [x.lower() for x in w[1:]])


def test_a_consistent_deck_edit_makes_a_teach_card(home):
    p = home / "deck.pptx"
    _deck(p)
    assert REG.register(p, "chat-1")["kind"] == "pptx"

    def fix(i, s):
        s.shapes.title.text = _sentence(TITLES[i])
        for r in s.shapes.title.text_frame.paragraphs[0].runs:
            r.font.name = "Arial"
        for sh in list(s.shapes):
            if sh.name.startswith("eyebrow"):
                sh._element.getparent().remove(sh._element)
    _edit_deck(p, fix)
    cards = T.detect_edits()
    assert len(cards) == 1
    c = cards[0]
    assert c["type"] == "teach" and c["where"] == "home" and c["action"]["kind"] == "teach_accept"
    kinds = {r["kind"]: r for r in c["action"]["payload"]["rules"]}
    assert kinds["case"]["value"] == "sentence" and kinds["case"]["count"] == 9 == kinds["case"]["of"]
    assert kinds["font"]["value"] == "Arial" and kinds["removed"]["value"] == "eyebrow"
    assert "all 9 slide titles to sentence case" in c["title"] and "the font to Arial" in c["title"]
    assert c["title"].endswith("Make these your defaults?")
    assert c["evidence"][0]["file"] == str(p.resolve()) and c["evidence"][0]["chat_id"] == "chat-1"
    assert c["dedupe_key"].startswith("teach:") and c["expires"] > c["created"]
    assert T.detect_edits() and T.detect_edits()[0]["id"] == c["id"]          # stable until answered


def test_accepting_writes_learned_facts_and_moves_the_baseline(home):
    p = home / "deck.pptx"
    _deck(p)
    REG.register(p, "chat-2")
    _edit_deck(p, lambda i, s: setattr(s.shapes.title, "text", _sentence(TITLES[i])))
    c = T.detect_edits()[0]
    out = T.accept(c["action"]["payload"]["rules"], str(p.resolve()), "chat-2")
    assert len(out["facts"]) == 1
    f = MS.get(out["facts"][0])
    assert f["kind"] == "learned" and f["status"] == "active" and f["text"] == "Prefers slide titles in sentence case"
    assert f["source"]["type"] == "edit" and f["source"]["file"] == str(p.resolve())
    assert T.detect_edits() == []                                           # the same edit is not offered again
    assert "sentence case" in A.context_block("a deck")


def test_accepting_updates_the_active_company_font(home):
    from engines.forge import brand as B
    prof = B.save({"name": "Acme", "fonts": {"major": "Georgia", "minor": "Calibri"}, "template_mode": "forge"},
                  activate=True)
    p = home / "deck.pptx"
    _deck(p)
    REG.register(p, "c")

    def fix(i, s):
        for r in s.shapes.title.text_frame.paragraphs[0].runs:
            r.font.name = "Arial"
        for sh in s.shapes:
            if sh.name.startswith("eyebrow"):
                sh.text_frame.paragraphs[0].runs[0].font.name = "Arial"
    _edit_deck(p, fix)
    c = T.detect_edits()[0]
    out = T.accept(c["action"]["payload"]["rules"], str(p.resolve()), "c")
    assert out["company"] is True and B.get(prof["id"])["fonts"] == {"major": "Arial", "minor": "Arial"}


def test_a_one_off_edit_is_noise(home):
    p = home / "deck.pptx"
    _deck(p)
    REG.register(p, "chat-3")

    def one(i, s):
        if i == 0:
            s.shapes.title.text = "Quarterly revenue grew twelve percent"
            for r in s.shapes.title.text_frame.paragraphs[0].runs:
                r.font.name = "Arial"
    _edit_deck(p, one)
    assert T.detect_edits() == []
    def two(i, s):                                            # two of nine slides is still not a habit
        if i < 2:
            s.shapes.title.text = _sentence(TITLES[i])
    _edit_deck(p, two)
    assert T.detect_edits() == []


def test_deleting_one_slide_or_adding_one_is_noise(home):
    p = home / "deck.pptx"
    _deck(p)
    REG.register(p, "c")
    from pptx import Presentation
    prs = Presentation(str(p))
    sid = prs.slides._sldIdLst[0]
    prs.part.drop_rel(sid.rId)
    prs.slides._sldIdLst.remove(sid)
    prs.save(str(p))
    _later(p)
    assert T.detect_edits() == []


def test_a_file_meshpool_did_not_make_is_never_read(home, monkeypatch):
    mine, other = home / "mine.pptx", home / "other.pptx"
    _deck(mine)
    _deck(other)
    REG.register(mine, "c")
    _edit_deck(other, lambda i, s: setattr(s.shapes.title, "text", _sentence(TITLES[i])))
    read = []
    real_sum, real_snap = REG.checksum, REG.snapshot
    monkeypatch.setattr(REG, "checksum", lambda p: read.append(str(p)) or real_sum(p))
    monkeypatch.setattr(REG, "snapshot", lambda p: read.append(str(p)) or real_snap(p))
    assert T.detect_edits() == []
    assert str(other) not in read and str(other.resolve()) not in read
    assert all(r.endswith("mine.pptx") for r in read)
    assert REG.register(other.with_suffix(".txt"), "c") is None               # a file type it does not track


def test_a_file_changed_just_after_meshpool_wrote_it_is_not_a_user_edit(home):
    p = home / "deck.pptx"
    _deck(p)
    REG.register(p, "c")
    from pptx import Presentation
    prs = Presentation(str(p))
    for i, s in enumerate(prs.slides):
        s.shapes.title.text = _sentence(TITLES[i])
    prs.save(str(p))                                                      # no utime bump: inside the grace period
    assert T.detect_edits() == []
    assert len(T.detect_edits(grace=-1000)) == 1


def test_switches_off_means_nothing(home, monkeypatch):
    p = home / "deck.pptx"
    _deck(p)
    REG.register(p, "c")
    _edit_deck(p, lambda i, s: setattr(s.shapes.title, "text", _sentence(TITLES[i])))
    assert len(T.detect_edits()) == 1
    assert T.detect_edits(incognito=True) == []
    T.set_learn_from_edits(False)
    assert T.detect_edits() == [] and T.accept([{"fact": "x y z w"}], str(p))["facts"] == []
    T.set_learn_from_edits(True)
    A.save_settings(memory_on=False)
    assert T.detect_edits() == []
    A.save_settings(memory_on=True)
    monkeypatch.setenv("MH_INSIGHTS", "0")
    assert T.detect_edits() == []


def test_incognito_chat_files_are_not_registered(home):
    p = home / "deck.pptx"
    _deck(p)
    assert REG.register(p, "c", incognito=True) is None and not REG.is_registered(p)


# ---------- workbooks and documents ---------------------------------------------------------------------------------------
def _book(path, fmt="General", font="Calibri", sheets=("Data",)):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = sheets[0]
    for r in range(1, 21):
        c = ws.cell(row=r, column=1, value=r * 1000.5)
        c.number_format = fmt
        c.font = Font(name=font)
    for n in sheets[1:]:
        wb.create_sheet(n)
    wb.save(path)


def _edit_book(path, fn):
    from openpyxl import load_workbook
    wb = load_workbook(str(path))
    fn(wb)
    wb.save(str(path))
    _later(path)


def test_workbook_number_format_and_font_changes(home):
    p = home / "m.xlsx"
    _book(p)
    REG.register(p, "c")

    def fix(wb):
        from openpyxl.styles import Font
        for row in wb["Data"].iter_rows():
            for c in row:
                c.number_format = "#,##0.0"
                c.font = Font(name="Arial")
        wb["Data"].column_dimensions["A"].width = 30
    _edit_book(p, fix)
    rules = {r["kind"]: r for r in T.detect_edits()[0]["action"]["payload"]["rules"]}
    assert rules["format"]["value"] == "#,##0.0" and rules["format"]["count"] == 20
    assert rules["font"]["value"] == "Arial"


def test_one_workbook_cell_edit_is_noise(home):
    p = home / "m.xlsx"
    _book(p)
    REG.register(p, "c")

    def one(wb):
        wb["Data"]["A1"].number_format = "0.00"
        wb["Data"]["A2"].number_format = "0.00"
    _edit_book(p, one)
    assert T.detect_edits() == []


def test_an_added_sheet_is_a_habit_only_when_it_repeats_across_workbooks(home):
    a, b = home / "a.xlsx", home / "b.xlsx"
    _book(a)
    _book(b)
    REG.register(a, "c")
    REG.register(b, "c")
    _edit_book(a, lambda wb: wb.create_sheet("Notes"))
    assert T.detect_edits(only=[str(a.resolve())]) == []                    # once is a one-off
    _edit_book(b, lambda wb: wb.create_sheet("Notes"))
    cards = T.detect_edits(only=[str(b.resolve())])
    assert cards and cards[0]["action"]["payload"]["rules"][0]["kind"] == "sheet"


def _doc(path, headings, font="Calibri"):
    from docx import Document
    d = Document()
    for h in headings:
        p = d.add_heading(h, level=1)
        for r in p.runs:
            r.font.name = font
        para = d.add_paragraph("Body text goes here for this section.")
        for r in para.runs:
            r.font.name = font
    d.save(path)


def test_document_heading_case_and_font(home):
    p = home / "r.docx"
    _doc(p, TITLES[:6])
    REG.register(p, "c")
    from docx import Document
    d = Document(str(p))
    hs = [x for x in d.paragraphs if x.style.name.startswith("Heading")]
    for i, h in enumerate(hs):
        h.text = _sentence(TITLES[i])
    for para in d.paragraphs:
        for r in para.runs:
            r.font.name = "Arial"
    d.save(str(p))
    _later(p)
    c = T.detect_edits()[0]
    rules = {r["kind"]: r for r in c["action"]["payload"]["rules"]}
    assert rules["case"]["value"] == "sentence" and rules["case"]["noun"] == "headings"
    assert rules["font"]["value"] == "Arial" and rules["font"]["scope"] == "document"


def test_case_classifier():
    assert REG.case_of("Quarterly Revenue Grew Twelve Percent") == "title"
    assert REG.case_of("Quarterly revenue grew twelve percent") == "sentence"
    assert REG.case_of("QUARTERLY REVENUE GREW") == "upper"
    assert REG.case_of("Revenue grew") == ""
