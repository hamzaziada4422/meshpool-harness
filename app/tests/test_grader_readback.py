"""0.0.13: an effect the reviewer cannot read back is "unverified"; a rework request never asks for a second write."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core, grader as GR  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402

# What a reviewer said in the 0.0.12 CLI check: it wanted a read-back that no tool could give, and the agent
# then ran the idempotent write again.
RAW = {
    "verdict": "partial", "acceptance": "would_send_back",
    "items": [
        {"requirement": "Meeting booked for Tuesday 10:00", "status": "not_met",
         "evidence": "Cannot read back the booking: no read tool lists calendar entries, the create call only said ok"},
        {"requirement": "Summary file written", "status": "met", "evidence": "summary.md exists with 3 sections"},
    ],
    "blockers": ["The booking could not be read back and verified"],
    "fix": ["Call create_booking again for Tuesday 10:00 and check that it returns the same id",
            "Re-send the status update to be sure it arrived", "Add the missing totals row to summary.md"],
}


def test_unreadable_effect_is_unverified_not_a_defect():
    res = GR.normalise(json.loads(json.dumps(RAW)))
    st = {i["requirement"]: i["status"] for i in res["items"]}
    assert st["Meeting booked for Tuesday 10:00"] == "unverified"
    assert res["not_met"] == 0 and res["unverified"] == 1
    assert res["blockers"] == []
    assert not GR.needs_rework(res)


def test_rework_request_never_asks_for_a_second_write():
    raw = json.loads(json.dumps(RAW))
    raw["items"].append({"requirement": "Totals row in summary.md", "status": "not_met", "evidence": "no totals row"})
    res = GR.normalise(raw)
    assert GR.needs_rework(res)
    text = GR.inject_text(res)
    assert "create_booking again" not in text and "Re-send" not in text
    assert "totals row" in text.lower()
    assert "never run again a connected-app call that already succeeded" in text


def test_a_plain_missing_thing_is_still_not_met():
    res = GR.normalise({"verdict": "fail", "items": [{"requirement": "Report has a chart", "status": "not_met",
                                                      "evidence": "report.docx has no chart"}],
                        "fix": ["Re-run the test suite after adding the chart", "Add a bar chart"]})
    assert res["not_met"] == 1 and GR.needs_rework(res)
    assert "Add a bar chart" in res["fix"] and any("Re-run the test suite" in f for f in res["fix"])


def test_rubric_and_evidence_carry_the_rule():
    assert "READ-BACK RULE" in GR.SYSTEM and "never tell the agent to run" in GR.SYSTEM
    ev = GR.evidence({"requests": ["book it"], "final": "done", "actions": ['mcp__cal__create_booking:{"id": "1"}']}, [])
    assert "Actions the agent made in connected apps" in ev and "create_booking" in ev


@pytest.mark.parametrize("req,ev", [
    ("Report has a chart", "did not see the chart in report.docx"),
    ("Totals row in the workbook", "cannot see the totals row in sales.xlsx"),
    ("Figures match the CSV", "not verifiable from the CSV: the numbers differ"),
    ("Summary written in English", "could not verify it, the file is in German"),
])
def test_file_defects_are_never_downgraded_to_unverified(req, ev):
    res = GR.normalise({"verdict": "fail", "items": [{"requirement": req, "status": "not_met", "evidence": ev}]})
    assert res["not_met"] == 1 and res["unverified"] == 0 and GR.needs_rework(res)


@pytest.mark.parametrize("fix", [
    "Recreate the chart with axis labels", "Write the summary again in English", "Reapply the formatting to the header row",
    "Re-run the test suite after the fix", "Create the missing README",
])
def test_file_fixes_are_kept(fix):
    res = GR.normalise({"verdict": "fail", "items": [{"requirement": "x", "status": "not_met", "evidence": "y"}], "fix": [fix]})
    assert res["fix"] == [fix]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def test_the_gate_does_not_send_the_agent_back_for_an_impossible_read_back(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    a.messages.append({"role": "user", "content": "book Tuesday 10:00"})
    a.messages.append({"role": "assistant", "content": "Booked."})
    monkeypatch.setattr(GR, "enabled", lambda: True)
    monkeypatch.setattr(GR, "large", lambda info: "test")
    monkeypatch.setattr(GR, "run", lambda parent, info, cmds, **kw: dict(GR.normalise(json.loads(json.dumps(RAW))), usage={}, secs=0))
    g = {"msg0": 0, "t0": 0, "cmd0": 0, "after_plan": False, "grade_rounds": 0}
    turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
    again, final = a._grade_gate(g, turn, "Booked.")
    assert again is False
    assert not any(m.get("role") == "user" and "Independent review" in str(m.get("content")) for m in a.messages)
    assert "Could not check: Meeting booked" in final
