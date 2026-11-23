# Sources and licenses

| Suite | Repo | Pinned commit | License | Vendored |
|---|---|---|---|---|
| SpreadsheetBench (verified 400) | https://github.com/RUCKBReasoning/SpreadsheetBench | `49b73a94775fb489063f60ca1865e3a650079a79` | CC BY-SA 4.0 (stated in the repo README, "License and Citation"; no LICENSE file) | `data/spreadsheetbench/`: init and golden workbooks of 6 tasks plus `tasks.json` (id, instruction, instruction_type, answer_position copied from `data/spreadsheetbench_verified_400.tar.gz` `dataset.json`) |
| OfficeBench | https://github.com/zlwang-cs/OfficeBench | `b978b808667c32b52ce19a67ce1def1de9ae02b7` | Apache-2.0 (LICENSE in repo) | `data/officebench/`: `testbed/` of 4 tasks (1-15, 2-23, 1-11, 1-19), `reference/` for 1-11, and `tasks.json` (task text and evaluation config of the 4 subtasks) |

Redistribution is permitted by both licenses, so only the chosen tasks' files are vendored (about 3.4 MB; the largest
is OfficeBench 1-19 `paper.pdf`, a distractor file of the official testbed). Files are unmodified.
CC BY-SA 4.0 requires attribution and share-alike: the SpreadsheetBench files under `data/spreadsheetbench/` remain
under CC BY-SA 4.0 (Ma et al., "SpreadsheetBench", NeurIPS 2024 D&B); the OfficeBench files remain under Apache-2.0
(Wang et al., "OfficeBench", 2024). The grading code in this folder is a port of each repo's evaluation code.
