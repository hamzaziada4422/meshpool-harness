"""Independent review of 0.0.20, the terminal UI's keys on prompt_toolkit (agent/tui_input.py): what the old parser read
as one key must stay one key. Each test failed on the release candidate (942007e5) and passes with its fix."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testkit import cli_v2_shots as S  # noqa: E402

from meshpool_harness.agent.tui_input import FakeKeys, names, parse_keys  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.mark.parametrize("seq,key", [
    ("\x1b[1;3A", "up"), ("\x1b[1;3B", "down"), ("\x1b[1;3H", "home"), ("\x1b[1;3F", "end"),
    ("\x1b[3;3~", "delete"), ("\x1b[5;3~", "pageup"), ("\x1b[1;9A", "up"), ("\x1b[1;4D", "left"),
    ("\x1b[3;5~", "delete"), ("\x1b[1;6D", "left"), ("\x1b[1;10D", "left"), ("\x1b[1;5Z", "shift_tab"),
    ("\x1b[1;3P", "unknown"), ("\x1b[99~", "unknown"),
])
def test_alt_and_unknown_sequences_are_one_key_and_never_esc(seq, key):
    """Alt+Up read as Esc then Up: Esc clears the typed line, and while the agent works it stops the turn. A sequence
    prompt_toolkit has no name for put its letters into the line."""
    assert parse_keys(seq) == [key]
    assert parse_keys("x" + seq + "y") == ["x", key, "y"]


def test_the_esc_key_itself_is_still_esc():
    assert parse_keys("\x1b") == ["esc"] and parse_keys("\x1b\x1b[A") == ["esc", "up"]
    assert parse_keys("\x1bb") == ["word_left"] and parse_keys("\x1b[1;3D") == ["word_left"]


def test_windows_alt_with_an_arrow_is_the_arrow():
    """Windows: the console reader marks Alt with an Escape whose data is empty; the Esc key has data "\\x1b"."""
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import Keys
    assert names([KeyPress(Keys.Escape, ""), KeyPress(Keys.Up, "")]) == ["up"]
    assert names([KeyPress(Keys.Escape, ""), KeyPress(Keys.Left, "")]) == ["word_left"]
    assert names([KeyPress(Keys.Escape, "\x1b"), KeyPress(Keys.Up, "\x1b[A")]) == ["esc", "up"]


def test_alt_up_keeps_the_typed_line():
    t, out, clk = S.make_terminal()
    t.keys_factory = lambda: FakeKeys("hello", "\x1b[1;3A", "\x1b[1;3B", "!", "\r")
    assert t.read_input() == "hello!"
    t.keys_factory = lambda: FakeKeys("ab", "\x1b[1;10D", "\x1b[1;10D", "X", "\r")
    assert t.read_input() == "Xab"
