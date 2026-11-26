"""Daniel (consultant persona), round 2 of the release candidate: the brand and notes fixes held; these flaws were
still visible. Fixtures: his final deck as text (round2_deck.json, rebuilt by the test), his live slide plan (round2_slides.json), the report he
attached (northfield_report.txt) and the scripted first-pass plan (slides.json). No model is called.

1 visual flaws: a chevron squeezed over a panel, a bracket touching a value label, a column head wrapping into the
  section number, empty cells in a filled column, a source line printed twice, a mostly empty cover.
2 source lines that name the working files ("the project fact base") instead of the report.
3 invented material: a "Target 47%" line, owners and dates for the next steps, "Board meeting, November 2026".
4 "update the same deck" made "Deck (updated).pptx" beside the deck.
5 the memo: Word's blue title rule, "Confidential" as a heading, a blank footer.
6 speaker notes of 253 words for one slide."""
from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest
from pptx import Presentation

from test_daniel019_decks import BRIEF, FACTS, FOOTER, PLAN, Scripted

FIX = Path(__file__).parent / "fixtures" / "daniel019"
LIVE = json.loads((FIX / "round2_slides.json").read_text())


def _build_daniel_deck(path: Path) -> Path:
    """Daniel's round 2 deck rebuilt from its text spec (fixtures/daniel019/round2_deck.json, read from the file he
    downloaded): every shape at its place with its text, and each chart's plot geometry, value scale and labels, on
    the forge's own template in his brand. Test fixtures are text, never decks."""
    from lxml import etree
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE, PP_PLACEHOLDER
    from pptx.oxml.ns import qn
    from pptx.util import Inches, Pt
    from engines.forge import requested as RQ
    from engines.forge.render import render
    spec = json.loads((FIX / "round2_deck.json").read_text())
    theme, brand = RQ.look(RQ.parse(BRIEF))
    theme["fonts"] = {"major": "Georgia", "minor": "Georgia"}               # his deck: Georgia throughout
    base = path.with_name("template.pptx")
    render([{"archetype": "cover", "title": "Template"}, {"archetype": "argument_only", "title": "Template page",
            "blocks": [{"text": "x"}]}], str(base), fix_labels=False, theme=theme, brand=brand)
    prs = Presentation(str(base))
    lst = prs.slides._sldIdLst
    for sid in list(lst):
        prs.part.drop_rel(sid.rId)
        lst.remove(sid)
    I = lambda v: Inches(v)                                                 # noqa: E731
    kinds = {"title": (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE),
             "center_title": (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE), "subtitle": (PP_PLACEHOLDER.SUBTITLE,)}

    def text(sh, d):
        tf = sh.text_frame
        bp = tf._txBody.find(qn("a:bodyPr"))
        for k in ("anchor", "wrap"):
            if d.get(k):
                bp.set(k, d[k])
        for v, attr in zip(d.get("margins") or [], ("margin_left", "margin_top", "margin_right", "margin_bottom")):
            if v is not None:
                setattr(tf, attr, I(v))
        for k, runs in enumerate(d["paras"]):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            for r in runs:
                run = p.add_run()
                run.text = r["t"]
                if r["pt"]:
                    run.font.size = Pt(r["pt"])
                run.font.bold = r["b"]
                if r["face"]:
                    etree.SubElement(run._r.get_or_add_rPr(), qn("a:latin"), typeface=r["face"])
    for s in spec:
        sl = prs.slides.add_slide(next(lay for lay in prs.slide_layouts if lay.name == s["layout"]))
        used = set()
        for d in s["shapes"]:
            x, y, w, h = d["box"]
            if d.get("ph"):
                sh = next(p for p in sl.placeholders if p.placeholder_format.type in kinds[d["ph"]])
                used.add(sh.shape_id)
                sh.left, sh.top, sh.width, sh.height = I(x), I(y), I(w), I(h)
            elif d["kind"] == "line":
                sh = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, I(x), I(y), I(w), I(h))
            elif d["kind"] == "chart":
                c = d["chart"]
                cd = CategoryChartData()
                cd.categories = c["cats"]
                for k, vals in enumerate(c["vals"]):
                    cd.add_series(f"S{k + 1}", vals)
                sh = sl.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, I(x), I(y), I(w), I(h), cd)
                ch = sh.chart
                if c["gap"] is not None:
                    ch.plots[0].gap_width = c["gap"]
                if c["max"] is not None:
                    ch.value_axis.maximum_scale = c["max"]
                    ch.value_axis.minimum_scale = c["min"] or 0
                if c["label_pt"]:
                    dl = ch.plots[0].series[0].data_labels
                    dl.show_value = True
                    dl.font.size = Pt(c["label_pt"])
                    if c["label_pos"] == "outEnd":
                        dl.position = XL_LABEL_POSITION.OUTSIDE_END
                if c["layout"]:
                    pa = ch._chartSpace.find(".//" + qn("c:plotArea"))
                    lay = pa.find(qn("c:layout"))
                    if lay is None:
                        lay = etree.SubElement(pa, qn("c:layout"))
                        pa.insert(0, lay)
                    ml = etree.SubElement(lay, qn("c:manualLayout"))
                    for tag, val in (("layoutTarget", "inner"), ("xMode", "edge"), ("yMode", "edge"),
                                     *((k, c["layout"][k]) for k in ("x", "y", "w", "h"))):
                        etree.SubElement(ml, qn(f"c:{tag}"), val=str(val))
            elif d["kind"] == "shape":
                sh = sl.shapes.add_shape(MSO_SHAPE.CHEVRON if d["auto"] == "chevron" else MSO_SHAPE.RECTANGLE,
                                         I(x), I(y), I(w), I(h))
            else:
                sh = sl.shapes.add_textbox(I(x), I(y), I(w), I(h))
            sh.name = d["name"]
            if d.get("rot"):
                sh.rotation = d["rot"]
            if d.get("paras"):
                text(sh, d)
        for p in list(sl.placeholders):
            if p.shape_id not in used:
                p._element.getparent().remove(p._element)
    prs.save(str(path))
    return path


@pytest.fixture(scope="module")
def r2_deck(tmp_path_factory):
    return _build_daniel_deck(tmp_path_factory.mktemp("r2deck") / "Ridgeway_PdM_Client_Deck.pptx")
REPORT = FIX / "northfield_report.txt"
TODAY = "2026-11-26"


def _fails(path, brief=None):
    from engines.forge.visual_check import check
    out: dict = {}
    for f in check(str(path), {"stamps": [], **(brief or {})}):
        if f["status"] == "fail":
            out.setdefault((f["rule"], f.get("slide")), []).append(f["message"])
    return out


def _texts(sl):
    return [sh.text_frame.text for sh in sl.shapes if sh.has_text_frame and sh.text_frame.text.strip()]


# ---------------------------------------------------------------------------------------------------- item 1
def test_the_checks_fail_each_flaw_daniel_saw_in_his_round_2_deck(r2_deck):
    got = _fails(r2_deck)
    s4 = " ".join(got.get(("P31", 4), []))
    assert "ov_FlowPointer" in s4, got                                     # the chevron over the left panel
    assert "touches the value label '31'" in s4, got                        # "12 months faster" on the "31"
    s6 = " ".join(got.get(("P31", 6), []))
    assert "'Owner and timing' wraps up into the section number '04'" in s6, got
    assert "'What it involves' has no text in rows 3, 4" in " ".join(got.get(("P24", 6), [])), got
    assert "repeats the source line" in " ".join(got.get(("P30", 7), [])), got
    assert "'project fact base'" in " ".join(got.get(("P30", 4), [])), got   # item 2
    assert ("P32", 1) in got, got                                           # the cover's empty navy


def _render_live(tmp_path, slides=None):
    """Daniel's live plan through the renderer, in his brand, as the build draws it (present density)."""
    from engines.forge import requested as RQ
    from engines.forge.render import render, plain_sources
    from engines.forge.grounded import ground
    slides = copy.deepcopy(slides or LIVE)
    close = next(s for s in slides if s["archetype"] == "closing")
    close["steps"] = close["steps"][:4]
    for st in close["steps"][2:]:                       # the final deck: rows 3 and 4 had no detail left
        st.pop("detail", None)
        st.pop("milestones", None)
    s27 = {"node": "5.1", "archetype": "chart_takeaway", "section": "Why pilot-first wins",
           "title": "Cumulative net benefit turns positive in year 1 and reaches EUR 3.1m by year 3",
           "subtitle": "Cumulative net benefit, five-plant rollout, EUR m, years 0 to 3 (modelled)",
           "chart": {"kind": "column", "categories": ["Year 0", "Year 1", "Year 2", "Year 3"],
                     "series": [{"name": "EUR m", "values": [-1.2, 0.4, 1.9, 3.1]}], "decimals": 1,
                     "highlight_categories": ["Year 3"]},
           "takeaway": {"text": "Net benefit is positive from year 1"},
           "bullets": [{"text": "Year 0 starts at -1.2, the upfront outlay"}],
           "source": {"text": "Northfield Research, Industry Benchmark Report 2025; economics modelled from 37 case "
                              "studies"},
           "footnotes": ["Northfield Research, Industry Benchmark Report 2025; economics modelled from 37 case studies"]}
    slides.insert(len(slides) - 1, s27)
    brief = BRIEF + "\n--- northfield_report.txt ---\n" + REPORT.read_text()
    ground(slides, brief, BRIEF)
    plain_sources(slides, brief)
    theme, brand = RQ.look(RQ.parse(BRIEF))
    out = tmp_path / "live.pptx"
    render(slides, str(out), fix_labels=False, theme=theme, brand=brand, density="present")
    return out


def test_the_renderer_draws_daniels_slides_without_those_flaws(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_TODAY", TODAY)
    out = _render_live(tmp_path)
    got = _fails(out)
    for rule in ("P24", "P30", "P31", "P32"):
        assert not [k for k in got if k[0] == rule], (rule, got)
    prs = Presentation(str(out))
    close = prs.slides[len(prs.slides) - 1]
    heads = [sh for sh in close.shapes if sh.name.startswith("role_subtitle_Head")]
    assert "What it involves" not in [h.text_frame.text for h in heads]        # rows 3 and 4 had nothing in it
    sec = next(sh for sh in close.shapes if sh.name == "ov_SectionNo")
    for h in heads:                                                            # every head on one line, clear of "04"
        assert h.left + h.width <= sec.left or h.top >= sec.top + sec.height, h.text_frame.text
    s7 = prs.slides[5]
    assert not [sh for sh in s7.shapes if sh.name == "Footnotes"], _texts(s7)  # the source line is printed once


def test_a_pointer_never_squeezes_into_a_gap_and_a_bracket_clears_the_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_TODAY", TODAY)
    out = _render_live(tmp_path)
    prs = Presentation(str(out))
    s4 = prs.slides[3]
    from engines.forge.visual_check import _labels, _vis_rect, BRACKET_CLEAR
    gf = next(sh for sh in s4.shapes if getattr(sh, "has_chart", False) and sh.has_chart)
    labs = _labels(gf)
    assert labs
    for sh in s4.shapes:
        if sh.name.startswith(("ov_DeltaArrow", "ov_Head_Delta")):
            r = _vis_rect(sh)
            for lb in labs:
                assert min(r[2], lb[2]) - max(r[0], lb[0]) <= -BRACKET_CLEAR + 1e-6 or \
                    min(r[3], lb[3]) - max(r[1], lb[1]) <= -BRACKET_CLEAR + 1e-6 or r[3] <= lb[1] - BRACKET_CLEAR, \
                    (sh.name, r, lb)


def test_empty_cells_in_a_filled_table_column_fail_the_checks(tmp_path):
    from engines.forge.render import render
    s = {"archetype": "table_focus", "title": "Pilot-first pays back in 19 months against 31 for plant-wide",
         "table": {"columns": ["Approach", "Payback (months)", "Main trade-off"],
                   "rows": [["Pilot line", "19", "Data issues found early"], ["Vendor service", "24", "Lock-in"],
                            ["Plant-wide", "31", "Data problems found late"], ["Do nothing", "0", "Downtime stays"]]},
         "takeaway": {"text": "Start with one line"}, "bullets": [{"text": "Pilot first pays back fastest"}],
         "source": {"text": "Northfield Research, Industry Benchmark Report 2025"}}
    out = tmp_path / "t.pptx"
    render([s], str(out), fix_labels=False)
    assert not [k for k in _fails(out) if k[0] == "P24"]
    prs = Presentation(str(out))
    tb = next(sh for sh in prs.slides[0].shapes if getattr(sh, "has_table", False) and sh.has_table).table
    for ri in (3, 4):                                     # rows 1 and 2 keep their text, 3 and 4 go blank (slide 6)
        for p in tb.cell(ri, 2).text_frame.paragraphs:
            for r in p.runs:
                r.text = ""
    prs.save(str(out))
    msgs = " ".join(m for (r, _), v in _fails(out).items() if r == "P24" for m in v)
    assert "'Main trade-off' has empty cells in rows 3, 4" in msgs, msgs


# ---------------------------------------------------------------------------------------------------- item 2
def test_source_lines_name_the_report_never_the_working_files():
    from engines.forge.render import plain_sources
    slides = copy.deepcopy(LIVE)
    s21 = next(s for s in slides if s["node"] == "2.1")
    assert "project fact base" in s21["source"]["text"]
    plain_sources(slides, "")
    t = s21["source"]["text"]
    assert "fact base" not in t and "project" not in t, t
    assert t == ("Median payback by option, 2025; success rates and vendor renewal intent from Northfield Research, "
                 "Industry Benchmark Report 2025"), t
    one = [{"source": {"text": "Survey results (F9, S2C1); see the dossier"}}]
    plain_sources(one, "Brief\n--- Northfield_PdM_Benchmark_2025.pdf ---\ntext")
    assert one[0]["source"]["text"] == "Survey results from Northfield_PdM_Benchmark_2025.pdf", one


# ---------------------------------------------------------------------------------------------------- items 2, 3, 6
def _plan_with_inventions():
    plan = copy.deepcopy(PLAN)
    plan[0]["date"] = "Board meeting, November 2026"
    s3 = next(s for s in plan if s["id"] == "S3")
    s3["source"]["text"] = "Northfield Research, 2025 survey; success rates from the project fact base (F9)"
    return plan


@pytest.fixture(scope="module")
def grounded_deck(tmp_path_factory):
    """The scripted plan (with its "Target 47%" line, owners and dates, and a board meeting on the cover) built from
    Daniel's brief with the report attached."""
    mp = pytest.MonkeyPatch()
    from engines.forge import build as B
    from engines.forge import common as CM
    mp.setattr(B, "_review_on", lambda client, rules: False)
    mp.setenv("MH_PERSONAS", "0")
    mp.setenv("MH_DOSSIER", "0")
    mp.setenv("MH_ASK_AUTO", "1")
    mp.setenv("MH_TODAY", TODAY)
    mp.setattr(CM, "find_soffice", lambda *a, **k: None)
    rules = copy.deepcopy(CM.load_rules())
    rules["repair"]["max_rounds"] = 1
    mp.setattr(B, "load_rules", lambda *a, **k: rules)
    from engines.forge.engine import solve
    work = tmp_path_factory.mktemp("round2")
    rep = work / "Northfield_PdM_Benchmark_2025.txt"
    shutil.copy(REPORT, rep)
    res = solve(BRIEF + FACTS, workdir=str(work), task_id="r2", client=Scripted(_plan_with_inventions()),
                files=[str(rep)])
    mp.undo()
    out = Path(res["artifacts"][0])
    return {"pptx": str(out), "qa": json.loads((out.parent / "qa.json").read_text())}


def test_the_deck_adds_no_target_owner_date_or_meeting_the_sources_do_not_give(grounded_deck):
    prs = Presentation(grounded_deck["pptx"])
    every = " | ".join(t for sl in prs.slides for t in _texts(sl))
    assert not [sh.name for sl in prs.slides for sh in sl.shapes if sh.name.startswith("ov_Target")]
    assert not re.search(r"\bTarget\b", every), every
    assert "Board meeting" not in every and "November 2026" in every
    close = " | ".join(_texts(prs.slides[len(prs.slides) - 1]))
    for made_up in ("COO", "CFO", "Head of Maintenance", "Procurement", "HR with", "29 Jan 2027", "30 Apr 2027",
                    "June 2027", "Owners and dates are proposed", "Owner and timing"):
        assert made_up not in close, (made_up, close)
    assert grounded_deck["qa"]["rules"]["P29"] == "pass", [f["message"] for f in grounded_deck["qa"]["failures"]
                                                         if f["rule"] == "P29"]


def test_the_source_lines_of_the_built_deck_name_the_report(grounded_deck):
    prs = Presentation(grounded_deck["pptx"])
    srcs = [sh.text_frame.text for sl in prs.slides for sh in sl.shapes if sh.name == "Source"]
    assert srcs and not [t for t in srcs if re.search(r"(?i)fact base|dossier|\bF\d+\b", t)], srcs
    assert any("success rates from Northfield Research" in t for t in srcs), srcs
    assert grounded_deck["qa"]["rules"]["P30"] == "pass"


def test_the_invented_material_check_flags_daniels_deck(r2_deck):
    from engines.forge.grounded import check
    import datetime as dt
    corpus = BRIEF + "\n--- Northfield_PdM_Benchmark_2025.pdf ---\n" + REPORT.read_text()
    got = {f["slide"]: f["message"] for f in check(str(r2_deck), corpus, BRIEF, None, dt.date(2026, 11, 26))}
    assert "the occasion 'Board meeting'" in got.get(1, ""), got
    assert "a 'Target' the sources do not set" in got.get(3, ""), got
    for bit in ("the owner 'Plants'", "the owner 'HR'", "the date '29 Jan 2027'", "the date 'January 2027'"):
        assert bit in got.get(6, ""), (bit, got)
    assert 2 not in got and 5 not in got, got        # figures straight from the report pass


def test_a_next_step_the_sources_name_no_owner_for_says_so(tmp_path):
    from engines.forge.render import render
    from engines.forge.grounded import ground
    s = {"archetype": "closing", "title": "Approve one pilot line today",
         "steps": [{"action": "Choose the pilot line", "owner": "COO", "timing": "29 Jan 2027"},
                   {"action": "Review vendor lock-in clauses", "owner": "Procurement", "timing": "26 Feb 2027"},
                   {"action": "Track three KPIs from day one", "owner": "Plant manager"}],
         "source": {"text": "Northfield Research, Industry Benchmark Report 2025"}}
    import datetime as dt
    slides = [s]
    ground(slides, REPORT.read_text() + "\nThe COO owns the pilot.", "", dt.date(2026, 11, 26))
    assert s["steps"][0] == {"action": "Choose the pilot line", "owner": "COO"}
    assert "owner" not in s["steps"][1] and "timing" not in s["steps"][1]
    out = tmp_path / "c.pptx"
    render(slides, str(out), fix_labels=False)
    sl = Presentation(str(out)).slides[0]
    when = {sh.name: sh.text_frame.text for sh in sl.shapes if sh.name.startswith("When")}
    assert when == {"When1": "COO", "When2": "Owner to confirm", "When3": "Owner to confirm"}, when
    assert "Owner" in [sh.text_frame.text for sh in sl.shapes if sh.name.startswith("role_subtitle_Head")]


def test_speaker_notes_keep_to_about_120_words(grounded_deck):
    """Slide 2's notes in the plan run to about 250 words; slide 6's live notes were 253."""
    from engines.forge.notes import limit_for, trim
    assert len(next(s for s in PLAN if s["id"] == "S1")["notes"].split()) > 200
    notes = [s.notes_slide.notes_text_frame.text for s in Presentation(grounded_deck["pptx"]).slides]
    assert all(notes) and max(len(t.split()) for t in notes) <= 120, [len(t.split()) for t in notes]
    assert all(t.rstrip().endswith((".", "?", "!", '"', ")")) for t in notes), notes   # whole sentences
    assert limit_for(BRIEF) == 120 and limit_for("Add detailed speaker notes") == 300
    assert limit_for("speaker notes of about 200 words per slide") == 200
    long_ = " ".join(f"Sentence {i} has exactly six words." for i in range(40))
    assert len(trim(long_, 120).split()) == 120 and trim(long_, None) == long_


# ---------------------------------------------------------------------------------------------------- item 4
def _add_one(deck, tmp_path, task="Add one chart slide after slide 5 (1 slide)"):
    from engines.forge.engine import add_to_deck
    added = {"node": "6", "archetype": "chart_takeaway", "section": "Why a pilot wins",
             "title": "A five-plant rollout turns positive in year 1 and reaches EUR 3.1m by year 3",
             "chart": {"kind": "column", "categories": ["Y0", "Y1", "Y2", "Y3"],
                       "series": [{"name": "Cumulative net benefit, EUR m", "values": [-1.2, 0.4, 1.9, 3.1]}],
                       "decimals": 1},
             "takeaway": {"text": "Payoff turns positive within one year"},
             "source": {"text": "Northfield Research, Industry Benchmark Report 2025"},
             "notes": "These figures are modelled from 37 case studies."}

    class One(Scripted):
        def chat(self, *, model, messages, **kw):
            if "ONE consulting slide" in messages[0]["content"]:
                return {"choices": [{"message": {"content": json.dumps(added)}}]}
            if "engagement manager" in messages[0]["content"]:
                return {"choices": [{"message": {"content": json.dumps({"slides": [
                    {k: added[k] for k in ("node", "archetype", "title", "section")}]})}}]}
            return super().chat(model=model, messages=messages, **kw)
    return add_to_deck(task, str(deck), workdir=str(tmp_path / "work"), task_id="add", client=One([]))


@pytest.fixture()
def quiet_build(monkeypatch):
    from engines.forge import build as B
    from engines.forge import common as CM
    monkeypatch.setattr(B, "_review_on", lambda client, rules: False)
    monkeypatch.setenv("MH_PERSONAS", "0")
    monkeypatch.setattr(CM, "find_soffice", lambda *a, **k: None)


def test_slides_added_to_a_deck_are_saved_into_that_deck(tmp_path, quiet_build, r2_deck):
    deck = tmp_path / "Ridgeway_PdM_Client_Deck.pptx"
    shutil.copy(r2_deck, deck)
    (tmp_path / "work").mkdir()
    res = _add_one(deck, tmp_path)
    assert res["artifacts"] == [str(deck)], res
    assert len(Presentation(str(deck)).slides) == 8
    assert not list(tmp_path.glob("*(updated*")), list(tmp_path.iterdir())
    assert "changed in place" in res["answer"] and "Undo" in res["answer"]


def test_a_slide_added_to_a_deck_keeps_its_title_in_the_title_placeholder(tmp_path, quiet_build, r2_deck):
    """Slide 7 of Daniel's deck, added by the forge, had its title as a plain shape "Title 1": the checks reported
    "no filled title placeholder" (P7)."""
    from pptx.enum.shapes import PP_PLACEHOLDER
    old = Presentation(str(r2_deck)).slides[6]
    assert old.shapes.title is None                                   # the round 2 deck, as delivered
    deck = tmp_path / "Ridgeway_PdM_Client_Deck.pptx"
    shutil.copy(r2_deck, deck)
    (tmp_path / "work").mkdir()
    _add_one(deck, tmp_path)
    prs = Presentation(str(deck))
    new = prs.slides[5]                                                # "after slide 5"
    t = new.shapes.title
    assert t is not None and t.is_placeholder, [sh.name for sh in new.shapes]
    assert t.placeholder_format.type == PP_PLACEHOLDER.TITLE
    assert t.text_frame.text.startswith("A five-plant rollout turns positive in year 1")
    assert len([sh for sh in new.placeholders if sh.placeholder_format.type == PP_PLACEHOLDER.TITLE]) == 1
    assert t.left is not None and t.width is not None and t.top is not None      # its own geometry
    from engines.forge.validate_pptx import validate
    res = validate(str(deck), render=False)
    assert not [f for f in res["failures"] if f["status"] == "fail" and f.get("slide") == 6 and
                "title" in f["message"].lower() and "placeholder" in f["message"].lower()], res["failures"]


def test_a_copy_only_for_a_lock_file_or_an_explicit_save_as(tmp_path, quiet_build, r2_deck):
    from engines.forge.engine import deck_target
    deck = tmp_path / "Board deck.pptx"
    shutil.copy(r2_deck, deck)
    (tmp_path / "work").mkdir()
    assert deck_target(deck, "Update the same deck") == (deck, "in_place")
    assert deck_target(deck, "Add a slide and save it as a new file")[1] == "save_as"
    lock = tmp_path / "~$Board deck.pptx"
    lock.write_bytes(b"x")
    res = _add_one(deck, tmp_path)
    assert res["artifacts"] == [str(tmp_path / "Board deck (updated).pptx")], res
    assert len(Presentation(str(deck)).slides) == 7 and "open in another app" in res["answer"]


# ---------------------------------------------------------------------------------------------------- item 5
MEMO = ("# Predictive Maintenance in European Mid-Market Manufacturing: Client Memo\n\n"
        "## Ridgeway Advisory | Confidential\n\n**To:** Client leadership team\n\n## Purpose\n\n"
        "This memo summarises the Northfield Research Industry Benchmark Report 2025.\n")
MEMO_BRIEF = ("Write a one-page memo. Footer 'Ridgeway Advisory | Confidential'. Deep navy #14304F with an amber "
              "accent #E8A33D, Georgia headings.")


def _memo(tmp_path, monkeypatch, **kw):
    from docx import Document
    from meshpool_harness.agent import office_mcp_server as OS
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_CLARITY", "0")
    monkeypatch.setattr(OS, "_project", lambda: tmp_path)
    msg, bad = OS.make_document(MEMO, filename="Ridgeway_PdM_Memo.docx", **kw)
    assert not bad, msg
    return Document(str(tmp_path / "Ridgeway_PdM_Memo.docx"))


def _check_memo(d):
    from docx.oxml.ns import qn
    body = [p.text for p in d.paragraphs]
    assert "Ridgeway Advisory | Confidential" not in body, body             # not a heading in the text
    assert [p.text for s in d.sections for p in s.footer.paragraphs] == ["Ridgeway Advisory | Confidential"]
    for name in ("Title", "Heading 1", "Heading 2"):
        st = d.styles[name]
        assert str(st.font.color.rgb) == "14304F", name
        assert st.font.name == "Georgia", name
    rule = d.styles["Title"].element.pPr.find(qn("w:pBdr")).find(qn("w:bottom"))
    assert rule.get(qn("w:color")) == "14304F"                              # not Word's 4F81BD


def test_the_memo_carries_the_brand_and_the_footer(tmp_path, monkeypatch):
    d = _memo(tmp_path, monkeypatch, title="Predictive Maintenance in European Mid-Market Manufacturing: Client Memo",
              brand=MEMO_BRIEF)
    _check_memo(d)


def test_the_memo_follows_the_brand_of_the_chats_deck_brief(tmp_path, monkeypatch):
    from meshpool_harness.agent import office_jobs as OJ
    monkeypatch.setenv("MH_CHAT_ID", "chat-r2")
    monkeypatch.setitem(OJ.JOBS, "job-r2", {"scope": "chat-r2", "engine": "slides", "request": BRIEF, "started": 1})
    d = _memo(tmp_path, monkeypatch, title="Predictive Maintenance in European Mid-Market Manufacturing: Client Memo")
    _check_memo(d)
