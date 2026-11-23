"""Regression tests for the second agent audit pass."""
import io
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import core as A  # noqa: E402
from meshpool_harness.agent import planning as PL  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.agent import tui_render as R  # noqa: E402


def test_bad_reply_detection():
    assert A._bad_reply({"error": {"message": "overloaded"}})
    assert A._bad_reply({"choices": []})
    assert A._bad_reply({"choices": [{"message": None}]})
    assert A._bad_reply({"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]})
    assert not A._bad_reply({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]})
    assert not A._bad_reply({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})


def test_planning_signals_fast_on_huge_line():
    t0 = time.time()
    r = PL.signals("A" * 400000)
    assert time.time() - t0 < 2 and r["len"] == 400000


def test_safe_text_strips_terminal_sequences():
    s = "a\x1b]52;c;ZWNobw==\x07b\x1b[2Jc\x1b]0;title\x1b\\d\re\tf\ng\x9b31m"
    assert R.safe_text(s) == "abcde\tf\ng31m" or R.safe_text(s).startswith("abcde")
    assert "\x1b" not in R.safe_text(s) and "\x07" not in R.safe_text(s)


def test_terminal_emit_strips_sequences():
    out = io.StringIO()
    t = T.Terminal(print_mode=True, out=out, err=io.StringIO())
    t.emit({"type": "text_delta", "text": "hi \x1b]52;c;AAAA\x07there"})
    t.emit({"type": "assistant", "text": "", "streamed": True})
    assert "\x1b" not in out.getvalue() and "hi there" in out.getvalue()


def test_print_mode_discards_partial_reply_on_reset():
    out = io.StringIO()
    t = T.Terminal(print_mode=True, out=out, err=io.StringIO())
    t.emit({"type": "text_delta", "text": "The answer is par"})
    t.emit({"type": "stream_reset"})
    t.emit({"type": "text_delta", "text": "The answer is partial-free."})
    t.emit({"type": "assistant", "text": "", "streamed": True})
    assert out.getvalue() == "The answer is partial-free.\n"


def test_error_event_marks_print_mode_failed():
    t = T.Terminal(print_mode=True, out=io.StringIO(), err=io.StringIO())
    t.emit({"type": "error", "text": "The model returned an empty reply."})
    assert t.failed


def test_shell_block_stays_within_narrow_width():
    st = R.Style(0)
    for w in (5, 8, 12):
        rows, _ = R.shell_block("echo hello world", "\n".join(f"line {i}" for i in range(30)), 1.0, st, w)
        assert all(R.vlen(r) <= w for r in rows), w


def test_empty_reply_is_an_error_and_not_saved(tmp_path, monkeypatch):
    a = A.Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto", sessions_dir=str(tmp_path / "s"))
    events = []
    a.emit = events.append
    monkeypatch.setattr(a, "_chat", lambda turn: ({"content": ""}, False))
    a.send("hello")
    assert any(e["type"] == "error" and "empty reply" in e["text"] for e in events)
    assert not any(m.get("content") == "(no reply)" for m in a.messages)


def test_nameless_tool_call_is_repaired_before_history(tmp_path, monkeypatch):
    a = A.Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto", sessions_dir=str(tmp_path / "s"))
    a.emit = lambda ev: None
    replies = iter([({"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"arguments": "{}"}}]}, False),
                    ({"content": "done"}, False)])
    monkeypatch.setattr(a, "_chat", lambda turn: next(replies))
    a.send("go")
    calls = [c for m in a.messages if m.get("tool_calls") for c in m["tool_calls"]]
    assert calls and all(c["function"].get("name") for c in calls)


def test_repeat_guard_skips_while_background_job_runs(tmp_path):
    a = A.Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto", sessions_dir=str(tmp_path / "s"))
    (tmp_path / "log.txt").write_text("x", encoding="utf-8")

    class P:
        def poll(self): return None
    a.ws.__dict__["_bg"] = {"bg1": {"p": P()}}
    g = {}
    call = {"id": "1", "function": {"name": "read_file", "arguments": '{"path": "log.txt"}'}}
    outs = [a._guarded_tool_inner(call, g) for _ in range(6)]
    assert not any("Not run: you already made this call" in o for o in outs)


def test_approvals_are_serialised():
    t = T.Terminal(print_mode=False, out=io.StringIO(), err=io.StringIO(), interactive=False)
    import threading
    active, peak = [0], [0]
    lock = threading.Lock()

    def fake(kind, title, detail):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return "yes", ""
    t.keys_factory = lambda: None
    t._approve = fake
    t.raw_ok = lambda: False
    ths = [threading.Thread(target=t.approve, args=("bash", "x", "y")) for _ in range(4)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert peak[0] == 1
