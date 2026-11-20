# MeshPool: all benchmark results in one place

Compiled 20 November 2026 from the result files in this repository. Every number below is copied from the file
cited next to it. Nothing was re-run for this page. Where two files disagree, both numbers are shown and the newer
file is named.

## Headline

- **Current default engine.** MeshPool 0.0.7's default is the "Codex-design engine" (`profile:codex`). On the 52-task
  coding eval it solved **47/52**, at **$0.0034 per task**, with a **19 s median** and 10.7 model calls per task.
  The model was DeepSeek V4.1 Flash, with one run per task. Source: `v3/CODEX_ENGINE_EVAL.md`.
- **Against Codex (the rerun reported in the same file, run earlier the same day in a separate batch: 47/52, $0.0030
  per task, 21 s median): a tie on quality, cost and time.** Each side
  solved 4 tasks the other missed (p=1.0). Cost ratio 0.94x (0.72-1.22); time ratio 1.10x (0.73-1.69). In an earlier
  batch Codex solved 50/52 (`v3/EVAL52_FINAL.md`); `CODEX_ENGINE_EVAL.md` is the newer file.
- **Against OpenCode (48/52), Pi (48/52) and Hermes (49/52, 1 error): no paired comparison with the current engine
  exists.** Their numbers come from the earlier batch: $0.0047 / $0.0052 / $0.0049 per task, with medians of
  24.6 / 23.1 / 46.6 s. Run-to-run noise is about +/-3 solved tasks, so gaps of 1-4 tasks are not meaningful.
- **Against the engine 0.0.7 used before (46/52, $0.0069 per task, 23 s median):** the current engine is
  significantly faster, 0.49x the time (0.30-0.79). It also costs 0.63x as much, but that interval (0.32-1.34)
  includes "no difference".
- **Bottom line.** On quality, the current engine is a statistical tie with every harness measured. Cost and speed
  against Codex are also a tie. A significant cost advantage was measured only for **older** MeshPool versions:
  - V2 on the 24-task hard subset and on DABench;
  - the "routed" arm in the 18/19 November loops.

  MeshPool V2 was also significantly faster on data analysis (DABench).
- **Cost caveat.** The 52-task dollar figures are token estimates at list prices; billed spend ran about 2x. Compare
  ratios, not absolute dollars.
- **Not measured yet:**
  - SWE-bench Verified, Terminal-Bench and MiniWoB++ have not been run on the 0.0.7 engine. The 13 November
    SWE-bench numbers "were for the engine before 0.0.7" (`app/defaults/routing.json`).
  - Research, documents/office, spreadsheet and sales (GTM) tasks have no measurement for any agent.
  - Everything was run on a single model, DeepSeek V4.1 Flash. On your subscription, Claude Code and Codex use
    Anthropic's and OpenAI's own models, which were not tested.

### Names used in the sources (they are easy to mix up)

| Name in a source | What it is |
|---|---|
| Codex-design engine / `profile:codex` | Current 0.0.7 default: MeshPool's agent with Codex's base prompt, tools and loop, plus MeshPool's loop guard, compaction and routing text |
| MeshPool 0.0.7 (in `EVAL52_FINAL.md`) / "previous assignment" | The profile 0.0.7 used before the Codex-design engine replaced it. Superseded |
| Meshpool V1 / V2 / V3 (v2 and v3 reports) | Successive versions of the harness. V1 = frozen commit `b52c31c`; V2 = evaluated at `69e44e9` |
| Meshpool Harness v3 (run C), v5 "engine loop" (`BENCHMARKS.md`) | The one-shot benchmark loop. A different numbering from V1/V2/V3 above |
| Meshpool Code (app agent) | The chat agent inside the app. Shares the engine loop's tools but not its loop |
| routed (18/19 Nov loops), codex-routed (scorecard) | MeshPool's own arm, which sorts each task into a method (routes such as `code.debug`); Codex with MeshPool routing ("Codex + MeshPool routing") |

**Cost basis differs by file, so do not compare dollars across sections.**
- Run C: billed cost per solved task.
- 52-task eval and CODEX_ENGINE_EVAL: token estimates (billed about 2x).
- Broad 5-harness loop: token estimates.
- SWE sympy loop: billed spend split by tokens.

## 1. Coding: the 52-task V2 eval

**What was run.** 52 coding tasks in 8 categories: algorithmic, ambiguous, data, debugging, multi-file, performance,
refactoring and test-engineering. Each task has hidden tests and a reference solution. Test-engineering tasks are
graded by mutation testing and data tasks by exact answers. No LLM judge is used. The model was DeepSeek V4.1 Flash,
with one run per task. The v3 files do not state a date. The task set was written by the MeshPool developer
(`v2/V2_REPORT.md`).

| Arm | Solved | $ / task | Median s | Calls / task | Source |
|---|---|---|---|---|---|
| **MeshPool, Codex-design engine (current default)** | **47/52** | **$0.0034** | **19** | 10.7 | CODEX_ENGINE_EVAL |
| Codex (rerun, same day) | 47/52 | $0.0030 | 21 | 7.4 | CODEX_ENGINE_EVAL |
| Codex + MeshPool routing | 46/52 | $0.0033 | 28 | 8.5 | CODEX_ENGINE_EVAL |
| MeshPool 0.0.7, previous assignment (superseded) | 46/52 | $0.0069 | 23.3 | 13.5 | EVAL52_FINAL |
| Meshpool V3 | 47/52 | $0.0107 | 50.6 | 21.0 | EVAL52_V3 |
| Meshpool V2 | 47/52 | $0.0054 | 26.2 | 16.0 | EVAL52_FINAL / V3 |
| Meshpool V1 | 47/52 | $0.0053 | 33.5 | 15.5 | EVAL52_FINAL / V3 |
| Codex (earlier batch) | 50/52 | $0.0038 | 22.2 | 8.2 | EVAL52_FINAL / V3 |
| OpenCode | 48/52 | $0.0047 | 24.6 | 7.1 | EVAL52_FINAL / V3 |
| Pi | 48/52 | $0.0052 | 23.1 | 8.3 | EVAL52_FINAL / V3 |
| Hermes (1 error) | 49/52 | $0.0049 | 46.6 | 8.6 | EVAL52_FINAL / V3 |

EVAL52 "$ / task" figures are per attempted task; CODEX_ENGINE_EVAL labels its column only "$ / task". The 95% intervals on solve rate are wide:
- 46/52 is 88% (77%-95%);
- 47/52 is 90% (79%-96%);
- 50/52 is 96% (87%-99%).

**Paired comparisons.** Quality uses McNemar's exact test. Cost and time use bootstrap ratios over tasks both sides
solved; below 1 means MeshPool is cheaper or faster.

| MeshPool arm | vs | Only MeshPool / only them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Codex-design engine | Codex (rerun, same day) | 4 / 4 | tie (p=1.0) | 0.94x (0.72-1.22), tie | 1.10x (0.73-1.69), tie |
| Codex-design engine | previous assignment | not stated | not stated | 0.63x (0.32-1.34) | 0.49x (0.30-0.79), faster |
| previous assignment | Codex | 2 / 6 | tie (p=0.29) | same 1.41x (0.77-2.39) | slower 2.02x (1.29-2.95) |
| previous assignment | OpenCode | 4 / 6 | tie (p=0.75) | same 1.06x (0.56-1.85) | slower 2.27x (1.44-3.24) |
| previous assignment | Pi | 3 / 5 | tie (p=0.73) | same 0.97x (0.53-1.71) | slower 1.65x (1.12-2.50) |
| previous assignment | Hermes | 3 / 6 | tie (p=0.51) | same 0.98x (0.53-1.77) | same 0.91x (0.62-1.32) |
| Meshpool V3 | Codex | 2 / 5 | tie (p=0.45) | costlier 3.15x (1.70-5.07) | slower 3.54x (2.44-5.08) |
| Meshpool V3 | V2 | 4 / 4 | tie | costlier 2.54x (1.38-4.17) | slower 2.29x (1.48-3.50) |

V3 was also costlier (2.20x-2.50x) and slower (1.49x-4.07x) than OpenCode, Pi and Hermes, while tying on quality.
Source: `v3/EVAL52_V3.md`.

The superseded 0.0.7 assignment lost tasks in multi-file (6/8), test-engineering (4/6), algorithmic (7/8) and
refactoring (5/6). Its test-engineering cost was $0.0391 per task, against $0.0137 (V2), $0.0159 (V1) and
$0.0050-$0.0078 for Codex, OpenCode, Pi and Hermes. Source: `v3/EVAL52_FINAL.md`.

**Decision recorded in the source.** The Codex-design engine became the default profile for every route in 0.0.7.
The harness router (MH_HARNESS_ROUTER) stays off. On the dev suite Codex+routing led Codex 22 vs 19, but on the eval
it tied (46 vs 47).

**Caveats, as stated in the sources.**
- EVAL52_FINAL: "The competitor arms ran in one batch earlier the same day; MeshPool 0.0.7 ran afterwards, alone,
  while other work … shared the 4-core machine, so its timings are pessimistic."
- "Dollar figures are token estimates at list prices; billed spend ran about 2x those numbers, so compare ratios,
  not absolute dollars."
- "One run per task: run-to-run noise on these suites is about +/-3 solved tasks, so differences of 1-4 tasks are not
  meaningful."
- CODEX_ENGINE_EVAL: "the arms did not all run in the same batch".

## 2. Dev suite: ablations, profile selection, scorecard

These runs used the 24 dev tasks, which are never the eval tasks. The model was DeepSeek V4.1 Flash, with one run per
task and grading by hidden tests. No date is stated in the files. **They chose settings that were later replaced:**
the current default is the Codex-design engine (section 1), not the profiles picked here.

**Ablation.** Source: `v3/ABLATION_DEV.md`. All arms ran on snapshot c50f120, uncontended.

| Arm | What it is | Solved | $ / task | Median s | Calls / task |
|---|---|---|---|---|---|
| noplan | V3.2 with plan mode off | 22/24 | $0.0075 | 68 | 22.3 |
| noreview | V3.2 with the review step off | 20/24 | $0.0090 | 80 | 19.6 |
| lean | V2 method texts, no plan/review/mutation, plus sprint fixes | 20/24 | $0.0093 | 43 | 18.4 |
| routed | V3.2, everything on | 18/24 | $0.0105 | 74 | 22.8 |
| v3 | V3 as evaluated on the 52 eval tasks | 18/24 | $0.0111 | 80 | 25.0 |
| v2 | before this sprint | 17/24 | $0.0068 | 50 | 23.3 |
| oldmethods | V3.2 with V2's method texts | 17/24 | $0.0099 | 53 | 17.4 |

Caveat from the source: "None of the quality gaps is significant at this sample size; the direction is consistent:
plan mode costs solves and money." For example, noplan vs v2 was 7 vs 2 (p=0.18). Decision: plan mode off by
default.

**Profile selection.** Source: `v3/PROFILE_SELECTION.md`. The default was set to **lean-stop**. Three groups got
their own profile:
- algorithm tasks, full-noplan-stop: tied on solved (2) and cost 38% less;
- test tasks, full-noplan: 3 solved vs 2;
- data, full-noplan: 2 solved vs 1.

All groups have only 3 to 9 tasks. **Superseded:** CODEX_ENGINE_EVAL later made the Codex-design engine the default
for every route.

**Scorecard: tasks solved per category.** Source: `v3/SCORECARD_DEV.md`. The model was DeepSeek V4.1 Flash. "lean",
"fnp" and "fnps" are the meshpool:lean-stop, full-noplan and full-noplan-stop profiles. The totals row was summed
from the file's rows. Codex-routed 22 and Codex 19 match the "22 vs 19" quoted in CODEX_ENGINE_EVAL.

| Category (tasks) | lean | fnp | fnps | codex-routed | codex | opencode | pi | hermes |
|---|---|---|---|---|---|---|---|---|
| ambiguous-build (2) | 2 | 2 | 2 | 2 | 1 | 1 | 1 | 1 |
| code.build (3) | 3 | 3 | 2 | 3 | 3 | 3 | 2 | 3 |
| code.build.algorithm (3) | 2 | 1 | 2 | 3 | 3 | 3 | 3 | 2 |
| code.build.feature (1) | 1 | 1 | 1 | 1 | 1 | 0 | 1 | 1 |
| code.debug (3) | 3 | 3 | 3 | 3 | 2 | 2 | 2 | 3 |
| code.debug.perf (3) | 3 | 2 | 2 | 3 | 3 | 3 | 3 | 1 |
| code.refactor (3) | 2 | 2 | 2 | 3 | 2 | 2 | 3 | 3 |
| code.test (3) | 2 | 3 | 2 | 2 | 1 | 2 | 1 | 1 |
| data (3) | 1 | 2 | 2 | 2 | 3 | 3 | 2 | 1 |
| **Total (24)** | 19 | 19 | 18 | 22 | 19 | 19 | 18 | 16 |

With 1 to 3 tasks per category, a single task changes a category's winner.

## 3. SWE-bench Verified (real GitHub issues)

All of these runs measure engines from **before 0.0.7**. The model was DeepSeek V4.1 Flash through the Meshpool
gateway, with every agent run at the same time. Source: `docs/BENCHMARKS.md`.

| Run | Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|---|
| 13 Nov, 20 frozen test tasks | Pi | 19/20 | $0.0069 | 95 s |
| | Meshpool Code (app agent) | 18/20 | $0.0079 | 133 s |
| | Codex CLI | 17/20 | $0.0101 | 121 s |
| | Meshpool Harness v5 (engine loop) | 17/20 | $0.0093 | 184 s |
| 12 Nov, 9 dev tasks | Pi | 8/9 | $0.0072 | 108 s |
| | Codex CLI | 7/9 | $0.0065 | 84 s |
| | Meshpool Code (app agent) | 7/9 | $0.0084 | 107 s |
| | Meshpool Harness v5 | 7/9 | $0.0104 | 137 s |
| Run C (12 Nov), 20 tasks | Claude Code | 20/20 (84-100%) | $0.036 | 5.7 min |
| | Plain DeepSeek (mini-SWE-agent) | 20/20 (84-100%) | $0.027 | 7.5 min |
| | Meshpool Harness v3 | 18/20 (70-97%) | $0.039 | 6.9 min |
| | Codex CLI | 17/20 (64-95%) | $0.026 | 2.6 min |

**Code tools A/B.** 13 Nov, 10 Verified dev tasks.

| Arm | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Code (app agent) | 10/10 | $0.0069 | 98 s |
| app + all three tools | 9/10 | $0.0089 | 125 s |
| v5 (engine loop) | 6/10 | $0.0294 | 235 s |
| v5 + observe | 9/10 | $0.0180 | 158 s |
| v5 + all three tools | 7/10 | $0.0278 | 244 s |

**Caveats, as stated in the source.**
- 13 Nov: "Codex reported no usage on 2 of 20 runs; its cost is scaled from the other 18." "The gap to Pi (one task,
  $0.001 per solved) is inside run-to-run noise but it is in Pi's favour, so we do not claim a lead."
- 12 Nov dev: "a result can move by about two tasks".
- A/B: observe helped the engine loop (+3 tasks, 39% cheaper per solve) but not the app agent. All three tools stay
  off until observe is confirmed on test tasks.
- Run C ranges are 95% confidence intervals; cost is billed cost.

## 4. Terminal-Bench 2.0 (command-line tasks)

Source: `docs/BENCHMARKS.md`. Model: DeepSeek V4.1 Flash. All runs were before 0.0.7.

| Run | Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|---|
| Run C (12 Nov), 15 tasks | Codex CLI | 8/15 (30-75%) | $0.060 | 14.8 min |
| | Plain DeepSeek (mini-SWE-agent) | 7/15 (25-70%) | $0.210 | 41.9 min |
| | Meshpool Harness v3 | 6/15 (20-64%) | $0.182 | 29.1 min |
| | Claude Code | 4/15 (11-52%) | $0.380 | 42.2 min |
| 12 Nov dev, 4 tasks | v5 3/4, Meshpool Code 2/4, Codex 1/4, Pi 1/4 | | | |

Caveat from the source: on the 4 dev tasks, "Codex and Pi crashed on two tasks when the gateway's content filter
rejected hex output. Four tasks is too few to rank on; the rest was cut for budget."

## 5. Computer use: MiniWoB++ (browser tasks)

Source: `docs/BENCHMARKS.md`. Model: DeepSeek V4.1 Flash. All runs were before 0.0.7.

| Run | Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|---|
| Run C, 102 runs (rounds 10-12) | Plain DeepSeek (mini-SWE-agent) | 101/102 (95-100%) | $0.0026 | 29.0 s |
| | Meshpool Harness v3 | 97/102 (89-98%) | $0.0027 | 17.4 s |
| | Claude Code | 97/102 (89-98%) | $0.0082 | 21.4 s |
| 12 Nov dev, 10 tasks (second round) | Meshpool Harness v5 | 9/10 | $0.0009 | 18.7 s |
| | Pi | 9/10 | $0.0014 | 22.5 s |
| | Codex CLI | 9/10 | $0.0025 | 23.3 s |

Caveat: the 10-task round is dev tasks with one run each. Run C's MiniWoB++ table has no Pi or Codex row.

## 6. Data analysis: InfiAgent-DABench (public benchmark)

**What was run.** 12 medium and hard questions were sampled and 11 were completed by every harness. Grading used the
official closed-form grader. The model was DeepSeek V4.1 Flash, with one run each, on 19 November 2026. This measures
Meshpool **V2**, not 0.0.7. The same data appears in `v2/V2_REPORT.md` and `v2/dabench12_report.md`.

| Harness | Solved | $ / solved | Median s | Cost vs V2's (paired) | Time vs V2's (paired) |
|---|---|---|---|---|---|
| Meshpool V2 | 10/11 | $0.0013 | 7.1 | n/a | n/a |
| Meshpool V1 | 10/11 | $0.0014 | 8.0 | V2 same 0.94x (0.83-1.03) | same 0.79x (0.46-1.34) |
| Codex | 10/11 | $0.0031 | 11.5 | V2 cheaper 0.41x (0.38-0.47) | faster 0.38x (0.23-0.72) |
| OpenCode | 10/11 | $0.0147 | 14.7 | V2 cheaper 0.09x (0.07-0.11) | faster 0.47x (0.32-0.73) |
| Pi | 10/11 | $0.0129 | 15.6 | V2 cheaper 0.10x (0.09-0.11) | faster 0.61x (0.45-0.81) |
| Hermes | 10/11 | $0.0051 | 21.2 | V2 cheaper 0.26x (0.22-0.32) | faster 0.26x (0.19-0.44) |

Every harness missed the same question (dab-137), so quality is a tie.

Caveats from the source:
- Only 11 questions were completed by every harness.
- DABench "is easy (every harness solves almost everything)".

## 7. V2 eval: hard 24-task subset

**What was run.** 24 of the 52 eval tasks, 3 per category (stratified). The model was DeepSeek V4.1 Flash, with one
run each, on 19 November 2026, graded by hidden tests. Sources: `v2/V2_REPORT.md` and `v2/v2eval24_report.md` (same
data). The full 52-task run in section 1 is newer and larger. On it, V2 solved 47/52.

| Harness | Solved | $ / solved | Median s | V2 vs them: only V2 / only them | Cost ratio | Time ratio |
|---|---|---|---|---|---|---|
| Meshpool V2 | 19/24 | $0.0044 | 42.1 | n/a | n/a | n/a |
| Meshpool V1 | 21/24 | $0.0054 | 52.4 | 3 / 5, tie (p=0.73) | cheaper 0.59x (0.42-0.91) | same 0.89x (0.58-1.34) |
| Codex | 24/24 | $0.0039 | 24.1 | 0 / 5, tie (p=0.06) | cheaper 0.75x (0.59-0.94) | same 1.23x (0.59-2.52) |
| OpenCode | 22/24 | $0.0147 | 26.9 | 1 / 4, tie (p=0.38) | cheaper 0.24x (0.17-0.33) | same 0.81x (0.45-2.05) |
| Pi | 23/24 | $0.0116 | 32.3 | 1 / 5, tie (p=0.22) | cheaper 0.25x (0.19-0.33) | same 1.19x (0.80-1.66) |
| Hermes | 22/24 | $0.0077 | 36.8 | 2 / 5, tie (p=0.45) | cheaper 0.43x (0.29-0.62) | same 0.85x (0.62-1.36) |

The source's verdict was "promising but unproven". Cost was significantly lower against every harness. Quality on hard
coding was not better: V2 solved the fewest (19/24), though no gap was significant.

The report found five reasons V2 lost tasks:
- misrouting a bug fix to the Documents harness (fixed afterwards, not re-measured);
- self-testing churn on build-from-spec tasks;
- 8 of 12 edit calls failing on those tasks;
- a quick, wrong data answer;
- one 600 s timeout.

Caveats from the source:
- "Hard V2: 24 of the 52 evaluation tasks, one run each."
- "The V2 suite was written by me (the MeshPool developer)."
- "Only one model."
- "Performance tasks are timing-based and ran with up to 11 concurrent agents, so they can be noisy."
- Not run: SpreadsheetBench (the LibreOffice install failed), OfficeBench (the adapter is not built) and full
  SWE-bench Verified.
- Sprint spend was $10.02 of a $25 cap.

## 8. Broad 5-harness loop and SWE sympy loop (18/19 November)

Here "routed" is MeshPool choosing its method per task. Both loops were run by every arm, used the same model and
hour for each pair, and were graded by code. Both files carry the header "Harness loop: 2026-11-19 06:41 UTC".
The broad file is named 2026-11-18.

**Broad loop: 42 tasks x 1 repeat.** Suites: build, chat, data, debug, deep, hard. Source:
`2026-11-18-broad-5-harnesses.md`. The model column in the file reads "-". Cost is "estimated from tokens at list
prices (no billed spend available)".

| Arm | Solved | $ per solved | s per solved | routed vs them: quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|---|
| routed | 42/42 | $0.0012 | 7.5 | n/a | n/a | n/a |
| codex | 42/42 | $0.0019 | 8.4 | tie (p=1.00) | cheaper 0.62x (0.51-0.74) | same 0.89x (0.70-1.10) |
| opencode | 42/42 | $0.0018 | 8.7 | tie (p=1.00) | cheaper 0.67x (0.54-0.81) | same 0.86x (0.67-1.07) |
| pi | 41/42 | $0.0036 | 10.1 | tie (p=1.00) | cheaper 0.34x (0.29-0.40) | faster 0.77x (0.57-0.99) |
| hermes | 42/42 | $0.0047 | 22.1 | tie (p=1.00) | cheaper 0.25x (0.20-0.30) | faster 0.34x (0.27-0.41) |

**SWE sympy loop: 12 SWE tasks (sympy) x 2 repeats.** Source: `2026-11-19-swe-sympy-5-harnesses.md`. Cost is "billed
key spend per model, split between arms by tokens: deepseek-v4.1-flash $2.6592".

| Arm | Solved | $ per solved | s per solved | routed vs them: quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|---|
| routed | 24/24 | $0.0101 | 828.8 | n/a | n/a | n/a |
| codex | 23/24 | $0.0164 | 865.7 | tie (p=1.00) | cheaper 0.63x (0.47-0.80) | same 0.96x (0.70-1.24) |
| opencode | 23/24 | $0.0282 | 70.1 | tie (p=1.00) | cheaper 0.36x (0.25-0.46) | same 1.07x (0.77-1.41) |
| pi (3 errors) | 24/24 | $0.0356 | 876.1 | tie (p=1.00) | cheaper 0.28x (0.21-0.38) | same 0.79x (0.59-1.11) |
| hermes | 24/24 | $0.0224 | 1615.7 | tie (p=1.00) | cheaper 0.45x (0.32-0.59) | faster 0.69x (0.47-0.93) |

Caveats from the sources:
- Intervals are 95%, and "a difference counts only when it is beyond chance".
- Flaky tasks (solved in some repeats only): codex sympy__sympy-23950 1/2; opencode sympy__sympy-23413 1/2.
- These measure the routed harness of that week, not the 0.0.7 Codex-design engine.

## 9. Raw early runs (`docs/results/v1-raw/`)

These are per-run records with no write-up, so the counts below were taken directly from the files.
- `swe_h2h` is an earlier single-repeat round of the 12 sympy tasks: routed 11/12, codex, hermes, opencode and pi
  12/12 each. This is consistent with V2_REPORT's note that the "SWE rerun solved the earlier miss".
- `swe_h2h2` matches the SWE sympy loop above.
- `loop1` to `loop3` are the only runs on other models: Claude Haiku 5.5, GLM 4.7, GPT-6 Luna, Kimi K3 and
  meshpool-balanced. They have no task or grader description, so they are not summarized as results here.
- `loop4` does not match the broad report's counts.

## 10. What Auto routes where and why (`app/defaults/routing.json`, version 4, 20 Nov 2026)

**How Auto picks.** Each category has a quality score from 0 to 100 per agent; where it was measured, the score is the
solve rate. Auto takes the best score among the agents that are installed and signed in. It treats every agent
within **5 points** of that best score (`quality_band`) as equally good. Among those, it picks the cheapest, then the
fastest. The file also lists a `mode_bonus`: in code mode, Meshpool Code +2 and Pi +1; in cowork mode, Meshpool Work
+3. The file does not say how the bonus is applied.

| Category | Scores (agent: score) | Likely pick if all agents are available | Measured or estimate |
|---|---|---|---|
| code | Hermes 94, Codex 93, Pi 92, Meshpool Code 90, Meshpool Work 40 | Meshpool Code (cheapest and fastest within the band) | Measured: 52-task eval (Meshpool Work's 40 is an estimate) |
| terminal | Codex 53, Meshpool Code 40, Pi 25, Hermes 20, Meshpool Work 10 | Codex | Codex and Meshpool Code measured (run C); Pi, Hermes and Meshpool Work are estimates |
| browser | Meshpool Work 95, Meshpool Code 95, Pi 90, Codex 90, Hermes 70 | Meshpool Work or Meshpool Code (same numbers) | Meshpool from run C (97/102); Pi and Codex from the 12 Nov 10-task round (9/10); Hermes is an estimate |
| research | Meshpool Work 80, Hermes 78, Codex 76, Meshpool Code 62, Pi 55 | no cost data; see note | All estimates |
| documents | Meshpool Work 85, Hermes 78, Codex 68, Meshpool Code 55, Pi 50 | Meshpool Work | All estimates |
| data | Meshpool Work 84, Hermes 74, Codex 72, Meshpool Code 70, Pi 60 | Meshpool Work | All estimates |
| gtm (sales) | Meshpool Work 82, Hermes 80, Codex 70, Meshpool Code 55, Pi 50 | no cost data; see note | All estimates |
| general | Meshpool Work 75, Hermes 74, Codex 72, Meshpool Code 70, Pi 60 | no cost data; see note | All estimates |

**Cost and time figures stored with the scores.** Costs are dollars per solved task.
- **code:** Meshpool Code $0.0038 / 19 s, Codex $0.004 / 22.2 s, Pi $0.0057 / 23.1 s, Hermes $0.0052 / 46.6 s.
- **terminal:** Codex $0.06 / 888 s, Meshpool Code $0.182 / 1746 s.
- **browser:** Meshpool Work and Meshpool Code $0.0009 / 18.7 s, Pi $0.0014 / 22.5 s, Codex $0.0025 / 23.3 s.
- **All other categories:** no cost or time is stored.

**Notes on the routing numbers.**
- The `source` field says that on code, "Codex 50/52 and 47/52 on a rerun" and Meshpool Code 47/52, Pi 48/52 and
  Hermes 49/52 are "all a tie within run-to-run noise (+/-3 tasks), so Auto takes the cheapest and fastest, Meshpool
  Code".
- Codex's code score of 93 matches neither 50/52 nor 47/52 directly. The file does not explain it.
- Meshpool Code's $0.0038 comes from the new Codex-design engine run. Codex's $0.004 and Pi's and Hermes's costs come
  from the earlier batch. So "cheapest" compares figures from different runs.
- The code "seconds" values are medians, not time per solved task.
- No cost is stored for research, gtm or general, and several agents fall inside the 5-point band there:
  - research: Meshpool Work 80, Hermes 78 and Codex 76;
  - gtm: Meshpool Work 82 and Hermes 80;
  - general: Meshpool Work 75, Hermes 74 and Codex 72, with Meshpool Code exactly 5 below at 70.

  The file says only that Meshpool Work is "set above Hermes". It does not say which agent Auto picks there.
- Research and GTM: "no measurement for any agent, so Meshpool Work (the bundled browser and web research tools,
  twice as fast as Hermes on the eval) is set above Hermes".
- "Every other score is an estimate set below the measured agents; Hermes and the office categories have no cost or
  time yet."

## PowerPoint in chat: before and after the 0.0.8 fix (20 Nov 2026)

What was run: `testkit/deck_matrix.py`. Each harness got a new chat, in Chat ("cowork") and in Code, whose first message
was "Make a 3-slide PowerPoint about the history of coffee and save it in this folder." Auto was also tested with a
deck request as the second message and with three other phrasings. The model was DeepSeek V4.1 Flash in auto mode,
with 3 chats at a time and 6 minutes per message (a deck turn still running got 5 more minutes, then was stopped).
A run passes when a .pptx is in the chat's folder and opens with python-pptx. Before: the pre-fix code (f8c5ac3).
After: the 0.0.8 branch (599b316). One run per cell.

| Harness | Chat: before | Chat: after | Code: before | Code: after |
|---|---|---|---|---|
| Meshpool Work | deck (278 s) | deck (turn 349 s) | deck, late (537 s) | deck (turn 447 s) |
| Meshpool Code | deck only by working around the missing tool (found the app source, called it from the shell) | deck (make_deck 5 times), turn did not end | **refused** ("office tools ... are not available in this session") | deck, but the first message ran before the office tools had started (158 shell calls); fixed in 2559090 |
| Pi | deck (76 s) | deck (turn 533 s) | deck (86 s) | deck (turn 435 s) |
| Hermes | **no deck**: make_deck failed in about 2 s every time | deck (turn 239 s) | **no deck**, same | deck (turn 561 s) |
| Auto (picked Meshpool Work) | deck | deck | deck | deck |
| Auto, deck request as the second message | deck (58 s) | deck (turn 316 s) | | |
| Auto, "can you create a ppt on coffee" | **no deck** (asked "what angle?" and stopped) | deck (12 slides) | | |
| Auto, "I need slides for a talk about coffee" | deck (12 slides), turn did not end | deck (14 slides), turn did not end | | |
| Auto, "make a deck about coffee" | deck (10 slides), turn did not end | deck (12 slides), turn did not end | | |

**Decks produced: 10 of 14 before (3 of them after the 6-minute window), 14 of 14 after.** No harness refused after
the fix. The script flagged three after-runs as refusals, but each one produced a deck; the flagged sentences were
caveats, for example "I cannot rule out Office offering a repair prompt".

What changed between the two runs:

| Problem in the before run | Fix (commit) |
|---|---|
| "can you create a ppt" was classed as a quick question, whose hidden note says "Do not create or change files" | Requests to make an office file are never quick (95efc72) |
| Meshpool Code had no office tools | Office server for Code chats, office tools in Codex style once a message asks for a file (95efc72) |
| Hermes: the office server lost the proxy and CA settings in Hermes' filtered environment, so the deck engine could not reach the model | Meshpool's servers carry those settings in their own env (c0be1e9) |
| Agents asked about the brief and stopped, or rebuilt a finished deck twice | The note with a file request: recommended defaults, at most one question, at most one rebuild (c0be1e9) |
| Web search had no ddgs in the bundled runtime (the after run had it installed, as 0.0.8 will) | ddgs bundled (599b316) |
| The first message of a new Code chat ran before its office tools had started | Office requests wait for them (2559090) |

**Re-run of Meshpool Code after 2559090: not done.** Both chats waited for the office tools as intended
("Starting the office tools...", then meshpool-office with 14 tools), but every model call was refused: the test key's
budget is used up (HTTP 429 "Budget has been exceeded ... Current cost: 23.10, Max budget: 0.0"). The fix is covered by
a unit test (`tests/test_capabilities.py::test_a_deck_request_waits_for_the_office_tools_to_start`); the real-model
re-run needs the key's budget raised.

**Caveats.**
- One run per cell, and 3 chats ran at once on one key, so times vary run to run. The deck engine takes about 2.5
  minutes for 3 slides and about 5.5 minutes for 12 under that load; a rebuild doubles it.
- The after run had working web search and the before run did not, so their times are not directly comparable.
- Gateway spend for the after run was about $5.43, most of it the Meshpool Code (Code) run that had no office tools
  at first (6.7M input tokens). The before run cost about $2.93.

