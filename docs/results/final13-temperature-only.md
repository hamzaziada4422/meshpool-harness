# Meshpool vs Codex: final measurement (build 283eced2 + temperature fix only)

Updated 2026-11-24 12:38 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool default (codex profile + new rules) | 95/104 | 91.3% | $0.0045 | $0.0049 | 12.1 / 8 | 34 | 218 |
| Codex CLI 0.171.0 | 94/104 | 90.4% | $0.0028 | $0.0031 | 7.4 / 6 | 28 | 78 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Time per task (x Codex) | Solve rate minus Codex |
|---|---|---|---|
| Meshpool default (codex profile + new rules) | 1.57x (1.23-1.97) | 1.77x (1.33-2.41) | +1.0 pts (-7.7 to +9.6) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool default (codex profile + new rules): cheaper NO (1.57x), faster NO (1.77x), same quality YES (+1.0 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool default (codex profile + new rules): v2-alg-meeting-slot, v2-alg-merge-accounts, v2-amb-invoice-cents, v2-mf-config-layers, v2-mf-job-hooks, v2-mf-rename-module, v2-te-token-bucket
- Codex CLI 0.171.0: v2-alg-k-hop-path, v2-data-simpson, v2-dbg-tz-daily, v2-mf-config-layers, v2-mf-rate-limit, v2-mf-rename-module, v2-perf-config-reload, v2-rf-sync-async-client

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool default (codex profile + new rules) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 2/2 | 2/2 |
| v2-alg-k-hop-path | 2/2 | 1/2 |
| v2-alg-line-diff | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 2/2 | 2/2 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool default (codex profile + new rules): 1 (v2-te-semver)
- Codex CLI 0.171.0: 0
