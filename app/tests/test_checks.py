"""Visible quality checks: only checks that ran are listed; with no recorder nothing changes."""
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from meshpool_harness import checks as K  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    yield tmp_path
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)


def test_no_recorder_means_nothing_is_written(home):
    assert K.passed("deck_rules", "design rules", 5) is False
    K.rules_ran("deck_rules", "design rules", {"P1": "pass"})
    assert not list((home / "checks").glob("*.jsonl")) if (home / "checks").exists() else True


def test_the_line_lists_what_passed_with_counts(home):
    K.begin("c1")
    K.passed("deck_rules", "design rules", 20, "a.pptx")
    K.passed("office_schema", "Office schema", None, "a.pptx")
    K.passed("sources", "sources", 14)
    s = K.summary("c1")
    assert s["line"] == ("Checked the slide layout and text, and that the file opens cleanly. "
                         "Checked 14 sources.") and s["failed"] == 0
    assert K.final_line("c1") == s["line"]
    ev = K.events_for("c1")
    assert [e["type"] for e in ev] == ["check"] * 3 and ev[0]["text"] == "20 checks on the deck passed"
    assert K.events_for("c1", since=2)[0]["kind"] == "sources"
    K.end("c1")
    assert K.current() == ""


def test_a_rule_that_did_not_run_is_never_listed(home):
    K.begin("c2")
    K.rules_ran("deck_rules", "design rules", {"P0": "pass", "P1": "pass", "P2": "not_run", "P3": "not_run",
                                               "P4": "warn", "P5": "fail"}, "d.pptx")
    s = K.summary("c2")
    assert s["line"] == "Checked the slide layout and text."         # P0, P1, P4 ran and passed; P5 failed
    assert s["failed"] == 1 and "3 of 4" not in s["line"]             # the count stays in the details fold
    K.begin("c3")
    K.rules_ran("deck_rules", "design rules", {"P0": "not_run"})
    assert K.summary("c3")["line"] == ""                                     # nothing ran: no line at all


def test_a_failed_check_is_not_in_the_checked_line(home):
    K.begin("c4")
    K.failed("sources", "sources", 3)
    s = K.summary("c4")
    assert s["line"] == "" and s["failed"] == 3 and s["items"][0]["ok"] is False


def test_the_same_deck_checked_again_counts_once(home):
    K.begin("c5")
    K.passed("deck_rules", "design rules", 15, "a.pptx")
    import time
    time.sleep(1.2)
    K.passed("deck_rules", "design rules", 18, "a.pptx")
    assert K.summary("c5")["line"] == "Checked the slide layout and text."
    assert [i["label"] for i in K.summary("c5")["items"]] == ["18 checks on the deck passed"]


def test_a_child_process_finds_the_run_through_the_pointer_file(home, monkeypatch):
    K.begin("c6")
    monkeypatch.delenv("MH_CHECK_RUN")                     # a tool server started before the turn has no env var
    assert K.current() == "c6" and K.passed("sources", "sources", 2)
    assert K.summary("c6")["line"] == "Checked 2 sources."


def test_workbook_rules_are_recorded_and_the_result_is_unchanged(home):
    from openpyxl import Workbook
    from engines.forge import validate_xlsx as VX
    wb = Workbook()
    wb.active["A1"] = 1
    p = home / "w.xlsx"
    wb.save(p)
    before = VX.validate(str(p), do_recalc=False)
    K.begin("w1")
    after = VX.validate(str(p), do_recalc=False)
    assert after["rules"] == before["rules"] and after["failures"] == before["failures"]
    ran = [s for s in after["rules"].values() if s != "not_run"]
    ok = sum(1 for s in ran if s in ("pass", "warn"))
    items = K.summary("w1")["items"]
    assert any(i["kind"] == "workbook_rules" for i in items if i["ok"]) == (ok > 0)
    if ok:
        assert K.summary("w1")["line"].startswith("Checked the totals and formulas")


def test_a_deck_build_records_its_rules(home):
    from pptx import Presentation
    from engines.forge import validate_pptx as VP
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Revenue grew 12 percent on strong demand"
    p = home / "d.pptx"
    prs.save(p)
    K.begin("d1")
    res = VP.validate(str(p), render=False)
    ran = [s for s in res["rules"].values() if s != "not_run"]
    ok = sum(1 for s in ran if s in ("pass", "warn"))
    bad = sum(1 for s in ran if s == "fail")
    sm = K.summary("d1")
    assert ok > 0 and sm["line"].startswith("Checked the slide layout and text")
    assert sm["failed"] == bad
    assert "P0" not in sm["line"] and not any(r == "not_run" for r in [])      # not_run rules are never counted
    nr = sum(1 for s in res["rules"].values() if s == "not_run")
    assert ok + bad + nr == len(res["rules"])


def test_ooxml_check_records_structure_when_schemas_are_absent(home):
    from pptx import Presentation
    from engines.forge import ooxml_check as OX
    p = home / "o.pptx"
    Presentation().save(p)
    K.begin("o1")
    r = OX.check(str(p))
    items = K.summary("o1")["items"]
    kind = "office_schema" if r["schema_validation"] == "run" else "office_structure"
    assert [i["kind"] for i in items] == [kind]                     # "Office schema" only when it really ran
    assert items[0]["ok"] == r["ok"]


def test_sources_are_recorded_by_the_research_verifier(home, tmp_path):
    import test_research_engine as T
    c = T.corpus(tmp_path)
    md = T.good_body(c)
    from engines.research import verify as V
    K.begin("r1")
    probs = V.check(md, c, min_sources=2)
    items = K.summary("r1")["items"]
    assert items and items[0]["kind"] == "sources" and items[0]["ok"] == (not probs)
    K.begin("r2")
    V.check("No numbers here, no citations.", c, min_sources=1)       # nothing cited: no sources were checked
    assert K.summary("r2")["items"] == []
