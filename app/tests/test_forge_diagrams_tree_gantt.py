"""Tree and Gantt diagram archetypes (engines/forge/diagrams/tree.py, gantt.py): issue_tree, org_chart, gantt.

Each is rendered short, normal and long (no LibreOffice): no render errors, the slide carries `arch:<archetype>`, the
shapes exist, every shape inside the part is declared (`ov_`), lines are glued or axis-aligned hairlines, and what does
not fit is cut word by word into the speaker notes. The check functions give precise messages for bad input."""
import re

import pytest
from pptx import Presentation
from pptx.util import Emu

from engines.forge import diagrams as D
from engines.forge import render as R

SRC = {"text": "Source: integration office, September 2026"}
ARGUMENT = {"takeaway": {"text": "Costs, not volume, drive the profit gap"},
            "bullets": [{"text": "Unit costs rose on materials and labour"}, {"text": "Price held"}], "source": SRC}


def _slide(arch, title, **fields):
    return dict({"archetype": arch, "id": f"{arch}_1", "node": "n1", "title": title, "subtitle": "Exhibit"},
                **fields, **ARGUMENT)


# ------------------------------------------------------------------ content

TREE_SHORT = {"root": "Profit", "highlight": "Unit costs", "branches": [
    {"label": "Revenue", "value": "$410M", "children": [{"label": "Volume", "value": "+0.5%"},
                                                         {"label": "Price", "value": "+0.5%"}]},
    {"label": "Costs", "value": "$380M", "children": [{"label": "Fixed costs", "value": "+2%"},
                                                      {"label": "Unit costs", "value": "+19%"}]}]}
TREE_NORMAL = {"root": "Why did operating profit fall by $3.1M?", "highlight": "Materials", "branches": [
    {"label": "Revenue", "value": "$410M (+1%)", "children": [{"label": "Volume"}, {"label": "Price"}, {"label": "Mix"}]},
    {"label": "Costs", "value": "$380M (+12%)", "children": [
        {"label": "Fixed costs"}, {"label": "Unit costs", "children": [{"label": "Materials"}, {"label": "Labor"}]}]},
    {"label": "One-offs", "children": [{"label": "Restructuring"}, {"label": "Legal settlement"}]}]}


def _long_driver(k):
    return {"label": f"Driver {k} with a rather long descriptive name that goes on and on",
            "value": f"${k * 3}M and further commentary"}


TREE_LONG = {"root": "Which of the many possible levers should the board prioritise to restore group operating margin?",
             "branches": [{"label": "Grow revenue in existing markets through pricing and mix", "value": "$410M",
                           "children": [_long_driver(k) for k in (1, 2, 3, 4)]},
                          {"label": "Reduce unit costs across the supply chain and operations", "value": "$380M",
                           "children": [_long_driver(k) for k in (5, 6, 7, 8)]},
                          {"label": "Release working capital", "value": "$90M",
                           "children": [_long_driver(k) for k in (9, 10, 11, 12)]}]}

ORG_SHORT = {"root": {"label": "Chief Executive", "role": "Dana Reyes"}, "highlight": "Finance", "children": [
    {"label": "Operations", "role": "Ahmed Khan"}, {"label": "Finance", "role": "Lin Zhou"},
    {"label": "Commercial", "role": "Marta Silva"}]}
ORG_NORMAL = {"root": {"label": "Chief Executive", "role": "Dana Reyes"}, "highlight": "Segment B lead", "children": [
    {"label": "Segment A lead", "role": "J. Cole", "children": [{"label": "Team 1"}, {"label": "Team 2"}]},
    {"label": "Segment B lead", "role": "K. Ito", "children": [{"label": "Team 1"}, {"label": "Team 2"}]},
    {"label": "Segment C lead", "role": "M. Roy", "children": [{"label": "Team 1"}]},
    {"label": "Operations lead", "role": "A. Khan"}, {"label": "Finance lead", "role": "L. Zhou"}]}
ORG_LONG = {"root": {"label": "Group Chief Executive Officer and President", "role": "Dana Reyes, appointed 2021"},
            "children": [{"label": f"Chief {n} Officer, {n} organisation", "role": "Name Surname",
                          "children": [{"label": f"{n} programme office and delivery", "role": "Head"},
                                       {"label": f"{n} shared services"}, {"label": f"{n} risk"}]}
                         for n in ("Operating", "Financial", "Technology", "Commercial", "People", "Strategy", "Legal",
                                   "Marketing", "Quality")]}

PERIODS = ["Q1 26", "Q2 26", "Q3 26", "Q4 26", "Q1 27", "Q2 27"]
GANTT_SHORT = {"periods": PERIODS[:4], "rows": [
    {"label": "Pick pilot sites", "start": 0, "end": 1}, {"label": "Hire and train team", "start": "Q1 26", "end": "Q3 26"},
    {"label": "Interim review", "milestones": [{"at": 2, "label": "Go or no-go"}]},
    {"label": "Final recommendation", "start": 3, "end": 3, "highlight": True}]}
GANTT_NORMAL = {"periods": PERIODS, "rows": [
    {"label": "Discovery and design", "group": "Set up", "start": 0, "end": 1},
    {"label": "Hire and train team", "group": "Set up", "start": 0, "end": 2,
     "milestones": [{"at": 2, "label": "Team ready"}]},
    {"label": "Launch pilot", "group": "Pilot", "start": 2, "end": 2, "highlight": True},
    {"label": "Run and measure", "group": "Pilot", "start": 2, "end": 4},
    {"label": "Scale decision", "group": "Rollout", "start": 4, "end": 5,
     "milestones": [{"at": 5, "label": "Board approves"}]}]}
GANTT_LONG = {"periods": [f"Wk {k}" for k in range(1, 17)], "rows": [
    {"label": f"Workstream task number {k} with a long descriptive label that wraps",
     "group": ("Foundations and governance", "Build", "Rollout and adoption")[k // 5],
     "start": k % 6, "end": k % 6 + 3 + (k % 4),
     "milestones": [{"at": k % 6 + 3 + (k % 4), "label": f"Milestone {k} signed off"}] if k % 3 == 0 else []}
    for k in range(1, 15)]}

CASES = {
    "issue_tree": ("The profit gap traces back to unit costs", (TREE_SHORT, TREE_NORMAL, TREE_LONG)),
    "org_chart": ("One leader runs each customer segment", (ORG_SHORT, ORG_NORMAL, ORG_LONG)),
    "gantt": ("The pilot launches in the third quarter", (GANTT_SHORT, GANTT_NORMAL, GANTT_LONG)),
}
GRID = [(a, v, i) for a in CASES for i, v in enumerate(("short", "normal", "long"))]


def _render(tmp_path, slide):
    out = tmp_path / "d.pptx"
    r = R.render([slide], str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out)).slides[0]


def _name(sl):
    return sl._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}cSld").get("name")


def _notes(sl):
    return sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""


def _diagram_shapes(sl):
    return [sh for sh in sl.shapes if sh.name.startswith("ov_")]


# ------------------------------------------------------------------ rendering

@pytest.mark.parametrize("arch,version,k", GRID)
def test_renders_without_errors_and_declares_its_shapes(tmp_path, arch, version, k):
    title, content = CASES[arch]
    r, sl = _render(tmp_path, _slide(arch, title, **content[k]))
    assert f"arch:{arch}" in _name(sl)
    cut = [e for e in r["errors"] if "does not fit" in e]
    assert [e for e in r["errors"] if e not in cut] == []
    if version != "long":
        assert cut == []
    shapes = _diagram_shapes(sl)
    assert len(shapes) >= 8
    names = {sh.name for sh in shapes}
    assert any(re.match({"issue_tree": r"ov_ITNode", "org_chart": r"ov_OC(Root|Box)", "gantt": r"ov_GBar"}[arch], n)
               for n in names)
    for sh in shapes:                                       # no group, no picture, no emoji: native shapes only
        assert sh.shape_type is None or "GROUP" not in str(sh.shape_type) and "PICTURE" not in str(sh.shape_type)
    for sh in shapes:                                       # nothing below 10 pt
        if sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for run in p.runs:
                    assert run.font.size is None or run.font.size.pt >= 10


@pytest.mark.parametrize("arch", list(CASES))
def test_lines_are_glued_or_axis_aligned_hairlines(tmp_path, arch):
    title, content = CASES[arch]
    _, sl = _render(tmp_path, _slide(arch, title, **content[1]))
    lines = [sh for sh in sl.shapes if sh.shape_type is not None and "LINE" in str(sh.shape_type).upper()
             or sh._element.tag.endswith("cxnSp")]
    assert lines
    for ln in lines:
        assert min(abs(Emu(ln.width).inches), abs(Emu(ln.height).inches)) < 0.02          # horizontal or vertical
        assert ln.line.width.pt <= 1.5


@pytest.mark.parametrize("arch", list(CASES))
def test_colours_are_theme_slots_only(tmp_path, arch):
    title, content = CASES[arch]
    out = tmp_path / "d.pptx"
    R.render([_slide(arch, title, **content[1])], str(out), carry=False, density="balanced", fix_labels=False)
    from zipfile import ZipFile
    xml = ZipFile(out).read("ppt/slides/slide1.xml").decode()
    assert "srgbClr" not in xml and "gradFill" not in xml


def test_long_content_is_cut_into_the_notes_not_overflowed(tmp_path):
    for arch, k in (("issue_tree", 2), ("org_chart", 2), ("gantt", 2)):
        title, content = CASES[arch]
        slide = _slide(arch, title, **content[k])
        r, sl = _render(tmp_path, slide)
        assert "Did not fit and was left out" in _notes(sl), arch
        assert any("does not fit at the minimum size" in e for e in r["errors"]), arch


def test_issue_tree_keeps_the_leaf_order_and_marks_the_highlight(tmp_path):
    title, content = CASES["issue_tree"]
    _, sl = _render(tmp_path, _slide("issue_tree", title, **content[0]))
    nodes = sorted((sh for sh in sl.shapes if sh.name.startswith("ov_ITNode")),
                   key=lambda sh: int(sh.name.rsplit("_", 1)[1]))              # (the shape tree is in reading order)
    assert [sh.text_frame.text.split("\n")[0] for sh in nodes] == ["Profit", "Revenue", "Volume", "Price", "Costs",
                                                                    "Fixed costs", "Unit costs"]
    ys = [Emu(sh.top).inches for sh in nodes if sh.text_frame.text.split("\n")[0] in
          ("Volume", "Price", "Fixed costs", "Unit costs")]
    gaps = [round(b - a, 2) for a, b in zip(ys, ys[1:])]
    assert len(set(gaps)) == 1                               # leaves stacked evenly
    hot = [sh for sh in nodes if sh.text_frame.text.startswith("Unit costs")][0]
    assert 'schemeClr val="accent1"' in hot._element.xml


def test_org_chart_wraps_a_wide_row_under_the_same_parent(tmp_path):
    title, content = CASES["org_chart"]
    kids = [{"label": f"Regional director {k} for the northern territories"} for k in range(8)]
    slide = _slide("org_chart", title, root={"label": "Chief Executive"}, children=kids)
    slide["layout"] = "halves"
    _, sl = _render(tmp_path, slide)
    boxes = [sh for sh in sl.shapes if sh.name.startswith("ov_OCBox")]
    assert len(boxes) == 8
    rows = {round(Emu(sh.top).inches, 2) for sh in boxes}
    assert len(rows) <= 2
    assert max(Emu(sh.width).inches for sh in boxes) >= 1.0   # never squeezed below the floor


def test_gantt_bars_milestones_and_label_column(tmp_path):
    title, content = CASES["gantt"]
    _, sl = _render(tmp_path, _slide("gantt", title, **content[1]))
    bars = [sh for sh in sl.shapes if sh.name.startswith("ov_GBar")]
    assert len(bars) == 5
    assert all(sh.auto_shape_type is not None and "RECTANGLE" in str(sh.auto_shape_type) for sh in bars)
    diamonds = [sh for sh in sl.shapes if sh.name.startswith("ov_GMilestone")]
    assert len(diamonds) == 2 and all("DIAMOND" in str(sh.auto_shape_type) for sh in diamonds)
    heads = [sh for sh in sl.shapes if sh.name.startswith("ov_GHead")]
    assert [sh.text_frame.text for sh in heads] == PERIODS
    labels = [sh for sh in sl.shapes if sh.name.startswith("ov_GLabel")]
    assert labels and max(Emu(sh.width).inches for sh in labels) <= 0.35 * 12.2
    groups = [sh for sh in sl.shapes if sh.name.startswith("ov_GGroup")]
    assert [sh.text_frame.text for sh in groups] == ["Set up", "Pilot", "Rollout"]


def test_gantt_rows_that_cannot_fit_the_height_are_left_out_and_noted(tmp_path):
    from engines.forge import parts as PT
    title, content = CASES["gantt"]
    slide = _slide("gantt", title, **content[2])
    real = PT.compose

    def small(parts, lay, look, top, bottom, s=None):
        return [(0, (0.6, 1.55, 6.0, 2.5), parts[0])]
    PT.compose = small
    try:
        r, sl = _render(tmp_path, slide)
    finally:
        PT.compose = real
    assert len([sh for sh in sl.shapes if sh.name.startswith("ov_GBar")]) < 14
    assert "Did not fit and was left out" in _notes(sl)


# ------------------------------------------------------------------ check functions

def _errs(kind, part):
    return D.part_errors(dict(part, type=kind), 1)


def test_issue_tree_messages():
    assert _errs("issue_tree", {"root": "Profit", "branches": TREE_NORMAL["branches"]}) == []
    e = _errs("issue_tree", {"root": "", "branches": [{"label": "A"}]})
    assert any("needs 'root'" in m for m in e)
    e = _errs("issue_tree", {"root": "R", "branches": [{"label": "A", "children": [{"value": "1"}]}]})
    assert any("branches[0].children[0]: needs a 'label'" in m for m in e)
    many = [{"label": f"B{k}", "children": [{"label": "x"}, {"label": "y"}]} for k in range(7)]
    e = _errs("issue_tree", {"root": "R", "branches": many})
    assert any("14 leaves: at most 12" in m and "drop 2" in m for m in e)
    deep = [{"label": "A", "children": [{"label": "B", "children": [{"label": "C", "children": [{"label": "D"}]}]}]}]
    e = _errs("issue_tree", {"root": "R", "branches": deep})
    assert any("3 levels" in m and "branches[0].children[0].children[0]" in m for m in e)
    e = _errs("issue_tree", {"root": "R", "branches": [{"label": "A"}], "highlight": "Nope"})
    assert any("highlight 'Nope' matches no node label" in m for m in e)


def test_org_chart_messages():
    assert _errs("org_chart", {"root": {"label": "CEO"}, "children": ORG_NORMAL["children"]}) == []
    assert any("needs 'root'" in m for m in _errs("org_chart", {"children": [{"label": "A"}]}))
    e = _errs("org_chart", {"root": "CEO", "children": [{"label": "A", "children": [{"label": "B", "children": [
        {"label": "C"}]}]}]})
    assert any("children[0].children[0]" in m and "3 levels" in m for m in e)
    e = _errs("org_chart", {"root": "CEO", "children": [{"label": f"U{k}"} for k in range(17)]})
    assert any("17 children: at most 16" in m for m in e)
    e = _errs("org_chart", {"root": "CEO", "children": [{"role": "x"}]})
    assert any("children[0]: needs a 'label'" in m for m in e)


def test_gantt_messages():
    ok = {"periods": PERIODS, "rows": GANTT_NORMAL["rows"]}
    assert _errs("gantt", ok) == []
    assert any("3 to 16" in m for m in _errs("gantt", {"periods": ["a", "b"], "rows": [{"label": "x", "start": 0,
                                                                                         "end": 1}]}))
    assert any("duplicate label 'Q1'" in m for m in _errs("gantt", {"periods": ["Q1", "Q2", "Q1"], "rows": [
        {"label": "x", "start": 0, "end": 1}]}))
    e = _errs("gantt", {"periods": PERIODS, "rows": [{"label": "Build", "start": "Q9 26", "end": 2}]})
    assert any("rows[0] ('Build'): start 'Q9 26' is not one of the periods 'Q1 26' to 'Q2 27'" in m for m in e)
    e = _errs("gantt", {"periods": PERIODS, "rows": [{"label": "Build", "start": 4, "end": "Q2 26"}]})
    assert any("ends in 'Q2 26' before it starts in 'Q1 27'" in m for m in e)
    e = _errs("gantt", {"periods": PERIODS, "rows": [{"label": "Build", "start": 0, "end": 9}]})
    assert any("end 9 is not one of the" in m and "index 0 to 5" in m for m in e)
    e = _errs("gantt", {"periods": PERIODS, "rows": [{"label": "Gate", "milestones": [{"at": "Q7", "label": "x"}]}]})
    assert any("milestones[0] ('x'): at 'Q7' is not one of the" in m for m in e)
    e = _errs("gantt", {"periods": PERIODS, "rows": [{"label": "Empty"}]})
    assert any("needs 'start' and 'end', or at least one milestone" in m for m in e)
    e = _errs("gantt", {"periods": PERIODS, "rows": [{"label": f"r{k}", "start": 0, "end": 1} for k in range(15)]})
    assert any("15 rows: at most 14" in m for m in e)


def test_archetype_schema_errors_reach_the_slide():
    bad = _slide("gantt", "The pilot launches", periods=PERIODS, rows=[{"label": "x", "start": 3, "end": 1}])
    assert any("ends in" in m for m in D.schema_errors(bad))
    assert D.ARCHETYPES["gantt_plan"] == "gantt" and D.ARCHETYPES["issue_tree"] == "issue_tree"
    assert D.ARCHETYPES["org_chart"] == "org_chart"
