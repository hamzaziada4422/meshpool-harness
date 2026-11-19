# MeshPool V2: optimization sprint results

Date: 19 November 2026 · Model: **DeepSeek V4.1 Flash only** · Repo: `hamzaziada4422/meshpool-harness`, branch
`harness-router` · V1 = frozen commit `b52c31c` · V2 = the harness after this sprint (evaluated at `69e44e9`)

## Executive summary

| Question | Answer |
|---|---|
| Is MeshPool better at solving difficult tasks? | **No, not shown.** On the hard V2 subset (24 tasks), MeshPool V2 solved 19/24, the fewest of the six harnesses (Codex 24, Pi 23, OpenCode 22, Hermes 22, V1 21). None of the gaps is statistically significant (vs Codex p=0.06), but the direction is unfavourable. On DABench (public, data analysis) every harness solved 10/11: a tie. |
| Is MeshPool cheaper? | **Yes, statistically supported everywhere:** 0.24–0.75× the others on V2, 0.09–0.41× on DABench, and 0.59× V1 on V2. |
| Is MeshPool faster? | **On data analysis, yes** (DABench: 0.26–0.61× the others' time, significant). **On hard coding tasks, no:** a tie with every harness, and Codex's median time is lower (24 s vs 42 s). |
| Has quality improved without sacrificing cost efficiency? | **No.** Cost improved significantly against V1 (0.59×), but V2 solved 19/24 against V1's 21/24 (3 tasks only V2 solved, 5 only V1; p=0.73, a tie that leans negative). |
| Is the improvement statistically supported? | For **cost** yes; for **quality** no. |

**Verdict: promising but unproven.** The cost advantage is validated against every harness on two benchmarks, and
the speed advantage on data analysis. Quality on hard coding tasks is not better, and leans worse, on a small
sample. Stronger performance claims should wait for the full 52-task V2 evaluation and the fixes below.

## Results: hard V2 suite (24 of 52 evaluation tasks, 3 per category, stratified)

| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | Input tok / task | Output tok / task | Calls / task |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool V2 | 19/24 | 79% (60%–91%) | 0 | $0.0035 | $0.0044 | 42.1 | 80.1 | 186.7 | 105,087 | 2,630 | 13.4 |
| Meshpool V1 | 21/24 | 88% (69%–96%) | 0 | $0.0048 | $0.0054 | 52.4 | 72.1 | 169.2 | 124,752 | 4,034 | 14.0 |
| Codex | 24/24 | 100% (86%–100%) | 0 | $0.0039 | $0.0039 | 24.1 | 41.7 | 72.5 | 113,523 | 1,541 | 6.3 |
| OpenCode | 22/24 | 92% (74%–98%) | 0 | $0.0134 | $0.0147 | 26.9 | 66.2 | 172.1 | 286,702 | 1,410 | 7.3 |
| Pi | 23/24 | 96% (80%–99%) | 0 | $0.0111 | $0.0116 | 32.3 | 61.5 | 71.2 | 256,023 | 1,924 | 6.6 |
| Hermes | 22/24 | 92% (74%–98%) | 0 | $0.0070 | $0.0077 | 36.8 | 78.6 | 77.6 | 139,599 | 2,095 | 7.8 |

Meshpool V2 against each harness on the same tasks (quality: McNemar exact; cost and time: paired bootstrap 95% interval of the ratio, over tasks both solved):

| vs | Solved only by Meshpool V2 | Solved only by them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Meshpool V1 | 3 | 5 | tie (not significant, p=0.73) | cheaper 0.59× (0.42–0.91) | same speed 0.89× (0.58–1.34) |
| Codex | 0 | 5 | tie (not significant, p=0.06) | cheaper 0.75× (0.59–0.94) | same speed 1.23× (0.59–2.52) |
| OpenCode | 1 | 4 | tie (not significant, p=0.38) | cheaper 0.24× (0.17–0.33) | same speed 0.81× (0.45–2.05) |
| Pi | 1 | 5 | tie (not significant, p=0.22) | cheaper 0.25× (0.19–0.33) | same speed 1.19× (0.80–1.66) |
| Hermes | 2 | 5 | tie (not significant, p=0.45) | cheaper 0.43× (0.29–0.62) | same speed 0.85× (0.62–1.36) |

Solved by category:

| Category | Tasks | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|
| algorithmic | 3 | 2 | 3 | 3 | 2 | 3 | 2 |
| ambiguous | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| data | 3 | 2 | 2 | 3 | 3 | 3 | 2 |
| debugging | 3 | 2 | 3 | 3 | 3 | 3 | 3 |
| multi-file | 3 | 2 | 2 | 3 | 2 | 3 | 3 |
| performance | 3 | 3 | 3 | 3 | 3 | 2 | 3 |
| refactoring | 3 | 3 | 2 | 3 | 3 | 3 | 3 |
| test-engineering | 3 | 2 | 3 | 3 | 3 | 3 | 3 |

Cost per attempted task by category ($):

| Category | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|
| algorithmic | 0.0054 | 0.0063 | 0.0038 | 0.0194 | 0.0149 | 0.0103 |
| ambiguous | 0.0015 | 0.0015 | 0.0037 | 0.0073 | 0.0067 | 0.0036 |
| data | 0.0013 | 0.0018 | 0.0039 | 0.0180 | 0.0144 | 0.0059 |
| debugging | 0.0025 | 0.0029 | 0.0033 | 0.0119 | 0.0088 | 0.0062 |
| multi-file | 0.0051 | 0.0057 | 0.0035 | 0.0186 | 0.0118 | 0.0065 |
| performance | 0.0030 | 0.0047 | 0.0050 | 0.0155 | 0.0078 | 0.0131 |
| refactoring | 0.0025 | 0.0031 | 0.0036 | 0.0077 | 0.0148 | 0.0041 |
| test-engineering | 0.0062 | 0.0121 | 0.0045 | 0.0090 | 0.0094 | 0.0067 |

Unsolved tasks per harness:

- Meshpool V2: alg-topo-cycle, data-messy-join, dbg-tz-daily, mf-atomic-transfer, te-money-split
- Meshpool V1: data-mixed-timestamps, mf-config-layers, rf-break-cycle
- Codex: none
- OpenCode: alg-topo-cycle, mf-config-layers
- Pi: perf-regex-redos
- Hermes: alg-k-hop-path, data-mixed-timestamps

## Results: InfiAgent-DABench (public benchmark, official closed-form grader; 12 medium and hard questions sampled, 11 completed by every harness)

| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | Input tok / task | Output tok / task | Calls / task |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool V2 | 10/11 | 91% (62%–98%) | 0 | $0.0012 | $0.0013 | 7.1 | 9.0 | 16.2 | 13,251 | 390 | 2.3 |
| Meshpool V1 | 10/11 | 91% (62%–98%) | 0 | $0.0012 | $0.0014 | 8.0 | 11.3 | 20.1 | 15,016 | 411 | 2.5 |
| Codex | 10/11 | 91% (62%–98%) | 0 | $0.0028 | $0.0031 | 11.5 | 23.0 | 63.8 | 58,472 | 422 | 3.5 |
| OpenCode | 10/11 | 91% (62%–98%) | 0 | $0.0134 | $0.0147 | 14.7 | 19.3 | 25.1 | 145,615 | 349 | 4.3 |
| Pi | 10/11 | 91% (62%–98%) | 0 | $0.0117 | $0.0129 | 15.6 | 15.4 | 19.7 | 127,740 | 435 | 3.4 |
| Hermes | 10/11 | 91% (62%–98%) | 0 | $0.0046 | $0.0051 | 21.2 | 39.2 | 83.5 | 80,339 | 584 | 5.2 |

Meshpool V2 against each harness on the same tasks (quality: McNemar exact; cost and time: paired bootstrap 95% interval of the ratio, over tasks both solved):

| vs | Solved only by Meshpool V2 | Solved only by them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Meshpool V1 | 0 | 0 | tie | same cost 0.94× (0.83–1.03) | same speed 0.79× (0.46–1.34) |
| Codex | 0 | 0 | tie | cheaper 0.41× (0.38–0.47) | faster 0.38× (0.23–0.72) |
| OpenCode | 0 | 0 | tie | cheaper 0.09× (0.07–0.11) | faster 0.47× (0.32–0.73) |
| Pi | 0 | 0 | tie | cheaper 0.10× (0.09–0.11) | faster 0.61× (0.45–0.81) |
| Hermes | 0 | 0 | tie | cheaper 0.26× (0.22–0.32) | faster 0.26× (0.19–0.44) |

Solved by difficulty:

| Difficulty | Tasks | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|
| hard | 5 | 4 | 4 | 4 | 4 | 4 | 4 |
| medium | 6 | 6 | 6 | 6 | 6 | 6 | 6 |

Unsolved tasks per harness:

- Meshpool V2: dab-137
- Meshpool V1: dab-137
- Codex: dab-137
- OpenCode: dab-137
- Pi: dab-137
- Hermes: dab-137

## Diagnosis: why MeshPool V2 lost tasks (from per-tool traces)

1. **Misrouting:** v2-dbg-tz-daily, a bug fix in a code project, went to the *Documents* harness because the prompt
   mentions a "daily sales report". **Fixed after the evaluation** (`hrouter/route.py`: in a code project, document
   words no longer override code; test added). Not yet re-measured.
2. **Self-testing churn on build-from-spec tasks:** 71 `bash` calls across the algorithmic tasks, against a handful
   for Codex; v2-alg-meeting-slot took 32 calls against Codex's 5. This costs time, and the correctness tail (topo-cycle
   was wrong) shows the method is not converging. Next: one test script, rerun it; an adaptive depth for build.
3. **Edit failures:** 8 of 12 `edit` calls failed on those tasks. Next: show the nearest matching lines in the error;
   allow `write_file` for small files.
4. **Quick, wrong data answer:** data-messy-join answered after 3 calls without normalising IDs. Next: the data
   harness should state the data-quality checks (duplicates, ID formats, units, time zones) and run them before
   answering.
5. **A timeout:** te-money-split hit the 600 s limit (no trace was saved). Next: investigate the hang; a
   per-command timeout for test loops.

## Before and after: MeshPool V1 vs V2

| Metric | V1 (b52c31c) | V2 | Change |
|---|---|---|---|
| Hard V2 subset solved | 21/24 (88%) | 19/24 (79%) | −2 tasks (tie, p=0.73) |
| DABench solved | 10/11 | 10/11 | same |
| $ per solved, hard V2 | $0.0054 | $0.0044 | −19% |
| Cost ratio V2/V1, hard V2 (paired) | | 0.59× (0.42–0.91) | **significant** |
| Median s per task, hard V2 | 52.4 | 42.1 | −20% (tie) |
| P90 s, hard V2 | 169.2 | 186.7 | +10% |
| Input tokens per task, hard V2 | 124,752 | 105,087 | −16% |
| Output tokens per task, hard V2 | 4,034 | 2,630 | −35% |
| Model calls per task, hard V2 | 14.0 | 13.4 | −4% |
| DABench time per task (median) | 8.0 s | 7.1 s | tie |

## Engineering changelog (this sprint)

| Change | Evidence that motivated it | Measured effect |
|---|---|---|
| Per-tool traces in every run | Aggregates could not explain the 60-call tail on SWE tasks | Enabled every diagnosis above |
| Verify-before-done (one nudge when the last check after a change failed) | sympy-24066: declared done after a failing check | Not isolated |
| Never fetch the upstream fix; run targeted tests, not the whole suite | Traces: `fetch_url` of the GitHub PR diff; 17–27 s whole-file test runs | Not isolated |
| Issue reports route to debug, not build | Build-routed SWE issues used 25–60 calls vs 7–23 | SWE rerun solved the earlier miss |
| No narration between tool calls (code harness) | 1.5–2× OpenCode's output tokens on SWE | Output tokens −35% vs V1 on hard V2 |
| In a code project, document words do not route to Documents (after evaluation) | v2-dbg-tz-daily misrouted | Not yet measured |
| Lean tools, no todo list for focused fixes; reproduce first; error context; parallel reads; repo map; data preview (from the earlier sprint) | Extra calls vs Pi; read-before-edit rounds | Cost 0.59× V1 on hard V2 |

## Method and integrity

- **Same conditions:** one model (DeepSeek V4.1 Flash), the same workspace, the same prompt, one isolated data folder
  per run, and the same 600 s time limit.
- **Same meter:** every model call went through one metering proxy, and tokens were read from the endpoint's own
  replies.
- **Competitors as shipped:** Pi, Codex, OpenCode and Hermes ran through MeshPool's own integrations, unmodified
  except for the endpoint.
- **The V2 suite:**
  - 60 tasks I wrote: 8 for development, 52 for evaluation, across 8 categories;
  - every task has hidden tests and a reference solution, and was admitted only after it failed as given and passed
    with the reference;
  - test-engineering tasks are graded by mutation testing;
  - data tasks are graded by exact answers computed in code;
  - no LLM judge anywhere.
- **No tuning on evaluation tasks:** the post-evaluation routing fix is labelled as a new iteration and is not
  counted.
- **SWE contamination check:** every harness, MeshPool included, had 1–2 patches identical to the official fix out
  of 24. That is consistent with small single-answer fixes, not systematic look-up. MeshPool fetched an upstream PR
  in development runs, now forbidden.
- **Statistics:** Wilson intervals; McNemar's exact test (paired by task); paired bootstrap for cost and time ratios
  (over tasks both solved).

## Limitations

- **Small samples:**
  - **Hard V2:** 24 of the 52 evaluation tasks, one run each. A first full run at higher parallelism overloaded the
    Mac (it restarted and lost the partial results, about $2.40 of spend), so a stratified subset was run instead.
  - **DABench:** 11 questions completed by every harness.
- **The V2 suite was written by me (the MeshPool developer).** The tasks were validated by tests, but the
  construction is not independent. DABench is the independent check, and it is easy (every harness solves almost
  everything).
- **Only one model,** DeepSeek V4.1 Flash. Results may differ on other models.
- **Performance tasks are timing-based** and ran with up to 11 concurrent agents, so they can be noisy.
- **Not run:** SpreadsheetBench (needs LibreOffice, whose install failed), OfficeBench (the adapter is not built),
  and full SWE-bench Verified (needs Docker and approval; see `docs/plan/SWE_FEASIBILITY.md`).

## Cost

| Item | Spend |
|---|---|
| Before the sprint | $4.66 |
| Development runs and lost runs (machine restart) | about $2.50 |
| V2 subset (144 runs) and DABench (66 runs) | about $2.90 |
| **Total under the $25 cap** | **$10.02 spent, $14.98 left** |

## Recommendation

Do **not** make stronger claims yet. MeshPool can claim that it is **the cheapest harness on every benchmark we
ran** (significant), and **the fastest on data analysis**. It cannot claim better quality on hard coding tasks.

Next:
1. Fix the self-testing churn, the edit failures and the data-quality checks, tuning on `v2_dev`.
2. Run the full 52-task V2 evaluation (about $2–3) and DABench 24.
3. Wire SpreadsheetBench and OfficeBench.

Release only after the full suite is green (see `handoff prompt.md`).
