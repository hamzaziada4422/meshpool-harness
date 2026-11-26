# Meshpool vs Codex: final measurement (build 0ff01d8e swe_sympy, 23413 excluded (gateway budget 429 mid-run))

Updated 2026-11-24 23:25 UTC. 11 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool default | 19/22 | 86.4% | $0.0081 | $0.0094 | 23.2 / 16 | 56 | 150 |
| default + large-repo Codex style | 19/22 | 86.4% | $0.0052 | $0.0061 | 20.5 / 10 | 25 | 108 |
| Codex CLI 0.171.0 | 20/22 | 90.9% | $0.0065 | $0.0071 | 21.5 / 16 | 51 | 117 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool default | 1.29x (0.86-1.83) | 1.19x (0.77-1.92) | 1.35x (0.75-1.65) | -4.5 pts (-13.6 to +0.0) |
| default + large-repo Codex style | 0.83x (0.52-1.16) | 0.92x (0.60-1.18) | 0.88x (0.58-1.15) | -4.5 pts (-13.6 to +0.0) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool default: cheaper NO (1.29x), faster NO (1.19x), same quality NO (-4.5 pts). Reliable (interval clears the bar): cost no, time no, quality no.
- default + large-repo Codex style: cheaper NO (0.83x), faster YES (0.92x), same quality NO (-4.5 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool default: sympy__sympy-23262, sympy__sympy-23950
- default + large-repo Codex style: sympy__sympy-23262, sympy__sympy-23950
- Codex CLI 0.171.0: sympy__sympy-23262

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool default | default + large-repo Codex style | Codex CLI 0.171.0 |
|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool default: 0
- default + large-repo Codex style: 0
- Codex CLI 0.171.0: 0

## Where the solve gap comes from (added later)

The whole solve gap against Codex (D0 and EL at 19/22, Codex at 20/22) is one task, sympy-23950:

| Arm | Rep 0 | Rep 1 |
|---|---|---|
| D0 | fail | pass |
| EL | pass | fail |
| Codex | pass | pass |

Both failures hit the same test (test_as_set, 1 of 5 required tests). Every other task has the same outcome on all three arms. One flip in 22 pairs is within noise. It is not a structural loss, so there is nothing to patch. swe7 (2 reps per task) is what can settle it. The failing runs' request bodies were not kept; only D0-r1 and EL-r0, both passes, are under bodies/.
