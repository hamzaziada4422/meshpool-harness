# Meshpool vs Codex: final measurement (build af122ebf v2_dev)

Updated 2026-11-24 18:48 UTC. 24 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 41/48 | 85.4% | $0.0047 | $0.0055 | 9.1 / 7 | 23 | 104 |
| Codex CLI 0.171.0 | 45/48 | 93.8% | $0.0048 | $0.0051 | 11.3 / 9 | 27 | 140 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 1.08x (0.78-1.48) | 0.94x (0.64-1.23) | 0.86x (0.67-1.18) | -8.3 pts (-20.8 to +4.2) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper NO (1.08x), faster YES (0.94x), same quality NO (-8.3 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-dev2-alg-leaderboard, v2-dev2-data-mrr, v2-dev2-mf-bulk-orders, v2-dev2-te-pager, v2-dev2-te-ttlcache, v2-mf-soft-delete
- Codex CLI 0.171.0: v2-dev2-ref-table-loaders, v2-dev2-te-pager

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 4 (v2-dev2-perf-nearest, v2-dev2-te-ttlcache)
- Codex CLI 0.171.0: 1 (v2-dev2-te-ttlcache)
