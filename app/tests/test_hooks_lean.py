"""Hooks keep working when a chat runs the lean (Codex-style) tools: "shell" and "apply_patch" match the hooks written
for bash / edit / write, and Claude Code's capitalised names (Bash, Edit, Write, Read) match Meshpool's tools."""
import json
import sys

import pytest

from meshpool_harness import plugins as PL
from meshpool_harness.agent import extras as EX
from test_ws23_hooks import make_plugin
from ws23_kit import call, finish, home, new_chat, proj, script  # noqa: F401


@pytest.mark.parametrize("matcher,tool,args,hit", [
    ("Bash", "shell", {"command": ["bash", "-lc", "rm -rf x"]}, True),
    ("bash", "shell", {"command": ["bash", "-lc", "ls"]}, True),
    ("bash:rm *", "shell", {"command": ["bash", "-lc", "rm -rf x"]}, True),
    ("bash:rm *", "shell", {"command": ["bash", "-lc", "ls"]}, False),
    ("Bash(rm *)", "bash", {"command": "rm a"}, True),
    ("Edit|Write", "apply_patch", {}, True),
    ("edit", "apply_patch", {}, True),
    ("Write", "write_file", {}, True),
    ("Read", "read_file", {}, True),
    ("read_file", "apply_patch", {}, False),
    ("bash", "read_file", {}, False),
    ("mcp__*", "mcp__x__y", {}, True),
])
def test_matchers_cover_lean_and_claude_code_names(matcher, tool, args, hit):
    assert EX._tool_matches(matcher, tool, args) is hit


def test_pretooluse_hook_blocks_shell_in_a_lean_work_chat(monkeypatch, tmp_path, proj):
    monkeypatch.setenv("MH_LEAN_WORK", "1")
    PL.install_plugin(str(make_plugin(tmp_path)))
    PL.enable_plugin("guard", True)                                 # hooks run once the plugin is switched on
    marker = proj / "marker.txt"
    script(monkeypatch, call("shell", {"command": ["bash", "-lc", f'"{sys.executable}" -c "open(r\'{marker}\',\'w\')"']}),
           "done")
    s = new_chat(proj, mode="auto")
    finish(s, "run it")
    assert s.agent._codex()                                   # the lean tools really ran
    assert not marker.exists()
    assert "nope from plugin" in json.dumps(s.agent.messages)
