"""Where the independent check shows: the terminal block, /grade, the receipt. No network."""
import io
import json

from meshpool_harness.agent import grader as GR
from tests.test_grader import Model, PLAN3, _env, agent, item, say, verdict, call  # noqa: F401


def test_terminal_prints_a_compact_block():
    from meshpool_harness.agent import tui as T
    out = io.StringIO()
    t = T.Terminal(out=out, width=80)
    res = GR.normalise(json.loads(verdict([item("a"), item("b", "not_met", "missing"), item("c", "unverified", "no tool")])))
    ev = GR.event(res, text="Independent check: 1 of 3 requirements met.", rounds=0, why="a plan", secs=3, usage={})
    ev["rework"] = True
    t.emit(ev)
    raw = out.getvalue()
    assert "Independent check: 1 of 3 requirements met." in raw and "not met" in raw and "unchecked" in raw
    assert "Sending these back" in raw and "\x1b" not in raw


def test_slash_grade_exists():
    from meshpool_harness.agent import tui as T
    assert any(c[0] == "grade" for c in T.COMMANDS) and hasattr(T.Session, "cmd_grade")


def test_receipt_records_the_review(tmp_path, monkeypatch):
    from meshpool_harness import receipts as RC
    from meshpool_harness.hrouter import outcomes as OUT
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    OUT._append({"ts": 1.0, "chat": "c", "secs": 20.0, "tokens_in": 1000, "tokens_out": 100, "calls": 3,
                 "review": {"verdict": "partial", "met": 4, "total": 6, "rounds": 1, "secs": 14, "tokens": 5000, "error": ""}})
    OUT._append({"ts": 2.0, "chat": "d", "secs": 20.0, "tokens_in": 10, "tokens_out": 1, "calls": 1})   # an old row: no review
    r = RC.receipt_for("c")
    assert r["review"]["met"] == 4 and "checked independently: 4 of 6 met, 1 fix round" in r["text"]
    assert RC.receipt_for("d")["review"] is None and "independently" not in RC.receipt_for("d")["text"]


def test_agent_receipt_line_mentions_the_check(tmp_path, monkeypatch):
    Model(monkeypatch, [call("write_file", {"path": "a.txt", "content": "1"}, "w"), PLAN3, say("Done.")],
          [verdict([item("a")])])
    a, ev = agent(tmp_path)
    a.send("build a.txt")
    assert "**Independent check:** all 1 requirement" in a.last_receipt
