# Meshpool vs Codex: final measurement (build 4027c5ee / O=b52c31c8, v2_dev code)

Updated 2026-11-24 22:47 UTC. 19 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| old router (b52c31c8) | 32/37 | 86.5% | $0.0176 | $0.0204 | 37.8 / 20 | 65 | 236 |
| core, no re-check | 31/37 | 83.8% | $0.0047 | $0.0056 | 12.9 / 9 | 23 | 132 |
| Codex design (plain) | 34/37 | 91.9% | $0.0075 | $0.0082 | 20.8 / 15 | 46 | 137 |
| Codex CLI 0.171.0 | 34/37 | 91.9% | $0.0054 | $0.0059 | 12.6 / 10 | 31 | 105 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| old router (b52c31c8) | 3.30x (1.54-6.41) | 2.02x (1.20-3.58) | 1.74x (1.18-2.74) | -5.3 pts (-18.4 to +7.9) |
| core, no re-check | 0.94x (0.64-1.37) | 1.33x (0.59-2.70) | 1.04x (0.78-1.64) | -7.9 pts (-26.3 to +7.9) |
| Codex design (plain) | 1.36x (1.07-1.72) | 1.32x (0.85-1.95) | 1.28x (0.97-1.98) | +0.0 pts (-13.2 to +10.5) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- old router (b52c31c8): cheaper NO (3.30x), faster NO (2.02x), same quality NO (-5.3 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- core, no re-check: cheaper NO (0.94x), faster NO (1.33x), same quality NO (-7.9 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- Codex design (plain): cheaper NO (1.36x), faster NO (1.32x), same quality YES (+0.0 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- old router (b52c31c8): v2-dev2-mf-bulk-orders, v2-dev2-perf-window-distinct, v2-dev2-ref-pricing, v2-dev2-ref-table-loaders, v2-mf-soft-delete
- core, no re-check: v2-dev2-alg-intervalset, v2-dev2-alg-leaderboard, v2-dev2-te-pager, v2-dev2-te-ttlcache
- Codex design (plain): v2-dev2-mf-bulk-orders, v2-dev2-te-pager
- Codex CLI 0.171.0: v2-dev2-amb-priority, v2-dev2-ref-pricing, v2-dev2-te-pager

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | old router (b52c31c8) | core, no re-check | Codex design (plain) | Codex CLI 0.171.0 |
|---|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- old router (b52c31c8): 29 (v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-dev2-te-ttlcache, v2-te-slugify)
- core, no re-check: 1 (v2-dev2-te-ttlcache)
- Codex design (plain): 7 (v2-dev2-te-pager, v2-dev2-te-ttlcache)
- Codex CLI 0.171.0: 1 (v2-dev2-te-ttlcache)
