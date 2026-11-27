# Tool health: every tool meshpool's agent can call

`packaging/tool_health.py` calls each tool below once, for real, with a small valid input in a temp project, and
checks the result. It prints one line per tool (ok, FAIL with the error, or skip with the reason) and exits 1 on any
FAIL. The release workflow runs it next to `packaging/smoke.py` on the Mac disk images, the Windows app tree and the
Linux download (in a bare Ubuntu 22.04, as root and as an ordinary user), so a broken tool stops the release before
anything is published.

```sh
<the app's python> packaging/tool_health.py --app <the app's code folder> [--version 0.0.19] [--skip NAME] [--keep]
# from source:
MH_ALLOW_SYSTEM_SOFFICE=1 python packaging/tool_health.py --app app
```

How it calls them:

- Core tools go through the agent's own dispatch (`Agent._run_tool` in `agent/core.py`), with the tool list opened to
  every core tool (which chat type offers which is listed below).
- MCP tools go through the app's MCP manager (`agent/mcp.py`), started the way a chat starts it
  (`ui/agent_api.py`): the default `mcp.json` plus the office server, each server started with the same Python.
  Computer use is turned on for the check (it is off by default).
- Offline: a stand-in model (`testkit/mock_llm.py`); a local web server for the web and browser tools, which also
  answers as a SearXNG search API (`SEARXNG_URL`, so `web_search` and `web_research` run their real code path);
  `MH_ALLOW_PRIVATE_FETCH=1` so the fetch tools may read it; and the HTTP proxy pointed at a closed port, so an
  accidental internet call fails at once instead of leaving the machine.
- Completeness: after the checks it lists every tool the agent can be offered (the core schemas and every tool of
  every connected server). A tool without a check is a FAIL, so a new tool cannot ship without one.

Inventory: 90 tools the agent can be offered (32 core, 58 from built-in MCP servers), plus 7 bundled programs the
skills rely on. Remote servers and connectors need the internet or an account and are reported as skipped.

## Core tools (`core/tools.py`, `agent/core.py`)

Offered by chat type: a Code chat (Codex 0.171 style) gets `exec_command`; a Work chat (lean core) gets `shell`,
`apply_patch`, `update_plan`, `web_research`, `fetch_url`, `office_task` (once a message asks for a deck, report or
model) and `ask_user` (Office panel); the classic lane (`MH_LEAN_WORK=0`, plan mode, sub-agents) gets the rest of
`BUILTIN`. `diagnostics` comes with the code route, `find_skill` when skills load on demand, `probe` and `trace` with
`MH_OBSERVE=1`. `run_tests`, `todo`, `load_skill`, `finish` and `need_context` are the one-shot engines' tools
(`engines/code`).

| Tool | Server | Needs | How it is checked |
|---|---|---|---|
| exec_command | core | bash | `echo exec-$((6*7))`; result says `exec-42` and exit code 0 |
| shell | core | bash | `["bash","-lc","echo shell-$((6*7))"]`; result says `shell-42` |
| bash | core | bash | `echo bash-$((6*7))`; `bash-42` and `[exit 0]` |
| bash_many | core | bash | two echo commands; both outputs come back |
| bash_start | core | bash | starts `echo started-bg; sleep 30`; an id comes back |
| bash_output | core | bash | reads that id; it is running |
| bash_stop | core | bash | stops that id |
| probe | core | Python | runs a one-line script; its output comes back |
| trace | core | Python | runs a small script file; its output comes back |
| run_tests | core | Python | runs a one-line check; its output comes back |
| write_file | core | | writes `made.txt`; the file holds the text |
| read_file | core | | reads `notes.txt`; the text comes back |
| edit | core | | replaces a word in `made.txt`; the file changed |
| apply_patch | core | | an Update File patch; the file changed |
| grep | core | | finds a marker in `notes.txt` |
| glob | core | | `*.txt` lists both files |
| notebook_edit | core | nbformat | replaces cell 0 of a notebook; the cell changed |
| update_plan | core | | one step; "Plan updated" |
| todo_write | core | | one item; "1 of 1 done" |
| todo | core (engines) | | one item |
| ask_user | core | | a question with a recommended option; answered headless with it |
| use_skill | core | the bundled skills | loads `build-code`; its instructions come back |
| find_skill | core | | a search for a spreadsheet chart skill |
| load_skill | core (engines) | | runs (an engine workspace may have no skills) |
| diagnostics | core (code route) | ruff (bundled) | checks a small `.py` file |
| fetch_url | core | network (local page here) | reads a local page; its token comes back |
| web_search | core | network (local SearXNG here) | three results; titles come back |
| web_research | core | network (local SearXNG here) | searches and reads two local pages; the token comes back |
| delegate_task | core | the model (stand-in here) | one sub-agent task; its report comes back |
| office_task | core | the model, the office engines | the data engine on a CSV: it starts, asks the stand-in model and ends without a crash |
| finish, need_context | core (engines) | | skipped: signals to the one-shot engine loop, not calls that do work |

## meshpool-office (`agent/office_mcp_server.py`, `engines/forge/tools.py`)

| Tool | Needs | How it is checked |
|---|---|---|
| make_document | python-docx | Markdown to `health.docx` in the project |
| make_pdf | reportlab | Markdown to `health.pdf` |
| (the agent's own `python3`) | the bundled Python first on PATH, openpyxl, python-pptx | `exec_command` runs a script that writes `health.xlsx` and `health.pptx`, as a Work chat makes small files |
| edit_office_file | python-docx, openpyxl, python-pptx | edits the docx, xlsx (a formula) and pptx in place; reopens each to check |
| read_document | the readers in `docread.py` | reads the docx, xlsx, pptx and pdf back |
| convert_document | the bundled LibreOffice | pptx to pdf and to png, pdf to docx (the way to edit a PDF: there is no in-place PDF edit), docx to pdf |
| find_icon | the icon sets in `engines/icons` | a search for "chart" |
| check_deck, check_workbook, check_document | LibreOffice (recalculation) | run on the files made above; no crash |
| measure_office_file, slide_layouts | | run on the deck; no crash |
| make_deck, add_slides, make_spreadsheet, analyze_data | the model, the office engines, LibreOffice | each starts its engine, which asks the stand-in model and ends without a crash; a real build needs the meshpool model |
| office_job_status | | asked about the job of a long engine run, or about an unknown id |
| fill_template, merge_decks | | named in `agent/core.py` (approval card wording) but no server offers them in 0.0.18: nothing to check |

## meshpool-web (`agent/web_mcp_server.py`, `web.py`)

| Tool | Needs | How it is checked |
|---|---|---|
| web_fetch | network (local page here) | reads a local page |
| web_search | network (local SearXNG here) | three results |
| web_research | network (local SearXNG here) | reads two local pages, quotes the token |
| (web_search on the live internet) | network | skipped: needs network |

## meshpool-assistant (`agent/assistant_mcp_server.py`, `assistant.py`)

| Tool | Needs | How it is checked |
|---|---|---|
| memory_save, memory_view, memory_forget | the data folder | saves a fact, sees it, forgets it, sees it gone |
| project_note | the data folder | saves a note for the project |
| schedule_task, list_scheduled_tasks, delete_scheduled_task | the data folder | schedules a daily task, lists it, deletes it |
| search_chats | the saved chats | finds a seeded past chat by two words |
| run_subtasks | the model (stand-in here) | one part; its report comes back |

## meshpool-tools (`agent/demo_mcp_server.py`)

| Tool | How it is checked |
|---|---|
| current_time | at UTC+2 |
| calculate | `(1200*0.15)/12 = 15` |
| project_stats | counts the temp project |

## Resources (`agent/mcp.py`, offered only when a connected server has resources)

| Tool | How it is checked |
|---|---|
| mcp__meshpool_resources__list_resources | against a stand-in stdio server with one resource |
| mcp__meshpool_resources__read_resource | reads that resource's text |

## browser (`agent/browser_mcp_server.py`, `browser_use.py`)

Needs the bundled Chromium (`app/vendor/browsers`) and Playwright. Checked against a local page with a form.

| Tool | How it is checked |
|---|---|
| browser_open | opens the local page; its title comes back |
| browser_snapshot | the token and numbered elements |
| browser_type, browser_select, browser_click | types a name, picks Blue, clicks Go; the page says `clicked Ada Blue` |
| browser_press, browser_scroll, browser_wait | Tab, one screen down, one second |
| browser_screenshot | a `.png` is saved |
| browser_tabs | lists the tab |
| browser_back | follows a link, goes back to page 1 |
| browser_close | closes the browser |
| view_output | pictures of `health.pdf` |
| ui_audit | a local HTML file at the wide view |
| ui_smoke | the same file; no crash |

## computer-use (`agent/computer_mcp_server.py`, macOS and Windows)

screenshot, click, move_mouse, drag, scroll, type_text, press_key, open_app, screen_info, computer_check. On Linux each
must answer that computer use is not supported on Linux, in plain words; then it is reported as skipped (not on this
platform). On macOS and Windows `computer_check` and `screen_info` must run without crashing; the tools that move the
mouse or read the screen need a signed-in desktop and the user's permission and are skipped.

## Programs the skills rely on (`bundled.py`, `app/vendor`)

| Program | Skill | How it is checked |
|---|---|---|
| ffmpeg | convert-files, transcribe-audio | makes a one-second tone (skipped in a source checkout: no vendor folder) |
| pandoc | convert-files | Markdown to docx (skipped in a source checkout) |
| speech to text (faster-whisper, `vendor/whisper`) | transcribe-audio | transcribes a second of silence offline (skipped in a source checkout) |
| notebooks (nbconvert, ipykernel) | notebooks | runs a one-cell notebook with the bundled kernel |
| charts (matplotlib) | make-chart | draws a bar chart to png |
| HEIC (pillow-heif) | image-tools | writes and reads a `.heic` |
| OCR (rapidocr, optional) | read_file, read_document on pictures and scans | where installed, reads the deck picture's text; the apps ship without it, so it must say so plainly (no "failed") and is reported as skipped |

## Not checked offline

| What | Why |
|---|---|
| context7 (remote MCP server, off by default) | needs network |
| connectors (GitHub, Jira, Linear, ...) | need network and the user's account |
