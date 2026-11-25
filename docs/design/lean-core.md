# Lean core: rebuilding the code harness to be cheaper than Codex at equal quality

Status: proposal. Inputs: source reading of Codex CLI (codex-rs), Pi (pi-mono coding-agent) and OpenCode (v1.4.0 and HEAD); a literature review; our own paired benchmarks (final12-final14, 52 tasks x 2 reps vs Codex 0.171 on the same model and gateway).

## What the measurements say

| Build | Solved (Meshpool vs Codex) | Cost per solved vs Codex | Median time vs Codex | Median calls |
|---|---|---|---|---|
| 283eced2 | 92/98 vs 87/98 | 1.25x (0.87-1.90) | 1.59x (1.11-2.33) | 7 vs 6 |
| + temperature fix | 95/104 vs 94/104 | 1.57x (1.23-1.97) | 1.77x (1.33-2.41) | 8 vs 6 |
| + all runaway guards (8ba192ea) | 94/104 vs 96/104 | 1.63x (1.24-2.06) | 1.91x (1.46-2.38) | 8 vs 6 |

Quality is level. Each call costs about the same as Codex's (output and cache rate equal). The whole gap is the number of calls: 66% more, from 4.5 test runs and 5.1 writes per task against Codex's 2.0 and 2.0. Runaways are gone (0 capped replies, guards barely fired), so what is left is steady extra steps on every task.

## What the incumbents do

None of Codex, Pi or OpenCode runs a grader, a reviewer, verify notes or a forced plan. A turn ends when the model stops calling tools; a failed tool call is an ordinary observation. Common to all three:

1. A small, stable prefix: Pi about 0.7k tokens with 4 tools; OpenCode cut its prompt from 8 KB to 1 KB; Codex's tuned prompts are 6-7 KB.
2. Few tools: shell, read, one strong edit tool (exact match with tolerant fallback; Codex and Pi take many edits per call), write.
3. Parallel tool calls on by default.
4. Append-only history with a stable cache key; compaction only near the window edge.
5. Bounded tool output that keeps the head and tail, with a way to get the rest.
6. No sampling parameters (provider defaults).

## What the research says

- Swapping the harness changes solve rate by about as much as rerunning the same one; it sets the bill (up to 3x), mostly through the fixed prefix paid on every step.
- Prompts that push the model to test more add calls and output tokens for no extra solves; prompts that discourage tests cut calls 25-35% at the same solve rate.
- Self-checking without external feedback does not find errors. Checks belong in the tools (reject edits that break parsing, show the changed region), not in extra model turns.
- Masking old observations matches LLM summaries at about half the cost; under cache-aware pricing, append-only beats deleting history.

## Design

Default code lane ("lean core"):

- Prompt: at most about 1.5k tokens. Identity, environment, tool use, "keep going until it is done", "run the most specific relevant check once before you finish", "be concise". No method card, no plan requirement, no verify-each-step text.
- Tools: shell, read, edit (many exact replacements per call, tolerant matching, parse check on code files), write. Nothing else unless the task asks for it (office files, browser).
- Parallel tool calls on.
- Removed from the default path: route method card, independent grader, review, mutation, keep-note, requirement note, todo or plan nudges. They stay available as profile switches.
- Kept, since they cost nothing unless something loops: the repeat refusal, refusal, failure and budget escalation, the shape guard, the private scratch dir, process cleanup, no temperature.
- Output: head and tail kept at 2000 lines or 50 KB; the full output goes to a file the model can read.
- Context: append-only, stable prefix, cache key per session; compaction only near the window edge.

## How it is measured

One benchmark: the 52-task suite, paired, 2 reps per arm, Codex 0.171 twice in the same window, a private /tmp per run, parallel 3. Report solved/total, paired cost per solved and time with bootstrap CI, and median calls.

Bar to ship: solve rate not lower than Codex (CI lower bound above -5 points), and cost per solved below Codex with the CI upper bound below 1.0. Any layer added back must win the same paired test on cost per solve.

## Work lane candidate (MH_LEAN_WORK=1, off by default)

The Work (Cowork) chat runs the same lean core: Codex style, `prompt-core.md` plus `prompt-work.md` (data answers: compute with code over the full data, check duplicates, missing values, types and units first, give every requested field, respect length limits, round at the end; office and web tool pointers), workspace preload, re-check on edit, no grader or route card. Tools: shell, apply_patch, update_plan, office_task, web_research, fetch_url, and the office MCP tools whenever the office server is connected. Headless, the fixed prefix drops from about 6.2k tokens (Work prompt 3.2k + 19 tools 3.0k) to about 2.2k (0.9k + 1.3k). Not yet benchmarked.
