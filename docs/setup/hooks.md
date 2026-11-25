# Hooks

A hook is a command meshpool runs at a set moment, such as before a tool call or when a turn ends. Your own hooks live in `hooks.json` in the meshpool data folder. A project's hooks live in `.meshpool/hooks.json` and run only once you trust the folder (`meshpool trust`). Settings, Run hooks turns every hook off.

```json
{
  "PreToolUse": [{"matcher": "bash:git push*", "command": "echo 'no pushing from here' 1>&2; exit 2"}],
  "PostToolUse": [{"matcher": "edit|write_file", "command": "npm run lint --silent"}],
  "SessionStart": [{"matcher": "startup|compact", "command": "cat docs/today.md"}]
}
```

Each hook gets the event as JSON on its standard input: `event`, `tool`, `args`, `cwd` and the fields listed below.

## Events

| Event | When it runs | Extra fields |
| --- | --- | --- |
| `PreToolUse` | Before a tool call. Can block it, allow it, or change its arguments. | |
| `PostToolUse` | After a tool call that worked. | |
| `PostToolUseFailure` | After a tool call that failed. | |
| `PermissionDenied` | After you, a rule or a hook refused a tool call. Its output is passed to the agent. | `reason` |
| `UserPromptSubmit` | When you send a message. Can block it. | `prompt` |
| `SessionStart` | At the first message of a chat, and again after the chat is summarised. Its output is added to the chat. | `source` (`startup` or `compact`) |
| `SessionEnd` | When you leave the terminal UI. | `session` |
| `Stop` | When a turn ends. | `reply` |
| `StopFailure` | When a turn ends on an error. | `error` |
| `PreCompact`, `PostCompact` | Before and after a chat is summarised. | `messages`, `removed` |
| `SubagentStart`, `SubagentStop` | When a sub-agent starts and ends. | |
| `Notification` | When meshpool shows a desktop notification. | `message`, `kind` |
| `InstructionsLoaded` | When a subfolder's instructions or scoped rules load. Its output is passed to the agent. | `path`, `instructions` |
| `DirectoryAdded` | After `/add-dir` adds a folder. | `path` |
| `CwdChanged` | After a chat moves to a worktree or back. | `from`, `to` |
| `WorktreeCreate`, `WorktreeRemove` | After meshpool makes or removes a worktree. | |

## Options

- `matcher`: for the tool events, a tool name or pattern (`edit`, `mcp__*`), several joined with `|`, or `bash:` and a command pattern. For `SessionStart`, `startup` or `compact`.
- `timeout`: seconds before the hook is stopped. The default is 30.
- `args`: a list of arguments. With it, `command` is the program and runs directly, with no shell, so nothing in the arguments is read as shell syntax.
- `shell`: `bash` or `powershell`, to pick the shell that runs `command`.
- `windows`: a command to use instead of `command` on Windows.
- `async`: `true` runs the hook in the background. meshpool does not wait for it or read its output.
- `once`: `true` runs the hook once a chat.

## What a hook can answer

- Exit code 2 blocks a `PreToolUse` call or a `UserPromptSubmit` message. Standard error is the reason.
- JSON on standard output:
  - `{"decision": "block", "reason": "..."}` blocks, like exit code 2.
  - `{"decision": "allow"}` skips the question for this call. It never skips it for a destructive command.
  - `{"additionalContext": "..."}` adds a note the agent sees.
  - `{"updatedInput": {...}}` (`PreToolUse` only) changes the call's arguments. Your saved rules then judge the changed call, so a hook cannot use this to get around a deny rule.
- Any other text on standard output is passed to the agent as a note.
