"""Sara (finance: Excel, Word and PowerPoint in the web UI), round 3 at 5 of 10.

1. Codex went silent twice and the turn ended on "Codex sent nothing for 300 seconds ... (MH_ACP_IDLE_S sets the
   limit)". The chat now says it in plain words with Try again; a turn with no work done yet goes to meshpool's agent.
2. "Make a PDF of Board_Update_Q3.pptx" failed in Codex's sandbox (LibreOffice cannot open its local socket there),
   took 9 steps and ended on a card holding the raw soffice command and a JSON blob. meshpool converts it in one step,
   and every card says "Convert Board_Update_Q3.pptx to PDF".
3. A reply linked a backup in a temporary folder outside the project: "That folder is not one of your projects."
4. Codex saved over a workbook Excel had open (~$Budget_2026.xlsx next to it), with no question.
5. The look drew pages 1 and 2 of a 5-page policy whose section 3 changed, and the line said "all good" under a
   reply that asked about section 3.
6. The reply called the user "she" (her name came from the lock file).
7. The .xlsx file card had a generic icon; .pptx, .docx and .pdf had thumbnails.
8. A long wait said only "Still waiting for the model"."""
import json
import os
import re
import shutil
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import office_guard as OG  # noqa: E402
from meshpool_harness.harnesses import acp as ACP  # noqa: E402
from meshpool_harness.harnesses.acp import ACPAgent  # noqa: E402

UI = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"

# A fake Codex: answers the handshake, then per prompt key: STALL (a step, then silence: a model call that never
# answers), MUTE (silence at once), SOFFICE (asks to run soffice outside its sandbox), WRITELOCKED (asks to run a
# command that saves over a workbook), anything else echoes "fresh:<text>".
FAKE = textwrap.dedent('''
    import json, sys
    log = sys.argv[1]
    def send(o):
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    def upd(sid, u): send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
    ask = {}
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m:
            if m.get("id") in ask:
                sid, pmid = ask.pop(m["id"])
                with open(log, "a") as f: f.write(json.dumps({"method": "answer", "params": m.get("result")}) + "\\n")
                upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "asked-done"}})
                send({"jsonrpc": "2.0", "id": pmid, "result": {"stopReason": "end_turn"}})
            continue
        meth, mid, p = m["method"], m.get("id"), m.get("params", {})
        with open(log, "a") as f: f.write(json.dumps({"method": meth, "params": {k: v for k, v in p.items() if k != "mcpServers"}}) + "\\n")
        if meth == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"},
                  "agentCapabilities": {"loadSession": True}}})
        elif meth == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "s1"}})
        elif meth in ("session/load", "session/set_model", "session/set_mode"):
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif meth == "session/prompt":
            sid, text = p["sessionId"], p["prompt"][0]["text"]
            key = text.split("\\n\\n\\u200b")[0]
            if "STALL" in key:
                upd(sid, {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "rm -- lock", "kind": "execute",
                          "status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": "ok"}}]})
            elif "MUTE" in key:
                pass
            elif "SOFFICE" in key or "WRITELOCKED" in key:
                cmd = ("cd /tmp/lout && HOME=/tmp/lohome timeout 180 soffice -env:UserInstallation=file:///tmp/lohome/p6 "
                       "--headless --convert-to pdf --outdir /tmp/lout Board_Update_Q3.pptx 2>&1; echo $?") if "SOFFICE" in key \\
                    else "python3 -c \\"import openpyxl; wb=openpyxl.load_workbook('Budget_2026.xlsx'); wb.save('Budget_2026.xlsx')\\""
                ask[9001] = (sid, mid)
                send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
                    "sessionId": sid, "toolCall": {"toolCallId": "t1", "title": cmd, "kind": "execute",
                                                   "rawInput": {"command": ["bash", "-lc", cmd], "cwd": "/tmp/lout"}},
                    "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]}})
            else:
                upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "fresh:" + key}})
                send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
''')


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE)
    proj = tmp_path / "Board"
    proj.mkdir()
    log = tmp_path / "acp.jsonl"
    manifest = {"id": "fake", "name": "Codex", "type": "acp", "command": sys.executable, "args": [str(script), str(log)],
                "office": False, "share_mcp": False}
    agents = []

    def make(mode="ask", answer=None, events=None):
        asked = []

        def approve(kind, title, detail, options=None, scope=""):
            asked.append({"kind": kind, "title": title, "detail": detail, "options": options, "scope": scope})
            return (answer(asked[-1]) if answer else "yes"), ""
        a = ACPAgent(dict(manifest), str(proj), emit=(events.append if events is not None else None),
                     approve=approve, mode=mode)
        a.asked = asked
        agents.append(a)
        return a

    def calls(method=None):
        rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return [r for r in rows if method is None or r["method"] == method]
    yield make, calls, proj
    for a in agents:
        a.close()


def _soffice():
    try:
        from meshpool_harness import office_runtime
        return office_runtime.find_soffice()
    except Exception:  # noqa: BLE001
        return None


def _deck(path: Path, slides: int = 3):
    from pptx import Presentation
    p = Presentation()
    for i in range(slides):
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = f"Board update slide {i + 1}"
        s.placeholders[1].text = "Total budget the year: $1.67M"
    p.save(str(path))


def _workbook(path: Path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget 2026"
    ws.append(["Cost line", "Q1", "Q2", "Q3", "Total"])
    for r, (n, a, b, c) in enumerate([("Salaries", 410000, 415000, 425000), ("Rent", 60000, 60000, 60000)], start=2):
        ws.append([n, a, b, c, f"=SUM(B{r}:D{r})"])
    wb.save(str(path))


# ---------- 1. Codex goes silent ------------------------------------------------------------------------------------

def test_a_silent_codex_ends_the_turn_in_plain_words_with_try_again(fake, monkeypatch):
    make, calls, _ = fake
    monkeypatch.setenv("MH_ACP_IDLE_S", "1.5")
    evs = []
    a = make(mode="auto", events=evs)
    t0 = time.time()
    a.send("STALL after the rm")
    assert time.time() - t0 < 20
    errs = [e for e in evs if e.get("type") == "error"]
    assert errs and errs[-1]["text"] == "The model stopped answering. Your files are as the last step left them."
    assert errs[-1].get("again") is True                       # the page draws Try again on this line
    assert "MH_ACP_IDLE_S" not in errs[-1]["text"] and "seconds" not in errs[-1]["text"]
    assert a.turn_failed is None                               # a step ran: no silent move to another engine
    assert a.send("again") == "fresh:again"                    # the next message restarts the engine


def test_a_codex_silent_before_any_step_counts_as_an_engine_failure(fake, monkeypatch):
    make, calls, _ = fake
    monkeypatch.setenv("MH_ACP_IDLE_S", "1.5")
    a = make(mode="auto", events=[])
    a.send("MUTE please")
    assert a.turn_failed == ACP.STALL_TEXT                      # Session._engine_fallback runs it on meshpool's agent


def test_the_stall_line_has_a_try_again_button():
    js = (UI / "app.js").read_text(encoding="utf-8")
    case = js[js.index('case "error": {'):]
    case = case[:case.index("break; }")]
    assert "ev.again" in case and "data-again" in case and "Try again" in case and "retryLast()" in case


# ---------- 2. a PDF of a deck, in one step ------------------------------------------------------------------------

def test_a_pdf_request_is_one_conversion_with_a_plain_card(fake):
    if not _soffice():
        pytest.skip("LibreOffice is not available here")
    make, calls, proj = fake
    _deck(proj / "Board_Update_Q3.pptx", 8)
    evs = []
    a = make(mode="ask", events=evs)
    reply = a.send("Make a PDF of Board_Update_Q3.pptx that I can email to the board.")
    assert (proj / "Board_Update_Q3.pdf").is_file()
    assert OG.page_count(proj / "Board_Update_Q3.pdf") == 8
    assert "Board_Update_Q3.pdf is ready" in reply and "8 pages" in reply
    assert len(a.asked) == 1
    card = a.asked[0]
    assert card["title"] == "Convert Board_Update_Q3.pptx to PDF"
    plain, _, more = card["detail"].partition("\n\nDetails:\n")
    assert more and "soffice" not in plain and "{" not in plain and "-env:" not in plain
    steps = [e for e in evs if e.get("type") == "tool_start"]
    assert len(steps) == 1 and steps[0]["summary"] == "Convert Board_Update_Q3.pptx to PDF"
    assert not calls("session/prompt")                         # no engine call, no 9 steps


def test_a_pdf_request_with_more_work_goes_to_the_engine(fake):
    make, calls, proj = fake
    _deck(proj / "Board_Update_Q3.pptx")
    assert OG.conversion_request("Fix the typo in Board_Update_Q3.pptx and make a PDF", proj) is None
    assert OG.conversion_request("Make a PDF of Missing.pptx", proj) is None
    assert OG.conversion_request("Make a PDF of Board_Update_Q3.pptx that I can email to the board.", proj)[1] == "pdf"
    assert OG.conversion_request("Export Board_Update_Q3.pptx as a PDF", proj) is not None


def test_a_raw_soffice_permission_card_reads_in_plain_words(fake):
    make, calls, proj = fake
    a = make(mode="ask")
    assert a.send("SOFFICE please") == "asked-done"
    card = a.asked[0]
    assert card["kind"] == "bash" and card["title"] == "Convert Board_Update_Q3.pptx to PDF"
    # a command card like the built-in agent's: the detail is the command alone (it goes in the Details fold), no
    # JSON of the request; core.approval_view gives the card its plain line
    assert card["detail"].startswith("cd /tmp/lout") and "{" not in card["detail"] and '"command"' not in card["detail"]
    from meshpool_harness.agent.core import approval_view, _plain_command_title
    assert approval_view(card["kind"], card["title"], card["detail"]) == {"sum": "Convert Board_Update_Q3.pptx to PDF"}
    # the built-in agent's card for the same command (its title is "Run this command?") says the same
    assert approval_view("bash", "Run this command?", card["detail"])["sum"] == "Convert Board_Update_Q3.pptx to PDF"
    assert _plain_command_title("soffice --convert-to pdf x.pptx; rm -rf ~/Documents") != "Convert x.pptx to PDF?"
    # the page draws that plain line and folds the command under Details
    js = (UI / "app.js").read_text(encoding="utf-8")
    pa = js[js.index("function plainApproval("):]
    assert "ev.sum" in pa[:400] and "<summary>Details</summary>" in pa[:600]


def test_codex_is_told_to_convert_and_render_with_meshpool_s_office_tools(fake):
    """Daniel, round 3: "LibreOffice would not convert in this environment", so he was asked to check the layout
    himself. In the modes where Codex sandboxes its commands, a chat with the office tools tells it once how to make a
    PDF or page pictures: meshpool-office convert_document, which runs outside the sandbox."""
    from meshpool_harness import harnesses as HR
    assert HR.CODEX["no_soffice_in"] == ["ask", "edits"]
    make, calls, proj = fake
    a = make(mode="ask")
    a.manifest.update(office=True, no_soffice_in=["ask", "edits"])
    a.office_on = True
    a.send("make the board deck and check its layout")
    a.send("now fix slide 3")
    sent = [c["params"]["prompt"][0]["text"] for c in calls("session/prompt")]
    assert "convert_document" in sent[0] and "cannot run inside your command sandbox" in sent[0]
    assert "convert_document" not in sent[1]                 # once per session
    b = make(mode="auto")                                    # Full access: Codex runs commands without its sandbox
    b.manifest.update(office=True, no_soffice_in=["ask", "edits"])
    b.office_on = True
    b.send("make the board deck")
    assert "convert_document" not in calls("session/prompt")[-1]["params"]["prompt"][0]["text"]


def test_soffice_cannot_run_inside_codex_s_sandbox_but_meshpool_s_converter_can(tmp_path):
    """The cause, where Codex is installed: its workspace-write sandbox refuses the local socket LibreOffice opens
    at start, so the conversion exits 1 even for a text file. meshpool's own converter makes it."""
    codex = shutil.which("codex")
    if not codex:                     # an npx install under the home folder; a CI runner may not read /root at all
        try:
            npx = Path.home() / ".npm" / "_npx"
            codex = next(iter(sorted(npx.glob("*/node_modules/@openai/codex-linux-x64/vendor/*/bin/codex"))), None)
        except OSError:
            codex = None
    if not codex:                     # CI installs LibreOffice but not Codex: say only what is missing
        pytest.skip("Codex CLI not installed")
    if not shutil.which("bwrap"):
        pytest.skip("bwrap is not here")
    if not _soffice():
        pytest.skip("no usable LibreOffice here")
    import subprocess
    (tmp_path / "t.txt").write_text("hello")
    env = dict(os.environ, CODEX_HOME=str(tmp_path / "ch"))
    (tmp_path / "ch").mkdir()
    r = subprocess.run([str(codex), "sandbox", "-c", 'sandbox_mode="workspace-write"', "--", sys.executable, "-c",
                        "import socket; s=socket.socket(socket.AF_UNIX); s.bind('/tmp/mh_sock_probe_%d' % __import__('os').getpid()); print('bound')"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    if "bound" in r.stdout:
        pytest.skip("this Codex sandbox allows local sockets")
    assert "Operation not permitted" in (r.stdout + r.stderr)
    dst, why = OG.convert(tmp_path / "t.txt", "pdf")
    assert dst is not None and dst.is_file(), why


# ---------- 3. no link to a folder the app cannot open ------------------------------------------------------------

def test_a_reply_never_links_a_backup_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    C.ensure_home()
    proj = tmp_path / "Board"
    proj.mkdir()
    scratch = tmp_path / "scratchpad" / "Budget_2026.backup.xlsx"          # a temporary copy outside the project
    text = (f"Q4 is added.\n\nA copy of the file as it was before this change is at `{scratch}`.\n"
            f"The workbook is {proj / 'Budget_2026.xlsx'}.")
    from meshpool_harness.ui import agent_api as A

    class S:
        workdir = str(proj)
    ev = A.Session._no_dead_links(S(), {"type": "assistant", "text": text})
    assert str(scratch) not in ev["text"] and str(scratch.parent) not in ev["text"]
    assert "Budget_2026.backup.xlsx (a temporary copy outside this project" in ev["text"]
    assert str(proj / "Budget_2026.xlsx") in ev["text"]       # a file in the project stays a link
    # the page's TEMP_PATH_RX would have made a button of it: nothing in the new text matches it
    assert not re.search(r"/(?:tmp|var/tmp|var/folders)/\S*Budget_2026\.backup", ev["text"])


def test_outside_agents_are_told_not_to_point_to_scratch_copies():
    from meshpool_harness import assistant as AS
    assert "never point the user to a file outside it" in AS.NO_SCRATCH_COPIES
    block = json.dumps(AS.build_context("fix the budget", rules=True, record=False, lookup=False), ensure_ascii=False)
    assert json.dumps(AS.NO_SCRATCH_COPIES, ensure_ascii=False)[1:-1] in block


# ---------- 4. a workbook open in Excel ----------------------------------------------------------------------------

def test_codex_asks_before_changing_a_workbook_open_in_excel(fake):
    make, calls, proj = fake
    _workbook(proj / "Budget_2026.xlsx")
    (proj / "~$Budget_2026.xlsx").write_bytes(b"\x0cSara Finance" + b" " * 40)
    a = make(mode="ask", answer=lambda card: "keep")
    out = a.send("Budget_2026.xlsx has a wrong Total SUM in the Total column. Please fix it.")
    assert out.startswith("fresh:")
    assert len(a.asked) == 1
    card = a.asked[0]
    assert card["title"] == "Budget_2026.xlsx is open in Excel. Change it anyway?"
    assert [o["label"] for o in card["options"]] == ["Change it anyway", "Don't change it"]
    assert "Sara" not in card["detail"]                          # the person reading it is "you"
    sent = calls("session/prompt")[-1]["params"]["prompt"][0]["text"]
    assert "Do not change, replace or delete it in this turn" in sent


def test_the_open_file_card_is_not_asked_for_a_question_or_a_closed_file(fake):
    make, calls, proj = fake
    _workbook(proj / "Budget_2026.xlsx")
    (proj / "~$Budget_2026.xlsx").write_bytes(b"lock")
    a = make(mode="ask")
    a.send("Which cost line in Budget_2026.xlsx grew fastest from Q1 to Q3?")
    a.send("I closed Excel, so please remove the ~$Budget_2026.xlsx lock file. Then add a Q4 column.")
    assert a.asked == []
    (proj / "~$Budget_2026.xlsx").unlink()
    a.send("Please fix the Total in Budget_2026.xlsx")
    assert a.asked == []


def test_a_command_that_saves_over_an_open_workbook_asks_even_in_full_access(fake):
    make, calls, proj = fake
    _workbook(proj / "Budget_2026.xlsx")
    (proj / "~$Budget_2026.xlsx").write_bytes(b"lock")
    a = make(mode="auto")
    assert a.send("WRITELOCKED now") == "asked-done"
    assert len(a.asked) == 1 and a.asked[0]["title"].startswith("Change Budget_2026.xlsx? It is open in Office")


# ---------- 5. the look covers the pages that changed, and claims no more ---------------------------------------

def test_the_look_picks_the_pages_the_request_names():
    from meshpool_harness.agent import look as L
    texts = ["Expense policy\nSection 1 Scope", "Section 2 Travel", "Section 3 Approval limits\nlimits limits",
             "more about approval limits and procedures", "Section 4 Receipts"]
    assert L.changed_pages("x.docx", texts, "In Expense_Policy.docx, shorten section 3 by about a third") == [3, 4]
    assert L.changed_pages("x.pptx", texts, "fix slide 5") == [5]
    assert L.changed_pages("x.docx", texts, "tidy it") == [1, 2]
    assert L.pages_phrase([3, 4], 5) == "pages 3 and 4 of 5"


def _policy(path: Path):
    from docx import Document
    d = Document()
    for s in range(1, 6):
        d.add_heading(f"Section {s} Topic {s}", 1)
        for i in range(12):
            d.add_paragraph(f"Section {s} paragraph {i}. " + "Spending rules apply to every claim and need a receipt. " * 3)
        d.add_page_break()
    d.save(str(path))


def test_the_look_draws_section_3_of_a_five_page_policy(tmp_path):
    if not _soffice():
        pytest.skip("LibreOffice is not available here")
    from meshpool_harness.agent import look as L
    f = tmp_path / "Expense_Policy.docx"
    _policy(f)
    res = L.render_doc(str(f), tmp_path / "look", "r1", request="shorten section 3 by about a third")
    assert not res.get("error"), res
    shown = res["pages"]["shown"]
    assert res["pages"]["total"] >= 5
    pages_with_s3 = [i + 1 for i, t in enumerate(L._pdf_texts(_pdf_of(f, tmp_path))) if "Section 3" in t]
    assert set(pages_with_s3) & set(shown), (shown, pages_with_s3)
    assert all(f"page {p} of" in lbl for p, (lbl, _) in zip(shown, res["shots"]))


def _pdf_of(f: Path, tmp: Path) -> Path:
    from engines.forge.common import soffice_convert
    out = tmp / "pdfout"
    out.mkdir(exist_ok=True)
    return Path(soffice_convert(str(f), "pdf", str(out)))


def test_the_checked_line_claims_only_what_it_saw():
    from meshpool_harness.agent import lookreview as LR
    from meshpool_harness import checks as K
    v = {"persona": "a policy owner or manager", "problems": [], "pass": True,
         "also": "The pages shown only cover section 1, which looks untouched and has no markup. I can't see section 3's tracked deletions"}
    st = {"persona": v["persona"], "evals": [v], "last": {"pages": {"shown": [1, 2], "total": 5}}}
    line = LR.summary_line("doc", st)
    assert "found nothing to fix" not in line and "want me to fix it" not in line, line
    merged = K.merge_line(line, "Checked that the file opens cleanly.")
    assert "all good" not in merged, merged
    # a clean look of 2 pages of 5 says so, and is not "all good"
    v2 = dict(v, also="")
    line2 = LR.summary_line("doc", {"persona": v["persona"], "evals": [v2], "last": {"pages": {"shown": [3, 4], "total": 5}}})
    assert "found nothing to fix on pages 3 and 4 of 5" in line2, line2
    assert "all good" not in K.merge_line(line2, "Checked that the file opens cleanly.")


def test_no_all_good_under_a_reply_that_ends_unsure():
    from meshpool_harness import checks as K
    look, checks = "Checked as a policy owner: found nothing to fix.", "Checked that the file opens cleanly."
    assert K.merge_line(look, checks).endswith(": all good.")
    q = "I shortened section 3.\n\nI can't see section 3's tracked deletions in the pictures. Want me to look again?"
    m = K.merge_line(look, checks, reply=q)
    assert "all good" not in m and m == "Checked it the way a policy owner would, and that the file opens.", m
    assert "all good" not in K.merge_line(look, checks, reply=["Done.", "Checked as x.\n\nI also noticed y, want me to fix it?"])


# ---------- 6. "you", never "she" ---------------------------------------------------------------------------------

def test_the_prompt_says_you_never_the_third_person():
    from meshpool_harness import assistant as AS
    assert 'the user as "you"' in AS.PLAIN and "third person" in AS.PLAIN
    assert 'the user as "you"' in AS.LEAN_RULES      # Pi's short rules keep within their 450-character budget
    assert "lock file" in AS.ADDRESS and "she" in AS.ADDRESS
    block = json.dumps(AS.build_context("fix the budget", rules=True, record=False, lookup=False), ensure_ascii=False)
    assert json.dumps(AS.ADDRESS, ensure_ascii=False)[1:-1] in block


# ---------- 7. a workbook card gets a thumbnail ------------------------------------------------------------------

def test_an_xlsx_card_gets_a_first_sheet_thumbnail(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    C.ensure_home()
    js = (UI / "preview.js").read_text(encoding="utf-8")
    rx = re.search(r"const PV_THUMB_RX = /(.+)/i;", js).group(1)
    assert re.search(rx, "Budget_2026.xlsx", re.I)
    thumb = js[js.index("async function pvThumb"):]
    assert "&thumb=1" in thumb[:thumb.index("\n}\n")]
    if not _soffice():
        pytest.skip("LibreOffice is not available here")
    from meshpool_harness.ui import previews as PV
    from meshpool_harness.ui import workbench as WB
    proj = tmp_path / "Board"
    proj.mkdir()
    _workbook(proj / "Budget_2026.xlsx")
    monkeypatch.setattr(WB, "check_root", lambda r: Path(r).resolve(), raising=False)
    assert PV.meta(str(proj), "Budget_2026.xlsx")["kind"] == "sheet"          # the viewer still shows rows and columns
    for _ in range(240):
        m = PV.meta(str(proj), "Budget_2026.xlsx", thumb=True)
        if m.get("state") != "working":
            break
        time.sleep(0.25)
    assert m["kind"] == "pages" and m["state"] == "ready" and m["pages"] >= 1, m
    png = PV.page_png(str(proj), "Budget_2026.xlsx", 0, 320)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------- 8. a long wait says the last step ---------------------------------------------------------------------

def test_a_long_wait_names_the_last_step():
    js = (UI / "app.js").read_text(encoding="utf-8")
    note = js[js.index("function waitNote("):]
    note = note[:note.index("\n}\n")]
    assert "Last step: " in note and "Waiting for the model's next move" in note
    assert "cur.lastStep = ev.ok" in js
