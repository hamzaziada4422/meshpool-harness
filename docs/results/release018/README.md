# 0.0.18 live office check (stopped early by the owner: no more model credit)

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
failing reorder runs and on nothing else. Its live effect was not measured: the owner stopped all model spending.

Pilot (decks_pilot, 1 run on 8dbf65f7): meshpool 5/5, Codex 3/5.

## Office-7, 0.0.18 only (o7_018, partial)

| Task | Solved |
|---|---|
| xlsx_edit, docx_tracked, docx_mailmerge, invoices_fx | 2/2 each |
| xlsx_budget, pptx_chart, pdf_report | 1/1 each |

11/11 solved before the run was stopped. 0.0.17 on the same suite (docs/results/office_c171, O7c): 41/42.
OfficeBench and SpreadsheetBench were not run.

## Office panel (no model spend)

testkit/office_panel.js against the real app with the mock model: 22/22 after the fix in cc84a5cd (a panel chat
keeps the Work route and has ask_user; before it, the panel's questions failed).

Spend for everything here: about $0.48 (key spend 87.36 to 87.84).
