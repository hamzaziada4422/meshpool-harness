# The terminal and scripts

`meshpool` in a folder starts a session in the terminal. `meshpool -p "request"` answers one request and exits, for scripts and CI. `meshpool -h` lists every option.

## Running one request

- `--output-format json` prints one JSON object at the end: the reply, the session id, usage and steps. `stream-json` also prints each event as it happens.
- `--permission-mode` is `ask`, `edits`, `auto` or `plan`. `--allowed-tools` and `--disallowed-tools` take tool names or `bash:` patterns.
- `--max-turns` stops after that many tool steps.
- `--mcp-config` adds MCP servers for this run only.
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

## Shell completion

```bash
eval "$(meshpool completion bash)"
```

For zsh, `meshpool completion zsh > "${fpath[1]}/_meshpool"`. For fish, `meshpool completion fish > ~/.config/fish/completions/meshpool.fish`.
