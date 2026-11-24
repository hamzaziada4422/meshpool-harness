# Meshpool vs Codex: final measurement (build 2310d4b1 v2_dev code)

Updated 2026-11-24 19:38 UTC. 21 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 33/42 | 78.6% | $0.0042 | $0.0053 | 11.1 / 7 | 24 | 76 |
| Meshpool core read-first | 32/42 | 76.2% | $0.0063 | $0.0083 | 13.8 / 6 | 26 | 184 |
| Codex CLI 0.171.0 | 38/42 | 90.5% | $0.0046 | $0.0050 | 11.5 / 10 | 30 | 84 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 1.05x (0.84-1.34) | 1.08x (0.64-1.78) | 0.74x (0.61-1.12) | -11.9 pts (-26.2 to +2.4) |
| Meshpool core read-first | 1.60x (0.72-3.69) | 1.29x (0.57-2.62) | 0.62x (0.52-1.28) | -14.3 pts (-28.6 to -2.4) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper NO (1.05x), faster NO (1.08x), same quality NO (-11.9 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- Meshpool core read-first: cheaper NO (1.60x), faster NO (1.29x), same quality NO (-14.3 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-dev2-alg-intervalset, v2-dev2-mf-bulk-orders, v2-dev2-perf-window-distinct, v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-dev2-te-ttlcache, v2-rf-payment-strategies
- Meshpool core read-first: v2-alg-intervals, v2-dev2-alg-intervalset, v2-dev2-dbg-dst-schedule, v2-dev2-ref-table-loaders, v2-dev2-te-pager, v2-dev2-te-ttlcache, v2-rf-payment-strategies
- Codex CLI 0.171.0: v2-dev2-amb-priority, v2-dev2-te-pager, v2-dev2-te-ttlcache

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Meshpool core read-first | Codex CLI 0.171.0 |
|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 4 (v2-dev2-te-ttlcache)
- Meshpool core read-first: 1 (v2-dev2-mf-config-layers)
- Codex CLI 0.171.0: 1 (v2-dev2-te-ttlcache)
