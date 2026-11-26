"""meshpool doctor: problems in settings, hooks, rules, MCP server lists, skills and sub-agents are reported."""
import json

import pytest

from meshpool_harness import doctor as DR
from meshpool_harness import skills as SK


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "skills").mkdir(parents=True)
    monkeypatch.setenv("MESHPOOL_HOME", str(h))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(h / "meshpool.config"))
    monkeypatch.delenv("MESHPOOL_MCP", raising=False)
    monkeypatch.setattr(SK, "ensure_defaults", lambda: None)
    return h


def _problems(root):
    return [(r["level"], r["problem"]) for r in DR.check(root)]


def test_a_clean_setup_has_no_problems(home, tmp_path):
    text, code = DR.report(tmp_path)
    assert code == 0 and "No problems" in text


def test_broken_files_are_reported(home, tmp_path):
    (home / "meshpool.config").write_text("MH_MODEL=x\nnot a setting line\nFOO=1\n")
    (home / "hooks.json").write_text(json.dumps({"PreToolUse": [{"matcher": "bash"}],
                                                 "BeforeEverything": [{"command": "x"}],
                                                 "Stop": [{"command": "x", "args": "y", "timeout": "soon"}]}))
    (home / "permissions.json").write_text(json.dumps({"allow": "edit", "deny": ["bash:"], "rules": []}))
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"a": {}, "b": {"command": "x", "args": "y"}}}))
    d = home / "skills" / "nodesc"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: nodesc\n---\nDo it.")
    (home / "skills" / "empty").mkdir()
    p = _problems(tmp_path)
    flat = " | ".join(x for _, x in p)
    for needle in ("line 2 is not KEY=VALUE", "FOO is not a meshpool setting", "has no command",
                   "BeforeEverything is not a hook event", "args must be a list", "timeout must be a number",
                   "allow must be a list", "has no command pattern", "rules is not read",
                   "server a needs a command or a url", "server b: args must be a list", "has no description",
                   "has no SKILL.md"):
        assert needle in flat, needle
    text, code = DR.report(tmp_path)
    assert code == 1 and text.startswith("error:")


def test_bad_json_and_project_files(home, tmp_path):
    proj = tmp_path / "proj"
    (proj / ".meshpool").mkdir(parents=True)
    (proj / ".meshpool" / "hooks.json").write_text("{not json")
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"url": "https://example.com/mcp"}}}))
    p = _problems(proj)
    assert any("is not valid JSON" in x for _, x in p)
    assert any(lvl == "info" and "trust it" in x for lvl, x in p)


def test_skills_with_the_same_name_and_invalid_yaml(home, tmp_path):
    for sid in ("one", "two"):
        d = home / "skills" / sid
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: same\ndescription: Does a: thing\n---\nBody.")
    flat = " | ".join(x for _, x in _problems(tmp_path))
    assert "same name as" in flat and "not valid YAML" in flat


def test_shipped_skills_are_valid_yaml():
    import pathlib
    import yaml
    base = pathlib.Path(__file__).resolve().parents[1] / "defaults" / "skills"
    for f in base.glob("*/SKILL.md"):
        t = f.read_text(encoding="utf-8")
        assert isinstance(yaml.safe_load(t[3:t.find("\n---", 3)]), dict), f


def test_cli_doctor(home, tmp_path, capsys):
    from meshpool_harness import cli
    assert cli.main(["doctor", str(tmp_path)]) == 0
    assert "No problems" in capsys.readouterr().out
