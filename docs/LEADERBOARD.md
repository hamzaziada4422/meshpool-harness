# MeshPool benchmarks: every result so far

> **Superseded (19 Nov 2026, later):** the full 52-task evaluation against every harness (`docs/results/v3/EVAL52_FINAL.md`) does not support "cheapest". There, every MeshPool version ties the other harnesses on quality (46-47 of 52, within run-to-run noise), and Codex is cheaper and faster (MeshPool 0.0.7: 1.4x Codex's cost per solved task, 2x its time). The numbers below are from smaller, earlier runs.


DeepSeek V4.1 Flash for every harness. Same tasks, same time, one metering proxy, graded by tests or official
answers (no LLM judge). "Significant" = 95% interval or exact test excludes no difference. Full details:
`meshpool-v2-results.md` (Desktop) and `docs/results/` in the repo.

## At a glance: MeshPool V2 against each harness

| Benchmark | Tasks | MeshPool solved | Quality vs others | Cost vs others | Speed vs others |
|---|---|---|---|---|---|
| **InfiAgent-DABench** (public, data analysis, official grader) | 11 | 10/11 (all harnesses 10/11) | tie with all | **cheaper than all:** 0.09–0.41× | **faster than all:** 0.26–0.61× |
| **MeshPool V2 hard suite** (our own; hidden tests; 24 of 52, stratified) | 24 | 19/24 (Codex 24, Pi 23, OpenCode 22, Hermes 22) | tie with all (not significant); **fewest solved** | **cheaper than all:** 0.24–0.75× | tie with all |
| **SWE-bench Verified, SymPy** (real GitHub issues; official tests; 2 repeats; V1-era harness) | 12×2 | 24/24 (Codex 23, OpenCode 23, Pi 24, Hermes 24) | tie with all | **cheaper than all:** 0.28–0.63× | faster than Hermes; tie with others; fastest median (31.8 s) |
| **Broad suite** (chat, debug, build, hard, data, deep; V1-era) | 42 | 42/42 | tie with all | **cheaper than all:** 0.25–0.67× | faster than Pi and Hermes; tie with Codex and OpenCode |

**What MeshPool can claim today:**
- The cheapest harness on every benchmark run, significantly, against Pi, Codex, OpenCode and Hermes.
- The fastest on data analysis.

**What it cannot claim yet:** better quality on hard coding tasks. Codex solved the most there.

## MeshPool V1 → V2 (same hard tasks)

| | V1 | V2 |
|---|---|---|
| Solved (hard V2 subset) | 21/24 | 19/24 (tie, p=0.73) |
| Cost (paired) | 1.00× | **0.59×** (significant) |
| Output tokens per task | 4,034 | 2,630 |
| Median s per task | 52.4 | 42.1 |

## Per-harness numbers

### V2 hard suite (24 tasks)
| Harness | Solved | $ / solved | Median s | P90 s | Calls / task |
|---|---|---|---|---|---|
| MeshPool V2 | 19/24 | $0.0044 | 42.1 | 186.7 | 13.4 |
| MeshPool V1 | 21/24 | $0.0054 | 52.4 | 169.2 | 14.0 |
| Codex | 24/24 | $0.0039 | 24.1 | 72.5 | 6.3 |
| OpenCode | 22/24 | $0.0147 | 26.9 | 172.1 | 7.3 |
| Pi | 23/24 | $0.0116 | 32.3 | 71.2 | 6.6 |
| Hermes | 22/24 | $0.0077 | 36.8 | 77.6 | 7.8 |

### DABench (11 questions)
| Harness | Solved | $ / solved | Median s |
|---|---|---|---|
| MeshPool V2 | 10/11 | $0.0013 | 7.1 |
| MeshPool V1 | 10/11 | $0.0014 | 8.0 |
| Codex | 10/11 | $0.0031 | 11.5 |
| Hermes | 10/11 | $0.0051 | 21.2 |
| Pi | 10/11 | $0.0129 | 15.6 |
| OpenCode | 10/11 | $0.0147 | 14.7 |

### SWE-bench Verified, SymPy (12 issues × 2)
| Harness | Solved | $ / solved | Median s |
|---|---|---|---|
| MeshPool (V1-era) | 24/24 | $0.0101 | 31.8 |
| Codex | 23/24 | $0.0164 | 57.0 |
| OpenCode | 23/24 | $0.0282 | 48.0 |
| Pi | 24/24 | $0.0356 | 61.0 |
| Hermes | 24/24 | $0.0224 | 82.8 |

## Not yet run
- **SpreadsheetBench Verified:** needs LibreOffice.
- **OfficeBench:** the adapter is still to build.
- **The full 52-task V2 evaluation and DABench 24:** the commands are in `handoff prompt.md`.
- **Full SWE-bench Verified:** needs Docker and approval. MeshPool alone would cost about $5–13 over 500 tasks; all
  five harnesses $56–139, over the budget.

Budget: **$10.02 of $25 spent, $14.98 left.**
