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
