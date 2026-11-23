"""OSS-30: a Windows cp1252 console or a redirected stdout must not crash the terminal UI on glyphs like the prompt arrow."""
import io

import pytest

from meshpool_harness import cli
from meshpool_harness.agent import tui as T
from meshpool_harness.agent import tui_render as R


def cp1252():
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252", errors="strict", write_through=True)


@pytest.fixture(autouse=True)
def reset_ascii():
    yield
    R.ASCII[0] = False


def test_the_old_behaviour_is_a_crash():
    _, w = cp1252()
    with pytest.raises(UnicodeEncodeError):
        w.write("❯ ")


def test_prepare_streams_reconfigures_and_falls_back_to_ascii():
    raw, w = cp1252()
    assert R.prepare_streams([w]) is False
    assert w.encoding.lower().replace("-", "") == "utf8" and R.ASCII[0] is True
    w.write("❯ anything ✓")                         # no longer raises
    assert R.safe("❯ ready …") == "> ready ..."


def test_utf8_streams_are_left_alone():
    w = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    assert R.prepare_streams([w]) is True and R.ASCII[0] is False
    assert R.safe("❯") == "❯"


def test_terminal_draws_on_a_cp1252_stream_without_crashing():
    raw, w = cp1252()
    R.prepare_streams([w])
    term = T.Terminal(print_mode=True, out=w, err=w)
    term.p("  ❯ menu … │ ✓ done · ok")
    term._write("─" * 5 + "\n")
    out = raw.getvalue().decode("utf-8", "replace")
    assert "> menu ... | v done" in out and "-----" in out


def test_cli_start_prepares_the_streams(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(R, "prepare_streams", lambda *a, **k: seen.append(1) or True)
    assert cli.main(["--version"]) == 0
    assert seen
