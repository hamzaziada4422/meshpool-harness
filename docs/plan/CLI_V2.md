# CLI v2: the terminal interface

The `meshpool` command now draws one scrolling column in the style of the terminal brief: header, notices,
conversation, then a live area at the bottom (spinner while working, the input box when idle).

## Files

| File | Role |
| --- | --- |
| `app/meshpool_harness/agent/tui.py` | `Terminal` (events, live area, input editor, approvals), `Session` (turns and slash commands), `main` |
| `app/meshpool_harness/agent/tui_render.py` | Pure rendering: `Style`, wrapping, markdown, shell block, tool line, header, counters |
| `app/meshpool_harness/agent/tui_input.py` | Keys: termios (macOS, Linux), msvcrt (Windows), `parse_keys`, `FakeKeys` for tests |
| `testkit/vt.py` | Tiny terminal emulator (text and HTML out) used by tests and screenshots |
| `testkit/cli_v2_shots.py` | Scripted scenes and the PNG renderer (Playwright) |
| `app/tests/test_tui_v2.py` | Tests |

Standard library only; prompt_toolkit and rich are not dependencies.

## Screen

* Header: MeshPool mark (three rows of half blocks, sampled from `brand/mark.png`), `MeshPool` bold + version dim,
  `model · provider`, folder. Notices below with `/commands` in light blue and `N more notices hidden`.
* Right-aligned status line above the box: `Not logged in · Run /login` (no key), `✔ Update installed · Restart to
  update` (from `autoupdate._ST`, read locally; nothing calls GitHub).
* Input box between two full-width rules (grey at start, MeshPool blue once the conversation began), `❯` prompt in
  MeshPool blue, placeholder `Try "..."` while empty.
* Footer: access mode (Shift+Tab: ask, accept edits, full access, mapped to the agent's `ask`, `edits`, `auto`; ACP
  agents only have ask and auto) and the count of running sub-agents. Then `~/folder (branch)` and the counters line
  `↑fresh ↓out Rcached CHhit% [$cost] ctx%/window (mode)` with `model • effort` right-aligned. ctx% is yellow above
  80%. Narrow terminals drop R and CH, then the model.
* Shell calls (`bash`, `bash_many`) are full-width green blocks: bold `$ command`, dim `(timeout 60s)`, dim output,
  `Took 2.6s`. Output beyond 10 lines (`MH_TUI_OUTPUT_LINES`) is cut from the top with
  `… (N earlier lines, ctrl+o to expand)`.
* Other tools are one line: `● Read path 84 lines Took 0.1s`, `● Edit path +6 -1`, `● Search pattern 2 matches`.
* Assistant markdown: bold, `code` in magenta, hyphen lists with a 2-column hanging indent, run-in bold labels,
  `->` to `→`, fenced code indented, tables as rows. Text is rendered line by line as it streams; the unfinished
  line is shown raw in the live area.
* Spinner line with elapsed seconds: `✻ Running pytest… (7s · ctrl+c to interrupt)`.
* Permission prompt in ask mode: inline `1. Yes / 2. Yes, and don't ask again for commands in this session / 3. No`,
  arrows, 1-3 or y/a/n; Esc is No. ask_user questions use the same chooser.

## Keys

Enter sends, `\` + Enter or Alt+Enter new line, bracketed paste, Up/Down history, Left/Right/Home/End, Ctrl+A/E/K/U/W,
Tab completes a command, Shift+Tab cycles access, Ctrl+O prints the last shell output in full, Ctrl+L clears, Esc
clears the line, Ctrl+C clears or (twice) quits, Ctrl+D quits. Typing `/` shows a filtered list (Up/Down, Tab, Enter).

## Commands

`/help /model [id] /login /clear /compact /status /auto [mode] /mcp /connect /tokens /init /import /setup /exit`.

* `/model` lists the models the endpoint reports (`provider_setup.test`) plus ChatGPT models when signed in, and sets
  `agent.model`; ACP agents use `set_model`.
* `/login` offers MeshPool key, another OpenAI-compatible provider, or a ChatGPT sign-in; for keys it reads the key
  hidden (`getpass`), runs `provider_setup.test` (wire detection, model list, latency), lets you pick the default
  model and calls `provider_setup.save`.
* `/compact` shortens old tool output with core's `_shrink_context` (to 40% of the context size). Not for ACP agents.

## Degrading

* NO_COLOR: same layout (rules, box, spinner), no colour and no block fill (blocks are indented and set off by
  blank lines).
* Not a TTY: plain appended lines, no live area, raw streamed text, input via `input()`.
* `-p`: reply on stdout, everything else on stderr, non-zero exit on error (unchanged).
* Widths under 40 columns drop the mark; every live line is clipped to the width.

## Changes outside the TUI

* `agent/core.py`: `tool_start` events for `bash`/`bash_many` now carry `timeout_s` (one line), used for `(timeout 60s)`.

## Open items

* No price list exists in MeshPool: `$cost` shows only when `MH_PRICE_IN`, `MH_PRICE_OUT` (and optionally
  `MH_PRICE_CACHED`), USD per million tokens, are set. Cache-write tokens (`W`) are not reported by the agent.
* Context window comes from `MH_CONTEXT_WINDOW`, the endpoint's reported size (after `/model` or `/login`), a size in
  the model name, else 128k.
* Ctrl+O reprints the last block (the scrollback is append-only), and works at the prompt, not during a turn.
  Core caps tool output at 6000 characters, so "expanded" is at most that.
* During a turn only Ctrl+C is read (the terminal stays in normal mode); Esc and typing ahead are not.
* The "N agents" count is not a list; there is no key to open it.
* "No" in the raw permission prompt does not ask for a note (the `input()` fallback still does).
* Effort shows only from `MH_EFFORT` / `MH_REASONING_EFFORT`.
* Windows keys (`msvcrt`) are written but were not run here.
* The mark is a sampled, low-resolution version of the ribbon M; a hand-drawn glyph would be crisper.
* Screenshots: `python testkit/cli_v2_shots.py docs/screenshots` (made-up folder, scripted events).
