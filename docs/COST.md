# How Meshpool 0.0.16 is cheaper than Codex

This explains where Meshpool's savings against Codex CLI 0.171 come from, which settings produce them, and how they
were measured. All numbers come from the release round (raw rows on the audit branch under
`docs/results/release016/`), recomputed with `testkit/verify_paired.py` and `testkit/release_table.py`.

## How it was measured

- Same model (deepseek-v4.1-flash), same gateway, same hour, both agents on the same tasks, 2 runs per task (6 for
  the final office run).
- Every model call goes through a metering proxy that records uncached input, cached input and output tokens.
  Cost is priced at the gateway's list prices: $0.15 per million uncached input tokens, $0.003 per million cached
  input tokens and $0.60 per million output tokens.
- "Cost per solved" is total spend divided by tasks solved, so an agent that fails more pays more per success.
  The interval is a paired bootstrap over tasks (reps of a task stay together).
- "Median time" is the median over tasks of Meshpool's time divided by Codex's on the same task.
- Meshpool ran as shipped (`python -m meshpool_harness -p ... --auto` for office, the same agent through
  `api.run_task` for code), with one benchmark setting: `MH_CAPS` cache hints, which send a stable
  `prompt_cache_key` so the provider can reuse the cached prefix.

## The short answer

On the code suites, Meshpool makes about the same number of model calls as Codex and writes about the same amount of
output, but **every call carries 40-55% less input**. Almost all of that difference is in the fixed part of the
request, the part that is the same on every call.

| Suite | Calls per run, MP / Codex | Input tokens per call, MP / Codex | Output per run, MP / Codex | Cost per solved |
|---|---|---|---|---|
| SWE-bench Verified, SymPy | 19.9 / 23.2 | 13.3k / 18.3k | 3.9k / 4.8k | 0.76x |
| v2_dev code | 10.0 / 10.7 | 7.3k / 12.1k | 3.7k / 3.9k | 0.79x |
| v2_eval + build/debug/deep/hard | 6.5 / 6.5 | 5.8k / 10.9k | 1.57k / 1.60k | 0.71x |
| Work (DABench + data tasks) | 3.3 / 4.0 | 3.8k / 10.1k | 0.59k / 0.59k | 0.51x |

Cached input costs 50 times less than uncached input, and both agents get 86-95% of their input from the cache.
So the saving is mainly in the **uncached** input: the first call of every task is sent in full, and a request that
is about 5,000 tokens smaller pays for 5,000 fewer full-price tokens on that call, and on every later call whose
cache entry expired. On v2_eval, uncached input per run is 5.5k for Meshpool against 9.9k for Codex, while output (the
other half of the bill) is the same. That gap is the whole cost difference.

## Where the 5,000 tokens per call come from

Codex CLI 0.171's request (captured in `docs/results/codex171-request/`) contains:

| Part | Codex 0.171 | Meshpool code chat (`c171`) |
|---|---|---|
| Base instructions | 17.1k characters | the same text, with Meshpool's identity line (17.9k, `app/defaults/engines/codex/prompt-171.md`) |
| Skills block | 2.5k characters | none |
| Tool definitions | 9 tools, 18.7k characters: exec_command, write_stdin, request_user_input, view_image, multi_agent_v1, get_goal, create_goal, update_goal, web_search | 1 tool, 1.6k characters: exec_command, with Codex's own result format |
| Environment context | a user message | the same message, fixed for the chat so the prefix never changes |

Meshpool keeps exactly what does the work on a coding task: Codex's own instructions (which the model is already good
at following), its one command tool, and its result format. It drops what the task never uses: 8 tools and the skills
list. The offline measurement in `testkit/loop/small_budgets.json` puts the fixed overhead at 4,990 tokens per call for
Meshpool's code lane against 10,411 for Codex 0.171.

Meshpool then adds its own loop around that request, none of which is sent to the model: permission checks, the
sandbox, loop guards (a call refused or failing repeatedly with nothing changed is stopped), and compaction.

## The settings behind it (defaults in 0.0.16)

### Code chats

| Setting | Default | What it does | Measured effect |
|---|---|---|---|
| `MH_LARGE_REPO_STYLE` | `c171` | A repo too large to preload runs Codex 0.171's request (above). | swe_sympy: 22/24 vs 22/24 at 0.76x (the lean prompt was 1.86x there) |
| `MH_SMALL_REPO_STYLE` | `c171` | Every other code chat does too. `core` switches to Meshpool's own lean prompt (3.3k characters, files and git state preloaded, the last check re-run after each edit). | v2_dev: c171 39/42 at 0.79x, lean core 34/42; v2_eval: c171 144/156 at 0.71x, lean core 143/156 at 0.67x |
| Tool servers | on request | Meshpool's office and browser tools join a code chat only when the user's own words ask for an office file or a web page. Before this fix every chat carried 31 extra tool definitions (about 5k tokens per call). | A small code chat sends 9 tools (exec_command plus memory and notes) instead of 40 |
| `MH_SCRATCH_RM` | on | Deleting named scratch files, Python caches or temp folders is not treated as destructive, so an unattended run does not stall on a refusal. `curl | sh`, `.git`, wildcards and anything outside the project still need a yes. | dev6: 12 refusals in 37 runs, 1-3 wasted calls each |
| argv operators, patch blank lines | on | `["python", "t.py", "2>&1", "|", "tail"]` runs as a pipeline; a blank line between two patch sections is accepted. | 7 failed calls in 23 swe runs; 9 rejected patches in one run |

### Work chats (data and office files)

| Setting | Default | What it does | Measured effect |
|---|---|---|---|
| `MH_LEAN_WORK` | on | Work chats use the lean core (short prompt, files preloaded) instead of the full Work agent. | Work: 45/54 vs 44/54 at 0.51x; 3.8k vs 10.1k input per call, 3.3 vs 4.0 calls per task |
| `MH_WORK_OFFICE_STYLE` | `c171` | An office edit or a file the user names sends Codex 0.171's request (prompt and exec_command only); the rows below then apply to the lean route (`=lean`), decks, reports, models and tracked changes. | Office-7 41/42 vs 41/42 at 0.89x; SpreadsheetBench 33/36 vs 33/36 at 0.89x in the same hour |
| `MH_OFFICE_DIRECT` | on | Edits of existing files, and new files the user names ("make budget.xlsx"), are written with openpyxl, python-docx or python-pptx, the way Codex does. The deck, report and model engines are offered only for an open-ended deliverable ("a pitch deck for investors"). | pptx_chart: the slides engine took 33 and 84 calls in two runs; Codex built the deck by hand for $0.02 |
| Tracked changes | on request | A request for Word tracked changes or comments brings `edit_office_file`, which writes real revisions; revision XML written by hand is refused. | docx_tracked: 8 calls instead of 71 |
| Pre-answer checks (`MH_OFFICE_FINISH_CHECK`) | on | Before answering, the files written this turn are checked against the request's own terms: a stated slide count, a requested total row, a template's formatting, shapes inside the slide, an edit that changed nothing, TODAY() or NOW() in place of a year the request names, and (with LibreOffice) a formula the turn wrote that recalculates to an error. Each speaks once. | sb-374-31 went from 0 of 4 runs to 2 of 2 |
| Failed-check note | on | If the agent's own assert failed and no check passed after it, it is told the job is not done. | catches "my assertion was wrong" on a wrong file; no longer sends back a finished job |
| `MH_OFFICE_LOOK_CAP` | 2 | check_deck, check_workbook, check_document and view_output run at most twice per request. | pptx_chart ran check_deck 3-5 times a run |
| `MH_OFFICE_VERIFY_CAP` | 4 | Once the file is saved and 4 checks in a row pass, the agent is told to answer. | xlsx_budget took 41 calls in one run where Codex took 5-10 |

## Office results

Since 0.0.17, an office edit or a file the user names in a Work chat sends Codex 0.171's own
request, the same one code chats send: its prompt and its one command tool, with none of Meshpool's office notes,
checks or office tools (`MH_WORK_OFFICE_STYLE=c171`, the default; `=lean` keeps the 0.0.16 route). A new deck, report
or model and Word tracked changes keep the engines. Raw rows in `docs/results/office_c171/`.

| Suite | Solved, Meshpool vs Codex | Cost per solved vs Codex (95% CI) | Median time vs Codex |
|---|---|---|---|
| Office-7 (decks, documents, workbooks), 7 x 6 | 41/42 vs 41/42 | 0.89x (0.81-0.95) | 0.53x |
| SpreadsheetBench, same hour as Codex, 6 x 6 each | 33/36 vs 33/36 | 0.89x (0.78-1.03) | 1.22x |
| OfficeBench, same hour as Codex, 4 x 6 each | 24/24 vs 22/24 | 1.02x (0.63-1.31) | 1.07x |

Office-7 now solves as many tasks as Codex at 0.89x its cost, SpreadsheetBench, run side by side with Codex in the
same hour, as many tasks at 0.89x, and OfficeBench, side by side, 24 of 24 against Codex's 22 at about its cost
(1.02x). OfficeBench's last loss, ob-1-15-2 ("add the reference from reference.txt to the end of the document", 8 of 12
before), is fixed by one pre-answer check that also runs on Codex's request: the named file's text must be in the
document's paragraphs as the file gives it, not inside a content control and not reworded (12 of 12 with it; on the 239
saved office runs it fires on the 4 failures only). Side by side, SpreadsheetBench and OfficeBench took 1.22x and 1.07x
Codex's median time; against Codex's earlier runs (same settings) the times were 0.48-0.76x, so time here moves with
the hour more than with the route.

0.0.16, on the same suites: Office-7 42/42 at 0.94x, SpreadsheetBench 31/36 at 1.04x (0.90x on a later run), OfficeBench
21/24 at 0.83x. Why the route helps: a request that is exactly Codex's gets Codex's results, and Meshpool's notes on an
office request had told the model to check with tools it did not have (pdf_report: 42 calls searching the disk for
`view_output`, against Codex's 10).

## What did not save money

Measured and dropped or kept off:
- Meshpool's earlier full router and reviewer layers: 3.3x Codex's cost on v2_dev (dev6, "old router").
- The lean prompt on large repos: 1.86x on swe_sympy (an edit, run the slow tests, edit again loop).
- Recalculating workbooks through LibreOffice to check a formula change: one run spent 36 of 59 calls on it. A formula
  change is now checked by reading the formula text.

## Caveats

- v2_eval and v2_dev were used while shaping the prompts; the other suites were not.
- The gateway bills about 2x the list price for this model. The ratios above do not depend on the price level,
  because both agents pay the same prices.
- Office suites are small (7, 6 and 4 tasks, six runs each), so their intervals are wide.
- Until the last SpreadsheetBench run the benchmark ran Meshpool from source without LibreOffice, which the app
  bundles, so LibreOffice-based checks were not part of the Office-7 and OfficeBench runs.
- The office run was measured before the last review and security fixes (permissions, the app's page token,
  bounded file checks).
