# Real-world benchmark (PARTIAL: swe done)

6 tasks x 1 run(s) per arm; same model (deepseek-v4.1-flash) through llm.meshpool.ai, same metering proxy (testkit/loop/small_meter.py), concurrency 3, arms interleaved task by task in the same hour. Cost = metered tokens at testkit/bench3/prices.json ({'in': 0.33, 'cached': 0.0066, 'out': 1.32}). Versions: {'codex': 'codex-cli 0.171.0', 'pi': '1.5.2', 'meshpool_app': '/tmp/claude-0/rw/mp-head/app', 'meshpool_git': 'bf04014694c0b2efea492f14dc37724c5dc509b8', 'meshpool_version': 'meshpool 0.0.12', 'python': '/usr/local/bin/python3'}.

## Suite (testkit/realworld/)

| Category | Tasks | Grader |
|---|---|---|
| swe (6) | SWE-bench Verified SymPy issues 24539, 22914, 23824, 23534, 24213, 23262 (the fastest six in earlier runs) | official FAIL_TO_PASS + PASS_TO_PASS tests on a scratch copy (testkit/loop/swe_local.py) |
| office (5) | bench3 xlsx_budget, pdf_report, docx_tracked, pptx_chart, invoices_fx | bench3's strict file-content graders (LibreOffice recalc, live-formula test, native chart values, tracked-change XML) |
| everyday (9) | pdf_facts (15-page PDF -> 5 bullet facts), csv_sales (3 numeric answers), fix_test (multi-file Python repo), flask_feature (hidden tests), bash_rename (temp tree), md_to_docx (template fields), js_refactor (node tests + structure), three_files (exact values from 3 files), csv_clean (checksum of normalised rows) | deterministic; inputs from make_fixtures.py (fixed seeds); hidden tests written only into a scratch copy |

Every grader leaves the agent's workspace untouched (gradekit.scratch_copy / temp dirs); testkit/realworld/tests/test_graders.py
checks that each everyday grader fails on the untouched inputs and passes on a reference solution.

Arms: **Meshpool** = `python -m meshpool_harness -p ... --auto` from a fresh worktree of HEAD (bf040146), private home, no MH_*
switch except MH_MODEL. **Codex CLI** 0.171.0 = `codex exec --json`, stdin closed, Responses wire. **Pi** = bench.py's pi arm
(Pi 1.5.2 through Meshpool's Pi integration, pi_child.py). Same per-task timeout (swe/office 900 s, everyday 600 s).

## Headline

| Arm | Solved | $/task | $/solved | Calls/task | Input tok/task | Cached tok/task | Output tok/task | s/task | Errors | Timeouts |
|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool | 6/6 | $0.0160 | $0.0160 | 30.8 | 503,000 | 483,712 | 4,866 | 251 | 1 | 1 |
| Codex CLI | 6/6 | $0.0092 | $0.0092 | 14.5 | 205,756 | 190,379 | 2,181 | 52 | 0 | 0 |
| Pi | 6/6 | $0.0194 | $0.0194 | 9.3 | 259,832 | 211,456 | 1,544 | 40 | 0 | 0 |

## Per category

| Category | Meshpool solved | Meshpool $/solved | Codex CLI solved | Codex CLI $/solved | Pi solved | Pi $/solved |
|---|---|---|---|---|---|---|
| swe | 6/6 | $0.0160 | 6/6 | $0.0092 | 6/6 | $0.0194 |
| office | - | - | - | - | - | - |
| everyday | - | - | - | - | - | - |

## Paired ratios (Meshpool / other, same task and run; 95% bootstrap over task-runs)

| vs | pairs | Solves (MP vs them) | Cost ratio | $/solved ratio | Calls ratio | Time ratio |
|---|---|---|---|---|---|---|
| Codex CLI | 6 | 6 vs 6 (only MP 0, only them 0) | 1.74x (0.78-3.55) | 1.74x (0.78-3.55) | 2.13x (0.86-4.67) | 4.85x (1.35-16.54) |
| Pi | 6 | 6 vs 6 (only MP 0, only them 0) | 0.82x (0.41-1.63) | 0.82x (0.41-1.63) | 3.30x (1.30-5.91) | 6.26x (2.07-14.21) |

- swe: vs Codex CLI cost 1.74x (0.78-3.55), $/solved 1.74x (0.78-3.55); vs Pi cost 0.82x (0.41-1.63), $/solved 0.82x (0.41-1.63)

## Where Meshpool loses most

Ranked by solves lost against the better of Codex/Pi, then by extra $ per run against the cheaper of them.

| Task | Cat | MP solved | MP $/run | cheaper other $/run | Codex (solved, $/run) | Pi (solved, $/run) | Reason (from traces) |
|---|---|---|---|---|---|---|---|
| sympy__sympy-23534 | swe | 1/1 | $0.0359 | $0.0063 | 1, $0.0063 | 1, $0.0124 | ok |
| sympy__sympy-22914 | swe | 1/1 | $0.0136 | $0.0048 | 1, $0.0048 | 1, $0.0321 | ok |
| sympy__sympy-23262 | swe | 1/1 | $0.0222 | $0.0140 | 1, $0.0140 | 1, $0.0141 | ok |
| sympy__sympy-23824 | swe | 1/1 | $0.0127 | $0.0064 | 1, $0.0064 | 1, $0.0322 | ok |
| sympy__sympy-24539 | swe | 1/1 | $0.0059 | $0.0059 | 1, $0.0059 | 1, $0.0148 | ok |

## Every run

| Task | Cat | Rep | Arm | ok | $ | calls | s | detail |
|---|---|---|---|---|---|---|---|---|
| sympy__sympy-22914 | swe | 1 | mp | Y | 0.0136 | 18 | 143 | ok |
| sympy__sympy-22914 | swe | 1 | codex | Y | 0.0048 | 6 | 20 | ok |
| sympy__sympy-22914 | swe | 1 | pi | Y | 0.0321 | 10 | 58 | ok |
| sympy__sympy-23262 | swe | 1 | mp | Y | 0.0222 | 54 | 249 | ok |
| sympy__sympy-23262 | swe | 1 | codex | Y | 0.0140 | 27 | 112 | ok |
| sympy__sympy-23262 | swe | 1 | pi | Y | 0.0141 | 11 | 43 | ok |
| sympy__sympy-23534 | swe | 1 | mp | Y | 0.0359 | 78 | 900 | timeout after 900s |
| sympy__sympy-23534 | swe | 1 | codex | Y | 0.0063 | 9 | 22 | ok |
| sympy__sympy-23534 | swe | 1 | pi | Y | 0.0124 | 8 | 32 | ok |
| sympy__sympy-23824 | swe | 1 | mp | Y | 0.0127 | 16 | 83 | ok |
| sympy__sympy-23824 | swe | 1 | codex | Y | 0.0064 | 9 | 34 | ok |
| sympy__sympy-23824 | swe | 1 | pi | Y | 0.0322 | 8 | 40 | ok |
| sympy__sympy-24213 | swe | 1 | mp | Y | 0.0055 | 10 | 46 | ok |
| sympy__sympy-24213 | swe | 1 | codex | Y | 0.0178 | 27 | 100 | ok |
| sympy__sympy-24213 | swe | 1 | pi | Y | 0.0108 | 6 | 23 | ok |
| sympy__sympy-24539 | swe | 1 | mp | Y | 0.0059 | 9 | 82 | ok |
| sympy__sympy-24539 | swe | 1 | codex | Y | 0.0059 | 9 | 22 | ok |
| sympy__sympy-24539 | swe | 1 | pi | Y | 0.0148 | 13 | 45 | ok |

VERDICT: FAIL - Meshpool $/solved $0.0160 = 1.74x Codex ($0.0092) and 0.82x Pi ($0.0194) [need <= 0.80x both]; solved 6 vs Codex 6 / Pi 6 [need >= each] (PARTIAL: not every category/run is in yet)
