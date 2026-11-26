"""Behaviour problems persona testers still hit in round 2 of the 0.0.18 release candidate (Maya, Priya).

1. Auto picked Codex for documents; Codex wrote "unexpected status 404 Not Found: not found, url: http://127.0.0.1:.../
   v1/responses" as its reply and the user had to click "Use meshpool Code instead". Now an engine Auto chose that
   fails before any work runs the message again on meshpool's own agent, with one plain line. An engine the user
   picked keeps a plain error and a one-click way to meshpool's own agent.
2. An outside engine's error reached the chat raw: it goes through core/errtext.outside, the raw text in its details.
3. A dead model service under an outside agent looked like a slow one ("Still waiting for the model (3 min)"), and Try
   again posted the message twice under "Stopped. Send a message to continue.".
4. After Undo the model did not know: "did my prices go back?" got a confused answer.
5. The model talked about meshpool's internals: a hidden note ("The system note suggested making a PDF"), values it
   thought missing ("I did not recalculate it") and the clarity pass ("lightly reworded when the file was saved").
6. make_spreadsheet stopped before making the file and said "finished; its quality checks did not all pass. Files:"
   with no file, and the steps it took to recover counted as "4 of 7 steps had a problem".

Fake engines and a scripted model: no live model calls."""
from __future__ import annotations

import json
import os
import socket
import sys
import textwrap
import threading
import time
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import harness_router as HRT  # noqa: E402
from meshpool_harness import harnesses as HR  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.core import errtext as ET  # noqa: E402

CODEX_404 = "unexpected status 404 Not Found: not found, url: http://127.0.0.1:44615/v1/responses"
FALLBACK = "That way of working failed, so meshpool used its own agent."

# A fake ACP engine. FAKE_REPLY is its whole reply to a prompt; FAKE_HANG makes a prompt run until session/cancel.
FAKE_ACP = textwrap.dedent('''
    import json, os, sys
    def send(o):
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    pending = {}
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m:
            continue
        meth, mid = m["method"], m.get("id")
        if meth == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"},
                                                          "agentCapabilities": {}}})
        elif meth == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "s1"}})
        elif meth == "session/prompt":
            if os.environ.get("FAKE_HANG"):
                pending["s1"] = mid
                continue
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": os.environ["FAKE_REPLY"]}}}})
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        elif meth == "session/cancel":
            for sid, rid in list(pending.items()):
                pending.pop(sid)
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
''')


def _until(fn, secs=30.0, every=0.05):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    for k in ("MH_GRADER", "MH_LOOK_REVIEW", "MH_LEARN", "MH_CLARITY"):
        monkeypatch.setenv(k, "0")
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "mh").mkdir()
    C.write({"MESHPOOL_API_KEY": "mk-123456789", "MESHPOOL_BASE_URL": f"http://127.0.0.1:{_free_port()}/v1"})
    return tmp_path


@pytest.fixture
def chat(home, monkeypatch):
    from meshpool_harness.ui import agent_api as A
    sessions = home / "mh" / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: self._mcp_ready.set())
    proj = home / "proj"
    proj.mkdir()
    made = []
    yield A, proj, made
    for sid in made:
        s = A.LIVE.pop(sid, None)
        if s:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture
def engine(home, monkeypatch):
    """A fake outside engine named Codex, registered as harness "fakecodex" on the key endpoint."""
    script = home / "fake_acp.py"
    script.write_text(FAKE_ACP)
    manifest = {"id": "fakecodex", "name": "Codex", "mode": "code", "type": "acp", "command": sys.executable,
                "args": [str(script)], "available": True, "ready": True, "provider": "key", "office": False}
    real = HR.get
    monkeypatch.setattr(HR, "get", lambda hid, model=None: dict(manifest) if hid == "fakecodex" else real(hid, model))
    return manifest


def _model(monkeypatch, replies):
    """meshpool's own agent on a scripted model: one reply per request, then "Done."."""
    replies, seen = list(replies), []

    def fake(self_, base, key, body, stream):
        seen.append(body)
        nxt = replies.pop(0) if replies else {"role": "assistant", "content": "Done."}
        return {"choices": [{"message": nxt, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)
    return seen


def _idle(s, secs=60):
    assert _until(lambda: s.state == "idle" and not getattr(s, "_turn_open", False), secs), [
        (e.get("type"), e.get("text")) for e in s.events]


def _shown(s) -> list:
    """What the chat shows: user messages, replies, errors and status lines, in order."""
    import re
    house = re.compile(r"^(?:Working as:|context [\d,]+k|Starting |MCP|No MCP)|is ready\.")   # kept out of the chat
    return [(e["type"], e.get("text")) for e in s.events if e.get("type") in ("user", "assistant", "error", "status")
            and not (e.get("type") == "status" and (e.get("using") or e.get("route") or house.search(str(e.get("text")))))]


# ============================================================ 1 and 2: an outside engine that fails before any work
def test_engine_errors_are_plain_words_with_the_raw_text_kept():
    plain, raw = ET.outside(CODEX_404)
    assert raw == CODEX_404
    for bit in ("404", "http", "url", "unexpected status", "Codex", "127.0.0.1"):
        assert bit not in plain, (bit, plain)
    assert plain.startswith("The AI service")
    assert ET.engine_failed(CODEX_404) and ET.engine_failed("stream disconnected before completion: error sending "
                                                            "request for url (http://127.0.0.1:9/v1/responses)")
    assert ET.engine_failed("exceeded retry limit, last status: 500 Internal Server Error")
    assert ET.outside("Codex is not installed on this computer.") == ("Codex is not installed on this computer.", "")
    assert not ET.engine_failed("I could not find a file called prices.xlsx in this folder.")


def test_an_engine_auto_chose_that_fails_runs_the_message_on_meshpools_own_agent(chat, engine, monkeypatch):
    A, proj, made = chat
    monkeypatch.setenv("FAKE_REPLY", CODEX_404)
    monkeypatch.setattr(HRT, "route", lambda text, kind, **k: {"harness": "fakecodex", "name": "Codex",
                                                               "category": "documents", "reason": "docs words"})
    seen = _model(monkeypatch, [{"role": "assistant", "content": "Here is your list of bouquets."}])
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": "list my bouquets", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    assert s.harness["id"] == "fakecodex"
    s.send("list my bouquets")
    _idle(s)
    assert s.harness["id"] == "meshpool-code" and len(seen) == 1
    shown = _shown(s)
    assert shown == [("user", "list my bouquets"), ("status", FALLBACK),
                     ("assistant", "Here is your list of bouquets.")], shown
    line = next(e for e in s.events if e.get("text") == FALLBACK)
    for bit in ("404", "http", "Codex", "unexpected status"):
        assert bit not in line["detail"], line
    using = [e for e in s.events if e.get("using")]
    assert not any(e.get("back") or "Codex" in str(e.get("text")) for e in using), using     # no engine name shown
    assert not any(e.get("handoff") for e in using)          # no second "Using ..." line
    assert "list my bouquets" in json.dumps(seen[0]["messages"][-1])
    turn = s.cp.turns()[-1]                                   # Undo cuts meshpool's conversation at this message
    assert turn["message_index"] == 1, turn


def test_an_engine_the_user_picked_keeps_a_plain_error_and_a_way_to_meshpools_agent(chat, engine, monkeypatch):
    A, proj, made = chat
    monkeypatch.setenv("FAKE_REPLY", CODEX_404)
    seen = _model(monkeypatch, [])
    v = A.create({"kind": "code", "harness": "fakecodex", "task": "list my bouquets", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("list my bouquets")
    _idle(s)
    assert s.harness["id"] == "fakecodex" and not seen
    errs = [e for e in s.events if e.get("type") == "error"]
    assert len(errs) == 1 and errs[0]["detail"] == CODEX_404, errs
    assert "404" not in errs[0]["text"] and "http" not in errs[0]["text"], errs
    assert not any(e.get("type") == "assistant" and "unexpected status" in str(e.get("text")) for e in s.events)
    back = [e for e in s.events if e.get("using") and e.get("back")]
    assert back and back[-1]["back"]["harness"] == "meshpool-code", back


def test_a_start_failure_moves_to_meshpools_own_agent_with_one_line(chat, monkeypatch):
    """The older M29-02 path (the engine could not start) now goes the same way: meshpool's own agent, one line."""
    A, proj, made = chat
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": "fix the bug", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    ev0 = len(s.events)
    s.emit({"type": "user", "text": "fix the bug"})
    s.emit({"type": "error", "text": "Pi exited during the turn."})
    s.emit({"type": "turn_end"})
    s.agent.start_error = "Pi exited during the turn."
    s.harness = {"id": "pi", "name": "Pi", "type": "pi", "mode": "code"}
    from meshpool_harness.ui.agent_api import write_meta
    write_meta(s.id, routed="pi")
    moved = []
    monkeypatch.setattr(s, "handoff", lambda hid, why="", quiet=False: moved.append((hid, quiet)))
    assert s._engine_fallback("fix the bug", ev0) is True and moved == [("meshpool-code", True)]
    assert not [e for e in s.events[ev0:] if e.get("type") in ("user", "error", "turn_end")]
    s.emit({"type": "user", "text": "fix the bug"})          # the resend: the line goes under it
    assert [e.get("text") for e in s.events[ev0:]] == ["fix the bug", FALLBACK]
    assert s._engine_fallback("fix the bug", ev0) is False   # once per chat


# ============================================================ 3: a dead or hung model connection
def test_a_dead_model_service_under_an_outside_agent_is_the_service_down_line_at_once(chat, engine, monkeypatch):
    A, proj, made = chat
    monkeypatch.setenv("FAKE_HANG", "1")
    monkeypatch.setenv("MH_OUTSIDE_PROBE_S", "1")
    v = A.create({"kind": "code", "harness": "fakecodex", "task": "make the memo", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    t0 = time.time()
    s.send("make the memo")
    down = _until(lambda: next((e for e in s.events if e.get("down")), None), 20)
    assert down and time.time() - t0 < 15, [(e.get("type"), e.get("text")) for e in s.events]
    assert "connection refused" in down["text"] and "Checking again in" in down["text"], down
    s.stop()
    _idle(s)


def test_try_again_runs_the_message_once_more_without_a_second_copy_or_a_stopped_line(chat, monkeypatch):
    A, proj, made = chat
    calls, release = [], threading.Event()

    def fake(self_, base, key, body, stream):
        calls.append(body)
        if len(calls) == 1:                  # the first request never answers (a hung model connection)
            while not release.wait(0.1):
                self_._check_stop()
        return {"choices": [{"message": {"role": "assistant", "content": "Here it is."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}}, False
    monkeypatch.setattr(core.Agent, "_post", fake)
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "make the memo shorter", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("make the memo shorter")
    assert _until(lambda: len(calls) == 1 and s.state == "working", 30)
    try:
        r = A.session_action(s.id, "retry", {})
    finally:
        release.set()
    assert r["sent"] == {"queued": False}, r["sent"]
    _idle(s)
    assert len(calls) == 2
    shown = _shown(s)
    assert [x for x in shown if x[0] == "user"] == [("user", "make the memo shorter")], shown
    assert not any(e.get("type") == "interrupted" for e in s.events), [e for e in s.events if e.get("type") == "interrupted"]
    assert shown[-1] == ("assistant", "Here it is."), shown
    msgs = [m for m in s.agent.messages if m.get("role") == "user"]
    assert len(msgs) == 1, msgs                                # the model gets the message once too


# ============================================================ 4: Undo leaves a note for the model
def test_after_undo_the_model_is_told_which_files_went_back(chat, monkeypatch):
    A, proj, made = chat
    (proj / "Bouquet Price List.csv").write_text("Bouquet,Price\nRed Rose Dozen,65\n")
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "write_file", "arguments": json.dumps({"path": "Bouquet Price List.csv",
                                                       "content": "Bouquet,Price\nRed Rose Dozen,71.5\n"})}}]}
    seen = _model(monkeypatch, [call, {"role": "assistant", "content": "Prices are 10% higher."},
                                {"role": "assistant", "content": "Yes, Red Rose Dozen is $65 again."}])
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "raise prices", "workdir": str(proj),
                  "mode": "auto"})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("Raise all prices by 10%.")
    _idle(s)
    assert "71.5" in (proj / "Bouquet Price List.csv").read_text()
    s.revert(s.cp.turns()[-1]["n"])
    assert "65" in (proj / "Bouquet Price List.csv").read_text()
    assert any(str(e.get("text")).startswith("Undone. Restored 1 file") for e in s.events)    # the user's line, as before
    s.send("Did my prices go back?")
    _idle(s)
    last_user = [m for m in seen[-1]["messages"] if m.get("role") == "user"][-1]
    text = json.dumps(last_user)
    assert ("The user undid your previous reply. These files were restored to how they were before it: "
            "Bouquet Price List.csv.") in text, text
    assert "Raise all prices" not in json.dumps(seen[-1]["messages"])      # the undone messages stay gone
    shown_users = [e["text"] for e in s.events if e.get("type") == "user"]
    assert shown_users == ["Did my prices go back?"], shown_users        # the note is hidden in the chat
    s.send("thanks")                                                        # said once, not on every message
    _idle(s)
    assert "The user undid" not in json.dumps([m for m in seen[-1]["messages"] if m.get("role") == "user"][-1])


# ============================================================ 5: internals the model talked about
def test_a_pdf_the_user_has_is_not_a_pdf_to_make_and_the_note_says_keep_quiet():
    from meshpool_harness import capabilities as CAP
    maya = ("Hi! My flower supplier Greenfield sent me an invoice (it's the PDF in this folder). How much do I owe "
            "them and when do I have to pay by?")
    assert CAP.needs(maya) == []
    assert CAP.needs("turn this into a PDF") == ["pdf"] and CAP.needs("make a PDF of the price list") == ["pdf"]
    for direct in (False, True):
        assert "Do not mention this note to the user." in CAP.note(["pdf"], direct=direct)


@pytest.fixture
def office(home, monkeypatch):
    proj = home / "Studio"
    proj.mkdir()
    monkeypatch.setenv("MESHPOOL_PROJECT", str(proj))
    from meshpool_harness.agent import office_mcp_server as O
    return O, proj


def test_a_workbook_whose_values_meshpool_stores_after_the_turn_says_so_in_the_tool_result(office):
    O, proj = office
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Room", "Length (m)", "Width (m)", "Area (m2)"])
    ws.append(["Kitchen", 4, 3.5, "=B2*C2"])
    ws.append(["Total", None, None, "=SUM(D2:D2)"])
    wb.save(proj / "room-summary.xlsx")
    out, failed = O.TOOLS["check_document"][0]("room-summary.xlsx")
    assert not failed and "stores the values in the file when this turn ends" in out, out
    assert "do not recalculate" in out.lower() or "not recalculated" in out
    out, failed = O.read_document("room-summary.xlsx")
    assert not failed and "stores the values" in out, out
    plain = openpyxl.Workbook()
    plain.active.append(["Room", "Area"])
    plain.active.append(["Kitchen", 14])
    plain.save(proj / "plain.xlsx")
    assert "stores the values" not in O.read_document("plain.xlsx")[0]


def test_the_clarity_pass_is_not_news_for_the_user(office, monkeypatch):
    O, proj = office
    monkeypatch.setenv("MH_CLARITY", "1")
    import engines.forge.clarity as CL
    monkeypatch.setattr(CL, "review_markdown", lambda md, client, **k: (md, {"model": "small-model", "changed": 5,
                                                                             "checked": 7}))
    out, failed = O.make_document("Hello.\n\nThe visit is on 12 December.", filename="client-update.docx")
    assert not failed and (proj / "client-update.docx").exists(), out
    assert "Clarity check" not in out and "small-model" not in out and "lines made clearer" not in out, out
    assert "do not mention it to the user" in out, out


# ============================================================ 6: an engine that stopped, and steps that were redone
FAKE_ENGINE_STOPPED = textwrap.dedent("""
    import json
    print(json.dumps({"ok": False, "steps": [{"engine": "sheets", "ok": False, "answer": "",
                      "reason": "exceeded retry limit, last status: 504 Gateway Timeout"}]}))
""")


def test_an_engine_that_stopped_before_making_the_file_says_so(home, monkeypatch, tmp_path):
    from meshpool_harness.agent import office_jobs as OJ
    script = tmp_path / "fake_engine.py"
    script.write_text(FAKE_ENGINE_STOPPED)
    monkeypatch.setattr(OJ, "_python_m", lambda: [sys.executable, str(script)])
    saved = dict(OJ.JOBS)
    OJ.JOBS.clear()
    try:
        proj = tmp_path / "Studio"
        proj.mkdir()
        jid = OJ.new_job("sheets", "summary of rooms.xlsx", [], proj, scope="t-stop")
        OJ.run(jid, timeout=60)
        text, failed = OJ.report(jid, rel_to=proj)
    finally:
        OJ.JOBS.clear()
        OJ.JOBS.update(saved)
    assert failed
    assert text.startswith("The sheets engine stopped before it made the file, so nothing was saved"), text
    assert "quality checks" not in text and "Deliver this file" not in text and "Files:" not in text, text
    assert "504" not in text and "AI service had a problem" in text, text
    assert "Call the same tool again" in text


def test_the_rooms_summary_passes_the_sheets_gate(tmp_path, monkeypatch):
    """Priya's request goes to the plan for an edit or derivation of her workbook, and a summary sheet with live area
    formulas and a total row passes its gate (round 1's table fix, 0584cb41)."""
    import shlex
    from engines.sheets import engine as E
    from meshpool_harness.core.client import MockClient, tool_call
    monkeypatch.setenv("MH_DOSSIER", "0")
    root = Path(__file__).resolve().parents[1]
    wb = openpyxl.Workbook()
    wb.active.title = "Rooms"
    wb.active.append(("Room", "Length (m)", "Width (m)"))
    for r in (("Reception", 6.5, 4.2), ("Open studio", 14, 9.5), ("Kitchen", 4, 3.5)):
        wb.active.append(r)
    wb.save(tmp_path / "rooms.xlsx")
    script = textwrap.dedent("""
        import openpyxl
        src = openpyxl.load_workbook("rooms.xlsx")["Rooms"]
        rows = [r for r in src.iter_rows(min_row=2, values_only=True) if r[0]]
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Summary"
        ws.append(["Room", "Length (m)", "Width (m)", "Area (m2)"])
        for i, (n, l, w) in enumerate(rows, 2):
            ws.append([n, l, w, f"=B{i}*C{i}"])
        ws.append(["Total", None, None, f"=SUM(D2:D{len(rows) + 1})"])
        wb.save("room-summary.xlsx")
    """)
    chk = f"PYTHONPATH={shlex.quote(str(root))} {shlex.quote(sys.executable)} -m engines.sheets.check room-summary.xlsx"
    c = MockClient([tool_call("write_file", {"path": "build.py", "content": script}),
                    tool_call("bash", {"command": f"{shlex.quote(sys.executable)} build.py"}),
                    tool_call("bash", {"command": chk}),
                    tool_call("finish", {"answer": "Made room-summary.xlsx.", "evidence": [chk],
                                         "artifacts": ["room-summary.xlsx"]})])
    task = ("I've put rooms.xlsx in the Studio folder. Please make a summary workbook from it with each room's area "
            "(length times width) and a total at the bottom. Call it room-summary.xlsx.\n\nInput files (in the "
            "working directory): rooms.xlsx")
    r = E.solve(task, workdir=str(tmp_path), task_id="t", max_turns=8, client=c, model="mock",
                files=[str(tmp_path / "rooms.xlsx")])
    assert r["ok"] is True, r["answer"]
    checked = [m["content"] for m in c.requests[3]["messages"] if m["role"] == "tool"][-1]
    assert "PASS" in checked, checked


def test_failed_steps_a_later_step_redid_are_marked_retried(chat):
    """Priya's turn: make_spreadsheet stopped, ls and two checks failed on the missing file, a script made it and the
    check passed. Those failures were redone: the step count must not call them problems."""
    A, proj, made = chat
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "rooms", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.emit({"type": "user", "text": "make room-summary.xlsx"})
    steps = [("a", "read_file", "rooms.xlsx", True),
             ("b", "mcp__meshpool-office__make_spreadsheet", "Make a new spreadsheet, room-summary.xlsx from rooms.xlsx",
              False),
             ("c", "bash", "ls -la room-summary.xlsx", False),
             ("d", "mcp__meshpool-office__check_workbook", "Use the office tool check workbook on room-summary.xlsx",
              False),
             ("e", "mcp__meshpool-office__read_document", '{"path": "room-summary.xlsx"}', False),
             ("x", "mcp__meshpool-office__read_document", '{"path": "notes.docx"}', False),     # never redone
             ("f", "bash", "python3 - <<'EOF'\nwb.save(\"room-summary.xlsx\")\nEOF", True),
             ("g", "mcp__meshpool-office__check_document", "Use the office tool check document on room-summary.xlsx",
              True)]
    for sid, name, summary, ok in steps:
        s.emit({"type": "tool_start", "id": sid, "name": name, "summary": summary})
        s.emit({"type": "tool_end", "id": sid, "name": name, "ok": ok, "output": "" if ok else "File not found"})
    s.emit({"type": "turn_end"})
    marks = [e for e in s.events if e.get("type") == "steps_retried"]
    assert len(marks) == 1 and marks[0]["ids"] == ["b", "c", "d", "e"], marks
    order = [e.get("type") for e in s.events]
    assert order.index("steps_retried") < order.index("turn_end")
