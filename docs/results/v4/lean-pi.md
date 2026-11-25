# Meshpool on Pi: request size against vanilla Pi

No model was called. `testkit/lean_pi.py` starts Pi 1.5.2 (the version the app ships) against a capture endpoint that
logs every request body, once as vanilla Pi (`pi -p`) and once as Meshpool drives it (`PiAgent.send` with the same hidden
context block the app builds in `ui/agent_api.py`). Tokens are chars/3.5 over the JSON of `messages` + `tools` (the
`testkit/mock_llm.py` estimate), so the numbers compare arms, not providers. Run:
`python testkit/lean_pi.py --task code|office --out DIR`.

## First request, before and after

| Task | Vanilla Pi | Meshpool on Pi before | after | extra before | extra after |
|---|---|---|---|---|---|
| Plain coding ("Create hello.py ... run it") | 1557 | 3256 | 1995 | +1699 | +438 |
| Office edit (inventory.xlsx, add formula column) | 1592 | 4061 | 2774 | +2470 | +1182 |

The mid-task request (after one bash call) carries the same extra: the system prompt and tool list are identical on every
request (no clock, no ids), and nothing Meshpool adds is repeated per turn, so the prefix caches.

## Where the extra tokens came from (coding chat, before, about 1700)

| Part | Tokens | Source |
|---|---|---|
| Skills block (Pi's `<skills>` wrapper + 3 skills) and the "other skills" pointer | about 540 | `pi_skill_dirs` loaded the 3 best word-overlap hits however weak (a coding message matched `sales-create-an-asset` on the word "create") |
| Hidden first-message block: clock + UTC offset, HONESTY + PLAIN (about 310), "Notes from Meshpool" incl. the whole ENGINES office text (about 430, sent to every chat, office or not) | about 690 | `assistant.build_context` / `_notes` |
| `<mcp_servers>` block: 5 servers, long one-line notes | about 290 | `pi_rpc.SERVER_NOTE` |
| `tool_search` schema (Pi adds it once any server is deferred) | about 190 | Pi |
| Memory / project / instructions blocks | 0 here | already omitted when empty |

Tool schemas of Meshpool's servers (office, assistant, browser, web, tools) were already deferred (ws20); they cost nothing
until `tool_search` loads them. In an office edit chat the remaining extra was the six directly declared office tools
(about 800 tokens) on top of the above.

## What was cut

- Skills: loaded only on a real match (`skills.search(min_score=4, min_terms=2)`, at most 2). No match: no `<skills>` block
  and no index pointer. `$name` still loads a skill. About -540 for a coding chat.
- First message for Pi (`tools="deferred"`): date only (no clock, so the text is stable), a 3-sentence version of
  HONESTY + PLAIN (`LEAN_RULES`), no "Notes" paragraph (what each server is for moved into its one-line entry; the
  tool_search sentence duplicated Pi's own `<mcp_servers>` text), incognito line only when incognito, the office rules only
  when the chat asked for an office file and no capability note carries them (`agent_api`: Pi no longer gets ENGINES on every
  chat). Memory, project and instruction blocks are still omitted when empty. Other harnesses keep their text.
- `SERVER_NOTE` shortened; `meshpool-tools` (time, arithmetic, stats: bash does these) is not declared to Pi
  (`MH_PI_MCP=direct` brings it back).
- Office chats: declare `read_document`, `edit_office_file` and only the `check_*` tool of the file types the message names
  (`office_direct`); `convert_document` and the rest are one `tool_search` away.

Capability kept: every other Meshpool tool is still reachable. Tests run `tool_search` then `memory_save` (memory written)
and `tool_search` then `convert_document` (the office server answers) against real Pi.

## Not measured

The 2-4x call counts on invoices/budget/chart tasks need a real model. This change does not touch tool behaviour; it
removes the unrelated skills and the office boilerplate those chats carried on every request. Re-run bench3 (`mp-on-pi`
vs `pi`) to confirm. Tests: `app/tests/test_lean_pi.py` (first request within +500 tokens of vanilla for coding, +1500 for
office; deferred memory and office paths; they skip where Pi is not installed under `app/vendor/pi`).
