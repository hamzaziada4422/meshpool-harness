# Cost per solved task: what the research and other harnesses say, and what Meshpool does about it

Status: applied to the lean code lane (profile `core`) on claude/amazing-hopper-j9708m. Companion to
`docs/design/lean-core.md`, which covers the measured gap and the incumbents' architecture.

## Our own measurements

On the 52-task suite, every Meshpool build since the original Codex-design engine (0.0.7, 0.94x Codex's cost per
task) has been more expensive than Codex at the same solve rate: 1.25x-1.63x on 283eced2 through 8ba192ea. Per call
we cost about the same as Codex; the gap was always the number of calls. Turn decomposition of final14 (104 runs per
arm), turns per task:

| Phase | Meshpool 8ba192ea | Codex 0.171 |
|---|---|---|
| Before the first edit (ls, cat, git log, versions) | 3.0 | 3.2 |
| Edit turns | 2.3 | 1.2 |
| Non-edit turns between edits (patch, compile, check loops) | 2.4 | 0.4 |
| After the last edit | 2.0 | 1.4 |
| Total | 9.7 | 6.2 |

On the 12 tasks that carry the whole gap (dev1/dev2, 2 reps): `core` 1.82x Codex's cost per solved task, `core` with
the economy prompt 1.14x (9.6 calls per run against Codex's 8.8).

## Sources and what they found

1. **Analyzing and Mitigating Cost-Inefficient Behaviors in Coding Agents** (arXiv 2609.30725, 2026). Three
   behaviours waste cost in Claude Code and mini-SWE-agent: subsumed retrieval (re-reading code already in context;
   2-6 times per task, 5-11% of cost), similar-script regeneration (numbered variants of the same helper; up to 10%)
   and test re-execution without a code change (up to 5%). Seven developer-written principles in the system prompt
   (reuse context, targeted patches, understand the test harness, capture test output instead of re-running, persist
   one artifact instead of variants, break loops and submit the best solution) cut cost 14-42% with no robust loss of
   solves. Structure-aware retrieval tools (a code graph) raised cost.
2. **System Optimizations for Cost-Efficient Agent Harnesses** (arXiv 2611.12122, Berkeley, 2026). Harness choice
   barely moves accuracy but moves cost 2-3x; Claude Code and Codex cost about 2x a basic harness because of their
   prompts and tools. Re-reading context is up to 73% of cost even with caching; agents use 27% of tool output; file
   search can be most of the turns. Tool-output compaction (strip noise, deduplicate), context garbage collection and
   metadata-assisted search cut cost up to 35% at equal accuracy. A prompt asking for batched tool calls raised batching
   to 1.8 calls per turn.
3. **More with Less: Turn-Control Strategies** (arXiv 2510.16786). A turn limit at the 75th percentile of the
   baseline cuts cost 24-68% with small solve losses; a dynamic budget (start low, extend once when needed) is better
   than a fixed one. Meshpool's runaway budget (converge note, then wrap) is this pattern.
4. **The Complexity Trap** (arXiv 2508.21433). Masking observations older than 10 turns halves cost at the same solve
   rate as LLM summaries. Not adopted yet: on a gateway where cached input costs 2% of fresh input, masking breaks the
   cached prefix and saves little; it matters only for long trajectories.
5. **SWE-agent ACI** (NeurIPS 2024) and **mini-SWE-agent**: interfaces should be simple, concise and consolidate common
   operations into one action; a 100-line bash-only agent scores near the state of the art. Fewer, better actions
   beat more tools.
6. **Agentless** and **Aider's repo map**: give the model the project's layout and relevant code up front instead of
   letting it explore; the exploration rounds disappear without costing solves.
7. **Cognition (Devin), "Don't Build Multi-Agents"** and **LangChain, "Context Engineering for Agents"** (write, select,
   compress, isolate): one agent with a well-selected context beats orchestrated sub-agents; select what goes into
   context deliberately and compress tool output. Our grader and review layers were the opposite (extra agents
   re-reading the whole task) and are off in `core`.
8. **Anthropic, "Writing effective tools for agents"**: tool responses should be token-efficient (filter, truncate,
   paginate) and errors should tell the model exactly what to do next.

## What Meshpool's lean lane does now

| Change | Source | Where |
|---|---|---|
| No method card, notes, grader or review in the default code lane | 2, 7; final13-15 | profile `core` |
| Economy prompt: batch reads, no environment digging, one check at the end, no extra check scripts, stop when green | 1, 2, 5 | `defaults/engines/codex/prompt-core.md` |
| Reuse context, capture a long run's output once, one helper file not numbered variants, break loops and submit | 1 | prompt-core.md |
| Project files, git state and relevant source given up front | 6, 2 | `agent/preload.py` |
| A whole-file `cat` of text the model already has, unchanged, answered with a pointer instead of the text | 1 (subsumed retrieval), 2 | `core/tools.py` `_reread` |
| An edit and its check in one turn; calls after a failed edit in the same turn are skipped | 2 (batching), 5 | `agent/core.py`, prompt |
| Repeated identical output lines collapsed with a count | 2 (output compaction), 8 | `core/tools.py` `_squeeze` |
| Edits allowed in the task's private scratch folder (were 8 of 18 patch errors) | 8 | `core/tools.py` |
| Provider-default sampling (greedy decoding looped to the output cap) | final12 | `profiles/deepseek.yaml` |
| Runaway budget, shape guard, failure and refusal escalation kept | 3 | `agent/core.py` |

Switches for A/B: `MH_PRELOAD=0`, `MH_REREAD_NOTE=0`, `MH_SQUEEZE=0`, `MH_CORE_PROMPT_FILE=<path>`.

## Not adopted, and why

- Observation masking and LLM summaries: break the cached prefix; on cache-discounted pricing they pay only on long
  trajectories (see 4). Compaction near the window edge stays.
- Code-graph or structure-aware retrieval tools: raised cost in 1.
- Multi-agent orchestration and sub-agent reviewers: 7, and our own grader cost 2-4x the task it checked.
- A cheaper model for sub-steps: out of scope for a model-agnostic comparison on one model.
