# ws20-cost-bench: plan

Items: K-01, K-02, K-03, K-04, X-02, X-08, X-09, D-02, S-12, P-01, H-10.
Code base: 0.0.12 (branch start c189e04). Model for every arm: gpt-6-luna (Responses-style reasoning model;
$0.10 in, $0.01 cached in, $0.50 out per 1M tokens). Second model, if budget allows: deepseek-v4.1-flash.

## What was checked on 0.0.12 before planning

Smoke run, same prompt ("create hello.py that prints hello, then run it"), same model, same metering proxy:

| Arm | Calls | Input tokens | Fixed prompt per call |
|---|---|---|---|
| Pi 0.73.1 (`pi -p`) | 3 | 3.2k | ~1.0k |
| Codex 0.171.0 (`codex exec`) | 2 | 20.0k | ~9.9k |
| Meshpool CLI 0.0.12 (`meshpool -p --auto`) | 7 (4 agent + 3 independent check) | 41.0k | ~8.1k |

Offline size of what the agent sends before the user types (no user MCP servers): Code profile 7.7k chars of
system prompt + 35 tool schemas (23.9k chars); Work profile 12.2k chars + 36 schemas (25.2k chars). The 15
Meshpool Office tools (12k chars, about 3k tokens) ride on every Code request, including the CLI.

## Root causes and fixes

### X-02, K-02: CLI default is the expensive profile (reproduces)
- Cause: `agent/tui.py:1337` builds `Agent(wd, ...)` and never routes, so `Agent.route` stays `None` and
  `hrouter/profiles.py:settings_of(None)` returns `FULL` (plan auto, review, mutation, Meshpool prompt and tools).
  The app routes only when the session is created with `task` (`ui/agent_api.py:1273`), so an app chat created
  without it (the benchmark kit, the API) runs FULL too. The shipped default (`defaults/profiles.json`) is `codex`.
- Fix (root cause, every entry point): `Agent.send` routes the first message when no route is set
  (`hrouter.route(text, kind, workdir)` + `set_route`), so CLI, ACP server, API and app all get the configured
  default profile. `MH_HARNESS_PROFILE` still forces one.
- Edit reliability: find from the Forth baseline transcripts why edits fail on the first try (meter log + agent
  trace), fix the cause in the edit/apply_patch tool or its description, and add a regression test with the
  failing edit shape.
- Tests: `tests/test_ws20_cli_route.py`: a CLI-style headless Agent with no route gets the default profile's
  settings (`engine_style == "codex"`, `review False`) after its first message; fails on 0.0.12.

### K-01, K-03: office tasks cost 2.3-8.6x Codex and take minutes (to confirm on the baseline)
- Causes in the code: `WORK_SYSTEM` (`agent/core.py:202`) orders the agent to use the office engines
  (`office_task`, `make_deck`, `make_spreadsheet`: nested engine processes with their own long prompts, model
  reviews and rebuilds) and to run `check_deck`/`check_workbook`/`check_document` on every file, and forbids
  python-pptx/openpyxl; the independent grader (`agent/grader.py`) runs a second model loop (up to 150k tokens)
  on every "build" request, even when the result is already proven by a passing command.
- Fix (decided per task from the baseline numbers): the direct path is the default (python with
  openpyxl / python-docx / python-pptx / reportlab through bash, as Codex does), the engines are used only when the
  user asks for a designed deck or a model the engines exist for; cheap local checks (open the file, recalc,
  count charts, compare numbers to the source) instead of model reviews; the independent grader is skipped when
  local evidence already proves the result (tests or a check command passed after the last change, office file
  verified locally) and never retries a failing reviewer call more than once.
- Tests: prompt/route unit tests that fail on 0.0.12 (Work prompt does not forbid the direct path; office task
  does not route to an engine by default; grader skipped when evidence passes), plus the benchmark itself.

### S-12, P-01, H-10: fixed per-request overhead and too many tools
- Causes: every Meshpool MCP tool is always sent in full (`mcp_assist.LazyTools.split` treats `mcp__meshpool_*` as
  always shown; deferral starts only past 60 *other* tools); the office tools ride on every Code request; no cap
  on the number of tools (providers reject more than 128).
- Fix: one deferral rule for all non-core tools: core tools in full; Meshpool's own office tools only when the
  chat needs office files (capability detection, already used by Codex style); everything else behind the
  `mcp_find_tools` index once the set passes a small threshold; a hard cap so a request never carries more than
  128 tools or more than a token budget of schemas. Stable order so the prompt prefix stays cache-friendly.
- Tests: `tests/test_ws20_tool_budget.py`: Code chat without office need sends no office schemas; 200 MCP tools
  never produce more than 128 tools; schema bytes under a budget; deferred tool callable after lookup.

### X-08: PDF report grader does not require a chart
- Fix in the new kit: the PDF-report grader passes only when the PDF has a chart, vector (path drawing operators
  over the plot area) or raster (an embedded image), and its text carries the CSV's totals.
- Test: `testkit/bench3/tests/test_graders.py::test_pdf_report_requires_chart` (PDF without chart fails, with
  vector chart passes, with raster chart passes).

### X-09: graders modify the workspace and miscount tests
- Causes: `testkit/loop/swe_local.py:grade` restores test files in place (`git checkout HEAD -- tests`) and writes
  the patch into the workspace; `testkit/loop/bench.py:grade` writes hidden tests into the workspace;
  `testkit/practical/practical.py` graders delete files, write `.recalc`/`.render` folders and run commands
  inside the workspace. No shared parser for test results.
- Fix: `testkit/gradekit.py`: `scratch_copy(workdir)` (grade a copy; the workspace is never touched),
  `count_tests(output)` that sums every result line (pytest summary, every `cargo test` "test result:" line
  including unit, integration and doc-tests, `node --test`, unittest). All graders use it. Codex/Pi call counts
  come from the meter, never from their event streams.
- Tests: `testkit/bench3/tests/test_gradekit.py` (tree hash identical before and after grading every grader;
  cargo output with three result lines counts 22 passed; pytest with passed+failed+errors), and the bench.py /
  swe_local graders run on a copy.

### D-02: office quality not measured on a public suite
- Fix: an OfficeBench-style adapter and a SpreadsheetBench adapter (LibreOffice is installed here) in
  `testkit/bench3/public/`, run on a small public subset, results in `docs/results/v4/`. The 7 office tasks of
  the three-way kit are graded by file content, not by a model.

### K-04: public claims
- Re-measure with the three-way kit, then rewrite `docs/BENCHMARKS.md`, mark `docs/BENCHMARKS_V2.md` superseded,
  and fix the README rows (Work "all 7 passed", "42 s", Auto "best quality then lowest cost", "Any
  OpenAI-compatible endpoint works") to match measurements, including where Meshpool still loses.

## The three-way kit (K-01..K-04 measurement)

`testkit/bench3/run.py`: one command runs every arm on every task through one metering proxy.
- Meter (`testkit/bench3/meter.py`): forwards to the upstream; records per call task, arm, rep, path, model,
  HTTP status, seconds, input, cached, output, reasoning tokens; a request header or path names the arm so nested
  engine calls of the same run are attributed to it (engines inherit `MESHPOOL_BASE_URL`).
- Arms: `mp-work` / `mp-code` (the app's agent through the real UI server API, `kind` cowork/code), `mp-cli`
  (`meshpool -p --auto`), `codex` (`codex exec`, custom provider, responses wire), `pi` (`pi -p`, custom provider,
  openai-responses). Each arm gets a fresh copy of the task folder and a private home/config.
- Tasks: 7 office (xlsx edit in place, docx tracked changes, invoices with mixed currency to xlsx, docx mail-merge
  letters, xlsx live-formula budget, pptx native chart deck from quarterly.csv, PDF report from sales.csv with a
  chart) and 5 coding (Exercism python zipper, sgf-parsing, forth; rust react, macros; full test suites including
  ignored tests).
- 3 reps per task per arm; report mean and spread of cost, calls, tokens, seconds, pass rate; one price table.
- Results: `docs/results/v4/` (baseline first, then after each reduction), no client names.

## Order
1. Kit + graders (Sonnet, parallel: graders/tasks vs meter/arms/runner).
2. Baseline on the 0.0.12 code (a worktree at c189e04) and save it.
3. Fixes (Sonnet), each with its regression test; re-measure after each group.
4. Docs (K-04), ledger, full suite.
