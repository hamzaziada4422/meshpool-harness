# ws08-routing plan: routing, modes, Work output location

Base: `claude/sleepy-faraday-tkjcox` (0.0.12; rebased later on 370427e3, where every item still reproduced). Every item below was re-checked against 0.0.12 code; all still reproduce
(M29-04 partly: thumbs and Try again now call `outcomes.mark()`, Undo still does not, and `refit()` applies a mark to
every earlier turn of the chat).

## S-04 / D-04: Code-mode office prompts silently go to the Work agent

Root cause. `ui/agent_api.py` create-session: with Auto, `harness_router.route(task, kind, category=<spec backend>)`.
In a Code chat `hrouter/route.py:_decide` turns "data/docs words in a Code chat" into spec `data`/`docs`, backend
`data`/`documents`; in `defaults/routing.json` those categories score `meshpool-work` 84/85 vs `meshpool-code` 70/55
(+2 Code mode bonus), so `harness_router.route` (`harness_router.py:route`, candidate set `_ready()`) returns
`meshpool-work`. `_ready()` ignores the chat's mode. The user sees only "Using Meshpool Work".

Decision (owner direction, measured cost): Code mode handles office files itself. Meshpool Code already gets the office
MCP tools once a message asks for an office file (`_capability_turn` sets `office_on`) and has python; on the audit runs
the Work engine path cost 2.3-8.6x a direct python edit at equal pass rate. So:

1. `harness_router._ready(kind)` keeps only agents whose `mode` is the chat's mode (or `any`). A Code chat can never be
   routed to Work (or Hermes); a Work chat never to a code-only agent. One rule removes the class of silent cross-mode
   switches for every category, including `suggest._agent` and `_capability_turn`, which use the same function.
2. Auto's pick is said, not hidden: the "Using X" event carries the reason and, when Auto picked something other than
   the mode's own agent, a `back` target (the mode default). The chat line shows the reason and a "Use <default>
   instead" button that calls the existing `/api/agent/<id>/harness` switch (one click, history kept).

Tests (`tests/test_ws08_routing.py`): office prompts (pptx/xlsx/docx/data) in `kind="code"` with all built-ins ready
route to a code agent, never `meshpool-work`; `Session` created through `create()` with `kind=code`, harness Auto and an
office task runs on `meshpool-code` and its "using" event has `auto.reason`; a Work-mode office task still picks
`meshpool-work`; the `back` field is set when Auto picks a non-default agent.

## M29-02: Auto has no "failed to start" transition

Root cause. `ACPAgent._start` / `_ensure_session` (`harnesses/acp.py`) and `PiAgent._start` (`harnesses/pi_rpc.py`)
raise on a process that cannot start; `send()` turns it into an error event and nothing is recorded. `_ready()` only
checks `available` (installed), so Auto picks the same broken agent for every new chat.

Fix. New `hrouter/health.py`: `start_failed(hid, error)`, `start_ok(hid)`, `broken() -> {hid: row}`, kept in
`<data>/routing/harness-health.json` through `fsio.update_json` (cross-process lock, atomic write). A failure excludes
the agent from `_ready()` until it starts successfully once (the user picks it by hand, or a retry after
`RETRY_AFTER_S` = 6 h, so a fixed install comes back on its own). ACP and Pi record failure/success around their
start (process spawn + initialize + session/new). In the Session, when the first turn of an Auto-routed chat fails to
start, the chat hands off to the next agent Auto picks (now excluding the broken one), says so in one line, and sends
the message again.

Tests: a fake ACP manifest whose command exits immediately: after one failed start `_ready()` excludes it and
`route()` picks the next agent; `start_ok` restores it; the expiry restores it; concurrent writers do not lose rows.

## M29-03: outcome log flags good turns as failed after an earlier error

Root cause. `hrouter/outcomes.py:log_turn` computes `error`/`stopped` from `session.events[-30:]`, i.e. the chat's
last 30 events, not the turn's.

Fix. `log_turn(session, route, usage_before, ev0=None)` takes the turn's first event index (the caller,
`Session._start_turn.go`, already has `ev0`) and looks only at `events[ev0:]`; it also records `turn` (checkpoint turn
number) so marks can name a turn.

Test: a chat whose turn 1 ended in an error event and whose turn 2 is clean: turn 2's row has `error: False`
(fails on the old code).

## M29-04: `outcomes.mark()` has no caller for Undo; refit's undo branch is dead

Root cause. Thumbs and Try again post `/api/receipt/feedback` (which calls `mark`), but `Session.revert()` (Undo) never
marks, so the `"undo"` outcome in `refit()` is never produced; and `refit()` counts a mark against every earlier turn
of the chat (`m.ts >= r.ts`), so one retry fails the whole chat history.

Fix. `mark(chat, outcome, turn=None)`; `Session.revert(turn)` marks `undo` for each logged turn >= `turn`;
`/api/receipt/feedback` and the UI (`chat010.js feedback010`) pass the reply's turn number. `refit()` applies a mark to
the latest logged row of that (chat, turn) at or before the mark (no turn: the chat's latest turn before the mark);
the latest thumbs mark wins over an earlier one. `_append` rotates under `fsio.file_lock` with an atomic rewrite.

Tests: Undo through `Session.revert` writes an `undo` mark and `refit()` counts exactly that turn as failed; a retry
mark on turn 2 does not fail turn 1; thumbs down then up leaves the turn good; the feedback endpoint stores `turn`.

## F-03: `meshpool work` never shows (or reports) the answer

Root cause. `cli.py` `work` prints `res.get("answer")`, but `run_request` returns the answers only inside `steps[]`, so
it prints `null`; `conn.report()` builds its comment from the same missing key, so GitHub/Jira/HTTP reports carry no
answer.

Fix. `run_request` also returns top-level `answer` (last step's answer), `artifacts` (all steps) and `reason` (first
failing step); `work` prints the answer and files as readable text (`--json` keeps the JSON form) and reports them.

Test: `cli.main(["work", "file:task.md", ...])` with a stub engine: stdout contains the engine's answer and file;
`--report` writes `task.md.result.json` with that answer.

## F-08: memory and scheduling missing in Code-mode chats

Root cause. The default profile (`defaults/profiles.json` "codex") gives every Code route `engine_style: codex`;
`Agent.tool_schemas` (`agent/core.py`, Codex branch `keep()`) drops every `mcp__meshpool_*` tool except office, which
removes the `meshpool-assistant` server (memory_save/view/forget, project_note, schedule_task, list/delete, search_chats).
Also `schedule_task` defaults to `mode="cowork"`, so a task scheduled from a Code chat runs as Work.

Fix. `keep()` also keeps `mcp__meshpool_assistant__*` except `run_subtasks`; the assistant server learns the chat id
(`MH_CHAT_ID`, as the browser server already does) and `schedule_task` defaults `mode` to that chat's kind.

Test: a Code-profile Agent in codex style with a fake MCP manager exposing assistant + web tools lists the memory and
schedule tools and still hides web/run_subtasks; `schedule_task` with `MH_CHAT_ID` of a Code chat stores `kind: code`.

## S-05: Work attachments saved under `attachments/`

Root cause. `ui/workbench.py:attach` puts files attached in a chat with its own folder under `attachments/`, so an
"edit it in place" result is not at the folder root where the user (and the Files panel's top level) looks.

Fix. Save at the folder root in every chat (name clash: "name (2).ext" as today).

Test: `attach()` into a chat folder returns a root-level path and the file is at the root.

## F-18: onboarding promises "Meshpool Projects" but chats run in per-chat folders

Root cause. `ui/app.js` onboarding option "Start in Meshpool Projects" and its result text say tasks start in
`~/Meshpool Projects`, but a new chat without a chosen folder gets a per-chat folder in the data folder
(`project_store.new_scratch`), so the promise is false.

Fix. The option and its text say what happens: without a folder, each chat keeps its files in its own folder, which the
chat's Files panel opens (Show in Finder/Explorer); add folders any time. (The per-chat folder design stays: it keeps
chats from overwriting each other.)

Test: a static check that the onboarding no longer claims tasks start in Meshpool Projects, plus a Session created
without a folder lands in `chats_root()` (the behaviour the text now describes).

## Execution

Three Sonnet subagents in separate worktrees (merged here): (1) S-04/D-04 + M29-02, (2) M29-03 + M29-04,
(3) F-03 + F-08 + S-05 + F-18. Then review, revert-to-prove each test fails without its fix, full suite with `-n 4`.
