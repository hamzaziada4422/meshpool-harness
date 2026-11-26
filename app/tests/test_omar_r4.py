"""Omar, round 4: the receipt names the last test run after the last edit, not the last command."""
from types import SimpleNamespace

import pytest

from meshpool_harness.agent import core

PYTEST_OK = "..............\n14 passed in 0.3s\n"
PYTEST_2F = ("FAILED tests/test_a.py::test_one - assert 1 == 2\nFAILED tests/test_a.py::test_two - boom\n"
             "2 failed, 14 passed in 0.5s\n")


def _receipt(tmp_path, cmds, edits=1, edits0=0):
    f = tmp_path / "ops.py"
    f.write_text("x = 1\n")
    ws = SimpleNamespace(turn_files={str(f)}, turn_new={str(f)}, edits=edits, turn_edits0=edits0, turn_cmd0=0,
                         commands=cmds)
    agent = SimpleNamespace(ws=ws, root=str(tmp_path), _turn_line_stats=lambda: {"ops.py": "+1 -0"})
    return core.Agent.receipt(agent)


def _cmd(cmd, code, tail, after_edit=1):
    return {"cmd": cmd, "code": code, "kind": "bash", "after_edit": after_edit, "tail": tail}


@pytest.mark.parametrize("ro", ["git diff --stat", "git status --short", "cat ops.py", "cat tests/test_a.py | head -5"])
def test_a_read_only_command_after_the_tests_is_not_the_check(tmp_path, ro):
    text = _receipt(tmp_path, [_cmd("python -m pytest -q", 0, PYTEST_OK), _cmd(ro, 0, "")])
    assert "Verified: pytest, 14 passed" in text
    assert ro not in text


def test_the_last_test_run_wins_over_an_earlier_one(tmp_path):
    text = _receipt(tmp_path, [_cmd("pytest -q", 1, PYTEST_2F), _cmd("pytest -q", 0, PYTEST_OK), _cmd("git diff", 0, "")])
    assert "Verified: pytest, 14 passed" in text and "failed" not in text


@pytest.mark.parametrize("runner,tail,want", [
    ("npm test", "Tests: 3 passed, 3 total\n", "Verified: npm test, 3 passed"),
    ("go test ./...", "ok  \tpkg\t0.1s\n", None),
    ("python -m unittest", "Ran 6 tests in 0.1s\n\nOK\n", "Verified: unittest, 6 passed"),
])
def test_other_runners_count_too(tmp_path, runner, tail, want):
    text = _receipt(tmp_path, [_cmd(runner, 0, tail), _cmd("git log --oneline -3", 0, "abc x")])
    assert "git log" not in text
    if want:
        assert want in text
    else:
        assert f"`{runner}` passed" in text


def test_failures_that_were_there_before_the_first_edit_do_not_make_it_not_verified(tmp_path):
    before = _cmd("pytest -q", 1, PYTEST_2F, after_edit=0)
    after = _cmd("pytest -q", 1, PYTEST_2F, after_edit=1)
    text = _receipt(tmp_path, [before, after, _cmd("git diff --stat", 0, "")])
    assert "Verified: pytest, 14 passed (2 failures were there before this change)" in text
    assert "Not verified" not in text


def test_a_new_failure_is_still_not_verified(tmp_path):
    before = _cmd("pytest -q", 1, "FAILED tests/test_a.py::test_one - x\n1 failed, 15 passed in 1s\n", after_edit=0)
    after = _cmd("pytest -q", 1, PYTEST_2F, after_edit=1)
    text = _receipt(tmp_path, [before, after])
    assert "Not verified: pytest, 2 failed, 14 passed" in text


def test_without_the_earlier_ids_it_says_the_counts_and_not_not_verified(tmp_path):
    text = _receipt(tmp_path, [_cmd("pytest -q", 1, PYTEST_2F)])
    assert "pytest: 2 failed, 14 passed" in text
    assert "Not verified" not in text


def test_one_error_is_an_error(tmp_path):
    assert core._test_summary("pytest -q", "5 passed, 1 error in 0.2s") == "pytest, 1 error, 5 passed"
    assert core._test_summary("pytest -q", "5 passed, 2 errors in 0.2s") == "pytest, 2 errors, 5 passed"
    assert "Verified" not in core._check_line("pytest -q", "pytest -q", 0, "5 passed, 1 error in 0.2s")


@pytest.mark.parametrize("cmd", ["pytest -q | tail -3", "pytest -q || true"])
def test_a_piped_or_ignored_test_run_without_a_clean_summary_is_not_verified(cmd):
    line = core._check_line(cmd, cmd, 0, "")
    assert line.startswith("Not verified:") and "Verified:" not in line.replace("Not verified:", "")
    assert core._check_line(cmd, cmd, 0, PYTEST_OK).startswith("Verified: pytest, 14 passed")


# ---------------------------------------------------------------------------------------------- item 2
import json  # noqa: E402


def _scripted_agent(root, replies, mode="edits"):
    a = core.Agent(str(root), mode=mode, approve=None, emit=lambda e: None)
    seq = list(replies)

    def fake(base, key, body, stream):
        msg = seq.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, bool(msg.get("content"))
    a._post = fake
    return a


def _tool_call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def _quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_SANDBOX", "off")


def test_a_denied_write_command_in_p_tells_the_model_to_use_apply_patch(tmp_path, _quiet):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "x.py").write_text("a = 1\n")
    a = _scripted_agent(root, [
        _tool_call("bash", {"command": "cat >> tests/x.py <<'EOF'\nb = 2\nEOF\npython -m pytest -q"}),
        _tool_call("edit", {"path": "tests/x.py", "old": "a = 1\n", "new": "a = 1\nb = 2\n"}, "c2"),
        {"role": "assistant", "content": "done"}])
    a.send("add a test")
    outs = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert "The user denied this action" in outs[0]
    assert "Make the same change with edit" in outs[0]
    assert (root / "tests" / "x.py").read_text() == "a = 1\nb = 2\n"        # the model finished with the patch


def test_a_denied_command_that_writes_nothing_gets_no_edit_tool_hint(tmp_path, _quiet):
    root = tmp_path / "proj"
    root.mkdir()
    a = _scripted_agent(root, [_tool_call("bash", {"command": "make install"}), {"role": "assistant", "content": "ok"}])
    a.send("go")
    outs = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert "The user denied this action" in outs[0] and "same change" not in outs[0]


# ---------------------------------------------------------------------------------------------- item 3
import io  # noqa: E402

from meshpool_harness.agent import tui as TUI  # noqa: E402

RECEIPT = ("**Changed:** ledgerly/money.py (+3 -2)  \n"
           "**Checked:** Verified: pytest, 14 passed (1 command after the last change)")


def test_p_output_shows_the_receipt_without_asterisks():
    out, err = io.StringIO(), io.StringIO()
    t = TUI.Terminal(print_mode=True, out=out, err=err)
    t.emit({"type": "receipt", "text": RECEIPT})
    text = out.getvalue() + err.getvalue()
    assert "*" not in text
    assert "Changed: ledgerly/money.py (+3 -2)" in text
    assert "Checked: Verified: pytest, 14 passed" in text


def test_the_terminal_ui_shows_bold_labels_not_asterisks(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    from meshpool_harness.agent.tui_render import Style, receipt_line
    line = receipt_line("**Checked:** Verified: pytest, 14 passed", Style(256), 80)
    assert "*" not in line
    assert "\x1b[1mChecked:" in line                       # terminal bold on the label
    assert receipt_line("**Checked:** ok", Style(0), 80) == "Checked: ok"
