"""Where office engine work lands (persona run: Lina, Maya, Priya). A "Meshpool outputs" folder appeared inside the
user's own folder, with job folders, stale workbook copies, staged copies of her inputs, hidden .run folders with build
scripts, transcripts and logs, and dossier.md. The agent then told the user to delete it herself.

Now a job works in meshpool's own data (the chat's office-jobs folder) and only the deliverable is copied into the
user's folder, under its own name. A job that outlives its tool call returns a "still running" handle; a retry waits
on that build instead of starting a second one, and the file lands once (Lina: make_spreadsheet "timed out" three
times while it kept writing workbooks, and the turn took 45 minutes). Fake engine processes, no model calls."""
from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import office_jobs as OJ  # noqa: E402
from meshpool_harness.agent import office_mcp_server as O  # noqa: E402

# A fake sheets engine: what the real one leaves in its folder (the workbook, its build script, a .run folder with a
# transcript and a log, a research dossier and the staged copy of the input), then the result JSON on stdout.
FAKE = textwrap.dedent("""
    import json, os, sys, time
    out = sys.argv[sys.argv.index("--out") + 1]
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    os.makedirs(os.path.join(out, ".run", "scripts"), exist_ok=True)
    open(os.path.join(out, ".run", "transcript-req-1-sheets.jsonl"), "w").write("{}")
    open(os.path.join(out, ".run", "commands.log"), "w").write("$ python build_workbook.py")
    open(os.path.join(out, ".run", "scripts", "build_workbook.py"), "w").write("print(1)")
    open(os.path.join(out, "dossier.md"), "w").write("# notes")
    open(os.path.join(out, "dossier.json"), "w").write("{}")
    open(os.path.join(out, "sales_2025.csv"), "w").write("staged copy")
    import openpyxl
    wb = openpyxl.Workbook(); wb.active.title = "Summary"; wb.active.append(["Region", "Orders"])
    wb.save(os.path.join(out, "region_summary.xlsx"))
    print(json.dumps({"ok": True, "steps": [{"engine": "sheets", "ok": True, "answer": "Built region_summary.xlsx.",
                                             "artifacts": [os.path.join(out, "region_summary.xlsx")]}]}))
""")


@pytest.fixture()
def fake_engine(tmp_path, monkeypatch):
    script = tmp_path / "fake_engine.py"
    script.write_text(FAKE)
    monkeypatch.setattr(OJ, "_python_m", lambda: [sys.executable, str(script)])
    monkeypatch.setattr("engines.forge.tools.qa_summary", lambda files: "")
    saved = dict(OJ.JOBS)
    OJ.JOBS.clear()
    yield script
    OJ.JOBS.clear()
    OJ.JOBS.update(saved)


@pytest.fixture()
def project(tmp_path):
    p = tmp_path / "LinaData"
    p.mkdir()
    (p / "sales_2025.csv").write_text("order_id,region\n1,North\n")
    return p


def _tree(p: Path) -> set:
    return {str(x.relative_to(p)) for x in p.rglob("*")}


def test_a_sheets_job_in_a_project_folder_leaves_exactly_one_new_file(project, fake_engine):
    before = _tree(project)
    jid, how = OJ.start("sheets", "region summary workbook from sales_2025.csv", [str(project / "sales_2025.csv")],
                        project, scope="chat-lina")
    assert how == ""
    OJ.run(jid, timeout=60)
    text, failed = OJ.report(jid, rel_to=project)
    assert not failed, text
    assert _tree(project) - before == {"region_summary.xlsx"}, sorted(_tree(project) - before)
    assert not (project / "Meshpool outputs").exists()
    out = OJ.JOBS[jid]["out"]
    assert project not in out.parents and (out / ".run" / "commands.log").is_file()   # kept in meshpool's own data
    assert "- region_summary.xlsx" in text and "dossier" not in text and ".run" not in text
    assert OJ._made(OJ.JOBS[jid]) == [(project / "region_summary.xlsx").resolve()]


def test_a_rebuild_replaces_this_chats_own_earlier_file_and_never_the_users(project, fake_engine):
    a, _ = OJ.start("sheets", "region summary", [], project, scope="chat-lina")
    OJ.run(a, timeout=60)
    OJ.report(a)
    b, _ = OJ.start("sheets", "a different summary of regions by month", [], project, scope="chat-lina")
    OJ.run(b, timeout=60)
    OJ.report(b)
    assert sorted(x.name for x in project.glob("region_summary*")) == ["region_summary.xlsx"]
    (project / "region_summary.xlsx").write_bytes(b"the user's own edit")          # changed by the user: kept
    c, _ = OJ.start("sheets", "regional summary for the board", [], project, scope="chat-lina")
    OJ.run(c, timeout=60)
    OJ.report(c)
    assert (project / "region_summary.xlsx").read_bytes() == b"the user's own edit"
    assert (project / "region_summary (2).xlsx").is_file()


def test_a_slow_job_returns_a_still_running_handle_and_lands_once(project, fake_engine, monkeypatch):
    """make_spreadsheet on a job that outlives the call: "still running" (never a timeout), a retry of the same build
    waits on it instead of starting another, office_job_status gets the result, and the one file lands."""
    monkeypatch.setenv("FAKE_SLEEP", "3")
    monkeypatch.setattr(O, "WAIT", 0.5)
    monkeypatch.setattr(O, "_project", lambda: project)
    before = _tree(project)
    req = "Build region_summary.xlsx from the cleaned sales file: one row per region with order count"
    text, failed = O.make_spreadsheet(req)
    assert not failed and text.startswith("Still working") and "timed out" not in text.lower(), text
    jid = text.split("Job id: ")[1].split(".")[0]
    assert "office_job_status" in text and "Call office_job_status with this id" in text
    assert _tree(project) == before                                        # nothing half-made in her folder
    again, _ = O.make_spreadsheet(req + ", retrying the same request")    # a reworded retry: the same build
    assert f"Job id: {jid}" in again and len(OJ.JOBS) == 1, again
    deadline = time.time() + 60
    while True:
        monkeypatch.setattr(O, "WAIT", 5)
        text, failed = O.office_job_status(jid)
        if not text.startswith("Still working") or time.time() > deadline:
            break
    assert not failed and "region_summary.xlsx" in text, text
    assert _tree(project) - before == {"region_summary.xlsx"}
    assert len(OJ.JOBS) == 1


def test_a_client_that_gives_up_waiting_does_not_stop_the_job(project, fake_engine, monkeypatch):
    """The office client cancels a call it waited too long for ("no reply within 330 s"); that is not the user's
    Stop, so the build goes on and its file lands. A Stop still ends it."""
    from meshpool_harness.agent import mcp_stdio
    ev = threading.Event()
    ev.reason = "no reply within 330 s"
    ev.set()
    mcp_stdio._CALL.cancel = ev
    try:
        assert mcp_stdio.cancel_check()() is True and mcp_stdio.stop_check()() is False
        ev.reason = "cancelled by the user"
        assert mcp_stdio.stop_check()() is True
    finally:
        mcp_stdio._CALL.cancel = None


def test_an_unnamed_document_goes_to_the_top_of_the_folder(project, monkeypatch):
    monkeypatch.setattr(O, "_project", lambda: project)
    monkeypatch.setenv("MH_CLARITY", "0")
    msg, err = O.make_document("# Notes\n\nHello.")
    assert not err and (project / "document.docx").is_file(), msg
    assert not (project / "Meshpool outputs").exists()


def test_office_task_books_the_delivered_file_so_undo_takes_it_back(tmp_path, project, fake_engine, monkeypatch):
    """office_task runs without asking but writes the deliverable into the folder: the turn's checkpoint books it,
    and Undo removes it again (ad27fe76). The job's own files never enter the folder."""
    from meshpool_harness import config as C
    from meshpool_harness.agent import core
    from meshpool_harness.checkpoints import Checkpoints
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    for k in ("MH_GRADER", "MH_LOOK_REVIEW", "MH_LEARN", "MH_CACHE_FORMULAS", "MH_LEAN_WORK"):
        monkeypatch.setenv(k, "0")
    C.ensure_home()
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "office_task", "arguments": json.dumps({"engine": "sheets", "request": "region summary workbook"})}}]}
    replies = [call, {"role": "assistant", "content": "Your workbook is region_summary.xlsx."}]

    def fake(self_, base, key, body, stream):
        nxt = replies.pop(0) if replies else {"role": "assistant", "content": "Done."}
        return {"choices": [{"message": nxt, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)
    before = _tree(project)
    evs = []
    a = core.Agent(str(project), emit=evs.append, mode="auto", profile="work")
    cp = Checkpoints(str(project), tmp_path / "store", mode="journal")
    a.checkpoint = cp
    cp.begin_turn("make the workbook", 1, 0)
    a.send("Make a region summary workbook")
    cp.end_turn()
    ends = [e.get("output") for e in evs if e.get("type") == "tool_end"]
    assert _tree(project) - before == {"region_summary.xlsx"}, ends
    changed = [c["path"] for t in cp.turns() for c in t.get("changed") or []]
    assert changed == ["region_summary.xlsx"], changed
    r = cp.revert_to_before(1)
    assert not (project / "region_summary.xlsx").exists(), r
    assert _tree(project) == before


def test_listing_labels_reads_max_column_once_per_sheet(tmp_path, monkeypatch):
    """Lina's 50,000-row export: the workbook rules asked openpyxl for max_column on every row, and openpyxl works it
    out from every cell each time, so checking the workbook took over ten minutes. Read once per sheet now."""
    import openpyxl
    from openpyxl.worksheet.worksheet import Worksheet
    from engines.forge import validate_xlsx as VX
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Region", "Revenue ($)"])
    for i in range(3000):
        ws.append(["North", i])
    calls = {"n": 0}
    real = Worksheet.max_column

    def counted(self):
        calls["n"] += 1
        return real.fget(self)
    monkeypatch.setattr(Worksheet, "max_column", property(counted))
    labels = [VX._label(ws, r) for r in range(1, ws.max_row + 1)]
    assert labels[0] == "Region" and labels[5] == "North"
    assert calls["n"] < 50, calls
