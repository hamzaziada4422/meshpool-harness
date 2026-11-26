"""0.0.18 bug hunt, area AG (the agent loop and its tools). Each test is one bug; it failed before its fix."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import trust as TR  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    (tmp_path / "home").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _ws(tmp_path):
    return T.Workspace(str(tmp_path), str(tmp_path / ".run"))


def _asking_agent(root, mode="ask"):
    asked = []
    a = core.Agent(str(root), approve=lambda *x, **k: (asked.append(x[1]), ("no", ""))[1], mode=mode)
    return a, asked


# AG-01: read_file, grep and the syntax-error excerpt split lines with str.splitlines, which also splits at form feeds
# (common in Python and C sources) and U+2028, so every later line number disagreed with tracebacks and editors.
def test_line_numbers_count_newlines_only(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n\x0c\nb = 2\nc = 3\n", encoding="utf-8")
    ws = _ws(tmp_path)
    out = ws.read_file("a.py", start=3, end=3)
    assert out.startswith("a.py lines 3-3 of 4") and out.rstrip().endswith("b = 2")
    assert ws.grep("c = 3", "a.py") == "a.py:4:c = 3"
    assert ws.grep(r"b = 2\nc", "a.py", multiline=True) == "a.py:3:b = 2"
    (tmp_path / "u.txt").write_text("x y\nz\n", encoding="utf-8")
    assert ws.grep("z", "u.txt") == "u.txt:2:z"
    (tmp_path / "mac.txt").write_bytes(b"one\rtwo\r")              # CR-only endings still give lines
    assert ws.grep("two", "mac.txt") == "mac.txt:2:two"
    bad = T.Workspace._syntax_error(Path("b.py"), "x = 1\n\x0c\ny = (\n")
    assert bad and "y = (" in bad.split("\n")[1]


# AG-02: apply_patch could not touch the first line of a file that starts with a UTF-8 BOM (the BOM was part of the
# line, so no context matched).
def test_apply_patch_matches_and_keeps_a_bom(tmp_path):
    p = tmp_path / "a.py"
    p.write_bytes(b"\xef\xbb\xbfimport os\r\nx = 1\r\n")
    ws = _ws(tmp_path)
    out = ws.apply_patch("*** Begin Patch\n*** Update File: a.py\n@@\n-import os\n+import sys\n x = 1\n*** End Patch")
    assert out.startswith("Success")
    assert p.read_bytes() == b"\xef\xbb\xbfimport sys\r\nx = 1\r\n"


# AG-03: the output of a command that timed out went to the model without secret redaction (OSS-29 covered only
# commands that ended by themselves).
def test_timed_out_output_is_redacted(tmp_path):
    ws = _ws(tmp_path)
    secret = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    out = ws.bash(f"echo ANTHROPIC_API_KEY={secret}; sleep 30", timeout_s=2)
    assert "timed out" in out and secret not in out


# AG-04: bash_start / bash_output returned a background command's output without secret redaction.
def test_background_output_is_redacted(tmp_path):
    ws = _ws(tmp_path)
    secret = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    out = ws.bash_start(f"sleep 0.2; echo ANTHROPIC_API_KEY={secret}; sleep 0.5; echo again {secret}; sleep 5")
    out += ws.bash_output("bg1", wait_s=3)
    try:
        assert "again" in out and secret not in out
    finally:
        ws.bash_stop()


# AG-05: bash_start read a background command's output in the locale's code page; on Windows that is cp1252, so the
# UTF-8 that Git Bash writes came back as mojibake.
def test_background_output_is_read_as_utf8_on_windows(tmp_path, monkeypatch):
    seen = {}

    class FakeP:
        pid, stdout = 1, iter(())

        def poll(self):
            return 0

    def popen(args, **kw):
        seen.update(kw)
        return FakeP()
    class WinOs:                                   # os as tools.py sees it, with os.name "nt"; pathlib keeps its own
        name = "nt"

        def __getattr__(self, k):
            return getattr(os, k)
    ws = _ws(tmp_path)
    monkeypatch.setattr(ws, "_scratch", lambda e: e)
    monkeypatch.setattr(ws, "_py_env", lambda e: e)
    monkeypatch.setattr(T, "_bash", lambda: "bash")
    monkeypatch.setattr(T.subprocess, "Popen", popen)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    monkeypatch.setattr(T, "os", WinOs())
    ws.bash_start("echo hi")
    assert seen.get("encoding") == "utf-8"
