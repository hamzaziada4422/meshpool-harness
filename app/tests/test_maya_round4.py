"""Round 4 (0.0.20 candidate, e1dad99e): what kept Maya (a florist, Chat mode) at 7.5 of 10, with the items Lina and
Priya hit in the same areas.

1. Follow-up turns said "the tool I use to edit your page wasn't available just now" with no call, though 24 tools
   were offered. The cause was the test adapter (pictures turned Claude Code's Read tool on); meshpool now sends such
   a reply back once, with the tools' names. "I'm fixing that line and running it again." also counts as a promise.
2. Chat mode reads office files with read_document (no approval), and changes them with edit_office_file.
3. A chart request in Chat mode is a picture, not a quick text answer.
4. The footer names the open point ("1 to look at: legend sits on ..."); a reply unsure of its own check is not
   "all good".
5. One card for one picture in two formats, the lowercase brand, and the title after Undo.

Scripted model and fake tools: no model calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshpool_harness import run_task  # noqa: E402
from meshpool_harness.agent import core as A  # noqa: E402
from test_rescore2_behaviour import _idle, _model, chat, home  # noqa: E402,F401

MAYA_NO_TOOL = ("I couldn't connect the form to maya@mayasflowers.example, because the tool I use to edit your page "
                "wasn't available just now. Your page hasn't changed, and orders still don't reach you.\n\n"
                "Can you ask me again in a moment?")
LINA_PROMISE = ("The first attempt stopped on a header-styling error before it saved anything, so the workbook is "
                "still untouched. I'm fixing that line and running it again.")
MAYA_READ = ("cat index.html; echo ----; mkdir -p /tmp/x && cd /tmp/x && cp \"/home/maya/Maya's Flowers/Bouquet price "
             "list.xlsx\" a.xlsx && unzip -o -q a.xlsx && cat xl/sharedStrings.xml; echo; cat xl/worksheets/sheet1.xml")
LINA_EDIT = ("cp region_summary.xlsx /tmp/s/backup.xlsx && cat > /tmp/s/build.py <<'EOF'\nimport sys, openpyxl\n"
             "path = sys.argv[1]\nwb = openpyxl.load_workbook(path)\nd = wb['Data']\n"
             "d.column_dimensions['B'].width = 14\n"
             "d.freeze_panes = 'A2'\nwb.save(path)\nEOF\npython3 -I /tmp/s/build.py region_summary.xlsx")


def _script(monkeypatch, msgs, seen):
    it = iter(msgs)

    def fake_chat(self, turn):
        turn["calls"] += 1
        seen.append(json.loads(json.dumps(self.messages)))
        return next(it), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)


def _text(msgs) -> str:
    out = []
    for m in msgs:
        c = m.get("content")
        out.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict)))
    return "\n".join(out)


def _call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


# ------------------------------------------------------------------------------------------ 1: "tool unavailable"
@pytest.mark.parametrize("reply", [
    MAYA_NO_TOOL,
    "I couldn't fix the date column in `region_summary.xlsx`. None of my file tools were available this turn.",
    "I couldn't make the fix, because I have no way to edit files in this session... I don't have file tools.",
    "I couldn't run the check. The tool I need to run pandas isn't available to me in this chat.",
    "I couldn't fix the chart, because I can't run commands in this session. I only have a tool for reading files.",
    "The **tools I need to edit your page** wouldn't run just now, so `index.html` is unchanged.",
])
def test_replies_that_say_the_tools_are_unavailable_are_recognised(reply):
    assert A.claims_no_tools(reply), reply


@pytest.mark.parametrize("reply", [
    "I made the chart and saved it in your folder.",
    "Your page is ready. The order form sends each order to your email.",
    "The tool checked the page on a phone, a tablet and a desktop: all fine.",
    "No change was needed: the file already has the 12 bouquets.",
])
def test_ordinary_replies_are_not_taken_for_a_missing_tool(reply):
    assert not A.claims_no_tools(reply), reply


def test_a_reply_that_says_its_tool_is_unavailable_is_sent_back_once_with_the_tools(tmp_path, monkeypatch):
    """Maya: "Send the orders to maya@mayasflowers.example please" got "the tool I use to edit your page wasn't
    available just now" twice, with no call, while 24 tools were offered."""
    (tmp_path / "index.html").write_text("<form></form>")
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": MAYA_NO_TOOL},
                          _call("read_file", path="index.html"),
                          {"role": "assistant", "content": "I connected the form to maya@mayasflowers.example."}], seen)
    r = run_task("Send the orders to maya@mayasflowers.example please", workdir=str(tmp_path), engine="agent")
    assert r["answer"].strip().endswith("I connected the form to maya@mayasflowers.example."), r
    notes = [m["content"] for m in seen[-1]
             if m.get("role") == "user" and "Your tools are available" in str(m.get("content"))]
    assert len(notes) == 1, _text(seen[-1])[-800:]
    assert notes[0].startswith("[Meshpool] Your tools are available: ")
    assert notes[0].endswith("Use them to do the change.")
    assert "read_file" in notes[0], notes[0]


def test_the_tools_note_is_given_once_per_turn(tmp_path, monkeypatch):
    seen = []
    no = {"role": "assistant", "content": "None of my file tools were available this turn."}
    _script(monkeypatch, [no, dict(no), dict(no)], seen)
    run_task("Freeze the top row", workdir=str(tmp_path), engine="agent")
    assert sum("Your tools are available" in _text([m]) for m in seen[-1]) == 1


def test_the_look_review_fix_round_runs_after_a_tool_unavailable_reply(tmp_path, monkeypatch):
    """The chart's legend covered two bars. The fix round answered "the tool I need to re-run it isn't available"
    and the turn ended: now that reply is sent back and the fix runs."""
    (tmp_path / "chart.py").write_text("print('chart')")
    from meshpool_harness.agent import look as L
    rounds = []

    def gate(agent, g, final, **k):
        if not rounds:
            rounds.append(1)
            agent.messages.append({"role": "user", "content": "[meshpool] The legend sits on top of two bars. Fix it."})
            return True, final
        return False, final
    monkeypatch.setattr(L, "gate", gate)
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": "I made the chart."},
                          {"role": "assistant", "content": "I couldn't fix the chart. The tool I need to re-run it "
                                                           "isn't available in this session."},
                          _call("read_file", path="chart.py"),
                          {"role": "assistant", "content": "I moved the key below the chart."}], seen)
    r = run_task("Make a chart of my prices", workdir=str(tmp_path), engine="agent")
    assert r["answer"].strip().endswith("I moved the key below the chart."), r
    assert any("Your tools are available" in _text([m]) for m in seen[-1])
    assert any(m.get("tool_calls") for m in seen[-1][-4:]), "the fix round made a call"


def test_a_new_turn_carries_no_pictures_from_an_earlier_look(tmp_path, monkeypatch):
    """Lina, Sara: every follow-up after a look review carried its two page pictures (n_images 2 on each request)."""
    png = "data:image/png;base64,iVBORw0KGgo="
    a = A.Agent(str(tmp_path))
    a.messages += [{"role": "user", "content": "Build region_summary.xlsx"},
                   {"role": "assistant", "content": "I built it."},
                   {"role": "user", "content": [
                       {"type": "text", "text": "[meshpool] Before you call this done, look at what you made. ..."},
                       {"type": "image_url", "image_url": {"url": png}}]},
                   {"role": "assistant", "content": "The date column is narrow."},
                   {"role": "user", "content": [{"type": "text", "text": "Here is my mockup"},
                                                {"type": "image_url", "image_url": {"url": png}}]},
                   {"role": "assistant", "content": "Noted."}]
    seen = []
    _script(monkeypatch, [{"role": "assistant", "content": "Done."}], seen)
    a.send("Widen the date column")
    sent = seen[0]
    pics = [m for m in sent if isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])]
    assert len(pics) == 1 and "mockup" in json.dumps(pics[0]), "the user's own picture stays, the look's goes"
    assert any("Before you call this done" in _text([m]) for m in sent), "the review's words stay"


def test_work_said_to_be_under_way_is_a_promise():
    """Lina: "I'm fixing that line and running it again." ended the turn, and Progress said Done."""
    assert A._announces_action(LINA_PROMISE)
    assert A._announces_action("I'm saving a backup copy first.")
    assert not A._announces_action("I'm happy with how the chart looks.")
    assert not A._announces_action("If you want, I'm happy to add a second chart.")


def test_a_web_chat_that_stops_on_work_under_way_is_nudged(chat, monkeypatch):
    """The web Session path (meshpool Work, Codex style): the promise nudge fires before the reply is trimmed."""
    S, proj, made = chat
    seen = _model(monkeypatch, [{"role": "assistant", "content": LINA_PROMISE},
                                {"role": "assistant", "content": "I froze the top row of the Data sheet."}])
    v = S.create({"kind": "cowork", "harness": "meshpool-work", "task": "freeze", "workdir": str(proj)})
    made.append(v["id"])
    s = S.LIVE[v["id"]]
    s.send("Freeze the top row on the Data sheet of region_summary.xlsx")
    _idle(s)
    assert len(seen) >= 2 and A.ACT_NUDGE in json.dumps(seen[1]), [json.dumps(b)[-400:] for b in seen]
    replies = [e["text"] for e in s.events if e.get("type") == "assistant" and e.get("text")]
    assert replies[-1] == "I froze the top row of the Data sheet.", replies


def test_a_web_chat_that_says_its_tools_are_missing_is_nudged(chat, monkeypatch):
    S, proj, made = chat
    seen = _model(monkeypatch, [{"role": "assistant", "content": MAYA_NO_TOOL},
                                {"role": "assistant", "content": "I connected the form to your email."}])
    v = S.create({"kind": "cowork", "harness": "meshpool-work", "task": "orders", "workdir": str(proj)})
    made.append(v["id"])
    s = S.LIVE[v["id"]]
    s.send("Send the orders to maya@mayasflowers.example please")
    _idle(s)
    assert len(seen) >= 2 and "Your tools are available" in json.dumps(seen[1]), [json.dumps(b)[-400:] for b in seen]


# ------------------------------------------------------------------------------------------ 2: office files in Chat
def _chat_agent(tmp_path, monkeypatch, names=("exec_command", "mcp__meshpool-office__read_document",
                                              "mcp__meshpool-office__edit_office_file")):
    a = A.Agent(str(tmp_path))
    a.chat_mode = True
    monkeypatch.setattr(A.Agent, "tool_schemas", lambda self: [{"type": "function", "function": {"name": n}}
                                                               for n in names])
    return a


def test_chat_mode_reads_an_office_file_with_the_reader_not_unzip(tmp_path, monkeypatch):
    """Maya: "cat; cp; unzip" on her price list asked "Run 5 steps: cat, cp, unzip?"."""
    a = _chat_agent(tmp_path, monkeypatch)
    steer = a._office_read_steer("exec_command", {"cmd": MAYA_READ})
    assert steer.startswith("Not run:") and "mcp__meshpool-office__read_document" in steer and "no approval" in steer
    look = ("python3 -I -c \"import openpyxl;wb=openpyxl.load_workbook('Bouquet price list.xlsx');"
            "[print(r) for r in wb.active.iter_rows(values_only=True)]\"")
    assert a._office_read_steer("exec_command", {"cmd": look})
    chart = ("python3 -c \"import openpyxl, matplotlib; wb=openpyxl.load_workbook('Bouquet price list.xlsx'); "
             "plt.savefig('Bouquet price chart.png')\"")
    assert a._office_read_steer("exec_command", {"cmd": chart}) == "", "a chart made from the workbook still runs"
    assert a._office_read_steer("exec_command", {"cmd": "ls -la; cat index.html"}) == ""
    a.chat_mode = False
    assert a._office_read_steer("exec_command", {"cmd": MAYA_READ}) == "", "the Code tab is unchanged"


def test_without_the_reader_the_command_runs(tmp_path, monkeypatch):
    a = _chat_agent(tmp_path, monkeypatch, names=("exec_command",))
    assert a._office_read_steer("exec_command", {"cmd": MAYA_READ}) == ""


def test_chat_mode_changes_a_workbook_the_user_has_with_the_editor(tmp_path, monkeypatch):
    """Lina: "freeze the top row" went to a hand-written openpyxl script that rewrote region_summary.xlsx."""
    a = _chat_agent(tmp_path, monkeypatch)
    steer = a._office_read_steer("exec_command", {"cmd": LINA_EDIT})
    assert "mcp__meshpool-office__edit_office_file" in steer and "set_column_width" in steer, steer
    derived = LINA_EDIT.replace("wb.save(path)", "wb.save('region_summary_sorted.xlsx')")
    assert a._office_read_steer("exec_command", {"cmd": derived}) == "", "a new file made from it still runs"


def test_the_editor_sets_a_column_width(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))       # a file in the project is changed in place
    import openpyxl
    from meshpool_harness.agent import office_mcp_server as O
    p = tmp_path / "region_summary.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Data"
    wb.active.append(["Order", "Date"])
    wb.save(p)
    out, err = O.edit_office_file(str(p), [{"op": "set_column_width", "sheet": "Data", "column": "B", "width": 14},
                                           {"op": "freeze_panes", "sheet": "Data", "cell": "A2"}])
    assert not err, out
    ws = openpyxl.load_workbook(p)["Data"]
    assert ws.column_dimensions["B"].width == 14 and ws.freeze_panes == "A2"


def test_a_tracked_cut_reports_the_words_the_reader_sees(tmp_path, monkeypatch):
    """Sara: "shorten section 3 by about a third" lost half its words, and the reply said a third."""
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    import docx
    from meshpool_harness.agent import office_mcp_server as O
    p = tmp_path / "Expense_Policy.docx"
    d = docx.Document()
    d.add_paragraph("Approval limits apply to every claim. Managers approve claims up to five hundred dollars.")
    d.add_paragraph("Directors approve larger claims.")
    d.save(p)
    out, err = O.edit_office_file(str(p), [{"op": "tracked_delete", "find": " Managers approve claims up to five "
                                                                          "hundred dollars."}])
    assert not err, out
    assert "Words the reader sees: 10, down from 18 (8 fewer)" in out, out
    assert "give the real figure" in out


def test_a_fix_round_answer_leads_with_what_was_made():
    """Sara: the fix round's reply led with "I couldn't fix the ### problem" though the rest was done."""
    from meshpool_harness.agent import look as L
    assert "led by what you made and where it is" in L.TAIL and "comes after that" in L.TAIL


def test_chat_mode_keeps_the_reader_in_a_coding_chat(tmp_path, monkeypatch):
    """A Code-profile chat in Chat mode had no office tools until a message named an office file, so the price list
    in the folder was unzipped by hand."""
    class Mcp:
        def schemas(self):
            return [{"type": "function", "function": {"name": f"mcp__meshpool-office__{n}"}}
                    for n in ("read_document", "make_deck", "edit_office_file")]

        def builtin_tool_names(self, mod):
            return set()
    a = A.Agent(str(tmp_path), mcp=Mcp())
    monkeypatch.setattr(A.Agent, "_codex", lambda self: True)
    monkeypatch.setattr(A.Agent, "_c171", lambda self: False)
    monkeypatch.setattr(A.Agent, "_office_direct", lambda self: False)
    names = lambda: [t["function"]["name"] for t in a.tool_schemas()]   # noqa: E731
    assert not [n for n in names() if "meshpool-office" in n]
    a.chat_mode = True
    assert [n for n in names() if "meshpool-office" in n] == ["mcp__meshpool-office__read_document"]


def test_chat_mode_context_says_to_read_office_files_with_the_reader():
    from meshpool_harness import assistant as AS
    on = AS.build_context("make me a page", chat=True, office=True, lookup=False)["text"]
    assert AS.CHAT_MODE_OFFICE in on and "read_document" in on
    assert AS.CHAT_MODE_OFFICE not in AS.build_context("make me a page", chat=False, office=True, lookup=False)["text"]
    assert AS.CHAT_MODE_OFFICE not in AS.build_context("x", chat=True, office=False, lookup=False)["text"]


# ------------------------------------------------------------------------------------------ 3: a chart is a picture
def test_chat_mode_asks_for_a_chart_as_a_picture():
    from meshpool_harness import assistant as AS
    assert "chart or graph" in AS.CHAT_MODE and "PNG" in AS.CHAT_MODE and "never draw it in text" in AS.CHAT_MODE


def test_a_chart_request_is_not_a_quick_answer():
    """"Can you make me a simple chart..." was a quick request, whose note says "Do not create or change files", so
    the chart came as bars of text."""
    from meshpool_harness import pace as PACE
    ask = ("Can you make me a simple chart comparing the prices of my 12 bouquets, so I can see which are the cheapest "
           "and the most expensive?")
    assert PACE.classify(ask) == "normal"
    assert PACE.classify("Draw me a graph of my sales by month") == "normal"
    assert PACE.classify("What is the capital of Peru?") == "quick"


# ------------------------------------------------------------------------------------------ 4: the footer names it
def test_the_footer_names_the_one_open_point():
    from meshpool_harness import checks as CK
    look = ("Checked as a customer or shopper at Maya's Flowers: fixed nothing. Still open: legend sits on top of the "
            "two most expensive bars.\n\nI also noticed the title is off-centre toward the right of the page, want me "
            "to fix it?")
    short, full = CK.footer(look, {"line": ""})
    assert short == "Checked · 1 to look at: legend sits on top of the two most expensive bars", short
    short, _ = CK.footer("", {"line": "Checked the formulas. One thing to look at: some formulas show errors."})
    assert short == "Checked · 1 to look at: some formulas show errors", short
    long = ("Checked as a guest: fixed nothing. Still open: the sign-up button says thank you but saves nothing "
            "anywhere for the guests who sign up (where: the form at the bottom of the page).")
    short, _ = CK.footer(long, {"line": ""})
    assert short.startswith("Checked · 1 to look at: the sign-up button") and short.endswith("...") and \
        len(short) < 100 and "where:" not in short, short
    short, _ = CK.footer("", {"line": "Checked the deck. Things to look at: a; b."})
    assert short == "Checked · 2 to look at", short


def test_a_reply_unsure_of_its_own_check_is_not_all_good():
    """Priya: "I haven't opened it to confirm... My check of the workbook didn't finish." over "Checked · all good"."""
    from meshpool_harness import checks as CK
    reply = ("I built `sales-analysis.xlsx` in your Studio folder, but I haven't opened it to confirm the summary "
             "formulas and the chart display correctly. My check of the workbook didn't finish.\n\n"
             "**What's in the workbook**\n- Notes, Cleaned Data and Summary.\n\nTotal revenue is about 143,793.")
    look = "Checked as a sales manager: found nothing to fix on pages 1 and 2 of 5."
    assert CK.open_doubt(reply)
    assert CK.footer(look, {"line": ""}, reply=[reply])[0] == "Checked"
    for s in ("My check of the workbook didn't finish.", "I haven't opened the file to look at the charts.",
              "I couldn't check the layout.", "The charts are not looked at yet.", "How they render is unseen.",
              "So how they look is unchecked.", "I haven't seen how they render."):
        assert CK.open_doubt("Done.\n\n" + s), s
    for s in ("I made the chart and saved it in your folder.", "The workbook opens and every total matches the CSV."):
        assert not CK.open_doubt(s), s


# ------------------------------------------------------------------------------------------ 5: polish
def test_receipts_name_meshpool_in_lowercase():
    from meshpool_harness import receipts as RC
    assert RC._harness_name("meshpool-code") == "meshpool Code"
    assert RC._harness_name("meshpool-work") == "meshpool Work"


def test_the_start_line_says_meshpool_in_lowercase():
    src = (Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui" / "server.py").read_text(encoding="utf-8")
    assert 'print("  meshpool is running.")' in src and "Meshpool is running" not in src


def test_undo_puts_the_title_back(chat, monkeypatch):
    """Maya: after Undo the chat was back on the invoice question but still read "Bouquet price list"."""
    S, proj, made = chat
    _model(monkeypatch, [{"role": "assistant", "content": "You owe $632.88, due on 3 December."},
                         {"role": "assistant", "content": "I made your price list."}])
    v = S.create({"kind": "cowork", "harness": "meshpool-work", "task": "invoice", "workdir": str(proj)})
    made.append(v["id"])
    s = S.LIVE[v["id"]]
    s.send("How much do I owe my flower supplier?")
    _idle(s)
    s.send("Make me a price list with 12 bouquets")
    _idle(s)
    first = s.view(0)["title"]
    S.write_meta(s.id, title="Bouquet price list", auto_title=True)     # what the turn's file named the chat
    assert s.view(0)["title"] == "Bouquet price list"
    s.revert(2)
    assert s.view(0)["title"] == first, (first, s.view(0)["title"])
    S.write_meta(s.id, title="My own name", renamed=True)
    s.revert(1)
    assert s.view(0)["title"] == "My own name", "a name the user gave stays"
