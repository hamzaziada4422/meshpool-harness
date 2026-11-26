# Meshpool vs Codex: final measurement (build 255e0154 work tasks)

Updated 2026-11-24 18:57 UTC. 27 coding tasks (v2_eval), model deepseek-v4.1-flash via llm.meshpool.ai, list prices $0.15 in / $0.003 cached / $0.60 out per 1M tokens. Every arm ran twice in the same window; every run had its own private /tmp (earlier runs shared /tmp, which skewed them). Cost is metered at the proxy for every model call.

| Arm | Solved | Solve rate | $/task | $/solved | Calls/task (mean / median) | Median s/task | p90 s/task |
|---|---|---|---|---|---|---|---|
| Meshpool lean work (MH_LEAN_WORK=1) | 45/54 | 83.3% | $0.0010 | $0.0012 | 3.0 / 3 | 6 | 11 |
| Meshpool work, flag off | 43/54 | 79.6% | $0.0017 | $0.0021 | 3.0 / 2 | 6 | 14 |
| Codex CLI 0.171.0 | 48/54 | 88.9% | $0.0017 | $0.0020 | 4.3 / 3 | 7 | 19 |

Paired against Codex over the same tasks (bootstrap over tasks, 95% interval):

| Arm | Cost per solved (x Codex) | Total time (x Codex) | Median per-task time ratio | Solve rate minus Codex |
|---|---|---|---|---|
| Meshpool lean work (MH_LEAN_WORK=1) | 0.61x (0.48-0.78) | 0.64x (0.54-0.76) | 0.68x (0.60-0.82) | -5.6 pts (-13.0 to +0.0) |
| Meshpool work, flag off | 1.07x (0.87-1.37) | 0.88x (0.61-1.23) | 0.73x (0.64-1.03) | -9.3 pts (-16.7 to -1.9) |

Verdict (target: at least 20% cheaper per solved task, not slower, solve rate not lower):

- Meshpool lean work (MH_LEAN_WORK=1): cheaper YES (0.61x), faster YES (0.64x), same quality NO (-5.6 pts). Reliable (interval clears the bar): cost yes, time yes, quality no.
- Meshpool work, flag off: cheaper NO (1.07x), faster YES (0.88x), same quality NO (-9.3 pts). Reliable (interval clears the bar): cost no, time no, quality no.

Tasks with at least one failed run:

- Meshpool lean work (MH_LEAN_WORK=1): dab-137, dab-300, dab-523, dab-6, v2-dev2-data-dst-day, v2-dev2-data-mrr
- Meshpool work, flag off: dab-137, dab-300, dab-308, dab-523, v2-data-dupes-fx, v2-dev2-data-dst-day, v2-dev2-data-mrr
- Codex CLI 0.171.0: dab-137, dab-300, dab-523, v2-dev2-data-mrr

The earlier Meshpool-only failures (4530fc66), runs passed now:

| Task | Meshpool lean work (MH_LEAN_WORK=1) | Meshpool work, flag off | Codex CLI 0.171.0 |
|---|---|---|---|

Model replies that hit the 4,096-token output cap (runaway generation):

- Meshpool lean work (MH_LEAN_WORK=1): 0
- Meshpool work, flag off: 0
- Codex CLI 0.171.0: 0
