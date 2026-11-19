# Harness loop: 2026-11-19 06:41 UTC

42 tasks x 1 repeat(s), run by every arm (codex, hermes, opencode, pi, routed). Same model and hour per pair, graded by code. Intervals are 95%; a difference counts only when it is beyond chance.
Cost: estimated from tokens at list prices (no billed spend available).

## Meshpool against each harness, same model

| Model | vs | Quality | Cost (Meshpool / them) | Time (Meshpool / them) |
|---|---|---|---|---|
| - | codex | tie (0 vs 0, p=1.00) | cheaper 0.62x (0.51-0.74) | same speed 0.89x (0.70-1.10) |
| - | hermes | tie (0 vs 0, p=1.00) | cheaper 0.25x (0.20-0.30) | faster 0.34x (0.27-0.41) |
| - | opencode | tie (0 vs 0, p=1.00) | cheaper 0.67x (0.54-0.81) | same speed 0.86x (0.67-1.07) |
| - | pi | tie (1 vs 0, p=1.00) | cheaper 0.34x (0.29-0.40) | faster 0.77x (0.57-0.99) |

## Every harness in detail

| Arm | Solved | Solve rate (95% CI) | Errors | $ per solved | s per solved | Out tokens | Calls |
|---|---|---|---|---|---|---|---|
| codex | 42/42 | 100% (92%-100%) | 0 | $0.0019 | 8.4 | 462 | 3.6 |
| hermes | 42/42 | 100% (92%-100%) | 0 | $0.0047 | 22.1 | 729 | 5.3 |
| opencode | 42/42 | 100% (92%-100%) | 0 | $0.0018 | 8.7 | 360 | 4.6 |
| pi | 41/42 | 98% (88%-100%) | 0 | $0.0036 | 10.1 | 552 | 3.9 |
| routed | 42/42 | 100% (92%-100%) | 0 | $0.0012 | 7.5 | 560 | 4.5 |

Solved per suite:

| Suite | codex | hermes | opencode | pi | routed |
|---|---|---|---|---|---|
| build | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| chat | 10/10 | 10/10 | 10/10 | 9/10 | 10/10 |
| data | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| debug | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 |
| deep | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| hard | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 |

**routed vs codex**: quality TIE (0 pairs only routed solved, 0 only codex, p=1.000); cost cheaper 0.62x (0.51-0.74); time same speed 0.89x (0.70-1.10)

**routed vs hermes**: quality TIE (0 pairs only routed solved, 0 only hermes, p=1.000); cost cheaper 0.25x (0.20-0.30); time faster 0.34x (0.27-0.41)

**routed vs opencode**: quality TIE (0 pairs only routed solved, 0 only opencode, p=1.000); cost cheaper 0.67x (0.54-0.81); time same speed 0.86x (0.67-1.07)

**routed vs pi**: quality TIE (1 pairs only routed solved, 0 only pi, p=1.000); cost cheaper 0.34x (0.29-0.40); time faster 0.77x (0.57-0.99)

