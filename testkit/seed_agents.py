"""Fake agent folders (with fake logins that must never be read) under a Meshpool dev home's HOME, for the import
journey. Usage: seed_agents.py HOME_DIR   (HOME_DIR is the folder given to dev_app.sh; HOME is HOME_DIR/user)"""
import json
import sys
from pathlib import Path

h = Path(sys.argv[1]) / "user"
SECRET = "sk-live-NEVER-READ-1234567890"


def w(rel, text):
    p = h / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


w(".claude/.credentials.json", json.dumps({"claudeAiOauth": {"accessToken": SECRET}}))
w(".claude/skills/board-memo/SKILL.md", "---\nname: board-memo\ndescription: Write the monthly board memo\n---\nSteps.")
w(".claude/CLAUDE.md", "Always answer in British English.\n")
w(".claude/memory/notes.md", "- Works in strategy for GCC clients\n- Prefers Segoe UI\n")
w(".claude.json", json.dumps({"mcpServers": {"jira": {"command": "npx", "args": ["jira-mcp", f"--token={SECRET}"], "env": {"JIRA_API_TOKEN": SECRET, "JIRA_URL": "https://acme.atlassian.net"}}}}))
w(".codex/auth.json", json.dumps({"tokens": {"access_token": SECRET}}))
w(".codex/AGENTS.md", "Use tabs in code.\n")
w(".codex/config.toml", '[mcp_servers.docs]\ncommand = "docs-mcp"\n')
w(".cursor/mcp.json", json.dumps({"mcpServers": {"figma": {"url": "https://figma.example/mcp"}}}))
w(".cursor/rules/style.mdc", "Prefer small functions.\n")
w(".openclaw/openclaw.json", json.dumps({"token": SECRET}))
w(".openclaw/workspace/MEMORY.md", "- Lives in Dubai\n- Manager is Layla Haddad\n")
w(".hermes/.env", f"KEY={SECRET}\n")
w(".hermes/memories/MEMORY.md", "Likes short answers in chat\n§\nWorks Sunday to Thursday\n")
print("seeded agents")
