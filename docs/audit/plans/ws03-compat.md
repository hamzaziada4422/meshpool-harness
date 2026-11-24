# ws03-compat plan: endpoint compatibility, context windows, compaction

Scope: W-02, W-03, W-04, S-11, H-02, H-05, H-06, H-09, H-19, H-21, G-14, P-02, P-06.
Starting point: branch `claude/sleepy-faraday-tkjcox` (0.0.12). Line numbers below are 0.0.12.

## Root cause shared by most items

Meshpool builds one request shape for every endpoint and learns almost nothing from a refusal:

- `agent/core.py:1752` `_body()` always sends `max_tokens` from the profile (8192 for every unknown model), `tools`,
  `tool_choice`, `prompt_cache_key` and `user`, the history as stored (consecutive user messages, ids as the
  provider minted them) and MCP schemas exactly as the server published them.
- The only adaptation is `_adapt_params` (`agent/core.py:668`), which covers a fixed list of 11 field names, only
  on HTTP 400/422 text matching a narrow regex, and remembers per process only, keyed by bare model name.
- `core/client.py:47` `classify_error` puts every 4xx other than 400/401/403/404/413/429 into `fatal`, which the
  agent loop retries with backoff (`agent/core.py:2020-2050`); TLS failures read as `HTTP 0` (transient).
- Nothing knows the model's context window. Compaction triggers at a fixed 160 000 characters
  (`hrouter/profiles.py:34`), the context-error path shrinks once and then gives up (`agent/core.py:1887`).

Fix of the class: one place that (1) learns an endpoint+model's limits from `/models` metadata, local-server
APIs and error text, (2) remembers them on disk per endpoint+model, and (3) shapes every request from what it
knows, so the next call is right the first time and a request that cannot succeed unchanged is never resent.

New modules (owned by this workstream, no overlap with other sessions):

- `app/meshpool_harness/core/endpoint_caps.py`: the store and the readers.
  - `get(base, model)`, `learn(base, model, **caps)`, `forget(...)`; JSON at `config.home()/endpoint_caps.json`,
    written with `fsio.update_json` (cross-process lock, atomic), corrupt file quarantined.
  - Caps: `context_window`, `max_output`, `refused` (fields), `max_tokens_param`, `no_tools`, `strict_roles`,
    `tool_id_style`, `schema_subset`, `chars_per_token`, `first_byte_s`, `window_source`.
  - `diagnose(msg, body) -> fix | None`: one parser for refusal texts (Ollama, vLLM, llama.cpp, LM Studio,
    OpenAI, Azure, Gemini OpenAI-compat, Mistral, Anthropic, pydantic 422 `extra_forbidden`). Actions:
    `drop_field`, `max_tokens_param`, `max_out`, `window`, `no_tools`, `strict_roles`, `tool_ids`,
    `schema_subset`, `tls`.
  - `discover(base, key, model)`: once per endpoint per day: `GET {base}/models` (context_length,
    max_model_len, max_context_length, loaded_context_length, top_provider.*, max_completion_tokens,
    supported_parameters), and for local servers Ollama `/api/ps` + `/api/show`, llama.cpp `/props`,
    LM Studio `/api/v0/models`. Short timeouts, failures ignored, `MH_DISCOVER=0` turns it off.
  - `is_local(base)`, timeouts for local servers, token estimate with a learned chars-per-token ratio,
    `observe_usage()` that also detects a prompt the server cut (H-02).
- `app/meshpool_harness/core/compat.py`: pure, deterministic request shaping on a copy (history is untouched,
  so the cacheable prefix stays byte-stable).
  - `normalize_roles` (H-05), `fix_tool_ids` (H-19), `sanitize_schemas` (H-21), `cap_images` (P-06),
    `strip_tools` + `text_tools_prompt` + `parse_text_tool_calls` (W-02), `prepare(body, caps, base)`.

## Re-check on the new base (370427e, audit branch merged)

The base now has `core/context.py` (window from override / learned / Ollama `/api/show` / LM Studio / `/models`,
`fit()`, `max_output()`, `compact_chars()`, silent-truncation learning) and `core/modelcaps.py` (ModelCaps). Result:

- G-14, P-02: compaction already sized to the window (`_maybe_compact` uses `CTX.compact_chars`). Status
  "already fixed"; add tests for an 8K and a 32K window over 40 exchanges.
- H-09: context errors now retried up to 4 times with `fit(tighten)`; verify with a test, fix only gaps.
- H-02: truncation is learned but the cut reply is still used; vLLM `max_model_len` is not read from `/models`.
- W-02, W-04 (422 is still `fatal`; `prompt_cache_key` cannot be dropped on refusal), S-11, H-05, H-06, H-19,
  H-21, P-06: still reproduce. W-03: output caps on large windows still refused every call; an output-size error
  still compacts the history.

Changed design: no separate capability store. Learned facts go into `ModelCaps` (new fields, persisted per
endpoint+model with `fsio`); the refusal parser is a new module `core/refusals.py`; `core/compat.py` stays as
planned. `agent/core.py` edits were announced to the session that owns cost work there.

## Items

### W-02 models or servers without tool calling fail every turn
Reproduces in 0.0.12: `_body` always sends `tools`; Ollama "does not support tools" and vLLM "auto tool choice
requires --enable-auto-tool-choice" are HTTP 400 `bad_request`; `_adapt_params` cannot drop `tools`, so the clean
retry repeats and the turn fails.
Fix: `diagnose` -> `no_tools`, learned per endpoint+model. `compat.prepare` then sends no `tools`/`tool_choice`,
renders past tool calls/results as text, and adds a text tool protocol to the system message; the agent parses
`<tool_call>{...}</tool_call>` blocks from the reply into ordinary tool calls, so the agent keeps working on that
model. `/models` `supported_parameters` without `tools` sets it up front.
Test: mock Ollama and mock vLLM servers; first turn: one refused call then a working one; second turn: zero
refused calls; a text tool call in the reply runs the tool.

### W-03 max_tokens is always 8192
Reproduces: `_body` uses `limits.max_output_tokens` (8192); vLLM's "maximum context length is 4096 tokens ...
(8192 > 4096 - 120)" matches the context regex, so Meshpool compacts the history (useless) and fails.
Fix: `diagnose` reads output caps ("at most N completion tokens", "max_tokens must be <= N", Anthropic
"> N, which is the maximum allowed") and context errors that name input and requested output; when the input
fits, it is `max_out` (lower max_tokens to window - input - margin), not compaction. Every request clamps
max_tokens to `max_output` and to `context_window - estimated prompt`.
Test: mock vLLM with a 4096 window and a mock with a 4096 output cap: second request is accepted, history is
not compacted, the next chat's first request is already right.

### W-04 strict endpoints break every chat
Reproduces: `prompt_cache_key`/`user` are not in `_ADAPTABLE`; HTTP 422 is `fatal` and is resent 6 times with
1+2+4+8+16 s backoff.
Fix: `classify_error`: any 4xx that is not auth/model/quota/rate/context is `bad_request` (422 included);
`diagnose` drops any refused top-level field (Gemini "Unknown name", Azure "Unrecognized request argument",
pydantic `extra_forbidden` loc), learned on disk; a `bad_request` with no fix raises at once.
Test: mock Gemini-style 400 and pydantic 422 servers; exactly 2 requests, no sleep; next chat 1 request.

### S-11 self-signed TLS: -p re-checks for 30 min
Reproduces: certificate errors become `HTTP 0: connection problem`, the probe fails the same way, so the
endpoint is "down" and `_await_endpoint` waits `MH_RECOVER_MIN` (30 min).
Fix: `classify_error` -> `tls`; the agent raises a clear message at once (what failed, how to trust the
certificate); the probe reports `tls` and `wait_until_up` stops; LiveClient raises too.
Test: a real local HTTPS server with a self-signed certificate; the turn ends in seconds with "certificate" in
the error and at most one probe.

### H-02 local servers silently cut the prompt (Ollama num_ctx)
Reproduces: Ollama's OpenAI endpoint truncates to num_ctx without an error; nothing checks usage.
Fix: `discover` reads Ollama `/api/ps` (loaded context_length) and `/api/show` (num_ctx); after every reply
`observe_usage` compares prompt_tokens with the characters sent; a cut prompt learns the real window, the reply
is discarded, the conversation is fitted to the window and resent, and the user is told how to raise
num_ctx (`OLLAMA_CONTEXT_LENGTH`). If system prompt + tools alone do not fit, a clear error says so.
Test: mock Ollama that truncates and reports small prompt_tokens: the reply is not used, the resend fits,
status names OLLAMA_CONTEXT_LENGTH.

### H-05 strict chat templates
Reproduces: the agent appends `[Meshpool]` notes as user messages after user/tool messages (core.py 1176-1292),
so two user messages in a row reach the server; DeepSeek reasoner and Mistral/Gemma templates refuse it.
Fix: `compat.normalize_roles` on every request: non-leading system -> user note, consecutive same-role
messages merged, empty assistant messages dropped; `strict_roles` (learned from "roles must alternate") also
folds the leading system message into the first user message and makes the first turn a user turn.
Test: unit tests on the transform + mock template server that 400s on two users in a row.

### H-06 timeouts sized for cloud APIs
Reproduces: first byte 120 s, probe 30 s for every endpoint; a CPU model prefill takes longer, so every
retry times out the same way and the probe marks it down.
Fix: local endpoints get first-byte 900 s, probe 120 s; the observed first-byte latency is remembered per
endpoint+model and the timeout is at least 3x it.
Test: unit tests for the timeouts + a slow local mock that answers after the cloud timeout.

### H-09 context overflow that compaction cannot fix
Reproduces: on a context error the agent shrinks once (`shrunk`), then raises; the tail and the newest user
message stay as they were, so every later message fails the same way.
Fix: a ladder that repeats while it shrinks the request, fitted to the learned window: summary, hard shrink,
long user text cut, images dropped, then the tail's outputs cut to fit; it stops with a clear error only when
system prompt + tools alone exceed the window. The fitted history is saved, so the next message works.
Test: a mock with a small window that refuses until it fits; a huge pasted message still gets an answer, and
the next message too.

### H-19 tool-call ids
Reproduces: `_sanitize_calls` gives `call_0` to every id-less call (duplicates across turns); ids from one
provider (long, with dots, `toolu_...`) go unchanged to Mistral (9 alnum) or OpenAI (<= 40 chars).
Fix: `compat.fix_tool_ids` on every request: invalid or duplicate ids remapped deterministically (results
follow their call), orphan results become user text, missing results get a stub; `tool_id_style=alnum9`
learned from Mistral's error remaps all ids.
Test: unit + a mock Mistral server that enforces 9-alnum unique ids after a provider switch.

### H-21 MCP schemas passed through unchanged
Reproduces: `tool_schemas()` returns MCP `inputSchema` as published (`$ref`, `$defs`, `$schema`,
`additionalProperties`, type lists, `const`, `format: uri`).
Fix: `compat.sanitize_schemas`: always inline local `$ref`, drop `$schema/$id/$comment`, make the top level an
object with `properties`, prune `required` to existing properties; `schema_subset` (Gemini host or learned from
an error) keeps only the OpenAPI subset (type, nullable, description, enum, properties, required, items,
anyOf, min/max, format date-time/enum).
Test: unit on a nasty schema + mock Gemini server that rejects unknown schema keys.

### G-14 / P-02 compaction sized to the real context window
Reproduces: compaction starts at 160 000 chars regardless of the model; system prompt + tools are not counted;
summarizer asks for 2 800 output tokens over a 60 000-char log on the global LiveClient endpoint.
Fix: budget = (context_window - reserved output - system/tools tokens) in chars (learned ratio); compaction
starts at the smaller of that and the profile limit; `compaction.compact_history` takes `budget_chars` and
the agent's own endpoint (`_aux_complete`) for the summary, sizes the log and the tail to the budget.
Window comes from caps (discover/error) or profile `limits.context_window`.
Test: 8K and 32K windows simulated: 40 exchanges with no context error.

### P-06 prompt caching with more than 4 images
Reproduces: `_body` drops all but the newest 4 images on every request, so the cut moves by one image per call
and the prefix changes at the oldest kept image.
Fix: `compat.cap_images` moves the cut in steps (kept between keep and keep+step-1), deterministic, so most calls
share the whole earlier prefix.
Test: 20 calls each adding a screenshot: share of the prompt identical to the previous call's prefix goes from
about a quarter to at least half.

## Execution
Phase 1 (parallel, worktrees): A `endpoint_caps.py` + `classify_error`; B `compat.py`. Phase 2: C wires both
into `agent/core.py`, `core/client.py` (LiveClient), `core/gateway.py` (probe), `agent/compaction.py`.
Phase 3: review, revert-proof every test, full suite.
