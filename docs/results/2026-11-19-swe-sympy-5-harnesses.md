# Harness loop: 2026-11-19 06:41 UTC

12 tasks x 2 repeat(s), run by every arm (codex, hermes, opencode, pi, routed). Same model and hour per pair, graded by code. Intervals are 95%; a difference counts only when it is beyond chance.
Cost: billed key spend per model, split between arms by tokens: deepseek-v4.1-flash $2.6592

## Meshpool against each harness, same model

| Model | vs | Quality | Cost (Meshpool / them) | Time (Meshpool / them) |
|---|---|---|---|---|
| - | codex | tie (1 vs 0, p=1.00) | cheaper 0.63x (0.47-0.80) | same speed 0.96x (0.70-1.24) |
| - | hermes | tie (0 vs 0, p=1.00) | cheaper 0.45x (0.32-0.59) | faster 0.69x (0.47-0.93) |
| - | opencode | tie (1 vs 0, p=1.00) | cheaper 0.36x (0.25-0.46) | same speed 1.07x (0.77-1.41) |
| - | pi | tie (0 vs 0, p=1.00) | cheaper 0.28x (0.21-0.38) | same speed 0.79x (0.59-1.11) |

## Every harness in detail

| Arm | Solved | Solve rate (95% CI) | Errors | $ per solved | s per solved | Out tokens | Calls |
|---|---|---|---|---|---|---|---|
| codex | 23/24 | 96% (80%-99%) | 0 | $0.0164 | 865.7 | 4170 | 20.0 |
| hermes | 24/24 | 100% (86%-100%) | 0 | $0.0224 | 1615.7 | 4075 | 19.8 |
| opencode | 23/24 | 96% (80%-99%) | 0 | $0.0282 | 70.1 | 3146 | 16.2 |
| pi | 24/24 | 100% (86%-100%) | 3 | $0.0356 | 876.1 | 4006 | 17.7 |
| routed | 24/24 | 100% (86%-100%) | 0 | $0.0101 | 828.8 | 3774 | 19.2 |

Solved per suite:

| Suite | codex | hermes | opencode | pi | routed |
|---|---|---|---|---|---|
| swe_sympy | 23/24 | 24/24 | 23/24 | 24/24 | 24/24 |

Flaky (solved in some repeats only): codex: sympy__sympy-23950 1/2; opencode: sympy__sympy-23413 1/2

**routed vs codex**: quality TIE (1 pairs only routed solved, 0 only codex, p=1.000); cost cheaper 0.63x (0.47-0.80); time same speed 0.96x (0.70-1.24)

**routed vs hermes**: quality TIE (0 pairs only routed solved, 0 only hermes, p=1.000); cost cheaper 0.45x (0.32-0.59); time faster 0.69x (0.47-0.93)

**routed vs opencode**: quality TIE (1 pairs only routed solved, 0 only opencode, p=1.000); cost cheaper 0.36x (0.25-0.46); time same speed 1.07x (0.77-1.41)

**routed vs pi**: quality TIE (0 pairs only routed solved, 0 only pi, p=1.000); cost cheaper 0.28x (0.21-0.38); time same speed 0.79x (0.59-1.11)

