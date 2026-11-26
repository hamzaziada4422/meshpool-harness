"""Root causes found on the forge-kb eval decks (docs/results/2026-11-20-forge-kb.md, ten worst slides): each test
pins the engine fix, not one slide."""
from pptx import Presentation
from pptx.util import Emu

from engines.forge import render as R


def _render(slides, tmp_path):
    out = tmp_path / "d.pptx"
    r = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out))


def test_minimal_look_parts_end_on_the_grid(tmp_path):
    """P7 on 10 baseline slides: the minimal look narrowed each part by its inset, so headings and callouts ended at
    12.58 in, off the 24-column grid (right edge 12.73 in)."""
    s = {"archetype": "grid", "id": "s", "node": "n", "title": "Two workstreams carry the plan this year",
         "layout": "halves", "look": "minimal",
         "parts": [{"type": "stats", "heading": "Where we stand", "items": [{"value": "5", "label": "red workstreams"},
                                                                         {"value": "9", "label": "amber workstreams"}]},
                   {"type": "callout", "heading": "So what", "bullets": ["Fund the recovery", "Review weekly"]}],
         "source": {"text": "Source: integration office"}}
    r, prs = _render([s], tmp_path)
    assert r["errors"] == []
    rights = [Emu(sh.left + sh.width).inches for sh in prs.slides[0].shapes
              if sh.name.startswith(("Heading", "Callout")) and sh.width]
    assert rights and all(abs(x - R.RIGHT) < 0.02 or x < R.RIGHT - 0.5 for x in rights), rights


def test_short_table_never_breaks_a_word_to_grow_its_type(tmp_path):
    """BCG benchmark slide: a short table in a tall third grew its type until "Automated" broke mid-word."""
    from engines.forge.common import text_width_in
    s = {"archetype": "grid", "id": "s", "node": "n", "title": "Automation cuts the cost of every claim step",
         "layout": "thirds", "parts": [
             {"type": "table", "heading": "Cost per task", "table": {"columns": ["Task", "Manual", "Automated"],
              "rows": [["First notice of loss", "$12", "$1.40"], ["Fraud screening", "$9", "$0.60"],
                       ["Settlement letter", "$6", "$0.20"]]}},
             {"type": "stats", "items": [{"value": "-52%", "label": "handling time"}]},
             {"type": "stats", "items": [{"value": "70%", "label": "settled same day"}]}],
         "source": {"text": "Source: synthetic"}}
    r, prs = _render([s], tmp_path)
    tbl = next(sh for sh in prs.slides[0].shapes if sh.has_table).table
    col = tbl.columns[2].width / 914400
    run = tbl.cell(0, 2).text_frame.paragraphs[0].runs[0]
    size = run.font.size.pt
    assert text_width_in("Automated", size, R._font("body", True)) <= col - 0.1


def test_process_in_a_near_square_region_stacks_and_fills(tmp_path):
    """P20 on 3 dev decks: short chevrons in the middle of a tall region left most of it empty."""
    from pptx import Presentation as P_
    from engines.forge import parts as PT
    prs = P_()
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    part = {"type": "process", "steps": [{"title": "Name an owner", "text": "One executive per red workstream"},
                                         {"title": "Weekly review", "text": "Steering committee tracks each item"},
                                         {"title": "Escalate at day 60", "text": "Unresolved items go to the CEOs"}]}
    PT.p_process(R.Ctx({}), sl, 1, part, (6.9, 1.9, 5.8, 4.4), "boxed", 1, {}, None)      # a half, near square
    tops = sorted({round(sh.top / 914400, 2) for sh in sl.shapes if sh.name.startswith("ov_Step1_")})
    assert len(tops) == 3                                  # stacked: one row per step, down the region
    sl2 = prs.slides.add_slide(prs.slide_layouts[6])
    PT.p_process(R.Ctx({}), sl2, 1, part, (0.6, 1.9, 12.1, 2.0), "boxed", 1, {}, None)     # full width, short
    assert len({round(sh.top / 914400, 2) for sh in sl2.shapes if sh.name.startswith("ov_Step1_")}) == 1


def test_band_frame_title_band_tracker_and_two_tone(tmp_path):
    from engines.forge.template import default_template
    from pptx import Presentation as P_
    s = {"archetype": "bar_takeaway", "id": "s", "node": "n", "tracker": "2",
         "title": "Executive summary | Margins rise with automation",
         "chart": {"kind": "bar", "categories": ["A", "B", "C"], "series": [{"name": "x", "values": [30, 22, 14]}]},
         "takeaway": {"text": "Margins **rise** with scale"}, "bullets": [{"text": "A leads B by **one point**"}],
         "source": {"text": "Source: synthetic"}}
    out = tmp_path / "b.pptx"
    r = R.render([s], str(out), carry=False, fix_labels=False, theme={"palette": "forest", "frame": "band"})
    assert r["errors"] == []
    prs = P_(str(out))
    sl = prs.slides[0]
    assert {sh.name for sh in sl.slide_layout.shapes} >= {"Band", "BandRule"}
    title = sl.shapes.title.text_frame.paragraphs[0].runs
    assert title[0].text == "Executive summary | " and len(title) >= 2
    assert any(sh.name == "ov_Tracker" and sh.text_frame.text == "2" for sh in sl.shapes)
    plain = R.render([dict(s, tracker=None)], str(tmp_path / "p.pptx"), carry=False, fix_labels=False)
    assert "Band" not in {sh.name for sh in P_(str(tmp_path / "p.pptx")).slides[0].slide_layout.shapes}
    assert plain["errors"] == [] and default_template({"palette": "forest", "frame": "band"}) != default_template("forest")


def test_stat_fallback_keeps_currency_units():
    """Live build: a "$M" chart whose category said "With 2% espresso rise" became "$41.326%", and "$41.2M" in the text
    was read back as "$41.2"."""
    c = {"kind": "bar", "categories": ["Reported Q3", "With 2% espresso rise (illustrative)"],
         "series": [{"name": "Q3 revenue ($M)", "values": [41.2, 41.326]}], "unit": "$M", "decimals": 3}
    vals = [it["value"] for it in R.stat_stack(c, "revenue of $41.2M and")["items"]]
    assert vals == ["$41.2M", "$41.326M"]
    assert R._as_written(83.0, "reached $83.0bn") == "$83.0bn" and R._as_written(35, "up 35% in") == "35%"


def test_bookends_respect_no_cover():
    """Live build: "No cover slide" got a cover because "cover slide" matched."""
    from engines.forge.build import bookends, no_cover
    assert bookends("No cover slide: just the two content slides.") == {"opening": False, "closing": False}
    assert bookends("Start with a cover and end with next steps.") == {"opening": True, "closing": True}
    assert bookends("Without a title slide, and no next steps slide") == {"opening": False, "closing": False}
    assert no_cover("No cover slide: just the two content slides.") and no_cover("Without a title page")
    assert not no_cover("Q3 update for the board.")                      # a silent brief keeps the default cover


def test_p7_accepts_content_padded_inside_a_card_only(tmp_path):
    """Dev decks 02 and 07: a chart and a text inset 0.15in inside a card on the grid were flagged off the grid."""
    from pptx import Presentation as P_
    from pptx.enum.dml import MSO_THEME_COLOR as TC
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    from engines.forge.measure_pptx import measure
    from engines.forge.validate_pptx import _grid, load_rules, validate
    rules = load_rules()

    def deck(path, card_edges=None):
        prs = P_()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        sl = prs.slides.add_slide(prs.slide_layouts[5])
        sl.shapes.title.text = "Cards hold padded content on the grid"
        if card_edges:
            l_, r_ = card_edges
            card = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(l_), Inches(1.6), Inches(r_ - l_), Inches(4))
            card.fill.solid()
            card.fill.fore_color.theme_color = TC.BACKGROUND_2
            card.name = "Card1"
            tb = sl.shapes.add_textbox(Inches(l_ + 0.15), Inches(1.8), Inches(r_ - l_ - 0.3), Inches(1))
            tb.text_frame.text = "Inside the card"
            tb.name = "Text1"
            loose = sl.shapes.add_textbox(Inches(r_ + 0.4), Inches(1.8), Inches(1.5), Inches(1))
            loose.text_frame.text = "Not in a card"
            loose.name = "Text2"
        prs.save(path)
    deck(tmp_path / "g.pptx")
    edges = sorted(_grid(measure(str(tmp_path / "g.pptx"), rules), rules["pptx"])[0])
    inner = [e for e in edges if 0.3 < e < 9]
    deck(tmp_path / "p7.pptx", (inner[0], inner[len(inner) // 2]))
    msgs = [f["message"] for f in validate(str(tmp_path / "p7.pptx"), rules, render=False)["failures"] if f["rule"] == "P7"]
    assert not any("Text1" in m for m in msgs) and any("Text2" in m for m in msgs), msgs


def test_object_cells_and_half_missing_columns_keep_a_real_table():
    """Live build b5: cells written as {"lead", "prov"} and a Growth column half N/A pushed the table past 30% missing,
    so it was drawn as a numbered list of "N/A"s. Half-missing columns now go (footnoted) and cell sources stay."""
    from engines.forge import render as R
    cell = lambda v, p=("F1",): {"lead": v, "prov": list(p)}  # noqa: E731
    rows = [[cell("Visa"), cell("233bn"), cell("N/A", ()), cell("N/A", ())],
            [cell("Stripe"), cell("$1.4T"), cell("+38%"), cell("N/A", ())],
            [cell("Adyen"), cell("EUR 1.29T"), cell("+33%"), cell("N/A", ())],
            [cell("Wise"), cell("GBP 145B"), cell("N/A", ()), cell("0.53%", ("F2",))],
            [cell("Corridor"), cell("N/A", ()), cell("N/A", ()), cell("0.9%", ("F3",))],
            [cell("Mastercard"), cell("$28.2B"), cell("+12%"), cell("N/A", ())]]
    s = R.normalize({"archetype": "table_focus", "id": "s", "node": "1", "title": "Wise earns 0.53% on GBP 145B",
                     "table": {"columns": ["Player", "Volume", "Growth", "Take rate"], "rows": rows}})
    assert s["table"]["columns"] == ["Player", "Volume"]
    assert set(s["table"]["prov"]) == {"F1", "F2", "F3"}
    assert not [e for e in R.schema_errors(s) if "missing" in e]
    assert "+38% for Stripe" in " ".join(s.get("footnotes") or []) or "+38%" in s.get("notes", "")


def test_a_body_slide_written_as_a_cover_is_written_again(monkeypatch, tmp_path):
    """Live build b7: the writer turned a planned options table into a dark cover page mid-deck."""
    import json
    from pathlib import Path
    from engines.forge import build as BLD, clarity
    asks = {}

    def fake_call(client, model, system, user, task_id, max_tokens=12000):
        if task_id.endswith("-story"):
            return {"slides": [{"node": "1", "title": "Electric vans save $0.21 a mile", "archetype": "argument_only"},
                               {"node": "2", "title": "Two cities first returns the most", "archetype": "table_focus"},
                               {"node": "3", "title": "Approve phase one now", "archetype": "closing"}]}
        item = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
        asks[item["node"]] = asks.get(item["node"], 0) + 1
        if item["node"] == "2" and asks["2"] == 1:
            return {"archetype": "cover", "node": "2", "title": item["title"], "subtitle": "Options"}
        return {"archetype": item["archetype"], "node": item["node"], "title": item["title"]}
    monkeypatch.setattr(BLD, "_call", fake_call)
    monkeypatch.setattr(clarity, "review", lambda *a, **k: {})
    rendered = {}

    def fake_render(slides, path, **kw):
        rendered["slides"] = [dict(s) for s in slides]
        Path(path).write_bytes(b"")
        prov = Path(path).with_suffix(".provenance.json")
        prov.write_text("{}")
        return {"provenance": str(prov), "errors": []}
    monkeypatch.setattr(BLD, "render", fake_render)
    monkeypatch.setattr(BLD, "validate", lambda *a, **k: {"rules": {}, "failures": []})
    for name in ("number_checks", "fill_checks", "chart_checks"):
        monkeypatch.setattr(BLD, name, lambda *a, **k: {})
    BLD.build("Three slides, no cover slide.", str(tmp_path), 3, client=object())
    assert asks["2"] == 2
    assert [s["archetype"] for s in rendered["slides"]] == ["argument_only", "table_focus", "closing"]


def test_stat_tiles_keep_each_value_at_its_own_precision():
    """Live build b7: a $/mile and a $/van value in one chart became tiles reading "$6,510.00" and "$24,500.00"."""
    from engines.forge import render as R
    c = {"categories": ["Diesel cost per mile", "Electric cost per mile", "Annual saving per van", "Net premium per van"],
         "series": [{"name": "Value", "values": [0.92, 0.71, 6510, 24500]}], "unit": "$", "decimals": 2}
    assert [i["value"] for i in R.stat_stack(c)["items"]] == ["$0.92", "$0.71", "$6,510", "$24,500"]


def test_an_overlong_source_line_is_shortened_by_clause_and_noted(tmp_path):
    """Live build b7: the writer pasted the fact list into the source; it ran past its two lines (P5)."""
    from pptx import Presentation
    from engines.forge import render as R
    long = ("Company fleet data: 420 diesel vans in 6 cities, $0.92/mile diesel vs $0.71/mile electric, 31,000 miles a "
            "year, $78,000 electric vs $46,000 diesel, $7,500 federal credit, $1.4M depot charging per city, 40 pilot "
            "vans over 12 months, 98.6% vs 99.1% route completion, maintenance down 38%, driver rating 4.4 vs 3.6, "
            "three large customers asking for zero-emission delivery by 2028, and the board pack of June 2026")
    s = {"archetype": "argument_only", "id": "s", "node": "1", "title": "Electric vans save $0.21 a mile",
         "blocks": [{"text": "Costs fall", "bullets": ["Diesel costs more per mile", "Maintenance fell"]}],
         "source": {"text": long}}
    out = tmp_path / "d.pptx"
    r = R.render([s], str(out), carry=False, fix_labels=False)
    sl = Presentation(str(out)).slides[0]
    src = next(sh for sh in sl.shapes if sh.name == "Source").text_frame.text
    # whole clauses, never "... and others" (Daniel, 0.0.19); the rest is a remark for the checks, not a note
    assert not src.endswith("and others") and len(src) < len(long) and "Company fleet data" in src
    assert any("Source line shortened" in m for m in r["remarks"])
