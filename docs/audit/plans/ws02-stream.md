# ws02-stream plan: streaming and response parsing

Starting point: 0.0.12 (`c189e04`). Every item below was re-checked against 0.0.12 with a script that feeds the
reported input to the current code; all of them still reproduce. Line numbers are 0.0.12.

## Root cause shared by most items

There are four hand-written readers of model replies, each with its own subset of the guards:

| Reader | Used by | Guards it has | Guards it lacks |
|---|---|---|---|
| `core/wire.py:94 sse_to_chat` + `_first_then_idle` | engines (`SimpleLedger`, `LiveClient`), keep-warm pings | none | keep-alive cut-off, cut-stream check, JSON-reply detection |
| `agent/core.py:2261 tee` around `sse_to_chat` | app agent | keep-alive cut-off, weak cut check | full cut check, JSON-reply detection when streaming |
| `core/chatgpt.py:312 read_stream` + `_first_then_idle` | ChatGPT sign-in | error events | keep-alive cut-off |
| `agent/core.py:323 _probe_once` `r.read()` | endpoint probe | socket timeout only | wall-clock deadline |

All of them parse SSE line by line (`data:` lines are JSON-decoded one at a time), so the SSE framing rules
(multi-line `data:`, `event:`, comments) are wrong everywhere, and every per-chunk `ev.get(...)` assumes a dict.

**Fix for the class:** one module, `core/stream.py`, owns SSE framing, the two time guards (first byte / data-idle
with keep-alives not counting), the JSON-instead-of-SSE detection, the cut-stream check, chunk assembly for chat
completions, `<think>` splitting, request-body encoding and Retry-After parsing. `sse_to_chat`, `SimpleLedger`, the
agent's `_post`, `_probe_once`, `chatgpt.read_stream` and the Anthropic wire all go through it.

## Items

| ID | Reproduces on 0.0.12 | Root cause | Fix | Regression test (fails on 0.0.12) |
|---|---|---|---|---|
| W-05 | yes | Streaming requested, server answers `application/json`: agent `tee` finds no `data:` line, `sse_to_chat` returns an empty reply, `_post` raises "stream ended", attempt 1 + probe + attempt 2 then non-stream on attempt 3 (`agent/core.py:2018`). Engines (`wire.py:192`) return the empty reply as success. | `stream.read_reply` checks Content-Type and the first non-blank byte; a JSON body is read and returned as the reply on the first call. | `test_stream_wire.py::test_json_reply_to_a_stream_request_is_used_in_one_call` (engine) and `test_agent_stream.py::test_server_ignoring_stream_costs_one_call` (agent, counts requests on a mock server) |
| W-07 | yes | `sse_to_chat` keys tool calls by `index` only (`wire.py:119`); two calls both at index 0 with different ids are concatenated (`"readgrep"`, `{..}{..}`). | Assembler keeps a slot per (index, id): a new id at an index already holding another id opens a new call; id-less deltas go to the latest slot for that index. | `test_stream_wire.py::test_parallel_calls_sharing_index_0_stay_separate` |
| W-08 | yes | `slot["function"]["arguments"] += dict` → TypeError (`wire.py:130`); non-streamed replies with object arguments reach `_sanitize_calls`, which replaces them with `{}`. | Assembler and `normalize_reply` serialize object/list arguments with `json.dumps`; non-string names coerced. | `test_stream_wire.py::test_object_arguments_are_serialized` (streamed and non-streamed) |
| W-09 | yes | Engine reader `_first_then_idle` only sets a socket timeout; a server that sends `: keep-alive` every few seconds never trips it. `_probe_once` `r.read()` has no deadline. | `stream.iter_sse` enforces a data-idle clock (comments, blank lines and Anthropic `ping` events do not reset it); `stream.read_body` enforces a total deadline; used by engines, chatgpt, probe and agent. | `test_stream_wire.py::test_engine_call_on_keepalive_only_stream_ends`, `test_agent_stream.py::test_probe_on_keepalive_only_body_ends`; the existing agent control test `test_a_stream_of_only_keep_alives_ends` stays green |
| W-10 | yes | (a) `rate_limit_wait` only matches `[retry-after: <digits>]` (`client.py:99`), HTTP-date ignored. (b) agent waits `min(left, ...)` even when the stated reset is past the remaining budget (`agent/core.py:1922`). (c) `SimpleLedger` never reads the header; `LiveClient` without `MH_429_BACKOFF` waits a fixed 65 s. | `stream.retry_after_seconds(headers)` reads `retry-after-ms`, delta-seconds and HTTP-date; both transports put `[retry-after: N]` in the error. `client.stated_wait(msg)`; agent ends the turn at once (resumable message) when the stated wait exceeds what is left of the budget; `LiveClient` waits the stated time, with its own budget and the same early end. | `test_rate_limit_headers.py::test_http_date_retry_after`, `::test_reset_past_budget_ends_without_waiting`, `::test_engine_honours_retry_after` |
| W-11 | yes | `anthropic_wire.to_request` never sets `cache_control`. | Breakpoints on the last tool, the system prompt (sent as a block list) and the last block of the last two user turns: at most 4. `MH_ANTHROPIC_CACHE=0` turns it off. | `test_anthropic_wire.py::test_prompt_caching_breakpoints` |
| W-12 | yes | `wire.py:176` and `agent/core.py:2219` force `stream=False` for the Anthropic wire. | Anthropic SSE assembler in `anthropic_wire.py` (message_start / content_block_* / message_delta / message_stop / ping / error), fed by `stream.iter_sse`; both transports stream it. | `test_anthropic_wire.py::test_streamed_messages_reply_is_assembled`, `test_agent_stream.py::test_anthropic_wire_is_streamed` (mock server asserts `"stream": true` and checks text deltas reach the UI) |
| W-13 | yes | `to_request` maps every unknown `tool_choice` to `{"type":"auto"}` (`anthropic_wire.py:97`). | `"none"` → `{"type":"none"}`. | `test_anthropic_wire.py::test_tool_choice_none_is_kept` |
| W-19 / S-09 | yes | `sse_to_chat` never records whether the stream finished; the agent only rejects a cut stream with no content (`agent/core.py:2287`), engines accept anything. | Assembler records `[DONE]` and `finish_reason`; `result()` raises `StreamCut` ("HTTP 0: the stream ended before the reply was complete") when neither came. HTTP 0 is transient, so it is retried; after the retries the CLI exits non-zero. | `test_stream_wire.py::test_cut_stream_is_an_error`, `test_agent_stream.py::test_cut_stream_with_text_is_retried`, `test_agent_stream.py::test_cli_exits_non_zero_on_cut_streams` |
| W-20 | yes | `ev.get` on `data: "x"`, `data: 42`, `data: [..]` → AttributeError. | Non-object chunks are skipped; an `{"error": ...}` chunk raises a classified `RuntimeError` (HTTP code from the error when present). | `test_stream_wire.py::test_non_object_data_lines_are_skipped`, `::test_error_chunk_raises` |
| W-21 | yes | Each `data:` line is JSON-decoded on its own; a JSON object split across `data:` lines of one event is dropped. | Spec framing: `data:` lines of one event are joined with `\n` before decoding; if the joined text is not JSON, each line is tried alone (servers that omit the blank line). | `test_stream_wire.py::test_multiline_data_field` |
| W-22 | yes | Agent `keep` (`agent/core.py:1189`) and engine `keep` (`loop.py:217`) copy only `content`/`tool_calls`. DeepSeek-style thinking with tools needs `reasoning_content` sent back inside the turn. | Keep `reasoning_content` in history; `stream.prepare_messages` sends it only on assistant messages after the last user message and strips it elsewhere; a 400 naming the field strips it and remembers the model (agent `_REFUSED`, `LiveClient` one-shot). | `test_agent_stream.py::test_reasoning_content_is_kept_and_sent_back_in_the_turn`, `test_stream_wire.py::test_prepare_messages_scopes_reasoning` |
| S-10 / H-03 | yes | A model on a local/compatible server writes its call as text (`<tool_call>{..}</tool_call>`, `<function=x>..`, `[TOOL_CALLS]`, `<\|python_tag\|>`, or bare JSON); with no `tool_calls` the agent ends the turn and prints the raw text. | `core/textcalls.py: recover(msg, tool_names)` converts these to `tool_calls` only when the name is a tool offered in this request; prose around a tagged call is kept as content. Applied in the agent turn (with a `stream_reset`) and the engine loop; skipped when tools are off. | `test_textcalls.py` (one test per format, plus "JSON answer with an unknown name stays text"); `test_agent_stream.py::test_text_tool_call_runs_the_tool` |
| H-17 | partly | `<think>...</think>` in `content` (llama.cpp, Ollama, vLLM without a reasoning parser) is shown as the answer and kept in history; streaming shows no progress while the model reasons in `reasoning_content`; a reply that spends `max_tokens` on thinking ends with "(no reply)". | `stream.ThinkSplitter` moves `<think>` text (and text before an orphan `</think>`) to `reasoning_content`, streaming and non-streaming; the agent emits one "Thinking…" status per call when reasoning arrives before text; a `length` finish with reasoning but no answer gets one continuation asking for the answer. | `test_stream_wire.py::test_think_tags_are_split`, `test_agent_stream.py::test_thinking_shows_progress_and_is_not_the_answer`, `::test_length_cut_during_thinking_continues` |
| H-26 | residual risk | `json.dumps(body)` escapes a lone surrogate as `\ud800`; strict servers reject the body (400) and the cleanup retry (`clean`) only covers string message content. | `stream.encode_body` replaces lone surrogates anywhere in the body (keys, nested content parts, tool arguments) with U+FFFD and encodes UTF-8; used by every transport and the probe. | `test_stream_wire.py::test_lone_surrogates_never_reach_the_wire` (mock server parses the body as strict UTF-8 JSON) |

## Execution split (Sonnet subagents)

1. **A** `core/stream.py` (new), `core/wire.py`, `core/chatgpt.py`, `tests/test_stream_wire.py`: W-05/W-07/W-08/W-09/W-19/W-20/W-21/H-17 parsing/H-26, engine side of W-10 headers and W-22 `prepare_messages`.
2. **B** `core/anthropic_wire.py`, `tests/test_anthropic_wire.py`: W-11, W-12 assembler, W-13.
3. **C** `core/textcalls.py` (new), `core/client.py`, `tests/test_textcalls.py`, `tests/test_rate_limit_headers.py` (engine half): H-03, W-10 parsing and `LiveClient`.
4. **D** after A–C: `agent/core.py` (`_post`, `_probe_once`, `_chat` rate limits, `_turn`), `core/loop.py`, `tests/test_agent_stream.py`: wire everything into the app agent and engine loop, S-09 CLI exit.

A, B, C touch disjoint files and run in parallel in one tree; nothing is committed by subagents. Each new test is
checked to fail with the fix reverted before sign-off.
