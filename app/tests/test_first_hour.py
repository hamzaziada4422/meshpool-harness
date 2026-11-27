"""A new user's first hour with the released 0.0.18 Linux app (fresh home, a folder added in onboarding, the Chat tab):
the bugs that run found, one test each. No network, no model."""
import json
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")


# ---------- web tools in a Chat-tab chat in a folder ---------------------------------------------------------------------
class _Mcp:
    def schemas(self):
        return [{"type": "function", "function": {"name": n}} for n in
                ("mcp__meshpool-web__web_research", "mcp__meshpool-assistant__memory_save",
                 "mcp__meshpool-office__read_document")]

    def builtin_tool_names(self, mod):
        return set()


def _code_agent(tmp_path, monkeypatch, c171=True):
    from meshpool_harness.agent import core as A
    a = A.Agent(str(tmp_path), mcp=_Mcp(), emit=lambda e: None)
    monkeypatch.setattr(A.Agent, "_codex", lambda self: True)
    monkeypatch.setattr(A.Agent, "_c171", lambda self: c171)
    monkeypatch.setattr(A.Agent, "_office_direct", lambda self: False)
    return a


@pytest.mark.parametrize("c171", [True, False])
def test_a_chat_tab_chat_in_a_folder_can_search_the_web(tmp_path, monkeypatch, c171):
    """The Chat tab in a folder runs as a Codex-style Code chat: its tools were the shell, memory and the reader, so
    "find the latest news about X" ran curl (not on a bare computer) and then said "I don't have another way to search
    the web in this chat"."""
    a = _code_agent(tmp_path, monkeypatch, c171)
    names = lambda: [t["function"]["name"] for t in a.tool_schemas()]   # noqa: E731
    assert "web_research" not in names(), "a Code-tab chat with no web question keeps its lean list"
    a.chat_mode = True
    got = names()
    assert "web_research" in got and "fetch_url" in got, got
    assert not [n for n in got if n.startswith("mcp__meshpool-web__")], "one set of web tools, not two"


def test_a_web_question_in_a_code_chat_brings_the_web_tools(tmp_path, monkeypatch):
    from meshpool_harness.agent.core import Agent
    a = Agent(str(tmp_path), mode="auto", emit=lambda e: None)
    a._post = lambda base, key, body, stream: ({"choices": [{"message": {"role": "assistant", "content": "ok"},
                                                 "finish_reason": "stop"}], "usage": {}}, True)
    a.send("Fix the failing test in calc.py")
    assert not a.web_on
    a.send("Open example.com and tell me what's on it")
    assert a.web_on
    b = Agent(str(tmp_path), mode="auto", emit=lambda e: None)
    b._post = a._post
    b.send("Find the latest news about the James Webb telescope and summarise it")
    assert b.web_on


# ---------- what a failed search says -----------------------------------------------------------------------------------
def _engines(monkeypatch, err):
    from meshpool_harness import web as W

    def boom(*a, **k):
        raise W.WebError(err)
    for n in ("_ddgs", "_ddg", "_brave_html", "_bing"):
        monkeypatch.setattr(W, n, boom)
    monkeypatch.setattr(W, "_api_backends", lambda: [])
    monkeypatch.setattr(W, "_COOL", {})
    return W


def test_a_search_blocked_by_an_untrusted_certificate_says_so(monkeypatch):
    """A network that intercepts HTTPS failed every engine with CERTIFICATE_VERIFY_FAILED, and the tool said "the free
    search engines are limiting this computer, add a search key", which the agent passed on: a key would not help."""
    W = _engines(monkeypatch, "could not reach html.duckduckgo.com: [SSL: CERTIFICATE_VERIFY_FAILED] certificate "
                              "verify failed: self-signed certificate in certificate chain (_ssl.c:1011)")
    with pytest.raises(W.WebError) as e:
        W.search("james webb news")
    msg = str(e.value)
    assert "not trusted" in msg and "search key would not help" in msg, msg
    assert "limiting this computer" not in msg and "Add a search key" not in msg


def test_a_search_with_no_network_says_so(monkeypatch):
    W = _engines(monkeypatch, "could not reach www.bing.com: [Errno -3] Temporary failure in name resolution")
    with pytest.raises(W.WebError) as e:
        W.search("james webb news")
    assert "could not reach the search engines" in str(e.value) and "Add a search key" not in str(e.value)


def test_a_rate_limited_search_still_suggests_a_key(monkeypatch):
    W = _engines(monkeypatch, "HTTP 429 from html.duckduckgo.com")
    with pytest.raises(W.WebError) as e:
        W.search("james webb news")
    assert "Add a search key" in str(e.value)


# ---------- a folder added in the app is one project after a restart -----------------------------------------------------
def test_a_folder_added_in_the_app_is_not_made_a_second_project_at_the_next_start(tmp_path, monkeypatch):
    """"Add a folder" made a pinned project for Q4work; the next start's migration saw the folder in the old list and
    made a second, unpinned one, and new chats in Q4work went there, out of the pinned folder in the sidebar."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    from meshpool_harness import config as C
    from meshpool_harness import project_store as PS
    from meshpool_harness.ui import workbench as W
    C.ensure_home()
    f = tmp_path / "userhome" / "Documents" / "Q4work"
    f.mkdir(parents=True)
    W.add_project(str(f))                          # what the sidebar's "Add a folder" does (server.py, act "add")
    pid = PS.for_folder(str(f.resolve()))["id"]
    assert PS.migrate([str(f)]) == []              # the next start
    assert [p["id"] for p in PS.list_projects()] == [pid]
    assert PS.legacy_map()[str(f.resolve())] == pid


# ---------- the line under a finished deck names only what the final file still has ------------------------------------
def test_a_problem_fixed_in_a_later_pass_is_not_named_under_the_deck(tmp_path, monkeypatch):
    """A deck fixed in passes: a middle pass flagged an empty column on slide 4, the last pass did not. The line under
    the reply said "1 to look at: an empty box or placeholder is left on slide 4", while the reply named the cover."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    from meshpool_harness import checks as K
    K.begin("fh1")
    rows = []

    def row(ts, kind, label, count, ok, subject):
        rows.append({"ts": ts, "kind": kind, "label": label, "count": count, "ok": ok, "subject": subject})

    def detail(ts, lines):
        row(ts, "detail", "deck_rules", None, True,
            json.dumps({"kind": "deck_rules", "subject": "deck.pptx", "lines": lines}))
    row(100.0, "deck_rules", "design rules", 27, True, "deck.pptx")
    row(100.0, "deck_rules", "design rules", 2, False, "deck.pptx")
    detail(100.0, ["No empty column, box or placeholder: Slide 4: the column 'What it is' has no text in rows 2, 3."])
    row(102.0, "deck_rules", "design rules", 28, True, "deck.pptx")
    row(102.0, "deck_rules", "design rules", 1, False, "deck.pptx")
    detail(102.0, ["The cover fills its page: Slide 1: the cover's content spans 53% of its field."])
    K._file("fh1").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    s = K.summary("fh1")
    assert "slide 4" not in s["line"] and "empty box" not in s["line"], s["line"]
    assert not [d for d in s["details"] if "Slide 4" in d], s["details"]
    assert [d for d in s["details"] if "cover" in d], s["details"]
    K.end("fh1")


# ---------- approval cards ---------------------------------------------------------------------------------------------
def test_a_command_in_the_chats_own_folder_is_not_outside_it():
    """`cd /home/u/Documents/Q4work && ls` in a chat in Q4work asked "Use files outside this folder, in your home
    folder?"; a file in another folder of the home still asks that."""
    from meshpool_harness.agent.core import _plain_command_title as T
    root = "/home/u/Documents/Q4work"
    t = T('cd /home/u/Documents/Q4work && ls -la && python3 -c "import openpyxl, docx; print(1)"', root)
    assert "outside this folder" not in t, t
    assert "outside this folder" in T("cat /home/u/.ssh/id_rsa", root)
    assert "outside this folder" in T("cat /home/u/Documents/Q4work2/notes.txt", root)
    assert "outside this folder" in T("cd /home/u/Documents/Q4work && ls -la")    # no folder known: as before


def test_a_multi_line_command_reads_as_one_line_on_the_card():
    """"Use the internet? (python3 -I -c ")": only the first line of the command was kept, an open quote."""
    from meshpool_harness.agent.core import _plain_command_title as T
    t = T("python3 -I -c \"\nimport urllib.request\nprint(urllib.request.urlopen('https://example.com').read())\n\"")
    assert t.startswith("Use the internet? (python3 -I -c \" import urllib.request"), t
    assert not t.endswith('-c ")'), t


def test_reading_an_attachment_is_not_called_outside_the_folder(tmp_path, monkeypatch):
    """An attached contract.pdf is kept in runs/_uploads/chat-<id>; reading it asked "Use files outside this folder,
    in your home folder?" three times."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.delenv("MH_RUNS_DIR", raising=False)
    from meshpool_harness import config as C
    from meshpool_harness.agent.core import _plain_command_title as T
    up = C.runs_dir() / "_uploads" / "chat-20261127-133835-f667" / "contract.pdf"
    t = T(f"python3 -I -c \"import pypdf,sys; print(pypdf.PdfReader(sys.argv[1]).pages[0].extract_text())\" {up}",
          str(tmp_path / "proj"))
    assert "outside this folder" not in t and "home folder" not in t, t


def _work_agent(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.hrouter import profiles as PR
    from meshpool_harness.hrouter.route import Route
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    monkeypatch.setenv("MH_OFFICE_DIRECT", "1")
    monkeypatch.setenv("MH_WORK_OFFICE_STYLE", "c171")
    a = Agent(str(tmp_path), mode="auto", profile="work", emit=lambda e: None)
    a.set_route(Route(spec="docs", family="docs", base="work", profile="core", settings=PR.settings("core")), "x")
    return a


def test_a_question_about_an_attached_pdf_keeps_the_document_reader(tmp_path, monkeypatch):
    """The attachment's path named .pdf, so the question went on Codex's one-tool request: pdftotext (not installed),
    then PyMuPDF (not installed), then pypdf, an approval card each, instead of read_document."""
    a = _work_agent(tmp_path, monkeypatch)
    q = ("What is the monthly fee and the notice period in this contract?\n\nAttached files (in meshpool's attachments "
         "for this chat): /home/u/.mphome/runs/_uploads/chat-x/contract.pdf")
    assert not a.office_c171_for(q)
    assert a.office_c171_for("Sort data.xlsx by date and save it as out.xlsx"), "an office edit is unchanged"


def test_a_greater_than_inside_a_quoted_program_is_not_a_file_write():
    """awk 'NR>1{n++; ...}' orders.csv asked "Write 1{n++?"."""
    from meshpool_harness.agent.core import _plain_command_title as T
    t = T("cd /home/u/Documents/Q4work && awk -F, 'NR>1{n++; u[$2]+=$3} END{print \"rows\",n}' orders.csv",
          "/home/u/Documents/Q4work")
    assert "1{n++" not in t and not t.startswith("Write"), t
    assert T("sort data.csv > sorted.csv") == "Write sorted.csv?"
    assert T("sort data.csv > 'my list.csv'").startswith("Write my")


def test_a_chain_of_steps_names_its_programs_plainly():
    """"Run a Python step (7 steps: (which, python3, ..." and "(5 steps: python3, "$VENV/bin/pip", ...)"."""
    from meshpool_harness.agent.core import _plain_command_title as T
    t = T('(which python3 || true) && python3 -m venv "$VENV" && "$VENV/bin/pip" install -q pytest && '
          '"$VENV/bin/python" -m pytest -q')
    assert "Install software from the internet?" in t, "pip install from a venv's own path is still an install"
    t = T('(which python3 || true) && python3 -m venv .venv && ".venv/bin/python" -m pytest -q')
    assert "(which" not in t and '".venv' not in t, t
    assert "which" in t and "python" in t, t


def test_scheduling_a_task_asks_in_plain_words(tmp_path):
    """"Use schedule task (meshpool-assistant)?" named a server, not what would happen."""
    from meshpool_harness.agent.core import Agent
    seen = []
    a = Agent(str(tmp_path), mode="ask", emit=lambda e: None,
              approve=lambda kind, title, detail, *x, **k: seen.append(title) or ("yes", ""))
    a._permit("mcp__meshpool-assistant__schedule_task", {"name": "Monday summary", "prompt": "x", "when": "weekly"})
    assert seen and seen[0] == 'Schedule a task "Monday summary"?', seen
    assert "meshpool-assistant" not in seen[0]


# ---------- searching past chats finds how they ended ------------------------------------------------------------------------
def test_chat_search_shows_how_the_chat_ended(tmp_path, monkeypatch):
    """"What did we decide about the bakery budget?" found the chat but only its opening request, so the agent said it
    could not see any decision."""
    from meshpool_harness import assistant as AS
    from meshpool_harness.agent import core
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    d = tmp_path / "sessions" / "20261127-131641-cce1"
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({"id": d.name, "updated": 1795785401, "workdir": "/home/u/Q4work",
        "title": "bakery budget 2027", "messages": [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "Make an Excel budget for a small bakery for 2027 with a profit chart."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "I made bakery_budget_2027.xlsx: income 412,000 and profit 61,500 for "
                                             "the year, with a profit chart."}]}))
    out = AS.search_chats("bakery budget")
    assert "bakery_budget_2027.xlsx" in out and "61,500" in out, out


# ---------- "Show in file manager" on a computer with none ------------------------------------------------------------------
def test_show_in_file_manager_without_one_says_where_the_file_is(tmp_path, monkeypatch):
    """A bare Linux has no xdg-open: the toast read "There is no file manager to show it in (No such file or
    directory)"."""
    import subprocess
    from meshpool_harness.ui import files_api as F
    (tmp_path / "sales.xlsx").write_bytes(b"x")
    monkeypatch.setattr(F, "safe", lambda root, path: (tmp_path, tmp_path / path))

    def gone(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(subprocess, "Popen", gone)
    monkeypatch.setattr("os.startfile", gone, raising=False)
    with pytest.raises(ValueError) as e:
        F.reveal({"root": str(tmp_path), "path": "sales.xlsx"})
    assert "No such file or directory" not in str(e.value) and str(tmp_path / "sales.xlsx") in str(e.value)


# ---------- Undo of a reply that committed ------------------------------------------------------------------------------
def _repo(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("git"):
        pytest.skip("needs git")
    root = tmp_path / "calc"
    root.mkdir()
    env_git = ["-c", "user.name=T", "-c", "user.email=t@example.com"]

    def git(*a):
        return subprocess.run(["git", "-C", str(root), *env_git, *a], capture_output=True, text=True, check=True).stdout
    git("init", "-q", "-b", "main")
    (root / "calc.py").write_text("def mean(xs):\n    return sum(xs) / len(xs)\n")
    git("add", "-A")
    git("commit", "-q", "-m", "calc")
    return root, git


def test_undo_takes_back_the_commit_the_reply_made(tmp_path):
    """"Add a median function and commit", then Undo: the files went back but the commit stayed, so the repository
    showed the commit and, uncommitted, its reverse."""
    from meshpool_harness.checkpoints import Checkpoints
    from meshpool_harness.ui.agent_api import undo_message
    root, git = _repo(tmp_path)
    before = git("rev-parse", "HEAD").strip()
    cp = Checkpoints(str(root), tmp_path / "store")
    cp.begin_turn("add median and commit")
    (root / "calc.py").write_text((root / "calc.py").read_text() + "\n\ndef median(xs):\n    return sorted(xs)[0]\n")
    git("commit", "-q", "-am", "Add median")
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert r["restored"] == ["calc.py"] and r.get("commits_undone"), r
    assert git("rev-parse", "HEAD").strip() == before
    assert git("status", "--porcelain").strip() == "", "the folder matches the branch again"
    msg = undo_message(r)[0]
    assert "Took back the commit it made" in msg, msg


def test_undo_keeps_a_commit_that_was_already_pushed(tmp_path):
    from meshpool_harness.checkpoints import Checkpoints
    from meshpool_harness.ui.agent_api import undo_message
    root, git = _repo(tmp_path)
    cp = Checkpoints(str(root), tmp_path / "store")
    cp.begin_turn("add median, commit and push")
    (root / "calc.py").write_text("def mean(xs):\n    return 0\n")
    git("commit", "-q", "-am", "Change mean")
    git("update-ref", "refs/remotes/origin/main", "HEAD")       # what a push leaves behind
    head = git("rev-parse", "HEAD").strip()
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert git("rev-parse", "HEAD").strip() == head and r.get("commits_kept"), r
    assert "already sent to the remote" in undo_message(r)[0]


# ---------- the terminal UI's context share uses the same window as the chat -----------------------------------------------
def test_the_terminal_footer_uses_the_chats_context_window(monkeypatch):
    """The footer said "4.9%/128k" while the line above it said "context 1,049k (from the model table)"."""
    monkeypatch.delenv("MH_CONTEXT_WINDOW", raising=False)
    from meshpool_harness.agent import tui_render as R
    from meshpool_harness.core import context as CX
    want = CX.window("deepseek-v4.1-flash", probe_endpoint="cache")
    assert want > 128_000
    assert R.context_window("deepseek-v4.1-flash", {}) == want


# ---------- MCP servers started with npx run on the bundled Node -----------------------------------------------------------
def test_an_mcp_server_gets_the_bundled_node_ahead_of_the_login_path(tmp_path, monkeypatch):
    """child_env put the login shell's PATH first (/usr/local/bin from .profile, Homebrew, nvm), so the bundled npx
    (#!/usr/bin/env node) ran on an older system Node: "SyntaxError: Unexpected token '?'"."""
    import os
    from meshpool_harness import config as C
    from meshpool_harness.agent import mcp as MCP
    app = tmp_path / "Meshpool" / "app"
    (app.parent / "python").mkdir(parents=True)                   # the packaged layout (config.packaged)
    (app.parent / "node" / "bin").mkdir(parents=True)
    app.mkdir()
    monkeypatch.setattr(C, "APP", app)
    node_bin = str(app.parent / "node" / "bin")
    monkeypatch.setenv("PATH", os.pathsep.join([node_bin, "/usr/bin", "/bin"]))
    monkeypatch.setattr(MCP, "login_shell_env", lambda: {"PATH": os.pathsep.join(["/usr/local/bin", "/usr/bin", "/bin"]),
                                                         "HOME": str(tmp_path)})
    path = MCP.child_env()["PATH"].split(os.pathsep)
    assert path[0] == node_bin, path
    assert "/usr/local/bin" in path, "the login shell's folders are kept, after the app's own"
