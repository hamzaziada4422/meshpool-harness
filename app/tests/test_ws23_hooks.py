"""G-06: hooks of installed plugins are converted, run in the app's chats and in the terminal, and go with the plugin."""
import json
import sys
from pathlib import Path

import pytest

from meshpool_harness import chats, config as C, plugins as PL
from meshpool_harness.agent import extras as EX
from meshpool_harness.agent import tui as T
from ws23_kit import call, finish, home, new_chat, proj, script  # noqa: F401


@pytest.fixture(autouse=True)
def _full_cowork_lane(monkeypatch):
    """These tests cover the full Cowork lane (classic tools, ask_user, grader); lean Work is the default since work1
    and is tested in test_lean_work.py."""
    monkeypatch.setenv("MH_LEAN_WORK", "0")


def make_plugin(tmp_path, marker_name="ran.txt") -> Path:
    d = tmp_path / "plug"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / "hooks").mkdir()
    (d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "guard", "version": "1.0", "description": "x"}))
    (d / "skills" / "s").mkdir(parents=True)
    (d / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: a skill\n---\nbody\n")
    (d / "block.py").write_text("import sys\nsys.stderr.write('nope from plugin')\nsys.exit(2)\n")
    (d / "note.py").write_text("import json\nprint(json.dumps({'additionalContext': 'POSTNOTE'}))\n")
    py = f'"{sys.executable}"'
    (d / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "bash", "hooks": [{"type": "command", "timeout": 20,
                                                      "command": f'{py} "${{CLAUDE_PLUGIN_ROOT}}/block.py"'}]}],
        "PostToolUse": [{"matcher": "read_file", "hooks": [{"type": "command",
                                                           "command": f'{py} "${{CLAUDE_PLUGIN_ROOT}}/note.py"'}]}],
        "Notify": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    return d


def test_install_converts_hooks_and_remove_takes_them_away(tmp_path, proj):
    d = make_plugin(tmp_path)
    rec = PL.install_plugin(str(d))[0]
    notes = " ".join(rec["notes"])
    assert "Added 2 hook(s)" in notes and "PreToolUse(bash)" in notes and "Notify" in notes
    assert EX.hooks(proj)["PreToolUse"] == []                       # off until the plugin is switched on
    assert PL.list_plugins()[0]["enabled"] is False and PL.list_plugins()[0]["hooks_enabled"] is False
    PL.enable_plugin("guard", True)
    assert PL.list_plugins()[0]["enabled"] is True and PL.list_plugins()[0]["hooks_enabled"] is True
    h = EX.hooks(proj)
    pre = h["PreToolUse"][0]
    assert pre["matcher"] == "bash" and pre["timeout"] == 20
    assert "${CLAUDE_PLUGIN_ROOT}" not in pre["command"] and "block.py" in pre["command"]
    installed = PL._load()["plugins"]["guard"]
    assert Path(installed["hooks"]).is_file() and str(Path(installed["dir"])) in pre["command"]
    PL.enable_plugin("guard", False)                                # a disabled plugin's hooks do not run
    assert EX.hooks(proj)["PreToolUse"] == []
    PL.enable_plugin("guard", True)
    assert len(EX.hooks(proj)["PreToolUse"]) == 1
    PL.remove_plugin("guard")
    assert EX.hooks(proj)["PreToolUse"] == [] and not list((C.home() / "plugin-hooks").glob("guard*"))


def test_pretooluse_hook_blocks_in_app_chat(monkeypatch, tmp_path, proj):
    PL.install_plugin(str(make_plugin(tmp_path)))
    PL.enable_plugin("guard", True)                                 # hooks run once the plugin is switched on
    marker = proj / "marker.txt"
    script(monkeypatch, call("bash", {"command": f'"{sys.executable}" -c "open(r\'{marker}\',\'w\')"'}), "done")
    s = new_chat(proj, mode="auto")
    finish(s, "run it")
    assert not marker.exists()
    ends = [e for e in s.view(0)["events"] if e["type"] == "tool_end"]
    assert ends and not ends[0]["ok"]
    assert "Blocked by a hook" in json.dumps(s.agent.messages) and "nope from plugin" in json.dumps(s.agent.messages)


def test_pretooluse_hook_blocks_in_terminal(monkeypatch, tmp_path, proj, capfd):
    PL.install_plugin(str(make_plugin(tmp_path)))
    PL.enable_plugin("guard", True)                                 # hooks run once the plugin is switched on
    marker = proj / "marker.txt"
    script(monkeypatch, call("bash", {"command": f'"{sys.executable}" -c "open(r\'{marker}\',\'w\')"'}), "done")
    T.main(str(proj), prompt="run it", auto=True, no_mcp=True)
    capfd.readouterr()
    assert not marker.exists()
    sid = max(chats.listing(), key=lambda r: r.get("updated") or 0)["id"]
    assert "nope from plugin" in json.dumps(chats.saved_messages(sid))


def test_posttooluse_output_reaches_the_tool_result(monkeypatch, tmp_path, proj):
    PL.install_plugin(str(make_plugin(tmp_path)))
    PL.enable_plugin("guard", True)                                 # hooks run once the plugin is switched on
    (proj / "a.txt").write_text("hello")
    script(monkeypatch, call("read_file", {"path": "a.txt"}), "done")
    s = new_chat(proj, mode="auto")
    finish(s, "read it")
    tool_msgs = [m for m in s.agent.messages if m.get("role") == "tool"]
    assert tool_msgs and "POSTNOTE" in str(tool_msgs[0]["content"])
