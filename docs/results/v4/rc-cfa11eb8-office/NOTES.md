# Office run on cfa11eb8 (not used for conclusions)

deepseek-v4.1-flash, one run per arm. Two problems make this run unusable as a measurement:

1. Every office engine (sheets, slides, data) crashed on this commit in about 4 s with
   `AttributeError: module 'meshpool_harness.core.endpoint' has no attribute 'OPENER'` (provider_setup imported a
   name the 0.0.14 merge had dropped). The engine reported "finished" with no file and no model call. Reproduced
   outside the benchmark with `make_spreadsheet`. Fixed on the coordinator branch in 8c02f8d4 and bf03ffa8. Effect
   here: pptx_chart produced no deck on any Meshpool arm, and Meshpool on Codex/Pi produced no budget.xlsx or
   invoices.xlsx.
2. The model gateway had an outage mid-run: 166 calls returned 503 and 5 returned 401 (pdf_report, pptx_chart,
   xlsx_budget for Meshpool Work and Meshpool on Pi). The public-suite runs on this commit were stopped for the
   same reason and are not kept.

The final numbers come from the re-run on cc8361db (docs/results/v4/final-cc8361db-*).
