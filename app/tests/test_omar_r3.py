"""Omar (developer persona), round 3: a doubled history move, the verification line, a bool where a command goes,
local commits written with a heredoc, tips, and a promise followed by a malformed call. Scripted model, no network."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core as A  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _agent(root, replies, mode="auto"):
    a = A.Agent(str(root), mode=mode)
    seq, seen = list(replies), []

    def fake(base, key, body, stream):
        seen.append(body)
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    return a, seen


def _c171(root, mode="auto"):
    from meshpool_harness.hrouter import profiles as PROF
    from meshpool_harness.hrouter.route import Route
    a = A.Agent(str(root), mode=mode)
    a.set_route(Route(spec="code.debug", family="code", sub="debug", base="code", profile="c171",
                      settings=PROF.settings("c171")), "fix it")           # the profile that has exec_command
    return a


def _run(a, name, args, i=1):
    return a._run_tool({"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}})


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def _repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "o@x.io")
    _git(root, "config", "user.name", "o")
    for n in "abc":
        (root / f"{n}.txt").write_text(n)
        _git(root, "add", ".")
        _git(root, "commit", "-qm", f"add {n}")


# ---------------------------------------------------------------------------------------------- 1: a doubled reset
def test_1_the_same_history_move_twice_in_one_reply_runs_once(tmp_path):
    _repo(tmp_path)
    reset = {"command": "git reset --mixed HEAD~1"}
    a, seen = _agent(tmp_path, [
        {"role": "assistant", "content": "", "tool_calls": [_call(1, "bash", reset), _call(2, "bash", reset)]},
        {"role": "assistant", "content": "Done."}])
    a.send("undo the last commit")
    assert _git(tmp_path, "rev-list", "--count", "HEAD").strip() == "2"      # one commit undone, not two
    tools = [m["content"] for m in a.messages if m.get("role") == "tool"]
    assert tools[1].startswith("Skipped: this is the same command you just ran."), tools


def test_1_a_look_in_between_allows_the_same_move_again(tmp_path):
    _repo(tmp_path)
    reset = {"command": "git reset --mixed HEAD~1"}
    a, _ = _agent(tmp_path, [
        {"role": "assistant", "content": "", "tool_calls": [_call(1, "bash", reset),
                                                          _call(2, "bash", {"command": "git log --oneline"}),
                                                          _call(3, "bash", reset)]},
        {"role": "assistant", "content": "Done."}])
    a.send("undo two commits")
    assert _git(tmp_path, "rev-list", "--count", "HEAD").strip() == "1"


def test_1_history_key_names_the_moves():
    for c in ("git reset --hard HEAD~1", "git rebase main", "git checkout main", "git revert abc123",
              "git commit --amend --no-edit", "git branch -D old", "git stash drop", "cd x && git reset --mixed HEAD~1"):
        assert A._history_key([c]), c
    for c in ("git status", "git checkout -- a.py", "git commit -m x", "git branch", "git log", "ls"):
        assert not A._history_key([c]), c


# ------------------------------------------------------------------------------------- 3: a bool where a command goes
def test_3_a_bool_for_cmd_is_a_plain_tool_error_to_the_model(tmp_path):
    a = _c171(tmp_path)
    for bad in (True, False, None, 7):
        out = _run(a, "exec_command", {"cmd": bad})
        assert out.startswith("ERROR: exec_command: `cmd` must be text"), out
        assert "argument 'cmd'" not in out and "got bool" not in out and "send the command line itself" in out.lower()
    assert _run(a, "exec_command", {"cmd": "echo hi"}).count("hi") >= 1          # a real command still runs


def test_3_the_tool_workspace_itself_does_not_say_empty_for_a_bool(tmp_path):
    ws = T.Workspace(tmp_path, tmp_path / ".run")
    with pytest.raises(T.ToolError, match="must be the command line as text"):
        ws.exec_command(True)


# -------------------------------------------------------------------------- 4: a commit message written as a heredoc
MSG = "Add --currency option\n\nPass it to render. See ~/notes and $HOME and `x`.\n\nCo-Authored-By: A <a@b.c>"
COMMIT_F = "git add -A && git commit -F - <<'EOF'\n" + MSG + "\nEOF"
COMMIT_M = "git add -A && git commit -m \"$(cat <<'EOF'\n" + MSG + "\nEOF\n)\" && git status --short"


def _local_repo(tmp_path, monkeypatch):
    from meshpool_harness.core import sandbox as SB
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1")
    monkeypatch.delenv("MH_SANDBOX", raising=False)
    monkeypatch.setattr(SB, "available", lambda *a, **k: True)          # as test_sandbox_auto does
    home = tmp_path / "home"
    proj = home / "work"
    proj.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _git(proj, "init", "-q")
    (proj / "a.py").write_text("x = 1\n")
    return proj


@pytest.mark.parametrize("mode", ["ask", "edits"])
@pytest.mark.parametrize("tool,key", [("bash", "command"), ("exec_command", "cmd")])
@pytest.mark.parametrize("cmd", [COMMIT_F, COMMIT_M], ids=["dash_F", "dash_m_subst"])
def test_4_a_local_commit_with_a_heredoc_message_runs_in_p_mode(tmp_path, monkeypatch, mode, tool, key, cmd):
    proj = _local_repo(tmp_path, monkeypatch)
    a = A.Agent(str(proj), mode=mode, approve=None)                    # -p: nobody can approve
    ok, why = a._permit(tool, {key: cmd})
    assert ok, why


@pytest.mark.parametrize("cmd", [COMMIT_F, COMMIT_M], ids=["dash_F", "dash_m_subst"])
def test_4_the_rule_for_local_git_writes_knows_the_heredoc_forms(tmp_path, monkeypatch, cmd):
    from meshpool_harness.core import permrules as PR
    proj = _local_repo(tmp_path, monkeypatch)
    assert PR.git_local_ok(cmd, proj)


@pytest.mark.parametrize("cmd", [
    "git commit -F - <<EOF\nmsg\nEOF",                                        # an unquoted delimiter expands $(...)
    "git commit -F - <<'EOF'\nmsg\nEOF\ngit push origin main",
    "git commit -F - <<'EOF' && git push\nmsg\nEOF",
    "git commit -m x <<'EOF'\nrm -rf x\nEOF",
    "git commit -F /etc/passwd",
    "cat > a.py <<'EOF'\nx\nEOF\ngit commit -m x",
], ids=["unquoted", "push_after", "push_same_line", "heredoc_to_other_cmd", "file_outside", "other_heredoc"])
def test_4_other_forms_still_ask(tmp_path, monkeypatch, cmd):
    from meshpool_harness.core import permrules as PR
    proj = _local_repo(tmp_path, monkeypatch)
    assert not PR.git_local_ok(cmd, proj)


# ------------------------------------------------------------------------------------------------- 5: mode wording
def test_5_the_terminal_footer_uses_the_mode_words_of_config():
    from meshpool_harness import config as C
    from meshpool_harness.agent import tui_render as R
    from meshpool_harness.agent.tui_render import Style
    assert set(C.MODE_FOOTER) == set(C.MODE_INFO)
    st = Style(0)
    for key, (name, _) in C.MODE_INFO.items():
        line = R.mode_hint(st, key)
        assert f"{name}: {C.MODE_FOOTER[key]}" in line, line
        assert "ask before changes" not in line
    assert "Ask: runs in this folder, asks before risky steps" in R.mode_hint(st, "ask")


def test_5_permission_mode_help_shows_both_names():
    out = subprocess.run([sys.executable, "-m", "meshpool_harness", "--help"], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], timeout=60).stdout
    flat = " ".join(out.split())
    assert "ask (Ask" in flat and "edits (Accept edits)" in flat and "auto (Full access)" in flat and "plan" in flat, flat


# --------------------------------------------------------------------------------------------------- 6: Show tips
def test_6_show_tips_is_a_setting_on_by_default_and_the_terminal_follows_it(monkeypatch, tmp_path):
    from meshpool_harness import config as C
    from meshpool_harness.ui import settings_v2 as SV2
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from testkit import cli_v2_shots as S
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MH_TIPS", "1")                               # restored when the test ends
    assert C.tips_on() is True
    row = next(f for f in SV2.capabilities() if f["id"] == "tips")
    assert row["name"] == "Show tips" and row["on"] is True
    assert 'Try "' in S.startup_scene()[2].text()                    # a tip in the terminal
    SV2.set_capability("tips", False)                               # the switch in Settings
    try:
        assert C.tips_on() is False
        assert 'Try "' not in S.startup_scene()[2].text()
        assert next(f for f in SV2.capabilities() if f["id"] == "tips")["on"] is False
    finally:
        SV2.set_capability("tips", True)
    assert C.tips_on() is True and 'Try "' in S.startup_scene()[2].text()


def test_6_the_environment_wins_for_one_process(monkeypatch):
    from meshpool_harness import config as C
    monkeypatch.setenv("MH_TIPS", "0")
    assert C.tips_on() is False
    monkeypatch.setenv("MH_TIPS", "1")
    assert C.tips_on() is True


# ----------------------------------------------------------------------------------- 2: the verification line
def _project(root):
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
                                       "def test_zero():\n    assert add(0, 0) == 0\n")


PATCH_SCRIPT = ("apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: calc.py\n@@\n-    return a - b\n+    return a + b\n"
                "*** End Patch\nEOF\npython -m pytest -q 2>&1 | tail -5")


def test_2_a_test_run_in_the_same_step_as_the_edit_verifies_it(tmp_path):
    _project(tmp_path)
    a = _c171(tmp_path)
    _run(a, "exec_command", {"cmd": PATCH_SCRIPT})
    r = a.receipt()
    assert "not verified" not in r, r
    assert "Verified: pytest, 2 passed" in r, r


def test_2_a_script_that_writes_then_tests_counts_as_tested():
    assert A._tests_after_own_edit("python - <<'PY'\nopen('a.py','w').write('x=1')\nPY\npython -m pytest -q")
    assert not A._tests_after_own_edit("python -m pytest -q && sed -i 's/a/b/' a.py")
    assert not A._tests_after_own_edit("python -m pytest -q")


def test_2_a_search_with_no_match_is_not_a_failure():
    line = A._check_line("rg -n currency README.md", "rg -n currency README.md", 1, "")
    assert "FAILED" not in line and "no match" in line, line
    assert "FAILED" in A._check_line("python check.py", "python check.py", 1, "")
    assert "FAILED" in A._check_line("grep -n x f | wc -l; false", "grep -n x f", 1, "")


def test_2_the_receipt_skips_a_no_match_search_and_names_the_test_run(tmp_path):
    _project(tmp_path)
    a = _c171(tmp_path)
    (tmp_path / "README.md").write_text("an adder\n")
    _run(a, "exec_command", {"cmd": PATCH_SCRIPT})
    _run(a, "exec_command", {"cmd": "rg -n currency README.md"}, 2)
    r = a.receipt()
    assert "FAILED" not in r and "Verified: pytest, 2 passed" in r, r


def test_2_the_runner_and_counts_are_named():
    assert A._check_line("pytest -q", "pytest -q", 0, "14 passed in 0.2s").startswith("Verified: pytest, 14 passed")
    assert A._check_line("python -m pytest | tail", "python -m pytest | tail", 0,
                         "2 failed, 14 passed in 1s").startswith("Not verified: pytest, 2 failed, 14 passed")
