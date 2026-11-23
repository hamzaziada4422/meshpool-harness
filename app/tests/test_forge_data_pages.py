"""X-01: a deck carries the data the request asks for. Chart pages are computed from the user's csv/xlsx
(engines/forge/data_pages.py), and P22 fails a deck whose charts do not hold them."""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.forge import data_pages as DP  # noqa: E402

FIX = ROOT.parent / "testkit" / "practical" / "fixtures" / "office"
REQ = "Make a deck from quarterly.csv showing revenue by quarter for each region"
CSV = {"North": [1200, 1350, 1410, 1580], "South": [980, 1010, 1105, 1190], "East": [760, 840, 905, 1020],
       "West": [1430, 1395, 1510, 1655]}
needs_lo = pytest.mark.skipif(not shutil.which("soffice") and not shutil.which("libreoffice"),
                              reason="LibreOffice not installed")


def table(name):
    return dict(DP.tables([FIX / name]))[name]


def deck(path, charts):
    """A pptx with one slide per chart: (kind, categories, {series: values})."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    prs = Presentation()
    for cats, series in charts:
        sl = prs.slides.add_slide(prs.slide_layouts[6])
        cd = CategoryChartData()
        cd.categories = cats
        for n, v in series.items():
            cd.add_series(n, v)
        sl.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(8), Inches(5), cd)
    prs.save(str(path))
    return str(path)


def expectations():
    return DP.pages(REQ, [FIX / "quarterly.csv"])[2]


def test_ask_and_pivot_read_the_request():
    s = DP.ask(REQ, table("quarterly.csv"))
    assert s["measure"] == "revenue" and s["category"] == ("col", "quarter") and s["series"] == ("col", "region")
    pv = DP.pivot(table("quarterly.csv"), s)
    assert pv["categories"] == ["Q1", "Q2", "Q3", "Q4"]
    assert {x["name"]: x["values"] for x in pv["series"]} == CSV
    assert [x["name"] for x in pv["series"]] == list(CSV)                      # the file's order


def test_the_transposed_wording_swaps_axis_and_series():
    t = table("quarterly.csv")
    pv = DP.pivot(t, DP.ask("revenue by region for each quarter", t))
    assert pv["categories"] == ["North", "South", "East", "West"]
    assert [x["name"] for x in pv["series"]] == ["Q1", "Q2", "Q3", "Q4"]
    assert pv["series"][3]["values"] == [CSV[r][3] for r in CSV]
    for wording in ("revenue per region by quarter", "revenue by region and quarter", "each region's revenue over "
                    "the quarters"):
        assert DP.ask(wording, t)["series"] == ("col", "region"), wording


def test_nothing_to_compute_gives_none():
    t = table("quarterly.csv")
    assert DP.ask("a deck about our culture", t) is None and DP.ask("revenue", t) is None
    assert DP.tables([FIX / "contract.docx", "/nope.csv"]) == []                # never raises


def test_revenue_is_derived_from_units_and_price_per_month():
    import pandas as pd
    df = table("sales.csv")
    s = DP.ask("chart of revenue by month", df)
    pv = DP.pivot(df, s)
    d = pd.to_datetime(df["date"])
    want = (df["units"] * df["unit_price"]).groupby(d.dt.to_period("M")).sum()
    assert len(pv["categories"]) == len(want) and pv["categories"][0] == "Jan 2026"
    assert pv["series"][0]["values"] == [int(v) for v in want]                 # chronological, source arithmetic


def test_check_fails_the_audit_deck_and_names_what_is_missing(tmp_path):
    q4 = sum(v[3] for v in CSV.values())
    p = deck(tmp_path / "audit.pptx", [(["Q4"], {"Q4 revenue": [q4]})])
    fails = DP.check_data(p, expectations())
    assert len(fails) == 1 and fails[0]["rule"] == "P22" and fails[0]["slide"] == 1
    m = fails[0]["message"]
    assert "quarterly.csv" in m and "missing series North, South, East, West" in m
    assert "missing categories Q1, Q2, Q3" in m and "'Q4 revenue'" in m
    assert DP.check_data(str(tmp_path / "none.pptx"), expectations()) == []     # unreadable: P0's job, not P22's
    assert DP.check_data(deck(tmp_path / "empty.pptx", []), expectations())[0]["message"].count("no native chart")


def test_check_passes_the_full_chart_in_either_orientation_and_in_thousands(tmp_path):
    cats = ["Q1", "Q2", "Q3", "Q4"]
    assert DP.check_data(deck(tmp_path / "a.pptx", [(cats, CSV)]), expectations()) == []
    t = {f"Q{i + 1}": [CSV[r][i] for r in CSV] for i in range(4)}              # regions on the axis
    assert DP.check_data(deck(tmp_path / "b.pptx", [(list(CSV), t)]), expectations()) == []
    k = {r: [v / 1000 for v in vs] for r, vs in CSV.items()}                   # an axis in thousands
    assert DP.check_data(deck(tmp_path / "c.pptx", [(cats, k)]), expectations()) == []
    r1 = {r: [round(v / 1000, 1) for v in vs] for r, vs in CSV.items()}        # ... rounded to the decimals shown
    assert DP.check_data(deck(tmp_path / "d.pptx", [(cats, r1)]), expectations()) == []
    # one of several charts is enough; the wrong values are named
    bad = dict(CSV, North=[1200, 1350, 1410, 9999])
    f = DP.check_data(deck(tmp_path / "e.pptx", [(cats, bad)]), expectations())
    assert f and "North in Q4 shows 9,999 against 1,580" in f[0]["message"]
    assert DP.check_data(deck(tmp_path / "f.pptx", [(cats, bad), (cats, CSV)]), expectations()) == []


@needs_lo
def test_the_computed_page_renders_a_chart_that_passes_the_check(tmp_path):
    from pptx import Presentation
    from engines.forge.render import render
    pages, facts, expect = DP.pages(REQ, [FIX / "quarterly.csv"])
    assert len(pages) == 1 and all(f["id"].startswith("D") for f in facts)
    pg = pages[0]
    assert len(pg["title"].split()) <= 15 and "leads" in pg["title"] and pg["fixed"]
    assert pg["chart"]["kind"] == "column" and len(pg["chart"]["series"]) == 4
    out = tmp_path / "d.pptx"
    r = render([pg], str(out))
    assert r["errors"] == []
    assert any(s.has_chart for sl in Presentation(str(out)).slides for s in sl.shapes)
    assert DP.check_data(str(out), expect) == []


def test_many_categories_draw_a_line(tmp_path):
    pg = DP.pages("revenue by month", [FIX / "sales.csv"])[0][0]
    assert pg["chart"]["kind"] == "line" and pg["archetype"] == "line_trend" and len(pg["chart"]["categories"]) == 12


@needs_lo
def test_an_offline_build_carries_every_region_and_quarter_and_reports_p22(monkeypatch, tmp_path):
    from pptx import Presentation
    from engines.forge import build as BLD, clarity, engine as FE
    monkeypatch.setenv("MH_DOSSIER", "0")

    def fake_call(client, model, system, user, task_id, max_tokens=12000):
        if task_id.endswith("-story"):
            return {"slides": [{"node": "1", "title": "Revenue grows in every region", "archetype": "argument_only"},
                               {"node": "2", "title": "Act now to secure the growth", "archetype": "closing"}]}
        if "This slide: " in user:
            item = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
            return {"archetype": item["archetype"], "node": item["node"], "title": item["title"]}
        return {}
    monkeypatch.setattr(BLD, "_call", fake_call)
    monkeypatch.setattr(clarity, "review", lambda *a, **k: {})
    monkeypatch.setattr(FE, "brand_from", lambda task, files=None: ({}, None))
    csv = tmp_path / "quarterly.csv"
    shutil.copy(FIX / "quarterly.csv", csv)
    seen = {}
    real = BLD.build

    def spy(*a, **k):
        seen.update(k)
        res = real(*a, **k)
        seen["res"] = res
        return res
    monkeypatch.setattr(BLD, "build", spy)
    out = FE.solve(REQ + ", 4 slides", workdir=str(tmp_path / "w"), task_id="t", client=object(), files=[str(csv)])
    assert seen["data_expectations"] and seen["fixed_pages"][0]["fixed"]
    assert seen["res"]["rules"]["P22"] == "pass"
    prs = Presentation(out["artifacts"][0])
    charts = [s.chart for sl in prs.slides for s in sl.shapes if s.has_chart]
    ch = next(c for c in charts if len(list(c.plots[0].series)) == 4)
    assert list(c.label for c in ch.plots[0].categories) == ["Q1", "Q2", "Q3", "Q4"]
    assert {s.name: list(s.values) for s in ch.plots[0].series} == {k: [float(x) for x in v] for k, v in CSV.items()}
    assert "chart page computed from quarterly.csv" in out["answer"]
    assert json.loads((tmp_path / "w" / "qa.json").read_text())["rules"]["P22"] == "pass"


def test_a_lost_chart_fails_p22_and_reaches_the_answer(monkeypatch, tmp_path):
    """build() runs the check after every render; a deck without the chart fails P22 in qa.json and the answer."""
    from engines.forge import build as BLD, clarity, engine as FE
    monkeypatch.setenv("MH_DOSSIER", "0")
    monkeypatch.setattr(BLD, "_call", lambda client, model, system, user, task_id, max_tokens=12000: (
        {"slides": [{"node": "1", "title": "Revenue grows in every region", "archetype": "argument_only"}]}
        if task_id.endswith("-story") else {}))
    monkeypatch.setattr(clarity, "review", lambda *a, **k: {})
    monkeypatch.setattr(FE, "brand_from", lambda task, files=None: ({}, None))
    monkeypatch.setattr(BLD, "render", lambda slides, path, **kw: (
        deck(Path(path), [(["Q4"], {"Q4 revenue": [5445]})]),
        Path(path).with_suffix(".provenance.json").write_text("{}"),
        {"provenance": str(Path(path).with_suffix(".provenance.json")), "errors": []})[2])
    monkeypatch.setattr(BLD, "validate", lambda *a, **k: {"rules": {"P1": "pass"}, "failures": []})
    for n in ("number_checks", "fill_checks", "chart_checks"):
        monkeypatch.setattr(BLD, n, lambda *a, **k: {})
    csv = tmp_path / "quarterly.csv"
    shutil.copy(FIX / "quarterly.csv", csv)
    out = FE.solve(REQ + ", 3 slides", workdir=str(tmp_path / "w"), task_id="t", client=object(), files=[str(csv)])
    qa = json.loads((tmp_path / "w" / "qa.json").read_text())
    assert qa["rules"]["P22"] == "fail" and any(f["rule"] == "P22" for f in qa["failures"])
    assert "rules failing: P22" in out["answer"]


def test_check_deck_reports_p22_with_the_data_files(tmp_path):
    from engines.forge import tools as FT
    p = deck(tmp_path / "audit.pptx", [(["Q4"], {"Q4 revenue": [5445]})])
    text, err = FT.check_deck(p, render=False, data_files=[str(FIX / "quarterly.csv")], request=REQ)
    assert not err and "P22" in text and "missing series North, South, East, West" in text
    plain, _ = FT.check_deck(p, render=False)
    assert "P22" not in plain                                                   # backward compatible
    assert "data_files" in FT.TOOLS["check_deck"][2]


class Stub:
    def __init__(self):
        self.asked = []

    def chat(self, *, model, messages, **kw):
        self.asked.append(messages[0]["content"][:30])
        out = {"companies": [], "decision": "show revenue by quarter", "questions": ["What is the industry revenue "
               "growth rate in 2026?"]} if "which companies it is about" in messages[0]["content"] else {}
        return {"choices": [{"message": {"content": json.dumps(out)}}]}


def test_the_dossier_does_not_research_a_brief_about_the_attached_data(tmp_path):
    from engines.research import dossier as D
    called = []
    brief = f"{REQ}\n\n--- quarterly.csv ---\n" + (FIX / "quarterly.csv").read_text()
    d = D.build_dossier(brief, [], client=Stub(), models=["m"], out_dir=str(tmp_path),
                        search_fn=lambda *a, **k: called.append("search") or [],
                        fetch_fn=lambda *a, **k: called.append("fetch") or {})
    assert called == [] and d["facts"] == [] and d["questions"] == []
    assert d["skipped"] == "the brief is about the attached data; no company to research"
    assert d["identity"]["companies"] == [] and "no company" in (tmp_path / "dossier.json").read_text()
    assert re.search(r"Research dossier", (tmp_path / "dossier.md").read_text())
