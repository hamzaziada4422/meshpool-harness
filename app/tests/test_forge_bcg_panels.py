"""The BCG panel grammar: from-to shift rows, evidence lists, think-cell difference arrows and category descriptors on
column charts, and the deck-wide 'say it once' check."""
from pptx import Presentation

from engines.forge import build as B
from engines.forge import parts as PT
from engines.forge import render as R
from engines.forge import validate_pptx as VP

SRC = {"text": "Source: company accounts"}
SHIFT = {"type": "shift", "heading": "While cost drivers are falling...", "rows": [
    {"label": "High-value task delivery", "from": {"text": "High-skill employee research", "value": ">$100K/yr"},
     "to": {"text": "AI agent-powered task delivery", "value": "~$2-3K/yr"}},
    {"label": "Advisory costs", "from": {"text": "In-person advice per interaction", "value": "~$5"},
     "to": {"text": "AI-enabled advice per conversation", "value": "$0.20-0.30"}},
    {"label": "Time to access knowledge", "from": {"text": "Manual analysis of market data", "value": "3 hrs"},
     "to": {"text": "AI gathers and synthesises data", "value": "5 mins"}}]}
CHART = {"type": "chart", "heading": "... revenue potential is rising", "chart": {
    "kind": "column", "categories": ["Traditional", "Digitally enhanced", "AI-first"],
    "series": [{"name": "Index", "values": [1, 5, 30]}], "unit": "x", "decimals": 0,
    "delta": {"from": "Traditional", "to": "AI-first", "label": "25-35x"},
    "descriptors": ["IT as support function", "Digital processes, no redesign", "Redesigned around AI"]}}
EVIDENCE = {"type": "evidence", "heading": "Firms set up early capture the most", "items": [
    {"name": "Lemonade", "text": "Customer acquisition costs cut by 90%"},
    {"name": "Klarna", "text": "50% smaller workforce and 73% more revenue per FTE"},
    {"name": "Electrolux", "text": "60% of support calls resolved without a human agent"}]}


def _deck(slides, tmp_path):
    out = tmp_path / "deck.pptx"
    r = R.render(slides, str(out), carry=False, density="balanced", fix_labels=False)
    return r, Presentation(str(out)), str(out)


def _names(sl):
    return [sh.name for sh in sl.shapes]


def test_three_panel_story_draws_shift_chart_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_FETCH_LOGOS", "0")
    s = {"archetype": "grid", "id": "s1", "node": "1", "layout": "thirds",
         "title": "Low costs and high revenues give AI-first firms the edge",
         "parts": [SHIFT, CHART, EVIDENCE, {"type": "callout", "text": "Set up now to capture the margin"}],
         "source": SRC}
    r, prs, out = _deck([s], tmp_path)
    assert r["errors"] == []
    sl = prs.slides[0]
    nm = _names(sl)
    assert sum(n.startswith("ov_ShiftTile") for n in nm) == 3
    assert sum(n.startswith("ov_ShiftArrow") for n in nm) == 3
    assert any(x.startswith("ov_DeltaArrow") for x in nm) and "ov_DeltaLabel" in nm
    assert sum(x.startswith("ov_DeltaArrow") for x in nm) == 3       # bars in between: an elbow over them
    assert sum(n.startswith("ov_CatNote") for n in nm) == 3
    assert sum(n.startswith("ov_EvText") for n in nm) == 3
    text = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
    for word in ("25-35x", ">$100K/yr", "5 mins", "Klarna", "Redesigned around AI"):
        assert word in text
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    for rule in ("P5", "P7", "P8", "P9", "P17"):
        assert v["rules"][rule] == "pass", [f for f in v["failures"] if f["rule"] == rule]


def test_shift_type_never_breaks_a_word():
    narrow = PT._shift_plan(SHIFT, 3.6, up=3)
    for r, lab, fb, tb, need in narrow["rows"]:
        for wd in lab[0]["text"].split():
            assert R._need_w(wd, lab[0]["size"], True) <= narrow["lw"] - 0.2
        for b in fb + tb:
            if b.get("bold"):
                assert R._need_w(b["text"], b["size"], True) <= narrow["fw"] - 0.04


def test_part_schema_for_shift_and_evidence():
    assert PT.part_errors(SHIFT, 1) == [] and PT.part_errors(EVIDENCE, 2) == []
    assert PT.part_errors({"type": "shift", "rows": [{"label": "x", "from": {"value": "1"}}]}, 1)
    assert PT.part_errors({"type": "evidence", "items": [{"text": "no name"}]}, 1)


def test_delta_label_reads_as_a_multiple_or_a_gap():
    assert R._delta_label(1, 30, "x", 0) == "30x"
    assert R._delta_label(12, 19, "days", 0) == "+7 days"
    assert R._delta_label(10, 25, "", 0) == "2.5x"
    assert R._delta_label(18, 11, "%", 0) == "−7 pts"


def test_a_stated_multiple_is_not_a_mixed_measure():
    c = dict(CHART["chart"])
    assert R.chart_problems(c) == []
    c.pop("delta")
    assert R.chart_problems(c)


def test_short_column_chart_without_its_point_goes_back_to_the_writer():
    s = {"archetype": "column_trend", "title": "Waits grew", "chart": {
        "kind": "column", "categories": ["2023", "Today"], "series": [{"name": "Wait", "values": [12, 19]}]}}
    msgs = B.fill_checks([s], "balanced").get(1, [])
    assert any("delta" in m for m in msgs)
    s["chart"]["target"] = 10
    assert not any("must show its point" in m for m in B.fill_checks([s], "balanced").get(1, []))


def test_a_point_is_made_once_in_the_deck():
    slides = [{"archetype": "cover", "title": "x"},
              {"archetype": "executive_summary", "points": [{"lead": "No-shows cost the clinics $3.1M a year today"}]},
              {"archetype": "exhibit_argument", "title": "a",
               "blocks": [{"text": "No-shows cost the clinics $3.1M a year today in lost visits"}]},
              {"archetype": "exhibit_argument", "title": "b",
               "blocks": [{"text": "No-shows cost the clinics $3.1M a year today in lost visits"},
                          {"text": "Evening hours cut waits to 8 days at three pilot clinics"}]}]
    out = B.repeat_checks(slides)
    assert 2 not in out and 3 not in out                 # the summary sums up by design; slide 3 says it first
    assert any("repeats slide 3" in m for m in out[4])


def test_a_callout_that_restates_the_cards_is_flagged():
    s = {"archetype": "exhibit_argument", "title": "a",
         "blocks": [{"text": "Reminders cut no-shows from 18% to 11% across five clinics"}],
         "callout": {"bullets": ["Reminders cut no-shows from 18% to 11% across five clinics"]}}
    s2 = dict(s, callout={"bullets": ["Rolling reminders out first is the cheapest lever we have"]})
    assert B.repeat_checks([s]) and not B.repeat_checks([s2])


def test_minus_signs_and_highlights():
    assert R.MINUS.sub("−", "-38% in 2021-2025") == "−38% in 2021-2025"


def test_a_steep_fall_reads_as_times_lower_and_period_charts_keep_their_cagr():
    assert R._delta_label(100, 4, "x", 1) == "25x lower"
    assert R._delta_label(10, 4, "", 0) == "2.5x lower"
    s = {"archetype": "column_trend", "title": "Sales grew", "chart": {
        "kind": "column", "categories": ["FY2022", "FY2023", "FY2024"], "series": [{"name": "S", "values": [1, 2, 3]}]}}
    assert not any("must show its point" in m for m in B.fill_checks([s], "balanced").get(1, []))


def test_a_from_to_slide_counts_as_an_exhibit():
    assert "shift" in B.EXHIBIT_PARTS and "evidence" in B.EXHIBIT_PARTS


def test_a_plan_never_schedules_work_in_the_past():
    import datetime
    s = {"archetype": "closing", "title": "Approve the pilot", "steps": [
        {"action": "Select depots", "detail": "Pick the two depots by end of January 2026", "timing": "Q1 2026"},
        {"action": "Launch", "detail": "Vans on the road", "timing": "Q2 2027"}]}
    out = B.date_checks([s], datetime.date(2026, 11, 20))
    assert any("Q1 2026" in m for m in out[1]) and any("January 2026" in m for m in out[1])
    assert not any("2027" in m for m in out[1])
    assert B.date_checks([dict(s, archetype="exhibit_argument")], datetime.date(2026, 11, 20)) == {}


def test_the_writer_is_told_today(monkeypatch):
    monkeypatch.setenv("MH_TODAY", "2026-11-20")
    assert "20 November 2026" in B.today_line() and "after today" in B.today_line()


def test_repeated_bullets_are_cut_and_a_block_keeps_its_lead():
    slides = [{"archetype": "exhibit_argument", "title": "a", "blocks": [
                  {"text": "Electric vans save $0.21 on every mile driven at current power prices",
                   "bullets": [{"text": "$0.21 saved on every mile driven at current power prices"}]}]},
              {"archetype": "exhibit_argument", "title": "b", "blocks": [
                  {"text": "Depot charging costs $1.4M per city across both pilot cities",
                   "bullets": [{"text": "Electric vans save $0.21 on every mile driven at current power prices"},
                               {"text": "Grid upgrades take nine months from order to power on site"}]}]}]
    assert B.drop_repeats(slides) == 2
    assert slides[0]["blocks"][0]["bullets"] == [] and slides[0]["blocks"][0]["text"]
    assert [b["text"] for b in slides[1]["blocks"][0]["bullets"]] == [
        "Grid upgrades take nine months from order to power on site"]


def test_the_summary_points_are_the_sections():
    slides = [{"archetype": "executive_summary", "title": "x", "section": "Answer",
               "points": [{"lead": "a"}, {"lead": "b"}, {"lead": "c"}]},
              {"archetype": "exhibit_argument", "title": "a", "section": "Where we stand"},
              {"archetype": "exhibit_argument", "title": "b", "section": "Where we stand"},
              {"archetype": "table_focus", "title": "c", "section": "The options"},
              {"archetype": "closing", "title": "d", "section": "The plan"}]
    assert B.tracker_checks(slides) == {}
    assert B.tracker_checks([dict(slides[0], points=slides[0]["points"][:2])] + slides[1:])
    nums = [s.get("_section", "").split(" | ")[0] for s in R.plan_forms([dict(s) for s in slides])]
    assert nums == ["", "01", "01", "02", "03"]


def test_the_summary_is_a_statement_panel(tmp_path, monkeypatch):
    monkeypatch.setitem(R.SUMMARY_PANEL, "on", True)                 # off by default; the layout is kept
    s = {"archetype": "executive_summary", "id": "s2", "node": "1",
         "title": "Fund the pilot now and gate the rollout on a 2% downtime target",
         "decision": "Decision today: approve the two gates for the 2027 rollout",
         "points": [{"lead": "Spend runs ahead of plan", "bullets": ["£19M spent against £17M planned"]},
                    {"lead": "Automation lifts productivity", "bullets": ["Picks per hour rose from 92 to 151"]},
                    {"lead": "Downtime must fall first", "bullets": ["Coventry runs at 7.1% against 2%"]}],
         "metrics": [{"value": "£48M", "label": "programme budget"}, {"value": "3 of 8", "label": "sites live"}],
         "source": SRC}
    r, prs, out = _deck([s], tmp_path)
    nm = _names(prs.slides[0])
    assert r["errors"] == [] and "ov_StatementPanel" in nm and nm.index("ov_StatementPanel") < nm.index("Title 1")
    assert {"role_numeral_1", "Claim3", "Evidence3", "ov_StatementAsk"} <= set(nm)
    text = " ".join(sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)
    assert "01" in text and "£48M" in text and "Approve the two gates" in text
    v = VP.validate(out, VP.load_rules(), render=False, density="balanced")
    for rule in ("P7", "P8", "P9", "P11", "P17"):
        assert v["rules"][rule] == "pass", [f for f in v["failures"] if f["rule"] == rule]


def test_writer_hears_about_thin_summary_rows_and_zero_bars():
    from engines.forge.build import fill_checks
    summ = {"archetype": "executive_summary", "title": "t",
            "points": [{"lead": f"Point {i}", "bullets": [f"Fact {i}"]} for i in range(4)]}
    full = dict(summ, points=[{"lead": f"Point {i}", "bullets": [f"Fact {i}", f"More {i}"]} for i in range(4)])
    zero = {"archetype": "column_trend", "title": "t", "chart": {
        "kind": "column", "categories": ["Renewal price", "New quotes", "Claims satisfaction"], "delta": {"from": 0, "to": 1},
        "series": [{"name": "% change", "values": [22, 9, 0]}]}}
    got = fill_checks([summ, full, zero], "balanced")
    assert any("summary rows are thin" in m for m in got.get(1, []))
    assert not any("summary rows are thin" in m for m in got.get(2, []))
    assert any("bar of 0" in m for m in got.get(3, []))


def test_fact_ids_in_calcs_are_not_prose():
    """calcs[].from must hold fact ids: flagging them sent six slides back to the writer for nothing (relay build)."""
    from engines.forge.build import prose_checks
    s = {"archetype": "bar_takeaway", "title": "The pilot costs 40% less", "calcs": [
        {"id": "S1C1", "value": "40%", "formula": "1 - 5.4 / 9", "from": ["F2"]}],
         "bullets": [{"text": "Pilot costs £5.4M (F2)"}]}
    got = prose_checks([s]).get(1, [])
    assert not any("calcs" in m for m in got)
    assert any("bullets[0]" in m and "F2" in m for m in got)


def test_calcs_may_use_small_numbers_from_the_facts():
    """'picks per hour rose from 92 to 151': (151 - 92) / 92 is a fair calc; the page-text filter for small ordinals
    dropped the 92 from the fact and sent the slide back (a live relay build lost its 64% this way)."""
    from engines.forge.build import verify_calcs, load_rules
    facts = [{"id": "F1", "text": "picks per hour rose from 92 to 151; 3 of 8 warehouses are automated"}]
    s = {"archetype": "bar_takeaway", "title": "Picks rose 64%", "calcs": [
        {"id": "C1", "value": "64%", "formula": "(151 - 92) / 92 * 100", "from": ["F1"]},
        {"id": "C2", "value": "5", "formula": "8 - 3", "from": ["F1"]}]}
    ok, bad = verify_calcs([s], facts, load_rules())
    assert not bad.get(1), bad


def test_a_stat_value_must_be_a_figure():
    from engines.forge.build import fill_checks
    s = {"archetype": "grid", "title": "t", "parts": [{"type": "stats", "items": [
        {"value": "-140", "label": "roles"}, {"value": "None", "label": "made redundant"}]}]}
    assert any("stat value is a figure" in m for m in fill_checks([s], "balanced").get(1, []))


def test_rounded_figures_and_ranges_count_as_the_fact():
    """3.6x is 7.1 / 2 rounded (a float edge refused it); '81-83%' gives 81% as well as 83% (the range's first figure
    lost its unit and the writer dropped the peer benchmark); '(7.1%)' is an aside, not a negative."""
    from engines.forge.build import _same_number
    from engines.forge.common import numbers
    assert _same_number("3.6x", 3.6, "x", 3.55, "x")
    got = numbers("peers report renewal rates of 81-83%; Coventry (7.1%); a loss of (4.2)")
    assert ("81", 81.0, "%") in got and any(v == 7.1 and u == "%" for _, v, u in got) and \
        any(v == -4.2 for _, v, _u in got)


def test_a_chart_may_plot_small_numbers_the_facts_give():
    """'picks per hour rose from 92 to 151': a before/after chart of 92 and 151 is sourced (the chart check dropped the
    92 with the page-text filter and the writer replaced the chart with text)."""
    from engines.forge.build import chart_fact_checks, load_rules
    facts = [{"id": "F1", "text": "picks per hour rose from 92 to 151 in automated sites"}]
    s = {"archetype": "column_trend", "title": "Picks rose 64%", "chart": {
        "kind": "column", "categories": ["Before", "After"], "series": [{"name": "Picks per hour", "values": [92, 151]}]}}
    assert not chart_fact_checks([s], facts, load_rules()).get(1)


def test_a_footnote_citing_a_figure_the_slide_never_shows_goes_back():
    from engines.forge.build import fill_checks
    s = {"archetype": "closing", "title": "Gate the rollout on downtime",
         "steps": [{"action": "Trial the second vendor", "detail": "The quote is £2.1M cheaper per site"}],
         "footnotes": ["The £4.2M is the £2.1M per-site difference for two sites."]}
    got = fill_checks([s], "balanced").get(1, [])
    assert any("footnote cites 4.2M" in m for m in got)
    ok = dict(s, steps=[{"action": "Trial the second vendor", "detail": "Saves £4.2M across two sites, £2.1M each"}])
    assert not any("footnote cites" in m for m in fill_checks([ok], "balanced").get(1, []))


def test_a_footnote_may_quote_a_fact_the_chart_does_not_plot():
    from engines.forge.build import fill_checks
    s = {"archetype": "challenge_matrix", "title": "Claims service is not the cause",
         "rows": [], "footnotes": ["Claims satisfaction is 7.9 of 10, flat on 2024."]}
    facts = [{"id": "F1", "text": "claims satisfaction is 7.9 of 10, flat on 2024"}]
    assert not any("footnote cites" in m for m in fill_checks([s], "balanced", facts).get(1, []))
