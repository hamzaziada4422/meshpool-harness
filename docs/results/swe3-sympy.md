# Meshpool vs Codex: final measurement (build 2310d4b1 swe_sympy)

Updated 2026-11-24 20:08 UTC. 12 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool core (lean profile) | 18/24 | 75.0% | $0.0128 | $0.0171 | 32.3 / 14 | 56 | 636 |
| Meshpool core read-first | 20/24 | 83.3% | $0.0124 | $0.0149 | 32.2 / 15 | 77 | 718 |
| Codex CLI 0.171.0 | 22/24 | 91.7% | $0.0060 | $0.0065 | 20.2 / 12 | 44 | 185 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool core (lean profile) | 2.57x (1.55-4.50) | 2.95x (1.74-4.79) | 2.22x (1.41-3.47) | -16.7 pts (-37.5 to +0.0) |
| Meshpool core read-first | 2.22x (1.26-3.44) | 2.82x (1.62-4.20) | 2.58x (1.23-3.51) | -8.3 pts (-25.0 to +0.0) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool core (lean profile): cheaper NO (2.57x), faster NO (2.95x), same quality NO (-16.7 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- Meshpool core read-first: cheaper NO (2.22x), faster NO (2.82x), same quality NO (-8.3 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool core (lean profile): sympy__sympy-22456, sympy__sympy-23262, sympy__sympy-23413, sympy__sympy-23950
- Meshpool core read-first: sympy__sympy-23262, sympy__sympy-23413
- Codex CLI 0.171.0: sympy__sympy-23262

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool core (lean profile) | Meshpool core read-first | Codex CLI 0.171.0 |
|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool core (lean profile): 0
- Meshpool core read-first: 0
- Codex CLI 0.171.0: 0
