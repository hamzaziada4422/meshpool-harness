# Saved sub-agents

meshpool's agent can hand parts of a job to sub-agents that work at the same time. A saved sub-agent is a named helper with its own instructions, which you write once and use by name ("use the reviewer agent on calc.py").

Put a Markdown file in one of these folders (a later one wins for the same name):

- your meshpool data folder, `agents/`: yours, for every project
- `<project>/.claude/agents/`: a project's Claude Code agents, read as they are
- `<project>/.meshpool/agents/`: the project's own

```markdown
---
name: reviewer
description: Reviews a diff for bugs and missing tests
tools: Read, Grep, Glob
model: provider/model-id
---
You are a strict reviewer. Report bugs and missing tests only, each with a file and line.
```

`tools` and `model` are optional. Tools can be meshpool's names (`read_file`, `grep`, `bash`) or Claude Code's (`Read`, `Grep`, `Bash`). A definition can only narrow what a sub-agent may do: its tools are picked from the chat's own, and every action is checked the same way as the chat's.

The chat sees the list of saved sub-agents in its instructions. In the terminal, `/agents` lists them. With `isolation: worktree`, a sub-agent that edits code works in its own git worktree (see [worktrees](worktrees.md)).
