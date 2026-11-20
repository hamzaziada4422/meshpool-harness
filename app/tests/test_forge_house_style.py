"""House style (docs/deck-engine/house-style.md): blocks side by side share their top and bottom edges, row lists use
the body, short tables run full width with their so-what under them, and the validator fails a column that stops
short of the one beside it."""
from pptx import Presentation

from engines.forge import render as R
from engines.forge import validate_pptx as VP

SRC = {"text": "Source: company accounts 2021-2025"}
IN = 914400


def _render(slides, tmp_path, name="deck.pptx"):
    out = tmp_path / name
    r = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out)), str(out)


def _box(sh):
    return sh.left / IN, sh.top / IN, sh.width / IN, sh.height / IN


def _feet_failures(path):
    v = VP.validate(path, VP.load_rules(), render=False, density="balanced")
    return [f for f in v["failures"] if "share their top and bottom" in f["message"]]


def test_unaligned_feet_flags_a_panel_that_stops_short():
    shp = lambda name, x, y, w, h, kind="sp": {"name": name, "kind": kind, "depth": 0, "x": x, "y": y, "w": w,  # noqa
                                               "h": h, "filled": True, "text": "", "role": None}
    s = {"shapes": [shp("Table", 0.6, 1.6, 6.5, 4.6, "graphicFrame"), shp("ov_Panel", 7.4, 1.6, 5.3, 2.4)]}
    f = VP._unaligned_feet(s, 3)
    assert f and f["rule"] == "P20" and "ov_Panel" in f["message"] and "2.2in" in f["message"]
    s["shapes"][1]["h"] = 4.4                                      # within 0.45in: aligned
    assert VP._unaligned_feet(s, 3) is None
    s["shapes"][1]["h"] = 2.4                                      # a second part continues the shorter column
    s["shapes"].append(dict(shp("Callout", 7.4, 4.2, 5.3, 2.0), text="so what"))
    assert VP._unaligned_feet(s, 3) is None


def test_next_steps_use_the_body(tmp_path):
    s = {"archetype": "closing", "id": "s", "node": "9", "title": "Approve option B and start talks this month",
         "steps": [{"action": "Approve $6M", "detail": "Funds the contract", "owner": "CFO", "timing": "December"},
                   {"action": "Sign the partner", "detail": "Locks local production", "owner": "COO", "timing": "Q1"},
                   {"action": "Set the gate", "detail": "Plant only if sales reach $20M", "owner": "CEO",
                    "timing": "Q1"}], "source": SRC}
    r, prs, out = _render([s], tmp_path)
    sl = prs.slides[0]
    bands = [sh for sh in sl.shapes if sh.name.startswith("ov_RowBand")]
    assert r["errors"] == [] and bands                             # alternate rows on a light band
    last = max(sh.top + sh.height for sh in sl.shapes if sh.name.startswith(("Action", "Detail", "When"))) / IN
    assert last > R.FOOT_RULE_Y - 0.5                              # the rows run to the foot of the body
    assert not _feet_failures(out)


def test_a_short_table_runs_full_width_with_its_so_what_under_it(tmp_path):
    s = {"archetype": "table_focus", "id": "s", "node": "3", "title": "The package cuts turnover to 14%",
         "table": {"columns": ["Effect", "Timing"], "rows": [["Turnover 19% to 14%", "Year 1"],
                                                            ["Satisfaction +20 points", "Year 1"],
                                                            ["200 nurses", "18 months"]]},
         "takeaway": {"text": "The $16.3M package cuts agency spend by $21M by year two"},
         "bullets": [{"text": "Self-rostering is the cheapest option at $0.8M"},
                     {"text": "International recruitment adds 200 nurses at $9M"}], "source": SRC}
    r, prs, out = _render([s], tmp_path)
    sl = prs.slides[0]
    tbl = next(sh for sh in sl.shapes if sh.has_table)
    assert _box(tbl)[2] > 11.0                                     # full width, not a half beside a panel
    assert not _feet_failures(out)


def test_points_beside_a_chart_end_at_its_foot(tmp_path):
    s = {"archetype": "bar_takeaway", "id": "s", "node": "3", "title": "Vietnam is the third of five markets",
         "chart": {"kind": "bar", "categories": ["Thailand", "Indonesia", "Vietnam", "Malaysia", "Philippines"],
                   "series": [{"name": "Sales", "values": [340, 290, 210, 150, 120]}], "unit": "$M"},
         "takeaway": {"text": "Vietnam is a $210M market today"},
         "bullets": [{"text": "Thailand and Indonesia are larger but already concentrated"},
                     {"text": "Size alone would rank Vietnam third"},
                     {"text": "Growth and openness decide the case"}], "source": SRC}
    r, prs, out = _render([s], tmp_path)
    sl = prs.slides[0]
    cards = [sh for sh in sl.shapes if sh.name.startswith("ov_PointCard")]
    chart = next(sh for sh in sl.shapes if sh.has_chart)
    assert r["errors"] == [] and len(cards) == 3
    foot = max(c.top + c.height for c in cards) / IN
    assert abs(foot - (chart.top + chart.height) / IN) < 0.2
    assert not _feet_failures(out)


def test_a_callout_beside_an_exhibit_takes_its_height(tmp_path):
    s = {"archetype": "grid", "id": "s", "node": "6", "title": "SME focus scores highest of four options",
         "layout": "main_side", "look": "boxed",
         "parts": [{"type": "harvey", "heading": "Options against criteria", "criteria": ["Return", "Risk", "Capital"],
                    "rows": [{"label": "Stay", "scores": [0, 4, 4]}, {"label": "Digital", "scores": [2, 2, 2]},
                             {"label": "SME focus", "scores": [4, 3, 3]}, {"label": "Merger", "scores": [3, 0, 1]}]},
                   {"type": "callout", "heading": "Why option three", "bullets": ["Highest return", "Moderate risk"]}],
         "source": SRC}
    r, prs, out = _render([s], tmp_path)
    assert r["errors"] == [] and not _feet_failures(out)


def test_a_roadmap_with_room_gives_each_milestone_a_card(tmp_path):
    s = {"archetype": "grid", "id": "s", "node": "9", "title": "The plan reaches a plant decision in 24 months",
         "layout": "full", "parts": [{"type": "timeline", "heading": "Entry roadmap", "milestones": [
             {"date": "Month 0", "label": "Sign co-manufacturer", "text": "Approve $6M and sign the contract"},
             {"date": "Month 9", "label": "Launch", "text": "1,200 modern-trade stores"},
             {"date": "Month 24", "label": "Gate", "text": "Build a plant if run-rate sales reach $20M"}]}],
         "source": SRC}
    r, prs, _ = _render([s], tmp_path)
    sl = prs.slides[0]
    cards = [sh for sh in sl.shapes if sh.name.startswith("ov_TLCard")]
    assert r["errors"] == [] and len(cards) == 3
    assert all(c.has_text_frame and c.text_frame.text.strip() for c in cards)    # the label is in its card (P17)


def _chart_slide(t, arch="bar_takeaway"):
    return {"archetype": arch, "id": t, "node": t, "title": f"{t} sales lead the region", "subtitle": "Sales, $M",
            "chart": {"kind": "bar", "categories": ["North", "South", "East"],
                      "series": [{"name": "Sales", "values": [12, 9, 6]}], "unit": "$M"},
            "takeaway": {"text": "North leads"}, "bullets": [{"text": "North grew 18%"}, {"text": "South is flat"}],
            "source": SRC}


def test_runs_of_chart_slides_alternate_and_rows_are_used_once():
    steps = [{"action": "Approve", "detail": "x", "owner": "CFO"}, {"action": "Sign", "detail": "y", "owner": "COO"}]
    risk = {"archetype": "challenge_matrix", "title": "Two risks need owners", "columns": ["Risk", "Today", "Action"],
            "rows": [{"label": "Supply", "cells": [{"lead": "One source"}, {"lead": "Add a second"}]},
                     {"label": "Price", "cells": [{"lead": "Rivals cut 8%"}, {"lead": "Hold list price"}]}]}
    deck = [_chart_slide("1"), _chart_slide("2", "column_trend"), _chart_slide("3"), risk,
            {"archetype": "table_focus", "title": "t", "table": {"columns": ["a", "b"], "rows": [["x", "y"]] * 3}},
            {"archetype": "closing", "title": "Approve now", "steps": steps}]
    forms = [s.get("_form") for s in R.plan_forms(deck)]
    assert forms[:3] == ["side", "mirror", "side"]               # short charts: chart left, then right, then left
    assert forms[3] == "columns" and forms[5] == "columns"      # rows next to a table: icon columns, a process
    alone = [risk, _chart_slide("4"), dict(risk, title="Two more risks")]
    assert [s.get("_form") for s in R.plan_forms(alone)][::2] == [None, "columns"]   # banded rows once per deck


def test_two_slides_in_a_row_that_look_alike_fail(tmp_path):
    R.POINTS["on"] = True
    deck = [_chart_slide("1"), _chart_slide("2")]
    for s in deck:
        s["layout"] = "side"                                    # forced: the same composition twice
    _, _, out = _render(deck, tmp_path)
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    assert any(f["rule"] == "P15" and "looks like slide 1" in f["message"] for f in v["failures"])
    _, _, out2 = _render([_chart_slide("1"), _chart_slide("2")], tmp_path, "d2.pptx")   # planned: they alternate
    v2 = VP.validate(out2, VP.load_rules(), render=False, density="balanced")
    assert not [f for f in v2["failures"] if "looks like slide" in f["message"]]


def test_a_big_number_shown_twice_goes_back_to_the_writer():
    from engines.forge.build import variety_checks
    st = lambda v: {"type": "stats", "items": [{"value": v, "label": "saving per van"}]}  # noqa: E731
    slides = [{"archetype": "grid", "title": "a", "parts": [st("$6,510"), {"type": "text", "lead": "x"}]},
              {"archetype": "bar_takeaway", "title": "b", "chart": {"categories": ["a"], "series": []}},
              {"archetype": "grid", "title": "c", "parts": [st("$6,510"), {"type": "chart", "chart": {}}]}]
    msgs = variety_checks(slides)
    assert any("already a big number on slide 1" in m for m in msgs.get(3, []))


def test_never_an_eyebrow_over_the_title(tmp_path):
    """The user's standing rule: no small label (eyebrow) over a slide title, ever. The section shows only as its
    number in the top right corner."""
    s = dict(_chart_slide("1"), section="What to do next")
    _, prs, _ = _render([s, dict(_chart_slide("2"), section="What to do next")], tmp_path)
    for sl in prs.slides:
        title = sl.shapes.title
        above = [sh for sh in sl.shapes if sh.has_text_frame and sh is not title and sh.text_frame.text.strip()
                 and sh.top + sh.height <= title.top + title.height * 0.5 and sh.left < 3 * IN
                 and not sh.name.startswith(("ov_Tag", "ov_SectionNo"))]
        assert not above, [(sh.name, sh.text_frame.text) for sh in above]
        assert "ov_SectionNo" in {sh.name for sh in sl.shapes}


def test_small_multiples_share_one_scale():
    from engines.forge import parts as P
    mk = lambda vals: {"type": "chart", "chart": {"kind": "bar", "categories": ["N", "S"], "unit": "$M",  # noqa: E731
                                                  "series": [{"name": "x", "values": vals}]}}
    parts = [mk([120, 84]), mk([64, 52]), mk([38, 35])]
    P._share_scale(parts)
    assert {p["chart"]["_shared_max"] for p in parts} == {120}
    mixed = [mk([1, 2]), dict(mk([3, 4]), chart=dict(mk([3, 4])["chart"], unit="%"))]
    P._share_scale(mixed)
    assert not any("_shared_max" in p["chart"] for p in mixed)   # different units: each its own scale


def test_column_charts_carry_think_cell_annotations(tmp_path):
    """A run of periods gets a CAGR arrow; a target value a dashed line; a target category a dashed outline."""
    s = {"archetype": "column_trend", "id": "t", "node": "1", "title": "Revenue grew 41% in three years",
         "chart": {"kind": "column", "categories": ["2022", "2023", "2024", "2025"],
                   "series": [{"name": "Revenue", "values": [410, 452, 489, 577]}], "unit": "$M", "target": 600},
         "source": SRC}
    r, prs, _ = _render([s], tmp_path)
    sl = prs.slides[0]
    names = {sh.name for sh in sl.shapes}
    assert r["errors"] == [] and {"ov_CagrLabel", "ov_TargetLine", "ov_TargetLabel"} <= names
    lab = next(sh for sh in sl.shapes if sh.name == "ov_CagrLabel").text_frame.text
    assert lab == "CAGR +12.1%"                                   # (577/410)^(1/3) - 1
