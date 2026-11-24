"""Saved sub-agents: named helpers defined in Markdown files, which delegate_task can start by name.

Where they live (yours win for the same name):
  <project>/.claude/agents/*.md    a project's Claude Code agents, read as they are, once you trust the folder
  <project>/.meshpool/agents/*.md  the project's own, once you trust the folder
  <meshpool home>/agents/*.md      yours, for every project

Each file:
  ---
  name: reviewer
  description: Reviews a diff for bugs and missing tests
  tools: read_file, grep, glob        (optional; Claude Code names such as Read, Grep, Bash also work)
  model: provider/model-id             (optional)
  ---
  The instructions the sub-agent follows.

A definition can only narrow what a sub-agent may do: its tools are picked from the chat's own, and every action is
checked like the chat's.
"""
from __future__ import annotations

import re
from pathlib import Path

from meshpool_harness import config as C

# Claude Code tool names, read as Meshpool's
ALIASES = {"read": ["read_file"], "grep": ["grep"], "glob": ["glob"], "ls": ["list_dir", "glob"],
           "bash": ["bash", "bash_many", "bash_start", "shell"], "edit": ["edit", "apply_patch", "notebook_edit"],
           "multiedit": ["edit", "apply_patch"], "write": ["write_file"], "notebookedit": ["notebook_edit"],
           "webfetch": ["fetch_url"], "websearch": ["web_search"], "todowrite": ["todo_write"],
           "task": ["delegate_task"]}
_NAME = re.compile(r"[^a-z0-9_-]+")


def _slug(s: str) -> str:
    return _NAME.sub("-", str(s or "").strip().lower()).strip("-")[:60]


def _list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[,\s]+", str(v or "")) if x.strip()]


def folders(root) -> list[Path]:
    """The project's folders first, the user's last, so the user's own definition wins for a name. A project's
    folders count only once the user trusts it (trust.py): its definitions steer what sub-agents do."""
    from meshpool_harness import trust as TR
    project = [Path(root) / ".claude" / "agents", Path(root) / ".meshpool" / "agents"] if TR.is_trusted(root) else []
    return project + [C.home() / "agents"]


_NO_MODEL = {"inherit", "default", "sonnet", "opus", "haiku", "fast", "best", "auto"}   # Claude Code words: the chat's model


def definitions(root) -> dict[str, dict]:
    """name -> {name, description, tools (None for all), model, prompt, path}."""
    from meshpool_harness import skills as SK
    out: dict[str, dict] = {}
    for d in folders(root):
        try:
            files = sorted(d.glob("*.md"))
        except OSError:
            continue
        for f in files:
            if f.is_symlink() or not f.is_file():         # never follow a link out of the folder
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")[:60000]
            except OSError:
                continue
            meta, body = SK.parse(text)
            name = _slug(meta.get("name") or f.stem)
            if not name or not body.strip():
                continue
            tools = _list(meta.get("tools")) if meta.get("tools") else None
            out[name] = {"name": name, "description": str(meta.get("description") or "").strip()[:300],
                         "tools": tools, "model": (str(meta.get("model") or "").strip()
                                                   if str(meta.get("model") or "").strip().lower() not in _NO_MODEL
                                                   else "") or None,
                         "own": f.parent.resolve() == (C.home() / "agents").resolve(),
                         "prompt": body.strip(), "path": str(f)}
    return out


def tool_names(defn: dict, available: list[str]) -> list[str]:
    """The tools of a definition, from the chat's own: all of them when it names none."""
    if not defn.get("tools"):
        return list(available)
    want: set[str] = set()
    for t in defn["tools"]:
        k = t.strip()
        want.update(ALIASES.get(k.lower().replace("_", "").replace(" ", ""), [k]))
        want.add(k)
    return [n for n in available if n in want or any(n.startswith(w.rstrip("*")) for w in want if w.endswith("*"))]


def catalog_text(root) -> str:
    """For the chat's own instructions: the saved sub-agents it can name in delegate_task."""
    defs = definitions(root)
    if not defs:
        return ""
    rows = "\n".join(f"- {d['name']}: {d['description'] or '(no description)'}" for d in list(defs.values())[:30])
    return ("\n\n# Saved sub-agents\n\nThe user saved these helpers. To use one, give its name as `agent` in a "
            "delegate_task task:\n" + rows)
