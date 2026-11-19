# Meshpool harness router: benchmark results

Date: 19 November 2026 · Branch `harness-router` on `hamzaziada4422/meshpool-harness` (PR #1, not merged)

## Bottom line

Meshpool was compared with Pi, Codex, OpenCode and Hermes on the same model (DeepSeek V4.1 Flash), the same tasks,
at the same time, graded by tests rather than by a judge. In both head-to-head benchmarks below:

- **Quality:** Meshpool solved every task it was given: 24 of 24 real GitHub issues and 42 of 42 tasks in the broad
  suite. It was never worse than any other harness beyond chance. A real quality *lead* is not proven yet, because
  every harness solves almost everything on these tasks (see Limits).
- **Cost:** Meshpool was the cheapest harness in every comparison, beyond chance. On real GitHub issues it cost
  0.28 to 0.63 times what the others cost per solved task.
- **Speed:** Meshpool had the fastest median time per solved issue. It was significantly faster than Hermes (and
  than Pi on the broad suite). Against Codex and OpenCode the difference is a statistical tie.

"Beyond chance" means the 95% interval of the difference excludes "no difference" (paired bootstrap for cost and
time, McNemar's exact test for solves).

---

## 1. Real GitHub issues: SWE-bench Verified (SymPy), 5 harnesses, same model

12 real SymPy issues from SWE-bench Verified, each run twice per harness (24 runs each). Graded the official way:
the issue's hidden test patch is applied after the agent finishes, and every FAIL_TO_PASS and PASS_TO_PASS test must
pass. Only tasks that fail as they stand and pass with the official fix on this machine were used.

| Harness | Solved | Solve rate (95% CI) | Cost per solved issue | Median time per solved issue | Model calls per task |
|---|---|---|---|---|---|
| **Meshpool** | **24/24** | **100% (86–100%)** | **$0.0101** | **31.8 s** | 19.2 |
| Codex | 23/24 | 96% (80–99%) | $0.0164 | 57.0 s | 20.0 |
| OpenCode | 23/24 | 96% (80–99%) | $0.0282 | 48.0 s | 16.2 |
| Pi | 24/24 | 100% (86–100%) | $0.0356 | 61.0 s | 17.7 |
| Hermes | 24/24 | 100% (86–100%) | $0.0224 | 82.8 s | 19.8 |

Meshpool against each harness on the same tasks and repeats:

| vs | Quality | Cost (Meshpool ÷ them) | Time (Meshpool ÷ them) |
|---|---|---|---|
| Pi | tie | **0.28× (0.21–0.38), cheaper** | 0.79× (0.59–1.11), tie |
| OpenCode | tie (Meshpool solved one it missed) | **0.36× (0.25–0.46), cheaper** | 1.07× (0.77–1.41), tie |
| Hermes | tie | **0.45× (0.32–0.59), cheaper** | **0.69× (0.47–0.93), faster** |
| Codex | tie (Meshpool solved one it missed) | **0.63× (0.47–0.80), cheaper** | 0.96× (0.70–1.24), tie |

Why it is cheaper: Meshpool sent 224k input tokens per issue on average, against 486k for Codex, 550k for Hermes,
784k for Pi and 815k for OpenCode, and wrote fewer output tokens than all but OpenCode.

Flaky tasks (solved in one repeat but not the other): Codex on sympy-23950, OpenCode on sympy-23413. Pi hit 3
endpoint stream timeouts ("Body Timeout") and still solved those tasks.

## 2. Broad suite: 5 harnesses, same model (42 tasks)

Chat (10), debugging (8), building (4), hard multi-file bugs (8), data analysis (7) and a "deep" suite of
multi-file tasks with hidden tests (5 of 6 ran). One repeat. Graded by code.

| Harness | Solved | Cost per solved task | Time per solved task | Model calls per task |
|---|---|---|---|---|
| **Meshpool** | **42/42** | **$0.0012** | **7.5 s** | 4.5 |
| Codex | 42/42 | $0.0019 | 8.4 s | 3.6 |
| OpenCode | 42/42 | $0.0018 | 8.7 s | 4.6 |
| Pi | 41/42 | $0.0036 | 10.1 s | 3.9 |
| Hermes | 42/42 | $0.0047 | 22.1 s | 5.3 |

| vs | Quality | Cost (Meshpool ÷ them) | Time (Meshpool ÷ them) |
|---|---|---|---|
| Codex | tie | **0.62× (0.51–0.74), cheaper** | 0.89× (0.70–1.10), tie |
| OpenCode | tie | **0.67× (0.54–0.81), cheaper** | 0.86× (0.67–1.07), tie |
| Pi | tie | **0.34× (0.29–0.40), cheaper** | **0.77× (0.57–0.99), faster** |
| Hermes | tie | **0.25× (0.20–0.30), cheaper** | **0.34× (0.27–0.41), faster** |

## 3. Across models: Meshpool against its own previous agent and Pi (earlier version)

30 tasks (chat, debug, build, hard) × 3 repeats per model. This ran before the latest speed changes (lean code
harnesses, no todo list for focused fixes, reproduce-first, error context, parallel tools, issue routing).

| Model | vs the previous Meshpool agent | vs Pi |
|---|---|---|
| DeepSeek V4.1 Flash | same quality; **cost 0.51×**; speed tie | same quality; **cost 0.56×**; speed tie |
| Claude Haiku 5.5 | same quality; **cost 0.56×**; speed tie | same quality; **cost 0.17×**; Pi faster (1.28×) |
| GPT-6 Luna | same quality; **cost 0.51×**; speed tie | same quality; **Pi cheaper (2.72×) and faster (1.67×)** |
| Kimi K3 | not comparable: the Meshpool team budget ran out mid-run | not comparable (same reason) |

GPT-6 Luna is the one model where Pi was clearly better at the time. The later changes target exactly why (extra
calls and output tokens). The benchmark has not yet been rerun on GPT-6 Luna.

---

## Method

- **Same conditions:** every harness ran on the same model through the same endpoint, on the same task, at the same
  time, in its own isolated data folder. Pi, Codex, OpenCode and Hermes ran through Meshpool's own integrations,
  configured as users get them.
- **Same meter:** every harness's model calls went through one metering proxy, so tokens and calls were counted from
  the endpoint's own replies, not from each harness's self-report.
- **Graded by tests:** hidden tests, official SWE-bench test patches, or exact answers computed by code. No judge model.
- **Statistics:** Wilson intervals for solve rates; McNemar's exact test for paired solves; paired bootstrap for cost
  and time ratios. Timings from runs that spanned the laptop sleeping were left out of the speed comparisons.
- **Cost:** tokens × the model's price, scaled to billed key spend where the endpoint reported it.
- **Code:** `testkit/loop/` (`bench.py`, `meter.py`, `swe_local.py`, `optimize.py`, suites in `suites/`).

## Limits (what this does not prove yet)

1. **No quality lead yet:** almost every harness solves almost every task here. Showing Meshpool solves *more*
   needs harder benchmarks: full SWE-bench Verified across all repositories, SWE-bench Pro, DeepSWE, Terminal-Bench.
   These need Docker, so they run in the final CI pass.
2. **Small samples:** 12 real issues × 2 repeats; 42 broad tasks × 1 repeat. More repeats will tighten every interval.
3. **Mostly one model:** the 5-harness comparisons used DeepSeek V4.1 Flash. The multi-model run predates the latest
   changes, and Claude Code is not in the comparison yet.

## Budget

$4.66 of the $25 cap spent (tracked from the key's billed spend, with GitHub runs included); **$20.34 left**.
GitHub Actions are all disabled until the final run.

## What is in Meshpool now (since this work started)

- **Harness router:** every chat is routed to a specialised harness (Chat, Debug, Build, Refactor, Review, Tests,
  Terminal, Explain, Research, Data, Documents, Browser, Automation). Debug and Build also have sub-types (syntax,
  test, exception, regression, performance; CLI, API, UI, function), plus language packs. The route shows as a chip
  in the chat that can be clicked to change it.
- **Zero-token work before the first model call:** reproducing the named failing command; showing the failing lines;
  previewing data files; a repo map.
- **An instant syntax and bug check on every edit.** Independent reads run in parallel. Focused fixes run with a lean
  tool set.
- **A review receipt after every change:** what changed, which command checked it, or a plain "not verified".
- **Memory v2:** relevant recall, per-project notes kept outside the project, fenced memory that cannot leak into
  replies.
- **Robustness:**
  - a refused model setting is adjusted and retried;
  - a spent budget stops at once with a clear message;
  - a masked API key gets one clear message;
  - Pi's lost replies are fixed;
  - the "NaN d ago" sidebar bug is fixed.
- **The benchmark loop:** head-to-head against Pi, Codex, OpenCode and Hermes; local SWE-bench; and a Harness²-style
  optimizer that keeps a harness change only when the statistics say it helps.
