# Full SWE-bench Verified: cost and time estimate (not started; needs approval)

## Measured inputs (local SymPy run, DeepSeek V4.1 Flash, 12 issues × 2 repeats, 19 Nov 2026)

| Harness | $ per task | Model calls per task | Input tokens per task | Output tokens per task | Median s per task | Mean s per task |
|---|---|---|---|---|---|---|
| Meshpool | 0.0101 | 19.2 | 224k (87% cached) | 3.8k | 32 | 77 |
| Codex | 0.0157 | 20.0 | 486k | 4.2k | 57 | 72 |
| OpenCode | 0.0271 | 16.2 | 815k | 3.1k | 48 | 70 |
| Pi | 0.0356 | 17.7 | 784k | 4.0k | 61 | 109 |
| Hermes | 0.0224 | 19.8 | 550k | 4.1k | 83 | 113 |
| **All five** | **0.111** | | | | | |

SymPy issues are mid-difficulty within Verified. The other repositories (Django, Astropy, Matplotlib, scikit-learn,
Sphinx, pytest, xarray and others) have bigger code bases and longer test runs. Scenarios scale the measured cost by
1.0× (optimistic), 1.6× (base) and 2.5× (conservative).

## Scope

- Eligible: all 500 Verified tasks.
- Supported locally without Docker: SymPy only. 75 tasks exist; 12 of the first 32 tried could be graded fairly on
  Python 3.12, so roughly 30 are usable. All other repositories need SWE-bench's Docker images (x86_64).
- This Mac: 19 GB free and Apple silicon. Docker needs about 40 GB free, and the x86_64 images run under emulation,
  so it is **not feasible locally** (one attempt ran the disk out of space and was undone).
- Feasible host: GitHub-hosted Ubuntu runners (free for this public repo). Free about 30 GB after cleanup; run
  tasks one image at a time, deleting each after use; shard across a job matrix to stay under the 6-hour job limit.

## Estimates for all 500 tasks

| Scenario | Meshpool only | All five harnesses | Wall clock (5 harnesses, 10 runner shards, 2 tasks in parallel each) |
|---|---|---|---|
| Optimistic (1.0×) | $5.05 | $55.50 | ~9 h |
| Base (1.6×) | $8.10 | $88.80 | ~14 h |
| Conservative (2.5×) | $12.60 | $138.80 | ~22 h |

Time assumptions: image pull 1–3 min, a harness run 1–4 min, and grading 1–2 min per task and harness; each task
image is pulled once and shared by all five harnesses.

Likely failure sources:
- image pull rate limits on Docker Hub;
- disk exhaustion when deletion lags behind pulls;
- harness crashes or stream timeouts (Pi hit 3 "Body Timeout" errors in 24 runs);
- flaky upstream tests;
- endpoint rate limits under parallel load;
- the 6-hour job limit.

## Within the remaining budget ($20.29 before the V2 runs)

The full benchmark across five harnesses does **not** fit, even optimistically ($55.50). It fits for Meshpool alone
in every scenario.

Recommended largest representative subset: **a 100-task stratified sample** (proportional by repository, fixed
seed) on **Meshpool, Codex and OpenCode**, the three cheapest, at about $0.053 per task × 1.6 = $8.50 base and
$13.30 conservative. Pi and Hermes can be added on a 30-task slice (+$3.50 base).
