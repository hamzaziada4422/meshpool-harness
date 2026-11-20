# Forge knowledge base: blockers

## 1. No model calls (2026-11-20)

- The Meshpool test key answers every call with `budget_exceeded` (max budget 0.0, spent 23.10).
- The DeepSeek key the session proxy injects for `api.deepseek.com` is rejected as invalid.

What it blocks:

- Live `engines.forge.build` runs: storyline, content, repair rounds, tokens and dollars per deck, build time with a
  model.
- The reviewer's polish score (`review.py`, `critics.py`), so no A/B can show a polish gain.
- Section 4D (reference-guided review) cannot be measured: it ships behind a flag, off, recorded as not run.

What was done instead:

- A renderer-only A/B: 12 briefs with hand-written slide JSON, frozen before any engine change
  (`app/tests/fixtures/forge_kb_briefs/`), rendered and validated before and after each change
  (`testkit/forge_kb_eval.py`). Same content, so any difference comes from the engine.
- Vision labelling of the library and the reference pages done by Claude subagents in this session from contact
  sheets, not by the gateway models.

To unblock: raise the test key's budget (or provide a working key), then run
`python -m engines.forge.build <brief.md> <out> --slides 10` for the 12 briefs on the baseline commit and on the final
branch, and re-run the 4D A/B.
