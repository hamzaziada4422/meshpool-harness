# ws04-usage plan: usage, cost and budgets (incl. nested calls)

Starting point: 0.0.12 (branch claude/sleepy-faraday-tkjcox). Every item below was re-checked against 0.0.12 code.

## Root causes (0.0.12)

| ID | Reproduces in 0.0.12? | Root cause |
|---|---|---|
| S-02 | yes | `agent/core.py` counts only its own calls (`_chat`, `_aux_complete` add to `self.usage`). `office_task` runs the engine in a subprocess (`agent/office_jobs.py:run`) whose result JSON carries per-step loop usage that nobody reads; engine-internal helper calls (forge review, clarity, research grid, credit, image calls) are not in that number either. The Usage page (`ui/workbench.py:usage`) sums `session.json` usage, so the same gap reaches the UI. |
| W-14 | yes | `core/loop.py:209-216`: fixed DeepSeek prices × `MH_BILLING_FACTOR` for every model; a reply with no `usage` adds 0. `receipts.py` uses one user price for every model. |
| M04-02 | yes | The only budget check is in `core/loop.py` (engine loops). `Agent._chat` (chats), `ui/agent_api.py` goal continuations / auto-resume, scheduled runs (`scheduler_os.run_due` -> `agent_api.run_scheduled`) and parallel helper fan-out never check `MH_BUDGET_USD`. |
| M35-02 | yes | `core/loop.py:213-216` checks after the reply arrives and returns before the reply's tool calls run, so a reply that crosses the budget while calling `finish` is thrown away and the task reports failure. |
| F-22 | yes | `ui/workbench.py:594`: `m = d.get("model") or d.get("harness")`, one row per chat keyed by the chat's model setting, falling back to the agent id. Calls on other models in the same chat (engines, reviewers, fast model) are lumped under the chat's model, and harness chats show agent names. |
| M28-02 | yes | Only `Agent._chat` (chat wire) takes `GW.health(...).acquire`. `core/wire.py:SimpleLedger.chat` (LiveClient: engines, compaction, memory, summaries, classifiers, reviewers in engines), `Agent._aux_complete` (calls `self._post` directly) and the responses/chatgpt paths skip the gate and never look at endpoint health; health is in-memory per process, so engine subprocesses cannot see that the app marked the endpoint down. |
| M28-07 | yes | `agent/core.py` `_chat`: `slot = h.acquire(); try: self._wait(self._post...) finally: h.release(slot)`. `_wait` raises `Interrupted` on Stop while the worker thread keeps running `_post`; the slot is released at once. When Stop lands before `_urlopen` created the connection, `_abort_live` has nothing to close and the request is still sent: one more request in flight than the gate allows. |
| X-10 | yes (class) | No provider-reported number reaches the chat for nested engine calls, so any nested figure is an estimate. Fix: every call records the provider's own `usage` (input, cached, cache write, output, reasoning) at the transport; an estimate is used only when the provider sent none, and is flagged. |
| K-05 | yes | Auto's runtime cost data (`hrouter/outcomes.log_turn`, `hrouter/harness_select.log_outcome`, `api._run_agent`) is the chat agent's own `usage` delta, so nested engine calls are missing; `testkit/loop/bench.py` without the metering proxy prices every model with one DeepSeek price. |
| G-09 | yes | Umbrella of S-02 + W-14. |
| P-12 | yes | `core/gateway.py:_max_inflight` returns 4 for every endpoint unless the user sets `MH_MAX_INFLIGHT`; 10 chats -> 6 wait. Waiting is shown only for the chat agent; helper/engine waits are silent. |

## Design: one usage ledger (`core/usage.py`, new)

Every model call in the process tree records into one ledger at the transport layer, from the provider's own `usage` block.

- `normalize(raw)` -> `{in, cached, cache_write, out, reasoning, reported}` from chat completions (OpenAI / DeepSeek `prompt_cache_hit_tokens` / `completion_tokens_details.reasoning_tokens`), Responses (`input_tokens`, `input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`) and Anthropic (`input_tokens + cache_read + cache_creation`). `in` always includes cached and cache-write tokens. No usage -> `reported: False`, tokens estimated from request/reply chars (/3, deliberately high) and flagged `est: True`; never zero.
- Prices (`price_for(model, base)`), first match wins: user per-model override (`MH_PRICES` JSON in settings; the old `MH_PRICE_IN/OUT/CACHED` still apply as a global override), the endpoint's `/models` metadata when it carries prices (OpenRouter-style per-token strings or per-1M fields; cached per endpoint for a day, short timeout, never blocks a call for long), the maintained table `app/defaults/prices.json`, else `source: "unknown"`. Unknown price: displayed cost is `None` ("price unknown", counted in `unpriced_calls`), and for budgets the call is charged at the most expensive table price, so it is never free.
- Records go to `MESHPOOL_HOME/usage/calls-YYYY-MM.jsonl` (global; Usage page) and to the task file of the current task and every ancestor task (`usage/tasks/<task>.jsonl`). Appends are one JSON line under `fsio.file_lock`. Record fields: `id, ts, chat, task, source, model, host, in, cached, cache_write, out, reasoning, reported, est, cost, budget_cost, price_src, pid`.
- Tasks: `start_task(chat, budget_usd, parent)`; the budget defaults to the `MH_BUDGET_USD` setting. A contextvar holds the current task and source; `bind(task, source)`. Child processes get `MH_USAGE_TASK` / `MH_USAGE_SOURCE` (`child_env`); MCP servers that only know `MH_CHAT_ID` resolve the chat's current task from `usage/current/<chat>.json`. Resolution order: contextvar, `MH_USAGE_TASK`, pointer for `MH_CHAT_ID`, none (global ledger only, source "background").
- Budget: `reserve(model, request_chars, max_tokens)` before every call raises `BudgetExceeded` when the task's (or any ancestor's) recorded `budget_cost` plus in-flight reservations is already at or over the budget. The call that crosses is allowed and its reply is used (M35-02); the next one is refused. Reservations bound parallel fan-out (96 helper calls at once cannot all pass).
- `Task.poll()` returns records not seen yet (incremental read by offset), `Task.totals()` -> `{in, cached, cache_write, out, reasoning, calls, cost, unpriced_calls, unreported_calls, by_model, by_source}`.
- Query helpers for readers: `totals(since, until)`, `by_model`, `by_chat`, `by_day`, `by_source`.

## Work packages (Sonnet executes; Opus reviews each)

WP1 and WP2 run in parallel (disjoint files, separate worktrees). WP3 and WP4 start after both are merged and run in parallel (disjoint files).

### WP1: ledger foundation (S-02, W-14, X-10, G-09 base)
Files: `core/usage.py` (new), `app/defaults/prices.json` (new), `tests/test_usage_ledger.py` (new).
Tests: normalize for the three wire shapes and missing usage; price lookup order incl. `/models` metadata from a local HTTP server, unknown -> `None` cost and conservative budget cost; record writes both files with locking from 8 threads and 3 processes (no lost lines); ancestor roll-up; reserve refuses at/over budget and bounds 96 parallel reservations; poll is incremental; child process with `MH_USAGE_TASK` writes into the parent's task.

### WP2: one gate for every model call (M28-02, M28-07, P-12)
Files: `core/gateway.py`, `core/wire.py` (gate/health around the HTTP send only), `agent/core.py` (`_chat` gate block, `_wait`, `_post`, `_aux_complete`), `tests/test_gateway_ws04.py` (new).
- Provider-sized gate: explicit `MH_MAX_INFLIGHT` stays a fixed cap; otherwise the cap comes from the endpoint (Meshpool gateway 16, known cloud APIs 32, localhost 2, other 8) and adapts: halve on 429 / concurrency errors, +1 after 10 successes up to the cap. Waiting emits a status and a `gate` event (`waiting` then `acquired` with seconds waited); `gateway.snapshot()` reports inflight/limit/waiting per endpoint for the Usage page.
- Cross-process health: marking an endpoint+model down also writes a small marker in the slots folder; `is_down()` honours a fresh marker from another process; marking up removes it.
- `gateway.call(base, key, model, send, probe=..., check_stop=None, emit=None)`: health wait, acquire, send, release (ok/fail), used by `SimpleLedger.chat` per attempt and by the agent.
- Agent: the slot is released by the worker thread when `_post` really ends, not when `_wait` gives up; `_post` takes an abort token checked before opening the connection, in `checked_connect` and in the stream loop, so a call abandoned before its connection exists never goes out. `_aux_complete` goes through the same gated path.
Tests: 10 concurrent calls on the Meshpool base wait for nobody; 429 halves the limit; `MH_MAX_INFLIGHT=2` caps a mock server at 2 concurrent from `SimpleLedger.chat` and from `_aux_complete`; an endpoint marked down by another process makes `SimpleLedger.chat` send nothing; Stop before connect: the server never sees the request and the slot stays taken until the worker ends.

### WP3: plumbing and budgets everywhere (S-02, M04-02, M35-02, W-14, X-10)
Files: `core/wire.py`, `core/client.py` (LiveClient + MockClient record), `core/loop.py`, `agent/core.py`, `agent/office_jobs.py`, `agent/office_mcp_server.py`, `agent/delegate.py`, `ui/agent_api.py`, `harnesses/pi_rpc.py`, `harnesses/acp.py`, `core/chatgpt_relay.py`, `engines/forge/design.py`, tests.
- Record at every transport: `SimpleLedger.chat` (all wires), `LiveClient.chat` when its ledger is the benchmark one, `MockClient.chat`, `Agent._chat` (chat, responses, chatgpt), `_aux_complete`, chatgpt relay, forge image calls, pi `message_end` usage, ACP turns as unreported calls.
- `reserve` before every call at the same places; `BudgetExceeded` is not a `RuntimeError`, so retry loops never swallow it.
- Agent: per-send task (or the one the session hands it); `_wait` copies the context into its thread; after every call and while an office job runs, `task.poll()` folds new records into `self.usage` and the turn and emits `usage` events (`aux` for anything but the chat's own calls), so session usage includes engines, helpers, reviewers and sub-agents. `self.usage` gains `cost`, `unpriced_calls`, `unreported_calls`. `BudgetExceeded` ends the turn with a clear message and sets `budget_stopped`.
- Office jobs get `child_env` (job task under the chat task); `delegate.py` stops adding sub-agent usage by hand (sub-agents record under a child task, so the parent sees them once).
- `loop.py`: remove the fixed-price check; `reserve` before each call; a crossing reply is processed (finish accepted); no current task -> the loop starts one with `MH_BUDGET_USD`.
- `agent_api`: one task per user message, shared by goal continuations and auto-resume; scheduled runs get one task per run; a budget stop pauses the goal and auto-resume.
Tests: office job with a mock engine subprocess making N calls -> session usage includes them (provider numbers, not estimates); chat over budget stops; goal loop stops on budget; scheduled run stops on budget; 96 parallel helper calls against $0.10 stop near the limit; `loop.py` crossing reply that finishes is ok; gpt-class price trips earlier than DeepSeek price; reply without usage is charged.

### WP4: readers (S-02 display, F-22, K-05, W-14 display)
Files: `ui/workbench.py` (`usage()`), `ui/settings_v2.py` (`usage_by_day`, prices), `ui/app.js` (Usage page), `receipts.py`, `yourwork.py`, `hrouter/outcomes.py`, `hrouter/harness_select.py`, `api.py`, `cli.py`, `testkit/loop/bench.py`, tests.
- Usage page from the ledger: totals with cost, By model (real model of each call), by source (chat / engines / reviewers / helpers / sub-agents / external), per-chat rows including nested calls, unpriced and unreported counts, running/waiting model calls from `gateway.snapshot()`.
- Receipts and outcome rows carry the turn's ledger cost (per model) and `price unknown` instead of one global price; Your work shows measured cost.
- `api.run_task`, `meshpool -p --json`, `harness_select.log_outcome` report nested tokens and cost; `bench.py` without the meter takes tokens and cost from the run's ledger.
Tests: Usage page lists models not agents; a chat with nested engine calls shows them; receipt cost uses per-model prices; outcome row includes nested tokens; CLI `--json` includes cost.

## Review checklist (Opus)
Line-by-line diff review against the item text; every new test run once with the fix reverted (must fail) and once restored; concurrency (threads + processes), Stop paths, Windows (`msvcrt` locks, no fork), macOS paths, restart (ledger survives, tasks resumable), full suite with `-n 4` compared with the starting commit.
