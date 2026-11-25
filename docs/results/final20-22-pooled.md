# Meshpool vs Codex: final measurement (build 9d011d0e (0fc488b1 for final20; prompts identical), pooled)

Updated 2026-11-24 17:22 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 294/312 | 94.2% | $0.0023 | $0.0025 | 5.9 / 4 | 15 | 48 |
| Codex CLI 0.171.0 | 280/312 | 89.7% | $0.0029 | $0.0032 | 7.3 / 6 | 19 | 52 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 0.77x (0.64-0.89) | 1.01x (0.75-1.37) | 0.74x (0.68-0.90) | +4.5 pts (+0.0 to +9.6) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper YES (0.77x), faster NO (1.01x), same quality YES (+4.5 pts). Reliable (interval clears the bar): cost no, time no, quality yes.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-alg-k-hop-path, v2-alg-merge-accounts, v2-amb-invoice-cents, v2-data-messy-join, v2-data-mixed-timestamps, v2-mf-config-layers, v2-mf-job-hooks, v2-perf-config-reload, v2-perf-dag-paths, v2-perf-regex-redos, v2-te-money-split
- Codex CLI 0.171.0: v2-alg-k-hop-path, v2-alg-meeting-slot, v2-alg-topo-cycle, v2-alg-window-median, v2-amb-invoice-cents, v2-data-mixed-timestamps, v2-data-simpson, v2-dbg-csv-import, v2-dbg-pagination-sync, v2-mf-config-layers, v2-mf-job-hooks, v2-mf-rate-limit, v2-mf-rename-module, v2-perf-config-reload, v2-rf-sync-async-client, v2-te-token-bucket

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 6/6 | 5/6 |
| v2-alg-k-hop-path | 5/6 | 5/6 |
| v2-alg-line-diff | 6/6 | 6/6 |
| v2-mf-atomic-transfer | 6/6 | 6/6 |
| v2-mf-i18n-plurals | 6/6 | 6/6 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Codex CLI 0.171.0: 1 (v2-te-bankers-round)
