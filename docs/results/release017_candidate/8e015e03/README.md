0.0.17 candidate: 8e015e03 (main dd4ad47d + 0008-0012 + review fixes); app code identical to rc017b c193d8e1.
Meshpool work arm; Codex rows from release016/office_final6. Cost per solved vs Codex with 95% bootstrap CI; time is
the median of per-task median-time ratios. Spend: $9.29 -> $10.22 billed (user approved +$1.50).

| suite | build | solved | cost per solved vs Codex | time vs Codex |
|---|---|---|---|---|
| Office-7 | rc017 | 40/42 | 1.03x (0.77-1.38) | 0.65x |
| Office-7 | 0.0.16 | 42/42 | 0.94x (0.71-1.22) | 0.85x |
| Office-7 | Codex | 41/42 | 1.00x | 1.00x |
| SpreadsheetBench | rc017 (12 reps) | 64/72 | 1.08x (0.82-1.37) | 0.87x |
| SpreadsheetBench | 0.0.16 | 31/36 | 0.90x (0.70-1.18) | 1.08x |
| SpreadsheetBench | Codex | 35/36 | 1.00x | 1.00x |
| OfficeBench | rc017 | 22/24 | 0.73x (0.53-1.00) | 0.61x |
| OfficeBench | 0.0.16 | 21/24 | 0.83x (0.51-1.44) | 0.79x |
| OfficeBench | Codex | 24/24 | 1.00x | 1.00x |

Verdict: not better than 0.0.16 on cost. Office-7 failures (docx_mailmerge r3 due dates, xlsx_budget r6 profit) had
neither the 0009 sentence nor any new check note in their sessions. SpreadsheetBench cost per run rose from $0.0056 to
$0.0069 (calls 9.1 -> 11.4); the new checks fired in 16 of 72 runs, too few to explain it, and 0.0.16 itself ranged
$0.0056-$0.0065 across runs. Not released.
