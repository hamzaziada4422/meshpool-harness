# Meshpool vs Codex: final measurement (build 9d011d0e)

Updated 2026-11-24 16:39 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 98/104 | 94.2% | $0.0027 | $0.0029 | 6.5 / 4 | 15 | 52 |
| Codex CLI 0.171.0 | 91/104 | 87.5% | $0.0028 | $0.0032 | 7.2 / 6 | 21 | 54 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 0.88x (0.65-1.17) | 1.14x (0.70-1.80) | 0.85x (0.66-1.04) | +6.7 pts (+0.0 to +14.4) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper NO (0.88x), faster NO (1.14x), same quality YES (+6.7 pts). Reliable (interval clears the bar): cost no, time no, quality yes.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-amb-invoice-cents, v2-data-messy-join, v2-data-mixed-timestamps, v2-mf-job-hooks, v2-perf-regex-redos, v2-te-money-split
- Codex CLI 0.171.0: v2-alg-topo-cycle, v2-amb-invoice-cents, v2-data-mixed-timestamps, v2-dbg-csv-import, v2-dbg-pagination-sync, v2-mf-config-layers, v2-mf-job-hooks, v2-mf-rate-limit, v2-mf-rename-module, v2-perf-config-reload, v2-rf-sync-async-client, v2-te-token-bucket

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 2/2 | 1/2 |
| v2-alg-k-hop-path | 2/2 | 2/2 |
| v2-alg-line-diff | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 2/2 | 2/2 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Codex CLI 0.171.0: 0
