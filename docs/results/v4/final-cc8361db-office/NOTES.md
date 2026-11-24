# Final office run on cc8361db (0.0.15 release line), 24 November 2026

Same matrix as the earlier runs: 7 office tasks x Meshpool Work, Meshpool CLI, Meshpool on Codex, Meshpool on Pi;
6 SpreadsheetBench and 4 OfficeBench tasks x Meshpool Work, Meshpool on Codex. DeepSeek V4.1 Flash, one run, one
metering proxy. Spend: office $0.865, SpreadsheetBench $0.099, OfficeBench $0.151 (cap $1.20).

The gateway returned HTTP 503 (and a few 401) during the run: 240 of 1014 office requests, 112 of 219
SpreadsheetBench, 79 of 214 OfficeBench. A cell is marked "gateway-hit" when any of its requests got a 503 or 401;
its result is not counted as a Meshpool pass or failure. "calls" counts successful (200) requests only. Seconds
for Meshpool on Codex / on Pi come from the app's task receipts, because the kit's clock waits for an app state
that does not go idle after those turns (pdf_report on Codex has no receipt, 900 s is the time limit). There was
no budget left to re-run the gateway-hit cells after the gateway recovered.

Clean failures (no gateway error, so these are Meshpool's): pdf_report on Meshpool Work and CLI (the PDF has no
chart), xlsx_budget on Meshpool Work and CLI (budget values not linked to the Assumptions sheet), invoices_fx on
Meshpool on Pi (time limit before stating the total). The 400s on xlsx_budget / Meshpool on Pi are request errors,
not gateway errors.


| task | arm | pass | calls (200) | $ | s | gateway errors |
|---|---|---|---|---|---|---|
| docx_mailmerge | mp-cli | pass | 7 | 0.0060 | 19 | 0 |
| docx_mailmerge | mp-on-codex | pass | 8 | 0.0077 | 26 | 0 |
| docx_mailmerge | mp-on-pi | pass | 11 | 0.0059 | 34 | 0 |
| docx_mailmerge | mp-work | pass | 13 | 0.0109 | 50 | 0 |
| docx_tracked | mp-cli | pass | 36 | 0.0427 | 242 | 5 (gateway-hit) |
| docx_tracked | mp-on-codex | pass | 19 | 0.0127 | 202 | 21 (gateway-hit) |
| docx_tracked | mp-on-pi | fail | 0 | 0.0000 | 32 | 4 (gateway-hit) |
| docx_tracked | mp-work | pass | 37 | 0.0420 | 338 | 6 (gateway-hit) |
| invoices_fx | mp-cli | pass | 29 | 0.0326 | 447 | 7 (gateway-hit) |
| invoices_fx | mp-on-codex | fail | 23 | 0.0334 | 173 | 6 (gateway-hit) |
| invoices_fx | mp-on-pi | fail | 21 | 0.0209 | 111 | 0 |
| invoices_fx | mp-work | pass | 30 | 0.0389 | 426 | 12 (gateway-hit) |
| pdf_report | mp-cli | fail | 11 | 0.0105 | 52 | 0 |
| pdf_report | mp-on-codex | fail | 73 | 0.1127 | 900 | 53 (gateway-hit) |
| pdf_report | mp-on-pi | fail | 36 | 0.0422 | 203 | 28 (gateway-hit) |
| pdf_report | mp-work | fail | 9 | 0.0126 | 31 | 0 |
| pptx_chart | mp-cli | pass | 28 | 0.0275 | 134 | 0 |
| pptx_chart | mp-on-codex | pass | 33 | 0.0535 | 574 | 23 (gateway-hit) |
| pptx_chart | mp-on-pi | fail | 31 | 0.0399 | 256 | 9 (gateway-hit) |
| pptx_chart | mp-work | pass | 135 | 0.1429 | 660 | 11 (gateway-hit) |
| xlsx_budget | mp-cli | fail | 27 | 0.0162 | 62 | 0 |
| xlsx_budget | mp-on-codex | pass | 15 | 0.0140 | 60 | 0 |
| xlsx_budget | mp-on-pi | pass | 44 | 0.0261 | 144 | 0 |
| xlsx_budget | mp-work | fail | 32 | 0.0227 | 123 | 0 |
| xlsx_edit | mp-cli | pass | 32 | 0.0513 | 734 | 16 (gateway-hit) |
| xlsx_edit | mp-on-codex | pass | 7 | 0.0117 | 197 | 25 (gateway-hit) |
| xlsx_edit | mp-on-pi | fail | 1 | 0.0011 | 70 | 4 (gateway-hit) |
| xlsx_edit | mp-work | pass | 19 | 0.0265 | 429 | 10 (gateway-hit) |
clean mp-cli: 2/4 pass, 18.2 calls, $0.0150, 67 s per clean task
clean mp-on-codex: 2/2 pass, 11.5 calls, $0.0109, 43 s per clean task
clean mp-on-pi: 2/3 pass, 25.3 calls, $0.0176, 96 s per clean task
clean mp-work: 1/3 pass, 18.0 calls, $0.0154, 68 s per clean task

v4-fin-spreadsheetbench
| task | arm | pass | calls (200) | $ | s | gateway errors |
|---|---|---|---|---|---|---|
| sb-269-43 | mp-on-codex | pass | 6 | 0.0052 | 13 | 0 |
| sb-269-43 | mp-work | pass | 10 | 0.0074 | 107 | 3 (gateway-hit) |
| sb-280-17 | mp-on-codex | pass | 7 | 0.0100 | 166 | 7 (gateway-hit) |
| sb-280-17 | mp-work | pass | 10 | 0.0066 | 210 | 3 (gateway-hit) |
| sb-374-31 | mp-on-codex | pass | 7 | 0.0060 | 130 | 22 (gateway-hit) |
| sb-374-31 | mp-work | fail | 7 | 0.0088 | 600 | 19 (gateway-hit) |
| sb-39515 | mp-on-codex | pass | 17 | 0.0200 | 235 | 24 (gateway-hit) |
| sb-39515 | mp-work | pass | 8 | 0.0084 | 61 | 1 (gateway-hit) |
| sb-41969 | mp-on-codex | pass | 6 | 0.0051 | 33 | 0 |
| sb-41969 | mp-work | pass | 5 | 0.0055 | 22 | 0 |
| sb-56921 | mp-on-codex | pass | 11 | 0.0083 | 160 | 27 (gateway-hit) |
| sb-56921 | mp-work | pass | 11 | 0.0079 | 316 | 6 (gateway-hit) |
clean mp-on-codex: 2/2 pass, 6.0 calls, $0.0052, 23 s per clean task
clean mp-work: 1/1 pass, 5.0 calls, $0.0055, 22 s per clean task

v4-fin-officebench
| task | arm | pass | calls (200) | $ | s | gateway errors |
|---|---|---|---|---|---|---|
| ob-1-11-0 | mp-on-codex | fail | 18 | 0.0158 | 428 | 39 (gateway-hit) |
| ob-1-11-0 | mp-work | fail | 25 | 0.0173 | 368 | 8 (gateway-hit) |
| ob-1-15-2 | mp-on-codex | pass | 16 | 0.0124 | 101 | 5 (gateway-hit) |
| ob-1-15-2 | mp-work | pass | 37 | 0.0550 | 260 | 7 (gateway-hit) |
| ob-1-19-3 | mp-on-codex | fail | 0 | 0.0000 | 46 | 6 (gateway-hit) |
| ob-1-19-3 | mp-work | fail | 2 | 0.0035 | 175 | 6 (gateway-hit) |
| ob-2-23-0 | mp-on-codex | fail | 19 | 0.0239 | 84 | 5 (gateway-hit) |
| ob-2-23-0 | mp-work | fail | 18 | 0.0231 | 82 | 3 (gateway-hit) |
