# Office on Codex 0.171's request (MH_WORK_OFFICE_STYLE=c171)

A Work chat's office edit, or a file the user names, sends the request Codex CLI 0.171 sends: its prompt and its one
command tool (exec_command), with none of Meshpool's office notes, pre-answer checks, caps or office tools. A new deck,
report or model and Word tracked changes keep the Work route. Built on bba6e990; the default flip is the only change
after it.

Settings, the same as release016/office_final6: deepseek-v4.1-flash through llm.meshpool.ai, `--parallel 2`,
`MH_ALLOW_SYSTEM_SOFFICE=1`, `MH_CAPS='{"cache_hints": true, "cache": "auto"}'`, testkit/bench3/run.py with
`--arms mp-work` (O7c, OBc, SBc) or `--arms codex,mp-work` (SBp, OBp: Codex in the same hour, side by side).
Recompute with `python testkit/release_table.py --a mp-work --b codex NAME=FILE[,CODEX_FILE]`.

| Suite | Runs | Solved, Meshpool vs Codex | Cost per solved vs Codex (95% CI) | Median per-task time vs Codex |
|---|---|---|---|---|
| Office-7 (O7c, vs office_final6 Codex) | 7 x 6 | 41/42 vs 41/42 | 0.89x (0.81-0.95) | 0.53x |
| SpreadsheetBench, same hour (SBp) | 6 x 6 each | 33/36 vs 33/36 | 0.89x (0.78-1.03) | 1.22x |
| SpreadsheetBench (SBc, vs office_final6 Codex) | 6 x 12 | 64/72 vs 35/36 | 1.09x (0.87-1.34) | 0.76x |
| OfficeBench, same hour (OBp) | 4 x 6 each | 22/24 vs 24/24 | 0.95x (0.53-1.45) | 1.29x |
| OfficeBench (OBc, vs office_final6 Codex) | 4 x 6 | 22/24 vs 24/24 | 0.90x (0.61-1.35) | 0.48x |

0.0.16 on the same suites (office_final6): Office-7 42/42 at 0.94x, SpreadsheetBench 31/36 at 1.04x, OfficeBench 21/24
at 0.83x.

Where it loses: OfficeBench ob-1-15-2 ("add the reference to the end of the document") was solved in 8 of 12 runs against
Codex's 12 of 12; the failing runs put the entry at the end of the References list instead. In the same hour,
SpreadsheetBench and OfficeBench ran 1.22x and 1.29x Codex's median time. Spend for these runs: $2.25 billed.
