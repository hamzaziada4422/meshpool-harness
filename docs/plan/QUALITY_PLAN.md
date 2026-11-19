# Quality sprint plan (V3): solve more, stay cheaper

Goal: MeshPool solves more hard tasks than Codex, Pi, OpenCode and Hermes on DeepSeek V4.1 Flash, while staying at
least 10-20% cheaper than the cheapest of them (Codex: $0.0039 per attempted task on the hard V2 subset). Cost and
speed may rise against V2 ($0.0035) to buy quality; the ceiling is about $0.0035 x 1.0-1.1 per task.

Where we stand (V2, hard subset of 24): MeshPool 19/24, Codex 24/24, Pi 23, OpenCode 22, Hermes 22, V1 21.
Known causes (from V2 traces): build-from-spec tasks churn (71 bash calls, wrong answer on edge cases), 8 of 12 edits
failed, a quick wrong data answer (no data-quality checks), one misroute (fixed, unmeasured), one 600 s timeout.
Dev baseline in this session (V2 harness): v2-data-dupes-fx failed the same way (answered after 1 bash call).

Rules kept from the handoff: DeepSeek V4.1 Flash only; $25 cap ($14.98 left); 1 repetition; do not weaken tasks or
graders; tune only on dev tasks; report ties and regressions honestly.

## Work packages

Each package is done by a Sonnet sub-agent in its own files; the lead (Opus) verifies each with code (tests, build.py,
a dev-suite run) before it is kept. No package commits; the lead commits after verification.

### WP1. More dev tasks (testkit/loop/v2/t_dev2.py)
8 dev tasks cannot show a quality change. Add 16 (2 per category), harder, in a new file, validated by build.py
(fail as given, pass with reference). Eval tasks untouched.
Verify: build.py admits all, 0 rejected; `suites/v2_eval.json` byte-identical.

### WP2. Edit tool that does not fail (app/meshpool_harness/core/tools.py, edit)
- Strip read_file line-number prefixes from `old` (and `new`) when every line has one.
- No exact match: if exactly one match exists after normalising trailing whitespace / indentation / tabs vs spaces,
  apply it (re-indent `new` by the same offset) and say so in the result.
- Still no match: the error shows the closest region of the file (difflib, about 8 lines with line numbers) so the
  next try is right.
- Multiple matches: the error lists the line numbers of each.
Verify: new unit tests in app/tests/test_edit_fuzzy.py; existing tool tests green.

### WP3. Independent review before "done" (app/meshpool_harness/agent/core.py)
When a code-route turn is about to end with edits made (not plan mode), make one extra model call with a fresh,
small context: the user's request, `git diff` of the turn (capped ~12k chars, plus new files), and the last check
command with its exit code. The reviewer must reply `LGTM` or up to 5 concrete findings, each with an input and the
expected behaviour the request implies, so the agent can test it. Findings go back to the agent as one
`[Meshpool]` message: check each with code, fix the real ones, ignore what code shows is wrong. Once per request.
Usage counts toward the turn. Flag `MH_REVIEW` (default on for code.build, code.debug, code.refactor, code.test;
`0` turns it off). Reviewer errors never break the turn.
Verify: unit test with the mock LLM (reviewer called once, findings injected, LGTM ends the turn, off switch works).

### WP4. Methods that aim at correctness (app/meshpool_harness/hrouter/specs.py)
Replace "run ... once" with a requirement-driven method:
- code.build: list every rule in the request (tie-breaks, boundaries, empty/invalid input, error types and messages,
  performance limits); write ONE check script covering each rule plus a large-input timing case when speed or size is
  mentioned; rerun only that script after each change; delete it at the end unless tests were asked for.
- code.debug: after the fix, test the neighbouring boundary cases of the same bug class, not only the reported case.
- code.test: tests must kill mutants: assert exact values, each branch, each boundary (n-1, n, n+1), each error path
  and message; run them against the code.
- data: before answering, check and report duplicates, ID/key normalisation (case, whitespace, leading zeros),
  units and currencies, time zones and date formats, and missing values; answer only from the cleaned computation.
- ambiguous requests (build/debug variants): infer the intended behaviour from the codebase's existing conventions
  and tests before choosing; state the assumption in one line.
Verify: hrouter tests green; dev-suite A/B.

### WP5. Competitor arms in this container
Pi, Codex (npx codex-acp), OpenCode (npx opencode-ai acp) and Hermes (vendored) must run here through the metering
proxy. Verify: one dev task per arm completes with metered tokens.

### WP0. Research: what open-source agents do that raises solve rate (runs first, in parallel with WP1, WP2, WP5)
Survey with sources: SWE-agent (agent-computer interface, linting edits), mini-swe-agent, OpenHands (critic model),
Agentless (localise, repair, generated reproduction tests, patch selection), AutoCodeRover, Moatless, CodeMonkeys and
other test-time-compute work (sample several fixes, select by tests), SWE-Search (MCTS), Trae Agent, Aider (edit
formats, repo map, architect/editor split), Codex CLI (apply_patch), Claude Code, Refact, open LSP tooling (SolidLSP,
multilspy), ast-grep, Hypothesis, mutation tools (mutmut, cosmic-ray). Output: a ranked list of techniques by expected
solve-rate gain per dollar on a cheap model, each with the evidence and how it fits our harness. WP3, WP4 and WP6-WP8
are adjusted to what it finds.

### Out of the box (candidates, measured like everything else)
- WP6. Differential testing with an oracle: for build/algorithmic/performance work, the agent writes a brute-force
  reference that follows the spec literally, then compares the real implementation against it on many random small
  inputs (and times a large one). Wrong edge cases show up as concrete counterexamples. For data questions: compute
  the answer two independent ways (pandas and DuckDB SQL) and answer only when they agree.
- WP7. Zero-token mutation feedback for test-writing tasks: after the agent writes tests, the harness mutates the
  code under test (flip comparisons, off-by-one, swap operators, drop branches), runs the tests, and tells the agent
  which mutants survived, with the line. Costs no model tokens until there is something to fix.
- WP8. Adaptive second attempt: only when the verification is weak (review findings unresolved, final check failing,
  or no check run), run a second independent attempt from a clean copy in a git worktree and pick the candidate that
  passes more of the agent's own checks plus the other attempt's checks. Cheap model, so a second sample on the hard
  tail costs little; easy tasks never pay for it.
- WP9. Hidden-requirement mining: before coding, a zero-token pass extracts every number, quoted string, error type
  and "must/never/only/at most" clause from the request into the checklist the agent must tick off.

## Measurement

1. Dev A/B (24 dev tasks): V2 harness vs V3 candidate, 1 rep each (about $0.15 per arm). Keep a change only if it
   solves more without more than ~25% cost.
2. Final: full 52-task V2 eval, 6 arms (routed, v1, pi, codex, opencode, hermes), parallel 4 (4-core box), about
   $2.5-3. Then DABench 24. Report with report_v2.py: solves, McNemar, paired cost/time ratios.
3. Success: more solved than every competitor (or tied at the top) and cost per attempted task at most 0.9x Codex.
