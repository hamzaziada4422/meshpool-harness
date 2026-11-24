# Meshpool vs Codex: final measurement (build 3d1d78b0 v2_dev code)

Updated 2026-11-24 20:38 UTC. 21 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| core, no preload | 32/42 | 76.2% | $0.0067 | $0.0088 | 16.6 / 9 | 24 | 195 |
| core, verify prompt | 34/42 | 81.0% | $0.0057 | $0.0070 | 13.4 / 7 | 24 | 64 |
| core, no preload + verify | 35/42 | 83.3% | $0.0074 | $0.0089 | 18.5 / 10 | 31 | 174 |
| Codex CLI 0.171.0 | 37/42 | 88.1% | $0.0046 | $0.0052 | 11.1 / 9 | 29 | 78 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| core, no preload | 1.67x (1.10-2.40) | 1.48x (0.87-2.28) | 0.98x (0.81-1.20) | -11.9 pts (-23.8 to +0.0) |
| core, verify prompt | 1.32x (0.76-2.20) | 1.01x (0.62-1.37) | 1.06x (0.70-1.39) | -7.1 pts (-23.8 to +9.5) |
| core, no preload + verify | 1.69x (1.07-2.65) | 1.58x (1.00-2.51) | 1.19x (0.86-1.70) | -4.8 pts (-19.0 to +7.1) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- core, no preload: cheaper NO (1.67x), faster NO (1.48x), same quality NO (-11.9 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- core, verify prompt: cheaper NO (1.32x), faster NO (1.01x), same quality NO (-7.1 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- core, no preload + verify: cheaper NO (1.69x), faster NO (1.58x), same quality NO (-4.8 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- core, no preload: v2-alg-intervals, v2-dev2-amb-priority, v2-dev2-mf-bulk-orders, v2-dev2-perf-window-distinct, v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-dev2-te-ttlcache
- core, verify prompt: v2-amb-accounting-export, v2-dev2-perf-nearest, v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-dev2-te-ttlcache
- core, no preload + verify: v2-dev2-alg-intervalset, v2-dev2-mf-bulk-orders, v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-dev2-te-ttlcache
- Codex CLI 0.171.0: v2-dev2-mf-bulk-orders, v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-te-slugify

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | core, no preload | core, verify prompt | core, no preload + verify | Codex CLI 0.171.0 |
|---|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- core, no preload: 3 (v2-dev2-perf-nearest, v2-dev2-te-ttlcache)
- core, verify prompt: 4 (v2-dev2-mf-config-layers, v2-dev2-te-ttlcache)
- core, no preload + verify: 4 (v2-dev2-te-ttlcache)
- Codex CLI 0.171.0: 2 (v2-dev2-te-ttlcache)
