# Meshpool vs Codex: final measurement (build 7b69cc03 (lean core + preload + re-read + squeeze + re-check))

Updated 2026-11-24 15:19 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 95/104 | 91.3% | $0.0016 | $0.0017 | 4.5 / 3 | 11 | 36 |
| Codex CLI 0.171.0 | 98/104 | 94.2% | $0.0028 | $0.0029 | 7.3 / 6 | 22 | 58 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Time per task (x Codex) | Solve rate minus Codex |
|---|---|---|---|
| Meshpool core (lean profile) | 0.58x (0.45-0.73) | 0.63x (0.48-0.85) | -2.9 pts (-9.6 to +3.8) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper YES (0.58x), faster YES (0.63x), same quality NO (-2.9 pts). Reliable (interval clears the bar): cost yes, time yes, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): v2-alg-k-hop-path, v2-alg-merge-accounts, v2-mf-config-layers, v2-mf-i18n-plurals, v2-mf-job-hooks, v2-perf-regex-redos
- Codex CLI 0.171.0: v2-amb-invoice-cents, v2-mf-config-layers, v2-mf-job-hooks, v2-mf-rate-limit

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 2/2 | 2/2 |
| v2-alg-k-hop-path | 0/2 | 2/2 |
| v2-alg-line-diff | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 1/2 | 2/2 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Codex CLI 0.171.0: 0
