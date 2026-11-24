"""Extensibility from the feature ledger: more instruction files (local, GEMINI.md, rules folders, other agents'
files), hook events that now fire, the switch that turns hooks off, and a trusted project's own .mcp.json."""
import json
import subprocess

from meshpool_harness import trust as TR
from meshpool_harness.agent import extras as EX
from meshpool_harness.agent import mcp as MC


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_HOOKS", raising=False)
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    (tmp_path / "home").mkdir(exist_ok=True)


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def test_local_files_rules_and_other_agents(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    r = _repo(tmp_path)
    (r / ".cursorrules").write_text("Cursor says use tabs.", encoding="utf-8")
    (r / "CLAUDE.local.md").write_text("My own note.", encoding="utf-8")
    (r / ".claude" / "rules").mkdir(parents=True)
    (r / ".claude" / "rules" / "api.md").write_text("Rule about the API.", encoding="utf-8")
    t = EX.instruction_text(r)
    assert "Cursor says use tabs" in t and "My own note" in t and "Rule about the API" in t
    (r / "AGENTS.md").write_text("Agents file wins over Cursor's.", encoding="utf-8")
    t = EX.instruction_text(r)
    assert "Agents file wins" in t and "Cursor says" not in t and "My own note" in t
    (r / "AGENTS.md").unlink()
    (r / "GEMINI.md").write_text("Gemini file.", encoding="utf-8")
    assert "Gemini file." in EX.instruction_text(r)


def test_new_hook_events_fire_and_the_switch_turns_hooks_off(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    log = tmp_path / "events.txt"
    (tmp_path / "home" / "hooks.json").write_text(json.dumps({e: [{"command": f"echo {e} >> {log}"}] for e in (
        "PostToolUseFailure", "PostCompact", "SubagentStart", "SubagentStop", "Notification", "SessionEnd")}),
        encoding="utf-8")
    for e in ("PostToolUseFailure", "PostCompact", "SubagentStart", "SubagentStop", "Notification", "SessionEnd"):
        EX.run_hooks(tmp_path, e, {})
    assert log.read_text().split() == ["PostToolUseFailure", "PostCompact", "SubagentStart", "SubagentStop",
                                       "Notification", "SessionEnd"]
    monkeypatch.setenv("MH_HOOKS", "0")
    assert all(not v for v in EX.hooks(tmp_path).values())


def test_a_waiting_approval_runs_the_notification_hook(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    from meshpool_harness.agent import core
    log = tmp_path / "n.txt"
    (tmp_path / "home" / "hooks.json").write_text(json.dumps({"Notification": [{"command": f"cat > {log}"}]}),
                                                  encoding="utf-8")
    a = core.Agent(str(tmp_path), approve=lambda *x: ("no", ""), mode="ask")
    a._permit("bash", {"command": "make build"})
    assert json.loads(log.read_text())["event"] == "Notification"


def test_project_mcp_servers_need_trust(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    r = _repo(tmp_path)
    (r / ".mcp.json").write_text(json.dumps({"mcpServers": {"proj-tool": {"command": "echo", "args": ["x"]}}}),
                                 encoding="utf-8")
    assert ".mcp.json" in TR.needs_prompt(r)
    assert MC.project_servers(r) == {}
    TR.trust(r)
    assert MC.project_servers(r) == {"proj-tool": {"command": "echo", "args": ["x"]}}
