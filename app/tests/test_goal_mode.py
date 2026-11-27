"""Goal mode (agent/goal.py, 0.0.20): a goal and a bar, a rubric frozen before turn 1, an independent check that scores
the work in code, budgets and stop rules enforced in code, resume after a restart. Fake models only: the agent's model
call is replaced, nothing reaches a network."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from meshpool_harness import chats
from meshpool_harness.agent import compaction
from meshpool_harness.agent import core
from meshpool_harness.agent import goal as G
from meshpool_harness.agent import grader as GR
from meshpool_harness.ui import agent_api as A


def _st(**kw):
    st = G.freeze(G.new("ship the report", "chat", now=1000.0), now=1000.0)
    st.update(kw)
    return st


def _out(**kw):
    base = {"interrupted": False, "error": "", "acted": True, "ready": False, "needs": "", "blocker": "", "loop": False}
    base.update(kw)
    return base


def _check(st, status="met", **kw):
    items = [{"id": c["id"], "status": status, "evidence": "seen"} for c in st["rubric"]]
    sc = G.score(st["rubric"], {i["id"]: i["status"] for i in items}, st["bar"])
    c = {"items": items, "gates": [], "score": sc["score"], "must_open": sc["must_open"], "gates_ok": True}
    c.update(kw)
    return c


# ---------- the rubric: drafted, edited, frozen, hashed -----------------------------------------------------------------
def test_rubric_is_normalised_to_4_to_12_criteria_with_ids_weights_and_must_flags():
    short = G.normalise_rubric([{"text": "The deck has 10 slides", "weight": 9, "must": "yes"}], "a deck", "chat")
    assert 4 <= len(short) <= 12 and [c["id"] for c in short] == ["c1", "c2", "c3", "c4"]
    assert short[0] == {"text": "The deck has 10 slides", "check": "", "weight": 5, "must": True, "needs_user": False,
                        "id": "c1"}
    many = G.normalise_rubric([f"criterion {i}" for i in range(20)], "x")
    assert len(many) == 12 and many[-1]["id"] == "c12"
    dup = G.normalise_rubric(["A", "a", "B", "C", "D"], "x")
    assert [c["text"] for c in dup] == ["A", "B", "C", "D"]


def test_frozen_rubric_is_hashed_and_cannot_be_changed(tmp_path):
    st = G.new("make every test pass", "code")
    G.review(st, [{"text": "All tests pass", "must": True}, "No lint errors", "Docs updated", "No debug prints"])
    G.edit(st, [{"text": "All tests pass", "must": True, "weight": 3}, "No lint errors", "Docs updated", "No prints"])
    assert st["status"] == "review" and not st["rubric_hash"]
    G.freeze(st)
    h = st["rubric_hash"]
    assert st["status"] == "working" and len(h) == 64 and h == G.rubric_hash(st["rubric"])
    assert G.rubric_hash(json.loads(json.dumps(st["rubric"]))) == h            # the same criteria: the same hash
    other = json.loads(json.dumps(st["rubric"]))
    other[0]["weight"] = 1
    assert G.rubric_hash(other) != h                                          # any change: another hash
    with pytest.raises(ValueError):
        G.edit(st, ["anything"])                                              # frozen: edits are refused
    G.freeze(st, ["replaced"])                                                # a second freeze changes nothing
    assert st["rubric_hash"] == h
    G.save(tmp_path, st)
    assert not G.load(tmp_path).get("tampered")
    raw = json.loads(G.path(tmp_path).read_text())
    raw["rubric"][0]["text"] = "Tests can fail"                               # changed outside meshpool
    G.path(tmp_path).write_text(json.dumps(raw))
    loaded = G.load(tmp_path)
    assert loaded["tampered"] is True
    assert G.decide(loaded, _out(ready=True), _check(loaded))["action"] == "paused"


def test_the_working_agent_sees_the_rubric_and_the_findings_but_not_how_to_change_them():
    st = _st()
    st["findings"] = {"items": [{"id": "c2", "text": st["rubric"][1]["text"], "status": "not_met",
                                 "evidence": "deck.pptx fails P0"}], "gates": [{"name": "check_document deck.pptx",
                                                                                "ok": False, "tail": "zip"}],
                      "fix": ["Rebuild the deck"]}
    st["history"] = [{"score": 6.0}]
    note = G.note(st)
    for c in st["rubric"]:
        assert c["text"] in note
    assert "6/10" in note and "deck.pptx fails P0" in note and "gate failed: check_document" in note
    assert "Rebuild the deck" in note and "cannot change the rubric" in note and "GOAL: READY" in note


# ---------- the score: computed in code -----------------------------------------------------------------------------------
def test_score_is_ten_times_the_weighted_share_met():
    rub = [{"id": "c1", "text": "a", "weight": 3, "must": False}, {"id": "c2", "text": "b", "weight": 1, "must": False},
           {"id": "c3", "text": "c", "weight": 1, "must": False}, {"id": "c4", "text": "d", "weight": 5, "must": False}]
    s = G.score(rub, {"c1": "met", "c2": "met", "c3": "not_met", "c4": "met"})
    assert s["score"] == 9.0 and s["met"] == ["c1", "c2", "c4"] and s["not_met"] == ["c3"]
    assert G.score(rub, {k: "met" for k in ("c1", "c2", "c3", "c4")})["score"] == 10.0
    assert G.score(rub, {})["score"] == 0.0


def test_could_not_check_counts_as_not_met_and_a_missing_answer_too():
    rub = [{"id": f"c{i}", "text": str(i), "weight": 1} for i in range(1, 5)]
    s = G.score(rub, {"c1": "met", "c2": "met", "c3": "unverified"})        # c4: no answer at all
    assert s["score"] == 5.0 and s["unverified"] == ["c3", "c4"]
    assert G.score(rub, {"c1": "met", "c2": "met", "c3": "met", "c4": "maybe"})["score"] == 7.5


def test_an_unmet_must_have_keeps_the_score_below_the_bar():
    rub = [{"id": "c1", "text": "core", "weight": 1, "must": True}] + [
        {"id": f"c{i}", "text": str(i), "weight": 5} for i in range(2, 6)]
    s = G.score(rub, {"c1": "not_met", "c2": "met", "c3": "met", "c4": "met", "c5": "met"}, bar=9)
    assert s["raw"] == 9.5 and s["score"] == 8.0 and s["must_open"] == ["c1"]
    st = _st(bar=9.0)
    assert not G.met_bar({"score": 9.5, "gates_ok": True, "must_open": ["c1"]}, 9)
    assert G.met_bar({"score": 9.0, "gates_ok": True, "must_open": []}, 9)
    assert not G.met_bar({"score": 10.0, "gates_ok": False, "must_open": []}, 9)      # a failing gate blocks "met"
    assert not G.met_bar({"score": 10.0, "gates_ok": True, "error": "x"}, 9)
    assert G.below(10) == 9.0 and G.fmt(9.0) == "9" and G.fmt(8.5) == "8.5" and st["bar"] == 9.0


# ---------- decide(): every stop rule ---------------------------------------------------------------------------------------
def test_decide_met_needs_the_score_and_every_gate():
    st = _st()
    assert G.decide(st, _out(ready=True), _check(st))["action"] == "met"
    bad_gate = _check(st, gates=[{"name": "npm test", "ok": False, "code": 1}], gates_ok=False)
    assert G.decide(st, _out(ready=True), bad_gate)["action"] == "continue"
    assert G.decide(st, _out(ready=True), _check(st, "not_met"))["action"] == "continue"


@pytest.mark.parametrize("field,value,limit", [("spent", 2.0, {"usd": 2.0}), ("elapsed", 7200.0, {"hours": 2.0}),
                                               ("turns", 30, {"turns": 30}), ("checks", 10, {"checks": 10})])
def test_decide_stops_at_each_budget(field, value, limit):
    st = _st()
    st["budgets"].update(limit)
    st[field] = value
    d = G.decide(st, _out(), _check(st, "not_met"))
    assert d["action"] == "stopped" and d["reason"] == "budget" and d["budget"] == list(limit)[0]
    assert G.decide(st, _out(), _check(st, "met"))["action"] == "met"          # the last check can still meet the bar
    G.apply(st, d)
    assert G.report(st, _check(st, "not_met")).startswith("Stopped at the budget: 0/10.")


def test_a_spending_limit_stop_counts_as_the_money_budget():
    st = _st()
    d = G.decide(st, _out(error="budget"), _check(st, "not_met"))
    assert d["action"] == "stopped" and d["budget"] == "usd"


def test_decide_stuck_rules():
    st = _st()
    assert G.decide(st, _out(loop=True))["reason"].startswith("stuck: it kept repeating")
    G.record_turn(st, _out(blocker="npm is not installed (exit 127)"))
    G.record_turn(st, _out(blocker="npm is not installed (exit 127)"))
    assert G.decide(st, _out())["action"] == "continue"
    G.record_turn(st, _out(blocker="npm is not installed (exit 127)"))
    d = G.decide(st, _out())
    assert d["action"] == "stopped" and "same blocker 3 turns" in d["reason"]
    st = _st()
    G.record_turn(st, _out(acted=False))
    assert G.decide(st, _out(acted=False))["action"] == "continue"
    G.record_turn(st, _out(acted=False))
    assert "2 turns in a row without doing anything" in G.decide(st, _out(acted=False))["reason"]


def test_no_score_rise_across_3_checks_with_no_file_change_is_stuck_but_a_change_is_not():
    st = _st()
    for sc in (6.0, 6.0):
        G.record_check(st, {"score": sc})
    assert not G.flat(st)
    G.record_check(st, {"score": 5.0})
    assert G.flat(st)
    assert "no score rise across 3 checks" in G.decide(st, _out(), _check(st, "not_met"))["reason"]
    st = _st()
    G.record_check(st, {"score": 6.0})
    G.record_turn(st, _out(changed=True))                                     # a file changed between the checks
    G.record_check(st, {"score": 6.0})
    G.record_check(st, {"score": 6.0})
    assert not G.flat(st)
    st = _st()
    for sc in (4.0, 6.0, 6.0):
        G.record_check(st, {"score": sc})
    assert not G.flat(st)                                                     # the score rose within the window


def test_decide_pauses_for_the_user_and_on_errors_only_the_user_can_fix():
    st = _st()
    d = G.decide(st, _out(needs="Which of the two logos should it use?"))
    assert d["action"] == "paused" and d["question"].startswith("Which of the two logos")
    d = G.decide(st, _out(ready=True), _check(st, "not_met", needs_user="Please attach the 2025 figures."))
    assert d["action"] == "paused" and "2025 figures" in d["question"]
    assert G.decide(st, _out(error="auth"))["reason"] == "the model's key was refused"
    assert G.decide(st, _out(error="quota"))["reason"].startswith("the key's quota")
    assert G.decide(st, _out(error="model"))["reason"] == "the model is not available"
    assert G.decide(st, _out(error="net"))["action"] == "wait"                # an outage: waited out, never a stop
    assert G.decide(st, _out(error="other"))["action"] == "paused"
    assert G.decide(st, _out(interrupted=True))["reason"] == "you stopped it"
    assert G.error_kind("The endpoint rejected the API key. Check it in Settings") == "auth"
    assert G.error_kind("Your key's budget or quota is used up") == "quota"
    assert G.error_kind("The endpoint does not offer a model named x") == "model"
    assert G.error_kind("Couldn't reach the model (HTTP 0: timed out)") == "net"
    assert G.error_kind("Spending limit reached for this task") == "budget"


def test_parse_line_reads_ready_and_the_old_done_as_ready():
    assert G.parse_line("Fixed it.\nGOAL: READY") == ("READY", "")
    assert G.parse_line("Fixed it.\nGOAL: DONE") == ("READY", "")
    assert G.parse_line("x\nGOAL: CONTINUE with the charts") == ("CONTINUE", "with the charts")
    assert G.parse_line("GOAL: NEEDS YOU: which folder?") == ("NEEDS", "which folder?")
    assert G.parse_line("GOAL: BLOCKED: no network") == ("BLOCKED", "no network")
    assert G.parse_line("no line") == ("", "")
    assert G.strip_line("Done.\nGOAL: READY") == "Done."


def test_checks_run_when_ready_every_5_goal_turns_and_when_a_budget_runs_out():
    st = _st()
    assert G.check_due(st, _out(ready=True)) == "the agent said it is ready"
    for _ in range(4):
        G.record_turn(st, _out())
    assert G.check_due(st, _out()) == ""
    G.record_turn(st, _out())
    assert G.check_due(st, _out()) == "every 5 goal turns"
    G.record_check(st, {"score": 3.0})
    assert G.check_due(st, _out()) == ""
    st["budgets"]["turns"] = 6
    G.record_turn(st, _out())
    assert G.check_due(st, _out()) == "the budget ran out"


# ---------- the 80 percent converge note --------------------------------------------------------------------------------
def test_the_converge_note_comes_once_at_80_percent():
    st = _st()
    st["budgets"]["turns"] = 10
    st["turns"] = 7
    assert not G.converge_due(st) and "Converge" not in G.note(st)
    st["turns"] = 8
    assert G.converge_due(st)
    assert "Goal budget is 80 percent used (the turn limit). Converge" in G.note(st)
    st["converged"] = True
    assert not G.converge_due(st) and "Converge" not in G.note(st)
    st["converged"], st["turns"] = False, 10
    assert not G.converge_due(st)                                            # at 100 percent it stops instead


def test_session_sends_the_converge_note_on_one_turn_only(monkeypatch):
    s = A.Session.__new__(A.Session)
    s.id, s.goal, s.events, s.lock = "conv-test", "x", [], threading.Lock()
    s.emit = lambda ev: s.events.append(ev)
    s.gs = _st()
    s.gs["budgets"]["turns"], s.gs["turns"] = 10, 8
    first, second = s._goal_body(), s._goal_body()
    assert "Converge" in first and "Converge" not in second and s.gs["converged"] is True


# ---------- the state file: atomic, counters kept -------------------------------------------------------------------------
def test_goal_json_is_written_atomically(tmp_path, monkeypatch):
    st = _st(turns=4, checks=2, spent=0.37)
    seen = []
    orig = os.replace

    def spy(a, b):
        seen.append((Path(a).name, Path(b).name))
        assert Path(a).read_text(encoding="utf-8").startswith("{")          # the whole file is written before the swap
        return orig(a, b)
    monkeypatch.setattr(os, "replace", spy)
    assert G.save(tmp_path, st)
    assert seen and seen[0][1] == "goal.json" and seen[0][0].endswith(".tmp")
    assert [p.name for p in tmp_path.iterdir()] == ["goal.json"]              # no temporary file left behind
    back = G.load(tmp_path)
    assert (back["turns"], back["checks"], back["spent"]) == (4, 2, 0.37)
    assert not G.save(tmp_path / "missing", st)                               # no folder: nothing written

    def broken(a, b):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", broken)
    st["turns"] = 99
    assert G.save(tmp_path, st) is False
    assert G.load(tmp_path)["turns"] == 4 and [p.name for p in tmp_path.iterdir()] == ["goal.json"]


def test_budgets_read_settings_and_defaults_in_one_place(monkeypatch):
    monkeypatch.setenv("MH_BUDGET_USD", "3")
    monkeypatch.setenv("MH_GOAL_TURNS", "12")
    b = G.budgets()
    assert b["usd"] == 3.0 and b["turns"] == 12 and b["checks"] == G.DEFAULTS["checks"] == 10
    monkeypatch.setenv("MH_GOAL_USD", "1.5")
    assert G.budgets()["usd"] == 1.5 and G.budgets(turns=5)["turns"] == 5
    assert G.DEFAULTS["turns"] == 30 and G.DEFAULTS["hours"] == 4 and G.DEFAULTS["check_every"] == 5


# ---------- compaction keeps the goal ------------------------------------------------------------------------------------------
def test_compaction_keeps_the_goal_the_rubric_and_the_findings_word_for_word(monkeypatch):
    monkeypatch.setenv("MH_COMPACT_MODEL", "0")
    st = _st()
    G.record_check(st, {"score": 7.0, "items": [{"id": "c3", "text": st["rubric"][2]["text"], "status": "not_met"}]})
    keep = G.brief(st)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "the task"}]
    for i in range(30):
        msgs += [{"role": "assistant", "content": f"step {i}"}, {"role": "user", "content": f"ok {i}"}]
    r = compaction.compact_history(msgs, keep=keep)
    assert r and r["removed"] > 0
    note = msgs[2]["content"]
    assert keep in note and "ship the report" in note and st["rubric_hash"][:12] in note
    for c in st["rubric"]:
        assert c["text"] in note
    assert "Last check: 7/10" in note


def test_the_agent_hands_its_goal_brief_to_the_compaction(tmp_path, monkeypatch):
    seen = {}

    def fake(messages, **kw):
        seen.update(kw)
        return None
    monkeypatch.setattr(compaction, "compact_history", fake)
    a = core.Agent(str(tmp_path))
    st = _st()
    a.goal_brief = lambda: G.brief(st)
    a.compact_now()
    assert seen["keep"] == G.brief(st) and "ship the report" in seen["keep"]


# ---------- gates: Code and Chat -------------------------------------------------------------------------------------------
def _py_project(root: Path, passing: bool):
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text(f"def test_x():\n    assert {passing}\n")
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    py = venv / "python"                                      # the project's own Python: here, the one that has pytest
    py.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
    py.chmod(0o755)


def test_code_gates_are_found_from_the_project_files(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest", "build": "vite build",
                                                                   "lint": "eslint ."}}))
    (tmp_path / "Makefile").write_text("lint:\n\techo\ntest:\n\techo\n")
    (tmp_path / "go.mod").write_text("module x\n")
    cmds = [g["cmd"] for g in G.find_code_gates(tmp_path)]
    assert ["npm", "test"] in cmds and ["npm", "run", "build"] in cmds and ["npm", "run", "lint"] in cmds
    assert ["make", "lint"] in cmds and ["make", "test"] in cmds and ["go", "test", "./..."] in cmds
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "echo \"Error: no test specified\""}}))
    assert ["npm", "test"] not in [g["cmd"] for g in G.find_code_gates(tmp_path)]
    assert G.find_code_gates(tmp_path / "nothing") == []


@pytest.mark.skipif(sys.platform == "win32", reason="the project venv layout here is POSIX")
def test_code_gates_judge_by_exit_codes(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    _py_project(good, True)
    _py_project(bad, False)
    g = G.run_code_gates(good, 120)
    assert len(g) == 1 and g[0]["kind"] == "test" and g[0]["ok"] is True and g[0]["code"] == 0
    b = G.run_code_gates(bad, 120)
    assert b[0]["ok"] is False and b[0]["code"] == 1 and "failed" in b[0]["tail"]
    assert G.gates_ok(g) and not G.gates_ok(b)
    assert G.gates_ok([{"name": "make test", "ok": None, "why": "make is not installed"}])   # not run: not a fail


def test_code_gates_run_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-secret-value-123")
    (tmp_path / "Makefile").write_text("test:\n\t@env > env.txt\n")
    if not __import__("shutil").which("make"):
        pytest.skip("make is not installed here")
    G.run_code_gates(tmp_path, 60)
    assert "sk-secret-value-123" not in (tmp_path / "env.txt").read_text()


def test_chat_gates_run_the_office_checks(tmp_path):
    import docx
    d = docx.Document()
    d.add_paragraph("Quarterly report")
    d.save(tmp_path / "report.docx")
    (tmp_path / "broken.docx").write_bytes(b"this is not a zip file")
    files = G.office_files(tmp_path, 0)
    assert set(files) == {"report.docx", "broken.docx"}
    gates = {g["name"]: g for g in G.chat_gates(tmp_path, files, render=False)}
    assert gates["check_document report.docx"]["ok"] is True
    assert gates["check_document broken.docx"]["ok"] is False
    assert not G.gates_ok(list(gates.values()))
    assert G.office_files(tmp_path, time.time() + 3600) == []                 # only what changed since the goal began


def test_chat_gates_check_a_workbook_and_a_deck(tmp_path):
    import openpyxl
    import pptx
    wb = openpyxl.Workbook()
    wb.active["A1"], wb.active["A2"], wb.active["A3"] = 1, 2, "=A1+A2"
    wb.save(tmp_path / "model.xlsx")
    pr = pptx.Presentation()
    pr.slides.add_slide(pr.slide_layouts[1]).shapes.title.text = "Revenue grew"
    pr.save(tmp_path / "deck.pptx")
    gates = G.chat_gates(tmp_path, ["model.xlsx", "deck.pptx"], render=False)
    names = [g["name"] for g in gates]
    assert "check_workbook model.xlsx" in names and "check_deck deck.pptx" in names
    assert all(g["ok"] in (True, False, None) for g in gates)


# ---------- the grader: drafts the rubric before the work, scores against it after ----------------------------------------
class _FakeModel:
    """The model behind every agent: the rubric writer, the goal reviewer and the working agent, told apart by their
    system prompts. The working agent writes notes.md (v1, then v2) and says it is ready."""

    def __init__(self, proj: Path):
        self.proj, self.calls, self.tools_seen = proj, [], []

    def __call__(self, agent, turn):
        sysmsg = str(agent.messages[0].get("content") or "")
        self.calls.append(sysmsg[:40])
        if sysmsg.startswith("You write the rubric"):
            body = {"criteria": [{"text": "notes.md exists", "check": "open it", "weight": 2, "must": True},
                                 {"text": "notes.md says version 2", "check": "read it", "weight": 3, "must": True},
                                 {"text": "notes.md has a title", "check": "first line", "weight": 1},
                                 {"text": "No placeholder text", "check": "read it", "weight": 1}]}
            return {"role": "assistant", "content": json.dumps(body)}, False
        if sysmsg.startswith("You are the INDEPENDENT REVIEWER of meshpool, in goal mode"):
            self.tools_seen.append(not getattr(agent, "_no_tools", False))
            text = (self.proj / "notes.md").read_text() if (self.proj / "notes.md").exists() else ""
            v2 = "version 2" in text
            items = [{"id": "c1", "status": "met" if text else "not_met", "evidence": "notes.md"},
                     {"id": "c2", "status": "met" if v2 else "not_met", "evidence": "it says " + text.strip()[:40]},
                     {"id": "c3", "status": "met", "evidence": "# Notes"},
                     {"id": "c4", "status": "met" if v2 else "unverified", "evidence": "read it" if v2 else ""}]
            return {"role": "assistant", "content": json.dumps({"items": items, "fix": [] if v2 else
                                                                 ["Write version 2 into notes.md"]})}, False
        n = sum(1 for c in self.calls if not c.startswith(("You write", "You are the INDEPENDENT")))
        (self.proj / "notes.md").write_text(f"# Notes\nversion {min(n, 2)}\n")
        return {"role": "assistant", "content": f"Wrote notes.md version {min(n, 2)}.\nGOAL: READY"}, False


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MH_KEEP_AWAKE", "0")
    sessions = tmp_path / "runs" / "sessions"
    sessions.mkdir(parents=True)
    for mod in (core, A, chats):
        monkeypatch.setattr(mod, "SESSIONS", sessions)
    proj = tmp_path / "proj"
    proj.mkdir()
    fake = _FakeModel(proj)
    monkeypatch.setattr(core.Agent, "_chat", lambda self, turn: fake(self, turn))
    made = []

    def make():
        v = chats.create({"workdir": str(proj), "no_mcp": True})
        s = chats.get(v["id"], no_mcp=True)
        made.append(s)
        return s
    make.fake, make.proj, make.sessions = fake, proj, sessions
    yield make
    for s in made:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


def _until(fn, secs=30, what="the goal"):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return
        time.sleep(0.05)
    raise AssertionError(f"{what} did not happen in {secs} s")


def test_draft_rubric_and_goal_check_use_the_real_grader_with_a_fake_model(chat, tmp_path):
    s = chat()
    out = GR.draft_rubric(s.agent, "notes.md says version 2", "chat", 10)
    assert out["error"] == "" and len(out["criteria"]) == 4
    rub = G.normalise_rubric(out["criteria"], "x", "chat")
    (chat.proj / "notes.md").write_text("# Notes\nversion 1\n")
    res = GR.run(s.agent, {"requests": ["x"], "final": "done", "files": ["notes.md"], "root": str(chat.proj)}, [],
                 rubric=rub, gates=[{"name": "check_document a.docx", "ok": True}])
    assert [i["status"] for i in res["items"]] == ["met", "not_met", "met", "unverified"]
    assert res["fix"] == ["Write version 2 into notes.md"]
    assert chat.fake.tools_seen == [True]                     # the goal check keeps its read-only tools (never light)
    assert G.score(rub, {i["id"]: i["status"] for i in res["items"]})["score"] == 4.3


def test_normalise_goal_fills_every_criterion_and_distrusts_a_bare_met():
    rub = G.normalise_rubric(["a", "b", "c", "d"], "x")
    r = GR.normalise_goal({"items": [{"id": "c1", "status": "met", "evidence": "seen"}, {"id": "c2", "status": "met"},
                                     {"id": "zz", "status": "met", "evidence": "x"}],
                           "needs_user": "Which year?"}, rub)
    assert [i["status"] for i in r["items"]] == ["met", "unverified", "unverified", "unverified"]
    assert r["needs_user"] == "Which year?" and GR.normalise_goal({"items": []}, rub) is None


def test_the_per_turn_rework_is_off_while_a_goal_is_active(tmp_path, monkeypatch):
    a = core.Agent(str(tmp_path))
    called = []
    monkeypatch.setattr(GR, "run", lambda *x, **k: called.append(1) or {"error": "x"})
    a.goal_active = True
    assert a._grade_gate({"msg0": 0}, {}, "a long final answer " * 200) == (False, "a long final answer " * 200)
    assert called == []


def test_goal_turns_are_deep_work():
    from meshpool_harness import pace
    assert pace.classify("what time is it?") == "quick"
    assert pace.classify("what time is it?", goal=True) == "deep"


# ---------- the whole loop in a real session -----------------------------------------------------------------------------
def test_a_goal_goes_from_rubric_to_work_to_a_check_below_the_bar_to_more_work_to_met(chat):
    s = chat()
    assert s._goal_mode() == "chat"
    s.set_goal("notes.md must say version 2")
    _until(lambda: s.gs and s.gs["status"] == "review", what="the rubric draft")
    assert [c["text"] for c in s.gs["rubric"]][:2] == ["notes.md exists", "notes.md says version 2"]
    s.goal_action("start")
    _until(lambda: s.gs["status"] in ("met", "stopped", "paused"), 60)
    assert s.gs["status"] == "met", s.gs.get("report")
    scores = [h["score"] for h in s.gs["history"]]
    assert scores[0] < 10 and scores[-1] == 10 and s.gs["checks"] == 2 and s.gs["turns"] == 2
    checks = [e for e in s.events if e.get("type") == "goal_check"]
    assert len(checks) == 2 and "Below the bar of 10" in checks[0]["text"] and checks[1]["met"]
    report = [e for e in s.events if e.get("goal_report")][-1]["text"]
    assert report.startswith("Met: 10/10 after 2 checks.")
    assert s.goal == "" and A.read_meta(s.id).get("goal") == ""
    users = [e["text"] for e in s.events if e.get("type") == "user"]
    assert users[0] == "notes.md must say version 2" and users[1] == A.GOAL_NEXT
    _until(lambda: s.state == "idle")
    s._persist()
    kept = A._transcript(s.id)["events"]
    assert [e["type"] for e in kept].count("goal_check") == 2                 # kept across reloads (_KEEP)
    assert not any(e["type"] == "goal" for e in kept)                         # the live pings are not saved
    v = s.view(0)
    assert v["goal_state"]["status"] == "met" and v["goal_state"]["history"][-1]["score"] == 10
    on_disk = G.load(chat.sessions / s.id)
    assert on_disk["status"] == "met" and on_disk["rubric_hash"] == G.rubric_hash(on_disk["rubric"])


def test_the_rubric_freezes_by_itself_after_its_countdown(chat):
    s = chat()
    s.goal_checker = lambda s_, st, why: {"items": [{"id": c["id"], "status": "met", "evidence": "x"}
                                                    for c in st["rubric"]], "gates": []}
    orig = G.review
    G.review = lambda st, rub, freeze_s=None, now=None: orig(st, rub, 0.3, now)
    try:
        s.set_goal("notes.md must say version 2")
        _until(lambda: s.gs and s.gs.get("frozen_at"), 20, "the freeze")
    finally:
        G.review = orig
    assert s.gs["rubric_hash"]
    _until(lambda: s.gs["status"] == "met", 30)


def test_a_message_stop_or_clearing_the_goal_takes_effect_mid_turn(chat, monkeypatch):
    slow = threading.Event()

    def busy(self, turn):                                     # a working agent that keeps going until stopped
        sysmsg = str(self.messages[0].get("content") or "")
        if not sysmsg.startswith(("You write", "You are the INDEPENDENT")):
            slow.set()
            time.sleep(0.15)
            n = len(self.messages)
            return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "type": "function", "function": {
                "name": "glob", "arguments": json.dumps({"pattern": "*.md"})}}]}, False
        return chat.fake(self, turn)
    monkeypatch.setattr(core.Agent, "_chat", busy)
    monkeypatch.setenv("MH_REPEAT_STOP", "0")
    s = chat()
    s.goal_drafter = lambda s_, st: []
    s.set_goal("keep looking")
    s.goal_action("start")
    _until(slow.is_set, what="the goal turn")
    s.send("also look at the docs folder")                    # a message joins the running turn at once
    _until(lambda: any(e.get("type") == "user" and e.get("steer") for e in s.events), what="the steer")
    s.stop()                                                  # Stop: the turn ends and the goal pauses
    _until(lambda: s.state == "idle", what="the stop")
    assert s.gs["status"] == "paused" and s.gs["reason"] == "you stopped it" and s.gs["turns"] == 1
    slow.clear()
    s.goal_action("resume")
    _until(slow.is_set, what="the resumed turn")
    assert s.gs["status"] == "working" and s.gs["turns"] == 1                 # counters kept
    s.set_goal("")                                            # clearing the goal stops the goal turn too
    _until(lambda: s.state == "idle", what="the clear")
    assert s.gs["status"] == "cleared" and s.goal == ""
    assert any(e.get("type") == "interrupted" for e in s.events)


def test_stop_during_the_check_pauses_the_goal(chat):
    s = chat()
    started = threading.Event()

    def slow_check(s_, st, why):
        started.set()
        end = time.time() + 10
        while time.time() < end and not s_._stop_req:
            time.sleep(0.05)
        raise core.Interrupted()
    s.goal_checker = slow_check
    s.goal_drafter = lambda s_, st: []
    s.set_goal("notes.md must say version 2")
    s.goal_action("start")
    _until(started.is_set, what="the check")
    assert s.gs["status"] == "checking"
    s.stop()
    _until(lambda: s.gs["status"] == "paused", what="the pause")
    assert s.gs["reason"] == "you stopped it"


def test_resume_after_a_restart_keeps_the_counters_and_counts_down(chat):
    s = chat()
    st = _st(turns=7, checks=3, spent=0.5, elapsed=900.0)
    st["budgets"]["resume_s"] = 0.6
    st["history"] = [{"n": 1, "score": 4.0}, {"n": 2, "score": 6.0}, {"n": 3, "score": 7.0}]
    G.save(chat.sessions / s.id, st)
    A.write_meta(s.id, goal=st["goal"])
    sid = s.id
    s.close()
    with A.LOCK:
        A.LIVE.pop(sid, None)
    seen = []
    orig = A.Session._goal_kick
    A.Session._goal_kick = lambda self: seen.append(self.gs["turns"]) or False
    try:
        s2 = chats.get(sid, no_mcp=True)
        g = s2.view(0)["goal_state"]
        assert g["status"] == "working" and g["resume_at"] > time.time()
        assert (s2.gs["turns"], s2.gs["checks"], s2.gs["spent"], s2.gs["elapsed"]) == (7, 3, 0.5, 900.0)
        assert any("resumes by itself in 1 s" in (e.get("text") or "") for e in s2.events)
        _until(lambda: seen, 10, "the automatic resume")
        assert seen == [7] and s2.gs["resume_at"] == 0.0
        s2.close()
    finally:
        A.Session._goal_kick = orig


def test_the_restart_countdown_can_be_cancelled(chat):
    s = chat()
    st = _st(turns=2)
    st["budgets"]["resume_s"] = 1.0
    G.save(chat.sessions / s.id, st)
    sid = s.id
    s.close()
    with A.LOCK:
        A.LIVE.pop(sid, None)
    seen = []
    orig = A.Session._goal_kick
    A.Session._goal_kick = lambda self: seen.append(1) or False
    try:
        s2 = chats.get(sid, no_mcp=True)
        s2.goal_action("cancel_resume")
        time.sleep(1.5)
        assert seen == [] and s2.gs["status"] == "paused" and "cancelled" in s2.gs["reason"]
        assert G.load(chat.sessions / sid)["turns"] == 2
        s2.close()
    finally:
        A.Session._goal_kick = orig


def test_resume_goals_opens_running_goals_after_a_restart(chat, monkeypatch):
    s = chat()
    G.save(chat.sessions / s.id, _st())
    other = chat()
    G.save(chat.sessions / other.id, _st(status="met"))
    opened = []
    monkeypatch.setattr(A, "get", lambda sid, no_mcp=False: opened.append(sid))
    assert A.resume_goals() == [s.id] and opened == [s.id]
    monkeypatch.setenv("MH_GOAL_RESUME", "0")
    assert A.resume_goals() == []


def test_the_goal_holds_the_computer_awake_for_its_whole_run(chat, monkeypatch):
    from meshpool_harness import awake
    calls = []
    monkeypatch.setattr(awake, "hold", lambda: calls.append("hold"))
    monkeypatch.setattr(awake, "release", lambda: calls.append("release"))
    s = chat()
    s.goal_drafter = lambda s_, st: []
    s.goal_checker = lambda s_, st, why: {"items": [{"id": c["id"], "status": "met", "evidence": "x"}
                                                    for c in st["rubric"]], "gates": []}
    s.set_goal("notes.md must say version 2")
    s.goal_action("start")
    _until(lambda: s.gs["status"] == "met")
    _until(lambda: s.state == "idle")
    assert calls[0] == "hold" and calls.count("hold") == calls.count("release")
    assert not getattr(s, "_goal_awake", False)


def test_the_time_budget_turns_tools_off_mid_turn(tmp_path, monkeypatch):
    seen = []

    def fake(self, turn):
        seen.append(bool(getattr(self, "_no_tools", False)))
        if seen[-1]:
            return {"role": "assistant", "content": "Done what I could.\nGOAL: CONTINUE"}, False
        n = len(self.messages)
        return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "type": "function", "function": {
            "name": "glob", "arguments": json.dumps({"pattern": "*"})}}]}, False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    a = core.Agent(str(tmp_path), mode="auto")
    a.wrap_deadline, a.wrap_text = time.time() - 1, "[meshpool] The goal's time budget is used up."
    reply = a.send("keep going")
    assert seen[:2] == [False, True] and "Done what I could" in reply
    assert any("time budget is used up" in str(m.get("content")) for m in a.messages if m["role"] == "user")


def test_an_older_goal_with_only_its_text_keeps_working(chat):
    s = chat()
    s.gs = None
    s.goal = "ship it"
    st = s._goal_ensure()
    assert st["status"] == "working" and st["rubric_hash"] and len(st["rubric"]) == 4
    assert G.load(chat.sessions / s.id)["goal"] == "ship it"


# ---------- words: the report, the terminal, away ----------------------------------------------------------------------
def test_report_wording_follows_grader_report():
    st = _st()
    st["rubric"][1]["text"] = "Charts have sources"
    check = _check(st, "met")
    check["items"][1].update(status="not_met", evidence="slide 4 has none")
    check["items"][2]["status"] = "unverified"
    check["gates"] = [{"name": "check_deck deck.pptx", "ok": False}]
    G.record_check(st, check)
    G.apply(st, {"action": "stopped", "reason": "budget", "budget": "turns"})
    text = G.report(st, check)
    assert text.splitlines()[0] == f"Stopped at the budget: {G.fmt(check['score'])}/10. It reached the turn limit."
    assert "- Not done: Charts have sources (slide 4 has none)" in text
    assert "- Could not check: " + st["rubric"][2]["text"] in text and "- Gate failed: check_deck deck.pptx" in text
    st2 = _st()
    G.record_check(st2, _check(st2))
    G.apply(st2, {"action": "met"})
    assert G.report(st2, _check(st2)).startswith("Met: 10/10 after 1 check.")
    for t in (text, G.report(st2), G.status_line(st), G.away_line(st)):
        assert "—" not in t and "Meshpool" not in t


def test_terminal_goal_command_shows_a_plain_status_line_and_runs_the_same_engine():
    from meshpool_harness.agent.tui_session import SharedCommands as TS
    shown, did = [], []
    st = _st(turns=3, checks=1, spent=0.25, elapsed=600.0)
    st["history"] = [{"n": 1, "score": 7.0}]
    fake_s = type("S", (), {"gs": st, "goal": st["goal"], "set_goal": lambda self, t: did.append(("set", t)),
                            "goal_action": lambda self, op: did.append(("op", op))})()
    St = type("St", (), {"dim": staticmethod(str), "amber": staticmethod(str)})
    fake = type("F", (), {"term": type("T", (), {"st": St, "p": lambda self, x: shown.append(x)})(), "s": fake_s,
                          "_need_shared": lambda self: True})()
    TS.cmd_goal(fake, "")
    assert shown[-1].strip().startswith("Goal: ship the report. Working. 7/10. Turn 3 of 30, 1 of 10 checks, $0.25")
    TS.cmd_goal(fake, "start")
    TS.cmd_goal(fake, "resume")
    TS.cmd_goal(fake, "off")
    TS.cmd_goal(fake, "make it a 10/10")
    assert did == [("op", "start"), ("op", "resume"), ("set", ""), ("set", "make it a 10/10")]


def test_while_you_were_away_has_one_line_per_goal(chat, monkeypatch):
    from meshpool_harness import away
    s = chat()
    st = _st()
    G.record_check(st, _check(st))
    G.apply(st, {"action": "met"})
    G.save(chat.sessions / s.id, st)
    rows = away.goal_runs(0)
    assert len(rows) == 1 and rows[0]["chat_id"] == s.id
    assert rows[0]["line"] == "Goal met: ship the report. 10/10 after 1 check."
    assert away.goal_runs(time.time() + 60) == []
