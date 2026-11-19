# Open-source research: what each harness is built from

Checked on 2026-11-18 against each repository's own licence file, not the GitHub label. Meshpool ships inside a
desktop app, so the rules are:

- **Adopt**: code we ship. MIT, Apache-2.0, BSD or Unlicense only, and noted in `THIRD_PARTY_NOTICES.md`.
- **Borrow**: we take the design and write our own code. Any licence is fine.
- **Optional**: a separate process the user installs, such as an MCP server or CLI. GPL and AGPL are fine here
  because they are never linked into our code.
- **Avoid**: not used.

Stars and last push are from the day of the check.

## Code harnesses (debug, build, refactor, review, test, terminal)

| Tool | Licence | Stars | Decision | Why |
|---|---|---|---|---|
| ruff | MIT | 51k | **Adopt (done)** | The bug rules (F, E9) check every edit in about 20 ms. Ships in the requirements. |
| Newest CPython (3.14+) | PSF | n/a | **Adopt (done)** | Exact syntax-error locations and "did you mean" hints; `pdb -p` attaches to live processes (3.14). |
| SolidLSP (inside oraios/serena, `src/solidlsp`) | **MIT** (component) | 31k | **Adopt next** | One client for many language servers: definition, references and rename at symbol level. Agents report cross-file edits taking 8–12 steps without it. |
| Serena (the application) | **GPL-3.0** | 31k | **Optional** | Offer it as an MCP server the user can add in one click. Never bundled. |
| microsoft/multilspy | MIT | 0.6k | Fallback for SolidLSP | Same idea, smaller project. |
| ast-grep | MIT | 17k | **Adopt next** | Structural search and rewrite for the refactor harness: one rule instead of many hand edits. |
| tree-sitter + py-tree-sitter, grep-ast | MIT, Apache-2.0 | 28k, 1.5k | **Adopt next** | A repo map (Aider's approach) for the build harness: signatures instead of whole files. |
| ripgrep | Unlicense | 70k | Adopt | Already used for search where present. |
| sourcegraph/zoekt | Apache-2.0 | 2k | Later | Trigram index for very large repos. |
| LibCST, jscodeshift, OpenRewrite | MIT/PSF, MIT, Apache-2.0 | 2k, 10k, 4k | Optional per language | Codemods for the Python, JS and Java refactor packs. |
| SWE-ReX, microsandbox, E2B | MIT, Apache-2.0, Apache-2.0 | 0.7k, 9k, 15k | Later | Sandboxed execution: run untrusted commands outside the user's machine. |

## Router

| Tool | Licence | Decision | Why |
|---|---|---|---|
| aurelio-labs/semantic-router + qdrant/fastembed | MIT, Apache-2.0 | **Adopt next** (stage 1) | Local embedding match, about 10 ms and no API call, for requests the rules are unsure about. |
| lm-sys/RouteLLM | Apache-2.0 | Borrow | Choosing a strong or weak model by difficulty, trained on preference data. Our outcome log is the training data. |
| HM-Router (hmarkc) | none stated | Borrow the idea only | Route over (model, harness) pairs, not the model alone. Our loop already measures pairs. No licence, so no code. |
| adaptive-harness | MIT | Borrow | Weights update after every scored task; two harnesses race on hard, high-blast-radius tasks and a synthesiser merges the result. |
| Protocol-Lattice/harness-router | MIT | Not now | Picks the next tool inside a loop with a decision model: an extra call per step, which works against our cost goal. |

## Harness pool and protocol

| Tool | Licence | Decision | Why |
|---|---|---|---|
| **HarnessRouter CE** | Apache-2.0 | **Borrow heavily, adopt parts** | See below. |
| Pi, Codex, OpenCode, Hermes | MIT, Apache-2.0, MIT, MIT | In the pool (done) | Frontier baselines and backends. |
| goose, Qwen Code, Gemini CLI, Aider, OpenHands | Apache-2.0 / MIT | Add through ACP or HarnessRouter-style drivers | More backends Auto can choose when the loop shows they win. |

From HarnessRouter (Apache-2.0):
1. **Measured per-model capabilities** (`runner/reasoning.py`). A field a model lacks is refused, not ignored.
   *Done in our own way:* a refused field is removed, remembered per model and retried. Their measured
   reasoning-level table is next.
2. **A closed error vocabulary and a retry rule per error.** *Done for quotas:* a spent budget is never retried.
   *Next:* the same envelope on our ACP and MCP surfaces.
3. **Idempotency keys**, so a retried task never runs twice. *Next*, for the MCP and ACP task tools.
4. **Session branching**: restore the files of any earlier response and continue from there. *Next*, for "branch
   from here" in chat (our checkpoints already record every turn).
5. **Memory consolidation as reviewable runs**: nothing is overwritten, every record is stamped with who wrote it,
   and each run's changes can be listed. *Next*, as the design for dream.memory.
6. **Long exact values go in as files, not prompt text**, and secrets are redacted from everything that leaves the
   sandbox. *Next.*
7. **Keep-alive streaming** with an inactivity timeout instead of a total timeout. Our gateway health probe
   already covers most of this.

## Memory and dreaming

| Tool | Licence | Decision | Why |
|---|---|---|---|
| Hermes memory layer (`agent/memory_*.py`) | MIT | **Adopt (port)** | Provider interface, fenced recall, a scrubber that keeps memory out of output, background prefetch. |
| tigerless-labs/agent-memory | MIT | Borrow | Plain Markdown as the source of truth plus a local ranked index. This matches our files-first design. |
| hsyvy/dreaming-memory | MIT | Borrow | An offline pass checks notes against transcripts and rewrites a linked wiki and index. |
| mem0, graphiti, cognee, letta | Apache-2.0 | Optional external provider | One at a time, as Hermes allows. |

## Research, web and data

| Tool | Licence | Decision | Why |
|---|---|---|---|
| OpenResearch (alphaXiv) | MIT | Borrow + pool | Experiment records, a playbook plus skills, paper sources. |
| gpt-researcher, open_deep_research, Tongyi DeepResearch | Apache-2.0, MIT, Apache-2.0 | Borrow | Plan, parallel search, then synthesise with citations. |
| trafilatura | Apache-2.0 | Adopt | Main-text extraction for fetch_url. |
| crawl4ai | Apache-2.0 | Optional | Heavier crawling. |
| Firecrawl | **AGPL-3.0** | Optional only | Never bundled. |
| docling, markitdown | MIT | Adopt (docling partly in use) | Document input. |
| DuckDB, Polars | MIT | **Adopt next** | The data harness queries files with SQL instead of pasting rows into the prompt. |

## Measurement and observability

| Tool | Licence | Decision | Why |
|---|---|---|---|
| SWE-bench harness + Verified | MIT | **Adopted** | Real issues graded by their own tests (`testkit/swe_ab.py`, now with app-agent and Pi solvers). |
| Terminal-Bench | Apache-2.0 | Next | The terminal harness's benchmark. Codex led it in our earlier run. |
| inspect_ai + inspect_evals | MIT | Later | One runner for the academic suites. |
| OpenTelemetry GenAI conventions, openllmetry | Apache-2.0 | Adopt next | Standard traces. |
| Langfuse | MIT core, separate `ee/` | Optional export target | Self-hosted. |
| Arize Phoenix | **Elastic License 2.0** | Export only | Not bundled. |
