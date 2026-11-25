"""Layered instructions, hooks, custom slash commands and @file mentions."""
import json
import os
import subprocess
from pathlib import Path

from meshpool_harness.agent import extras as EX
from meshpool_harness.agent.core import Agent


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("MH_TRUST_PROJECT", "1")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)


def _trusted(monkeypatch, tmp_path):
    """A test folder the user has trusted, so its own .meshpool/hooks.json runs (see trust.py)."""
    _home(monkeypatch, tmp_path)
    from meshpool_harness import trust as TR
    TR.trust(tmp_path)


def test_instructions_come_from_home_parents_and_project(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    (tmp_path / "home" / "MESHPOOL.md").write_text("Always reply in French.", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "pkg" / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "AGENTS.md").write_text("Use tabs.\n@docs.md\n", encoding="utf-8")
    (repo / "docs.md").write_text("Run make test.", encoding="utf-8")
    (repo / "pkg" / "CLAUDE.md").write_text("This package is legacy.", encoding="utf-8")
    t = EX.instruction_text(repo / "pkg" / "sub")
    assert "French" in t and "Use tabs" in t and "Run make test" in t and "legacy" in t
    assert t.index("French") < t.index("Use tabs") < t.index("legacy")


def test_hooks_block_and_report(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path)
    (tmp_path / ".meshpool").mkdir()
    (tmp_path / ".meshpool" / "hooks.json").write_text(json.dumps({
        "PreToolUse": [{"matcher": "bash:git push*", "command": "echo no pushing here 1>&2; exit 2"}],
        "PostToolUse": [{"matcher": "edit", "command": "echo checked"}]}), encoding="utf-8")
    blocked, msg = EX.run_hooks(tmp_path, "PreToolUse", {}, tool="bash", args={"command": "git push origin x"})
    assert blocked and "no pushing" in msg
    assert EX.run_hooks(tmp_path, "PreToolUse", {}, tool="bash", args={"command": "ls"}) == (False, "")
    assert EX.run_hooks(tmp_path, "PostToolUse", {}, tool="edit", args={"path": "a"}) == (False, "checked")
    a = Agent(str(tmp_path), approve=lambda *x: ("yes", ""), mode="auto")
    ok, why = a._permit("bash", {"command": "git push origin main"})
    assert not ok and "hook" in why.lower()


def test_custom_commands_expand_with_arguments(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    d = tmp_path / ".meshpool" / "commands"
    d.mkdir(parents=True)
    (d / "review.md").write_text("---\ndescription: review\n---\nReview $1 for bugs. Focus: $ARGUMENTS", encoding="utf-8")
    out = EX.expand_command("/review app.py security", tmp_path)
    assert out == "Review app.py for bugs. Focus: app.py security"
    assert EX.expand_command("/nothing x", tmp_path) is None and EX.expand_command("hello", tmp_path) is None


def test_at_mentions_attach_files_inside_the_project_only(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('hi')", encoding="utf-8")
    out = EX.expand_mentions("Explain @src/a.py and @src and email me@example.com and @../../etc/passwd", tmp_path)
    assert "print('hi')" in out and "folder contents: a.py" in out and "root:" not in out and "example.com" in out


def test_hooks_can_decide_in_json_and_add_context(tmp_path, monkeypatch):
    _trusted(monkeypatch, tmp_path)
    (tmp_path / ".meshpool").mkdir()
    (tmp_path / ".meshpool" / "hooks.json").write_text(json.dumps({
        "PreToolUse": [{"matcher": "bash:ls*", "command": "echo '{\"decision\": \"allow\"}'"},
                       {"matcher": "bash:curl*", "command": "echo '{\"decision\": \"block\", \"reason\": \"no network in this project\"}'"}],
        "PostToolUse": [{"matcher": "*", "command": "echo '{\"additionalContext\": \"remember to run the linter\"}'"}],
        "PreCompact": [{"command": "echo saved > compact.flag"}]}), encoding="utf-8")
    assert EX.run_hooks_full(tmp_path, "PreToolUse", {}, tool="bash", args={"command": "ls -la"})["allow"] is True
    r = EX.run_hooks_full(tmp_path, "PreToolUse", {}, tool="bash", args={"command": "curl x"})
    assert r["blocked"] and "no network" in r["message"]
    assert EX.run_hooks(tmp_path, "PostToolUse", {}, tool="edit", args={}) == (False, "remember to run the linter")
    a = Agent(str(tmp_path), approve=lambda *x: ("no", ""), mode="ask")
    assert a._permit("bash", {"command": "ls -la"}) == (True, "")                  # the hook answered the question
    assert a._permit("bash", {"command": "rm -rf build"})[0] is False             # but never for a destructive command
    EX.run_hooks(tmp_path, "PreCompact", {})
    assert (tmp_path / "compact.flag").exists()


def test_a_subfolders_instructions_join_the_first_time_work_reaches_it(tmp_path, monkeypatch):
    import json as _json
    from meshpool_harness.agent import core as K
    _home(monkeypatch, tmp_path)
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    proj = tmp_path / "proj"
    (proj / "pkg" / "sub").mkdir(parents=True)
    (proj / "AGENTS.md").write_text("Top rules.")
    (proj / "pkg" / "AGENTS.md").write_text("Pkg uses tabs.")
    (proj / "pkg" / "sub" / "CLAUDE.md").write_text("Sub is legacy code.")
    (proj / "pkg" / "sub" / "x.py").write_text("x = 1\n")
    (proj / "y.py").write_text("y = 1\n")
    a = Agent(str(proj), mode="auto")
    a._turn_no = 1
    run = lambda name, args: a._run_tool({"id": "c", "type": "function", "function": {"name": name, "arguments": _json.dumps(args)}})
    assert "Top rules" in a.messages[0]["content"] and "Pkg uses tabs" not in a.messages[0]["content"]
    assert "Instructions from" not in run("read_file", {"path": "y.py"})            # the project's own: already loaded
    out = run("read_file", {"path": "pkg/sub/x.py"})
    assert "Pkg uses tabs" in out and "Sub is legacy" in out and out.index("Pkg uses tabs") < out.index("Sub is legacy")
    assert "Instructions from" not in run("read_file", {"path": "pkg/sub/x.py"})    # once only


def test_path_scoped_rules_apply_only_to_matching_files(tmp_path, monkeypatch):
    import json as _json
    import subprocess as _sp
    from meshpool_harness.agent import core as K
    _home(monkeypatch, tmp_path)
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    proj = tmp_path / "proj"
    (proj / ".claude" / "rules").mkdir(parents=True)
    (proj / ".cursor" / "rules").mkdir(parents=True)
    _sp.run(["git", "init", "-q", str(proj)], check=True)
    (proj / ".claude" / "rules" / "always.md").write_text("Always use British spelling.")
    (proj / ".claude" / "rules" / "api.md").write_text("---\npaths:\n  - \"src/api/**/*.ts\"\n---\nAPI handlers validate input.")
    (proj / ".cursor" / "rules" / "tests.mdc").write_text("---\nglobs: *.test.ts\nalwaysApply: false\n---\nTests use vitest.")
    (proj / "src" / "api" / "v1").mkdir(parents=True)
    (proj / "src" / "api" / "v1" / "users.ts").write_text("x")
    (proj / "src" / "a.test.ts").write_text("x")
    (proj / "README.md").write_text("x")
    a = Agent(str(proj), mode="auto")
    a._turn_no = 1
    run = lambda name, args: a._run_tool({"id": "c", "type": "function", "function": {"name": name, "arguments": _json.dumps(args)}})
    sysp = a.messages[0]["content"]
    assert "British spelling" in sysp and "validate input" not in sysp and "vitest" not in sysp
    assert "Rules from" not in run("read_file", {"path": "README.md"})
    out = run("read_file", {"path": "src/api/v1/users.ts"})
    assert "API handlers validate input" in out and "vitest" not in out
    assert "Tests use vitest" in run("read_file", {"path": "src/a.test.ts"})
    assert "Rules from" not in run("read_file", {"path": "src/api/v1/users.ts"})        # once
