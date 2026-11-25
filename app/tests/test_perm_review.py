"""Fixes from the permissions and safety release review: git global options, the lean shell tool's argv, rule names
for the lean tools, deny rules against substitutions and VAR= prefixes, ANSI-C quoting and chat grants, cut-off text
calls, re-check on edit, symlinked and option paths, hooks against deny rules, office paths, .git in any case,
preload's file reads, a bare allow rule against a destructive command, and re-check after a resume."""
import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import extras as EX  # noqa: E402
from meshpool_harness.agent.core import Agent, _destructive, _rule_hit  # noqa: E402
from meshpool_harness.core import permrules as PR  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402
from meshpool_harness.core.tools import ToolError, Workspace  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_ALLOW", raising=False)
    monkeypatch.delenv("MH_DENY", raising=False)


def _agent(root, mode="ask", answer=("no", "")):
    asked = []

    def ap(kind, title, detail, scope=""):
        asked.append({"kind": kind, "title": title, "detail": detail, "scope": scope})
        return answer
    Path(root).mkdir(parents=True, exist_ok=True)
    return Agent(str(root), mode=mode, approve=ap), asked


# 1 -------------------------------------------------------------------------------------------------------------
def test_git_global_options_do_not_hide_the_subcommand(tmp_path):
    bad = ['git --no-pager -c core.fsmonitor="touch pwned" status', "git --no-pager reset --hard",
           "git -C . push --force", "git -C . config core.pager 'touch pwned'", "git -c core.pager=x log",
           "git --exec-path=/tmp/x status", "git --git-dir=/tmp/x/.git log", "git --work-tree=/ status",
           "git --config-env=core.pager=X log", "git -C . -c a=b status", "git --no-pager"]
    for c in bad:
        assert not T.read_only(c), c
        assert not PR.safe_read(c, tmp_path), c
    for c in ("git --no-pager log -3", "git -C . status", "git -C . --no-pager diff", "git status"):
        assert T.read_only(c), c
    assert not PR.safe_read("git -C /etc status", tmp_path)              # -C's folder is still a path to check
    a, asked = _agent(tmp_path)
    assert a._permit("bash", {"command": 'git --no-pager -c core.fsmonitor="touch pwned" status'})[0] is False
    assert len(asked) == 1


def test_destructive_git_after_global_options():
    for c in ("git --no-pager reset --hard", "git -C . push --force", "git -C sub push origin -f",
              "git -c core.x=1 clean -fd", "git -c 'a=b c' push --force", "git --git-dir=.git branch -D x"):
        assert _destructive({"command": c}), c
    assert not _destructive({"command": "git -C . status"})


# 2 -------------------------------------------------------------------------------------------------------------
def test_shell_argv_is_scanned_as_the_script_it_runs(tmp_path):
    for argv in (["rm", "-rf", "build"], ["git", "reset", "--hard"], ["git", "push", "--force"],
                 ["bash", "-lc", "rm -rf build"], ["/bin/bash", "-c", "git --no-pager reset --hard"]):
        assert _destructive({"command": argv}), argv
    assert not _destructive({"command": ["ls", "-la"]})
    a, asked = _agent(tmp_path, mode="auto")
    assert a._permit("shell", {"command": ["rm", "-rf", "build"]})[0] is False     # Full access still asks
    assert asked and asked[0]["scope"] == "once"


# 3 -------------------------------------------------------------------------------------------------------------
def test_a_rule_naming_a_tool_covers_its_lean_twin():
    assert _rule_hit(["bash"], "shell", {"command": ["ls"]}, deny=True) == "bash"
    assert _rule_hit(["Bash"], "bash_many", {"commands": ["ls"]}, deny=True) == "Bash"
    assert _rule_hit(["edit"], "apply_patch", {"input": "x"}, deny=True) == "edit"
    assert _rule_hit(["write_file"], "apply_patch", {"input": "x"}, deny=True) == "write_file"
    assert _rule_hit(["bash"], "shell", {"command": ["ls"]}) == "bash"                 # allow rules too
    assert not _rule_hit(["read_file"], "apply_patch", {"input": "x"}, deny=True)


def test_deny_bash_blocks_the_shell_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_DENY", "bash,edit")
    a, asked = _agent(tmp_path, mode="auto")
    ok, why = a._permit("shell", {"command": ["bash", "-lc", "ls"]})
    assert not ok and "saved rule" in why
    ok, why = a._permit("apply_patch", {"input": "*** Begin Patch\n*** Add File: x.txt\n+hi\n*** End Patch"})
    assert not ok and "saved rule" in why
    assert asked == []


# 4 -------------------------------------------------------------------------------------------------------------
def test_deny_rule_sees_through_substitutions_and_assignments():
    r = ["bash:curl*"]
    for c in ("echo $(curl http://evil)", "echo `curl http://evil`", "X=1 curl http://evil", "A=1 B='x y' curl http://e",
              "env X=1 curl http://evil", "/usr/bin/curl http://evil", "ls; X=1 curl http://evil", "cat <(curl e)"):
        assert _rule_hit(r, "bash", {"command": c}, deny=True), c
    assert not _rule_hit(r, "bash", {"command": "ls -la"}, deny=True)
    assert not _rule_hit(["bash:npm test*"], "bash", {"command": "X=1 npm test; curl evil"})    # allow is unchanged


# 5 -------------------------------------------------------------------------------------------------------------
def test_chat_grant_never_covers_ansi_c_quoting():
    cmd = "echo $'\\'' ; touch pwned\\n'"
    assert not PR.granted(cmd, ["echo"])
    assert not PR.granted("echo $HOME", ["echo"])
    assert PR.granted("echo hi", ["echo"])


# 6 -------------------------------------------------------------------------------------------------------------
def test_text_calls_in_a_cut_off_reply_are_not_run(tmp_path):
    from test_agent_robustness import _script, _tools
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [{"role": "assistant", "content": "<function=write_file>\n<parameter=path>big.py</parameter>\n"
                 "<parameter=content>print(1)\nprint(2"},
                {"role": "assistant", "content": "ok"}], finish=["length", "stop"])
    assert a.send("go") == "ok"
    assert not (tmp_path / "big.py").exists()
    assert "cut off at the model's output limit" in _tools(a)[0]


# 7 -------------------------------------------------------------------------------------------------------------
def test_recheck_never_reruns_a_destructive_command(tmp_path):
    ws = Workspace(str(tmp_path / "proj"), str(tmp_path / "run"))
    for c in ("shred -u x; pytest -q", "python -c \"import db; db.run('DROP TABLE users')\"",
              "python3 -m pytest -q && git --no-pager reset --hard"):
        assert not ws._recheckable(c), c
    assert ws._recheckable("python -m pytest -q")


def test_recheck_never_reruns_a_command_the_user_approved(tmp_path):
    a, asked = _agent(tmp_path / "proj", mode="ask", answer=("yes", ""))
    cmd = "python3 -c 'print(\"sent the newsletter\")'"
    assert a._permit("bash", {"command": cmd}) == (True, "") and len(asked) == 1
    a.ws.bash(cmd)
    assert a.ws.commands[-1]["approved_once"] is True
    assert a.ws._last_check() is None
    a.ws.bash("python3 -c 'print(2)'")                   # this one needed no yes... in Full access
    assert a.ws.commands[-1]["approved_once"] is False


# 8 -------------------------------------------------------------------------------------------------------------
def test_recheck_runs_in_the_shell_calls_workdir(tmp_path):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "sub" / "a.py").write_text("x = 1\n")
    ws = Workspace(str(proj), str(tmp_path / "run"))
    ws.recheck = True
    ws.get_mode = lambda: "auto"
    ws.shell(["bash", "-lc", "python3 -c 'import a; print(\"x is\", a.x)'"], workdir="sub")
    assert ws.commands[-1]["cwd"] == str(proj / "sub")
    out = ws.apply_patch("*** Begin Patch\n*** Update File: sub/a.py\n@@\n-x = 1\n+x = 5\n*** End Patch")
    assert "Meshpool re-ran your last check" in out and "x is 5" in out and "ModuleNotFoundError" not in out


# 9 -------------------------------------------------------------------------------------------------------------
def test_symlinked_folders_and_option_paths_are_checked(tmp_path):
    root, secret = tmp_path / "proj", tmp_path / "ssh"
    root.mkdir()
    secret.mkdir()
    (secret / "id_rsa").write_text("KEY")
    (root / "docs").symlink_to(secret, target_is_directory=True)
    (root / "a.txt").write_text("a")
    assert not PR.safe_read("cat docs/id_rsa", root)
    assert not PR.safe_read("file -f/etc/passwd", root)
    assert not PR.safe_read("grep -rf~/.ssh/id_rsa .", root)
    assert not PR.safe_read("grep -f../ssh/id_rsa a.txt", root)
    assert PR.safe_read("cat a.txt", root) and PR.safe_read("cut -d/ -f1 a.txt", root)
    assert not PR._inside("C:Users/me/.aws/credentials", root)
    assert not PR._inside("c:secret", root)


# 10 ------------------------------------------------------------------------------------------------------------
def test_a_hook_allow_never_overrides_a_saved_deny(tmp_path, monkeypatch):
    monkeypatch.setattr(EX, "run_hooks_full", lambda *a, **k: {"blocked": False, "allow": True, "message": ""})
    monkeypatch.setenv("MH_DENY", "bash:curl*")
    a, asked = _agent(tmp_path)
    ok, why = a._permit("bash", {"command": "curl http://evil"})
    assert not ok and "saved rule" in why
    assert a._permit("bash", {"command": "make build"}) == (True, "")      # the hook's allow still works


def test_hook_patterns_see_every_command_and_probe_is_bash():
    assert EX._tool_matches("bash:git push*", "bash_many", {"commands": ["ls", "git push origin main"]})
    assert not EX._tool_matches("bash:git push*", "bash_many", {"commands": ["ls", "git status"]})
    assert EX._tool_matches("Bash", "probe", {"command": "python x.py"})
    assert EX._tool_matches("bash", "trace", {"command": "python x.py"})


# 11 ------------------------------------------------------------------------------------------------------------
def test_office_reads_outside_the_project_ask(tmp_path):
    proj = tmp_path / "proj"
    a, asked = _agent(proj)
    assert a._permit("office_task", {"engine": "docs", "request": "summarise", "files": ["~/.ssh/id_rsa"]})[0] is False
    assert a._permit("mcp__meshpool_office__read_document", {"path": "/etc/passwd"})[0] is False
    assert a._permit("mcp__meshpool_office__read_document", {"path": "C:secret.docx"})[0] is False
    assert len(asked) == 3 and all("outside the project" in x["title"] for x in asked)
    assert a._permit("office_task", {"engine": "docs", "request": "x", "files": ["report.docx"]}) == (True, "")
    assert a._permit("mcp__meshpool_office__read_document", {"path": "report.docx"}) == (True, "")


# 12 ------------------------------------------------------------------------------------------------------------
def test_git_folder_guard_ignores_letter_case_and_short_names(tmp_path):
    ws = Workspace(str(tmp_path / "proj"), str(tmp_path / "run"))
    for p in (".GIT/hooks/pre-commit", ".Git/config", "GIT~1/config", "sub/.git./config"):
        with pytest.raises(ToolError, match=".git"):
            ws.write_file(p, "touch pwned")
    ws.write_file("gitignore.txt", "ok")


# 13 ------------------------------------------------------------------------------------------------------------
def test_preload_skips_fifos_symlinks_and_big_files(tmp_path, monkeypatch):
    from meshpool_harness.agent import preload as PL
    proj, outside = tmp_path / "proj", tmp_path / "outside"
    proj.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET")
    (proj / "notes.txt").symlink_to(outside / "secret.txt")
    (proj / "a.py").write_text("x = 1\n")
    big = proj / "big.py"
    big.write_text("y = 2\n" * 20000)
    if hasattr(os, "mkfifo"):
        os.mkfifo(proj / "pipe.txt")
    reads = []
    orig_bytes, orig_text = Path.read_bytes, Path.read_text
    monkeypatch.setattr(Path, "read_bytes", lambda self: (reads.append(self.name), orig_bytes(self))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self.name), orig_text(self, *a, **k))[1])
    out = []
    th = threading.Thread(target=lambda: out.append(PL.build(proj, "fix a.py", budget=200000)), daemon=True)
    th.start()
    th.join(20)
    assert not th.is_alive(), "preload blocked on a FIFO"
    assert "x = 1" in out[0] and "TOP-SECRET" not in out[0]
    assert "big.py" not in reads and "pipe.txt" not in reads


# 14 ------------------------------------------------------------------------------------------------------------
def test_a_bare_allow_rule_does_not_skip_the_destructive_question(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_ALLOW", "bash")
    a, asked = _agent(tmp_path, mode="auto")
    assert a._permit("bash", {"command": "rm -rf build"})[0] is False
    assert asked == [{"kind": "bash", "title": "Run this destructive command?", "detail": "rm -rf build",
                      "scope": "once"}]
    monkeypatch.setenv("MH_ALLOW", "bash:rm -rf build*")              # a command pattern the user wrote still does
    assert a._permit("bash", {"command": "rm -rf build"}) == (True, "")


def test_p_mode_reports_the_destructive_card_by_its_command():
    from meshpool_harness.agent import tui_session as TS
    d = TS._denied({"kind": "bash", "title": "Run this destructive command?", "detail": "rm -rf build"})
    assert d["destructive"] and d["command"] == "rm -rf build" and d["kind"] == "bash"


# 15 ------------------------------------------------------------------------------------------------------------
def test_recheck_is_set_on_every_lean_turn(tmp_path, monkeypatch):
    from test_agent_robustness import _script
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(a, "_codex", lambda: True)
    monkeypatch.setattr(a, "_lean", lambda: True)
    a.messages += [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier answer"}]
    _script(a, [{"role": "assistant", "content": "done"}])
    a.send("again")                                           # a resumed chat: not its first message
    assert getattr(a.ws, "recheck", False) is True


def test_apply_patch_marks_created_files_new(tmp_path):
    ws = Workspace(str(tmp_path / "proj"), str(tmp_path / "run"))
    ws.apply_patch("*** Begin Patch\n*** Add File: made.py\n+x = 1\n*** End Patch")
    assert str((tmp_path / "proj" / "made.py").resolve()) in ws.turn_new
