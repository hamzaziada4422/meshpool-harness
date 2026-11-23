# Real-world benchmark: Meshpool vs Codex CLI vs Pi

20 tasks x 2 run(s) per arm; same model (deepseek-v4.1-flash) through llm.meshpool.ai, same metering proxy (testkit/loop/small_meter.py), concurrency 3, arms interleaved task by task in the same hour. Cost = metered tokens at testkit/bench3/prices.json ({'in': 0.33, 'cached': 0.0066, 'out': 1.32}). Versions: {'codex': 'codex-cli 0.171.0', 'pi': '1.5.2', 'meshpool_app': '/tmp/claude-0/rw/mp-head/app', 'meshpool_git': 'bf04014694c0b2efea492f14dc37724c5dc509b8', 'meshpool_version': 'meshpool 0.0.12', 'python': '/usr/local/bin/python3'}.

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

Run notes: 2 runs per arm, 40 task-runs each, all 120 complete (no budget cut, no HTTP 429). Metered spend $2.50 at
prices.json (incl. a $0.02 smoke run) of the $3 cap. Two grader bugs found during run 1 were fixed and run 1 was re-graded
from the kept workspaces (testkit/realworld/regrade.py; rows carry `regraded_from`): pdf_facts flagged "succeeded Johan
Berg" as naming the wrong CEO (hit all three arms), and csv_sales q1 is an exact half-cent tie (511253.075), so .07 and
.08 are both accepted. The runner process died once during office run 1; Meshpool's pdf_report run 1 was still looping
(418 calls) and was stopped by hand, recorded as a 900 s timeout with its metered cost (a lower bound). Raw data:
results.jsonl, meter.jsonl, run.json next to this file; traces in /tmp/claude-0/rw/run/logs.

## Headline

| Arm | Solved | $/task | $/solved | Calls/task | Input tok/task | Cached tok/task | Output tok/task | s/task | Errors | Timeouts |
|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool | 34/40 | $0.0361 | $0.0424 | 39.9 | 457,072 | 415,616 | 14,871 | 152 | 3 | 3 |
| Codex CLI | 40/40 | $0.0079 | $0.0079 | 9.6 | 123,888 | 111,091 | 2,223 | 31 | 0 | 0 |
| Pi | 38/40 | $0.0181 | $0.0191 | 11.8 | 266,351 | 228,698 | 3,179 | 42 | 0 | 0 |

## Per category

| Category | Meshpool solved | Meshpool $/solved | Codex CLI solved | Codex CLI $/solved | Pi solved | Pi $/solved |
|---|---|---|---|---|---|---|
| swe | 12/12 | $0.0200 | 12/12 | $0.0082 | 12/12 | $0.0165 |
| office | 6/10 | $0.1821 | 10/10 | $0.0115 | 8/10 | $0.0374 |
| everyday | 16/18 | $0.0068 | 18/18 | $0.0057 | 18/18 | $0.0126 |

## Paired ratios (Meshpool / other, same task and run; 95% bootstrap over task-runs)

| vs | pairs | Solves (MP vs them) | Cost ratio | $/solved ratio | Calls ratio | Time ratio |
|---|---|---|---|---|---|---|
| Codex CLI | 40 | 34 vs 40 (only MP 0, only them 6) | 4.57x (1.97-7.77) | 5.38x (2.27-9.60) | 4.16x (2.16-6.72) | 4.93x (2.87-7.49) |
| Pi | 40 | 34 vs 38 (only MP 1, only them 5) | 1.99x (0.83-3.66) | 2.22x (0.90-4.25) | 3.40x (1.77-5.80) | 3.62x (2.08-5.59) |

- swe: vs Codex CLI cost 2.43x (1.26-4.00), $/solved 2.43x (1.26-4.00); vs Pi cost 1.21x (0.62-2.06), $/solved 1.21x (0.62-2.06)

- office: vs Codex CLI cost 9.53x (2.92-17.28), $/solved 15.88x (4.63-44.22); vs Pi cost 3.65x (1.00-8.07), $/solved 4.87x (1.13-14.41)

- everyday: vs Codex CLI cost 1.07x (0.72-1.47), $/solved 1.20x (0.85-1.59); vs Pi cost 0.48x (0.32-0.67), $/solved 0.54x (0.36-0.76)

## Where Meshpool loses most

Ranked by solves lost against the better of Codex/Pi, then by extra $ per run against the cheaper of them.

| Task | Cat | MP solved | MP $/run | cheaper other $/run | Codex (solved, $/run) | Pi (solved, $/run) | Reason (from traces) |
|---|---|---|---|---|---|---|---|
| md_to_docx | everyday | 0/2 | $0.0026 | $0.0081 | 2, $0.0081 | 2, $0.0120 | Cheap but wrong: rewrote the brief's ISO dates as '9 February 2026' and appended '(excl. VAT)' instead of copying the given values. |
| pdf_report | office | 1/2 | $0.3838 | $0.0148 | 2, $0.0148 | 2, $0.0193 | Verification loop, both runs: run 1 re-read make_report.py with identical `sed -n` ranges 130+ times (418 calls, stopped by hand); run 2 re-checked the PDF with pdftotext/pdfinfo/python -c 100+ times and timed out at 900 s without a chart (283 calls). No repeated-action guard or step budget; $0.77 of Meshpool's $1.44 total. |
| pptx_chart | office | 1/2 | $0.0907 | $0.0185 | 2, $0.0185 | 2, $0.0589 | Run 1: built the deck one shape at a time (63 edit_office_file calls + 7 check_deck, 128 calls vs Codex 16). Run 2: left the deck in 'Meshpool outputs/<stamp>/deck.pptx' instead of ./deck.pptx, so the requested file is missing. |
| xlsx_budget | office | 1/2 | $0.0536 | $0.0066 | 2, $0.0066 | 2, $0.0275 | Office engine builds a multi-sheet 'model' through nested engine calls (42 and 83 calls vs Codex 7-8). Run 1 used named ranges instead of Assumptions! references and failed that strict check (its live-update check passed: partly grader strictness); run 2 passed at 13x Codex's cost. |
| invoices_fx | office | 1/2 | $0.0147 | $0.0058 | 2, $0.0058 | 0, $0.0289 | Over-built workbook (Cover/Inputs/Invoices/Calc/Outputs/Checks) that repeats invoice ids on extra sheets, failing 'one row per invoice line'; Codex wrote one sheet in 8 calls. |

## Every run

| Task | Cat | Rep | Arm | ok | $ | calls | s | detail |
|---|---|---|---|---|---|---|---|---|
| sympy__sympy-22914 | swe | 1 | mp | Y | 0.0136 | 18 | 143 | ok |
| sympy__sympy-22914 | swe | 1 | codex | Y | 0.0048 | 6 | 20 | ok |
| sympy__sympy-22914 | swe | 1 | pi | Y | 0.0321 | 10 | 58 | ok |
| sympy__sympy-22914 | swe | 2 | mp | Y | 0.0038 | 5 | 25 | ok |
| sympy__sympy-22914 | swe | 2 | codex | Y | 0.0056 | 7 | 22 | ok |
| sympy__sympy-22914 | swe | 2 | pi | Y | 0.0120 | 8 | 34 | ok |
| sympy__sympy-23262 | swe | 1 | mp | Y | 0.0222 | 54 | 249 | ok |
| sympy__sympy-23262 | swe | 1 | codex | Y | 0.0140 | 27 | 112 | ok |
| sympy__sympy-23262 | swe | 1 | pi | Y | 0.0141 | 11 | 43 | ok |
| sympy__sympy-23262 | swe | 2 | mp | Y | 0.0718 | 104 | 459 | ok |
| sympy__sympy-23262 | swe | 2 | codex | Y | 0.0121 | 22 | 56 | ok |
| sympy__sympy-23262 | swe | 2 | pi | Y | 0.0200 | 22 | 69 | ok |
| sympy__sympy-23534 | swe | 1 | mp | Y | 0.0359 | 78 | 900 | timeout after 900s |
| sympy__sympy-23534 | swe | 1 | codex | Y | 0.0063 | 9 | 22 | ok |
| sympy__sympy-23534 | swe | 1 | pi | Y | 0.0124 | 8 | 32 | ok |
| sympy__sympy-23534 | swe | 2 | mp | Y | 0.0417 | 71 | 486 | ok |
| sympy__sympy-23534 | swe | 2 | codex | Y | 0.0064 | 8 | 20 | ok |
| sympy__sympy-23534 | swe | 2 | pi | Y | 0.0123 | 8 | 49 | ok |
| sympy__sympy-23824 | swe | 1 | mp | Y | 0.0127 | 16 | 83 | ok |
| sympy__sympy-23824 | swe | 1 | codex | Y | 0.0064 | 9 | 34 | ok |
| sympy__sympy-23824 | swe | 1 | pi | Y | 0.0322 | 8 | 40 | ok |
| sympy__sympy-23824 | swe | 2 | mp | Y | 0.0099 | 14 | 79 | ok |
| sympy__sympy-23824 | swe | 2 | codex | Y | 0.0075 | 9 | 34 | ok |
| sympy__sympy-23824 | swe | 2 | pi | Y | 0.0127 | 7 | 41 | ok |
| sympy__sympy-24213 | swe | 1 | mp | Y | 0.0055 | 10 | 46 | ok |
| sympy__sympy-24213 | swe | 1 | codex | Y | 0.0178 | 27 | 100 | ok |
| sympy__sympy-24213 | swe | 1 | pi | Y | 0.0108 | 6 | 23 | ok |
| sympy__sympy-24213 | swe | 2 | mp | Y | 0.0064 | 8 | 36 | ok |
| sympy__sympy-24213 | swe | 2 | codex | Y | 0.0066 | 9 | 39 | ok |
| sympy__sympy-24213 | swe | 2 | pi | Y | 0.0142 | 9 | 45 | ok |
| sympy__sympy-24539 | swe | 1 | mp | Y | 0.0059 | 9 | 82 | ok |
| sympy__sympy-24539 | swe | 1 | codex | Y | 0.0059 | 9 | 22 | ok |
| sympy__sympy-24539 | swe | 1 | pi | Y | 0.0148 | 13 | 45 | ok |
| sympy__sympy-24539 | swe | 2 | mp | Y | 0.0105 | 17 | 114 | ok |
| sympy__sympy-24539 | swe | 2 | codex | Y | 0.0051 | 5 | 16 | ok |
| sympy__sympy-24539 | swe | 2 | pi | Y | 0.0109 | 6 | 27 | ok |
| docx_tracked | office | 1 | mp | Y | 0.0035 | 8 | 22 | all 7 checks passed |
| docx_tracked | office | 1 | codex | Y | 0.0106 | 12 | 29 | all 7 checks passed |
| docx_tracked | office | 1 | pi | Y | 0.0181 | 19 | 49 | all 7 checks passed |
| docx_tracked | office | 2 | mp | Y | 0.0038 | 8 | 19 | all 7 checks passed |
| docx_tracked | office | 2 | codex | Y | 0.0126 | 18 | 43 | all 7 checks passed |
| docx_tracked | office | 2 | pi | Y | 0.0121 | 9 | 25 | all 7 checks passed |
| invoices_fx | office | 1 | mp | n | 0.0258 | 32 | 96 | FAILED: one row per invoice line (no more than the 8 lines plus per-invoice subtotals) (19 |
| invoices_fx | office | 1 | codex | Y | 0.0062 | 8 | 21 | all 4 checks passed |
| invoices_fx | office | 1 | pi | n | 0.0308 | 22 | 86 | FAILED: every line has invoice, customer, currency, item, qty, unit price, amount and USD  |
| invoices_fx | office | 2 | mp | Y | 0.0035 | 6 | 18 | all 4 checks passed |
| invoices_fx | office | 2 | codex | Y | 0.0055 | 6 | 14 | all 4 checks passed |
| invoices_fx | office | 2 | pi | n | 0.0271 | 30 | 84 | FAILED: a total row holds the USD grand total 4308.96 [3/4 checks passed] |
| pdf_report | office | 1 | mp | Y | 0.4154 | 418 | 900 | still looping when the runner process was killed (>=15 min); stopped by hand, counted as a |
| pdf_report | office | 1 | codex | Y | 0.0216 | 25 | 102 | all 4 checks passed |
| pdf_report | office | 1 | pi | Y | 0.0190 | 14 | 54 | all 4 checks passed |
| pdf_report | office | 2 | mp | n | 0.3521 | 283 | 900 | timeout after 900s |
| pdf_report | office | 2 | codex | Y | 0.0080 | 7 | 32 | all 4 checks passed |
| pdf_report | office | 2 | pi | Y | 0.0196 | 16 | 60 | all 4 checks passed |
| pptx_chart | office | 1 | mp | Y | 0.1265 | 128 | 372 | all 4 checks passed |
| pptx_chart | office | 1 | codex | Y | 0.0183 | 16 | 76 | all 4 checks passed |
| pptx_chart | office | 1 | pi | Y | 0.0645 | 53 | 161 | all 4 checks passed |
| pptx_chart | office | 2 | mp | n | 0.0549 | 38 | 135 | deck.pptx is missing |
| pptx_chart | office | 2 | codex | Y | 0.0187 | 15 | 71 | all 4 checks passed |
| pptx_chart | office | 2 | pi | Y | 0.0533 | 32 | 122 | all 4 checks passed |
| xlsx_budget | office | 1 | mp | n | 0.0357 | 42 | 155 | FAILED: Budget formulas reference the Assumptions sheet (>=5 cells) (0 cells) [6/7 checks  |
| xlsx_budget | office | 1 | codex | Y | 0.0079 | 7 | 57 | all 7 checks passed |
| xlsx_budget | office | 1 | pi | Y | 0.0307 | 20 | 83 | all 7 checks passed |
| xlsx_budget | office | 2 | mp | Y | 0.0715 | 83 | 243 | all 7 checks passed |
| xlsx_budget | office | 2 | codex | Y | 0.0054 | 8 | 24 | all 7 checks passed |
| xlsx_budget | office | 2 | pi | Y | 0.0243 | 23 | 83 | all 7 checks passed |
| bash_rename | everyday | 1 | mp | Y | 0.0046 | 10 | 22 | all 3 checks passed |
| bash_rename | everyday | 1 | codex | Y | 0.0036 | 7 | 18 | all 3 checks passed |
| bash_rename | everyday | 1 | pi | Y | 0.0167 | 11 | 32 | all 3 checks passed |
| bash_rename | everyday | 2 | mp | Y | 0.0052 | 8 | 29 | all 3 checks passed |
| bash_rename | everyday | 2 | codex | Y | 0.0057 | 7 | 14 | all 3 checks passed |
| bash_rename | everyday | 2 | pi | Y | 0.0123 | 7 | 25 | all 3 checks passed |
| csv_clean | everyday | 1 | mp | Y | 0.0073 | 7 | 46 | all 2 checks passed |
| csv_clean | everyday | 1 | codex | Y | 0.0068 | 5 | 13 | all 2 checks passed |
| csv_clean | everyday | 1 | pi | Y | 0.0111 | 4 | 12 | all 2 checks passed |
| csv_clean | everyday | 2 | mp | Y | 0.0042 | 5 | 21 | all 2 checks passed |
| csv_clean | everyday | 2 | codex | Y | 0.0095 | 9 | 26 | all 2 checks passed |
| csv_clean | everyday | 2 | pi | Y | 0.0137 | 7 | 22 | all 2 checks passed |
| csv_sales | everyday | 1 | mp | Y | 0.0025 | 5 | 34 | all 3 checks passed |
| csv_sales | everyday | 1 | codex | Y | 0.0044 | 4 | 8 | all 3 checks passed |
| csv_sales | everyday | 1 | pi | Y | 0.0104 | 5 | 15 | all 3 checks passed |
| csv_sales | everyday | 2 | mp | Y | 0.0026 | 5 | 16 | all 3 checks passed |
| csv_sales | everyday | 2 | codex | Y | 0.0019 | 4 | 8 | all 3 checks passed |
| csv_sales | everyday | 2 | pi | Y | 0.0098 | 4 | 12 | all 3 checks passed |
| fix_test | everyday | 1 | mp | Y | 0.0044 | 5 | 12 | all 1 checks passed |
| fix_test | everyday | 1 | codex | Y | 0.0045 | 4 | 9 | all 1 checks passed |
| fix_test | everyday | 1 | pi | Y | 0.0097 | 5 | 13 | all 1 checks passed |
| fix_test | everyday | 2 | mp | Y | 0.0022 | 5 | 27 | all 1 checks passed |
| fix_test | everyday | 2 | codex | Y | 0.0045 | 5 | 12 | all 1 checks passed |
| fix_test | everyday | 2 | pi | Y | 0.0098 | 5 | 15 | all 1 checks passed |
| flask_feature | everyday | 1 | mp | Y | 0.0178 | 18 | 44 | all 1 checks passed |
| flask_feature | everyday | 1 | codex | Y | 0.0079 | 8 | 23 | all 1 checks passed |
| flask_feature | everyday | 1 | pi | Y | 0.0146 | 9 | 30 | all 1 checks passed |
| flask_feature | everyday | 2 | mp | Y | 0.0180 | 17 | 72 | all 1 checks passed |
| flask_feature | everyday | 2 | codex | Y | 0.0073 | 7 | 19 | all 1 checks passed |
| flask_feature | everyday | 2 | pi | Y | 0.0131 | 9 | 26 | all 1 checks passed |
| js_refactor | everyday | 1 | mp | Y | 0.0047 | 7 | 24 | all 5 checks passed |
| js_refactor | everyday | 1 | codex | Y | 0.0022 | 4 | 10 | all 5 checks passed |
| js_refactor | everyday | 1 | pi | Y | 0.0190 | 6 | 24 | all 5 checks passed |
| js_refactor | everyday | 2 | mp | Y | 0.0116 | 16 | 45 | all 5 checks passed |
| js_refactor | everyday | 2 | codex | Y | 0.0048 | 5 | 9 | all 5 checks passed |
| js_refactor | everyday | 2 | pi | Y | 0.0108 | 6 | 20 | all 5 checks passed |
| md_to_docx | everyday | 1 | mp | n | 0.0030 | 5 | 12 | FAILED: start_date filled (2026-02-09); end_date filled (2026-05-29) [10/12 checks passed] |
| md_to_docx | everyday | 1 | codex | Y | 0.0073 | 9 | 20 | all 12 checks passed |
| md_to_docx | everyday | 1 | pi | Y | 0.0122 | 9 | 29 | all 12 checks passed |
| md_to_docx | everyday | 2 | mp | n | 0.0023 | 5 | 12 | FAILED: start_date filled (2026-02-09); end_date filled (2026-05-29) [10/12 checks passed] |
| md_to_docx | everyday | 2 | codex | Y | 0.0089 | 13 | 33 | all 12 checks passed |
| md_to_docx | everyday | 2 | pi | Y | 0.0118 | 9 | 23 | all 12 checks passed |
| pdf_facts | everyday | 1 | mp | Y | 0.0082 | 11 | 41 | all 7 checks passed |
| pdf_facts | everyday | 1 | codex | Y | 0.0062 | 7 | 13 | all 7 checks passed |
| pdf_facts | everyday | 1 | pi | Y | 0.0121 | 6 | 14 | all 7 checks passed |
| pdf_facts | everyday | 2 | mp | Y | 0.0048 | 7 | 15 | all 7 checks passed |
| pdf_facts | everyday | 2 | codex | Y | 0.0065 | 7 | 12 | all 7 checks passed |
| pdf_facts | everyday | 2 | pi | Y | 0.0119 | 7 | 19 | all 7 checks passed |
| three_files | everyday | 1 | mp | Y | 0.0030 | 5 | 21 | all 3 checks passed |
| three_files | everyday | 1 | codex | Y | 0.0053 | 6 | 14 | all 3 checks passed |
| three_files | everyday | 1 | pi | Y | 0.0178 | 3 | 15 | all 3 checks passed |
| three_files | everyday | 2 | mp | Y | 0.0030 | 5 | 18 | all 3 checks passed |
| three_files | everyday | 2 | codex | Y | 0.0051 | 4 | 8 | all 3 checks passed |
| three_files | everyday | 2 | pi | Y | 0.0107 | 4 | 19 | all 3 checks passed |

VERDICT: FAIL - Meshpool $/solved $0.0424 = 5.38x Codex ($0.0079) and 2.22x Pi ($0.0191) [need <= 0.80x both]; solved 34 vs Codex 40 / Pi 38 [need >= each]
