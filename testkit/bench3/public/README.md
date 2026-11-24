# Public office benchmarks for bench3 (audit item D-02)

Small fixed subsets of two public suites as `--tasks-module` files for `testkit/bench3/run.py`. No model calls are
made by the adapters or tests. LibreOffice (`soffice`) is required for SpreadsheetBench grading.

## Tasks and why

SpreadsheetBench verified-400 (`spreadsheetbench.py`, ids `sb-<id>`). Candidates: single test case, workbooks under
60 KB, instruction under 900 chars; 6 drawn with `random.Random(20251123).sample` per instruction type (then
dropping tasks that need real VBA output): 3 cell-level (`sb-41969`, `sb-39515`, `sb-56921`: formulas) and 3
sheet-level (`sb-269-43`, `sb-280-17`, `sb-374-31`: row deletion and cleanup).

OfficeBench (`officebench.py`, ids `ob-<task>-<subtask>`). Chosen from tasks whose testbed ships in the repo, whose
files are only docx/xlsx/pdf/txt and whose checks run on output files; mix of kinds:
`ob-1-15-2` (append reference.txt to a docx), `ob-2-23-0` (PDF invoice total into costs.xlsx),
`ob-1-11-0` (sort an xlsx descending into a new file, exact match to the reference), `ob-1-19-3` (question over a
PDF, answer text). Calendar/email tasks (docker apps) are excluded.

## How grading matches the official code

- SpreadsheetBench: `compare_workbooks` / `cell_level_compare` / `transform_value` / `compare_cell_value` are ported
  from `evaluation/evaluation.py`. As in the official flow (`open_spreadsheet.py`), output and golden workbooks are
  recalculated by headless LibreOffice first (`gradekit.recalc_xlsx`, private profile), loaded with
  `data_only=True`, and every cell of each `answer_position` range (comma separated, optional sheet prefix) must match
  after rounding/type normalisation. A missing output file or sheet fails.
- OfficeBench: `evaluate_contain` (case-insensitive keywords over the OfficeBench readers; numbers ignore commas),
  `evaluate_file_exist`, `evaluate_exact_match` (xlsx, active sheet, both directions) are ports of
  `utils/evaluate.py`; the task's evaluation list comes verbatim from its subtask json, all checks must pass.
- Graders work on `gradekit.scratch_copy`, never modify the workdir, and return a failure dict instead of raising.

## Deviations

- SpreadsheetBench: the official harness has the model write code that saves `<n>_<id>_output.xlsx`; here the prompt
  names the input (`1_<id>_init.xlsx`) and output (`1_<id>_output.xlsx`) files and the answer range, and asks for no
  macros. Verified-400 has one test case per task (not three). Only the cell-level comparison is used (the official
  script uses it for both instruction types).
- OfficeBench: the official agent runs in docker with app actions; here any agent edits the testbed folder directly
  (workdir = testbed, files under `./data`). The official `finish_task` answer written to `data/answer.txt` is
  replaced by: `data/answer.txt` if the agent wrote it, else the final reply text. PDF reading uses `pypdf` instead of
  PyPDF2. The `expected_file` path `../../../../reference/x` maps to the vendored `reference/x` (not placed in the
  workdir). The official prompt is the bare task text; a short note about `./data` and `answer.txt` is appended.
  Some official keyword checks are strict about wording (e.g. `ob-2-23-0` needs the text "total due" in the sheet).

## Run

```
/root/.venvs/mp/bin/python testkit/bench3/run.py --tasks-module testkit/bench3/public/spreadsheetbench.py --out <dir> --arms codex,mp-work,mp-on-codex --reps 1 --model deepseek-v4.1-flash --budget 2 --mp-src app
/root/.venvs/mp/bin/python testkit/bench3/run.py --tasks-module testkit/bench3/public/officebench.py --out <dir> --arms codex,mp-work,mp-on-codex --reps 1 --model deepseek-v4.1-flash --budget 2 --mp-src app
```

Self-test (input fails, reference passes, workdir unchanged):
`cd /home/user/meshpool-harness && /root/.venvs/mp/bin/python -m pytest -q -p no:cacheprovider testkit/bench3/public/tests`
