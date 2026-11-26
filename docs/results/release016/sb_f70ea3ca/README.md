SpreadsheetBench, 6 tasks x 6 reps, Meshpool work arm on f70ea3ca (0873b968 + patch 0007), deepseek-v4.1-flash.
Codex rows: office_final6/spreadsheetbench/codex.results.jsonl (same tasks, 6 reps, same prices).

| arm | solved | cost per solved | vs Codex (95% bootstrap CI) | median secs |
|---|---|---|---|---|
| Meshpool f70ea3ca | 31/36 | $0.00650 | 0.90x (0.70-1.18) | 25.1 (0.94x) |
| Meshpool f133d912 | 31/36 | $0.00753 | 1.04x | 23.6 |
| Codex 0.171 | 35/36 | $0.00721 | 1.00x | 26.8 |

Failures: sb-56921 r1 (511; the formula check fired, the agent re-saved the same formulas, see patch 0008),
sb-41969 r2, sb-280-17 r2, sb-39515 r3 (O2 = 0, not a date formula), sb-269-43 r6.

Correction: commit ff33fc55's message says bench runs found no LibreOffice. Every bench script already set
MH_ALLOW_SYSTEM_SOFFICE=1; only the offline replay lacked it. The arms.py default is harmless.
An earlier attempt (sb7 on ff33fc55) produced no results: since 1e954486 the bench client could not read the page
token (fixed in patch 0007); it made 0 model calls.
