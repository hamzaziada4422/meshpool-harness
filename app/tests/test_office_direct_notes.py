"""Office direct: the notes name the libraries the agent writes with, not engine tools; engine builds land under the
file name the user asked for (save_as). office3 traces: xlsx_budget 59 calls, pptx_chart deck.pptx missing."""
import shutil
import threading
from pathlib import Path

from meshpool_harness import assistant as AS
from meshpool_harness import capabilities as CAP
from meshpool_harness.agent import office_jobs as OJ
from meshpool_harness.agent import office_mcp_server as S


def test_direct_note_names_libraries_not_engine_tools():
    n = CAP.note(["spreadsheet"], direct=True)
    assert "openpyxl" in n and "make_spreadsheet" not in n and "office_task" not in n
    n = CAP.note(["presentation", "pdf"], direct=True)
    assert "python-pptx" in n and "reportlab" in n and "make_deck" not in n


def test_engine_note_unchanged_and_mentions_save_as():
    n = CAP.note(["spreadsheet"])
    assert "make_spreadsheet" in n and "save_as" in n


def test_direct_notes_never_forbid_writing_by_hand():
    t = AS._notes("all", "direct", True, False)
    assert "Never write a .pptx or .xlsx by hand" not in t and "openpyxl" in t
    assert "edit_office_file" not in t                      # not offered in office direct until an engine job
    assert "Never write a .pptx or .xlsx by hand" in AS._notes("all", True, True, False)
    assert "make_spreadsheet" not in AS._notes("all", False, True, False)


def test_save_as_copies_the_built_file_into_the_project(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_project", lambda: tmp_path)
    out = tmp_path / "Meshpool outputs" / "job"
    out.mkdir(parents=True)
    (out / "deck.pptx").write_bytes(b"pptx")
    jid = "j1"
    OJ.JOBS[jid] = {"engine": "slides", "out": out, "done": True, "result": {"ok": True, "steps": []},
                    "inputs": set(), "started": 0, "event": threading.Event()}
    S.SAVE_AS[jid] = "deck.pptx"
    try:
        text, failed = S._report(jid)
        assert not failed and (tmp_path / "deck.pptx").read_bytes() == b"pptx"
        assert "Saved as: deck.pptx" in text
        S._report(jid)                                   # a second status call copies nothing new
        assert not (tmp_path / "deck (2).pptx").exists()
    finally:
        OJ.JOBS.pop(jid, None)
        S.SAVE_AS.pop(jid, None)


def test_save_as_never_replaces_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_project", lambda: tmp_path)
    out = tmp_path / "Meshpool outputs" / "job"
    out.mkdir(parents=True)
    (out / "budget.xlsx").write_bytes(b"new")
    (tmp_path / "budget.xlsx").write_bytes(b"user")
    jid = "j2"
    OJ.JOBS[jid] = {"engine": "sheets", "out": out, "done": True, "result": {"ok": True, "steps": []},
                    "inputs": set(), "started": 0, "event": threading.Event()}
    S.SAVE_AS[jid] = "budget.xlsx"
    try:
        S._report(jid)
        assert (tmp_path / "budget.xlsx").read_bytes() == b"user"
        assert (tmp_path / "budget (2).xlsx").read_bytes() == b"new"
    finally:
        OJ.JOBS.pop(jid, None)
        S.SAVE_AS.pop(jid, None)


def test_schemas_offer_save_as():
    for name in ("make_deck", "make_spreadsheet"):
        assert "save_as" in S.TOOLS[name][2]


def test_session_notes_defer_to_the_engines_for_a_new_deliverable(monkeypatch):
    """A message asking for a new deck, report or model gets the engine note (with save_as): Agent.send offers the
    engines for it, so a "write it yourself" note would contradict the tools the turn has."""
    from types import SimpleNamespace
    from meshpool_harness.ui.agent_api import Session
    monkeypatch.delenv("MH_OFFICE_DIRECT_NOTE", raising=False)
    agent = SimpleNamespace(_office_direct=lambda: True, office_engines=False)
    s = SimpleNamespace(harness={}, agent=agent)
    f = Session._office_direct
    assert f(s, "Sort data.xlsx by date") is True
    assert f(s, "Make a 10-slide pitch deck for investors") is False
    assert f(s, "Make deck.pptx, 4 to 6 slides presenting quarterly.csv") is True   # named file: built by hand
    agent.office_engines = True                       # an earlier message brought the engines: they stay
    assert f(s, "Sort data.xlsx by date") is False
    agent.office_engines, agent._office_direct = False, (lambda: False)
    assert f(s, "Sort data.xlsx by date") is False
    agent._office_direct = lambda: True
    monkeypatch.setenv("MH_OFFICE_DIRECT_NOTE", "0")
    assert f(s, "Sort data.xlsx by date") is False


def test_edit_note_in_office_direct_names_libraries_not_absent_tools():
    n = CAP.note(["edit"], direct=True)
    assert "openpyxl" in n and "edit_office_file" not in n and "make_spreadsheet" not in n
    assert "formula text" in n
    assert "edit_office_file" in CAP.note(["edit"])                  # the engine lane keeps its note


def _office_agent(tmp_path, monkeypatch):
    import json as _json
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.hrouter import profiles as PROF
    from meshpool_harness.hrouter.route import Route
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    monkeypatch.setenv("MH_OFFICE_DIRECT", "1")
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None)
    a.set_route(Route(spec="data", family="data", base="work", profile="core", settings=PROF.settings("core")), "x")
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "shell", "arguments": _json.dumps({"command": ["bash", "-lc", "python3 -c 'assert 5 == 4'"]})}}]},
        {"role": "assistant", "content": "Done."},
        {"role": "assistant", "content": "Fixed and saved."},
    ])

    def fake(base, key, body, stream):
        return {"choices": [{"message": next(replies), "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, True
    a._post = fake
    return a


def test_office_direct_does_not_accept_done_right_after_its_own_failed_assert(tmp_path, monkeypatch):
    """SpreadsheetBench sb-374-31 (38f57ffb): its assert failed, it printed the rows and answered "Done" with the
    file unchanged. Once per request it is told the job is not done."""
    a = _office_agent(tmp_path, monkeypatch)
    assert a.send("Delete the blank rows above Code in data.xlsx") == "Fixed and saved."
    assert any("Your own check failed (AssertionError)" in str(m.get("content")) for m in a.messages)


def test_office_finish_check_can_be_switched_off(tmp_path, monkeypatch):
    a = _office_agent(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_OFFICE_FINISH_CHECK", "0")
    assert a.send("Delete the blank rows above Code in data.xlsx") == "Done."
