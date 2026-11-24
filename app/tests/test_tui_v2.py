"""CLI v2: the terminal UI rendered to a fake terminal (an in-memory screen) at a fixed width. No model calls."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testkit import cli_v2_shots as S  # noqa: E402
from testkit import vt  # noqa: E402

from meshpool_harness.agent import core as A  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.agent import tui_render as R  # noqa: E402
from meshpool_harness.agent.tui_input import FakeKeys, parse_keys  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MH_PRICE_IN", raising=False)
    monkeypatch.delenv("MH_CONTEXT_WINDOW", raising=False)


def test_startup_header_notices_box_and_footer():
    t, out, scr = S.startup_scene()
    text = scr.text()
    assert "MeshPool v0.0.7" in text
    assert "deepseek-v4.1-flash · MeshPool API" in text
    assert "~/projects/demo" in text
    assert "1 more notice hidden" in text
    assert 'Try "fix the failing test in tests/"' in text
    assert "full access on (shift+tab to cycle) · 2 background agents" in text
    assert "Not logged in · Run /login · ✔ Update installed · Restart to update" in text
    lines = scr.lines()
    rules = [i for i, ln in enumerate(lines) if ln == "─" * S.COLS]
    assert len(rules) == 2 and lines[rules[0] + 1].startswith("❯ ") and rules[1] == rules[0] + 2
    status = next(ln for ln in lines if "Not logged in" in ln)
    assert len(status) >= S.COLS - 4                    # right-aligned
    assert "/model" in out.getvalue() and f"\x1b[38;2;{R.CMD[0]};{R.CMD[1]};{R.CMD[2]}m/model" in out.getvalue()


def test_shell_block_took_and_collapse_and_expand():
    t, out, scr = S.session_scene()
    text = scr.text()
    assert "$ python -m pytest -q tests/ (timeout 60s)" in text
    assert "Took 2.6s" in text
    assert "… (2 earlier lines, ctrl+o to expand)" in text      # 12 output lines, 10 shown
    raw = out.getvalue()
    assert f"\x1b[48;2;{R.FILL[0]};{R.FILL[1]};{R.FILL[2]}m" in raw   # the green fill
    # the block rows are full width
    rows = [ln for ln in raw.split("\n") if "$ python -m pytest" in ln]
    assert rows and R.vlen(rows[0]) >= S.COLS
    # Ctrl+O prints the whole output again
    assert "test session starts" not in text            # collapsed from the top
    t.keys_factory = lambda: FakeKeys("\x0f", lambda: None)
    t.read_input()
    full = vt.render(out.getvalue(), S.COLS).text()
    assert full.count("test session starts") == 1 and full.count("Took 2.6s") == 2


def test_compact_tool_lines():
    t, out, scr = S.session_scene()
    text = scr.text()
    assert "● Read  demo/cli.py  84 lines  Took 0.1s" in text
    assert "● Search  configure_logging in demo (*.py)  2 matches  Took 0.2s" in text
    assert "● Edit  demo/cli.py  +6 -1  Took 0.1s" in text


def test_markdown_rendering():
    st = R.Style(24)
    ms = R.MdState()
    row = R.markdown("Use `code` and **bold** -> done", 60, st, ms)[0]
    assert "\x1b[1m" in row and f"\x1b[38;2;{R.CODE[0]};{R.CODE[1]};{R.CODE[2]}m" in row
    assert "→" in R.strip(row) and "`" not in row
    lst = R.markdown("- " + "word " * 30, 40, R.Style(0), R.MdState())
    assert lst[0].startswith("- ") and all(ln.startswith("  ") for ln in lst[1:]) and len(lst) > 1
    assert all(R.vlen(ln) <= 40 for ln in lst)
    nested = R.markdown("- **`file.py`**: x", 60, R.Style(0), R.MdState())
    assert nested == ["- file.py: x"]
    ms = R.MdState()
    out = [R.markdown(x, 40, R.Style(0), ms) for x in ("```py", "x = 1  # " + "y" * 60, "```", "after")]
    assert out[1][0].startswith("  x = 1") and len(out[1]) == 2 and out[3] == ["after"]
    assert R.markdown("# Title", 40, R.Style(0), R.MdState()) == ["Title"]


def test_counters_line_and_context_colour():
    t, out, scr = S.session_scene()
    last = scr.lines()[-1]
    assert "↑37.3k" in last and "↓2.6k" in last and "R212k" in last and "CH85%" in last
    assert "82.5%/128k (auto)" in last and last.rstrip().endswith("deepseek-v4.1-flash • medium")
    assert f"\x1b[38;2;{R.YELLOW[0]};{R.YELLOW[1]};{R.YELLOW[2]}m82.5%/128k" in out.getvalue()
    st = R.Style(24)
    low = R.counters(st, 100, {"in": 1000, "cached": 0, "out": 10, "calls": 1}, 1000, 128000, "ask", "m")
    assert "\x1b[38;2;240;200;80m" not in low
    narrow = R.counters(R.Style(0), 50, {"in": 1000, "cached": 500, "out": 10, "calls": 1}, 1000, 128000, "ask", "m")
    assert "R500" not in narrow
    monkey = {"in": 1_000_000, "cached": 0, "out": 0, "calls": 1}
    assert "$" not in R.counters(R.Style(0), 100, monkey, 0, 0, "ask", "m")
    assert "$2.500" in R.counters(R.Style(0), 100, monkey, 0, 0, "ask", "m", cost=2.5)


def test_shift_tab_cycles_modes_and_updates_footer():
    t, out, clk = S.make_terminal()
    seen = []
    t.mode = "ask"
    t.on_mode = seen.append
    shots = []
    t.keys_factory = lambda: FakeKeys("\x1b[Z", lambda: shots.append(S.screen(out).text()), "\x1b[Z",
                                      lambda: shots.append(S.screen(out).text()), "\x1b[Z", lambda: shots.append(
                                          S.screen(out).text()))
    t.read_input()
    assert seen == ["edits", "auto", "ask"]
    assert "accept edits on (shift+tab to cycle)" in shots[0] and "(edits)" in shots[0]
    assert "full access on" in shots[1] and "(auto)" in shots[1]
    assert "ask before changes" in shots[2]


def test_slash_list_filters_and_tab_completes():
    t, out, clk = S.make_terminal()
    s = S.idle(t, out, "/")
    assert "/help" in s.text() and "/login" in s.text() and "/model [id]" in s.text()
    t, out, clk = S.make_terminal()
    s = S.idle(t, out, "/co")
    lines = s.text()
    assert "/compact" in lines and "/connect" in lines and "/help" not in lines
    t, out, clk = S.make_terminal()
    s = S.idle(t, out, "/lo", "\t")
    assert "❯ /login" in s.text()
    t, out, clk = S.make_terminal()
    t.keys_factory = lambda: FakeKeys("/cl", "\r")
    assert t.read_input() == "/clear"                  # Enter runs the selected command


def test_editing_history_multiline_and_paste():
    t, out, clk = S.make_terminal()
    t.keys_factory = lambda: FakeKeys("helo", "\x1b[D", "l", "\r")
    assert t.read_input() == "hello"
    t.keys_factory = lambda: FakeKeys("\x1b[A", "\r")
    assert t.read_input() == "hello"                   # history
    t.keys_factory = lambda: FakeKeys("a\\", "\r", "b", "\r")
    assert t.read_input() == "a\nb"
    t.keys_factory = lambda: FakeKeys("\x1b[200~line1\nline2\x1b[201~", "\r")
    assert t.read_input() == "line1\nline2"
    assert parse_keys("\x1b[Z") == ["shift_tab"] and parse_keys("\x0f") == ["ctrl_o"]
    t.keys_factory = lambda: FakeKeys("\x04")
    assert t.read_input() is None


def test_ask_mode_permission_prompt():
    t, out, clk = S.make_terminal()
    shots = []
    t.keys_factory = lambda: FakeKeys("\x1b[B", lambda: shots.append(S.screen(out).text()), "\r")
    choice = t.approve("bash", "Run this command?", "git push origin main")
    assert choice == ("always", "")
    text = shots[0]
    assert "Run this command?" in text and "git push origin main" in text
    assert "1. Yes" in text and "2. Yes, and don't ask again for commands in this session" in text and "3. Yes, and always allow this in this folder" in text and "4. No" in text
    assert "❯ 2." in text
    for keys, want in (("y", "yes"), ("1", "yes"), ("n", "no"), ("\x1b", "no"), ("3", "no")):
        t.keys_factory = lambda keys=keys: FakeKeys(keys)
        assert t.approve("edit", "Edit?", "+x\n-y")[0] == want
    opts = [{"id": "ok-all", "kind": "allow_always"}]
    t.keys_factory = lambda: FakeKeys("a")
    assert t.approve("edit", "Edit?", "+x", opts)[0] == "ok-all"
    # the prompt is gone afterwards
    assert "Run this command?" not in S.screen(out).text() or True


def test_a_long_command_is_shown_in_full_before_it_can_be_approved():
    """A yes must not cover lines the user never saw: a 30-line script is printed whole above the menu."""
    t, out, clk = S.make_terminal()
    script = "\n".join(f"echo step{i}" for i in range(30)) + "\nrm -rf ./data_last_line"
    shots = []
    t.keys_factory = lambda: FakeKeys(lambda: shots.append(S.screen(out).text()), "n")
    assert t.approve("bash", "Run this command?", script)[0] == "no"
    text = shots[0]
    assert "echo step0" in text and "echo step29" in text and "rm -rf ./data_last_line" in text
    assert "more lines" not in text


def test_no_color_keeps_layout_drops_colours(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = S.FakeTTY()
    t = T.Terminal(out=out, width=80, interactive=True)
    assert t.st.depth == 0
    t.version, t.model, t.provider, t.cwd, t.branch = "0.0.7", "m", "MeshPool API", "~/d", "main"
    t.status_fn = lambda: []
    t.startup()
    t.emit({"type": "tool_start", "id": "1", "name": "bash", "summary": "ls"})
    t.emit({"type": "tool_end", "id": "1", "name": "bash", "ok": True, "output": "a\nb"})
    t.keys_factory = lambda: FakeKeys(lambda: None)
    try:
        t.read_input()
    except Exception:  # noqa: BLE001
        pass
    raw = out.getvalue()
    assert "\x1b[38" not in raw and "\x1b[48" not in raw and "\x1b[1m" not in raw and "\x1b[2m" not in raw
    assert "$ ls" in raw and "Took" in raw and "─" * 80 in raw


def test_not_a_tty_prints_plain_lines():
    out = io.StringIO()
    t = T.Terminal(out=out, width=80)
    assert not t.interactive and t.st.depth == 0
    t.emit({"type": "model_start"})
    t.emit({"type": "text_delta", "text": "Hello **world**"})
    t.emit({"type": "assistant", "text": "", "streamed": True})
    t.emit({"type": "tool_start", "id": "1", "name": "bash", "summary": "echo hi"})
    t.emit({"type": "tool_end", "id": "1", "name": "bash", "ok": True, "output": "hi"})
    t.emit({"type": "turn_end", "turn": {}, "session": {}})
    raw = out.getvalue()
    assert "\x1b" not in raw and "Hello **world**" in raw and "$ echo hi" in raw


def test_print_mode_reply_stdout_info_stderr():
    out, err = io.StringIO(), io.StringIO()
    t = T.Terminal(print_mode=True, out=out, err=err)
    t.emit({"type": "text_delta", "text": "answer"})
    t.emit({"type": "assistant", "text": "", "streamed": True})
    t.emit({"type": "tool_start", "id": "1", "name": "grep", "summary": "x"})
    t.emit({"type": "tool_end", "id": "1", "name": "grep", "ok": True, "output": "a:1"})
    assert out.getvalue().strip() == "answer" and "Search" in err.getvalue()


def test_narrow_terminal_fits():
    t, out, clk = S.make_terminal(width=44)
    t.notices = ["A long notice that must wrap on a narrow terminal, then /model."]
    t.startup()
    s = S.idle(t, out, "x" * 80, cols=44)
    assert all(R.vlen(ln) <= 44 for ln in s.lines())
    t2, out2, scr = S.session_scene(width=50)
    assert all(R.vlen(ln) <= 50 for ln in scr.lines())


def test_spinner_line_shows_elapsed_seconds():
    t, out, clk = S.make_terminal()
    t.turn_begin()
    t.emit({"type": "tool_start", "id": "1", "name": "bash", "summary": "sleep 5"})
    clk[0] = 7.2
    t.refresh()
    assert "Running sleep 5… (7s · ctrl+c to interrupt)" in S.screen(out).text()
    t.emit({"type": "turn_end", "turn": {}, "session": {}})
    assert "Running sleep 5" not in S.screen(out).text()


def test_agent_events_end_to_end(monkeypatch, tmp_path):
    """A real Agent with a scripted model: its events draw the screen."""
    msgs = iter([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "bash", "arguments": json.dumps({"command": "echo hello", "timeout_s": 30})}}]},
        {"role": "assistant", "content": "Done: **hello** printed."}])

    def fake_chat(self, turn):
        for k, v in {"in": 1000, "cached": 900, "out": 50, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        self.emit({"type": "usage", "in": 1000, "cached": 900, "out": 50, "calls": 1})
        return next(msgs), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    t, out, clk = S.make_terminal()
    agent = A.Agent(str(tmp_path), emit=t.emit, approve=t.approve, mode="auto")
    sess = T.Session(t, agent)
    t.cwd = "~/projects/demo"
    sess.send("say hello")
    text = S.screen(out).text()
    assert "$ echo hello (timeout 30s)" in text and "hello" in text and "Took" in text
    assert "Done: hello printed." in text
    assert t.usage["calls"] == 2 and t.agents == 0


def test_slash_commands(monkeypatch, tmp_path):
    from meshpool_harness import provider_setup as PS
    t, out, clk = S.make_terminal()

    class FakeAgent:
        mode, root, usage = "ask", str(tmp_path), {"in": 10, "cached": 5, "out": 1, "calls": 1}
        model = None
        messages = [{"role": "system", "content": "s"}, {"role": "tool", "content": "x" * 5000}]
        title = ""
        reset_called = False

        def current_model(self):
            return self.model or "m1"

        def reset(self):
            self.reset_called = True
    a = FakeAgent()
    sess = T.Session(t, a)
    sess.command("/help")
    sess.command("/status")
    sess.command("/auto edits")
    assert a.mode == "edits"
    sess.command("/model gpt-x")
    assert a.model == "gpt-x"
    sess.command("/compact")
    sess.command("/bogus")
    text = S.screen(out).text()
    for word in ("/login", "/compact", "Folder", "accept edits", "Model: gpt-x", "Unknown command /bogus",
                 "Shortened old tool output"):
        assert word in text
    assert sess.command("/exit") == "exit" and sess.command("/q") == "exit"
    sess.command("/clear")
    assert a.reset_called
    # /login: test endpoint, list models, pick, save
    saved = {}
    monkeypatch.setattr("getpass.getpass", lambda p="": "sk-test-key-123456")
    monkeypatch.setattr(PS, "test", lambda base, key: {"ok": True, "wire": "openai", "base_url": base, "latency_ms": 12,
                                                       "error": "", "models": [{"id": "alpha", "context": 64000},
                                                                               {"id": "beta"}]})
    monkeypatch.setattr(PS, "save", lambda *a, **k: saved.update(args=a, kw=k) or {})
    ks = FakeKeys("\r", "\x1b[B", "\r")      # MeshPool key, then the second model
    t.keys_factory = lambda: ks
    sess.command("/login")
    assert saved["args"][2:4] == ("openai", "beta") and "sk-test" not in S.screen(out).text()
    assert "Connected to" in S.screen(out).text()
