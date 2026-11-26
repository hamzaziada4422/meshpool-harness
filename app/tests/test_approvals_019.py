"""0.0.19 approvals: plain, the same rule everywhere, and only for real risk. Persona testers (Sara, Maya, Omar, Priya)
scored the release candidate 5 to 6 out of 10, and approvals were the top complaint of four of them. Each test names
the item it covers; the sandbox-auto safety rules (secrets hidden, home read-only, git hooks and config locked,
deletes and the network ask) stay in test_sandbox_auto.py and test_sandbox_auto_review.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import tui_session as TS  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402
from meshpool_harness.core import sandbox as SB  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402

UI = Path(core.__file__).resolve().parents[1] / "ui"
OFFICE = "mcp__meshpool-office__"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1")
    monkeypatch.delenv("MH_SANDBOX", raising=False)
    monkeypatch.setattr(core.Agent, "_mcp_destructive", lambda self, n: False)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    home = tmp_path / "home"
    p = home / "work"
    p.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return p


def _agent(root, monkeypatch, sandbox=True, mode="ask", answer=("no", "")):
    monkeypatch.setattr(SB, "available", lambda *a, **k: sandbox)
    asked = []

    def ap(kind, title, detail, scope=""):
        asked.append({"kind": kind, "title": title, "detail": detail, "scope": scope})
        return answer
    return Agent(str(root), mode=mode, approve=ap), asked


# ---------------------------------------------------------------------------------------- 1: office cards, plain
def _price_list(p: Path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Prices"
    ws.append(["Bouquet", "What's in it", "Price"])
    names = ["Red roses", "Spring tulips", "Sunflower sunshine", "Bridal", "Bridesmaid posy", "Birthday brights",
             "Peony and rose", "Lily and eucalyptus", "Mixed seasonal", "Orchid", "Daisy chain", "Lavender"]
    for i, n in enumerate(names):
        ws.append([n, "flowers", 69 + i])
        ws.cell(row=i + 2, column=3).number_format = '"$"#,##0.00'
    wb.save(p)


def test_1_a_price_change_reads_as_prices_with_the_first_one_named(proj):
    _price_list(proj / "Bouquet Price List.xlsx")
    args = {"path": "Bouquet Price List.xlsx", "in_place": True,
            "operations": [{"op": "set_range", "sheet": "Prices", "start": "C2",
                            "values": [[round((69 + i) * 1.1, 2)] for i in range(12)]}]}
    title, lines = core.office_card(OFFICE + "edit_office_file", args, proj)
    assert title == "Change Bouquet Price List.xlsx: 12 prices (Red roses $69 to $75.90, and 11 more)"
    assert "Price, C2 (Red roses): $69 to $75.90" in lines and "set_range" not in " ".join(lines)
    detail = core.office_detail(OFFICE + "edit_office_file", args, proj)
    plain, call = detail.split(core.DETAILS_MARK)
    assert "{" not in plain and json.loads(call)["operations"][0]["op"] == "set_range"     # the exact call, folded


def test_1_a_formula_fix_reads_as_a_fix(proj):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget 2026"
    ws.append(["Line", "Q1", "Q2", "Q3", "Total"])
    for r in range(2, 8):
        ws.append([f"Line {r}", 1, 2, 3, f"=B{r}+C{r}"])
    wb.save(proj / "Budget_2026.xlsx")
    args = {"path": "Budget_2026.xlsx", "operations": [
        {"op": "set_cell", "sheet": "Budget 2026", "cell": f"E{r}", "value": f"=SUM(B{r}:D{r})"} for r in range(2, 8)]}
    title, lines = core.office_card(OFFICE + "edit_office_file", args, proj)
    assert title == "Fix 6 Total formulas in Budget_2026.xlsx"
    assert lines[0] == "Total, E2 (Line 2): =B2+C2 to =SUM(B2:D2)"
    assert lines[-1] == "It changes the file itself. Undo can put it back."      # in_place left out: in the folder


def test_1_tracked_deletions_read_as_shortening_a_section(proj):
    from docx import Document
    d = Document()
    d.add_heading("2. Travel", level=1)
    d.add_paragraph("Book trains early.")
    d.add_heading("3. Approval limits and procedures", level=1)
    long = ["Managers approve up to 500 euros and keep the receipts for seven years in the finance folder.",
            "Directors approve anything above that after a written request with three quotes attached.",
            "All approvals are logged in the expense tool before the end of the month in which they are made."]
    for t in long:
        d.add_paragraph(t)
    d.save(proj / "Policy.docx")
    args = {"path": "Policy.docx", "operations": [{"op": "tracked_delete", "find": t} for t in long]}
    title, lines = core.office_card(OFFICE + "edit_office_file", args, proj)
    assert title == "Shorten section 3 of Policy.docx as tracked changes (3 deletions)"
    assert all(len(x) < 110 for x in lines)                    # no paragraph-long "find" strings on the card


@pytest.mark.parametrize("tool,args,want", [
    ("make_spreadsheet", {"request": "Price list spreadsheet for 12 bouquets. Columns: ... Use sensible prices",
                          "save_as": "Bouquet Price List.xlsx"}, "Make a new spreadsheet, Bouquet Price List.xlsx"),
    ("convert_document", {"path": "Board_Update_Q3.pptx", "to": "pdf"}, "Make a PDF of Board_Update_Q3.pptx"),
    ("add_slides", {"deck": "decks/Q3.pptx", "request": "two slides on risks"}, "Add slides to Q3.pptx"),
    ("edit_office_file", {"path": "deck.pptx", "operations": [{"op": "set_notes", "slide": 2, "text": "x"}]},
     "Change deck.pptx: 1 edit"),
])
def test_1_other_office_tools_say_what_they_make_without_the_brief(proj, tool, args, want):
    title, _ = core.office_card(OFFICE + tool, args, proj)
    assert title == want
    step = core._summary(OFFICE + tool, args)                   # the step row: the same words, never the JSON
    assert step == want and "{" not in step and "sensible" not in step


def test_1_the_web_card_folds_the_exact_call_under_details():
    js = (UI / "app.js").read_text()
    assert 'split("\\n\\nDetails:\\n")' in js and "<summary>Details</summary>" in js
    assert core.DETAILS_MARK == "\n\nDetails:\n"


# ---------------------------------------------------------------------------------------- 2: which office actions ask
NEW_FILES = [("make_spreadsheet", {"request": "a price list", "save_as": "prices.xlsx"}),
             ("make_document", {"markdown": "# Update\nOn schedule.", "filename": "update.docx"}),
             ("convert_document", {"path": "Board_Update_Q3.pptx", "to": "pdf"}),
             ("make_deck", {"request": "a deck"}),
             ("edit_office_file", {"path": "Budget.xlsx", "operations": [{"op": "set_cell", "cell": "A1", "value": 1}]})]


def _office_files(proj):
    for n in ("Board_Update_Q3.pptx", "Budget.xlsx"):
        (proj / n).write_bytes(b"x")


def test_2_new_files_and_undoable_edits_in_the_folder_do_not_ask(proj, monkeypatch):
    """Priya and Sara were asked to make new files in their own folder and to convert a deck to PDF (twice)."""
    _office_files(proj)
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    for tool, args in NEW_FILES:
        assert a._permit(OFFICE + tool, args) == (True, ""), tool
    assert a._permit("office_task", {"engine": "sheets", "request": "x", "files": ["Budget.xlsx"]}) == (True, "")
    assert asked == []
    b, asked_b = _agent(proj, monkeypatch, sandbox=False, mode="edits")      # Accept edits, no sandbox: the same
    for tool, args in NEW_FILES:
        assert b._permit(OFFICE + tool, args)[0], tool
    assert asked_b == []


def test_2_before_every_command_and_change_asks_for_all_of_them(proj, monkeypatch):
    _office_files(proj)
    monkeypatch.setenv("MH_SANDBOX_AUTO", "0")
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    for tool, args in NEW_FILES:
        assert a._permit(OFFICE + tool, args)[0] is False, tool
    assert a._permit("office_task", {"engine": "docs", "request": "x"})[0] is False      # the engines too
    assert len(asked) == len(NEW_FILES) + 1
    assert all("{" not in x["title"] for x in asked)


def test_2_without_a_sandbox_the_one_yes_for_the_folder_covers_office_files(proj, monkeypatch):
    _office_files(proj)
    a, asked = _agent(proj, monkeypatch, sandbox=False, answer=("always", ""))
    assert a._permit(OFFICE + "make_spreadsheet", {"request": "x"})[0]
    assert asked[0]["scope"].startswith("ordinary commands and edits in this folder")
    for tool, args in NEW_FILES:
        assert a._permit(OFFICE + tool, args)[0], tool
    assert a._permit("bash", {"command": "python3 x.py"})[0]
    assert len(asked) == 1                                      # one rule for shell work and office files


def test_2_an_open_file_outside_or_unrestorable_still_asks(proj, tmp_path, monkeypatch):
    _office_files(proj)
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    edit = {"path": "Budget.xlsx", "in_place": True, "operations": [{"op": "set_cell", "cell": "A1", "value": 1}]}
    (proj / "~$Budget.xlsx").write_bytes(b"")                   # Excel has it open
    assert a._permit(OFFICE + "edit_office_file", edit)[0] is False
    assert asked[-1]["title"].startswith("Budget.xlsx is open in Office.") and asked[-1]["scope"] == "once"
    (proj / "~$Budget.xlsx").unlink()
    assert a._permit(OFFICE + "edit_office_file", edit)[0]                       # closed again: no question
    out = dict(edit, in_place=None, output=str(tmp_path / "elsewhere.xlsx"))
    out.pop("in_place")
    assert a._permit(OFFICE + "edit_office_file", out)[0] is False
    assert "outside the project folder" in asked[-1]["title"]
    (proj / ".gitignore").write_text("Budget.xlsx\n")           # Undo does not keep it: asks
    assert a._permit(OFFICE + "edit_office_file", edit)[0] is False
    assert "Undo cannot put it back" in asked[-1]["title"]
    (proj / ".gitignore").unlink()
    assert a._permit(OFFICE + "read_document", {"path": str(tmp_path / "x.pdf")})[0] is False   # reading outside


# ---------------------------------------------------------------------------------------- 3: URLs are not the network
XML_SCRIPT = ("python3 - <<'EOF'\nimport zipfile, re\nNS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/"
              "2006/main'}\nz = zipfile.ZipFile('Expense_Policy.docx')\nprint(len(z.read('word/document.xml')))\nEOF")
COMMIT = ('git add a.py && git commit -q -m "Add currency" -m "Claude-Session: https://claude.ai/code/session_x" '
          '&& git log --oneline -1')


@pytest.mark.parametrize("cmd", [XML_SCRIPT, COMMIT, "echo 'see https://example.com/docs' > notes.md",
                                 "python3 -c \"print('https://x.org')\"",
                                 "grep -rn websocket src", "python3 -c \"import urllib.parse as u; print(u.quote('a b'))\""])
def test_3_a_url_in_code_or_text_is_not_network_use(proj, cmd):
    assert PR.sandbox_auto_ok(cmd, proj), cmd
    assert not core._plain_command_title(cmd).startswith("Use the internet"), cmd


@pytest.mark.parametrize("cmd", [
    "curl -s https://api.example.com/x", "wget https://x.org/f.zip", "pip install pandas",
    "python3 -c \"import requests; print(requests.get('https://api.x.com').text)\"",
    "python3 - <<'EOF'\nfrom urllib.request import urlopen\nprint(urlopen('https://x.org').read())\nEOF",
    "python3 -c \"import pandas as pd; pd.read_csv('https://x.org/d.csv')\"",
    "node -e \"fetch('https://x.org').then(r => r.text())\"", "git push origin main", "nc evil 80",
])
def test_3_commands_that_really_reach_the_network_still_ask(proj, cmd):
    assert not PR.sandbox_auto_ok(cmd, proj), cmd
    assert PR.NETWORK.search(cmd), cmd


def test_3_the_xml_namespace_script_runs_without_a_card(proj, monkeypatch):
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    assert a._permit("exec_command", {"cmd": XML_SCRIPT}) == (True, "") and asked == []


# ---------------------------------------------------------------------------------------- 4: a patch, then commands
PATCH_THEN_TESTS = ("apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: hello.txt\n+hello\n*** End Patch\nEOF\n"
                    "python3 -c \"print('tests ran')\"")


def test_4_a_patch_then_commands_is_split(proj, monkeypatch):
    sp = T.split_patch_script(PATCH_THEN_TESTS)
    assert sp[0].startswith("*** Begin Patch") and sp[2] == "python3 -c \"print('tests ran')\"" and sp[3] is False
    assert T.split_patch_script("cd sub && apply_patch <<EOF && make\n*** Begin Patch\n*** End Patch\nEOF")[2:] == \
        ("cd sub && make", True)
    assert T.split_patch_script("apply_patch <<EOF | tee x\n*** Begin Patch\n*** End Patch\nEOF") is None
    # Accept edits, no sandbox: the patch is an edit (no question), the command its own question, named plainly
    monkeypatch.setenv("MH_SANDBOX_AUTO", "0")
    a, asked = _agent(proj, monkeypatch, sandbox=False, mode="edits")
    assert a._permit("exec_command", {"cmd": PATCH_THEN_TESTS})[0] is False
    assert len(asked) == 1 and asked[0]["kind"] == "bash"
    assert asked[0]["title"] == "Run a Python step?" and "steps" not in asked[0]["title"]
    assert "apply_patch" not in asked[0]["detail"]


def test_4_in_the_sandbox_neither_part_asks_and_both_run(proj, monkeypatch):
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    assert a._permit("exec_command", {"cmd": PATCH_THEN_TESTS}) == (True, "") and asked == []
    monkeypatch.setattr(SB, "available", lambda *a, **k: False)       # run it here without bubblewrap
    out = a.ws.exec_command(PATCH_THEN_TESTS)
    assert (proj / "hello.txt").read_text() == "hello\n" and "tests ran" in out and "A hello.txt" in out
    out = a.ws.shell(["bash", "-lc", PATCH_THEN_TESTS.replace("hello.txt", "two.txt")])
    assert (proj / "two.txt").exists() and "tests ran" in out


def test_4_a_patch_outside_the_folder_still_asks_as_an_edit(proj, tmp_path, monkeypatch):
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    script = PATCH_THEN_TESTS.replace("hello.txt", str(tmp_path / "out.txt"))
    assert a._permit("exec_command", {"cmd": script})[0] is False
    assert asked[0]["kind"] == "edit" and "outside the project folder" in asked[0]["title"]
    assert asked[0]["scope"] == "once"                        # a yes here never covers later edits outside


# ---------------------------------------------------------------------------------------- 5: local git writes
def _repo(p: Path):
    run = lambda *c: subprocess.run(["git", "-C", str(p), *c], check=True, capture_output=True)   # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (p / "a.py").write_text("x = 1\n")
    run("add", "a.py")
    run("commit", "-q", "-m", "first")
    (p / "a.py").write_text("x = 2\n")


GIT_OK = [COMMIT, "git add a.py", "git commit -am 'fix rounding'", "git stash", "git stash pop",
          "git reset --soft HEAD~1", "git restore --staged a.py", "git restore a.py", "git checkout a.py",
          "git checkout -b feature/x", "git switch -c topic", "git branch topic2",
          "git status --short && git add a.py && git commit -q -m x"]
GIT_ASK = ["git push origin main", "git pull", "git fetch", "git clone https://x.org/r.git", "git reset --hard",
           "git clean -fd", "git rebase main", "git filter-branch --all", "git config core.hooksPath x",
           "echo x > .git/hooks/pre-commit", "git checkout main", "git branch -D topic", "git stash drop",
           "git add ../other.py", "git commit --amend -m y && git push"]


def test_5_local_git_writes_run_in_accept_edits(proj, monkeypatch):
    """Omar: `git add` and `git commit` were refused in Accept edits, a whole task lost."""
    _repo(proj)
    monkeypatch.setenv("MH_SANDBOX_AUTO", "0")
    a, asked = _agent(proj, monkeypatch, sandbox=False, mode="edits")
    for c in GIT_OK:
        assert a._permit("bash", {"command": c}) == (True, ""), c
    assert asked == []
    for c in GIT_ASK:
        assert a._permit("bash", {"command": c})[0] is False, c
    assert len(asked) == len(GIT_ASK)


def test_5_and_in_sandbox_auto(proj, monkeypatch):
    _repo(proj)
    for c in GIT_OK:
        assert PR.sandbox_auto_ok(c, proj), c
    for c in GIT_ASK[:10]:                                    # the network, history rewrites, clean, config, hooks
        assert not PR.sandbox_auto_ok(c, proj), c


def test_5_amending_a_pushed_commit_asks(proj, tmp_path):
    _repo(proj)
    assert PR.git_local_ok("git commit --amend -m better", proj)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    for c in (["remote", "add", "origin", str(remote)], ["push", "-q", "origin", "HEAD:main"], ["fetch", "-q"]):
        subprocess.run(["git", "-C", str(proj), *c], check=True, capture_output=True)
    assert not PR.git_local_ok("git commit --amend -m better", proj)
    assert not PR.sandbox_auto_ok("git commit --amend -m better", proj)


def test_5_a_repo_with_hooks_asks(proj, monkeypatch):
    _repo(proj)
    hook = proj / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    assert not PR.git_local_ok("git commit -q -m x", proj)    # a hook runs code: the user says yes first


# ---------------------------------------------------------------------------------------- 6: $HOME in an edit path
def test_6_home_and_variables_expand_like_a_shell(proj, tmp_path, monkeypatch):
    home = Path(os.environ["HOME"])
    assert T.expand_path("$HOME/x.txt") == f"{home}/x.txt" and T.expand_path("~/x.txt") == f"{home}/x.txt"
    monkeypatch.setenv("SUB", str(proj / "sub"))
    assert T.expand_path("${SUB}/y.txt") == f"{proj}/sub/y.txt"
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    with pytest.raises(T.ToolError, match="not set"):
        T.expand_path("$NOPE_NOT_SET/z.txt")


def test_6_a_patch_to_home_asks_as_outside_and_never_makes_a_dollar_folder(proj, monkeypatch):
    """Omar: an apply_patch to `$HOME/omar_notes.txt` made a folder named "$HOME" in the project."""
    a, asked = _agent(proj, monkeypatch, sandbox=True, mode="edits")
    script = "apply_patch <<EOF\n*** Begin Patch\n*** Add File: $HOME/omar_notes.txt\n+hello\n*** End Patch\nEOF"
    assert a._permit("exec_command", {"cmd": script})[0] is False
    assert asked[0]["kind"] == "edit" and "outside the project folder" in asked[0]["title"]
    assert str(Path(os.environ["HOME"]) / "omar_notes.txt") in asked[0]["title"]
    out = a.ws.exec_command(script)                           # even if it ran: refused, no literal folder
    assert "writes must stay inside" in out and not (proj / "$HOME").exists()
    monkeypatch.setenv("SUB", "notes")
    a.ws.apply_patch("*** Begin Patch\n*** Add File: $SUB/a.txt\n+hi\n*** End Patch")
    assert (proj / "notes" / "a.txt").exists() and not (proj / "$SUB").exists()
    asked.clear()
    assert a._permit("apply_patch", {"input": "*** Begin Patch\n*** Add File: $SUB/b.txt\n+hi\n*** End Patch"})[0]
    assert asked == []                                        # $SUB is inside: an edit like any other


# ---------------------------------------------------------------------------------------- 7: wording
def test_7_the_buttons_say_what_each_answer_covers():
    js = (UI / "app.js").read_text()
    for b in ('data-d="yes">Allow just this<', ">Allow for this whole chat<", ">Always allow in ${esc(fname)}<",
              'data-d="no">Deny<'):
        assert b in js, b
    assert '"Allow just this"' in js and '"Allow for this whole chat"' in js       # ACP options read the same
    assert '"folder_name"' in (UI / "agent_api.py").read_text()


def test_7_deleting_the_users_own_file_asks_as_settings_say(proj, monkeypatch):
    """Omar: `rm sample.csv` ran without a card while Settings said "asks before deleting"."""
    (proj / "sample.csv").write_text("a,b\n")
    js = (UI / "app.js").read_text()
    assert "asks before deleting" in js                       # what Settings and the composer promise
    a, asked = _agent(proj, monkeypatch, sandbox=True)
    assert a._permit("exec_command", {"cmd": "sed -i '1i # x' a.py && rm sample.csv && ls"})[0] is False
    assert asked and asked[0]["title"].startswith("Delete") and "sample.csv" in asked[0]["title"]
    a.ws.write_file("debug_tmp.py", "print(1)\n")             # a file this chat made: its own scratch
    asked.clear()
    assert a._permit("bash", {"command": "rm debug_tmp.py"}) == (True, "") and asked == []
    assert a._permit("bash", {"command": "rm -rf __pycache__ && rm -f gone.txt"}) == (True, "")


def test_7_p_advice_never_names_the_mode_the_run_already_has():
    assert "--permission-mode" not in TS.DENIED_NOTE and "--auto" not in TS.DENIED_NOTE
    assert "nobody can answer" in TS.DENIED_NOTE
    _, how, kind = TS._allow_hint({"kind": "edit", "title": "Change notes.docx?"}, "edits")
    assert "--permission-mode edits" not in how and kind != "edit"
    _, how, _ = TS._allow_hint({"kind": "edit", "title": "Change /opt/x.txt, outside the project folder?"}, "edits")
    assert how.startswith("--dir")
    _, how, kind = TS._allow_hint({"kind": "edit", "title": "Edit notes.md?"}, "ask")
    assert how == "--permission-mode edits" and kind == "edit"      # only where it really helps


# ---------------------------------------------------------------------------------------- 8: the step row
def test_8_the_step_row_of_a_shell_patch_is_plain():
    script = ("apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: ledgerly/report.py\n@@\n-a\n+b\n"
              "*** Update File: ledgerly/cli.py\n@@\n-c\n+d\n*** End Patch\nEOF\npython -m pytest -q")
    row = core._summary("exec_command", {"cmd": script})
    assert row == "Edit ledgerly/report.py, ledgerly/cli.py, then run a Python step" and "<<" not in row
    assert core._summary("shell", {"command": ["bash", "-lc", script.split("\npython")[0]]}) == \
        "Edit ledgerly/report.py, ledgerly/cli.py"
    assert core._summary("exec_command", {"cmd": "ls -la"}) == "ls -la"          # other commands stay as written


def test_a_delete_card_names_only_what_the_delete_removes():
    """Cosmetic from the approvals work: a sed before an rm made the card read "Delete report.py, sample.csv?"."""
    from meshpool_harness.agent import core as K
    t = K._plain_command_title("sed -i 's/a/b/' report.py && rm sample.csv")
    assert t == "Delete sample.csv?", t
