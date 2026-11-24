"""The agent's receipt never says a check "passed" when its exit status could not show a failure.

`pytest -q | tail -5` exits with tail's status, so a failing pytest still exits 0; the receipt said
"**Checked:** `pytest -q | tail -5` passed". It now says the command ran and that the pipe hides failures before its
last stage. How commands execute is unchanged."""
import subprocess
from types import SimpleNamespace

import pytest

from meshpool_harness.agent import core


def _receipt(tmp_path, cmd: str, code: int) -> str:
    f = tmp_path / "ops.py"
    f.write_text("x = 1\n")
    ws = SimpleNamespace(turn_files={str(f)}, turn_new={str(f)}, edits=1,
                         commands=[{"cmd": cmd, "code": code, "kind": "bash", "after_edit": 1}])
    agent = SimpleNamespace(ws=ws, root=str(tmp_path), _turn_line_stats=lambda: {"ops.py": "+1 -0"})
    return core.Agent.receipt(agent)


def test_a_failing_pytest_piped_into_tail_exits_0_without_pipefail(tmp_path):
    """The premise: the shell Meshpool uses (bash -c, no pipefail) reports tail's status."""
    r = subprocess.run(["bash", "-c", "false | tail -5"], capture_output=True)
    assert r.returncode == 0


@pytest.mark.parametrize("cmd", [
    "pytest -q | tail -5",
    "cd /proj && python -m pytest -q 2>&1 | tail -20",
    "npm test |& tee out.log",
    "cargo test 2>&1 | grep -E 'test result'",
    "python -m engines.sheets.check out.xlsx | head -3",
])
def test_piped_check_is_not_reported_as_passed(tmp_path, cmd):
    text = _receipt(tmp_path, cmd, 0)
    assert "passed" not in text
    assert "ran (exit status is the last command's in the pipe; failures before it are not visible)" in text


@pytest.mark.parametrize("cmd", [
    "set -o pipefail; pytest -q | tail -5",
    "set -euo pipefail && pytest -q | tail -5",
    "bash -o pipefail -c 'pytest -q | tail -5'",
])
def test_pipefail_makes_the_status_honest(tmp_path, cmd):
    assert f"`{cmd}` passed" in _receipt(tmp_path, cmd, 0)


@pytest.mark.parametrize("cmd", [
    "pytest -q",
    "pytest -q -k 'a or b|c'",            # the | is quoted: no pipe
    "pytest -q || echo retry",            # || is not a pipe
    "grep -c def ops.py | wc -l",         # a pipe, but not a test or check run
])
def test_unpiped_or_non_check_commands_still_pass(tmp_path, cmd):
    assert f"`{cmd}` passed" in _receipt(tmp_path, cmd, 0)


def test_a_failing_exit_is_still_reported_as_failed(tmp_path):
    text = _receipt(tmp_path, "pytest -q | tail -5", 2)
    assert "FAILED (exit 2)" in text and "passed" not in text


def test_pipe_stages_respect_quotes_and_escapes():
    assert core._pipe_stages("grep 'a|b' x") == ["grep 'a|b' x"]
    assert core._pipe_stages('echo "a|b" | wc') == ['echo "a|b" ', " wc"]
    assert core._pipe_stages(r"echo a\|b") == [r"echo a\|b"]
    assert core._pipe_stages("a || b | c") == ["a || b ", " c"]
