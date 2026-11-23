
---

## Fixed build 8dc9f501, PARTIAL (swe + everyday only, Meshpool arm only)

Partial re-run, approved by the coordinator. Meshpool came from a fresh worktree of 8dc9f501, the commit with the
compaction death-loop fix, the repeated-read breaker, the work-lane rules and the office cuts. It ran once on the 15 swe +
everyday tasks with the same harness, graders, meter and timeouts. Office is not included: ws20 is re-running it, and the
remaining budget was capped at $0.50. Metered spend was $0.19, with no 429 and no budget cut. **Codex and Pi are NOT
re-run.** Their rows are run 1 from the earlier window above, so the arms did not share the hour this time. Raw data:
results_fix_8dc9f501.jsonl and meter_fix_8dc9f501.jsonl.

### Headline

| Arm | Solved | $/task | $/solved | Calls/task | Input tok/task | Cached tok/task | Output tok/task | s/task | Errors | Timeouts |
|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool | 15/15 | $0.0124 | $0.0124 | 16.7 | 277,477 | 260,096 | 3,776 | 55 | 0 | 0 |
| Codex CLI | 15/15 | $0.0069 | $0.0069 | 9.4 | 120,737 | 108,442 | 1,604 | 29 | 0 | 0 |
| Pi | 15/15 | $0.0160 | $0.0160 | 7.6 | 206,634 | 167,578 | 1,515 | 28 | 0 | 0 |

### Per category

| Category | Meshpool solved | Meshpool $/solved | Codex CLI solved | Codex CLI $/solved | Pi solved | Pi $/solved |
|---|---|---|---|---|---|---|
| swe | 6/6 | $0.0202 | 6/6 | $0.0092 | 6/6 | $0.0194 |
| everyday | 9/9 | $0.0073 | 9/9 | $0.0053 | 9/9 | $0.0137 |

### Paired ratios (Meshpool / other, same task and run; 95% bootstrap over task-runs)

| vs | pairs | Solves (MP vs them) | Cost ratio | $/solved ratio | Calls ratio | Time ratio |
|---|---|---|---|---|---|---|
| Codex CLI | 15 | 15 vs 15 (only MP 0, only them 0) | 1.80x (0.74-3.24) | 1.80x (0.74-3.24) | 1.77x (1.13-2.70) | 1.90x (1.28-2.67) |
| Pi | 15 | 15 vs 15 (only MP 0, only them 0) | 0.78x (0.28-1.60) | 0.78x (0.28-1.60) | 2.19x (1.28-3.41) | 1.95x (1.07-3.14) |

- swe: vs Codex CLI cost 2.19x (0.61-4.69), $/solved 2.19x (0.61-4.69); vs Pi cost 1.04x (0.22-2.91), $/solved 1.04x (0.22-2.91)

- everyday: vs Codex CLI cost 1.36x (0.62-2.39), $/solved 1.36x (0.62-2.39); vs Pi cost 0.53x (0.23-1.01), $/solved 0.53x (0.23-1.01)

Old build bf040146 on the same 15 tasks (run 1): 14/15 solved, $0.151. The fixed build: 15/15, $0.186. md_to_docx is
now solved, because given values are copied exactly. One SWE task, sympy__sympy-23262, still sprawls: 61 calls, $0.089,
nearly half of the total. Without it, Meshpool's $/solved would be about $0.0071, roughly level with Codex. Office was
the biggest loss in the main run and is still unmeasured on this build.

VERDICT (fixed build 8dc9f501, PARTIAL: swe+everyday only, Codex/Pi from the earlier window): FAIL - Meshpool $/solved $0.0124 = 1.80x Codex ($0.0069) and 0.78x Pi ($0.0160) [need <= 0.80x both]; solved 15 vs Codex 15 / Pi 15 [need >= each]
