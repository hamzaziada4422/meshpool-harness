# Meshpool Harness Router: plan

Status: in progress. Slice 1 is built on branch `harness-router` (see "Built so far" at the end).

Decisions (from review):
1. Model-agnostic. The harnesses never depend on one provider; the loop measures each on a cheap model.
2. Budget: $20 for measurement, on cheap models through the Meshpool endpoint.
3. Language packs: ordered by real use (TypeScript and Python first, then JavaScript, Java, C#, Go, Rust, C/C++).
4. Chat: today it is too slow and too wordy compared with Claude Cowork. Fixing this is first priority.
5. OpenResearch: borrow its ideas *and* add it to the pool as a harness. Use as many harnesses as help.
6. Home repo: `hamzaziada4422/meshpool-harness`.

## Goal

One router in front of a family of small, highly tuned harnesses. On every task category it must be:

1. **At least as good** as the best frontier harness (Pi, Codex CLI, Claude Code, OpenCode, Hermes) on a frozen benchmark set, run with the same model at the same time.
2. **Cheaper.** Target at most 0.7x the frontier harness's cost per solved task.
3. **Faster.** Target at most 0.8x its time per solved task.

Ship rule: a harness ships when its quality is within noise of the best (the existing 5-point `quality_band`) *and* it is cheaper or faster. If a frontier harness still wins a category, the router sends that category to it. Pi, Codex and OpenCode are open source (MIT and Apache-2.0), so they stay in the pool as backends.

## Where we start (what already exists)

| Piece | Today | Gap |
|---|---|---|
| Harness picker (`harness_router.py`) | Keyword match into 8 categories, then the best benchmark score, then the cheapest, then the fastest. No model call. | Coarse categories, no sub-tasks (debug vs build), no language awareness, never learns from outcomes. |
| Engine router (`router/`) | Regex rules, plus a 60-token model call when confidence is below 0.6. Builds engine chains (research → data → sheets → slides). | Separate from the harness picker. Two routers to merge into one. |
| Harnesses | Meshpool Code, Meshpool Work, Pi, Codex, OpenCode, Hermes (in-process or ACP). | The Meshpool ones are general-purpose. None is specialised. |
| Engines | code, data, sheets, slides, research, credit, browser, general, each with a deterministic finish gate | Good base for specialised harnesses. |
| Memory (`assistant.py`) | `memory.md` (12k chars, one fact per line), `instructions.md`, past-chat search | Flat file, no retrieval, no per-project memory, no outcome memory. |
| Benchmarks | SWE-bench Verified (20 frozen), Terminal-Bench 2 (4 dev tasks), MiniWoB++, office practical set | Most categories (research, data, docs, chat) have estimated scores, not measured ones. |

Current measured standing on SWE-bench Verified (20 frozen tasks, DeepSeek V4.1 Flash): Pi 19/20 at $0.0069, Meshpool Code 18/20 at $0.0079, Codex 17/20 at $0.0101. **Pi leads on code today.** That is the bar.

## Architecture

```
request ──► Router ──► Harness (+ sub-harness, language pack, model tier, budget) ──► Gates ──► answer
              │               │                                                      │
              │               └── shared: tools, skills, MCP, memory, extensions    │
              └────────── outcome log (trace, cost, pass/fail) ◄────────────────────┘
```

### 1. Router v2: three stages, cheapest first

| Stage | How | Cost | Decides when |
|---|---|---|---|
| 0. Signals | Deterministic: attached file types, repo present, error text or traceback pasted (regex for Python/JS/Rust/Go/Java traces), language from file extensions and lockfiles, explicit `@harness` or `/debug` | ~0 ms, $0 | A strong signal fires (e.g. traceback + repo → `code.debug.python`) |
| 1. Semantic | Local embedding model (ONNX, ~30 MB, e.g. via `aurelio-labs/semantic-router` with a fastembed encoder) against ~50 labelled examples per route | ~10–20 ms, $0 | Top route clears a similarity margin |
| 2. Small model | One JSON-only call on the cheapest model (the existing 60-token classifier, extended with sub-route, language and difficulty) | ~200–400 ms, about $0.00005 | Stages 0 and 1 are unsure |

The router outputs a **route**: `{harness, sub, language, difficulty, model_tier, budget}`. `difficulty` drives model choice inside the harness: easy goes to a cheap model, hard to a strong one. This is the RouteLLM idea, `lm-sys/RouteLLM`, Apache-2.0.

**Learning loop.** Every task writes `{route, harness, model, tokens, cost, seconds, gate_passed, user_undo/retry}` to a local SQLite outcome log. The empty `outcome` column in `routing.csv` finally gets filled. Each week, refit the routing table and the semantic examples from these outcomes. Retries and undos count as failures.

**Routing granularity.** Route per chat at the start, and allow a re-route mid-chat when the task changes, for example from "explain this" to "now fix it". The route chip in the chat shows the pick and lets the user override it.

### 2. Harness contract

Every harness is one spec, so a new harness is mostly config plus checks:

```yaml
id: code.debug
system_prompt: prompts/code_debug.md   # byte-stable, so the provider prompt cache hits
tools: [read_file, grep, edit, bash, run_tests, diagnostics, finish]
language_packs: auto                   # injected by the router's language signal
model_tiers: {easy: deepseek-v4.1-flash, hard: <strong model>}
budget: {turns: 40, usd: 0.05}
pre_steps: [reproduce]                 # deterministic work before the model's first turn
finish_gate: [repro_passes, related_tests_pass, no_test_edits]
output_style: diff                     # minimal-token output format
```

### 3. The harness catalog (7 families)

Sorted by how often people use AI for each task, and by what we can measure.

#### H1. Chat (fast path): new, and the biggest cost lever
Most messages are questions, explanations, rewrites and short answers. They need no agent loop.
- No tools by default. Cheapest model. Streams the first token in under 1 s. Memory recall is injected as a fenced block.
- Escalates by itself to a tool harness when the answer needs files, the web or actions (one tool `escalate(route)`).
- Measure: MT-Bench-style judged set from real (anonymised) app chats, plus time to first token.

#### H2. Code: the deepest family, with sub-harnesses × language packs

| Sub-harness | What makes it better and cheaper |
|---|---|
| **code.debug** | Reproduce first, as deterministic pre-steps: run the failing command, parse the traceback (`core/errctx.py` already parses Python, pytest, gcc/clang, rustc, tsc, Ruff, mypy, Pyright, Node), and inject only `file:line:col + message + 5 lines of context`. Python: newest interpreter (3.14+, managed with `uv python`). It gives precise syntax-error locations and "did you mean" hints, `python -X importtime`, and `pdb -p <pid>` remote attach (new in 3.14) for live processes. `git bisect run` for regressions. Flaky-test detection (re-run 3x). |
| **code.build** | Plan, then edit in small diffs. Repo map from tree-sitter tags (Aider's approach, Apache-2.0) instead of reading whole files. Structural edits with `ast-grep` (MIT). |
| **code.refactor/migrate** | `ast-grep` rewrite rules and codemods first, with the model only for leftovers. Big token savings on mechanical changes. |
| **code.review** | Read-only. Diff plus linters plus type checker; output review comments only. |
| **code.terminal/devops** | Container-native commands (already in the engine). Install, CI, Docker and env fixes. Codex currently leads Terminal-Bench, so start from its approach. |

**Language packs** (what you asked about "specific languages"): each pack is the toolchain, a compact language-specific prompt and an output-format rule.

| Pack | Fast checks fed back as structured diagnostics |
|---|---|
| Python | `python3.14 -m py_compile`, `ruff check --output-format json`, `ty` or `pyright --outputjson`, `pytest -x -q` |
| TypeScript/JS | `tsc --noEmit --pretty false`, `biome lint --reporter json`, `vitest`/`jest` |
| Rust | `cargo check --message-format=json`, `cargo clippy`, `cargo test` |
| Go | `go vet`, `go build`, `gopls check`, `go test` |
| Java/Kotlin, C/C++, SQL | later, same pattern (`javac`/`gradle`, `clang -fsyntax-only`, `sqlfluff`) |

**How output tokens drop:** (a) edits as search/replace or unified diff, never whole files; (b) diagnostics pre-parsed by code, not dumped raw; (c) one short pack prompt instead of a general prompt that covers every language; (d) the finish gate rejects "explain what I did" essays, and the summary is generated from the diff.

#### H3. Research
- Base: our `engines/research` (dossier, grid, verify) plus ideas from `alphaXiv/OpenResearch` (MIT), `Alibaba-NLP/DeepResearch` (Apache-2.0) and `langchain-ai/open_deep_research` (MIT).
- From **OpenResearch** specifically: (1) the **experiment record**, where every run is a row in a local SQLite store tied to a commit and its artifacts, so agents learn from past results; (2) the **playbook-plus-skills split**, with a small durable system prompt and task procedures as on-demand skills; (3) **literature sources** (alphaXiv, arXiv, bioRxiv, PubMed); (4) **delegation over MCP** (`delegate`, `wait`, `subscribe`) instead of each harness's own sub-agents; (5) its rule "unknown is not absent: report a failed read as a failure, never as empty".
- Sub-harnesses: `research.web` (sourced brief), `research.academic` (papers, experiments), `research.finance` (SEC and filings via `edgartools`, already in the stack).
- Gate: every claim has a source, and every quote is checked verbatim (already built).
- Measure: BrowseComp-Plus or GAIA subset, plus our sourced-brief set.

#### H4. Data analysis
- A sandboxed Jupyter kernel with **DuckDB** (MIT) and **Polars** (MIT). SQL over CSV, Parquet and Excel without loading everything into the prompt. The model sees the schema and samples, never the raw dump.
- Charts: Vega-Lite or matplotlib, rendered and checked.
- Gate: every number traced to executed output (already built in `engines/data`).
- Measure: DABstep or DA-Code subset, plus our practical set.

#### H5. Documents and office (Meshpool Work)
- Already strong: forge, slides, sheets, with 20 deck rules, 13 workbook rules and schema checks. Inputs through `docling` (MIT) and `markitdown` (MIT).
- Work: split into `docs.word`, `docs.sheets`, `docs.slides`, `docs.pdf` sub-harnesses with smaller prompts.

#### H6. Browser and computer use
- Already strong (MiniWoB++ 97/102, cheapest). Keep it. Add `microsoft/playwright-mcp` (Apache-2.0) accessibility snapshots as the default observation (fewer tokens than screenshots), with screenshots only on demand.

#### H7. Automation and ops (the "works on its own" harness)
- Scheduled tasks, connectors (58 already), file watchers and webhooks. It runs headless with no chat open, and reports into a chat or a notification.
- Local tools on the computer, without the harness in the loop: Shortcuts/AppleScript on macOS, PowerShell on Windows, CLI tools.

Vertical packs on top: finance/credit (already built) and GTM/sales (about 60 skills already shipped).

#### H8. Dreaming harnesses: work done while the user is away
Agents that run offline (idle time, overnight, or after a chat ends) and make the next live task faster, cheaper and
better. Each is proposal-first: it writes a diff or a report the user approves, with a receipt and one-step rollback,
and none of them talks to the user in the moment. Prior art: the Hermes plugin catalog's `dreaming`
(hsyvy/dreaming-memory: an offline pass checks quick notes against transcripts and rewrites a linked wiki plus a
short index), `hermes-dreaming` (nightly corroboration of facts across days, proposals with verbatim anchors) and
`oneiros` (proposal-only consolidation, morning review, rollback, no model calls); Letta's sleep-time agents.

| Dream | When | What it produces |
|---|---|---|
| **dream.memory** | Nightly, or at session start if a night was missed | Consolidates memory: checks saved facts against what the user actually said, merges duplicates, drops stale ones, resolves references ("same as last time"). Proposals only; the user approves in a morning review. |
| **dream.project** | When a project folder changes a lot, or nightly for active projects | Project memory: repo map (tree-sitter tags), the commands that build and test it, conventions, flaky tests. The code harnesses start every task warm instead of re-exploring. |
| **dream.skills** | Weekly | Mines successful transcripts for repeated procedures and drafts new skills or edits to existing ones (Hermes' self-improving skills idea). The user approves each skill. |
| **dream.router** | Nightly | Refits the routing table and the semantic examples from the outcome log (undo, retry and errors count as failures). It also proposes moving a category to another harness when the data says so. |
| **dream.replay** | Nightly, within a small budget | Replays failed or slow tasks from the outcome log, with consent, on other harnesses or models, offline. A replay that wins becomes routing evidence and a regression test for the loop. |
| **dream.anticipate** | Before the user's usual start time | Pre-computes likely next work: the morning brief, scheduled-task inputs, research the user asked to follow, and warm prompt caches for active chats. |
| **dream.agents** | Per agent (Pi, Codex, Hermes, Meshpool) | The same memory and project dreams for the outside agents, through the shared memory MCP server, so every agent in the pool benefits. |

Guards: a dream spends from its own small daily budget, which the user sets. It never edits user files directly, it
screens secrets out of anything it stores, and it records what it read. The loop measures each dream by whether the
next live tasks got faster, cheaper or more accurate, and a dream that does not pay for itself is switched off.

### 4. Memory layer (from Hermes, MIT)

Port Hermes Agent's memory design into a shared Meshpool service that every harness uses, including Pi and Codex through the existing `meshpool-assistant` MCP server:

- `MemoryProvider` interface with lifecycle `initialize → system_prompt_block / prefetch / sync_turn → tools → shutdown`, and a `MemoryManager` that fans out to **one built-in provider plus at most one external provider**. External options: mem0 (Apache-2.0), Graphiti (Apache-2.0), Cognee (Apache-2.0).
- **Fenced recall:** recalled memory goes into the prompt inside `<memory-context>` and is scrubbed from output with Hermes' streaming scrubber, so memory never leaks into replies or files.
- Background prefetch and sync threads, with timeouts so a slow provider never blocks a turn.
- Layers: (1) user facts and instructions (what exists today); (2) **project memory** per folder or repo (conventions, commands that work, where things are); (3) **outcome memory**, the OpenResearch-style experiment and run log, which the router also reads; (4) **session search** with SQLite FTS5 over past chats.
- Keep the current rules: no secrets, no tasks or to-dos stored as facts.
- Attribution: add Hermes' notice to `THIRD_PARTY_NOTICES.md` (a Hermes notice already exists in `packaging/`).

### 5. Chat experience (Work → chat)

To audit in the app first. Initial list:
- The route chip shows "Debug · Python · fast model" with a one-click override and the reason.
- Per-reply cost, tokens and time (small and optional).
- Edit and resend, regenerate, and **branch from any message**.
- Memory chips: "used 2 memories" with click to view or forget.
- Streaming tool progress inline, collapsed by default, with no wall of tool calls.
- Instant fast-path replies (H1) with no "starting agent" delay.
- Needed from you: the specific chat problems you see.

### 6. Surfaces and extensibility

| Surface | Plan |
|---|---|
| Desktop app, CLI, TUI | Exist. Route chip and memory UI added. |
| **ACP server** | Expose the *router itself* as an ACP agent (`acp_server.py` exists), so Zed, JetBrains and other editors get Meshpool Auto. |
| **MCP server** | Expose each harness as an MCP tool (`meshpool.debug`, `meshpool.research`…), so Claude Desktop, Cursor and Codex can call our specialised harnesses. |
| Background daemon | Runs H7 automations, schedules and watchers with no window open. |
| OS hooks | Global hotkey, macOS Share/Services menu and Shortcuts actions, Windows context menu ("Ask Meshpool about this file"). |

**Extensions.** Three levels, all optional and on demand:
1. **Skills** (SKILL.md): exist, loaded only when a task matches.
2. **MCP servers and plugins**: exist (Claude plugin format).
3. **TypeScript extensions**: a Node sidecar that loads Pi-style extensions (`registerTool`, event hooks such as `tool_call` and `before_agent_start`, commands). Pi's extension API is MIT and proven, and reusing its shape means Pi extensions work in Meshpool. Python harnesses call into the sidecar over JSON-RPC.

### 7. Observability and evaluation

- **Tracing:** OpenTelemetry with GenAI semantic conventions, stored locally in SQLite, with optional export to Langfuse (MIT core, self-host) or Arize Phoenix. Check Phoenix's licence before bundling; it is Elastic License 2.0 as far as we know, so export only. Builds on the existing `core/observe.py` and `testkit/trace_stats.py`.
- **Eval runner:** `UKGovernmentBEIS/inspect_ai` (MIT) as the common runner for all suites. `promptfoo` (MIT) for router regression tests (a labelled set of about 500 prompts; target routing accuracy ≥ 95%).
- **Suites per harness:** SWE-bench Verified (code), Terminal-Bench 2 (terminal), MiniWoB++ and WebArena-Lite (browser), GAIA or BrowseComp-Plus (research), DABstep (data), our office practical set (docs), a judged chat set (chat).
- **Protocol (keep the existing one):** same model, same tasks, same hours (DeepSeek peak and off-peak changes cost), dev tasks for tuning and frozen test tasks for claims, 95% confidence intervals.
- **Nightly CI run** on a small slice per harness. Regressions block release.

### 8. Licensing

Ship only MIT, Apache-2.0, BSD or similar inside the app. AGPL and Elastic-licensed tools are used only as optional external services. Every borrowed repo goes into `docs/stack/license-and-repo-ledger.csv` (the existing practice).

## Phases

| Phase | Scope | Exit criterion |
|---|---|---|
| **P0. Baseline** (1 wk) | inspect_ai runner; frozen sets for every category; OTel traces; outcome log; run Pi, Codex, OpenCode, Hermes and Meshpool together | Measured (not estimated) scores for every category in `routing.json` |
| **P1. Router v2 + harness contract** (1–2 wk) | Merge both routers; three-stage routing; route spec; port existing engines to the contract | ≥ 95% routing accuracy on the labelled set; overhead < 50 ms p50 |
| **P2. Code family** (2–3 wk) | code.debug + Python and TS packs first, then build, refactor and review; Rust and Go packs | Tie or beat Pi on SWE-bench Verified frozen, at ≤ 0.7x cost |
| **P3. Chat fast path + memory** (2 wk) | H1, Hermes memory port, chat UX items | Chat cost ≤ 0.3x the current agent path; time to first token < 1 s |
| **P4. Data, research, docs** (3 wk) | H3 with OpenResearch ideas, H4 with DuckDB, H5 split | Beat Hermes and Codex on each category set |
| **P5. Surfaces + extensions** (2 wk) | ACP and MCP exposure, daemon, TS extension sidecar | A Pi extension runs unchanged in Meshpool |
| **P6. Continuous** | Nightly evals, weekly router refit | No category regresses release to release |

## Built so far (slice 1)

- `app/meshpool_harness/hrouter/`: signals, specs (chat, code.debug, code.build, code.refactor, code.review,
  code.test, code.terminal, code.explain, research, data, docs, browser, automation), the route decision (rules first,
  one small-model call only when unsure) and an outcome log.
- Language packs plus a `diagnostics` tool. It uses the newest Python on the machine (3.14+), Ruff bug rules, tsc,
  node --check, cargo check, go build/vet, javac, cc, dotnet, bash -n and shellcheck, and checks JSON, TOML and YAML
  syntax. Output is `path:line:col message` with the failing lines.
- Chat: skills load on demand. The Work prompt went from about 9,500 to about 3,000 tokens per call. Answer-first
  style, ask_user only when a choice is really open, and an optional fast model (`MH_MODEL_FAST`) for chat-lane
  messages.
- App wiring: every new chat is routed. The route rides on Pi, Codex and other outside agents' first message, Auto
  uses the routed category, and the route survives a restart.
- The loop: `testkit/loop/bench.py` with chat, debug and build suites graded by code, and
  `.github/workflows/harness-loop.yml` running daily on cheap models with a budget cap. Runs are kept on the
  `loop-history` branch, and a regression fails the run.

## Decisions needed from you (original list)

1. **Target model(s).** Optimise for DeepSeek V4.1 Flash only (as the current benchmarks do), or also for a strong tier (GPT-6.1 Terra or Claude Sonnet 5.5) for hard tasks?
2. **Benchmark budget** per full run (all frontier harnesses × all suites).
3. **Language priority** for code packs. Proposed: Python and TS first, then Rust and Go.
4. **Chat problems** you see today in Work → chat.
5. **OpenResearch:** borrow its ideas only (the plan above), or also add it as a harness in the pool (it is a Rust app; it could run through ACP)?
6. **Which repo is the home** for this work: `hamzaaziada/meshpool-harness` (where the releases originate), mirrored to `hamzaziada4422`?
