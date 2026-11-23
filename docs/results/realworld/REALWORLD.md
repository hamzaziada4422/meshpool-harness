# Real-world benchmark (PARTIAL: run 1 of every category done)

20 tasks x 1 run(s) per arm; same model (deepseek-v4.1-flash) through llm.meshpool.ai, same metering proxy (testkit/loop/small_meter.py), concurrency 3, arms interleaved task by task in the same hour. Cost = metered tokens at testkit/bench3/prices.json ({'in': 0.33, 'cached': 0.0066, 'out': 1.32}). Versions: {'codex': 'codex-cli 0.171.0', 'pi': '1.5.2', 'meshpool_app': '/tmp/claude-0/rw/mp-head/app', 'meshpool_git': 'bf04014694c0b2efea492f14dc37724c5dc509b8', 'meshpool_version': 'meshpool 0.0.12', 'python': '/usr/local/bin/python3'}.

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
| Meshpool | 17/20 | $0.0379 | $0.0446 | 44.3 | 488,580 | 447,002 | 16,096 | 165 | 2 | 2 |
| Codex CLI | 20/20 | $0.0084 | $0.0084 | 10.4 | 141,177 | 128,051 | 2,439 | 36 | 0 | 0 |
| Pi | 19/20 | $0.0201 | $0.0212 | 12.1 | 278,848 | 236,352 | 3,457 | 43 | 0 | 0 |

## Per category

| Category | Meshpool solved | Meshpool $/solved | Codex CLI solved | Codex CLI $/solved | Pi solved | Pi $/solved |
|---|---|---|---|---|---|---|
| swe | 6/6 | $0.0160 | 6/6 | $0.0092 | 6/6 | $0.0194 |
| office | 3/5 | $0.2023 | 5/5 | $0.0129 | 4/5 | $0.0408 |
| everyday | 8/9 | $0.0069 | 9/9 | $0.0053 | 9/9 | $0.0137 |

## Paired ratios (Meshpool / other, same task and run; 95% bootstrap over task-runs)

| vs | pairs | Solves (MP vs them) | Cost ratio | $/solved ratio | Calls ratio | Time ratio |
|---|---|---|---|---|---|---|
| Codex CLI | 20 | 17 vs 20 (only MP 0, only them 3) | 4.52x (1.39-8.66) | 5.31x (1.60-10.26) | 4.24x (1.67-7.73) | 4.58x (2.16-8.01) |
| Pi | 20 | 17 vs 19 (only MP 0, only them 2) | 1.88x (0.57-4.30) | 2.10x (0.62-4.81) | 3.66x (1.52-7.74) | 3.85x (1.69-7.16) |

- swe: vs Codex CLI cost 1.74x (0.78-3.55), $/solved 1.74x (0.78-3.55); vs Pi cost 0.82x (0.41-1.63), $/solved 0.82x (0.41-1.63)

- office: vs Codex CLI cost 9.40x (2.28-15.66), $/solved 15.67x (3.73-53.41); vs Pi cost 3.72x (0.73-11.13), $/solved 4.96x (1.09-15.20)

- everyday: vs Codex CLI cost 1.15x (0.75-1.63), $/solved 1.30x (0.89-1.77); vs Pi cost 0.45x (0.27-0.71), $/solved 0.51x (0.30-0.81)

## Where Meshpool loses most

Ranked by solves lost against the better of Codex/Pi, then by extra $ per run against the cheaper of them.

| Task | Cat | MP solved | MP $/run | cheaper other $/run | Codex (solved, $/run) | Pi (solved, $/run) | Reason (from traces) |
|---|---|---|---|---|---|---|---|
| xlsx_budget | office | 0/1 | $0.0357 | $0.0079 | 1, $0.0079 | 1, $0.0307 | FAILED: Budget formulas reference the Assumptions sheet (>=5 cells) (0 cells) [6/7 checks passed] |
| invoices_fx | office | 0/1 | $0.0258 | $0.0062 | 1, $0.0062 | 0, $0.0308 | FAILED: one row per invoice line (no more than the 8 lines plus per-invoice subtotals) (19 rows mention invoice ids) [3/4 checks passed] |
| md_to_docx | everyday | 0/1 | $0.0030 | $0.0073 | 1, $0.0073 | 1, $0.0122 | FAILED: start_date filled (2026-02-09); end_date filled (2026-05-29) [10/12 checks passed] |
| pdf_report | office | 1/1 | $0.4154 | $0.0190 | 1, $0.0216 | 1, $0.0190 | all 4 checks passed |
| pptx_chart | office | 1/1 | $0.1265 | $0.0183 | 1, $0.0183 | 1, $0.0645 | all 4 checks passed |

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
| docx_tracked | office | 1 | mp | Y | 0.0035 | 8 | 22 | all 7 checks passed |
| docx_tracked | office | 1 | codex | Y | 0.0106 | 12 | 29 | all 7 checks passed |
| docx_tracked | office | 1 | pi | Y | 0.0181 | 19 | 49 | all 7 checks passed |
| invoices_fx | office | 1 | mp | n | 0.0258 | 32 | 96 | FAILED: one row per invoice line (no more than the 8 lines plus per-invoice subtotals) (19 |
| invoices_fx | office | 1 | codex | Y | 0.0062 | 8 | 21 | all 4 checks passed |
| invoices_fx | office | 1 | pi | n | 0.0308 | 22 | 86 | FAILED: every line has invoice, customer, currency, item, qty, unit price, amount and USD  |
| pdf_report | office | 1 | mp | Y | 0.4154 | 418 | 900 | still looping when the runner process was killed (>=15 min); stopped by hand, counted as a |
| pdf_report | office | 1 | codex | Y | 0.0216 | 25 | 102 | all 4 checks passed |
| pdf_report | office | 1 | pi | Y | 0.0190 | 14 | 54 | all 4 checks passed |
| pptx_chart | office | 1 | mp | Y | 0.1265 | 128 | 372 | all 4 checks passed |
| pptx_chart | office | 1 | codex | Y | 0.0183 | 16 | 76 | all 4 checks passed |
| pptx_chart | office | 1 | pi | Y | 0.0645 | 53 | 161 | all 4 checks passed |
| xlsx_budget | office | 1 | mp | n | 0.0357 | 42 | 155 | FAILED: Budget formulas reference the Assumptions sheet (>=5 cells) (0 cells) [6/7 checks  |
| xlsx_budget | office | 1 | codex | Y | 0.0079 | 7 | 57 | all 7 checks passed |
| xlsx_budget | office | 1 | pi | Y | 0.0307 | 20 | 83 | all 7 checks passed |
| bash_rename | everyday | 1 | mp | Y | 0.0046 | 10 | 22 | all 3 checks passed |
| bash_rename | everyday | 1 | codex | Y | 0.0036 | 7 | 18 | all 3 checks passed |
| bash_rename | everyday | 1 | pi | Y | 0.0167 | 11 | 32 | all 3 checks passed |
| csv_clean | everyday | 1 | mp | Y | 0.0073 | 7 | 46 | all 2 checks passed |
| csv_clean | everyday | 1 | codex | Y | 0.0068 | 5 | 13 | all 2 checks passed |
| csv_clean | everyday | 1 | pi | Y | 0.0111 | 4 | 12 | all 2 checks passed |
| csv_sales | everyday | 1 | mp | Y | 0.0025 | 5 | 34 | all 3 checks passed |
| csv_sales | everyday | 1 | codex | Y | 0.0044 | 4 | 8 | all 3 checks passed |
| csv_sales | everyday | 1 | pi | Y | 0.0104 | 5 | 15 | all 3 checks passed |
| fix_test | everyday | 1 | mp | Y | 0.0044 | 5 | 12 | all 1 checks passed |
| fix_test | everyday | 1 | codex | Y | 0.0045 | 4 | 9 | all 1 checks passed |
| fix_test | everyday | 1 | pi | Y | 0.0097 | 5 | 13 | all 1 checks passed |
| flask_feature | everyday | 1 | mp | Y | 0.0178 | 18 | 44 | all 1 checks passed |
| flask_feature | everyday | 1 | codex | Y | 0.0079 | 8 | 23 | all 1 checks passed |
| flask_feature | everyday | 1 | pi | Y | 0.0146 | 9 | 30 | all 1 checks passed |
| js_refactor | everyday | 1 | mp | Y | 0.0047 | 7 | 24 | all 5 checks passed |
| js_refactor | everyday | 1 | codex | Y | 0.0022 | 4 | 10 | all 5 checks passed |
| js_refactor | everyday | 1 | pi | Y | 0.0190 | 6 | 24 | all 5 checks passed |
| md_to_docx | everyday | 1 | mp | n | 0.0030 | 5 | 12 | FAILED: start_date filled (2026-02-09); end_date filled (2026-05-29) [10/12 checks passed] |
| md_to_docx | everyday | 1 | codex | Y | 0.0073 | 9 | 20 | all 12 checks passed |
| md_to_docx | everyday | 1 | pi | Y | 0.0122 | 9 | 29 | all 12 checks passed |
| pdf_facts | everyday | 1 | mp | Y | 0.0082 | 11 | 41 | all 7 checks passed |
| pdf_facts | everyday | 1 | codex | Y | 0.0062 | 7 | 13 | all 7 checks passed |
| pdf_facts | everyday | 1 | pi | Y | 0.0121 | 6 | 14 | all 7 checks passed |
| three_files | everyday | 1 | mp | Y | 0.0030 | 5 | 21 | all 3 checks passed |
| three_files | everyday | 1 | codex | Y | 0.0053 | 6 | 14 | all 3 checks passed |
| three_files | everyday | 1 | pi | Y | 0.0178 | 3 | 15 | all 3 checks passed |

VERDICT: FAIL - Meshpool $/solved $0.0446 = 5.31x Codex ($0.0084) and 2.10x Pi ($0.0212) [need <= 0.80x both]; solved 17 vs Codex 20 / Pi 19 [need >= each] (PARTIAL: not every category/run is in yet)
