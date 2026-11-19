# Ablation on the dev suite (24 tasks, one run each, same snapshot c50f120, uncontended)

DeepSeek V4.1 Flash, every arm on the same 24 dev tasks (never the eval tasks), graded by hidden tests.

| Arm | What it is | Solved | $ / task | Median s | Calls / task |
|---|---|---|---|---|---|
| noplan | V3.2 with plan mode off | **22/24** | $0.0075 | 68 | 22.3 |
| noreview | V3.2 with the review step off | 20/24 | $0.0090 | 80 | 19.6 |
| lean | V2 method texts, no plan, no review, no mutation, plus this sprint's fixes | 20/24 | $0.0093 | **43** | 18.4 |
| routed | V3.2, everything on | 18/24 | $0.0105 | 74 | 22.8 |
| v3 | V3 as evaluated on the 52 eval tasks | 18/24 | $0.0111 | 80 | 25.0 |
| v2 | before this sprint | 17/24 | **$0.0068** | 50 | 23.3 |
| oldmethods | V3.2 with V2's method texts | 17/24 | $0.0099 | 53 | 17.4 |

Paired (McNemar exact; bootstrap ratios over tasks both solved): noplan vs v2 7 vs 2 tasks only one side solved
(p=0.18), cost 1.53x (1.01-2.26), time 2.1x; noplan vs routed 5 vs 1 (p=0.22), same cost, same speed. None of the
quality gaps is significant at this sample size; the direction is consistent: plan mode costs solves and money.

Decision: plan mode off by default (kept as an option for the optimizer). Round 2 measures full-noplan,
full-noplan-stop and lean-stop on the current code (multi-edit, loop guard, profiles).
