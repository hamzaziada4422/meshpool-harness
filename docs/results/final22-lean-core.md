# Meshpool vs Codex: final measurement (build 9d011d0e)

Updated 2026-11-24 17:22 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 98/104 | 94.2% | $0.0022 | $0.0024 | 5.7 / 4 | 13 | 37 |
| Codex CLI 0.171.0 | 94/104 | 90.4% | $0.0031 | $0.0034 | 7.7 / 6 | 18 | 38 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 0.69x (0.55-0.89) | 0.82x (0.63-1.17) | 0.82x (0.65-0.97) | +3.8 pts (-1.9 to +10.6) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper YES (0.69x), faster YES (0.82x), same quality YES (+3.8 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-alg-merge-accounts, v2-amb-invoice-cents, v2-mf-config-layers, v2-mf-job-hooks, v2-perf-config-reload
- Codex CLI 0.171.0: v2-alg-meeting-slot, v2-alg-window-median, v2-amb-invoice-cents, v2-dbg-pagination-sync, v2-mf-config-layers, v2-mf-job-hooks, v2-perf-config-reload, v2-rf-sync-async-client

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 2/2 | 2/2 |
| v2-alg-k-hop-path | 2/2 | 2/2 |
| v2-alg-line-diff | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 2/2 | 2/2 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Codex CLI 0.171.0: 0
