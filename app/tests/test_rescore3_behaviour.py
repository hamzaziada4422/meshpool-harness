"""Behaviour problems persona testers hit in round 3 of the 0.0.18 release candidate (Priya, Lina, Maya, Sara).

1. Auto sent office work to Codex (Priya's rooms.xlsx, Sara's xlsx, pptx and docx). Office work now goes to
   meshpool's own agent, whatever outside agents are installed.
3. Codex-made workbooks had no stored totals: the value filling ran only in meshpool's own agent loop. It now runs
   for every harness at the turn's end.
4. Notes that told the model "do not mention this to the user" made it tell the user exactly that. They are neutral
   facts now, and the save of a Word document fixes typos only, and says which.
5. "Checked as an office or facilities manager: found nothing to fix." sat inside the answer. It is a short muted
   line in the turn's footer now ("Checked · all good").
6, 13. Builder language in Chat mode replies ("min-height: 44px", "put your address in SHOP_EMAIL"): Chat mode chats
   get one short paragraph asking for outcomes and offers to do it.
7. "I can't display images in this chat" while the chart was on screen: the tool result says the picture is shown.
8. A raw shell command, a link, a path and "30000" as the final answer: nudged once, then the turn ends on what it
   made.
9. A dropped CSV was copied into the user's folder: it is kept in the chat's attachments in meshpool's data.
10. meshpool's end-of-turn write only touches workbooks the turn wrote.
11. "<invoke_placeholder> </invoke_placeholder>" in a reply: never shown or saved.
12. An order form that says "Thank you" while it sends nothing: the reviewer and a measured check flag it.

Fake engines and a scripted model: no live model calls."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import textwrap
import time
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshpool_harness import harness_router as HRT  # noqa: E402
from meshpool_harness import harnesses as HR  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from test_rescore2_behaviour import FAKE_ACP, _idle, _model, _until, chat, home  # noqa: E402,F401

PRIYA = ("Take rooms.xlsx, which lists 8 rooms with length and width, and make a new workbook called rooms-summary.xlsx "
         "with the area of every room and a total at the bottom.")
SARA = ["Budget_2026.xlsx has a wrong Total SUM in the Total column (it seems to leave out a quarter). Please fix it.",
        "Update the numbers in Board_Update_Q3.pptx from Budget_2026.xlsx and fix the typo \"teh\" on every slide.",
        "In Expense_Policy.docx, shorten section 3 by about a third. Make it as tracked changes so I can review.",
        "Make a PDF of Board_Update_Q3.pptx that I can email to the board."]
PRIYA_RAW = ("mkdir -p /tmp/claude-0/-tmp/852bb802/scratchpad/chk && soffice --headless --convert-to csv --outdir "
             "/tmp/claude-0/-tmp/852bb802/scratchpad/chk rooms-summary.xlsx >/dev/null 2>&1; cat\n"
             "[rooms-summary.csv](rooms-summary.csv)\n/tmp/claude-0/home/user/fakehome/Studio\n30000")


def _soffice() -> bool:
    try:
        from meshpool_harness.agent import office_mcp_server as O
        return bool(O._soffice())
    except Exception:  # noqa: BLE001
        return bool(shutil.which("soffice"))


def _rooms(path: Path, cached: bool = False) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rooms"
    ws.append(["Room", "Length (m)", "Width (m)", "Area (m2)"])
    ws.append(["Kitchen", 4, 3.5, "=B2*C2"])
    ws.append(["Hall", 2, 5, "=B3*C3"])
    ws.append(["Total", None, None, "=SUM(D2:D3)"])
    wb.save(path)


@pytest.fixture
def outside(home, monkeypatch):
    """A fake outside engine registered as Codex, installed and ready, next to meshpool's own agents."""
    script = home / "fake_acp.py"
    script.write_text(FAKE_ACP)
    codex = {"id": "codex", "name": "Codex", "mode": "code", "type": "acp", "command": sys.executable,
             "args": [str(script)], "available": True, "ready": True, "provider": "key", "office": True}
    real = HR.load_all
    monkeypatch.setattr(HR, "load_all", lambda: [h for h in real() if h["id"] != "codex"] + [dict(codex)])
    return codex


# ============================================================ 1: office work on Auto goes to meshpool's own agent
def test_priyas_workbook_request_on_auto_runs_on_meshpool_work_with_codex_installed(chat, outside, monkeypatch):
    A, proj, made = chat
    from meshpool_harness import project_store as PS
    _rooms(proj / "rooms.xlsx")
    monkeypatch.setattr(PS, "get_meta", lambda pid: {"link": {"path": str(proj)}} if pid == "studio" else {})
    monkeypatch.setattr(PS, "touch", lambda pid: None)
    assert "codex" in HRT._ready("code")                     # Codex is installed and Auto may pick it
    v = A.create({"harness": "meshpool-auto", "task": PRIYA, "project_id": "studio"})   # Chat mode, linked folder
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    assert s.harness["id"] == "meshpool-work", s.harness["id"]
    using = next(e for e in s.events if e.get("using"))
    assert using["auto"]["harness"] == "meshpool-work" and "Office files" in using["auto"]["reason"], using


@pytest.mark.parametrize("task", [PRIYA, *SARA])
def test_office_files_route_to_meshpools_own_agent_in_both_modes(outside, task):
    assert HRT.route(task, "cowork")["harness"] == "meshpool-work"
    r = HRT.route(task, "code")                               # the Code tab keeps a Code agent (S-04)
    assert r["harness"] == "meshpool-code" and r["ranking"][0]["harness"] == "meshpool-code", r


DANIEL = ["From the Northfield research report, make a 6 to 8 slide board deck in Ridgeway's brand: navy 14304F, amber "
          "E8A33D, Georgia font. Save it as Ridgeway_PdM_Board_Deck.pptx.",
          "Make a 6 to 8 slide board deck from Northfield_PdM_Benchmark_2025.pdf, in Ridgeway Advisory's brand: navy "
          "14304F, amber E8A33D, Georgia font. Include a cover, an executive summary, charts built from the report's "
          "numbers, and next steps. Only use facts in the report. Save it as Ridgeway_PdM_Board_Deck.pptx.",
          "Add two slides to the same deck on skills and the talent gap, from the report. Add short speaker notes to "
          "every slide too.",
          "A one-page memo to the board summarising the deck, as Ridgeway_PdM_Board_Memo.docx, in the same brand, with "
          "a footer reading: Ridgeway Advisory | Confidential."]


@pytest.mark.parametrize("task", DANIEL)
def test_daniels_deck_and_memo_in_a_code_chat_with_a_folder_run_on_meshpools_own_agent(chat, outside, task):
    A, proj, made = chat
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": task, "workdir": str(proj)})
    made.append(v["id"])
    assert A.LIVE[v["id"]].harness["id"] == "meshpool-code"    # the office tools and the forge engine, not Codex


@pytest.mark.parametrize("task", DANIEL[2:])
def test_an_office_turn_in_a_chat_auto_gave_to_codex_moves_to_meshpools_own_agent(chat, outside, monkeypatch, task):
    A, proj, made = chat
    monkeypatch.setattr(HRT, "route", lambda text, kind, **k: {"harness": "codex", "name": "Codex",
                                                               "category": "code", "reason": "code words"})
    v = A.create({"kind": "code", "harness": "meshpool-auto", "task": "tidy the build script", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    assert s.harness["id"] == "codex"                          # Auto chose it for the first, non-office message
    s._capability_turn(task)
    assert s.harness["id"] == "meshpool-code"
    picked = A.create({"kind": "code", "harness": "codex", "task": "x", "workdir": str(proj)})
    made.append(picked["id"])
    p = A.LIVE[picked["id"]]
    p._capability_turn(task)                                   # picked by hand: it keeps the work
    assert p.harness["id"] == "codex"


def test_the_reopened_session_line_names_no_engine():
    import inspect
    from meshpool_harness.harnesses import acp, pi_rpc
    for mod in (acp, pi_rpc):
        src = inspect.getsource(mod)
        assert "could not reopen its own session" not in src and "earlier session for this chat could not be" in src


def test_code_work_still_goes_by_the_table_and_a_picked_outside_agent_stays_available(outside):
    assert HRT.route("fix the bug in export.py that writes report.xlsx", "code")["harness"] != "meshpool-work"
    assert HR.get("codex")["available"]                       # the user can still pick it


# ============================================================ 3: value filling for every harness at the turn's end
FAKE_WRITER = FAKE_ACP.replace('''    elif meth == "session/prompt":''', '''    elif meth == "session/prompt" and os.environ.get("FAKE_WRITE"):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Room", "Area"]); ws.append(["Kitchen", 14]); ws.append(["Hall", 10])
        ws.append(["Total", "=SUM(B2:B3)"])
        wb.save(os.environ["FAKE_WRITE"])
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Made room-summary.xlsx."}}}})
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
    elif meth == "session/prompt":''')


@pytest.mark.skipif(not _soffice(), reason="LibreOffice is needed to calculate the values")
def test_a_workbook_an_outside_agent_made_gets_its_values_at_the_turns_end(chat, home, monkeypatch):
    A, proj, made = chat
    script = home / "fake_writer.py"
    assert FAKE_WRITER != FAKE_ACP
    script.write_text(FAKE_WRITER)
    manifest = {"id": "fakecodex", "name": "Codex", "mode": "code", "type": "acp", "command": sys.executable,
                "args": [str(script)], "available": True, "ready": True, "provider": "key", "office": True}
    real = HR.get
    monkeypatch.setattr(HR, "get", lambda hid, model=None: dict(manifest) if hid == "fakecodex" else real(hid, model))
    out = proj / "room-summary.xlsx"
    monkeypatch.setenv("FAKE_WRITE", str(out))
    v = A.create({"kind": "code", "harness": "fakecodex", "task": "make room-summary.xlsx", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("make room-summary.xlsx")
    _idle(s, 120)
    vals = openpyxl.load_workbook(out, data_only=True).active
    assert vals["B4"].value == 24, "the total has no stored value"
    turn = s.cp.turns()[-1]
    assert turn["changed"] == [{"path": "room-summary.xlsx", "status": "added"}], turn
    s.cp.revert_to_before(turn["n"])                          # Undo removes the file meshpool filled as well
    assert not out.exists()


# ============================================================ 4: no secrecy notes, typos only
def _strings_sent_to_the_model():
    import ast
    root = Path(__file__).resolve().parents[1]
    for base in (root / "meshpool_harness", root / "engines"):
        for p in base.rglob("*.py"):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(
                n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body and isinstance(
                n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                    yield p, n.value


def test_no_text_sent_to_the_model_asks_it_to_keep_something_from_the_user():
    import re
    rx = re.compile(r"\b(?:do not|don't|never)\s+mention\b|not for the user|note for you, not the user", re.I)
    bad = [(str(p.name), s[:80]) for p, s in _strings_sent_to_the_model() if rx.search(s)]
    assert not bad, bad
    from meshpool_harness import capabilities as CAP
    from meshpool_harness import xl_values
    from meshpool_harness.agent import look as L
    for text in (CAP.note(["pdf"]), CAP.note(["pdf"], direct=True), xl_values.PENDING_NOTE, L.TAIL):
        assert not rx.search(text), text


class _Proofreader:
    def __init__(self, edits):
        self.edits = edits

    def chat(self, model, messages, **k):
        self.system = messages[0]["content"]
        return {"choices": [{"message": {"content": json.dumps({"edits": self.edits})}}]}


def test_the_save_fixes_typos_only_and_says_so_in_plain_words(home, monkeypatch):
    import engines.forge.clarity as CL
    monkeypatch.setattr("meshpool_harness.core.models.reviewer_models", lambda *a, **k: ["small-model"])
    md = ("The open house is on Saturday and we will recieve guests from ten.\n\n"
          "Phase two is on track and the the builders finish in March.\n\n"
          "We hope you can join us for coffee and a tour.")
    cl = _Proofreader([{"id": "s1.0", "text": "The open house is on Saturday and we will receive guests from ten."},
                       {"id": "s1.1", "text": "Phase two is on track and the builders finish in March."},
                       {"id": "s1.2", "text": "Please come along for a coffee and a guided tour."}])     # a rewording
    out, rep = CL.review_markdown(md, cl, typos=True)
    assert "recieve" not in out and "the the" not in out and "We hope you can join us for coffee" in out, out
    assert rep["changed"] == 2 and rep["rejected"][0]["why"] == "reworded, not a typo fix", rep
    assert "spelling" in cl.system and "never rephrase" in cl.system
    from meshpool_harness.agent import office_mcp_server as O
    note = O.typo_note(rep)
    assert "2 spelling mistakes were corrected (recieve to receive, the the to the)" in note, note
    assert "mention" not in note.lower() and "tidied" not in note


# ============================================================ 5: the look line goes to the footer
class _Agent:
    def __init__(self):
        self.events, self.messages, self._turn_no = [], [{"role": "assistant", "content": "Made rooms-summary.xlsx."}], 3
        self._look_st = {}

    def emit(self, ev):
        self.events.append(ev)


def test_the_look_line_is_not_added_to_the_reply():
    from meshpool_harness.agent import look as L
    a = _Agent()
    line = "Checked as an office or facilities manager: found nothing to fix."
    assert L._close(a, "Made rooms-summary.xlsx.", line) == "Made rooms-summary.xlsx.\n\n" + line   # the turn result
    assert a.messages[-1]["content"] == "Made rooms-summary.xlsx."
    assert not [e for e in a.events if e.get("type") == "assistant"], a.events
    assert a.events == [{"type": "look_line", "text": line, "look": True}]
    assert a.last_look["line"] == line


@pytest.mark.parametrize("look,checks,short", [
    ("Checked as an office or facilities manager: found nothing to fix.", "", "Checked · all good"),
    ("Checked as a customer on her phone ordering a birthday cake: fixed 2 things: the order form error and low "
     "contrast.", "", "Checked · fixed 2 things"),
    ("", "Checked the formulas. One thing to look at: some formulas show errors.", "Checked · 1 to look at"),
    ("Checked as a florist shop owner: found nothing to fix.", "Checked the formulas, and that the file opens cleanly.",
     "Checked · all good"),
])
def test_the_footer_line_is_short_with_the_sentence_in_its_fold(look, checks, short):
    from meshpool_harness import checks as CK
    got, full = CK.footer(look, {"line": checks})
    assert got == short and full and "Checked" in full, (got, full)


def test_the_turn_footer_carries_the_look_and_the_reply_has_no_checked_sentence(chat, monkeypatch):
    A, proj, made = chat
    _model(monkeypatch, [{"role": "assistant", "content": "I made rooms-summary.xlsx with the areas and a total."}])
    v = A.create({"kind": "cowork", "harness": "meshpool-work", "task": "rooms", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    line = "Checked as an office or facilities manager: found nothing to fix."
    from meshpool_harness.agent import look as L
    monkeypatch.setattr(L, "gate", lambda agent, g, final, **k: (False, L._close(agent, final, line)))
    s.send("make rooms-summary.xlsx")
    _idle(s)
    replies = [e["text"] for e in s.events if e.get("type") == "assistant" and e.get("text")]
    assert replies == ["I made rooms-summary.xlsx with the areas and a total."], replies
    foot = [e for e in s.events if e.get("type") == "checks_line"]
    assert len(foot) == 1 and foot[0]["text"] == "Checked · all good" and foot[0]["summary"] == line, foot
    assert "Checked as" not in json.dumps(s.agent.messages[-1])


# ============================================================ 6, 13: Chat mode asks for outcomes, not code
def test_chat_mode_gets_one_paragraph_about_outcomes_and_the_code_tab_does_not(chat, monkeypatch):
    A, proj, made = chat
    from meshpool_harness import assistant as AS
    seen = _model(monkeypatch, [])
    for kind, want in (("cowork", True), ("code", False)):
        v = A.create({"kind": kind, "harness": "meshpool-work" if kind == "cowork" else "meshpool-code",
                      "task": "make an order page", "workdir": str(proj)})
        made.append(v["id"])
        s = A.LIVE[v["id"]]
        s.send("make an order page")
        _idle(s)
        first = json.dumps(seen[-1]["messages"])
        assert ("not a developer" in first) is want, (kind, first[-600:])
    assert "I'll connect the form" in AS.CHAT_MODE and "never ask them to edit code" in AS.CHAT_MODE
    assert len(AS.CHAT_MODE) < 700                            # one short paragraph


# ============================================================ 7: a saved picture is shown in the chat
def test_a_chart_saved_in_the_folder_is_said_to_be_shown_in_the_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    proj = tmp_path / "LinaData"
    proj.mkdir()
    a = core.Agent(str(proj), mode="auto")
    a._turn_no = 1
    a.emit = lambda ev: None
    out = a._run_tool({"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(
        {"path": "monthly_revenue_2025.svg", "content": "<svg xmlns='http://www.w3.org/2000/svg'></svg>"})}})
    assert "monthly_revenue_2025.svg is saved in the user's folder and shown to the user in this chat" in out, out
    assert "view_output" in out                               # a model that takes images may look at it
    monkeypatch.setattr(a, "_sees_images", lambda: False)
    out = a._run_tool({"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(
        {"path": "chart2.svg", "content": "<svg xmlns='http://www.w3.org/2000/svg'></svg>"})}})
    assert "shown to the user in this chat" in out and "view_output" not in out, out
    out = a._run_tool({"id": "c3", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(
        {"path": "notes.txt", "content": "hi"})}})
    assert "shown to the user" not in out


# ============================================================ 8: a command instead of an answer
def test_a_raw_command_as_the_final_answer_is_nudged_once(chat, monkeypatch):
    A, proj, made = chat
    seen = _model(monkeypatch, [{"role": "assistant", "content": PRIYA_RAW},
                                {"role": "assistant", "content": "I made rooms-summary.xlsx with a total of 24 m²."}])
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "rooms", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("make rooms-summary.xlsx")
    _idle(s)
    assert len(seen) == 2 and "Your last message was a command, not an answer" in json.dumps(seen[1]["messages"][-1])
    evs = [(e["type"], e.get("text"), e.get("malformed")) for e in s.events if e.get("type") in ("assistant",
                                                                                                  "review_round")]
    assert evs[-2:] == [("review_round", None, True),
                        ("assistant", "I made rooms-summary.xlsx with a total of 24 m².", None)], evs


def test_a_second_raw_command_ends_on_what_the_turn_made(chat, monkeypatch):
    A, proj, made = chat
    _rooms(proj / "rooms-summary.xlsx")
    seen = _model(monkeypatch, [{"role": "assistant", "content": PRIYA_RAW},
                                {"role": "assistant", "content": "cat rooms-summary.csv\n/tmp/x/Studio"}])
    v = A.create({"kind": "code", "harness": "meshpool-code", "task": "rooms", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    later = time.time() + 30                                  # made during the turn, as far as its time says
    os.utime(proj / "rooms-summary.xlsx", (later, later))
    s.send("make rooms-summary.xlsx")
    _idle(s)
    assert len(seen) == 2
    last = [e["text"] for e in s.events if e.get("type") == "assistant" and e.get("text")][-1]
    assert last == "Made rooms-summary.xlsx, 1 sheet.", last


def test_an_outside_engine_that_answers_with_a_command_is_asked_once_then_ends_plainly(chat, home, monkeypatch):
    A, proj, made = chat
    script = home / "fake_acp.py"
    script.write_text(FAKE_ACP)
    manifest = {"id": "fakecodex", "name": "Codex", "mode": "code", "type": "acp", "command": sys.executable,
                "args": [str(script)], "available": True, "ready": True, "provider": "key", "office": True}
    real = HR.get
    monkeypatch.setattr(HR, "get", lambda hid, model=None: dict(manifest) if hid == "fakecodex" else real(hid, model))
    monkeypatch.setenv("FAKE_REPLY", PRIYA_RAW)
    v = A.create({"kind": "code", "harness": "fakecodex", "task": "rooms", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("make rooms-summary.xlsx")
    _idle(s)
    replies = [e["text"] for e in s.events if e.get("type") == "assistant" and e.get("text")]
    assert replies and "soffice" not in replies[-1] and "/tmp/" not in replies[-1], replies
    assert "came out as a command" in replies[-1] or replies[-1].startswith("Made "), replies


@pytest.mark.parametrize("text,bad", [
    (PRIYA_RAW, True), ('{"cmd": "ls -la"}', True), ("/home/user/Studio/rooms-summary.xlsx", True),
    ('<invoke name="bash"><parameter name="command">ls</parameter></invoke>', True),
    ("I made rooms-summary.xlsx with a total of 320.7 m² at the bottom.", False), ("49995", False),
    ("Done. Run this to check it:\n\n```\npython3 check.py\n```\n\nIt prints the total.", False),
])
def test_what_counts_as_a_command_instead_of_an_answer(text, bad):
    from meshpool_harness.core import textcalls as TC
    assert TC.malformed_reply(text) is bad


# ============================================================ 9: a dropped file stays out of the user's folder
def test_a_dropped_file_is_kept_with_the_chat_and_one_in_the_folder_is_used_where_it_is(chat, monkeypatch):
    A, proj, made = chat
    seen = _model(monkeypatch, [])
    (proj / "rooms.xlsx").write_bytes(b"PK rooms")
    v = A.create({"kind": "cowork", "harness": "meshpool-work", "task": "clean it", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    csv = b"order_id,region\n1,North\n" * 1000
    r = s.send("Please clean this", files=[{"name": "sales_2025.csv", "data": base64.b64encode(csv).decode()},
                                           {"name": "rooms.xlsx", "data": base64.b64encode(b"PK rooms").decode()}])
    assert not r.get("queued")
    _idle(s)
    assert sorted(x.name for x in proj.iterdir()) == ["rooms.xlsx"]          # nothing copied into the folder
    kept = s.attachments_dir() / "sales_2025.csv"
    assert kept.read_bytes() == csv
    assert s._attached == [{"name": "sales_2025.csv", "path": str(kept), "attached": True},
                           {"name": "rooms.xlsx", "path": "rooms.xlsx", "reused": True}], s._attached
    sent = json.dumps(seen[-1]["messages"][-1])
    assert str(kept) in sent and "Attached files (in the project folder): rooms.xlsx" in sent, sent
    user = next(e for e in s.events if e.get("type") == "user")
    assert "meshpool keeps them" not in user["text"]                         # the note is for the model only
    from meshpool_harness.agent import office_mcp_server as O
    monkeypatch.setattr(O, "_project", lambda: proj)
    monkeypatch.setenv("MH_CHAT_ID", s.id)                                   # the office tools serve this chat
    assert O._readable(kept)                                                 # the office tools may read it
    monkeypatch.setenv("MH_CHAT_ID", "another-chat")
    assert not O._readable(kept)                                             # never another chat's upload
    monkeypatch.setenv("MH_CHAT_ID", s.id)
    assert s.agent._outside_path(str(kept)) == ""                            # so may the agent, without asking
    turn = s.cp.turns()[-1]
    r = s.cp.revert_to_before(turn["n"])                                     # Undo leaves both alone
    assert kept.exists() and (proj / "rooms.xlsx").exists(), r


# ============================================================ 10: meshpool's own write only on files the turn wrote
def test_value_filling_skips_a_workbook_the_turn_only_read(tmp_path, monkeypatch):
    from meshpool_harness import checkpoints as CP
    from meshpool_harness import xl_values
    root = tmp_path / "LinaData"
    root.mkdir()
    _rooms(root / "region_summary.xlsx")
    cp = CP.Checkpoints(str(root), tmp_path / "store", mode="journal")
    t0 = time.time() - 1
    cp.begin_turn("check every number with pandas", 1, 0)
    os.utime(root / "region_summary.xlsx", None)                 # fresh on disk, but this turn did not write it
    with cp.tool("write_file", {"path": "made.xlsx"}):
        _rooms(root / "made.xlsx")
    filled = []
    monkeypatch.setattr(xl_values, "fill", lambda p, *a, **k: filled.append(Path(p).name) or 0)
    core._cache_formula_values(root, t0, cp)
    assert filled == ["made.xlsx"], filled
    assert sorted(cp.turn_written()) == ["made.xlsx"]
    cp.end_turn()


# ============================================================ 11: tool markup never reaches the chat
def test_tool_markup_in_a_reply_is_not_shown_or_saved(chat, monkeypatch):
    A, proj, made = chat
    _model(monkeypatch, [{"role": "assistant", "content": (
        "Your order page is ready.<invoke_placeholder> </invoke_placeholder>\n\nIt has a form for name and email."
        "<invoke_placeholder>\n</invoke_placeholder>\n\n<invoke_placeholder> </invoke_placeholder>")}])
    v = A.create({"kind": "cowork", "harness": "meshpool-work", "task": "order page", "workdir": str(proj)})
    made.append(v["id"])
    s = A.LIVE[v["id"]]
    s.send("make my order page")
    _idle(s)
    reply = [e["text"] for e in s.events if e.get("type") == "assistant" and e.get("text")][-1]
    assert reply == "Your order page is ready.\n\nIt has a form for name and email.", reply
    s._persist(running=False)
    saved = (A.SESSIONS / s.id / "transcript.jsonl").read_text(encoding="utf-8")
    assert "invoke_placeholder" not in saved
    from meshpool_harness.core import textcalls as TC
    assert TC.display_safe("Use `<parameter name=\"x\">` here") == "Use `<parameter name=\"x\">` here"


# ============================================================ 12: a form that pretends
MAYA_FORM = textwrap.dedent("""
    <html><body><h1>Maya's Flowers</h1>
    <form id="order"><input name="name"><input name="email"><button type="submit">Order</button></form>
    <p id="done" hidden></p>
    <script>
    document.getElementById("order").addEventListener("submit", e => { e.preventDefault();
      const n = e.target.name.value; const d = document.getElementById("done");
      d.textContent = "Thank you, " + n + "! Here is your order."; d.hidden = false; });
    </script></body></html>""")


def test_a_form_that_only_says_thank_you_is_flagged_and_an_honest_one_is_not():
    from meshpool_harness.agent import look as L
    assert "sends nothing anywhere" in L.fake_form(MAYA_FORM, "", "make an order page for my flower shop")
    assert L.fake_form(MAYA_FORM.replace("e.preventDefault();", "e.preventDefault(); fetch('/api/order');"), "") == ""
    assert L.fake_form(MAYA_FORM, "fetch('https://formspree.io/f/x', {method: 'POST'})") == ""
    assert L.fake_form(MAYA_FORM.replace("<h1>", "<p>Ordering online is coming soon.</p><h1>"), "") == ""
    assert L.fake_form(MAYA_FORM.replace('<form id="order">', '<form id="order" action="https://shop.example/o">'),
                       "") == ""
    assert L.fake_form(MAYA_FORM, "", "a placeholder order form that does nothing yet") == ""
    assert "fake-form" in L.KEEP
    from meshpool_harness.agent import lookreview as LR
    assert "Ordering online is coming soon" in LR.SYSTEM and "thanks the visitor" in LR.VERDICT_ASK
    LR.VERDICT_ASK % "a customer"                              # still formats
