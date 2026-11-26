"""The checks in plain words (Priya, Maya). "Checked the workbook: 27 of 36 checks passed" and "The checker flagged
three things" read as failure to a non-technical user, and "Checks sheet... a rule meant for financial models"
leaked a rule that does not apply to a room list. The line now says what was checked; it names only a real problem
in the file; rules that do not apply to the kind of file are not run or reported; the list stays in the details."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import checks as K  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    yield tmp_path
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)


def test_all_that_matters_passed_says_what_was_checked(home):
    """Priya's rooms workbook: style rules failed (typed numbers, a frozen header, no checks sheet). Nothing in the
    line about them, no counts; the details keep them."""
    K.begin("p")
    for name in ("rooms (edited).xlsx", "rooms summary.xlsx", "rooms summary.xlsx"):
        K.passed("office_structure", "the file opens and reads correctly", None, name)
        K.rules_ran("workbook_rules", "workbook rules", {f"X{i}": "pass" for i in range(9)}
                    | {"X10": "fail", "X1": "fail", "X12": "fail"}, name,
                    names={"X1": "Rooms: 16 typed-in numbers on a calc sheet", "X10": "Rooms: header panes are not "
                           "frozen.", "X12": "No checks sheet. Add one row per integrity check"})
    K.fact("workbook_totals", True, "rooms summary.xlsx")
    sm = K.summary("p")
    assert sm["line"] == "Checked the totals and formulas, and that the file opens cleanly.", sm["line"]
    assert not any(w in sm["line"] for w in ("of", "flag", "rule", "pass", "fail", "36", "27")), sm["line"]
    assert sm["failed"] and any("header panes" in d for d in sm["details"])      # the fold keeps the list


def test_a_real_problem_is_named_in_plain_words(home):
    K.begin("q")
    K.rules_ran("workbook_rules", "workbook rules", {"X1": "pass", "X5": "fail", "X10": "fail"}, "b.xlsx",
                names={"X5": "Summary!C4: evaluates to #DIV/0!", "X10": "Data: 3 cells where..."})
    K.fact("workbook_totals", True, "b.xlsx")
    sm = K.summary("q")
    assert sm["line"] == "Checked the totals and formulas. One thing to look at: some formulas show errors.", sm["line"]
    assert "#DIV/0!" not in sm["line"] and "X5" not in sm["line"]


def test_a_list_workbook_is_not_checked_against_model_rules(home):
    """A list, table or simple summary has no Checks tab and its data is typed in: the checks-sheet rule (X12) and
    the typed-numbers-on-a-calc-sheet rule (X1) do not run, and nothing about them is reported."""
    from engines.forge import validate_xlsx as VX
    wb = Workbook()
    ws = wb.active
    ws.title = "Rooms"
    ws.append(["Room", "Length (m)", "Width (m)", "Area (m2)"])
    for i, (n, a, b) in enumerate([("Reception", 6.5, 4.2), ("Kitchen", 4, 3.5)], 2):
        ws.append([n, a, b, f"=B{i}*C{i}"])
    p = home / "rooms.xlsx"
    wb.save(p)
    K.begin("t")
    r = VX.validate(str(p), do_recalc=False)
    assert r["rules"]["X12"] == "not_run" and r["rules"]["X1"] == "not_run", r["rules"]
    assert not [f for f in r["failures"] if f["rule"] in ("X1", "X12") and f["status"] == "fail"]
    assert not any("X12" in d or "checks sheet" in d.lower() for d in K.summary("t")["details"])
    model = Workbook()                                          # a model still gets them
    model.active.title = "Calc"
    model.active.append(["Revenue ($)", 100])
    model.save(home / "m.xlsx")
    rm = VX.validate(str(home / "m.xlsx"), do_recalc=False)
    assert rm["rules"]["X12"] == "fail" and rm["rules"]["X1"] == "fail", rm["rules"]


def test_the_engine_report_tells_the_agent_to_speak_plainly(home, tmp_path, monkeypatch):
    from meshpool_harness.agent import office_jobs as OJ
    monkeypatch.setattr("engines.forge.tools.qa_summary", lambda files: "- a.xlsx: forge rules failing: X10")
    jid = OJ.new_job("sheets", "a list", [], tmp_path / "proj", scope="chat-x")
    (tmp_path / "proj").mkdir()
    j = OJ.JOBS[jid]
    (j["out"] / "a.xlsx").write_bytes(b"x")
    j.update(result={"ok": True, "steps": [{"answer": "Built a.xlsx.", "artifacts": [str(j["out"] / "a.xlsx")]}]},
             done=1.0)
    try:
        text, _ = OJ.report(jid)
        assert "in plain words" in text and "rule ids" in text and "nothing else to copy, move or delete" in text
    finally:
        OJ.JOBS.pop(jid, None)
