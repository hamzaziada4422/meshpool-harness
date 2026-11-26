"""Daniel, 0.0.19, continued: speaker notes (item 2), the visible flaws the QA loop now catches (item 3), the check
details (item 4) and the Word memo and document properties (item 5). Scripted plans, no model; the files are read
with python-pptx and python-docx."""
from __future__ import annotations

import copy
import json
import re
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches, Pt

from test_daniel019_decks import BRIEF, FACTS, FOOTER, PLAN, Scripted, daniel_deck, offline  # noqa: F401

ARTEFACT = re.compile(r"\[QA\]|\bprov\b|\b(?:[FCWO]\d{1,3}|S\d+[CO]\d+)\b|[{}]|moved off the slide|"
                      r"Detail for the presenter|storyline|corrected here|fact list", re.I)


def _rules(res):
    return {k: v for k, v in res["rules"].items() if v == "fail"}


# ---------------------------------------------------------------------------------------------------- item 2
def _noisy_plan():
    cover = copy.deepcopy(PLAN[0])
    cover["notes"] = ("Say aloud: this is the decision (C1). [QA] title shortened\nThe storyline title said tripled; it "
                      "is corrected here. Detail for the presenter (moved off the slide):\n- {\"bullets\": [{\"text\": "
                      "\"71% succeed\", \"prov\": [\"F9\"]}]}")
    grid = copy.deepcopy(next(s for s in PLAN if s["archetype"] == "grid"))
    grid["notes"] = ("Adoption is 18% in 2021 and 47% in 2025 (F6). That is 2.6 times the 2021 level (C1), so 'nearly "
                     "tripled' is the accurate wording. The storyline title said tripled; it is corrected here. Pilot "
                     "pays back in 19 months, prov F9.")
    grid["parts"] = grid["parts"] + [{"type": "text", "heading": "Sensor prices fell", "bullets": [
        {"text": "Sensor prices are down 35% since 2021", "prov": ["F7"]}], "prov": ["F7"]},
        {"type": "text", "heading": "Cloud analytics", "bullets": [
            {"text": "Cloud analytics come bundled with service contracts", "prov": ["F7"]}]}]
    grid["footnotes"] = grid["footnotes"] + ["The sources do not state a packaging figure.",
                                             "Country margins are wider.", "Medians across adopters.",
                                             "Plants of 250 to 2,000 employees."]
    return [cover, grid]


def test_speaker_notes_hold_presenter_prose_only(tmp_path, offline):
    """The notes held "[QA] Note moved off the slide", "Detail for the presenter (moved off the slide):", a JSON
    bullets blob with "prov F9", fact ids (C1, F9) and "The storyline title said tripled; it is corrected here"."""
    res = offline.build(BRIEF + FACTS, str(tmp_path), slides_wanted=2, client=Scripted(_noisy_plan()), task_id="t",
                        density="present")
    notes = [s.notes_slide.notes_text_frame.text for s in Presentation(res["pptx"]).slides]
    assert all(notes), notes
    for t in notes:
        assert not ARTEFACT.search(t), t
    assert "Adoption is 18% in 2021 and 47% in 2025." in notes[1]
    # text moved off the slide is plain presenter notes, with no label (the fifth and sixth parts do not fit)
    assert "Sensor prices are down 35% since 2021" in notes[1] or "Cloud analytics" in notes[1], notes[1]
    # what the renderer and the checks have to say goes to the build's record, not the notes
    qa = json.loads((Path(res["pptx"]).parent / "qa.json").read_text())
    assert "rules" in qa


def test_every_note_of_daniels_deck_is_clean(daniel_deck):
    notes = [s.notes_slide.notes_text_frame.text for s in Presentation(daniel_deck["pptx"]).slides]
    assert len(notes) == 6 and all(notes)
    for t in notes:
        assert not ARTEFACT.search(t), t


def test_the_notes_cleaner_on_daniels_own_lines():
    from engines.forge.notes import clean
    raw = next(s for s in PLAN if s["id"] == "S2")["notes"]
    out = clean(raw)
    assert "(C1)" not in out and "corrected here" not in out and "fact list" not in out and "Say aloud" not in out
    assert out.startswith("Adoption is 18% in 2021 and 47% in 2025")
    assert clean("[QA] Note moved off the slide: x\nDetail for the presenter (moved off the slide):\n- Plain line") == \
        "Plain line."


# ---------------------------------------------------------------------------------------------------- item 3
def test_daniels_deck_has_none_of_the_visible_flaws(daniel_deck):
    prs = Presentation(daniel_deck["pptx"])
    rules = daniel_deck["qa"]["rules"]
    for r in ("P23", "P24", "P25", "P26", "P27"):
        assert rules[r] == "pass", (r, rules, [f for f in daniel_deck["qa"]["failures"] if f["rule"] == r])
    texts = [[sh for sh in sl.shapes if sh.has_text_frame] for sl in prs.slides]
    # no "Preliminary" stamp: the brief did not ask for one
    assert not any(sh.name.startswith("ov_Tag") for sl in texts for sh in sl)
    # the footnote keeps its first words ("19 months is the survey median ...", not "months is ...")
    notes = [sh.text_frame.text for sl in texts for sh in sl if sh.name == "Footnotes"]
    assert any(t.startswith("19 months is the survey median") for t in notes), notes
    # no source line cut to "... and others"
    for sl in texts:
        for sh in sl:
            if sh.name == "Source":
                assert not sh.text_frame.text.rstrip().endswith("and others") and \
                    sh.text_frame.text.count("(") == sh.text_frame.text.count(")"), sh.text_frame.text
    # the next steps have no empty "What it involves" column
    last = " | ".join(sh.text_frame.text for sh in texts[-1] if sh.name.startswith("role_subtitle_Head"))
    assert "Next step" in last and "What it involves" not in last, last


def _one_chart_deck(tmp_path, chart, title="Germany leads adoption at 58% against a 47% average", **extra):
    from engines.forge.render import render
    s = {"id": "S1", "node": "1", "archetype": "chart_takeaway", "title": title,
         "chart": chart, "takeaway": {"text": "Germany leads"}, "source": {"text": "Northfield Research 2025"},
         **extra}
    out = tmp_path / "one.pptx"
    render([s], str(out), fix_labels=False)
    return str(out)


COUNTRIES = {"kind": "column", "categories": ["Germany", "Netherlands", "Italy", "Poland"],
             "series": [{"name": "Adoption, %", "values": [58, 51, 42, 31]}], "unit": "%", "target": 47,
             "highlight_categories": ["Germany"]}


def test_a_target_label_is_never_drawn_on_a_bar_and_a_label_on_a_bar_fails_the_checks(tmp_path):
    from engines.forge.validate_pptx import validate
    from engines.forge.visual_check import _bars
    p = _one_chart_deck(tmp_path, COUNTRIES)
    sl = Presentation(p).slides[0]
    gf = next(sh for sh in sl.shapes if getattr(sh, "has_chart", False) and sh.has_chart)
    lab = next(sh for sh in sl.shapes if sh.name == "ov_TargetLabel")
    r = (lab.left / 914400, lab.top / 914400, (lab.left + lab.width) / 914400, (lab.top + lab.height) / 914400)
    for b in _bars(gf):
        assert min(r[2], b[2]) - max(r[0], b[0]) <= 0.04 or min(r[3], b[3]) - max(r[1], b[1]) <= 0.04, (r, b)
    assert validate(p, render=False)["rules"]["P23"] == "pass"
    # the first pass's "Target 47%" on the Germany bar: the checks fail it
    prs = Presentation(p)
    sl = prs.slides[0]
    b = _bars(next(sh for sh in sl.shapes if getattr(sh, "has_chart", False) and sh.has_chart))[0]
    tb = sl.shapes.add_textbox(Inches(b[0] - 0.1), Inches(b[1] + 0.3), Inches(0.9), Inches(0.26))
    tb.text_frame.text = "Target 47%"
    prs.save(p)
    res = validate(p, render=False)
    assert res["rules"]["P23"] == "fail" and "Target 47%" in next(f["message"] for f in res["failures"]
                                                                   if f["rule"] == "P23")


def test_one_series_keeps_one_colour_and_the_accent_marks_only_the_highlight(tmp_path):
    chart = {"kind": "column", "categories": ["Y0", "Y1", "Y2", "Y3"], "series": [{"name": "EUR m",
             "values": [-1.2, 0.4, 1.9, 3.1]}], "unit": "", "decimals": 1}
    p = _one_chart_deck(tmp_path, chart, title="Five-plant rollout turns positive in year 1, EUR 3.1m by year 3")
    xml = next(sh for sh in Presentation(p).slides[0].shapes if getattr(sh, "has_chart", False)
               and sh.has_chart).chart.part.blob.decode()
    assert "<c:dPt>" not in xml                               # no bar of its own colour: Y0 was pale amber, Y2 grey
    p = _one_chart_deck(tmp_path, dict(chart, highlight_categories=["Y3"]), title="Year 3 reaches EUR 3.1m net")
    xml = next(sh for sh in Presentation(p).slides[0].shapes if getattr(sh, "has_chart", False)
               and sh.has_chart).chart.part.blob.decode()
    pts = re.findall(r"<c:dPt>.*?</c:dPt>", xml, re.S)
    assert len(pts) == 1 and 'val="accent1"' in pts[0] and "<c:idx val=\"3\"/>" in pts[0]


def test_empty_columns_cut_footnotes_orphans_and_stray_stamps_fail_the_checks(tmp_path):
    from engines.forge.visual_check import check
    p = _one_chart_deck(tmp_path, COUNTRIES, footnotes=["19 months is the survey median."], tags=["Preliminary"])
    assert {f["rule"] for f in check(p, {"stamps": []}) if f["status"] == "fail"} == {"P27"}
    assert not [f for f in check(p, {"stamps": ["preliminary"]}) if f["status"] == "fail"]
    prs = Presentation(p)
    sl = prs.slides[0]
    head = sl.shapes.add_textbox(Inches(0.6), Inches(6.2), Inches(2), Inches(0.3))
    head.name = "role_subtitle_Head16"
    head.text_frame.text = "What it involves"
    note = next(sh for sh in sl.shapes if sh.name == "Footnotes")
    note.text_frame.paragraphs[0].runs[0].text = "months is the survey median for pilot-first adopters."
    t = sl.shapes.title
    t.width = Inches(5.2)
    t.text_frame.text = "Start now with one pilot line: it pays back in 19 months, not 31"
    for r in t.text_frame.paragraphs[0].runs:
        r.font.size = Pt(25)
    prs.save(p)
    got = {f["rule"]: f["message"] for f in check(p, {"stamps": ["preliminary"]}) if f["status"] == "fail"}
    assert set(got) == {"P24", "P25", "P26"}, got
    assert "What it involves" in got["P24"] and "months is the survey" in got["P25"] and "31" in got["P26"]


def test_a_long_source_line_wraps_or_keeps_whole_clauses_never_and_others():
    from engines.forge.render import source_blocks
    t = ("Source: Northfield Research, Predictive Maintenance in European Mid-Market Manufacturing, Industry Benchmark "
         "Report 2025 (412 plant managers; economics modelled from 37 case studies)")
    b = source_blocks(t, 7.0)
    assert b[0]["text"] == t                                       # two lines at most: the whole line
    long_ = "; ".join([t] + [f"Company filing {i} (annual report, notes {i} and {i + 1})" for i in range(12)])
    b = source_blocks(long_, 7.0)
    assert not b[0]["text"].endswith("and others") and b[0]["text"].count("(") == b[0]["text"].count(")")
    assert long_.startswith(b[0]["text"])


def test_slides_added_to_a_deck_carry_its_look_and_no_section_number(tmp_path, daniel_deck, monkeypatch):
    """The added chart slide showed "01" at top right and bars in navy, grey and pale amber."""
    import shutil
    from engines.forge import build as B
    from engines.forge import common as CM
    from engines.forge.engine import add_to_deck
    monkeypatch.setattr(B, "_review_on", lambda client, rules: False)
    monkeypatch.setenv("MH_PERSONAS", "0")
    monkeypatch.setattr(CM, "find_soffice", lambda *a, **k: None)
    deck = tmp_path / "Ridgeway_PdM_Client_Deck.pptx"
    shutil.copy(daniel_deck["pptx"], deck)
    added = {"node": "6", "archetype": "chart_takeaway", "section": "Why a pilot wins",
             "title": "A five-plant rollout turns positive in year 1 and reaches EUR 3.1m by year 3",
             "chart": {"kind": "column", "categories": ["Y0", "Y1", "Y2", "Y3"],
                       "series": [{"name": "Cumulative net benefit, EUR m", "values": [-1.2, 0.4, 1.9, 3.1]}],
                       "decimals": 1},
             "takeaway": {"text": "Payoff turns positive within one year"},
             "source": {"text": "Northfield Research, Industry Benchmark Report 2025"},
             "notes": "These figures are modelled from 37 case studies (F8). They are not a forecast for one line."}

    class One(Scripted):
        def chat(self, *, model, messages, **kw):
            if "ONE consulting slide" in messages[0]["content"]:
                return {"choices": [{"message": {"content": json.dumps(added)}}]}
            if "engagement manager" in messages[0]["content"]:
                return {"choices": [{"message": {"content": json.dumps({"slides": [
                    {k: added[k] for k in ("node", "archetype", "title", "section")}]})}}]}
            return super().chat(model=model, messages=messages, **kw)
    res = add_to_deck("Add one chart slide after slide 5 (1 slide)", str(deck), workdir=str(tmp_path),
                      task_id="add", client=One([]))
    out = Presentation(res["artifacts"][0])
    assert len(out.slides) == 7
    new = out.slides[5]
    names = [sh.name for sh in new.shapes]
    assert "ov_SectionNo" not in names, names
    assert FOOTER in " ".join(sh.text_frame.text for sh in new.shapes if sh.has_text_frame)
    xml = next(sh for sh in new.shapes if getattr(sh, "has_chart", False) and sh.has_chart).chart.part.blob.decode()
    assert "<c:dPt>" not in xml
    assert not ARTEFACT.search(new.notes_slide.notes_text_frame.text)


# ---------------------------------------------------------------------------------------------------- item 4
def test_the_check_details_name_what_failed_in_plain_words(tmp_path, monkeypatch):
    """The check line said "all 19 passed" with collisions left, and its details named no check."""
    from meshpool_harness import checks
    from engines.forge.validate_pptx import validate
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    p = _one_chart_deck(tmp_path, COUNTRIES)
    prs = Presentation(p)
    sl = prs.slides[0]
    from engines.forge.visual_check import _bars
    b = _bars(next(sh for sh in sl.shapes if getattr(sh, "has_chart", False) and sh.has_chart))[0]
    tb = sl.shapes.add_textbox(Inches(b[0]), Inches(b[1] + 0.3), Inches(0.9), Inches(0.26))
    tb.text_frame.text = "Target 47%"
    prs.save(p)
    checks.begin("run-d019")
    try:
        validate(p, render=False)
    finally:
        sm = checks.summary("run-d019")
        checks.end("run-d019")
    assert sm["failed"] >= 1 and "all" not in sm["line"].split(":", 1)[1], sm
    assert any(d.startswith("Chart labels clear of the bars: Slide 1") and "Target 47%" in d for d in sm["details"]), \
        sm["details"]
    assert not any(re.match(r"P\d+:", d) for d in sm["details"]), sm["details"]


# ---------------------------------------------------------------------------------------------------- item 5
def test_the_word_memo_uses_real_heading_list_and_table_styles_and_clean_properties(tmp_path, monkeypatch):
    from docx import Document
    from meshpool_harness.agent import office_mcp_server as OS
    from meshpool_harness.ui import settings_v2 as SV
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_CLARITY", "0")
    monkeypatch.setattr(OS, "_project", lambda: tmp_path)
    SV.save_profile(name="Daniel Reyes")
    md = ("# Predictive maintenance: what the Northfield report means for you\n\n**Recommendation**\n\nApprove one "
          "pilot line.\n\n## Why now\n\n• 47% of plants use it\n• Downtime falls 23%\n\n- Pilot first\n- Then scale\n\n"
          "| Approach | Payback (months) |\n|---|---|\n| Pilot line | 19 |\n| Plant-wide | 31 |\n")
    msg, bad = OS.make_document(md, filename="Ridgeway_PdM_Memo.docx", footer=FOOTER, heading_font="Georgia",
                                color="#14304F")
    assert not bad, msg
    path = tmp_path / "Ridgeway_PdM_Memo.docx"
    d = Document(str(path))
    styles = [(p.style.name, p.text) for p in d.paragraphs if p.text.strip()]
    assert ("Heading 1", "Predictive maintenance: what the Northfield report means for you") in styles
    assert ("Heading 2", "Recommendation") in styles and ("Heading 2", "Why now") in styles
    bullets = [t for s, t in styles if s == "List Bullet"]
    assert bullets == ["47% of plants use it", "Downtime falls 23%", "Pilot first", "Then scale"], styles
    assert not any(t.lstrip().startswith(("•", "-")) for _, t in styles)
    assert d.styles["Heading 1"].font.name == "Georgia" and str(d.styles["Heading 1"].font.color.rgb) == "14304F"
    mar = d.tables[0]._tbl.tblPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblCellMar")
    assert mar is not None and len(mar) == 4
    assert d.sections[0].footer.paragraphs[0].text == FOOTER
    cp = d.core_properties
    assert cp.author == "Daniel Reyes" and cp.title.startswith("Predictive maintenance")
    with zipfile.ZipFile(path) as z:
        props = z.read("docProps/core.xml").decode()
    assert not re.search(r"python-docx|python-pptx|openpyxl", props, re.I), props


def test_decks_and_workbooks_the_engines_make_carry_no_library_name(tmp_path, monkeypatch, daniel_deck):
    from engines.sheets.builder import new_workbook
    with zipfile.ZipFile(daniel_deck["pptx"]) as z:
        props = z.read("docProps/core.xml").decode()
    assert not re.search(r"python-pptx|Steve Canny|generated using", props, re.I), props
    assert "Predictive maintenance" in props
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    wb = new_workbook()
    wb.save(tmp_path / "m.xlsx")
    with zipfile.ZipFile(tmp_path / "m.xlsx") as z:
        props = z.read("docProps/core.xml").decode()
    assert "openpyxl" not in props, props
