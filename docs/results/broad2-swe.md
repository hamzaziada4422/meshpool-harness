# Meshpool vs Codex: final measurement (build 9d011d0e swe_sympy, core with cache hints)

Updated 2026-11-24 18:48 UTC. 12 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 20/24 | 83.3% | $0.0085 | $0.0102 | 26.1 / 16 | 82 | 297 |
| Codex CLI 0.171.0 | 21/24 | 87.5% | $0.0067 | $0.0077 | 21.8 / 14 | 42 | 158 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 1.33x (1.00-1.82) | 2.04x (1.42-2.77) | 2.29x (1.19-2.93) | -4.2 pts (-16.7 to +8.3) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper NO (1.33x), faster NO (2.04x), same quality NO (-4.2 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): sympy__sympy-22456, sympy__sympy-23262, sympy__sympy-24066
- Codex CLI 0.171.0: sympy__sympy-23262, sympy__sympy-24443

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Codex CLI 0.171.0 |
|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Codex CLI 0.171.0: 0
