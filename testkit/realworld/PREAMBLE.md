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
