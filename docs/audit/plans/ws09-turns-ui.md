# ws09-turns-ui: plan

Scope: the chat turn state machine (send, stop, queue, approvals, questions) in `app/meshpool_harness/ui/agent_api.py`,
its routes in `ui/server.py`, and the chat state in `ui/app.js` / `ui/composer.js`. Checked against 0.0.12 (`c189e04`).

## Root cause shared by most items

`Session.state` is written from six places with no common rule: `send()` reads it unlocked, `_start_turn()` sets
`working` after a key check and a wait of up to 50 s, the MCP start thread sets `idle` if it still reads `starting`,
`approve()` sets `working` after any answer, `_question_state()` flips it on events, and the turn thread sets `idle` at
the very end of a long epilogue with no `finally`. Nothing ties an approval, a question, an interrupt flag or a late
answer to the turn it belongs to, and nothing closes what was open when the process ended.

## The fix: one explicit state machine

In `Session`:

- `_turn_id` (int, class default 0): the turn token. `_claim_turn()` moves `idle`/`starting` -> `working` under
  `self.lock`, increments `_turn_id`, resets `_stop_req`, sets `t_turn`, and returns the token, or `None` when a turn
  already runs. It runs first, before the key check, the capability wait and every other slow step.
- `_set_state(to, token=..., expect=...)`: every transition goes through it, under `self.lock`, and is refused when the
  token is stale or the current state is not expected. Allowed: starting->idle, starting->working, idle->working,
  working<->waiting, working/waiting->idle.
- `_end_turn(token, synthetic=False)`: the only way a turn ends. Under the lock: if the token is current, takes the
  next message (after-stop list, queue, then goal/auto-resume decided by the caller) or goes `idle`; closes approvals
  still open from that turn (approval_done "stopped"); clears `_interrupting`. With `synthetic=True` it also emits
  `turn_end` with `synthetic: True` so the transcript never says running for a turn that never ran.
- The turn thread body is `try: ... finally: <epilogue, every step guarded> ; finally: _end_turn(token)`, so no
  exception (ENOSPC, a locked file on Windows, a bug in a helper) can leave the chat `working`.
- A follow-up turn (queue, goal, auto-resume, after-stop) hands over: new token, state stays `working`, new thread.
  Every early return (bad key, stopped before start, capability failure) ends its own token.
- Approvals carry the token they were opened under and a unique id (`"ap-" + 10 hex`). Leaving `waiting` after an
  answer happens only if that token is still current and the chat is still `waiting`; an approval asked outside a turn
  never changes the state.
- Questions: `_question_state` goes through `_set_state` with the current token, so a late `question_done` cannot
  resurrect a finished turn.
- Resume closes what was open: `_close_open_items()` runs after the events are loaded (transcript, harness events or
  rebuilt messages) and appends, once, `approval_done {decision: "stopped", closed: true}` for each approval without a
  done, `question_done {source: "stopped", closed: true}` for each live question without one, `tool_end {ok: false,
  stopped: true}` for each `tool_start` without an end, and `turn_end {synthetic: true}` when the last user turn has
  neither `turn_end` nor `interrupted`; then persists with `running: false`.
- `view()` adds `open: {approvals: [ids], question: id|null}`: the page closes any card the server no longer waits on.

## Items

| ID | Reproduces in 0.0.12? | Root cause (0.0.12) | Fix | Regression test (fails on the old code) |
|---|---|---|---|---|
| M01-01 | yes | `send()` agent_api.py:542-560 checks `state` unlocked; `_start_turn` sets `working` at :790 after the key check and `_capability_turn` (up to 50 s) | `_claim_turn()` under the lock before anything slow; the loser of the race steers/queues | `test_ws09_turns.py::test_two_concurrent_sends_run_one_turn` (two threads call send while the capability wait blocks; exactly one `agent.send`) |
| M01-03 | yes | epilogue :919-976 has no `finally`; `write_meta` at :939/:970 raising kills the thread before `state = "idle"` | epilogue steps guarded, `_end_turn` in an outer `finally` | `::test_epilogue_exception_still_ends_the_turn` (write_meta raises OSError(ENOSPC); state idle, Stop/Undo work) |
| M01-04 | yes | key check :781-786 returns from `_start_turn` called by the epilogue (:953) while state stays `working` | key check runs inside the claimed turn and ends it | `::test_follow_up_refused_by_key_check_goes_idle` |
| M01-05 | yes | early returns :784-786 and :903-907 emit `user` but no `turn_end`; `_turn_open` stays True and the transcript says running | early returns end with `turn_end {synthetic}`; resume adds one for old transcripts | `::test_early_return_turn_is_not_running_after_restart` |
| M01-06 | yes | `_start_mcp_servers` :376-377 check-then-set without the lock | `_set_state("idle", expect=("starting",))` under the lock | `::test_mcp_start_cannot_overwrite_a_running_turn` |
| M02-02 | yes | `self._n` restarts at 1 per Session (:434-435) | unique string ids; `answer()` and the route accept strings | `::test_approval_ids_are_unique_across_restarts` |
| M02-03 | yes | nothing closes an approval saved open | `_close_open_items()` on resume + page reconciles with `view.open` | `::test_resume_closes_open_approval_question_and_steps`, UI `test_ws09_ui.py::test_reopened_chat_has_no_live_cards_or_spinners` |
| M02-04 | same as M23-02 | | | |
| M02-05 | yes | `Agent.always` / Pi `_always` live in memory; ACP's allow_always lives in the harness process | the set is a persisting set bound from `meta.json["always"]` on create, resume and handoff; ACP allow_always is recorded per kind and answered from meta (bash never for destructive commands) | `::test_allow_in_this_chat_survives_reap_restart_and_handoff` |
| M03-01 | yes | after Stop, `send()` sees `working` and steers into the stopping turn; the epilogue drops steers when `agent._stop` (:932, :957) | a message sent once Stop was requested goes to `_after_stop`; `_end_turn` starts it as its own turn | `::test_message_sent_right_after_stop_runs_as_its_own_turn`; UI Enter-after-Stop check |
| M03-03 | yes | `send(interrupt=True)` waits 15 s (:528-530) then falls into steer/queue | interrupt stops and puts the message in `_after_stop`; no 15 s wait | `::test_send_now_on_a_slow_stop_still_runs` |
| M03-04 | yes | `_interrupting` (:520) is never cleared when the interrupted turn already ended | the flag holds the token it was set for; set only under the lock while that turn runs; cleared by `_end_turn` | `::test_interrupt_flag_does_not_leak_to_next_stop` |
| M07-01 | yes | saved open question comes back live; `workTick` (app.js:991) hides Working whenever any `.qcard:not(.done)` exists | resume closes it; `workTick` uses the server state | backend resume test + UI test |
| M08-01 | yes | composer.js:151 disables Send while `starting` (MCP start, up to 45 s) though Enter and the server accept | Send enabled while starting; a built-in turn that starts before MCP is ready waits for it (Stop breaks the wait) | `::test_send_while_starting_waits_for_mcp_then_runs`; UI test Send enabled while starting |
| M23-02 | yes | `approve()` :446-449 sets `working` after any answer, even when the turn ended | token-guarded transition; open approvals of an ended turn are closed by `_end_turn` | `::test_late_permission_answer_does_not_revive_the_turn` |
| X-06 | yes | retry notices (core.py:2033, :1928) are plain status notes that stay | `Session.emit` tags retry notices `retry: true` and emits `retry_ok` once the model answers; the page removes retry notes on `retry_ok` (and on a reply/tool step, for older transcripts) | `::test_retry_notice_is_resolved_when_the_call_succeeds`; UI test |
| P-03 | yes | as M02-03/M07-01 plus steps whose end never came keep `.st.run` spinners and the group dots animating | resume closes steps; page settles steps and groups whenever the chat is idle and closes cards not in `view.open` | UI test: 36 open approvals + 30 open steps seeded; after reopen 0 enabled Allow buttons, 0 `.st.run`, no running animations |

## Execution

Two Sonnet subagents in parallel on disjoint files: backend (`agent_api.py`, `server.py`,
`tests/test_ws09_turns.py`) and page (`app.js`, `composer.js`, `tests/test_ws09_ui.py`) against the contract above.
Opus reviews each diff, reverts each fix to prove its test fails, and drives the real app with the mock model.
