# WP0 research: what raises solve rate on a cheap model

Date: 2026-11-19. Scope: techniques not already covered in OSS_RESEARCH.md. About 22 sources, most read through
abstracts or search summaries, a few (Agentless, SWE-agent) read as full-text tables. Evidence grades:
**A** = ablation in the paper with numbers on the benchmark; **B** = measured but on a different model/task or
vendor/secondary report; **C** = plausible, mechanism only or no direct measurement. Almost all numbers below come
from SWE-bench-style issue fixing with 2024-25 models. None were measured on DeepSeek V4.1 Flash or on
build-from-spec / data / test-writing tasks, so treat every gain as a prior, to be confirmed by the dev A/B.

## Ranked table (expected gain per dollar for us)

| # | Technique | Measured gain (source) | Evidence | Cost impact | How to do it here | Maps to |
|---|---|---|---|---|---|---|
| 1 | Forgiving edit tool: strip line-number prefixes, whitespace-normalised unique match, closest-region error, list of ambiguous matches | SWE-agent: 51.7% of trajectories had a failed edit; success of any edit try 90.5%, but after one failure only 57.2% [1]. Aider: disabling flexible patching raised edit errors 9x [2]. Lint-on-edit: +3.0 pts (18.0 vs 15.0) and no edit tool at all -7.7 [1] | A (old models) | Negative (fewer wasted turns) | Already specified in WP2. Add: run ruff/`py_compile` on the edited file and return the error with the edit result; reject the edit if it introduces a syntax error (the SWE-agent behaviour). | WP2 |
| 2 | Differential oracle: brute-force literal-spec reference plus random small inputs (Hypothesis) plus one large timing input | No direct end-to-end pass-rate number. Supporting: brute-force oracle used to filter wrong LLM solutions (UniCode) and as verifier (AutoCode, 98.7% agreement with judges) [3]; S*: LLM-generated *distinguishing inputs* between candidates let Qwen2.5-7B+S* beat Qwen2.5-32B by 10.7 pts on LiveCodeBench [4]; Agentic Hypothesis testing: 56% of sampled reports valid bugs [5] | B/C (indirect) | Small (+1-2 turns on build tasks) | Method text in `code.build`: write `ref_*.py` brute force straight from the spec, `hypothesis` or a seeded random loop comparing to the real one, print the first counterexample, shrink by hand. `hypothesis` is already installable; ship a 20-line helper in the method prompt. | WP6 (+WP4) |
| 3 | Mutation feedback for test-writing, computed by the harness | MuTAP: prompting with *surviving mutants* gave 93.57% mutation score and up to 28% more buggy programs caught than Pynguin/zero-shot [6]. Meta ACH: tests written to kill mutants, 73% accepted by engineers, mutant kill rate 12-15% vs 2.0-2.4% for coverage-only generation [7] | A (on test generation, not our grader) | Near zero tokens until a mutant survives; one short follow-up turn | After tests are written, run mutmut/cosmic-ray style operators (AST flips) on the file under test within a time cap; return "line N: `<` -> `<=` survives". Cap at ~15 mutants and 2 rounds. | WP7 |
| 4 | Execution-grounded selection, second sample only when weak | Agentless: majority vote 25.67%, +regression tests 27.00%, +generated reproduction tests 32.00% (+6.3 pts for $0.25 of $0.70 per issue) [8]. CodeMonkeys: voting on model-written tests plus a selection trajectory; 57.4% Verified for ~$2300 and 66.2% when selecting over an ensemble, better than any member [9]. Large Language Monkeys: DeepSeek-Coder-V2 15.9% with 1 sample, 56% with 250 (coverage, not selection) [10]. OpenHands critic: 60.6 -> 66.4% with 5 attempts [11]. Trae: dedup + regression-test pruning + selector, 75.2% Verified [12] | A for the pipeline, B for our setting | +1 attempt only on the weak tail (about 30-40% of tasks if the trigger is strict) -> ~+15% average | WP8 as written. Pick by execution: the candidate passing more of the union of both attempts' own checks and the existing tests; ties go to the smaller diff. Never ask the model to "rank" patches (see not-pay-off list). | WP8 |
| 5 | Requirement checklist built before coding, ticked at the end | No direct measurement found. Mechanism: Agentless/SWE-agent failures are mostly missed conditions, not tool failures; DeepSeek failures here (edge cases) match | C | Near zero (regex pass + 1 list in prompt) | WP9 as written: regex extraction of numbers, quoted strings, "must/never/only/at most", error types; the check script must contain one assertion per item. Cheapest item on this list, so keep it in the A/B despite weak evidence. | WP9 (+WP4) |
| 6 | Data-question protocol: data-quality pass first, compute twice independently, answer only if equal | No direct benchmark. DA-Code best LLM agents ~30% [13]; AgenticDataBench lists "failure to repair from execution feedback" and quick-answer failures among the main categories [14]. Two-way computation is self-consistency with different code paths, known to help when errors are independent | C | +1-2 tool calls on data tasks | Method text plus a harness helper that prints duplicates by normalised key, null counts, mixed types, tz/date-format hints, before any answer. DuckDB SQL as the second path (already planned). | WP4 data, WP6 |
| 7 | Independent review whose findings must be executable | OpenHands critic and Trae selector help when grounded in rollouts [11][12]. But LLM reviewers show systematic over-correction: they flag correct code as failing, worse with "explain and propose a fix" prompts [15] | B (mixed) | +1 short call | Keep WP3 as designed (findings must include an input and expected output, agent verifies by code, ignore refuted ones). Add: skip the reviewer when the final check already ran green and covered every checklist item, to avoid inventing work. | WP3 |
| 8 | Context discipline: collapse old observations, 100-line file windows, summarised search | SWE-agent: keeping last 5 observations 18.0 vs full history 15.0 (+3.0); 100-line window 18.0 vs 30-line 14.3 vs whole file 12.7; summarised search 18.0 vs iterative 12.0 [1] | A (GPT-4 era) | Negative (fewer input tokens) | Check what `agent/core.py` does with old tool results; truncate observations older than N turns to a one-line stub, cap read_file default to ~100-150 lines with a "more" hint, cap grep to 50 hits with file counts. | new (no WP) |
| 9 | Line-range edit as a second edit mode | Preprint: line-range edits accepted first try 99% vs 58% for search/replace on large-file refactors, same solved share, so the gain was retries [16]. Aider: DeepSeek models already follow the diff format 96.2-99.2% of the time [2] | B/C (single preprint) | Negative on large files | Only if WP2's fuzzy match still leaves failures: add `edit_lines(path, start, end, new)` that requires the agent to have just read those lines. Measure edit-failure rate from logs first. | WP2 extension |
| 10 | Architect/editor split | Aider benchmark: self-paired architect/editor +3 to +4.6 pts for 4o, Sonnet, 4o-mini; o1-mini + DeepSeek editor +10.3 [17] | B (old models, edit-only benchmark) | +30-60% (two calls per step) | Not worth it with one model. Cheap variant: one "plan the change as a list of concrete edits" turn before the first edit on code.build, no second model. | new, low priority |
| 11 | LSP tools (SolidLSP) | SWE-Master: MiniMax-M2.1 68.4 -> 70.4, GLM-4.7 66.2 -> 67.6, 5 fewer turns on Python [18]. One unverified preprint reports no gain and higher cost on C++ and low use unless mandated [18b] | B | + a language-server process per repo | Defer. Gains are 1.4-2 pts at best, and only matter for cross-file refactors. | new, later |
| 12 | Repo map (tree-sitter + PageRank) | No isolated ablation found. Aider's SWE-bench Lite 26.3% with 70.3% right-file rate was the full stack [19]. A blog reports 41.9 -> 50.4% from a structural index on Opus; not verified | C | + prompt tokens each turn | Only for large repos. For single-file build tasks it adds nothing. | new, low |

## Notes on the top 5

**1. Edit tool.** This is the one measured effect that matches a failure we see directly. SWE-agent's numbers [1]
show the damage is not the failed edit but the spiral after it: success after the first failure falls from 90.5% to
57.2%. Aider's finding that turning off flexible matching multiplies errors 9x [2] says the same thing from the other
side. Implement WP2 as specified, plus the syntax gate (reject an edit that makes the file unparseable, show the
parser error and the old lines). Log edit failures per run so the A/B can report them. A cheap model is more
sensitive here than frontier models, so expect a larger gain than the +3 pts the 2024 ablation shows.

**2. Differential oracle.** The strongest argument is mechanical: our observed failures are edge cases in
build-from-spec tasks that survive the agent's own hand-written tests because the agent writes tests from its own
reading of the spec. A literal brute force is a second, dumber reading. Random inputs find the disagreement; the
agent then decides which side is wrong against the spec text. Evidence that this lifts pass rate end to end is
indirect (verification of competitive-programming solutions [3], S* distinguishing inputs [4]). Caution from [20]:
volume of agent-written tests did not change SWE-bench outcomes and most were print statements. So the gain, if any,
comes from the oracle and the counterexample, not from "more tests". Keep it targeted and put the hard rule in the
method: print a counterexample or the line "no disagreement in N cases".

**3. Mutation feedback.** The harness does the work and the model reads only survivors. MuTAP and ACH [6][7] both
show survivors-in-prompt beats coverage-only prompting by a wide margin. The unknown is how closely our grader's
mutants resemble ours; use standard operators (relational, arithmetic, boundary constants, negation, return value,
remove branch) and report the surviving line plus the operator so the model knows what assertion to add. Equivalent
mutants waste turns: Meta needed preprocessing to push equivalence detection from 0.47 to 0.96 recall [7], so cap
rounds and let the agent mark a mutant "equivalent" with a one-line reason.

**4. Adaptive second attempt.** The one result that is both large and cheap: Agentless got +6.3 pts from selecting
with generated reproduction tests for $0.25 per issue [8], and the oracle-vs-selected gap stays big in later work
(a verifier recovered about 2 of ~8 points of headroom in one setup, so selection is not free either). Cost control is
the trigger, as already planned. Idea from inference only (no source): DeepSeek-style prefix caching makes a second
attempt that shares the system prompt and the exploration-phase context much cheaper than a fresh one, so fork the
second attempt after the read-only exploration steps rather than from zero. Needs a check against our actual billing.

**5. Requirement checklist.** No paper measures it, which is why it is ranked fifth despite being nearly free. It
is the cheapest way to attack "many edge cases solved wrong" and it feeds items 2 and 3 (each checklist line becomes
an assertion and a oracle comparison). Include it in the A/B with the other WP4 changes, and report it separately if
the budget allows.

## Things that do NOT pay off (or not for us)

- **More agent-written tests as such.** Across six models on SWE-bench Verified, resolved and unresolved tasks wrote
  tests equally often, prompts that increased or decreased test writing did not change outcomes, and GPT-5.2 wrote
  almost none yet matched top agents [20]. (Single preprint; hence the "targeted oracle, not volume" framing above.)
- **LLM-only review or self-critique with no execution.** Reviewers wrongly reject correct code, and richer prompts
  ("explain and propose a fix") raise the error rate [15]. Self-correction without external feedback is also weak
  in general (Huang et al., "LLMs cannot self-correct reasoning yet", ICLR 2024; recalled, not re-fetched). Keep WP3
  findings executable.
- **Asking the model to rank or "judge" candidate patches.** Execution-based selection is what the winning systems
  use [8][9][12]; the verifier gap remains large even with a trained critic. Training our own critic (OpenHands
  critic is a fine-tuned 32B, later 4B model [11]) is out of scope.
- **MCTS / tree search (SWE-Search).** +23% relative on SWE-bench Lite across five models, but at many times the
  token cost of one trajectory [21]; we are allowed +10%. Evidence A, cost makes it a non-starter.
- **Heavy sampling on a small model.** Coverage keeps rising with samples (15.9% to 56% at 250 samples for
  DeepSeek-Coder-V2 [10]) but selection recovers only part of it, and returns diminish for small models. At our
  budget only one extra sample on the weak tail is affordable.
- **Emotional or "tip" prompts.** Aider measured worse results for both edit formats [22].
- **Full history in context, long file views, iterative search.** All measured worse than the compact versions
  (-3.0, -5.3, -6.0 pts) [1].
- **Switching edit format wholesale.** DeepSeek models in Aider's leaderboard already follow the diff format
  96-99% of the time [2]. The Codex `apply_patch` format is tuned to GPT models; a community write-up (unverified
  source) reports a 6.7% success rate for one non-GPT model with it. Fix matching, not format.
- **Whole-file rewrites** on large files: robust but costly; only useful for small files.
- **LSP and repo map as first-wave work.** Gains of 1.4-2 pts on Python at best [18] and no clean repo-map ablation
  [19]; neither addresses our four failure causes. ast-grep: no published solve-rate measurement found; fine as a
  tool, not as a quality lever.
- **mini-swe-agent as a technique.** It shows a bash-only loop is competitive on strong models, i.e. that complex
  scaffolding is not needed. It does not offer a gain we can borrow. Leaderboard numbers seen in search snippets
  were not verifiable and are not used here.

## Suggested adjustments to the plan

1. Run order for the A/B: WP2 and WP4 first (cheap, certain); then WP6 and WP9 inside WP4's method text; WP7 as a
   harness feature; WP8 last because it is the only one that raises cost.
2. Add context discipline (row 8) to WP2's file as a one-line change to observation truncation; it lowers cost.
3. WP3: reviewer is skipped when the final check is green and covers the checklist.
4. Log per-run: failed-edit count, turns after first failed edit, surviving mutants at start and end, WP8 trigger
   rate, so the A/B explains itself.

## Sources

[1] SWE-agent, Yang et al. 2024, arXiv 2405.15793 (ablations in the HTML version, tables on linting, window, search, history, edit recovery).
[2] Aider edit leaderboard https://aider.chat/docs/leaderboards/edit.html and unified diff post https://aider.chat/2023/12/21/unified-diffs.html
[3] AutoCode (ICLR 2026) and UniCode https://arxiv.org/html/2510.17868v1 (brute-force oracles); Mokav https://arxiv.org/html/2406.10375v1
[4] S*: Test Time Scaling for Code Generation, arXiv 2502.14382
[5] Agentic Property-Based Testing, arXiv 2510.09907; Anthropic write-up https://red.anthropic.com/2026/property-based-testing
[6] MuTAP, arXiv 2308.16557
[7] Mutation-Guided LLM-based Test Generation at Meta, arXiv 2501.12862
[8] Agentless, arXiv 2407.01489 (Table 3 and 4 read from the HTML version)
[9] CodeMonkeys, arXiv 2501.14723
[10] Large Language Monkeys, arXiv 2407.21787 (DeepSeek-Coder-V2 figure from a secondary summary)
[11] OpenHands critic and inference-time scaling https://openhands.dev/blog/sota-on-swe-bench-verified-with-inference-time-scaling-and-critic-model ; arXiv 2603.03800
[12] Trae Agent, arXiv 2507.23370 (ablation scores not read)
[13] DA-Code, arXiv 2410.07331
[14] AgenticDataBench, arXiv 2607.01647; DABstep arXiv 2506.23719
[15] Are LLMs Reliable Code Reviewers? Overcorrection, arXiv 2603.00539; also arXiv 2508.12358
[16] Line-range vs search/replace preprint, preprints.org 202610.1889 (not read in full); SWE-Edit arXiv 2604.26102
[17] Aider architect/editor https://aider.chat/2024/09/26/architect.html
[18] SWE-Master, arXiv 2602.03411 (LSP ablation); [18b] "Language Server Tax" preprint seen only via a secondary blog
[19] Aider repo map: no ablation found; SWE-bench Lite result described in Aider's blog
[20] Rethinking the Value of Agent-Generated Tests, arXiv 2602.07900 (v1 preprint)
[21] SWE-Search, arXiv 2410.20285 (ICLR 2025)
[22] Aider unified diffs post (emotional prompts), same URL as [2]

Not verified here: Claude Code and Codex CLI practices (plan, verify with tests, apply_patch) were taken from product
documentation knowledge, not fetched; Moatless and Refact returned no measurable ablations; AutoCodeRover reports
19% on SWE-bench Lite at $0.43 per issue with test-based fault localisation only when tests exist (arXiv 2404.05427),
which did not change the ranking.
