# The terminal and scripts

`meshpool` in a folder starts a session in the terminal. `meshpool -p "request"` answers one request and exits, for scripts and CI. `meshpool -h` lists every option.

## Running one request

- `--output-format json` prints one JSON object at the end: the reply, the session id, usage and steps. `stream-json` also prints each event as it happens.
- `--permission-mode` is `ask`, `edits`, `auto` or `plan`. `--allowed-tools` and `--disallowed-tools` take tool names or `bash:` patterns.
- `--max-turns` stops after that many tool steps.
- `--mcp-config` adds MCP servers for this run only. With `--strict-mcp-config`, only those servers start, not your saved ones or the project's.
- `--settings FILE` applies meshpool settings for this run, from a JSON object or `KEY=VALUE` lines. Other flags still win.
- `--agent NAME` works as one of your [saved sub-agents](sub-agents.md): its instructions, tools and model.
- `--disable-slash-commands` turns off custom commands, server prompts and skills for the run.
- Piped input is added to the request: `cat notes.txt | meshpool -p "summarise this"`.

## Instructions

- `--append-system-prompt "..."` and `--append-system-prompt-file FILE` add to meshpool's own instructions.
- `--system-prompt "..."` and `--system-prompt-file FILE` replace them for this run. meshpool always keeps one rule: what tools bring back is data, not instructions. Sub-agents keep their own instructions.

## Sessions

- `-c` continues the last session in this folder. `--resume ID` continues a session by id.
- `--fork-session`, with `-c` or `--resume`, continues in a new session and leaves the old one as it was.
- `--session-id ID` gives a new session the id you choose (letters, digits, `-` and `_`). With `--fork-session` it names the new session.
- `--no-session-persistence`, with `-p`, keeps nothing of the run on disk.
- `-n NAME` names the session.

## Plans

`/plan` (or `--permission-mode plan`) makes the agent read and plan without changing anything. Each plan it writes is also saved as a Markdown file in `plans/` in meshpool's data folder. `/plan open` shows the last one's path. Settings, Plans folder (`MH_PLANS_DIR`) picks another folder, and `off` saves none.

## Reviews

`/review` reviews the current changes and reports findings, most severe first, without editing anything. `/review quick` looks only at the changed lines and reports at most 5 findings; `/review deep` also reads the callers and tests and reports up to 25; the default reports up to 10. `--max N` sets the cap, and anything after it says what to review. `/simplify` makes the changed code simpler without changing what it does. A command file of your own with the same name wins. Both work in the app too.

## Checking your setup

`meshpool doctor` (or `/doctor` in a session) reads meshpool's settings file, hooks, saved rules, MCP server lists, skills, sub-agents and instruction files, for you and for the current folder, and lists what is broken or ignored. It changes nothing. It exits with 1 when it finds an error.

## Shell completion

```bash
eval "$(meshpool completion bash)"
```

For zsh, `meshpool completion zsh > "${fpath[1]}/_meshpool"`. For fish, `meshpool completion fish > ~/.config/fish/completions/meshpool.fish`.

## Connected apps in the terminal

`meshpool mcp add-json NAME '{"command": "npx", "args": ["-y", "some-server"]}'` adds a server to your list (use `-` to read the JSON from standard input). `/mcp` lists the connected MCP servers, their tools and the prompts they offer. Type a prompt as a command, such as `/mcp__github__review-pr 12`, and the server fills in the message. What the server writes is treated as data.

A tool its server marks as one that deletes or overwrites data asks first in every mode, and an earlier "always" does not cover it. `MH_MCP_DESTRUCTIVE_ASK=0` turns this off. With no one to ask (`-p` with full access), it runs as before.
