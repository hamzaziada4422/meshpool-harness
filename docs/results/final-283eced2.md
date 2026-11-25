# Meshpool vs Codex: final measurement (build 283eced2)

Updated 2026-11-24 11:17 UTC. 49 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool default (codex profile + new rules) | 92/98 | 93.9% | $0.0041 | $0.0044 | 10.1 / 7 | 41 | 203 |
| Meshpool + review | 86/98 | 87.8% | $0.0043 | $0.0049 | 12.4 / 9 | 44 | 214 |
| Codex CLI 0.171.0 | 87/98 | 88.8% | $0.0031 | $0.0034 | 7.4 / 6 | 33 | 116 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Time per task (x Codex) | Solve rate minus Codex |
|---|---|---|---|
| Meshpool default (codex profile + new rules) | 1.25x (0.87-1.90) | 1.59x (1.11-2.33) | +5.1 pts (-2.0 to +12.2) |
| Meshpool + review | 1.42x (0.97-2.02) | 1.53x (1.11-2.00) | -1.0 pts (-9.2 to +7.1) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool default (codex profile + new rules): cheaper NO (1.25x), faster NO (1.59x), same quality YES (+5.1 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- Meshpool + review: cheaper NO (1.42x), faster NO (1.53x), same quality NO (-1.0 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool default (codex profile + new rules): v2-amb-invoice-cents, v2-mf-job-hooks, v2-perf-dag-paths, v2-perf-regex-redos, v2-te-semver
- Meshpool + review: v2-amb-invoice-cents, v2-data-mixed-timestamps, v2-mf-job-hooks, v2-perf-config-reload, v2-perf-dag-paths, v2-perf-regex-redos, v2-rf-sync-async-client, v2-te-business-days, v2-te-semver
- Codex CLI 0.171.0: v2-alg-topo-cycle, v2-amb-invoice-cents, v2-data-simpson, v2-mf-config-layers, v2-mf-job-hooks, v2-mf-js-recipe-units, v2-perf-regex-redos, v2-rf-dedupe-validation, v2-rf-sync-async-client

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool default (codex profile + new rules) | Meshpool + review | Codex CLI 0.171.0 |
|---|---|---|---|
| v2-alg-topo-cycle | 2/2 | 2/2 | 1/2 |
| v2-alg-k-hop-path | 2/2 | 2/2 | 2/2 |
| v2-alg-line-diff | 2/2 | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 2/2 | 2/2 | 2/2 |

Notes on this run:

- Scope: 49 of the 52 tasks ran for every arm. The run stopped at its $1.10 spending cap before v2-te-bankers-round, v2-te-token-bucket and v2-te-csv-line. All three arms are missing the same tasks, so the paired comparison is unaffected.
- Where the extra cost comes from (Meshpool default vs Codex): 986 vs 722 model calls (+37%), 293k vs 194k output tokens (+51%), and 1.41M vs 1.09M uncached input tokens (+30%). The median per-task cost ratio is 1.00. The 1.25x gap comes from a few runaway runs: v2-te-semver (two 600 s timeouts with 17k-42k output tokens, which alone is about 60% of the excess), v2-perf-dag-paths and v2-dbg-csv-import.
- Orphaned processes left by runs the bench killed at timeout were removed during the run, for all arms (one at 10:18, one at 10:27). Before that, two of them used CPU for about 5 minutes, which slowed whichever runs were in progress.
