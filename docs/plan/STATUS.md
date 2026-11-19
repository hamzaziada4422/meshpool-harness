# Status: where the harness router stands against the goal

**Latest (V2 sprint, 19 Nov 2026):** see `docs/results/v2/V2_REPORT.md`, `docs/BENCHMARKS_V2.md` and `docs/HANDOFF.md`. Verdict: promising but unproven. Cheapest everywhere (significant), fastest on data analysis; not better on hard coding tasks (19/24 vs Codex 24/24).


Goal: on the same model, be at least as good as every frontier harness (Pi, Codex, OpenCode, Hermes, Claude Code) on
every benchmark, and cheaper and faster. Claims need repeats and intervals, not one run.

Updated: 2026-11-18, end of day 1.

## Measured (testkit/loop, graded by code)

**Run 3** (30 tasks × 3 repeats, chat/debug/build/hard). This is the version from before the lean code harnesses. The
95% intervals come from a paired bootstrap; quality is compared with McNemar's test.

| Model | Meshpool vs its old base | Meshpool vs Pi |
|---|---|---|
| DeepSeek V4.1 Flash | same quality; cost 0.52×; faster | same quality; **cheaper** ($0.0028 vs $0.0050); Pi slightly faster (8.6 vs 9.2 s) |
| GPT-6 Luna | same quality; cost 0.50× | same quality; **Pi cheaper** ($0.0004 vs $0.0010) and **faster** (8.1 vs 13.5 s) |
| Claude Haiku 5.5 | same quality; cost 0.55× | same quality; **cheaper** ($0.0017 vs $0.0100); Pi faster (7.4 vs 9.5 s) |
| Kimi K3 | not comparable: the Meshpool *team* budget ran out mid-run | same |

What the numbers say:
- We send 3–4× less input than Pi, but make 1–3 more model calls on code tasks and write more output. That is why
  Pi is faster everywhere and cheaper on GPT-6 Luna, where output tokens dominate the price.
- Every arm solves about 100%, so these suites cannot separate quality. SWE-bench Verified is running for that.

Changes since run 3, aimed at calls and output (not yet measured):
1. Lean code harnesses: about 46% fewer tool-schema tokens.
2. Debug, test, review and explain have no todo list.
3. The command the user named runs before the first call (`<reproduction>`).
4. Every failing command shows the failing project lines, counted as read, so a fix needs no `read_file` first.
5. Each edit gets an instant syntax and bug check.

## Not measured yet
- Codex, OpenCode and Hermes on the real endpoint. They are wired into the loop through the app's ACP integration
  and the metering proxy, but they have only been run against the mock, where Codex cannot run.
- SWE-bench Verified (routed vs base vs Pi): the first run is in progress.
- Research, documents, browser and terminal suites.

## Robustness fixed on the way (all found by testing, none guessed)
- Pi's replies were lost: `send()` returned "" to schedules, sub-agents and headless runs.
- Every chat in the sidebar showed "NaN d ago" because `app.js` had two functions named `ago()`.
- A refused model field (for example Kimi and `temperature`) is now removed and retried, and remembered per model.
- A spent quota or budget now stops at once with one clear message. Before, it retried for minutes.
- A masked or mangled API key now gives one clear message. Before, it failed five different cryptic ways.
- The skills list is loaded on demand, and the names-only list is capped at 60 (a real install had 584 skills).

## Built since (all measured offline only, by tests; none on a model yet)
- Sub-subtask harnesses: debug (syntax, test, exception, regression, perf) and build (cli, api, ui, function), each
  with its own method. Specs resolve from most specific to general (spec.variant.language down to spec).
- Data-driven specs (`app/defaults/specs.json`) and the harness optimizer (`testkit/loop/optimize.py`: propose, probe,
  compose, after Harness²). Edits are adopted only when the stats say they help; lessons go to `lessons.jsonl`.
- A review receipt after every turn that changes files: what changed (with git line counts), the command that
  checked it, or a plain "not verified". It costs no model tokens.
- Parallel tools: independent read-only calls in one message run together. Order is kept.
- Memory v2: relevant recall, per-project notes outside the project, a fenced and scrubbed `<memory-context>`.
- Data harness: the files' shape is read before the first call; DuckDB.
- Deep suite: 6 multi-file tasks with hidden tests (checked solvable with reference fixes).

## CI
All GitHub workflows are disabled (spend control). Local suite: 933 passed. The 47 failures are all environmental
on this Mac (no LibreOffice app, Chromium path, case-insensitive disk); CI installs those. Re-enable once, at the end.

## Next, in order
1. Measure the lean changes, ideally with routed vs Pi vs Codex vs OpenCode vs Hermes on the same model. This needs
   the team budget raised.
2. Read the SWE-bench results and fix whatever loses.
3. SolidLSP (MIT) symbol tools for build and refactor; a tree-sitter repo map; ast-grep codemods.
4. The Hermes memory port, then the dreaming harnesses.
5. Research, terminal and documents suites, so Auto routes on measured data everywhere (`routing_fit.py`).
