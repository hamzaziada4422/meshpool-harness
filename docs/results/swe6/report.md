# Meshpool vs Codex: final measurement (build 0ff01d8e swe_sympy)

Updated 2026-11-24 23:19 UTC. 12 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool default | 20/23 | 87.0% | $0.0080 | $0.0093 | 22.9 / 16 | 55 | 150 |
| default + large-repo Codex style | 19/23 | 82.6% | $0.0052 | $0.0063 | 20.3 / 11 | 26 | 108 |
| Codex CLI 0.171.0 | 20/23 | 87.0% | $0.0064 | $0.0074 | 21.4 / 18 | 44 | 117 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool default | 1.17x (0.77-1.70) | 1.19x (0.80-1.88) | 1.27x (0.80-1.64) | +4.2 pts (-12.5 to +25.0) |
| default + large-repo Codex style | 0.83x (0.54-1.14) | 0.93x (0.63-1.17) | 0.89x (0.70-1.11) | -4.2 pts (-12.5 to +0.0) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool default: cheaper NO (1.17x), faster NO (1.19x), same quality YES (+4.2 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- default + large-repo Codex style: cheaper NO (0.83x), faster YES (0.93x), same quality NO (-4.2 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool default: sympy__sympy-23262, sympy__sympy-23950
- default + large-repo Codex style: sympy__sympy-23262, sympy__sympy-23413, sympy__sympy-23950
- Codex CLI 0.171.0: sympy__sympy-23262, sympy__sympy-23413

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool default | default + large-repo Codex style | Codex CLI 0.171.0 |
|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool default: 0
- default + large-repo Codex style: 0
- Codex CLI 0.171.0: 0
