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
