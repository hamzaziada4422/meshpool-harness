"""Import from other agents (Claude Code, Codex, Cursor, OpenClaw, Hermes): previewed first, dry run writes nothing,
secrets stay behind, and credential files are never opened."""
import builtins
import json
from pathlib import Path

import pytest

from meshpool_harness import agent_import as AI
from meshpool_harness import assistant as A
from meshpool_harness import importer as IM
from meshpool_harness import memstore as MS

SECRET = "sk-live-THIS-MUST-NEVER-BE-READ-1234567890"


@pytest.fixture
def world(tmp_path, monkeypatch):
    h = tmp_path / "user"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mp"))
    (tmp_path / "mp").mkdir()
    MS._ENSURED.clear()
    MS._CACHE.clear()

    def w(rel, text):
        p = h / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    creds = [
        w(".claude/.credentials.json", json.dumps({"claudeAiOauth": {"accessToken": SECRET}})),
        w(".codex/auth.json", json.dumps({"tokens": {"access_token": SECRET}})),
        w(".hermes/.env", f"OPENAI_API_KEY={SECRET}\n"),
        w(".openclaw/openclaw.json", json.dumps({"channels": {"token": SECRET}})),
    ]
    w(".claude/skills/board-memo/SKILL.md", "---\nname: board-memo\ndescription: Write the monthly board memo\n---\nSteps.")
    w(".claude/skills/board-memo/.env", f"KEY={SECRET}\n")                           # a login file inside a skill
    w(".claude/skills/board-memo/helper.py", "print('hi')\n")
    w(".claude/CLAUDE.md", "Always answer in British English.\nKeep slides to one message each.\n")
    w(".claude/memory/notes.md", "# Notes\n- Works in strategy for GCC clients\n- Prefers Segoe UI\n")
    w(".claude.json", json.dumps({"mcpServers": {
        "jira": {"command": "npx", "args": ["-y", "jira-mcp", f"--token={SECRET}"],
                 "env": {"JIRA_URL": "https://acme.atlassian.net", "JIRA_API_TOKEN": SECRET}},
        "notion": {"url": f"https://mcp.notion.com/mcp?api_key={SECRET}&workspace=acme",
                   "headers": {"Authorization": f"Bearer {SECRET}", "X-Team": "acme"}}}}))
    w(".codex/AGENTS.md", "Use tabs in code.\n")
    w(".codex/config.toml", '[mcp_servers.docs]\ncommand = "docs-mcp"\nargs = ["--root", "/work"]\n')
    w(".cursor/mcp.json", json.dumps({"mcpServers": {"figma": {"url": "https://figma.example/mcp"}}}))
    w(".cursor/rules/style.mdc", "---\nalwaysApply: true\n---\nPrefer small functions.\n")
    w(".openclaw/workspace/MEMORY.md", "# Memory\n- Lives in Dubai\n- Manager is Layla Haddad\n")
    w(".hermes/memories/MEMORY.md", "Likes short answers in chat\n§\nWorks Sunday to Thursday\n")
    w(".hermes/config.yaml", "mcp_servers:\n  linear:\n    command: linear-mcp\n    env:\n      LINEAR_API_KEY: abc\n      TEAM: core\n")
    return {"home": h, "creds": creds}


def _opened_paths(monkeypatch):
    seen = []
    real_open, real_rt, real_rb = builtins.open, Path.read_text, Path.read_bytes

    def o(f, *a, **k):
        seen.append(str(f))
        return real_open(f, *a, **k)

    def rt(self, *a, **k):
        seen.append(str(self))
        return real_rt(self, *a, **k)

    def rb(self, *a, **k):
        seen.append(str(self))
        return real_rb(self, *a, **k)
    monkeypatch.setattr(builtins, "open", o)
    monkeypatch.setattr(Path, "read_text", rt)
    monkeypatch.setattr(Path, "read_bytes", rb)
    return seen


def test_every_agent_is_found_with_a_preview(world):
    r = AI.scan()
    by = {a["id"]: a for a in r["agents"]}
    assert set(by) == {"claude", "codex", "cursor", "openclaw", "hermes"}
    c = by["claude"]["counts"]
    assert c == {"skill": 1, "mcp": 2, "memory": 2, "instructions": 1}
    assert by["codex"]["counts"]["mcp"] == 1 and by["cursor"]["counts"]["instructions"] == 1
    assert by["openclaw"]["counts"]["memory"] == 2 and by["hermes"]["counts"]["memory"] == 2
    assert by["hermes"]["counts"]["mcp"] == 1
    skill = next(i for i in by["claude"]["items"] if i["kind"] == "skill")
    assert skill["preview"] == "Write the monthly board memo"


def test_credential_files_are_never_opened(world, monkeypatch):
    seen = _opened_paths(monkeypatch)
    r = AI.scan()
    AI.apply([i["key"] for a in r["agents"] for i in a["items"]])
    cred_names = {p.name for p in world["creds"]} | {".env"}
    touched = [p for p in seen if Path(p).name in cred_names or Path(p).name == "openclaw.json"]
    assert touched == [], touched
    reported = {Path(c).name for a in r["agents"] for c in a["credentials_not_read"]}
    assert {".credentials.json", "auth.json", ".env", "openclaw.json"} <= reported       # said so, by name only


def test_a_credential_file_is_refused_by_the_reader_itself(world):
    assert AI._read(world["creds"][0]) == ""
    assert str(world["creds"][0]) in AI._REFUSED


def test_secrets_stay_behind_and_are_listed(world):
    r = AI.scan()
    mcp = {i["name"]: i for a in r["agents"] for i in a["items"] if i["kind"] == "mcp"}
    jira = mcp["jira"]
    assert SECRET not in json.dumps(jira["spec"]) and "JIRA_API_TOKEN" in jira["stripped"] and "--token" in jira["stripped"]
    assert jira["spec"]["env"] == {"JIRA_URL": "https://acme.atlassian.net"}
    notion = mcp["notion"]
    assert "Authorization" in notion["stripped"] and "api_key" in notion["stripped"]
    assert notion["spec"]["url"] == "https://mcp.notion.com/mcp?workspace=acme" and notion["spec"]["headers"] == {"X-Team": "acme"}
    assert mcp["linear"]["spec"]["env"] == {"TEAM": "core"} and mcp["linear"]["stripped"] == ["LINEAR_API_KEY"]


def test_strip_secrets_cases():
    s, gone = AI.strip_secrets({"command": "x", "args": ["--api-key", "abc", "--root", "/w", "sk-abcdefghijk1234"],
                                "env": {"MY_KEY": "v", "PATH_HINT": "ok"}})
    assert s["args"] == ["--root", "/w"] and s["env"] == {"PATH_HINT": "ok"}
    assert "MY_KEY" in gone and "--api-key" in gone
    s2, g2 = AI.strip_secrets({"url": "https://user:pw@host.example/mcp"})
    assert s2["url"] == "https://host.example/mcp" and "(login in the address)" in g2


def test_dry_run_writes_nothing(world):
    from meshpool_harness import skills as SK
    keys = [i["key"] for a in AI.scan()["agents"] for i in a["items"]]
    r = AI.apply(keys, dry_run=True)
    assert r["dry_run"] and r["skills"] == ["board-memo"] and r["memory"] == 6 and len(r["mcp"]) == 5
    assert MS.load() == [] and A.instructions() == ""
    assert not (SK.folder() / "board-memo").exists()
    from meshpool_harness.agent import mcp as M
    assert "jira" not in M.load_config()


def test_apply_writes_each_kind_the_safe_way(world):
    from meshpool_harness import skills as SK
    from meshpool_harness.agent import mcp as M
    keys = [i["key"] for a in AI.scan()["agents"] for i in a["items"]]
    r = AI.apply(keys)
    assert r["errors"] == [], r["errors"]
    assert (SK.folder() / "board-memo" / "SKILL.md").is_file()
    assert not (SK.folder() / "board-memo" / ".env").exists()                      # no login file came along
    assert (SK.folder() / "board-memo" / "helper.py").is_file()
    cfg = M.load_config()
    assert SECRET not in json.dumps(cfg) and "jira" in cfg
    facts = MS.load()
    assert len(facts) == 6 and all(f["status"] == "pending" and f["source"]["type"] == "import" for f in facts)
    assert "Segoe UI" not in A.memory()                                            # waits in the tray, not in context
    assert "British English" in A.instructions() and "From Claude Code" in A.instructions()
    assert {s["server"] for s in r["stripped"]} == {"jira", "notion", "linear"}
    again = AI.apply([i["key"] for a in AI.scan()["agents"] for i in a["items"]])
    assert again["memory"] == 0 and again["skills"] == [] and again["mcp"] == []   # nothing twice


def test_hermes_and_openclaw_memories_split_into_facts(world):
    facts = {i["text"] for a in AI.scan(["hermes", "openclaw"])["agents"] for i in a["items"] if i["kind"] == "memory"}
    assert facts == {"Likes short answers in chat", "Works Sunday to Thursday", "Lives in Dubai", "Manager is Layla Haddad"}


def test_the_old_importer_also_leaves_secrets_behind(world, monkeypatch):
    monkeypatch.setattr(IM, "HOME", world["home"])
    for m in IM.find_mcp():
        assert SECRET not in json.dumps(m["spec"])
    jira = next(m for m in IM.find_mcp() if m["name"] == "jira")
    assert jira["has_secrets"] and "JIRA_API_TOKEN" in jira["stripped"]


def test_the_command_line_dry_run_and_yes(world, capsys):
    assert AI.cli(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "Nothing was written" in out and "not read: .credentials.json" in out
    assert SECRET not in out and MS.load() == []
    assert AI.cli(["--from", "hermes", "--yes"]) == 0
    assert len(MS.load()) == 2


def test_no_agents_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mp"))
    assert AI.cli([]) == 0 and "No other agents" in capsys.readouterr().out


def test_claude_code_hooks_are_offered_but_imported_only_when_chosen(world, tmp_path, capsys):
    from meshpool_harness import agent_import as AI
    h = tmp_path / "user"
    (h / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "npm run lint", "timeout": 20}]}],
        "Stop": [{"hooks": [{"type": "prompt", "prompt": "check"}, {"type": "command", "command": "say done"}]}],
        "MadeUpEvent": [{"hooks": [{"type": "command", "command": "x"}]}]}}), encoding="utf-8")
    found = AI.scan(["claude"])
    hooks = [i for a in found["agents"] for i in a["items"] if i["kind"] == "hook"]
    assert {i["event"] for i in hooks} == {"PreToolUse", "Stop"} and len(hooks) == 2
    pre = next(i for i in hooks if i["event"] == "PreToolUse")
    assert pre["hook"] == {"command": "npm run lint", "matcher": "edit|write_file", "timeout": 20}
    # the command line leaves hooks out unless --hooks is given
    assert AI.cli(["--from", "claude", "--yes"]) == 0
    assert not (tmp_path / "mp" / "hooks.json").exists()
    assert AI.cli(["--from", "claude", "--yes", "--hooks"]) == 0
    d = json.loads((tmp_path / "mp" / "hooks.json").read_text())
    assert d["PreToolUse"][0]["command"] == "npm run lint" and d["Stop"][0]["command"] == "say done"
    again = AI.apply([pre["key"]], only=["claude"])                  # already there: skipped, not doubled
    assert not again["hooks"] and len(json.loads((tmp_path / "mp" / "hooks.json").read_text())["PreToolUse"]) == 1


def test_matcher_translation():
    from meshpool_harness import agent_import as AI
    assert AI._matcher("Bash") == "bash" and AI._matcher(".*") == "*" and AI._matcher("mcp__github__.*") == "mcp__github__*"
    assert AI._matcher("Edit|MultiEdit|Write") == "edit|write_file"
