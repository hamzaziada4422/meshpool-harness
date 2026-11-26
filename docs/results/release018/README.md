# 0.0.18 live office check

Settings, the same as the 0.0.16 and 0.0.17 office runs: deepseek-v4.1-flash through llm.meshpool.ai, `--parallel 2`,
`MH_ALLOW_SYSTEM_SOFFICE=1`, `MH_CAPS='{"cache_hints": true, "cache": "auto"}'`, testkit/bench3/run.py. Codex CLI 0.171
in the same hour. 0.0.18 is the candidate at cc84a5cd (565f3286's code for these paths, plus the Office panel fix);
0.0.17 is the v0.0.17 tag.

## Decks: read, understand and edit an existing deck (testkit/bench3/public/decks.py)

Five tasks on one 8-slide deck: answer questions from a chart's data, a table, bullets and speaker notes; fix a typo
everywhere, change the presenter and retitle a slide while keeping the chart and the title format; add a slide after
the agenda with its layout; delete one slide and move another; add Q4 to the existing chart from a CSV.

| Arm | Solved (3 runs x 5 tasks) | Cost | Median time per task |
|---|---|---|---|
| meshpool 0.0.18 (decks018) | 13/15 | $0.085 | 22 s |
| Codex 0.171 (decks018) | 11/15 | $0.094 | 19 s |
| meshpool 0.0.17 (decks017) | 12/15 | $0.094 | 48 s |

Per task, 0.0.18 / Codex / 0.0.17: read 3/3/3, edit 3/3/3, add 3/2/3, chart 3/3/3, reorder 1/0/0.

Every reorder failure, in all three arms, removed the "DRAFT - do not present" slide from the slide list only: PowerPoint
hides it, but its text stays inside the file. The pre-answer check added after these runs (daa8b508,
core._pptx_orphan_slides_miss) names such slides once; replayed on the 49 decks written here it fires on exactly the 8
failing reorder runs and on nothing else. Its live effect is measured below (reorder_fix).

Pilot (decks_pilot, 1 run on 8dbf65f7): meshpool 5/5, Codex 3/5.

The leftover-slide check, live (reorder_fix, 3eff2941, 3 runs): 2/3, against 1/3 before it. In one run the check
fired and the agent removed the hidden slide properly; the other failure deleted a wrong slide (a model mistake, not
a leftover). No run left a deleted slide in the file.

Claude Sonnet as the model (decks_sonnet, 1 run each, meshpool's own harness through a local adapter that is not part
of this repository): 5/5, 4 to 5 calls a task (DeepSeek: 8 to 17), median 32 s. One task took 566 s, of which 549 s was
one slow model call.

## Office-7, OfficeBench, SpreadsheetBench on 0.0.18 (cc84a5cd)

| Suite | meshpool 0.0.18 | Codex, same hour | Cost per solved vs Codex | Median time per task vs Codex |
|---|---|---|---|---|
| Office-7 (o7_018, 7 x 3) | 21/21 | (0.0.17 run: 41/42) | | |
| OfficeBench (ob018, 4 x 3) | 11/12 | 12/12 | 0.84x | 1.27x |
| SpreadsheetBench (sb018, 6 x 2) | 10/12 | 9/12 | 0.76x | 0.98x |

OfficeBench's miss is ob-1-15-2 (1 of 3 runs), the added-text task the 0.0.17 check addressed (12/12 in its own
run); SpreadsheetBench sb-374-31 failed for both, and Codex also missed sb-269-43 once. Small samples: the time
ratios move with the hour (0.0.17's same-hour runs gave 1.22x and 1.07x).

## Office panel (no model spend)

testkit/office_panel.js against the real app with the mock model: 22/22 after the fix in cc84a5cd (a panel chat
keeps the Work route and has ask_user; before it, the panel's questions failed).

Spend for everything here: about $0.87 of model credit for the DeepSeek runs.
