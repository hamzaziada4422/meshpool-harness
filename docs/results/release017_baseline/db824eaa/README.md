db824eaa = 0873b968 + patches 0007, 0008, 0009 (not in 0.0.16). Meshpool work arm, 6 reps; Codex rows from
release016/office_final6 (6 reps, same tasks and prices). Time is the median of per-task median-time ratios.

| suite | build | solved | cost per solved | vs Codex (95% bootstrap CI) | time |
|---|---|---|---|---|---|
| OfficeBench | db824eaa | 23/24 | $0.00408 | 0.76x | 11.1 s median vs 17.5 s |
| OfficeBench | f133d912 | 21/24 | $0.00443 | 0.83x | 14.6 s |
| OfficeBench | Codex | 24/24 | $0.00535 | 1.00x | |
| SpreadsheetBench | db824eaa | 32/36 | $0.00768 | 1.07x (0.77-1.47) | 0.84x |
| SpreadsheetBench | f70ea3ca | 31/36 | $0.00650 | 0.90x (0.70-1.18) | 1.08x |
| SpreadsheetBench | Codex | 35/36 | $0.00721 | 1.00x | |

OfficeBench ob-2-23-0 went from 4/6 to 6/6 (patch 0009). SpreadsheetBench cost rose with two long runs on tasks
none of the patches touch (sb-374-31 r4, 38 calls; sb-41969 r4, 33 calls, both the agent's own re-checking).
sb-56921 r4 kept the formula the request called wrong (C15) and changed only C16; the formula check did not judge
an unchanged formula (patch 0010). Office-7 was not run on this build.
