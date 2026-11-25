# Meshpool vs Codex: final measurement (build 8ba192ea: temperature fix + runaway budget + shape guard)

Updated 2026-11-24 13:38 UTC. 52 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool default (codex profile + new rules) | 94/104 | 90.4% | $0.0047 | $0.0052 | 11.6 / 8 | 34 | 158 |
| Codex CLI 0.171.0 | 96/104 | 92.3% | $0.0029 | $0.0032 | 7.2 / 6 | 25 | 58 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Time per task (x Codex) | Solve rate minus Codex |
|---|---|---|---|
| Meshpool default (codex profile + new rules) | 1.63x (1.24-2.06) | 1.91x (1.46-2.38) | -1.9 pts (-8.7 to +4.8) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool default (codex profile + new rules): cheaper NO (1.63x), faster NO (1.91x), same quality NO (-1.9 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool default (codex profile + new rules): v2-alg-meeting-slot, v2-alg-topo-cycle, v2-amb-invoice-cents, v2-data-mixed-timestamps, v2-mf-config-layers, v2-mf-i18n-plurals, v2-mf-job-hooks, v2-perf-config-reload
- Codex CLI 0.171.0: v2-alg-topo-cycle, v2-amb-invoice-cents, v2-data-messy-join, v2-mf-config-layers, v2-mf-job-hooks, v2-rf-sync-async-client

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool default (codex profile + new rules) | Codex CLI 0.171.0 |
|---|---|---|
| v2-alg-topo-cycle | 1/2 | 1/2 |
| v2-alg-k-hop-path | 2/2 | 2/2 |
| v2-alg-line-diff | 2/2 | 2/2 |
| v2-mf-atomic-transfer | 2/2 | 2/2 |
| v2-mf-i18n-plurals | 1/2 | 2/2 |

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool default (codex profile + new rules): 0
- Codex CLI 0.171.0: 0

Runaway guards in the Meshpool runs (counted from each run's last request): failure-guard nudge 1 (v2-alg-topo-cycle r0) and runaway-budget converge note 1 (v2-dbg-pagination-sync r1). The shape guard, the wraps and the scratch-rewrite note never fired. No reply hit the output cap. The gap to Codex is therefore not runaways. It is steady per-task overhead: 11.6 model calls per task on average against Codex's 7.2.
