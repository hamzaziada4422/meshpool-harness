# ws07-engines plan: sheets, research, data, general engines

Audit done on 0.0.9; checked against 0.0.12 (commit c189e04). Paths are relative to `app/`.

## M30-01 (High): sheets run that ends aborted / tool-less / need_context loses a passing workbook

Still reproduces. `engines/sheets/engine.py` `solve()` sets `at_cap = not r.ok and (crashed or
r.reason.startswith(CAPS))`; `CAPS` is only turn, wall-clock and budget caps. The loop (`core/loop.py`) also ends
with `aborted: command failed N times ...` (guard Abort), `model stopped calling tools` (3 tool-less replies) and
`need_context`. Those skip `salvage()` and `rv.finalize()`, so the result is `ok False`, no artifacts, the model's
empty answer.

Fix (root cause: salvage is keyed on a list of reasons instead of "no accepted finish"): every run without an
accepted finish is salvaged (`at_cap = not r.ok`). `salvage()` words the head line for each end reason and, for
`need_context`, keeps the model's question in the answer. The review `finalize()` gets the same flag.

Tests (`tests/test_sheets_turn_cap.py`): a saved passing workbook then (a) three tool-less replies, (b) a command
that fails identically 5 times (guard abort), (c) need_context -> `ok True`, the workbook in artifacts, a non-empty
answer naming the reason. All fail on the old code.

## M30-02 (High): an input .xlsx copied into the job folder counts as saved by the run

Still reproduces. `cli.run_request` copies inputs into the output folder with `shutil.copy` (new mtime) and passes
the original paths as `files`. `SheetsWorkspace.saved_workbooks()` counts any .xlsx with `mtime >= started - 1`
whose resolved path is not an input path, so the copy counts: BUILD NOW never fires and `salvage()` checks and
delivers the user's unchanged input as the engine's result.

Fix (root cause: "written in this run" judged by mtime and path): the workspace fingerprints (sha256) every
workbook already in the folder and every input file when it starts. A workbook counts as saved only when its
content differs from what was in the folder at start and from every input. mtime stays as a cheap pre-filter.

Tests: input copied into the folder before `solve()` (shutil.copy, fresh mtime) -> `saved_workbook()` is False,
BUILD NOW fires, a cap run returns "no workbook saved" instead of delivering the input; once the model edits the
copy in place it counts.

## M30-03: delivering an earlier reviewed round overwrites the newer model.xlsx with no copy kept

Still reproduces. `WorkbookReview.finalize()` copies `best["copy"]` over `ws.root / best["workbook"]`; when the
newest version was never reviewed (it fails the checks), it is lost while the note says it "is kept in
.run/review/".

Fix: before the copy, any existing file at the destination whose sha256 is not a reviewed round's is copied to
`.run/review/latest/<name>`, and the note names that path (true in both cases).

Test: two rounds, round 1 reviewed and best; model.xlsx then rewritten (failing); finalize -> model.xlsx has round
1 content, `.run/review/latest/model.xlsx` has the newer bytes, the answer names it.

## M30-04: FINISH NOW sent with no saved workbook

Still reproduces: `turn_budget()` sends FINISH NOW ("Do not change the workbook any more") whenever <= 2 turns are
left, and `build_first()` can add BUILD NOW on the same turn.

Fix: with no saved workbook the last-turns note is a single SAVE NOW note (run the build script now so a workbook
exists; finish when it passes); FINISH NOW only when a workbook exists. BUILD NOW is not sent on the same turn.

Test: `turn_budget(n-2, n, ws)` with no workbook -> no "Do not change the workbook", no FINISH NOW, one save
instruction; with a workbook -> FINISH NOW as before.

## M31-01: research run ending on a cap/abort/tool-less replies throws away a passing report.md

Still reproduces: `engines/research/engine.py` returns the loop result as is.

Fix: same shape as sheets: any run without an accepted finish runs the engine's `finish_check` on `report.md`
(answer = the report's Answer section). Passing -> `ok True`, artifacts report.md / report.docx / sources.json,
the Answer as the answer, the reason says it was salvaged. Failing -> `ok False` with the files and the failures.
`cli.run_request` then hands report.md to the next step (research -> slides).

Tests: `tests/test_research_engine.py`: passing report then tool-less replies -> ok True with artifacts; failing
report -> ok False with failures listed; CLI chain research -> slides continues after a salvaged research step.

## M31-02 / M35-01 (High): repeat_failure counts a converging checker as identical failures

Still reproduces: `core/guards.py` `Guards.after_command()` keys failures on `cmd + re.sub(r"\d+", "N", last
error line)`. `python -m engines.sheets.check` prints `FAIL: 7 issues; stats {...}` -> `FAIL: N issues`, so 7, 6, 5,
4, 3 issues are five "identical" failures and the 5th aborts the run (sheets, research, code).

Fix (root cause: digits treated as noise): the signature is the command plus all error lines of the output with
only volatile tokens normalised (hex addresses, durations, clock times, temporary paths, pids), digits kept.
Counting is consecutive per command: a different output for the same command resets the count, a pass clears it.
Identical failures still warn at 3 and abort at 5.

Tests (`tests/test_guards_repeat_failure.py`): 7->6->5->4->3 issues never aborts; the same output 5 times aborts;
outputs that differ only in an address or a duration still count as identical; a different failure in between
resets the count. An engine-level test: the sheets engine with a converging checker finishes.

## M31-03: sources.json rewritten in place

Still reproduces: `engines/research/library.py` `Corpus.save()` uses `Path.write_text`.

Fix: `save()` writes through `fsio.atomic_write_text` (temp file + fsync + rename), so an interrupted save leaves
the previous file. `load()` of a file that is already damaged (written by an older version) moves it aside with
`fsio.quarantine`, starts empty and records why; the research `finish_check` and `engines.research.check` say the
corpus was damaged and where the copy is, instead of crashing on every later load.

Tests: a save killed mid-write (simulated failure inside the write) leaves the old sources.json loadable; a
truncated sources.json -> `Corpus(".")` loads, the damaged file is kept as `sources.json.corrupt-*`, finish_check
names it.

## M35-03: a reply ordered [finish, action] accepts finish and never runs the action

Still reproduces: `core/loop.py` runs a turn's calls in order and returns on an accepted finish.

Fix (root cause: finish is evaluated before the rest of the turn): finish (and need_context) calls are moved to the
end of the turn's call list before the turn runs (the stored assistant message uses the same order). The actions
run first; a failed action makes the finish refused as already done for [action, finish].

Tests (`tests/test_loop.py`): [finish, write_file] -> the file exists and the run finishes; [finish, failing bash]
-> finish not accepted that turn.

## M29-01: a failed routing-hint model call escapes router.route

Still reproduces: `router/router.py` `route()` calls `_classify()` unguarded; LiveClient retries transient errors
up to 25 times (~11 minutes in an outage) and then raises, failing `meshpool run`.

Fix: the hint call is optional: any exception keeps the rules' decision (reason says the hint failed). The call is
bounded: `LiveClient.chat()` takes `attempts` (default unchanged, 25) and the router asks for 2.

Tests (`tests/test_router_hint.py`): a client that raises -> the rules' engine, no exception; LiveClient with a
ledger that always fails transiently is called at most 2 times from the router (sleep patched).

## D-01: engines default to deepseek-v4.1-flash

Still reproduces for direct callers (`api.run_task`, tests, the app's office_task when the user picked a model in
the chat that is not MH_MODEL): `solve(model="deepseek-v4.1-flash")` in browser, code, credit, data, general,
research, sheets; `forge/build.py:28`; `router.DEFAULT_MODEL` read at import. The app agent's office_task starts
`meshpool run` without the agent's current model, so a model picked in the chat (including `chatgpt:` provider
models) is ignored by the engines.

Fix (one resolver): `providers.configured_model(explicit=None)`: explicit model, else MH_MODEL from the
environment, else MH_MODEL from the saved settings, else the default. Every engine's `solve(model=None)` resolves
through it; the router resolves at call time; forge's module default uses it (one line, ws06 owns forge).
`meshpool run --model` added; `office_jobs.new_job(model=...)` passes the agent's `current_model()`;
`run_request(model=...)` passes it to every engine. The provider travels with the model id (`chatgpt:` prefix), so
the provider follows too.

Tests (`tests/test_engine_model.py`): each engine's solve with no model uses MH_MODEL (env) and the saved-settings
model; an explicit model wins; office_task's job command carries `--model <agent model>`; `run_request(model=)`
reaches the engine; router classify uses the configured model.

## P-14: office runs slow

Measured baseline with deepseek-v4.1-flash on the practical "sales_xlsx" task (see the ledger row for numbers). Fixes
in this workstream's engines, each keeping the checks that gate quality:
1. The sheets research dossier runs before every workbook, even a CSV report or an edit of the user's workbook (an
   identify call, then up to 6 brief questions researched on the web, and a dossier the model then reads). It now
   runs only when the identify step finds a company the request is about (`build_dossier(require_company=True)` from
   sheets), and is skipped outright for edits of an attached workbook.
2. Further cuts follow from the measured trace (turns spent, tokens per turn) and are recorded in the ledger with
   before/after numbers; ws20 measures against Codex and Pi.

Test: a sheets run on a CSV report task makes no dossier research calls (identify only, or none for edits).
