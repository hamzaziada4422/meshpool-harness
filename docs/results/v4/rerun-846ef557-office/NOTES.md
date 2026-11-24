# Office re-run on 846ef557, stopped early (24 November 2026)

Build: 749d806c plus the PDF chart fix (make_pdf draws charts) and the budget grader fix (defined names on the
Assumptions sheet count as links). Six tasks x Meshpool Work, CLI, on Codex, on Pi; DeepSeek V4.1 Flash, one run,
cap $0.70. Stopped on the owner's instruction after 19 of 24 cells ($0.657 spent); pdf_report on Work, on Codex and
on Pi, and pptx_chart on Codex and on Pi did not run.

Gateway rule (coordinator, 24 November): every arm retries 503s, so a cell that hit 503s and finished counts, with
the retry time in its seconds. A cell is excluded only when it failed with a 503 or 401 among its last 5 requests.
The gateway returned 219 503s and 4 401s out of 751 requests.

Seconds for Meshpool on Codex / on Pi come from the app's task receipts. The kit's clock ran to the 900 s limit on
those arms because of an app bug, found from this run and fixed in c068fc43: every poll of a Pi or Codex chat
returned HTTP 500 after its first turn, so the chat never showed as finished.

The one valid failure, docx_tracked on Meshpool CLI (fee no longer italic), is fixed in 3c4e5268. The edit tool
ignored replace_text{tracked: true} and its Word op list hid tracked_replace, so the model hand-wrote the XML.

Same six tasks on 23 November (docs/results/v4/final-office): Codex 6/6, 12.2 calls, $0.012, 50 s per task; Pi 5/6,
9.5 calls, $0.0065, 37 s.

| task | arm | result | calls (200) | gateway errors | $ | s |
|---|---|---|---|---|---|---|
| xlsx_edit | mp-cli | pass | 18 | 4 | 0.0248 | 180 |
| xlsx_edit | mp-work | pass | 23 | 5 | 0.0264 | 303 |
| docx_tracked | mp-cli | fail | 26 | 11 | 0.0373 | 359 |
| docx_tracked | mp-work | pass | 26 | 16 | 0.0255 | 593 |
| xlsx_edit | mp-on-codex | pass | 13 | 12 | 0.0110 | 128 |
| xlsx_edit | mp-on-pi | excluded: gateway | 1 | 1 | 0.0010 | 88 |
| invoices_fx | mp-work | pass | 34 | 10 | 0.0360 | 405 |
| invoices_fx | mp-cli | pass | 32 | 7 | 0.0339 | 423 |
| docx_tracked | mp-on-codex | pass | 11 | 8 | 0.0105 | 98 |
| docx_tracked | mp-on-pi | pass | 13 | 4 | 0.0063 | 100 |
| invoices_fx | mp-on-codex | excluded: gateway | 35 | 34 | 0.0374 | 462 |
| xlsx_budget | mp-work | pass | 42 | 16 | 0.0615 | 643 |
| invoices_fx | mp-on-pi | excluded: gateway | 20 | 10 | 0.0251 | 408 |
| xlsx_budget | mp-cli | pass | 54 | 12 | 0.0428 | 554 |
| xlsx_budget | mp-on-codex | pass | 35 | 0 | 0.0414 | 208 |
| xlsx_budget | mp-on-pi | pass | 25 | 0 | 0.0232 | 178 |
| pptx_chart | mp-work | pass | 44 | 20 | 0.0792 | 900 |
| pptx_chart | mp-cli | pass | 58 | 31 | 0.0957 | 900 |
| pdf_report | mp-cli | pass | 9 | 4 | 0.0114 | 96 |

Valid cells: 16; excluded for the gateway: 3.
- mp-cli: 5/6 passed, 32.8 calls, $0.0410, 419 s per valid task
- mp-on-codex: 3/3 passed, 19.7 calls, $0.0210, 145 s per valid task
- mp-on-pi: 2/2 passed, 19.0 calls, $0.0147, 139 s per valid task
- mp-work: 5/5 passed, 33.8 calls, $0.0457, 569 s per valid task