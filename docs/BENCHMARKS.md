<img src="../app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Benchmarks

Meshpool Auto uses the results on this page to choose a harness for each task (see [ROUTING.md](ROUTING.md)).

**Which harness the numbers describe.** Run C below measured the Meshpool Harness v3 *benchmark loop* (the one-shot
engine). Meshpool Code and Meshpool Work in the app run the app's own chat agent, which shares that loop's tools but
not its loop. Since 12 November 2026 the app agent is measured directly (next section).

## Office work against Codex and Pi (23 November 2026)

Seven office tasks a user would type (edit a workbook in place, tracked changes in Word, invoices in three
currencies to Excel, mail-merge letters, a live-formula budget, a deck with a native chart, a PDF report with a
chart), graded by file content only: formulas recalculated in LibreOffice, chart data compared with the CSV, tracked
changes accepted and rejected, the PDF must contain a chart. Every agent ran on the same model (DeepSeek V4.1 Flash)
through one metering proxy that counts every request, including Meshpool's nested engine and reviewer calls. One run
per agent, so a single pass or fail can move; costs are estimates from one price table
(`testkit/bench3/prices.json`). Kit: `testkit/bench3` (one command, `run.py`); raw rows in `docs/results/v4/`.

| Agent | 0.0.12 passed | 0.0.12 $ per task | Now passed | Now $ per task | Now calls (7 tasks) | Now s per task |
|---|---|---|---|---|---|---|
| Pi | 7/7 | $0.0065 | 6/7 | $0.0061 | 65 | 35 |
| Codex CLI | 7/7 | $0.0102 | 7/7 | $0.0113 | 84 | 45 |
| Meshpool on Codex (the app driving Codex) | 5/7 | $0.033 | 6/7 | $0.025 | 185 | 72 |
| Meshpool on Pi (the app driving Pi) | 4/7 | $0.040 | 3/7 | $0.030 | 196 | 199 |
| Meshpool CLI (affected, see below) | 4/7 | $0.056 | 4/7 | $0.055 | 337 | 182 |
| Meshpool Work (affected, see below) | 5/7 | $0.056 | 6/7 | $0.154 | 874 | 396 |

**Meshpool is not cheaper or faster than Codex or Pi on office work yet.** Pi is the cheapest and Codex the most
reliable. The best Meshpool option, Meshpool on Codex, costs about 2.2x Codex and 4x Pi per task.

What changed since 0.0.12, and what it did:
- The Meshpool layer on top of Pi and Codex is much smaller per request (measured on a fake endpoint, first request
  of the workbook edit): on Pi 23.6k to 3.5k tokens (vanilla Pi 1.4k), on Codex 17.1k to 11.6k (vanilla Codex
  10.2k), and the extra Codex session that only wrote the chat title is gone. Meshpool on Codex is now 25% cheaper
  and 15% faster than in 0.0.12. Details: `testkit/bench3/profile/FINDINGS.md`.
- Editing an existing file no longer launches the spreadsheet engine. Meshpool Work's workbook edit went from 68
  calls and $0.058 to 16 calls and $0.019; letters and invoices are also close to Codex now.
- Known regression in this run (affects the Meshpool Work and Meshpool CLI rows, which run Meshpool's own loop): on three tasks (tracked changes, chart deck, PDF report) Meshpool Work fell into a
  loop where the context is summarized after every step and the agent repeats its last check (about 100 times, two
  runs hit the 15-minute limit). That loop alone explains Meshpool Work's cost in the table; it is being fixed in the
  agent's context handling and the table will be re-measured.
- Quality: Meshpool on Pi failed tracked changes (an extra edit outside the requested ones), invoices (extra rows),
  the budget (values, not formulas linked to the assumptions) and the PDF report (time limit). Pi failed the budget
  once in this run.

**Re-run after the compaction fix (24 November 2026, same tasks and model, one run).** The summarize-every-step loop
is gone. Meshpool CLI 6/7 at $0.036 and 108 s per task; Meshpool Work 6/7 at $0.093 and 194 s; Meshpool on Codex 4/7
at $0.030 and 103 s; Meshpool on Pi 5/7 at $0.049 and 139 s (Codex 7/7 at $0.011 and 45 s, Pi 6/7 at $0.006 and
35 s, from the run above). Still more expensive and slower than both. What still costs more: Meshpool's own loop
wrongly concludes the model's context window is 9k-50k tokens and shrinks its context, after which it repeats work
(chart deck 229 calls, PDF report 209); and it has no brake on near-identical repeated edits (one CLI run appended a
line to a scratch file 122 times). Both are being fixed in the agent's context handling. The default Work agent stays
Meshpool Work: it is the only app option at 6/7. Raw rows: `docs/results/v4/fix-8dc9f501-office/`.

Default agent: on these numbers the cheapest agent that passes is vanilla Pi or Codex; Meshpool keeps its own loop as
the Work default only until a re-measure shows it at or under Codex. The decision is recorded in
`hrouter` `builtin_for()` when the re-measure is done.

Coding sanity check (3 Exercism tasks: Python forth and zipper, Rust react with every test including ignored ones,
GPT-6 Luna, one run): Pi 3/3 at $0.0025 and 32 s per task, Meshpool on Pi 3/3 at $0.0041 and 32 s, Meshpool Code
3/3 at $0.0051 and 45 s, Codex 2/3 at $0.0063 and 54 s, Meshpool on Codex 2/3 at $0.0050. The full coding
comparison is the 52-task evaluation below.

## App agent, measured directly (12 November 2026, dev tasks)

Every agent ran at the same time on DeepSeek V4.1 Flash through the Meshpool gateway, with the same task text, caps
and graders. These are dev tasks and one run each, so a result can move by about two tasks; the frozen test tasks
confirm before any claim.

SWE-bench Verified, 9 dev tasks:

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Pi | 8/9 | $0.0072 | 108 s |
| Codex CLI | 7/9 | $0.0065 | 84 s |
| Meshpool Code (app agent) | 7/9 | $0.0084 | 107 s |
| Meshpool Harness v5 (engine loop) | 7/9 | $0.0104 | 137 s |

Computer use, MiniWoB++, 10 dev tasks (second round; the browser tool with the fixes the app now uses):

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v5 | 9/10 | $0.0009 | 18.7 s |
| Pi | 9/10 | $0.0014 | 22.5 s |
| Codex CLI | 9/10 | $0.0025 | 23.3 s |

Terminal-Bench 2.0, 4 dev tasks: v5 3/4, Meshpool Code 2/4, Codex 1/4, Pi 1/4. Codex and Pi crashed on two tasks
when the gateway's content filter rejected hex output. Four tasks is too few to rank on; the rest was cut for budget.

## App agent on the frozen test tasks (13 November 2026)

SWE-bench Verified, 20 frozen test tasks, all agents at the same time, one run each:

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Pi | 19/20 | $0.0069 | 95 s |
| Meshpool Code (app agent) | 18/20 | $0.0079 | 133 s |
| Codex CLI | 17/20 | $0.0101 | 121 s |
| Meshpool Harness v5 (engine loop) | 17/20 | $0.0093 | 184 s |

Codex reported no usage on 2 of 20 runs; its cost is scaled from the other 18. Meshpool Code beats Codex on solves
and cost but not time. Pi leads on all three. The gap to Pi (one task, $0.001 per solved) is inside run-to-run noise
but it is in Pi's favour, so we do not claim a lead.

## Code tools A/B (13 November 2026, 10 Verified dev tasks)

`MH_OBSERVE` adds `probe` and `trace`; "x" arms added `MH_NAV` and `MH_LINT_EDIT` too (both since removed: they added calls without adding solves).

| Arm | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Code (app agent) | 10/10 | $0.0069 | 98 s |
| app + all three tools | 9/10 | $0.0089 | 125 s |
| v5 (engine loop) | 6/10 | $0.0294 | 235 s |
| v5 + observe | 9/10 | $0.0180 | 158 s |
| v5 + all three tools | 7/10 | $0.0278 | 244 s |

Observe helped the engine loop (+3 tasks, 39% cheaper per solve). It did not help the app agent, which already
solved all 10. Navigation and the edit check added calls without adding solves. All three stay off until observe is
confirmed on test tasks.

`MH_ERROR_CONTEXT=1` (not yet measured): when a command fails, its output gets the project lines it points to
(Python tracebacks, pytest, gcc/clang, rustc, tsc, Ruff, mypy, Pyright, Node stacks), marked and counted as a read.
It adds no tool and no call; it targets the 9.7 re-reads per task after the first edit. Off until an A/B on test tasks
shows fewer calls or more solves.

## How the agents were measured

Every agent ran under the same conditions:

- the same model, DeepSeek V4.1 Flash, through the Meshpool gateway;
- the same tasks, caps and graders;
- the same hours (run C, 12 November 2026), because gateway caching and latency change by the hour.

Cost is billed cost per solved task. Time is total wall time divided by the number of solved tasks. Ranges are 95% confidence intervals.

## SWE-bench Verified: 20 tasks every agent finished

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v3 | 18/20 (70-97%) | $0.039 | 6.9 min |
| Codex CLI | 17/20 (64-95%) | $0.026 | 2.6 min |
| Claude Code | 20/20 (84-100%) | $0.036 | 5.7 min |
| Plain DeepSeek (mini-SWE-agent) | 20/20 (84-100%) | $0.027 | 7.5 min |

## Terminal-Bench 2.0: 15 tasks every agent finished

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v3 | 6/15 (20-64%) | $0.182 | 29.1 min |
| Codex CLI | 8/15 (30-75%) | $0.060 | 14.8 min |
| Claude Code | 4/15 (11-52%) | $0.380 | 42.2 min |
| Plain DeepSeek (mini-SWE-agent) | 7/15 (25-70%) | $0.210 | 41.9 min |

## Computer use: MiniWoB++, 102 runs (rounds 10 to 12)

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v3 | 97/102 (89-98%) | $0.0027 | 17.4 s |
| Claude Code | 97/102 (89-98%) | $0.0082 | 21.4 s |
| Plain DeepSeek (mini-SWE-agent) | 101/102 (95-100%) | $0.0026 | 29.0 s |

## Engine loop history

The engine loop measured in run C (labelled v3 above) replaced the pre-release loop after solving as many or more
tasks for less in a same-hours comparison. Its additions (bash_many, todo follow-through, loop and stall breakers, a
step budget note, an environment snapshot) are also in the app agent; `MH_AGENT_V3=0` turns them off there.

## What is not measured yet

- **Pi, Hermes and the office work:** they were not in run C. Their routing scores are estimates, set below the measured harnesses.
- **The subscription harnesses:** in run C, Claude Code and Codex used DeepSeek V4.1 Flash. On your subscription they use Anthropic's and OpenAI's own models.

## Command line

`meshpool run` turns on the benchmarked flag set (run C) for code and browser tasks. Explicit `MH_*` settings always win.
