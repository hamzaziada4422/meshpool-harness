# Meshpool vs Codex: final measurement (build ba45634b swe_sympy)

Updated 2026-11-24 21:31 UTC. 12 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| core + cycle note | 19/24 | 79.2% | $0.0097 | $0.0122 | 27.4 / 14 | 63 | 193 |
| core + large-repo Codex style | 19/24 | 79.2% | $0.0075 | $0.0094 | 29.5 / 16 | 100 | 357 |
| Codex CLI 0.171.0 | 22/24 | 91.7% | $0.0073 | $0.0080 | 22.7 / 21 | 53 | 146 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| core + cycle note | 1.46x (0.71-3.21) | 1.56x (1.01-2.37) | 1.39x (1.07-2.00) | -12.5 pts (-33.3 to +0.0) |
| core + large-repo Codex style | 1.18x (0.80-1.58) | 2.17x (1.35-3.03) | 1.47x (0.82-2.95) | -12.5 pts (-25.0 to +0.0) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- core + cycle note: cheaper NO (1.46x), faster NO (1.56x), same quality NO (-12.5 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- core + large-repo Codex style: cheaper NO (1.18x), faster NO (2.17x), same quality NO (-12.5 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- core + cycle note: sympy__sympy-23262, sympy__sympy-23413, sympy__sympy-23950
- core + large-repo Codex style: sympy__sympy-22914, sympy__sympy-23262, sympy__sympy-23950, sympy__sympy-24443
- Codex CLI 0.171.0: sympy__sympy-23262

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | core + cycle note | core + large-repo Codex style | Codex CLI 0.171.0 |
|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- core + cycle note: 0
- core + large-repo Codex style: 0
- Codex CLI 0.171.0: 0
