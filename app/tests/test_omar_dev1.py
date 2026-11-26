"""Omar (developer persona) fixes, part 1: piped -p output."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import tui as T  # noqa: E402


class _Tty(io.StringIO):
    def isatty(self):
        return True


def _script(t):
    t.emit({"type": "model_start"})
    t.emit({"type": "text_delta", "text": "Let me look at the project first."})
    t.emit({"type": "assistant", "text": "Let me look at the project first.", "has_tools": True, "streamed": True})
    t.emit({"type": "tool_start", "id": "a", "name": "exec_command", "summary": "ls"})
    t.emit({"type": "tool_end", "id": "a", "name": "exec_command", "ok": True, "output": "x"})
    t.emit({"type": "model_start"})
    t.emit({"type": "assistant", "text": "Now I will read the tests.", "has_tools": True})
    t.emit({"type": "tool_start", "id": "b", "name": "exec_command", "summary": "cat t"})
    t.emit({"type": "tool_end", "id": "b", "name": "exec_command", "ok": True, "output": "y"})
    t.emit({"type": "model_start"})
    t.emit({"type": "text_delta", "text": "The answer is 42."})
    t.emit({"type": "assistant", "text": "The answer is 42.", "streamed": True})
    t.emit({"type": "turn_end"})
    t.flush_held()


def test_p_stdout_holds_only_the_final_answer():
    out, err = io.StringIO(), io.StringIO()
    t = T.Terminal(print_mode=True, out=out, err=err, interactive=False)
    _script(t)
    assert out.getvalue() == "The answer is 42.\n"
    assert "Let me look" not in err.getvalue()          # stderr is not a terminal: narration goes nowhere


def test_p_narration_goes_to_stderr_when_it_is_a_terminal():
    out, err = io.StringIO(), _Tty()
    t = T.Terminal(print_mode=True, out=out, err=err, interactive=False)
    _script(t)
    assert out.getvalue() == "The answer is 42.\n"
    assert "Let me look at the project first." in err.getvalue()
    assert "Now I will read the tests." in err.getvalue()
