# Why the winning harnesses win, and what MeshPool should copy

Research only. Sources: `docs/results/v3/EVAL52_V3.md`, `runs-v2/eval52-v3/{results,meter}.jsonl`, and the installed code of each harness (Pi `app/vendor/pi`, Hermes `app/vendor/hermes`, OpenCode `opencode-ai@1.19.5` binary in the npx cache, Codex `codex-acp@2.6.0` and its `@openai/codex-linux-x64` binary, read with `strings`). Nothing was run and no model was called.

Data caveats, stated once:
- `meter.jsonl` records only per call: arm, task, path, status, secs, prompt/cached/completion tokens. It has no request or response bodies, so per-task tool calls are not visible for Codex, OpenCode, Pi or Hermes. Only MeshPool rows (`v2`, `routed` = V3) carry a `trace`. Winner behaviour below comes from their code and prompts, plus call and token counts.
- In `results.jsonl` the arm named `routed` is V3 (the one that scored 47/52 at 21 calls); `v2` is V2. The task text said "V2 at 16 calls"; V3 is the worse one (21 calls, 463k input tokens), so most findings are about V3.
- Meter call counts are higher than the report's "calls/task" for Hermes (median 14 vs 8.6) and OpenCode (a small title call comes first in 45 of 52 tasks, under 1.5k prompt tokens). The report's figures are the ones used for comparisons.
- One run per task per harness. A single flip is noise (McNemar p>=0.45 for every pair). The cost and call gaps are the robust signal; the solve gaps are not.

## 0. The headline finding: the gap is a tail, not the median

| Arm | Median calls/task | Median input tok/task | Tasks with >=30 calls | Share of all input tokens from those tasks |
|---|---|---|---|---|
| MeshPool V3 | 14 | 84k | 11 (9 of them passed) | 81% |
| MeshPool V2 | 11 | 54k | 10 | 71% |
| Codex | 7 | 88k | 1 | 9% |
| OpenCode | 7 | 131k | 0 | 0% |
| Pi | 7 | 121k | 0 | 0% |
| Hermes | 7 | 112k | 1 | 10% |

- V3's median task costs about what Codex's does (84k vs 88k input tokens; V3 is 96% cache-hit vs Codex 88%). Prompt size is not the problem. Codex's first call is 11.8k tokens against V3's 4.4k and it still wins on cost.
- V3 averages 462k input tokens/task because 11 tasks run to the 60-call cap, and 5 of the 11 hit it while passing: te-bankers-round (61 calls, 5.7M tokens), rf-dedupe-validation (60, 4.6M), te-semver (60, 4.1M), alg-window-median (60, 1.1M), alg-roman-strict (60, 1.1M). The top 8 tasks are 70% of V3's spend.
- Winners have no such tail. Their worst task is 30 calls (Codex, perf-regex-redos, which it failed). They stop when the work is done; MeshPool keeps verifying a result that already passes.

What the V3 tail is made of (trace evidence, `routed` rows):
- Self-built verification: te-semver reached `mutation: 18 of 25 survived`, then 15+ hand-written `python3 - <<PY` mutation scripts. rf-dedupe-validation ran 14+ near-identical `python3 - <<'EOF' src=open('_check.py').read()...` calls. alg-roman-strict alternates `edit roman.py` and a failing check heredoc 17 times.
- Byte-dump rabbit holes: mf-rate-limit has 27 `od -c` / `cat -A` calls out of 64 (failed, 0 hidden-test pass); mf-js-recipe-units has 56 of 60 (failed). Every other harness solved mf-js-recipe-units in 8-16 calls and mf-rate-limit in 9-17 calls (Hermes failed it at 0 calls recorded). dbg-async-lost-update had 21 such calls but passed. The "refuse the same call" guard (commit 0ef9173) does not stop these, because each command differs slightly (`sed -n '4,5p' | cat -A | sed -n '2p' | od -c | sed -n '3,4p'`). The trace logs them as ok=True, so they may be returned as "Not run" stubs, but they still cost a model call each.
- Gates that add rounds after the model already said "done": `review` fired in 38 of 52 runs (an extra model call each) and produced findings only 3 times. `mutation` ran in 39 runs and found survivors only 4 times. The stop path in `agent/core.py` `_turn` can add a verify nudge, a review, a mutation round, a todo nudge and a scope check in sequence.
- Bash failure rate is 125/655 (19%) in V3 against 40/494 (8%) in V2. Most are the model's own check scripts exiting non-zero, which is fine, but 7 `write_file` calls failed with "missing required argument 'path'" (huge writes, rf-dedupe) and the 3-ish "new is identical to old" edits are pure waste.

Routing detail: 4 of 6 refactor tasks (`rf-globals-to-instance`, `rf-sync-async-client`, `rf-dedupe-validation`, `rf-split-invoice`) were routed to `code.build`, which carries the heaviest method text (requirement list, `_check.py`, final audit). That is why V3 refactoring costs $0.0268/task against V2's $0.0024 (11x) at the same 6/6. Average calls by category (V3 / V2 / Codex / OpenCode / Pi / Hermes): refactoring 31.2 / 9.7 / 6.2 / 7.0 / 7.5 / 9.8; test-engineering 30.2 / 17.8 / 8.2 / 9.2 / 10.3 / 10.7; multi-file 30.9 / 23.8 / 8.0 / 7.9 / 9.1 / 7.1; algorithmic 27.4 / 24.9 / 11.8 / 9.2 / 11.0 / 10.9.

## 1. Per-harness profile

### Pi (`@earendil-works/pi-coding-agent`, 48/52, 8.3 calls, $0.0052)
- System prompt: tiny. `dist/core/system-prompt.js` builds: "You are a coding assistant running inside pi, a coding agent harness. Help users inspect files, run commands, and modify code.", a tool list, a rules list, a docs-paths block (only read "when the user asks about the harness itself"), the cwd. Roughly 2-3k characters. Rules are only the tool guidelines plus "Be concise in your responses" and "Show file paths clearly". There is no methodology text, no testing instruction, no planning instruction.
- Tools: default `read, bash, edit, write` (4; `grep`, `find`, `ls` exist but are off by default, and the prompt says "Use bash for file operations like ls, rg, find"). Schemas are small (about 2k characters in total, estimate). Guidelines: read = "Use read to examine files instead of cat or sed."; write = "Use write only for new files or complete rewrites."; edit = "Use edit for targeted changes. Batch changes to one file in a single call."
- Edit format: `edit(path, edits[{oldText,newText}])`. Several disjoint replacements per call, each matched against the original file (not incrementally), must be unique and non-overlapping. The tool accepts sloppy inputs (edits sent as a JSON string, a single object, or legacy oldText/newText) so format slips do not cost a retry. No read-before-edit rule, no fuzzy matching in the tool contract.
- Planning: none (no todo tool). Verification: none instructed; the model does what it wants. Stop: the assistant message has no tool calls. Churn limiters: only the small tool surface and the absence of any scaffolding. Pi is the proof that a near-empty prompt on this model gets 48/52 at 8 calls.
- Context: compaction with `reserveTokens 16384`, `keepRecentTokens 20000` (`settings-defaults.js`); never reached in these 8-call runs. Pi was run through MeshPool's `pi_rpc.py` with `--mode rpc --no-approve`.

### Hermes (`app/vendor/hermes`, 49/52, 8.6 calls, median 46.6 s, slowest of the winners)
- System prompt (`agent/prompt_builder.py`): a short identity ("Be direct: match the length of your reply to the weight of the ask... no narrating tool calls"), plus blocks injected for the model family. `deepseek` is in `TOOL_USE_ENFORCEMENT_MODELS`, so it gets: "You MUST use your tools to take action... Every response should either (a) contain tool calls that make progress, or (b) deliver a final result". It also gets the execution-discipline block (`<tool_persistence>`: "Keep calling tools until: (1) the task is complete, AND (2) you have verified the result"; `<verification>`: "'done' means every named acceptance criterion is verified"; `<missing_context>`; `<act_dont_ask>`), `TASK_COMPLETION_GUIDANCE` ("the deliverable is a working artifact backed by real tool output") and `PARALLEL_TOOL_CALL_GUIDANCE`, which gives MeshPool's own reason: "batching avoids resending the whole conversation on every extra round-trip... Only serialize calls when a later call genuinely depends on an earlier call's result". Plus memory/skills/session-search guidance that is dead weight in a one-shot task.
- Tools (ACP toolset `hermes-acp` = `_CODING_TOOLS`): about 35 (terminal, process_manage, read_file, write_file, patch, search_files, web_*, browser_*, todo_list, memory, skills_*, execute_code, delegate_task...). Large schema, yet only 8.6 calls, so schema size is again not the lever. `patch` = replace-only find/replace with "fuzzy matching (9 strategies) so minor whitespace/indentation differences won't break it", returns a diff, "auto-runs syntax checks after editing" (V4A patch mode only for OpenAI-family models). Hermes states plainly in code comments that "models UNDER-explore by default; never re-add an exploration-thrift line".
- Loop guards (`agent/tool_guardrails.py`): identical (tool, args, result) 3x triggers a notice, repeated 2-4-call cycles are detected, byte-identical repeated results become a reference stub (>=512 chars), idempotent read tools are classified separately. Same family as MeshPool's guard, plus result-stub dedup that MeshPool lacks.
- Stop gate (`verification_stop.py`, `turn_stop_gates.py`): if the model ends right after editing code with no fresh verification evidence, one bounded nudge is added. This is nearly the same idea as MeshPool's `verify_nudged`, and it is the likely reason Hermes is the slowest winner. Context: compressor at 0.75 of the window, not reached.
- Planning: optional `todo_list`; no forced plan.

### OpenCode (`opencode-ai@1.19.5`, 48/52, 7.1 calls, lowest, $0.0047, 8/8 multi-file)
- System prompt (embedded in the binary; the variant used for non-Claude/GPT/Gemini models is the "You are opencode, an interactive CLI tool..." text, ~7k characters, mostly tone and examples): "IMPORTANT: You should minimize output tokens as much as possible", "fewer than 4 lines", "NOT answer with unnecessary preamble or postamble", "DO NOT ADD ANY COMMENTS", "NEVER commit". Doing-tasks section: search to understand, implement, "Verify the solution if possible with tests. NEVER assume specific test framework", and run lint/typecheck. The copy I found also says "Use exactly one tool per assistant message", which would argue against batching, yet OpenCode is the lowest-call harness, so batching is not what makes it efficient (I could not confirm which prompt variant ran for this model; treat that line with caution). Tool descriptions for glob/grep say "You have the capability to call multiple tools in a single response. It is always better to speculatively perform multiple searches as a batch".
- Tools: read, edit, write, bash, glob, grep, list, todowrite/todoread, task (sub-agent), webfetch, skill, question and more; about 10-12 enabled. Edit description: "You must use your `Read` tool at least once in the conversation before editing", exact string replace with `replaceAll`, errors "oldString not found" / "Found multiple matches... provide more surrounding lines". The in-process edit tool is known to use layered fuzzy matchers (line-trimmed, whitespace-normalised, indentation-flexible, block-anchor); these are not visible in the minified strings, so this part is from memory. It also returns LSP diagnostics after edits (not verified here).
- Planning: `todowrite` available, not forced. Verification: told to run tests/lint when known. Stop: no tool calls. Context: auto compaction and pruning of old tool output; not reached.
- Why it is cheapest on calls: terse output (1.5k completion tokens/task, the lowest), no gates, minimal method, and it begins editing quickly. 45 of 52 runs start with a small title-generation call (595 prompt tokens), which counts toward its 7.1.

### Codex (`codex-acp@2.6.0` over the `codex` Rust binary, 50/52, 8.2 calls, $0.0038)
- System prompt: for an unknown model slug Codex falls back to its generic coding-agent `prompt.md`, about 14.5k characters, embedded in the binary. Key text: "You are a coding agent running in the Codex CLI"; "Please keep going until the query is completely resolved, before ending your turn and yielding back to the user. Only terminate your turn when you are sure that the problem is solved. Autonomously resolve the query... Do NOT guess or make up an answer."; "Fix the problem at the root cause rather than applying surface-level patches"; "Avoid unneeded complexity"; "Do not attempt to fix unrelated bugs or broken tests"; "Keep changes consistent with the style of the existing codebase. Changes should be minimal and focused"; "Do not waste tokens by re-reading files after calling `apply_patch` on them. The tool call will fail if it didn't work."; "Use `git log` and `git blame`"; "prefer using `rg`".
- Validation text: "start as specific as possible to the code you changed so that you can catch issues efficiently, then make your way to broader tests"; formatting retries capped at 3; in the "never" approval mode (this benchmark: `agent-full-access`) "proactively run tests, lint and do whatever you need to ensure you've completed the task".
- Planning: `update_plan` tool, used "when the task is non-trivial", with a paragraph of good and bad plan examples; "Do not use plans for simple or single-step queries". Preamble messages before tool calls.
- Tools: `exec_command` (PTY, default 10 s timeout, `yield_time_ms`), `write_stdin`, `apply_patch` (freeform tool with a grammar: `*** Begin Patch / *** Update File / @@ / *** End Patch`, several files and hunks per call), `update_plan`, `view_image`, optional web search. Around 5 tools. Edit format: a V4A-style patch, so one call can edit many files and many hunks. Schemas are small; the 11.8k first-call figure is the long prompt plus environment context and AGENTS.md block.
- Stop: no tool calls. Context: `model_context_window = 128000` in MeshPool's generated config; it uses the Responses API (425 `/responses` calls in the meter); no compaction reached.
- Why it wins: strongest quality (50/52) and a run-to-completion discipline without any harness-side gates. It tells the model to be specific in testing and to stop re-reading after a patch.

### MeshPool V3 (for contrast; `agent/core.py`, `hrouter/specs.py`, `core/tools.py`)
- Base system prompt (`SYSTEM` + `V3_RULES` + `PLAIN` + `HONESTY`) is about 4.4k tokens on call 1 with the spec addon, `environment_snapshot`, `repo_map`/`reproduction` blocks. Tool set after lean-dropping: roughly 10 tools (read_file, grep, glob, edit, write_file, bash, bash_many, todo_write unless dropped, diagnostics, fetch_url), about 4k characters of schema (all 15 schemas together are 7.3k characters).
- Edit: `edit(path, old, new)`, one replacement per call, read-before-edit enforced (`core/tools.py` lines ~507 and 523; one failure in the run), returns a syntax and bug check. `write_file` is used as often as `edit` (104 vs 100 calls).
- Planning: `todo_write` plus `_PLAN` plan-needed logic and reminders every 6 calls (`agent/planning.py`). Verification: spec text (requirements list, one `_check.py`, final audit, brute-force comparison for algorithms, probe neighbouring cases for debugging), then harness gates at stop: verify nudge, LLM review (`core/review.py`), mutation feedback (`core/mutate.py`), todo nudge, optional scope check.
- Stop: no tool calls AND every gate passed. This is the main structural difference from all four winners.
- Churn limiters: loop guard (3 exact / 5 numeric-shape repeats), `_v3_notes` (repetition note, no-progress note at 12 and 24 steps, "15 steps left"), 60-step cap in the bench. They fire late; the 60-call runs show they do not bend the trajectory.

## 2. Per-category winners and likely mechanism

Solved (V3 / V2 / Codex / OpenCode / Pi / Hermes) and cost per attempted task:

| Category (n) | Solves | Winner(s) | Cost note | Likely mechanism |
|---|---|---|---|---|
| algorithmic (8) | 7/7/8/8/7/7 | Codex, OpenCode | V3 $0.0099 vs $0.0053-0.0055 | Both just implement and run a quick check; 9-12 calls against V3's 27. V3's algorithm addon demands a brute-force comparison on hundreds of seeded inputs plus a timing run, which produced two 60-call passes (window-median, roman-strict) and the alg-line-diff failure at 19 calls (V1, Pi, OpenCode and Codex solved it in 6-17 calls). The method text, not the model, is the churn. |
| ambiguous (5) | 4/5/5/4/5/5 | Codex, Pi, Hermes, V2 | V3 cheapest of all at $0.0021 | Winners read the nearby code and decide; Hermes explicitly says "act on the obvious default" (`<act_dont_ask>`) and Codex says "Do NOT guess". V3's 4/5 miss (amb-invoice-cents) was a 9-call run, so V3 stopped early rather than over-verified. This is a quality miss of one task; OpenCode missed the same one. Treat as noise. |
| data (6) | 6 each | tie | V3/V2 $0.0016 vs $0.0026-0.0042 | MeshPool's own win. `data_preview` pre-injection removes the exploration round (3.7 calls vs 4.2-5.3), and the data spec is short. Keep. |
| debugging (8) | 8/8/8/7/7/8 | Codex, Hermes, V2, V3 | V3 $0.0033 vs Codex $0.0030 | Reproduction pre-run (`_PACKS.reproduce`) and the debug spec are competitive. Calls: V3 14.1 vs 6-7 for winners, the excess is again verification (probe neighbouring cases) and od/cat-A loops. OpenCode's dbg-stale-cache miss was a 3-call run (stopped too fast). |
| multi-file (8) | 5/7/7/8/6/6 | OpenCode (8/8, 7.9 calls), Codex and V2 (7) | V3 $0.0056 vs Codex $0.0035 | OpenCode: terse, edits fast, fuzzy edit matching, no gates. Codex: one `apply_patch` call spans many files. V3 lost three tasks (mf-job-hooks, mf-js-recipe-units, mf-rate-limit): two were byte-dump loops (56/60 and 27/64 calls). V3's `code.build.feature` addon (grep every path, build `_check.py`) plus 30.9 calls is the opposite of the winners' behaviour. No winner is consistently right on mf (every harness misses at least one of mf-config-layers, mf-job-hooks, mf-rate-limit), so some mf variance is task-level. |
| performance (5) | 5/3/4/4/5/5 | V3, Pi, Hermes | V3 $0.0034 cheapest | V3's perf variant addon (time first, find hot spot, replace with index, time again) works: V3 +2 over V2. Keep it. |
| refactoring (6) | 6/6/6/6/6/6 | all tie | V3 $0.0268 vs $0.0024-0.0045 | Pure cost loss. 4 of 6 rf tasks were routed to `code.build`, giving them requirement lists and `_check.py` loops; rf-dedupe-validation alone is 4.6M tokens. V2 did the same tasks in 9.7 calls. |
| test-engineering (6) | 6/5/6/5/6/6 | V3, Codex, Pi, Hermes | V3 $0.0348 vs Codex $0.0050 (7x) | V3's te method (boundary lists, exact values, mutation feedback) bought one extra solve vs V2, at 30 calls and 7x the cost. Codex gets 6/6 at 8 calls with only "start as specific as possible" and "when adding tests... run them". The mutation round (te-semver: 18 of 25 survived, 60 calls) is the cost driver. |

## 3. Ranked changes for MeshPool

Mark: [ENV] flag/env var that already exists, [SPEC] spec override JSON (`MH_SPECS_FILE` / `testkit/loop/candidates/*.json`, read by `overrides()` in `hrouter/specs.py`; keys `addon`, `addon_extra`, `drop`, `tools` per spec, spec.variant, spec.variant.language), [CODE] needs a code change (but is small and gateable by a new env var).

1. **Stop earlier: turn off the review and mutation gates by default and keep only the verify nudge.** [ENV: `MH_REVIEW=0`, `MH_MUTATION=0`; both already exist]
   - Where: `agent/core.py` `_turn` stop path; `core/review.py:enabled`; `_mutation_feedback`.
   - Evidence: review ran in 38/52 and had findings in 3; mutation ran in 39/52 and survivors in 4; each is an extra call and appends to the long context; te-semver reached 60 calls after "18 of 25 survived". None of the four winners has such a gate; Hermes has only the one-shot verify-on-stop nudge, which MeshPool already has.
   - Expected: te cost 7x -> about 2x of Codex; calls/task -3 to -5 in code routes; solves flat (V3's te advantage over V2 is 1 task, within noise); watch te and perf (V3's gains over V2: te +1, perf +2). Risk: low-medium (te 6/6 may drop to 5/6; re-run te only). A/B: dev suite, `MH_REVIEW=0 MH_MUTATION=0` against current. Cheapest A/B available.

2. **Replace V3 `code.build` / `code.debug` / `code.test` method text with the V2 short methods; keep V3's perf and algorithm variants only where they paid.** [SPEC: `MH_SPECS_FILE=testkit/loop/candidates/v2-methods.json`, already in the repo; layer an override that keeps `code.debug.perf`]
   - Where: `hrouter/specs.py` (`SPECS["code.build"].addon`, `code.debug`, `code.test`, `VARIANT_ADDON["code.build.algorithm"]`, `["code.build.feature"]`).
   - Evidence: V2 and V3 tie at 47/52 but V2 costs 2.5x less and runs 2.3x faster; V2's `mf` 7 vs V3's 5; `rf` $0.0024 vs $0.0268; Pi (no method at all) gets 48/52 at 8 calls, Codex only says "start as specific as possible".
   - Expected: -5 to -8 calls/task on build, refactor and test routes; solves flat to +1 on mf; risk: V3's perf gain (+2 over V2) disappears if `code.debug.perf` is also reverted, so keep that variant. Already A/B-ready with the candidate file.

3. **Hard turn budget with a "stop when green" rule.** [CODE, small: add an env var such as `MH_TURN_CAP=24`; or [ENV] `MH_BENCH_MAX_STEPS=24` as a crude proxy]
   - Where: `agent/core.py` `_turn` (`pace_deadline`/`PACE.WRAP` machinery already appends a "answer now, no more tools" message and sets `_no_tools`); `_v3_notes` (the "15 steps left" note fires at `max_steps-15`, which is too early if the cap is dropped to 24 via the env var, so set the cap in code and the note at cap-6).
   - Evidence: 11 V3 tasks at >=30 calls hold 81% of input tokens; 5 of them passed only at the 60 cap and would have passed earlier (the code already worked; the rest was extra checking); winners never exceed 30. A "once a check after the last edit exits 0 and no new edit, end" rule is the missing half of the 60-call cap.
   - Expected: input tokens/task 463k -> about 200k, calls 21 -> about 13; solves: risk to hard tasks that need more than 24 calls (te-bankers-round and alg-roman-strict passed at 60; alg-line-diff/V2 needed 47). Mitigate: wrap at 30, not 24, and let the final reply say what failed. Risk medium. A/B on the dev suite with the cap alone.

4. **Cure the byte-dump rabbit hole at its cause: fuzzy-tolerant edit, plus a family guard on byte inspection.** [CODE, `core/tools.py` edit and `agent/core.py` `_guarded_tool`]
   - Evidence: mf-rate-limit (27 od/cat -A calls of 64) and mf-js-recipe-units (56 of 60) both failed and are among the five V3 misses; every winner solved them in 8-17 calls. Hermes `patch` uses 9-strategy fuzzy matching, OpenCode has layered fuzzy replacers, Pi/Codex accept a patch grammar; MeshPool's `edit` is exact-match with read-first. When an exact match fails the model hunts for invisible characters instead of retrying.
   - Change: (a) when `old` is not found, retry with line-trimmed, whitespace-normalised and CRLF-normalised matching, and apply if unique; otherwise return the nearest 5 lines of the file with their exact bytes shown via `repr` in the error, so the model never needs `od`. (b) in `_guarded_tool`, count commands matching `od -c|xxd|hexdump|cat -A` in a turn; refuse the 3rd without an intervening file change (the numeric-shape guard misses these because the pipeline varies).
   - Expected: removes the two worst tails and the 7 "missing required argument 'path'" and 6 "identical to old" wasted calls; likely +1 to +2 solves on mf. Risk low. Testing: run only mf-rate-limit and mf-js-recipe-units, 2 tasks, a cheap test but not an env flag.

5. **Multi-edit and multi-file edit in one call.** [CODE, `core/tools.py` edit schema]
   - Evidence: Pi `edit(path, edits[])` ("Batch changes to one file in a single call"); Codex `apply_patch` covers several files and hunks per call; OpenCode `replaceAll`. V3 uses 100 `edit` plus 104 `write_file` calls (whole-file rewrites, partly because one-replacement-per-call is slow) and mf tasks take 31 calls against 8.
   - Change: accept `edits: [{old,new}]` on `edit` (keep `old`/`new` as sugar), and relax read-before-edit to "read or seen in error context or in this turn's output".
   - Expected: -2 to -4 calls on mf/rf; schema grows about 150 tokens; risk low (input leniency, like Pi's `prepareEditArguments`). Not an env flag; needs a new schema.

6. **Fix routing: send refactor tasks to `code.refactor`.** [CODE in `hrouter/route.py` or classifier; test by [ENV] forcing the spec]
   - Evidence: 4 of 6 `rf-*` tasks routed to `code.build` (global-to-instance, sync-to-async, dedupe-validation, split-invoice) and averaged about 40 calls; the 2 routed to `code.refactor` averaged 14 calls and passed. V2's rf average is 9.7 calls, $0.0024.
   - Expected: rf cost back toward $0.003/task; solves flat. Risk low. A/B by checking the route-fit script (`testkit/loop/routing_fit.py`) on the 6 rf tasks.

7. **Short stop-discipline and "do not re-check what a tool already confirmed" lines in the system prompt, replacing most of V3_RULES.** [SPEC via `addon_extra`, or edit `V3_RULES`]
   - Evidence, nearly verbatim from the winners: Codex "Do not waste tokens by re-reading files after calling `apply_patch`... The tool call will fail if it didn't work"; "start as specific as possible... then broader tests"; "Do not attempt to fix unrelated bugs"; Hermes "Do NOT re-verify internal file edits a tool already confirmed" and "Finishing the job: ... Keep working until you have actually exercised the code or produced the requested result". Add: "Write your check script once to a file; edit it and rerun it. Do not retype it in a heredoc." (rf-dedupe-validation: 14+ re-typed copies).
   - Expected: -1 to -3 calls; solves flat. Risk very low. A/B: `addon_extra` in a candidate JSON, cost near zero.

8. **Make batching real: put it in the tool descriptions and use the cheap batch tools.** [CODE: tool descriptions in `core/tools.py` `SCHEMAS`; or [SPEC] `tools`/`drop`]
   - Evidence: V3 used `bash_many` 26 times against V2's 79, and `bash` 655 times (12.6 per task). OpenCode's glob/grep descriptions say "It is always better to speculatively perform multiple searches as a batch" and Hermes explains the economics in its prompt. This is the one place where V3 has the pieces (parallel `read_file`/`grep`/`bash_many`, `MH_PARALLEL_TOOLS=1`) but does not use them. The OpenCode "one tool per message" line shows that batching is not required for 7 calls, so rank this below the stop and gate changes.
   - Expected: -1 to -2 calls/task; risk low; A/B by adding one sentence to the `read_file`, `grep`, `bash` descriptions (a small code edit) or via `addon_extra`.

9. **Result-stub dedup and cap on repeated tool output** (Hermes `tool_guardrails.py`: byte-identical repeated results become a reference stub after 512 chars). [CODE]
   - Evidence: V3 median input 84k, but od-loop runs resend large outputs 50 times; this lowers the cost of the tail rather than preventing it. Expected: -10% input tokens on affected tasks. Risk low. Lower priority than 3 and 4.

10. **Drop `todo_write` and the plan reminders for focused routes; keep for >= 3 named requirements.** [ENV `MH_PLAN=0` / SPEC `drop`]
    - Evidence: V3's own comment in `specs.py` says todo cost one or two calls against Pi, which has none; Codex's plan is "not for simple or single-step queries". `todo_write` was called 22 times and costs a call each plus the follow-through nudge. Expected: -0.5 to -1 call on build routes. Risk low. A/B: `MH_PLAN=0` plus `drop: ["todo_write"]` on `code.build`.

Safe to leave alone: the data route (cheapest in the field), the repo-map/`reproduce`/`data_preview` first-message injection (V3 has 3.7-5.4 calls on data and 14 on debug where Codex needs 7), the tool-schema size (not the lever), and prompt-cache behaviour (96% hit).

## 4. A/B plan

| Order | Test | How | Cost to run |
|---|---|---|---|
| 1 | Gates off | `MH_REVIEW=0 MH_MUTATION=0` | one env pair |
| 2 | V2 methods + V3 perf variant | `MH_SPECS_FILE=testkit/loop/candidates/v2-methods.json` (extend with `code.debug.perf`) | one JSON |
| 3 | Gates off + V2 methods + `MH_PLAN=0` | combine 1, 2, 10 | combination |
| 4 | Turn cap 30 with wrap | small code change, new env var | few lines |
| 5 | Byte-dump guard and fuzzy edit, on mf-rate-limit and mf-js-recipe-units only | code change | 2 tasks |
| 6 | Multi-edit schema, routing fix | code change | a day |

Expected combined effect of 1-4 and 10 (my estimate, not measured): about 12-14 calls/task, 170-220k input tokens/task, V2-like cost ($0.005-0.006/task) with 47-49/52. Beating Codex's 8.2 calls and 116k tokens needs 4, 5 and 7 as well.
