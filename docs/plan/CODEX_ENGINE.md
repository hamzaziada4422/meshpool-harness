# The "codex" engine style

MeshPool's own agent can run in Codex's design: Codex's base instructions, Codex's tool set, Codex's loop. MeshPool Auto's routing (sub-harness method text) stays on top. It is a harness-profile switch, so it can be A/B tested with `profile:codex` bench arms. Motivation: `docs/results/v3/EVAL52_FINAL.md` and `docs/plan/WINNER_CONCEPTS.md` (Codex 50/52 at about 8 calls on DeepSeek V4.1 Flash; MeshPool 46-47/52 at 13-16 calls).

Nothing is assigned yet: `app/defaults/profiles.json` defines the profile `codex`, but `default` and `assign` are unchanged. The owner decides after measurement.

## Upstream sources

Repository `github.com/openai/codex`, Apache-2.0, **commit `097558c1bdfc6318cb0ce80a64816ca4d61c7814`** (main on 2026-11-19).

| Upstream file | Used for |
|---|---|
| `codex-rs/protocol/src/prompts/base_instructions/default.md` (identical to `codex-rs/models-manager/prompt.md`) | the base instructions: `app/defaults/engines/codex/prompt.md` |
| `codex-rs/core/gpt_5_2_prompt.md`, section `## apply_patch` | the patch-format description (the default prompt has none) |
| `codex-rs/core/assets/tools/apply_patch.lark` | the grammar, kept as `apply_patch.lark` (reference; the chat API has no grammar tools) |
| `codex-rs/core/src/tools/handlers/plan_spec.rs` | the `update_plan` tool definition |
| `codex-rs/apply-patch/src/{parser,streaming_parser,seek_sequence,file_update,text_file,invocation}.rs` | re-implemented in Python: `core/applypatch.py`, `Workspace.apply_patch/shell` in `core/tools.py` |
| `LICENSE`, `NOTICE` | copied next to the prompt, `app/defaults/engines/codex/` |

Attribution: a NOTICE comment at the top of `prompt.md` (removed when the prompt is loaded, so the model never sees it), the licence and NOTICE files beside it, a docstring in `applypatch.py`, and a row in `THIRD_PARTY_NOTICES.md`.

## What was adapted

- The identity lines name MeshPool and say it is built on the open-source Codex CLI design; the sentence about the old Codex model was dropped.
- The `apply_patch` example call uses `{"input": "..."}` (MeshPool's function argument) instead of Codex's argv form. The shell tool still accepts `["apply_patch", "<patch>"]` and `bash -lc "[cd dir &&] apply_patch <<'EOF' ... EOF"`, as Codex does, so a model that follows the prompt text of either form works.
- The `## apply_patch` section sits under Tool Guidelines.
- A `# Sandbox and approvals` section replaces what Codex injects as a developer message: MeshPool has no sandbox, only permission modes. `<environment_context>` (cwd, `approval_policy`, shell) goes on the first user message: Auto maps to `never` (so the prompt's "proactively run tests" applies), Supervised and Auto-accept edits map to `on-request`.

## What runs in codex mode

Condition: harness profile setting `engine_style: "codex"` (or `MH_ENGINE_STYLE=codex`), a code chat (not Work/cowork chats), not plan mode.

- System prompt: Codex's text, plus the project's `MESHPOOL.md`/`AGENTS.md`/`CLAUDE.md` as an `<INSTRUCTIONS>` block. No MeshPool rules, skills catalog, honesty/plain-language blocks, or route addon (the system prompt stays byte-stable for the provider cache).
- First user message: the original text, then (after the hidden `​` marker) `<environment_context>` and the route's method text (`hrouter.route.addon`), the same form the `codex-routed` arm uses.
- Tools, exactly: `shell`, `apply_patch`, `update_plan`, plus the user's own MCP tools (MeshPool's bundled `mcp__meshpool_*` servers are left out). `view_image` is not offered.
  - `shell`: `command` (argv list; a string is run with bash), `workdir`, `timeout_ms`. Runs through the same path as `bash` (timeout, Stop, output cap, error context, commands log), same permission prompt ("Run this command?", group `bash`).
  - `apply_patch`: `input` is the patch. Codex's parser (lenient markers and heredoc wrapper, first chunk without `@@`, blank line as context, `*** End of File`, `*** Move to`), Codex's 4-pass line matcher (exact, trailing whitespace, both-side whitespace, typographic punctuation folded to ASCII), `@@ context` seek, chunks with no old lines append at the end, per-line line endings kept, upstream error and success texts (`Success. Updated the following files:` with `A`/`M`/`D`). All-or-nothing: every file is built in memory, then written with rollback. Code files (`.py`, `.json`, `.js`) that would stop parsing because of the patch are rejected before anything is written (a file that was already broken may still be patched). No read-before-edit rule. Paths stay inside the project and out of `.git`. Permission: "edit" group (auto-accepted in Auto-accept-edits mode; also when given through `shell`).
  - `update_plan`: `explanation`, `plan[{step, status}]`. Reuses MeshPool's plan plumbing: the same `plan` event as `todo_write`, so the UI Plan section and `plan_state()` show it. Returns `Plan updated`. No reminders.
- Loop: the model decides when to stop (a reply with no tool calls). Off in this style: review, mutation feedback, plan instruction/reminders, the todo nudge, the verify nudge, the scope check, the V3 notes, and the pre-first-call packs (reproduce, repo map, data preview, environment snapshot). Kept (no model calls): `_guarded_tool` loop guard, compaction, cut-off continuation, refused-field adaptation, quota stop, masked-key message, session files outside the project, steers.
- The `codex` profile: `plan: off, review: false, mutation: false, step_methods: false, methods: "v2-stop"` (the method text the default `lean-stop` profile gives), so `profile:codex` and the routed default differ in the engine, not in the routing text.

Cowork/data routes: the style applies only to chats whose agent profile is `code` (the bench, the code agent). The app's Work chats keep MeshPool's own prompt and tools whatever profile is forced. Assigning `codex` to a `data` or `docs` route key in `profiles.json` is a measurement decision left to the owner.

## How to A/B

```
python testkit/loop/bench.py --arms profile:codex,codex,codex-routed,profile:lean-stop --suites <the 52 hard tasks> --out runs-v2/codex-engine
```

- `profile:codex`: MeshPool's agent in codex style, routed (method text on the first message).
- `codex`: the real Codex over ACP. `codex-routed`: real Codex plus the route method text.
- `profile:lean-stop` (the current default): MeshPool's own style.

Compare solved/52, calls per task, and input tokens; the question is whether `profile:codex` closes the gap to `codex-routed` (same prompt text, same tools semantically, same method text) and where it differs (below). To assign it after measurement, add `"assign": {"code.build": "codex"}` (or change `default`) in `profiles.json`.

## Differences from real Codex

- `shell` instead of the current `exec_command`/`write_stdin` PTY pair: no interactive sessions or yielding. Default timeout is MeshPool's (180 s, capped at 900 s), not Codex's. Output is MeshPool's `[exit N]` plus output, capped with the full text in a file; error context for failing commands is added (no model call).
- `apply_patch` is a JSON function with one `input` string, not a freeform grammar tool (chat completions has no custom tools). The patch text and semantics are Codex's. Added: the syntax check on code files, and CRLF/CR line endings are preserved per line as upstream does.
- No sandbox: MeshPool's permission prompts and the workdir/`.git` write limits stand in for Codex's sandbox and approval policy.
- The bench's real-Codex arm (`codex-acp@2.6.0`) ran an older, shorter prompt (about 14.5k characters); this prompt is today's `default.md` (about 21k) and also carries the apply_patch section.
- No `view_image`, no web search tool, no sub-agents, no `AGENTS.md` discovery below the project root.
- The environment context is one block on the first user message (Codex sends it as a separate message and refreshes it per turn).
- Compaction is MeshPool's (older tool outputs elided at `compact_chars`), not Codex's model-written summary.
