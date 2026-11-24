Codex CLI 0.171.0 request on the gateway, taken from dev6 (v2-perf-anagrams, codex rep 0, last request 006).
Codex is Apache-2.0 (openai/codex); these files are its prompts and tool schemas as it sent them.

- 01-developer.txt, 02-developer.txt, 03-user.txt, 04-user.txt: the first four input items, verbatim text.
- tools.json: the full tools array.
- top-level.json: every other top-level field.
- function_calls.json: the first three function_call items.
- outputs.json: function_call_output samples. `success` is exit 0, `failure` is exit 1, and `long` holds the two
  longest/truncated outputs found across the dev6 Codex runs.

Reasoning: the meter does not record output_tokens_details. Across every dev6 Codex request body (3736 calls) there
are 0 input items of type `reasoning`. Codex replays its reasoning items (with encrypted_content), so the gateway
returned none, and thinking is not on for deepseek-v4.1-flash even with reasoning={"summary":"auto"}.
