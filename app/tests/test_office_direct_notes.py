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
    assert "openpyxl" in n and "make_spreadsheet" not in n
    assert "For Word tracked changes or comments, use edit_office_file" in n   # offered only for those requests
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
    assert any("Your own check failed and the file has not been saved" in str(m.get("content")) for m in a.messages)


def test_office_finish_check_can_be_switched_off(tmp_path, monkeypatch):
    a = _office_agent(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_OFFICE_FINISH_CHECK", "0")
    assert a.send("Delete the blank rows above Code in data.xlsx") == "Done."


def _deck(path, n):
    from pptx import Presentation
    prs = Presentation()
    for _ in range(n):
        prs.slides.add_slide(prs.slide_layouts[5])
    prs.save(str(path))


def test_slide_count_check(tmp_path):
    import time as _t
    from meshpool_harness.agent.core import _slide_count_miss
    t0 = _t.time() - 1
    _deck(tmp_path / "deck.pptx", 3)
    ask = "Make deck.pptx, 4 to 6 slides presenting quarterly.csv to leadership."
    miss = _slide_count_miss(ask, tmp_path, t0)
    assert "4 to 6 slides" in miss and "deck.pptx has 3 slides" in miss
    _deck(tmp_path / "deck.pptx", 5)
    assert _slide_count_miss(ask, tmp_path, t0) == ""
    assert _slide_count_miss("Make a 5-slide deck", tmp_path, t0) == ""
    assert "deck.pptx has 5 slides" in _slide_count_miss("Make a 3-slide deck", tmp_path, t0)
    assert _slide_count_miss("Make a deck about sales", tmp_path, t0) == ""            # no stated count
    assert _slide_count_miss(ask, tmp_path, _t.time() + 60) == ""                       # not written this turn


def test_total_row_check(tmp_path):
    import time as _t
    import openpyxl
    from meshpool_harness.agent.core import _total_row_miss
    t0 = _t.time() - 1
    ask = "In inventory.xlsx, add a Value column. Add a total row for the Value column."
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Value"])
    ws.append(["a", 5])
    ws.append([None, "=SUM(B2:B2)"])
    wb.save(tmp_path / "inventory.xlsx")
    assert "no cell in it says Total" in _total_row_miss(ask, tmp_path, t0)
    ws["A3"] = "Total"
    wb.save(tmp_path / "inventory.xlsx")
    assert _total_row_miss(ask, tmp_path, t0) == ""
    assert _total_row_miss("Sort inventory.xlsx by value", tmp_path, t0) == ""


def test_revision_requests_bring_the_office_editor(tmp_path, monkeypatch):
    from meshpool_harness.office_intent import wants_revisions
    assert wants_revisions("Make these changes as tracked changes so I can accept or reject them in Word.")
    assert wants_revisions("Add a comment on paragraph 2 of memo.docx")
    assert not wants_revisions("Make the dates in report.docx bold")
    assert "tracked_replace" in CAP.note(["edit"], direct=True)


def test_template_formatting_check(tmp_path):
    import time as _t
    import docx
    from meshpool_harness.agent.core import _template_format_miss
    t0 = _t.time() - 1
    tpl = docx.Document()
    p = tpl.add_paragraph("Dear {name}, you owe ")
    p.add_run("{amount}").bold = True
    tpl.save(tmp_path / "letter_template.docx")
    _t.sleep(0.01)
    (tmp_path / "letters").mkdir()
    flat = docx.Document()
    flat.add_paragraph("Dear Ann, you owe $5")
    flat.save(tmp_path / "letters" / "ann.docx")
    ask = "Use letter_template.docx and customers.csv to make one letter per customer. Keep the template's formatting."
    since = (tmp_path / "letter_template.docx").stat().st_mtime + 0.001
    miss = _template_format_miss(ask, tmp_path, since)
    assert "ann.docx" in miss and "bold" in miss
    good = docx.Document()
    q = good.add_paragraph("Dear Ann, you owe ")
    q.add_run("$5").bold = True
    good.save(tmp_path / "letters" / "ann.docx")
    assert _template_format_miss(ask, tmp_path, since) == ""
    assert _template_format_miss("Sort data.xlsx", tmp_path, t0) == ""


def test_verification_cap_after_a_saved_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from meshpool_harness.agent.core import Agent
    a = Agent.__new__(Agent)
    a._office_direct = lambda: True
    save = {"cmd": "python3 -c 'wb.save(\"b.xlsx\")'", "code": 0}
    chk = {"cmd": "python3 -c 'print(1)'", "code": 0}
    a.ws = SimpleNamespace(commands=[save, chk, chk, chk])
    g = {"cmds0": 0}
    assert a._office_verify_note(g) == ""                       # three checks: not yet
    a.ws.commands.append(chk)
    note = a._office_verify_note(g)
    assert "Stop checking" in note and a._office_verify_note(g) == ""      # once
    g2 = {"cmds0": 0}
    a.ws.commands = [save, chk, {"cmd": "x", "code": 1}, chk, chk]
    assert a._office_verify_note(g2) == ""                      # a failing check since the save: keep working


def test_off_slide_shapes_are_caught(tmp_path):
    import time as _t
    from pptx import Presentation
    from pptx.util import Emu
    from meshpool_harness.agent.core import _off_slide_miss
    t0 = _t.time() - 1
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_textbox(Emu(prs.slide_width - 100), Emu(0), Emu(500000), Emu(100000))
    prs.save(str(tmp_path / "deck.pptx"))
    assert "past the edge" in _off_slide_miss(tmp_path, t0)
    prs2 = Presentation()
    s2 = prs2.slides.add_slide(prs2.slide_layouts[6])
    s2.shapes.add_textbox(Emu(0), Emu(0), Emu(500000), Emu(100000))
    prs2.save(str(tmp_path / "deck.pptx"))
    assert _off_slide_miss(tmp_path, t0) == ""


def test_unchanged_output_is_caught(tmp_path):
    import os
    import time as _t
    import openpyxl
    from meshpool_harness.agent.core import _unchanged_output_miss
    wb = openpyxl.Workbook()
    wb.active.append([None, "x"])
    wb.active.append(["Code", "y"])
    wb.save(tmp_path / "data.xlsx")
    old = _t.time() - 100
    os.utime(tmp_path / "data.xlsx", (old, old))
    t0 = _t.time() - 1
    wb.save(tmp_path / "out.xlsx")
    ask = "In data.xlsx, delete the blank rows above Code and save to out.xlsx"
    assert "exactly the same cells" in _unchanged_output_miss(ask, tmp_path, t0)
    wb.active.delete_rows(1)
    wb.save(tmp_path / "out.xlsx")
    assert _unchanged_output_miss(ask, tmp_path, t0) == ""


def test_hand_written_revision_xml_is_refused_when_tracked_changes_were_asked(tmp_path, monkeypatch):
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.hrouter import profiles as PROF
    from meshpool_harness.hrouter.route import Route
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    monkeypatch.setenv("MH_OFFICE_DIRECT", "1")
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None)
    a.set_route(Route(spec="data", family="data", base="work", profile="core", settings=PROF.settings("core")), "x")
    patch = {"input": "*** Begin Patch\n*** Add File: x.xml\n+<w:ins w:id=\"1\"><w:r/></w:ins>\n*** End Patch"}
    assert a._engine_only("apply_patch", patch) == ""                 # not asked for tracked changes
    a.office_revisions = True
    assert "edit_office_file" in a._engine_only("apply_patch", patch)


def test_office_looks_are_capped_per_request(tmp_path):
    from meshpool_harness.agent.core import Agent
    a = Agent.__new__(Agent)
    a._office_direct = lambda: True
    a._guarded_tool_inner = lambda c, g, cut=False: "ok"
    for name in ("_scratch_rewrite_note", "_shape_run_note", "_cycle_note"):
        setattr(a, name, lambda *x, **k: "")
    a._office_verify_note = lambda g: ""
    from types import SimpleNamespace
    a.ws = SimpleNamespace(edits=0, commands=[])
    a.root = tmp_path
    g = {}
    call = {"function": {"name": "mcp__meshpool_office__check_deck", "arguments": "{}"}}
    assert a._guarded_tool(call, g).startswith("ok") and a._guarded_tool(call, g).startswith("ok")
    assert "already ran 2 times" in a._guarded_tool(call, g)


def test_review_request_checks_only_fire_on_what_was_asked(tmp_path):
    import time as _t
    import openpyxl
    from meshpool_harness.agent.core import _slide_count_miss, _total_row_miss, _unchanged_output_miss
    from meshpool_harness.office_intent import wants_revisions
    t0 = _t.time() - 1
    _deck(tmp_path / "deck.pptx", 8)
    assert _slide_count_miss("Summarise the first 3 slides of deck.pptx", tmp_path, t0) == ""
    assert _slide_count_miss("Add 2 slides to deck.pptx about pricing", tmp_path, t0) == ""
    wb = openpyxl.Workbook()
    wb.active.append(["a", 1])
    wb.save(tmp_path / "b.xlsx")
    assert _total_row_miss("Remove the total row from b.xlsx", tmp_path, t0) == ""
    assert _total_row_miss("Highlight the total row in b.xlsx", tmp_path, t0) == ""
    assert "total row" in _total_row_miss("Add a total row to b.xlsx", tmp_path, t0)
    assert not wants_revisions("Add a comment column to tracker.xlsx")
    assert wants_revisions("Add a comment to paragraph 2 of memo.docx")


def test_review_formatting_only_edit_is_not_unchanged(tmp_path):
    import os
    import time as _t
    import openpyxl
    from openpyxl.styles import Font
    from meshpool_harness.agent.core import _unchanged_output_miss
    wb = openpyxl.Workbook()
    wb.active.append(["h1", "h2"])
    wb.save(tmp_path / "in.xlsx")
    old = _t.time() - 100
    os.utime(tmp_path / "in.xlsx", (old, old))
    t0 = _t.time() - 1
    wb.active["A1"].font = Font(bold=True)
    wb.save(tmp_path / "out.xlsx")
    assert _unchanged_output_miss("Highlight row 1 of in.xlsx in bold, save as out.xlsx", tmp_path, t0) == ""


def test_review_revision_greps_are_not_refused(tmp_path, monkeypatch):
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.hrouter import profiles as PROF
    from meshpool_harness.hrouter.route import Route
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    monkeypatch.setenv("MH_OFFICE_DIRECT", "1")
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None)
    a.set_route(Route(spec="data", family="data", base="work", profile="core", settings=PROF.settings("core")), "x")
    a.office_revisions = True
    grep = {"command": ["bash", "-lc", "unzip -p contract.docx word/document.xml | grep -c '<w:ins '"]}
    assert a._engine_only("shell", grep) == ""


def test_hidden_notes_do_not_switch_on_office_or_browser_tools(tmp_path, monkeypatch):
    """CLI check (0.0.16): Meshpool's hidden notes name .docx and .xlsx, so every chat's first message switched the
    office tools on (and the browser tools rode along): 40 tools in a small code chat instead of the benchmark's 1."""
    from meshpool_harness.agent.core import Agent
    from meshpool_harness import capabilities as CAP
    a = Agent(str(tmp_path), mode="auto", emit=lambda e: None)
    a._post = lambda base, key, body, stream: ({"choices": [{"message": {"role": "assistant", "content": "ok"},
                                                 "finish_reason": "stop"}], "usage": {}}, True)
    a.send("Fix the bug in calc.py" + CAP.SEP + "[Meshpool] Office files (.docx, .xlsx, .pptx) are made with ...")
    assert not a.office_on and not a.browser_on
    a.send("Make report.docx from notes.txt")
    assert a.office_on
    a.send("Check the landing page in index.html renders")
    assert a.browser_on
