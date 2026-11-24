# Meshpool vs Codex: final measurement (build 0fc488b1)

Updated 2026-11-24 16:26 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 98/104 | 94.2% | $0.0021 | $0.0022 | 5.4 / 4 | 18 | 47 |
| Codex CLI 0.171.0 | 95/104 | 91.3% | $0.0028 | $0.0030 | 7.1 / 7 | 20 | 54 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 0.71x (0.56-0.91) | 1.03x (0.63-1.73) | 0.81x (0.69-0.99) | +2.9 pts (-5.8 to +12.5) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper YES (0.71x), faster NO (1.03x), same quality YES (+2.9 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-alg-k-hop-path, v2-mf-job-hooks, v2-perf-config-reload, v2-perf-dag-paths
- Codex CLI 0.171.0: v2-alg-k-hop-path, v2-amb-invoice-cents, v2-data-simpson, v2-mf-config-layers, v2-mf-rename-module, v2-rf-sync-async-client

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 2/2 | 2/2 |
| v2-alg-k-hop-path | 1/2 | 1/2 |
| v2-alg-line-diff | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 2/2 | 2/2 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Codex CLI 0.171.0: 1 (v2-te-bankers-round)
