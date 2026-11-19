# MeshPool's Codex-design engine on the 52-task evaluation

DeepSeek V4.1 Flash, the 52 eval tasks, one run each, graded by hidden tests; `profile:codex` (MeshPool's agent with
Codex's base prompt, `shell` / `apply_patch` / `update_plan` and Codex's loop, plus MeshPool's loop guard, compaction
and routing method text; upstream openai/codex@097558c, Apache-2.0). Codex and Codex+routing ran together earlier the
same day (runs-v2/codex-eval); MeshPool 0.0.7's previous assignment in runs-v2/eval52-final.

| Arm | Solved | $ / task | Median s | Calls / task | Output tokens / task |
|---|---|---|---|---|---|
| MeshPool, Codex-design engine | 47/52 | $0.0034 | 19 | 10.7 | 2,680 |
| Codex | 47/52 | $0.0030 | 21 | 7.4 | 1,894 |
| Codex + MeshPool routing | 46/52 | $0.0033 | 28 | 8.5 | 2,147 |
| MeshPool, previous assignment | 46/52 | $0.0069 | 23 | 13.5 | 7,473 |

Paired (McNemar exact; bootstrap ratio over tasks both solved): vs Codex 4 vs 4 tasks only one side solved (p=1.0),
cost 0.94x (0.72-1.22), time 1.10x (0.73-1.69): a tie on all three. vs the previous assignment: time 0.49x
(0.30-0.79, faster), cost 0.63x (0.32-1.34). Caveats: one run per task (noise is about +/-3 tasks); the arms did not
all run in the same batch; dollar figures are token estimates (billed spend ran about 2x).

Decision: the Codex-design engine is the default profile for every route in 0.0.7. The harness router
(MH_HARNESS_ROUTER) stays off: on the dev suite Codex+routing led Codex 22 vs 19, but on the eval it tied (46 vs 47),
and its partial eval run showed no gain.
