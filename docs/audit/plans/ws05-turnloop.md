# ws05-turnloop plan: agent turn loop, step caps, guards, quick turns, Stop

Scope: `app/meshpool_harness/agent/core.py` (turn loop), `core/guards.py`, `agent/planning.py`, plus the small
front-end pieces each fix needs (`ui/agent_api.py`, `ui/app.js`, `harnesses/pi_rpc.py`, `core/gateway.py`,
`core/mutate.py`, `profiles.py`, `cli.py`, `agent/tui.py`). Line numbers are 0.0.12 (`a76b6e3`).

Root-cause themes, so each fix removes a class of bug rather than one symptom:

1. **Turn-scoped state is reset in one place.** `Agent._begin_turn_state()` runs first in `_turn()` and resets
   everything that must not leak from one turn to the next (429 backoff counter, quick-turn wrap flag, loop-breaker
   history, "in a turn" flag). New turn-scoped state goes there.
2. **Every blocking call of a turn runs through `_wait`** (model calls on every wire, the reviewer call, the
   mutation check), so Stop ends the turn within a fraction of a second whatever is running.
3. **A request is valid by construction.** `_body()` pairs every assistant tool call with a tool result (and drops
   orphan results) on the copy it sends, so no interruption, crash or resume can produce a 400 on the next request.
4. **One permission matcher** (`core/permrules.py`) for saved rules, chat grants and the Supervised read-only
   shortcut, used by the built-in agent and the Pi bridge.
5. **Stop is a generation, not a flag that can be reset.** `Agent.stop()` already bumps `_gen`; the front end
   records the generation when a turn is requested and `send()` refuses to start a turn whose generation moved.

## Items

| ID | Reproduces in 0.0.12? | Root cause | Fix | Regression test |
|---|---|---|---|---|
| S-08 | Yes. `max_steps` defaults to 2000 (core.py:771); a mock model repeating one call runs ~2000 requests per turn. | No sane cap; guards never stop a turn. | Default cap 200 steps per turn (`MH_MAX_STEPS`, read per turn so a settings change applies to the next turn; `0`/`off` = no cap). Repeated-step breaker (below) ends a loop within ~8 steps. | `tests/test_turnloop_caps.py::test_repeating_mock_model_is_stopped_quickly` (mock model repeats one call forever: turn ends in <= 10 requests with a clear error) and `::test_default_step_cap_bounds_requests` |
| H-07 | Yes (cap 2000; `_v3_notes` only notes and only with V3; unknown models fall back to `deepseek.yaml`, temperature 0.0). | As S-08; `profiles.for_model` falls back to a greedy profile. | Step cap + breaker as S-08. Unknown models get `profiles/default.yaml` (provider default sampling, no temperature). | `::test_breaker_stops_identical_steps`, `::test_breaker_stops_alternating_pattern`, `::test_unknown_model_is_not_greedy` |
| G-04 | Yes. | As S-08; no UI setting; CLI has `--max-turns` only. | Default 200; breaker `MH_REPEAT_STOP` (default 8 identical steps = same calls and same results with no file change; a warning note at half); Settings field "Max steps per reply"; CLI `--max-steps` (alias of `--max-turns`), documented. | `::test_cap_setting_applies_next_turn`, `::test_cli_max_steps_flag`, UI settings test |
| H-15 | Yes. `_permit` asks for every `bash` in Supervised (only `READ_ONLY` tool names skip). "Always" adds group `bash` = every command. | No notion of a safe read-only command; chat grant keyed by tool group. | `permrules.safe_read(cmd, root)`: strict read-only check (subset of `tools.read_only`, no writes via `sort -o`/`uniq a b`/`sed`/`awk`/`find -fprint`/`git branch x`, no path outside the project, no `$`/backticks) skips the prompt in Supervised and Auto-accept edits. Chat grant becomes command patterns (below). | `tests/test_permission_rules.py::test_supervised_does_not_ask_for_ls`, `::test_supervised_still_asks_for_reads_outside_project` |
| G-05 | Partly. Saved rules exist (`_rules`, `_rule_hit`) but: `bash_many` joins commands so `bash:npm test*` matches `npm test; rm -rf x`; Codex `shell` commands are matched as `str(list)`; no UI to edit rules; no CLI command to edit them; "Allow in this chat" allows every command. | Matcher on the raw joined string; grant keyed by tool group. | `permrules.rule_hit` splits every command (bash, bash_many, bash_start, shell, probe, trace) into segments: an allow rule must cover every segment, a deny rule hits on any segment. Chat grant for commands stores `bash:<prefix>` patterns (`npm test`, `git status`, `ls`, ...) and covers a later command only when every segment matches; approval cards and the terminal prompt name the pattern. Settings > Permissions gets an allow/deny rule editor (`/api/rules`); CLI `meshpool permissions [list|allow|deny|remove] RULE [--project]`. Same scoping in the Pi bridge. | `::test_allow_rule_must_cover_every_segment`, `::test_shell_tool_rules_match_the_script`, `::test_chat_grant_is_scoped_to_the_command_pattern`, `::test_rules_api_round_trip`, `::test_cli_permissions_command`, `::test_pi_chat_grant_is_scoped` |
| H-25 | Not present for Stop (`_repair`), but a session saved mid-turn by a crash can be resumed with an unanswered tool call, and nothing guards the request itself. | No request-level invariant. | `_body()` pairs tool calls and results on the copy it sends; `Agent.resume()` repairs the history. | `tests/test_stop_and_cancel.py::test_stop_mid_tool_leaves_valid_history`, `::test_stop_mid_parallel_batch`, `::test_error_mid_turn_leaves_valid_history`, `::test_resume_after_crash_mid_tool_sends_valid_request`, `::test_body_pairs_orphans` |
| M34-01 | Yes. `_take_inbox` never revisits `pace_deadline`; `resume_steers` reuses the old deadline, so the late turn is wrapped at step 1. | Quick budget decided once per `send()`. | `_take_inbox` re-classifies steers: a non-quick steer clears the budget and, if the turn was already wrapped, lifts the no-tools rule with a note that the quick limits no longer apply. `resume_steers` gives the new turn its own budget (quick steer: fresh deadline; otherwise none). | `tests/test_quick_turns.py::test_task_added_to_quick_turn_gets_full_budget`, `::test_task_sent_as_quick_turn_ends_gets_full_budget` |
| M34-02 | Yes. Pi wrap calls `agent.steer(PACE.WRAP)`: emits a `user` steer event (persisted) and can land after the turn ended. | Model-only note sent through the user-message path. | `PiAgent.nudge(text, turn)` sends the RPC steer without a `user` event, only while that same turn is running (checked under the lock the turn end takes). The UI uses `nudge`. | `::test_pi_quick_wrap_is_not_a_user_message`, `::test_pi_quick_wrap_never_reaches_next_turn` (fake Pi) |
| M34-03 | Yes. `Session.set_plan` sets `agent.plan` mid-turn; `tool_schemas()`/`_codex()` read it per step. | No turn boundary for mode changes. | `Agent.plan` is a property: a change during a turn is held and applied when the turn ends. `Session.set_plan` while working stores it and says it applies from the next message (ACP `apply_mode` then). | `tests/test_plan_switch.py::test_plan_toggle_mid_turn_keeps_turn_tools` (Codex style, shell call after toggle runs) |
| M34-04 | Yes. `_apply_refused` drops `tool_choice` for a model that refused it, so the wrap request still offers tools. | Wrap relied only on `tool_choice`. | Once wrapped, every later step is no-tools, and tool calls returned on a no-tools step are not run: each gets "Not run: time is up..." and the model is asked again (at most twice, then the turn ends with its text). | `::test_quick_wrap_enforced_when_tool_choice_refused` |
| M34-05 | Yes. `app.js` turn_end shows "Build this plan" when the chat is in plan mode now (`cur.view.plan`). | Card keyed to current mode, not the turn. | The card shows only for a turn whose user message was sent in plan mode (`ev.plan`), only for the latest such turn, and is removed when a new message starts. | Playwright test in `tests/test_plan_switch.py::test_build_card_only_on_plan_turns` |
| M28-01 | Yes. `_no_vision` makes the drop one-shot (core.py:1896) and is never cleared. | Per-agent flag instead of per-model capability applied when the request is built. | `_no_vision_models` set; `_body()` leaves images out for those models (history keeps them); a vision 400 adds the model and retries; switching to a vision model sends images again. | `::test_second_image_to_text_only_model_does_not_400`, `::test_switch_to_vision_model_sends_images_again` |
| M28-03 | Yes. `_aux_complete` calls `_post` directly; `_mutation_feedback` runs `MU.survivors` inline. | Calls outside `_wait`. | Both through `_wait`; `mutate.survivors(..., should_stop)` stops between mutants and kills the running test process. | `::test_stop_cuts_reviewer_call`, `::test_stop_cuts_mutation_check` |
| M28-04 | Yes. `SimpleLedger(...).chat` runs outside `_wait` (core.py:1824). | As M28-03. | Through `_wait`. | `::test_stop_cuts_responses_wire_call` |
| M28-05 | Yes. `_n429` resets only after a successful call. | Turn-scoped state not reset. | Reset in `_begin_turn_state()`. | `::test_429_backoff_resets_each_turn` |
| M28-06 | Yes. `wait_until_up` leads only after `next_probe`; when every chat gave up nobody probes and a new caller sleeps silently up to 60 s. | Backoff shared even with no one waiting. | A caller that finds no other waiter probes at once; while it waits for the backoff it says so. | `tests/test_stop_and_cancel.py::test_fresh_caller_after_everyone_gave_up_probes_at_once` |
| M02-01 | Yes. Stop during an approval returns "no" and `_run_tool` writes the denial text. | Stop read as a denial. | `_run_tool`: a refusal while `_stop` is set raises `Interrupted` (history gets "Not run: the user stopped the agent."). Same in the Pi bridge. | `::test_stop_during_approval_is_not_a_denial` |
| M01-02 | Yes. `_start_turn` resets `_stop_req` after the capability wait, and `Agent.send` resets `_stop`. | Stop flag reset after it was set. | `_start_turn` marks the chat working and records the stop generation before any slow step; the office-tools wait polls Stop; `Agent.send` refuses (as Stopped) a turn whose generation moved since it was requested. | `::test_stop_while_office_tools_start_never_runs_the_turn`, `::test_stop_between_check_and_send` |
| M04-01 | Yes. `_goal_next`/`_resume_next` look for any error event in the turn. | Outcome read from any event, not from how the turn ended. | `_turn_outcome(evs)`: an error counts only if no assistant reply or tool activity came after it. GOAL: DONE in the final reply wins. | `tests/test_quick_turns.py::test_recovered_error_does_not_auto_resume`, `::test_goal_done_after_recovered_error` |

## Breaker details (S-08, H-07, G-04)

A step's signature is its calls (name + arguments) plus a hash of their results. With no file change in between:
the same signature 8 times in a row, or a 2- or 3-step pattern repeated 4 times, ends the turn with an error event
("Stopped: the agent repeated the same step ... Send a message to continue"). At half that, a note asks the model to
change approach. The history stays valid (every call has its result). `MH_REPEAT_STOP=0` turns it off.

## Execution

Five Sonnet work packages, each in its own worktree, merged here: A (caps, breaker, sampling), B (quick turns,
plan switch, build card), C (Stop, cancellation, vision, 429, gateway, H-25), D (permissions), E (goal/resume
outcome). Each package adds its own test file; reviewed line by line, each test is shown to fail with the fix
reverted.
