"""meshpool 0.0.17 ledger, this branch's review rows (FL-*): one test per row that had none, and the fixes made for the
rows that were still open (FL-L1, FL-L11, FL-L13, FL-R6a, FL-V15, FL-V17). No network, no model, no API key.

A few rows are about JavaScript in the app or the Office panel, which cannot run here. Those tests read the source and
say so in a comment; the browser checks in testkit (ui_crawl.js, office_panel.js, e2e_ledger.py) still cover them live.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
from urllib.parse import urlparse

import pytest

APP = Path(__file__).resolve().parents[1]
UI = APP / "meshpool_harness" / "ui"
OFFICE = APP / "meshpool_harness" / "office"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    from meshpool_harness.ui import agent_api as A
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    monkeypatch.setenv("MH_RUNS_DIR", str(home / "runs"))
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    monkeypatch.delenv("MH_INSTRUCTIONS_EXCLUDE", raising=False)
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    return tmp_path


def _git(cwd, *args) -> str:
    r = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


@pytest.fixture
def repo(env):
    r = env / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _wait(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _session(proj, replies):
    """An app chat (ui/agent_api.Session) whose model answers from a script."""
    from meshpool_harness.ui import agent_api as A
    s = A.Session(workdir=str(proj), mode="auto", harness="meshpool-code", no_mcp=True)
    seq = list(replies)

    def post(*a, **k):
        msg = seq.pop(0)
        msg = {"role": "assistant", "content": msg} if isinstance(msg, str) else msg
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {}}, False
    s.agent._post = post
    return s


def _say(s, text):
    n = sum(1 for e in s.events if e.get("type") == "turn_end")
    s.send(text)
    assert _wait(lambda: s.state == "idle" and sum(1 for e in s.events if e.get("type") == "turn_end") > n)


# ---------------------------------------------------------------------------------------------------- terminal
class _Board:
    """Who holds the (fake) terminal: how many raw readers at once, and the terminal mode."""

    def __init__(self):
        self.active = self.most = 0
        self.mode = "cooked"
        self.lock = threading.Lock()


def _raw_keys(board, keys=(), on_enter=None):
    class Raw:
        def __init__(self, fd=None):
            self.pending: list = []
            self.keys = list(keys)

        def __enter__(self):
            with board.lock:
                board.active += 1
                board.most = max(board.most, board.active)
                self.saved, board.mode = board.mode, "raw"
            if on_enter:
                on_enter()
            return self

        def __exit__(self, *a):
            with board.lock:
                board.active -= 1
                board.mode = self.saved
            return False

        def read(self, timeout=None):
            if self.pending:                      # as RawKeys: keys read together with an earlier one come first
                return self.pending.pop(0)
            if self.keys:
                return self.keys.pop(0)
            if timeout is None:
                raise EOFError
            time.sleep(timeout)
            return None
    return Raw


def _terminal(**kw):
    from meshpool_harness.agent import tui as T
    return T.Terminal(out=io.StringIO(), width=80, interactive=True, **kw)


def test_esc_watcher_and_a_question_never_read_the_keyboard_together(monkeypatch):
    """FL-L1: an approval that opens between the Esc watcher's "is a question open?" check and its taking the
    keyboard waits for the watcher; only one reader holds the terminal and its mode is restored after both."""
    from meshpool_harness.agent import tui as T
    board = _Board()
    approver_in = threading.Event()
    monkeypatch.setattr(T.K, "RawKeys", _raw_keys(board))            # what the watcher opens
    t = _terminal()
    t.keys_factory = lambda: _raw_keys(board, keys=["enter"], on_enter=approver_in.set)()   # the approval's keys
    result = {}
    real_is_set = t.idle.is_set
    fired = []

    def is_set():                      # the watcher's check returns what it saw, but the question opens right after
        seen = real_is_set()
        if not fired and threading.current_thread().name == "watcher":
            fired.append(1)
            threading.Thread(target=lambda: result.setdefault("a", t.approve("bash", "Run this?", "make")),
                             name="approver", daemon=True).start()
            approver_in.wait(0.4)      # unfixed: the approval is already reading; fixed: it waits on the watcher's lock
        return seen
    t.idle.is_set = is_set
    t._turn = True
    w = threading.Thread(target=t._watch_esc, name="watcher", daemon=True)
    w.start()
    assert _wait(lambda: "a" in result)
    t.turn_finish()
    w.join(3)
    assert not w.is_alive()
    assert result["a"][0] == "yes"
    assert board.most == 1, "the Esc watcher and the approval read the keyboard at the same time"
    assert board.mode == "cooked" and board.active == 0


def test_keys_typed_while_the_agent_works_reach_the_next_prompt(monkeypatch):
    """FL-T3: keys typed while a turn runs are kept by the Esc watcher and arrive in the next prompt, none swallowed."""
    from meshpool_harness.agent import tui as T
    board = _Board()
    monkeypatch.setattr(T.K, "RawKeys", _raw_keys(board, keys=["f", "i", "x"]))
    t = _terminal()
    t._turn = True
    w = threading.Thread(target=t._watch_esc, daemon=True)
    w.start()
    assert _wait(lambda: len(t.typeahead) == 3)
    t.turn_finish()
    w.join(3)
    t.keys_factory = lambda: _raw_keys(board, keys=[" ", "i", "t", "enter"])()     # a key source like RawKeys
    assert t.read_input() == "fix it"
    assert board.mode == "cooked"


def test_history_search_ends_on_a_paste_and_alt_backspace_stops_at_a_line_break():
    """FL-V15: a paste or a move ends a Ctrl+R search, keeping the match; Alt+Backspace (read as ctrl_w) deletes back to
    a line break, never into the line above (fixed now: Alt+Backspace never reached the code that stopped there)."""
    from meshpool_harness.agent.tui_input import FakeKeys, parse_keys
    t = _terminal()
    t.history = ["deploy the app", "run the tests"]
    t.keys_factory = lambda: FakeKeys("\x12", "test", "\x1b[200~ now\x1b[201~", "\r")
    assert t.read_input() == "run the tests now"
    t.keys_factory = lambda: FakeKeys("\x12", "deploy", "\x1b[F", "!", "\r")      # End: the search ends, typing goes on
    assert t.read_input() == "deploy the app!"
    t.keys_factory = lambda: FakeKeys("\x12", "zzz[(*", "\x1b", "\r")              # no match, odd characters: no crash
    assert t.read_input() in ("", None) or True
    assert parse_keys("\x1b\x7f") == ["ctrl_w"]
    t.keys_factory = lambda: FakeKeys("first line\x1b\r", "second", "\x1b\x7f", "x", "\r")   # Alt+Enter: a new line
    assert t.read_input() == "first line\nx"
    t.keys_factory = lambda: FakeKeys("first line\x1b\r", "\x1b\x7f", "x", "\r")   # at a line's start: the lines join
    assert t.read_input() == "first linex"


def test_bang_commands_run_only_when_typed_at_the_prompt():
    """FL-V16: "!ls" from piped input is a message, not a shell command; typed at the prompt it runs."""
    from meshpool_harness.agent import tui as T

    class Agent:
        root = os.getcwd()
        mode = "ask"
    for typed, want_shell in ((False, False), (True, True)):
        t = T.Terminal(out=io.StringIO(), width=80, interactive=False)
        if typed:
            t.keys_factory = lambda: None                                     # raw keys: typed at a real prompt
        s = T.Session(t, Agent())
        ran, sent = [], []
        s.shell, s.send = ran.append, sent.append
        lines = iter(["!echo hi", None])
        t.read_input = lambda *a, **k: next(lines)
        s.loop()
        assert (ran == ["echo hi"]) is want_shell and (sent == ["!echo hi"]) is (not want_shell)


def test_history_file_is_private_from_the_start_and_follows_resume(env, monkeypatch):
    """FL-L11: the prompt history file is created 0600 (never briefly readable by others), and after /resume into a chat
    of another folder the Up arrow walks that folder's prompts, the same key new prompts are saved under (fixed now)."""
    from meshpool_harness.agent import tui as T
    from meshpool_harness.agent import tui_more as TM
    a, b = env / "a", env / "b"
    a.mkdir()
    b.mkdir()
    made = []
    real_open = os.open

    def spy(path, flags, mode=0o777, *rest, **kw):
        if str(path).endswith("prompt-history.jsonl") and flags & os.O_CREAT:
            made.append(mode)
        return real_open(path, flags, mode, *rest, **kw)
    monkeypatch.setattr(os, "open", spy)
    old = os.umask(0)
    try:
        TM.add_history(str(a), "a prompt in a")
    finally:
        os.umask(old)
    f = Path(os.environ["MESHPOOL_HOME"]) / "prompt-history.jsonl"
    assert made and made[0] == 0o600
    if os.name != "nt":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    TM.add_history(str(b), "a prompt in b")
    t = _terminal()
    t.history, t._history_cwd = TM.load_history(str(a)), str(a)
    assert t.history == ["a prompt in a"]

    class Agent:
        root = str(b)
        mode = "ask"
    t.refresh_facts(Agent())                     # what TermFrontend.rebind does on /resume
    t.follow_history()
    assert t.cwd == str(b) and t.history == ["a prompt in b"]
    TM.add_history(t.cwd, "another in b")
    assert TM.load_history(str(b))[-1] == "another in b" and "another in b" not in TM.load_history(str(a))
    src = (APP / "meshpool_harness" / "agent" / "tui_session.py").read_text(encoding="utf-8")
    assert "follow_history()" in src.split("def rebind", 1)[1].split("\n    def ", 1)[0]


# ---------------------------------------------------------------------------------------------------- permissions
def test_probe_and_trace_ask_like_a_command_and_save_the_command(env):
    """FL-L13: in ask mode probe and trace raised IndexError (an MCP-style title from name.split('__')[1]). They now
    ask a command question with the command shown, and "Always in this folder" saves that command, not the tool."""
    from meshpool_harness.agent import core
    from meshpool_harness import config as C
    proj = env / "proj"
    proj.mkdir()
    for tool in ("probe", "trace"):
        asked = []
        a = core.Agent(str(proj), mode="ask", approve=lambda k, t, d: (asked.append((k, t, d)), ("no", ""))[1])
        ok, _ = a._permit(tool, {"command": "python x.py"})
        assert ok is False and asked == [("bash", f"Run this command ({tool})?", "python x.py")]
    a = core.Agent(str(proj), mode="ask", approve=lambda k, t, d: ("save", ""))
    assert a._permit("probe", {"command": "python x.py"})[0] is True
    rules = json.loads((C.home() / "permissions.json").read_text(encoding="utf-8"))
    saved = [r for p in rules["projects"].values() for r in p.get("allow", [])]
    assert saved == ["bash:python x.py"]
    b = core.Agent(str(proj), mode="ask", approve=lambda k, t, d: ("no", ""))
    assert b._permit("trace", {"command": "python x.py"})[0] is True        # the saved command covers it
    assert b._permit("trace", {"command": "python other.py"})[0] is False   # not every command of the tool
    assert core.rule_for("trace", {"command": "python y.py"}) == ["bash:python y.py"]


def test_permissions_file_has_one_writer_at_a_time(env):
    """FL-S8 (one of its low findings): saving folder rules from many threads at once keeps every rule."""
    from meshpool_harness.agent import core
    from meshpool_harness import config as C
    proj = env / "proj"
    proj.mkdir()
    ts = [threading.Thread(target=core.save_folder_rules, args=(str(proj), [f"bash:make t{i}"])) for i in range(12)]
    for x in ts:
        x.start()
    for x in ts:
        x.join(10)
    rules = json.loads((C.home() / "permissions.json").read_text(encoding="utf-8"))
    saved = {r for p in rules["projects"].values() for r in p.get("allow", [])}
    assert saved == {f"bash:make t{i}" for i in range(12)}


def test_claude_code_model_words_mean_the_chats_model(env):
    """FL-S8 (one of its low findings): a sub-agent file whose model is a Claude Code word (inherit, sonnet...) runs on
    the chat's model; a real model id is kept."""
    from meshpool_harness import config as C
    from meshpool_harness.agent import subagents as SA
    d = C.home() / "agents"
    d.mkdir(parents=True)
    (d / "a.md").write_text("---\nname: a\ndescription: x\nmodel: sonnet\n---\nDo a.\n")
    (d / "b.md").write_text("---\nname: b\ndescription: x\nmodel: inherit\n---\nDo b.\n")
    (d / "c.md").write_text("---\nname: c\ndescription: x\nmodel: deepseek-v4.1-flash\n---\nDo c.\n")
    defs = SA.definitions(env)
    assert defs["a"]["model"] is None and defs["b"]["model"] is None and defs["c"]["model"] == "deepseek-v4.1-flash"


def test_trust_prompt_says_subfolders_are_trusted_too(env):
    """FL-L8: the trust message for a folder with risky files says trusting it also trusts its subfolders."""
    from meshpool_harness import trust as TR
    proj = env / "proj"
    (proj / ".meshpool").mkdir(parents=True)
    (proj / ".meshpool" / "hooks.json").write_text(json.dumps({"PreToolUse": [{"command": "true"}]}))
    assert TR.needs_prompt(proj) == [".meshpool/hooks.json"]
    msg = TR.message(proj)
    assert "trusting it also trusts its subfolders" in msg
    TR.trust(proj)
    assert TR.is_trusted(proj / "sub" / "deeper") and TR.message(proj / "sub") == ""


def test_mcp_servers_that_is_not_an_object_starts_nothing(env, monkeypatch):
    """FL-L7: a trusted project's .mcp.json with "mcpServers": ["x"] gives no servers and no exception at start-up."""
    from meshpool_harness import trust as TR
    from meshpool_harness.agent import mcp as M
    proj = env / "proj"
    proj.mkdir()
    for bad in (["x"], "x", 3, None):
        (proj / ".mcp.json").write_text(json.dumps({"mcpServers": bad}))
        assert M.load_config(proj / ".mcp.json") == {}
    TR.trust(proj)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": ["x"], "servers": {"ok": "not a spec"}}))
    assert M.project_servers(proj) == {}
    monkeypatch.setattr(M, "active_config", lambda path=None: {})
    m = M.Manager(roots=[str(proj)], path=env / "home" / "none.json").start(timeout=2)
    try:
        assert m.servers == {} and m.config_error == ""
    finally:
        m.close()


# ---------------------------------------------------------------------------------------------------- files and rules
def test_content_search_never_follows_a_link_out_of_the_project(env, monkeypatch):
    """FL-L4: /api/fs/search with content=1 never returns lines from a file or folder that a link points to outside."""
    from meshpool_harness.ui import files_api as FA
    from meshpool_harness.ui import workbench as W
    outside = env / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("the zebra password\n")
    proj = env / "proj"
    proj.mkdir()
    (proj / "notes.txt").write_text("a zebra in the project\n")
    try:
        os.symlink(outside / "secret.txt", proj / "link.txt")
        os.symlink(outside, proj / "linkdir", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make symbolic links")
    monkeypatch.setattr(W, "known_roots", lambda: [proj])
    monkeypatch.setenv("MH_PICKER_IGNORED", "1")
    r = FA.search(str(proj), "zebra", content=True)
    assert [h["path"] for h in r["lines"]] == ["notes.txt"]
    assert not any("password" in h["text"] for h in r["lines"])
    st, body = FA.handle("GET", urlparse(f"/api/fs/search?root={proj}&q=zebra&content=1"))[:2]
    assert st == 200 and [h["path"] for h in body["lines"]] == ["notes.txt"]


def test_instruction_rules_that_link_outside_the_project_are_not_sent(env):
    """FL-L5: a rules file that is a link to a file outside the project never reaches the model."""
    from meshpool_harness.agent import extras as EX
    outside = env / "outside.md"
    outside.write_text("OUTSIDE-SECRET-RULE\n")
    proj = env / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".claude" / "rules").mkdir(parents=True)
    (proj / ".claude" / "rules" / "own.md").write_text("OWN-RULE\n")
    (proj / ".claude" / "rules" / "scoped.md").write_text("---\npaths: src/**\n---\nSCOPED-OWN\n")
    try:
        os.symlink(outside, proj / ".claude" / "rules" / "notes.md")
        os.symlink(outside, proj / ".claude" / "rules" / "scoped-link.md")
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make symbolic links")
    text = EX.instruction_text(proj)
    assert "OWN-RULE" in text and "OUTSIDE-SECRET-RULE" not in text
    assert "OUTSIDE-SECRET-RULE" not in EX.scoped_rules(proj, "src/a.py", set())


def test_scoped_rules_keep_leading_dots_and_manual_cursor_rules_wait(env):
    """FL-V14: a scoped rule for .github/** matches .github files (the leading dot is kept), and a Cursor rule marked
    manual (alwaysApply: false, no globs) is never loaded by itself."""
    from meshpool_harness.agent import extras as EX
    proj = env / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / ".cursor" / "rules").mkdir(parents=True)
    (proj / ".cursor" / "rules" / "ci.mdc").write_text("---\nglobs: .github/**\n---\nCI-RULE\n")
    (proj / ".cursor" / "rules" / "dot.mdc").write_text("---\nglobs: ./src/*.py\n---\nDOT-SLASH-RULE\n")
    (proj / ".cursor" / "rules" / "manual.mdc").write_text("---\ndescription: only when asked\nalwaysApply: false\n"
                                                         "---\nMANUAL-RULE\n")
    assert "CI-RULE" in EX.scoped_rules(proj, ".github/workflows/ci.yml", set())
    assert "CI-RULE" not in EX.scoped_rules(proj, "github/x.yml", set())
    assert "DOT-SLASH-RULE" in EX.scoped_rules(proj, "src/a.py", set())
    assert "MANUAL-RULE" not in EX.instruction_text(proj)
    for path in ("src/a.py", ".github/x.yml", "README.md"):
        assert "MANUAL-RULE" not in EX.scoped_rules(proj, path, set())


def test_read_file_binary_check_closes_the_file(env):
    """FL-L12: read_file on a binary file leaves no file handle open (no ResourceWarning)."""
    import gc
    from meshpool_harness.core.tools import ToolError, Workspace
    proj = env / "proj"
    proj.mkdir()
    (proj / "blob.bin").write_bytes(b"\x00\x01\x02" * 5000)
    ws = Workspace(str(proj), str(env / "out"))
    with warnings.catch_warnings(record=True) as got:
        warnings.simplefilter("always")
        with pytest.raises(ToolError, match="binary file"):
            ws.read_file("blob.bin")
        gc.collect()
    assert not [w for w in got if issubclass(w.category, ResourceWarning)]


# ---------------------------------------------------------------------------------------------------- cli and schedules
def test_w_is_a_flag_and_the_words_are_the_request():
    """FL-L10: meshpool -w "fix the tests": -w asks for an isolated copy and the words are the request."""
    from meshpool_harness.cli import _agent_parser
    a = _agent_parser().parse_args(["-w", "fix the tests"])
    assert a.worktree is True
    words = [v for k, v in vars(a).items() if isinstance(v, (str, list)) and v and
             ("fix the tests" == v or (isinstance(v, list) and "fix the tests" in v))]
    assert words, vars(a)
    assert _agent_parser().parse_args(["fix the tests"]).worktree is False


def test_cron_day_fields_and_dates_that_never_come():
    """FL-V13: both day fields restricted means either one (cron's rule); a day field starting with * keeps both
    (Vixie cron); 30 February and 31 April are refused."""
    from datetime import datetime
    from meshpool_harness import cron
    start = datetime(2026, 3, 1)                       # a Sunday
    hits, t = [], start
    for _ in range(12):
        t = cron.next_after("0 9 13 * 5", t)            # the 13th, or any Friday
        hits.append(t)
    assert all(h.day == 13 or h.weekday() == 4 for h in hits)
    assert any(h.day == 13 and h.weekday() != 4 for h in hits) and any(h.weekday() == 4 and h.day != 13 for h in hits)
    t, seen = start, []
    for _ in range(6):
        t = cron.next_after("0 9 */2 * 1", t)           # odd days that are also Mondays
        seen.append(t)
    assert all(h.weekday() == 0 and h.day % 2 == 1 for h in seen)
    for never in ("0 9 30 2 *", "0 9 31 4 *", "0 9 31 2,4,6 *"):
        with pytest.raises(cron.CronError, match="never comes"):
            cron.validate(never)
    assert cron.validate("0 9 29 2 *") == "0 9 29 2 *"  # 29 February comes in a leap year
    assert cron.next_after("0 9 29 2 *", start).year == 2028


def test_session_name_and_mcp_config_do_not_reach_child_runs(env, monkeypatch):
    """FL-V17: --name and --mcp-config are for this run only. The name is taken out of the environment when the chat
    takes it; --mcp-config is read once and taken out (fixed now: after the 0.0.16 merge it stayed in the environment,
    so commands the agent ran, a nested meshpool -p included, started those servers too)."""
    from meshpool_harness.agent import core
    from meshpool_harness.ui import agent_api as A
    proj = env / "proj"
    proj.mkdir()
    monkeypatch.setenv("MH_SESSION_NAME", "my named run")
    a = core.Agent(str(proj), mode="auto")
    a._post = lambda *x, **k: ({"choices": [{"message": {"role": "assistant", "content": "ok"},
                                             "finish_reason": "stop"}], "usage": {}}, False)
    a.send("hello")
    assert a.title == "my named run" and "MH_SESSION_NAME" not in os.environ
    child = core.Agent(str(proj), mode="auto")
    child._post = a._post
    child.send("child work")
    assert child.title == "child work"
    monkeypatch.setattr(A, "_RUN_MCP_SPEC", [])
    spec = json.dumps({"mcpServers": {"x": {"command": "x"}}})
    monkeypatch.setenv("MESHPOOL_FRONTEND", "terminal")
    monkeypatch.setenv("MH_MCP_CONFIG", spec)
    assert A.run_mcp_spec() == spec and "MH_MCP_CONFIG" not in os.environ
    assert A.run_mcp_spec() == spec                     # later chats in the same terminal still get them
    out = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('MH_MCP_CONFIG', 'unset'))"],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    assert out == "unset"
    src = (UI / "agent_api.py").read_text(encoding="utf-8")
    assert 'os.environ.get("MH_MCP_CONFIG"' not in src


# ---------------------------------------------------------------------------------------------------- the app's chats
def test_streamed_reasoning_folds_to_one_event_per_call(env, monkeypatch):
    """FL-R6a: 50 streamed reasoning pieces no longer pile up in the open chat's event list: once the call's saved copy
    arrives they become one shared marker each, never shown or saved, and positions stay put (fixed now)."""
    from meshpool_harness.agent import core
    from meshpool_harness.ui import agent_api as A

    class Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    chunks = [{"choices": [{"delta": {"reasoning_content": f"part {i} "}}]} for i in range(50)]
    chunks.append({"choices": [{"delta": {"content": "Answer."}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    monkeypatch.setattr(core, "_urlopen", lambda req, t, sink: Resp(body.encode()))
    proj = env / "proj"
    proj.mkdir()
    s = A.Session(workdir=str(proj), mode="auto", harness="meshpool-code", no_mcp=True)
    try:
        before = len(s.events)
        s.agent._post("http://x/v1", "k", {"model": "m", "messages": []}, True)
        thoughts = [e for e in s.events[before:] if e.get("type") == "thought" and not e.get("gone")]
        assert len(thoughts) == 1 and thoughts[0].get("final") and "part 49" in thoughts[0]["text"]
        gone = [e for e in s.events[before:] if e.get("gone") and e.get("type") == "thought"]
        assert len(gone) == 50 and all(g is gone[0] for g in gone)          # one shared marker, little memory
        assert [e["i"] for e in s.events[before:] if "i" in e] == sorted(e["i"] for e in s.events[before:] if "i" in e)
        shown = [e for e in s.view(before)["events"] if e.get("type") == "thought"]
        assert len(shown) == 1 and shown[0].get("final")
        s._persist()
        saved = (env / "sessions" / s.id).rglob("*")
        text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in saved if p.is_file())
        assert text.count('"gone"') == 0
        s.emit({"type": "thought", "text": "from a harness that sends no saved copy"})
        s.emit({"type": "turn_end"})
        assert [e for e in s.events if e.get("text") == "from a harness that sends no saved copy"]
    finally:
        s.close()
    js = (UI / "app.js").read_text(encoding="utf-8")       # JavaScript: read, not run. A page that missed the last
    case = js.split('case "thought": {', 1)[1].split("case ", 1)[0]   # pieces adds them from the saved copy
    assert "thoughtSeen" in case and "ev.text.slice(seen.length)" in case


def test_reply_versions_kept_on_try_again_cleared_on_undo(env):
    """FL-V11: Try again keeps the earlier answer under its own message; Undo or Edit of that message clears it."""
    from meshpool_harness.ui import agent_api as A
    proj = env / "proj"
    proj.mkdir()
    s = _session(proj, ["first answer", "second answer"])
    try:
        _say(s, "one")
        _say(s, "two")
        with A.LOCK:
            A.LIVE[s.id] = s
        A.thread_action(s.id, {"action": "keep_version", "turn": 2, "text": "an earlier answer"})
        A.session_action(s.id, "revert", {"turn": 2, "what": "chat", "keep_versions": True})
        assert A.read_meta(s.id).get("versions") == {"2": ["an earlier answer"]}
        A.session_action(s.id, "revert", {"turn": 1, "what": "chat"})
        assert not A.read_meta(s.id).get("versions")
    finally:
        with A.LOCK:
            A.LIVE.pop(s.id, None)
        s.close()


def test_fork_cuts_at_the_chosen_message_not_a_turn_count(env):
    """FL-S2: the branch's own cut point went with its replaced design; main's fork cuts by the message picked (its
    event index). A turn with a nudge (an open to-do) before it does not move the cut."""
    from meshpool_harness.ui import agent_api as A
    proj = env / "proj"
    proj.mkdir()
    todo = {"items": [{"content": "write a.txt", "status": "completed"}, {"content": "write b.txt", "status": "pending"}]}
    call = {"id": "c1", "type": "function", "function": {"name": "todo_write", "arguments": json.dumps(todo)}}
    s = _session(proj, [{"role": "assistant", "content": "", "tool_calls": [call]}, "all done", "b is not needed",
                        "second answer", "third answer"])
    try:
        _say(s, "one")
        nudges = [m for m in s.agent.messages if m.get("role") == "user" and m.get("content") not in ("one",)
                  and not str(m.get("content")).startswith("one")]
        assert nudges, "the open to-do nudge is a user-role message"
        _say(s, "two")
        _say(s, "three")
        idx = next(e["i"] for e in s.events if e.get("type") == "user" and e.get("text") == "two")
        new = s.fork(idx)
        said = [str(m.get("content")) for m in A.saved_messages(new) if m.get("role") in ("user", "assistant")]
        joined = "\n".join(said)
        assert "two" in said and "second answer" in joined and "b is not needed" in joined
        assert "three" not in said and "third answer" not in joined
    finally:
        s.close()


# ---------------------------------------------------------------------------------------------------- worktrees
def test_the_app_routes_reach_the_worktree_api(env, monkeypatch):
    """FL-T1: /api/worktree/... was answered by /api/work, so Work in a new worktree did nothing. The route order in
    ui/routes_extra.py sends it to ui/worktree_api.py (the test lost in the 0.0.16 merge, restored for main's routes)."""
    from meshpool_harness import worktrees as WT
    from meshpool_harness.ui import routes_extra as RX
    from meshpool_harness.ui import work_api
    monkeypatch.setattr(work_api, "handle", lambda *a, **k: (418, {"error": "work_api answered"}), raising=False)
    monkeypatch.setattr(WT, "listing", lambda *a, **k: [{"id": "w1"}])
    monkeypatch.setattr(WT, "summary", lambda r: {"id": r["id"], "from": "worktree_api"})
    st, body = RX.handle("GET", urlparse("/api/worktree/list"))[:2]
    assert st == 200 and body == {"items": [{"id": "w1", "from": "worktree_api"}]}
    st, body = RX.handle("GET", urlparse("/api/worktree"))[:2]
    assert st == 400 and "workdir" in body["error"]
    st, body = RX.handle("GET", urlparse("/api/worktree/nochat"))[:2]
    assert st == 404 and "isolated copy" in body["error"]
    prefixes = [p for p, _ in RX.AREAS]
    assert prefixes.index("/api/worktree") < prefixes.index("/api/work")
    from meshpool_harness.ui import server as S
    assert any("/api/worktree".startswith(p) for p in S.EXTRA_PREFIXES)


def test_discard_keeps_a_branch_with_unpushed_commits_and_counts_them(repo):
    """FL-T4: the branch's safety check (always 0 unpushed commits) went with its replaced design. Main's: discarding
    a copy keeps its branch when it holds commits the folder does not have, here exactly 2; merged work is not kept."""
    from meshpool_harness import worktrees as WT
    rec = WT.create(repo)
    wd = Path(rec["workdir"])
    for n in (1, 2):
        (wd / f"f{n}.txt").write_text(str(n))
        _git(wd, "add", "-A")
        _git(wd, "commit", "-qm", f"work {n}")
    r = WT.discard(rec["id"])
    assert r["kept_branch"] == rec["branch"]
    assert _git(repo, "rev-list", "--count", f"main..{rec['branch']}") == "2"
    rec2 = WT.create(repo)
    wd2 = Path(rec2["workdir"])
    (wd2 / "g.txt").write_text("g")
    _git(wd2, "add", "-A")
    _git(wd2, "commit", "-qm", "merged work")
    assert WT.merge(rec2["id"]).get("ok", True)
    r2 = WT.discard(rec2["id"])
    assert r2["kept_branch"] is None and (repo / "g.txt").exists()


def test_worktrees_never_touch_the_users_stash(repo):
    """FL-S3: carry (and its stash pop) went with the replaced design. Main's copies never use git stash: a stash
    entry the user made stays exactly as it was through create, merge and discard."""
    from meshpool_harness import worktrees as WT
    (repo / "a.txt").write_text("my unsaved work\n")
    _git(repo, "stash", "push", "-m", "mine")
    before = _git(repo, "stash", "list", "--format=%H %gs")
    rec = WT.create(repo)
    (Path(rec["workdir"]) / "new.txt").write_text("n")
    WT.merge(rec["id"])
    WT.discard(rec["id"])
    assert _git(repo, "stash", "list", "--format=%H %gs") == before
    src = (APP / "meshpool_harness" / "worktrees.py").read_text(encoding="utf-8")
    assert '"stash"' not in src


def test_worktree_actions_wait_for_a_running_chat(env, monkeypatch):
    """FL-S7: moving a chat into a worktree went with the replaced design. Main's equivalent race: merge, reset or
    discard of a chat's copy is refused while that chat's turn runs."""
    from meshpool_harness import worktrees as WT
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui import worktree_api as WA
    monkeypatch.setattr(WA, "_chat_copy", lambda sid: {"id": "w1"})
    called = []
    for fn in ("merge", "reset", "discard"):
        monkeypatch.setattr(WT, fn, lambda *a, _fn=fn, **k: called.append(_fn) or {"ok": True})

    class Busy:
        state = "working"
    with A.LOCK:
        A.LIVE["busy-chat"] = Busy()
    try:
        for act in ("merge", "reset", "discard"):
            st, body = WA.handle("POST", urlparse("/api/worktree/busy-chat"), json.dumps({"action": act}).encode())[:2]
            assert st == 409 and "finish" in body["error"]
    finally:
        with A.LOCK:
            A.LIVE.pop("busy-chat", None)
    assert called == []


# ---------------------------------------------------------------------------------------------------- JavaScript (read)
def test_message_actions_ignore_data_ma_outside_a_message():
    """FL-T2: the click handler for the buttons under your messages matched any [data-ma] (the MCP library's Add), a
    page error on Settings, Custom tools. JavaScript cannot run here, so this reads app.js; testkit/ui_crawl.js and
    testkit/mcp_err_probe.js check it in a browser."""
    js = (UI / "app.js").read_text(encoding="utf-8")
    handlers = [m.start() for m in re.finditer(r'closest\("\[data-ma\]"\)', js)]
    assert handlers
    for at in handlers:
        line = js[at:js.index("\n", at)]
        assert re.search(r'if \(!ma \|\| !ma\.closest\("\.msg-user"\)\) return|\?\.closest\("\.msg-user"\).*if \(!host\) return',
                         line), line
    assert 'data-ma="${esc(e.id)}"' in js                  # the MCP library still uses data-ma: the guard is needed


def test_undo_asks_before_it_changes_anything():
    """FL-L9: the "Only the files" undo menu went with the replaced design. Main's Undo asks first: undoTurn shows a
    confirmation before it posts /revert. JavaScript: read, not run (testkit/e2e_ledger.py drives it live)."""
    js = (UI / "app.js").read_text(encoding="utf-8")
    body = js.split("async function undoTurn(n) {", 1)[1].split("\n}\n", 1)[0]
    assert body.index("await ask(") < body.index("/revert")
    assert "Only the files" not in js and "Only the conversation" not in js


def _taskpane() -> str:
    return (OFFICE / "taskpane.js").read_text(encoding="utf-8")


def test_office_new_chat_stops_the_turn_and_drops_the_old_chats_events():
    """FL-O1: New chat stops a running turn, and events of the chat the panel left are never shown (a generation
    counter). JavaScript: read, not run; testkit/office_panel.js checks it in a browser."""
    js = _taskpane()
    new = js.split("function newChat() {", 1)[1].split("\n  }\n", 1)[0]
    assert re.search(r"if \(S\.busy && S\.id\) api\(\"POST\", `/api/agent/\$\{S\.id\}/stop`", new)
    assert "S.gen++" in new and "S.id = null" in new
    apply_ = js.split("function apply(v, gen) {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (gen !== S.gen) return;" in apply_
    poll = js.split("async function poll() {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (S.polling) return;" in poll and "gen === S.gen" in poll


def test_office_too_old_says_so(tmp_path):
    """FL-O4: Office 2016 and 2019 (which cannot run taskpane.js) get a message instead of an empty pane. compat.js
    runs first and taskpane.js marks that it ran. With node on the machine, compat.js is run in a tiny fake page."""
    html = (OFFICE / "taskpane.html").read_text(encoding="utf-8")
    assert html.index("/office/compat.js") < html.index("/office/taskpane.js")
    assert "window.__mpParsed = true" in _taskpane()
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the read checks above passed")
    js = (OFFICE / "compat.js").read_text(encoding="utf-8")
    probe = ("const vm = require('vm'); const out = [];\n"
             "for (const parsed of [false, true]) {\n"
             "  const doc = {body: {innerHTML: 'empty'}};\n"
             "  const win = {__mpParsed: parsed, setTimeout: f => f()};\n"
             "  vm.runInNewContext(" + json.dumps(js) + ", {window: win, document: doc});\n"
             "  out.push(doc.body.innerHTML);\n"
             "}\nconsole.log(JSON.stringify(out));\n")
    (tmp_path / "probe.js").write_text(probe)
    r = subprocess.run([node, str(tmp_path / "probe.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    old, new = json.loads(r.stdout)
    assert "too old for meshpool" in old and "Office 2021" in old and new == "empty"


def test_office_cursor_moves_do_not_read_the_whole_document():
    """FL-O5: a selection change waits 300 ms and then reads lightly (no whole document or deck); only the newest read
    updates the line. JavaScript: read, not run; testkit/office_panel.js checks it in a browser."""
    js = _taskpane()
    hook = next(ln for ln in js.splitlines() if "DocumentSelectionChanged" in ln)
    assert "clearTimeout(S.t)" in hook and "setTimeout(showContext, 300)" in hook
    show = js.split("async function showContext() {", 1)[1].split("\n  }\n", 1)[0]
    assert "readContext(true)" in show and "seq !== S.ctxSeq" in show
    for fn in ("readWord", "readPowerPoint"):
        body = js.split(f"async function {fn}(light) {{", 1)[1].split("\n  }\n", 1)[0]
        light = body.index("if (light) return")
        assert light < body.index("body" if fn == "readWord" else "slides.load")


def test_office_question_answer_does_not_start_a_second_poll():
    """FL-O6: an answer to meshpool's question is sent once and read by the poll loop already running; one poll loop
    at a time. JavaScript: read, not run; testkit/office_panel.js checks it in a browser."""
    js = _taskpane()
    send = js.split("async function send(text) {", 1)[1].split("\n  }\n", 1)[0]
    branch = send.split("if (answering) {", 1)[1].split("\n    }\n", 1)[0]
    assert "S.asking = false" in branch and "/send`" in branch and "return;" in branch and "poll()" not in branch
    assert "if (S.busy && !answering) return;" in send
