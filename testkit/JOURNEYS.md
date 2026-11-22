# Consumer journeys and how each is tested

Every journey a user can take in the Meshpool app, and the test that walks it. Run the suites against a local app
and the mock model (no paid calls). The set-up is at the end of this page.

| # | Journey | Harnesses | Test |
|---|---|---|---|
| 1 | First launch on a fresh install: onboarding, key and endpoint, first screen | all | `ui_crawl.js` (fresh port) |
| 2 | Every settings page: models, keys, about, harnesses, connectors, skills, MCP, permissions, usage | all | `ui_crawl.js`, `about.js`, `e2e_full.py` |
| 3 | Test the connection, list models, set a favourite and the default model | all | `e2e_full.py` |
| 4 | Add a project, rename it, browse its files | all | `e2e_full.py` |
| 5 | Preview files: image, Word, Excel, PowerPoint, text PDF, scanned PDF, CSV, Power BI | all | `e2e_full.py` |
| 6 | Edit and save a file; attach a file to a message | all | `e2e_full.py` |
| 7 | Git status panel, terminal panel (run and stop), CI panel | all | `e2e_full.py` |
| 8 | Pick a harness; see what each is best at; add and remove a custom harness; set the default for a mode | all | `e2e_full.py`, `modes.js` |
| 9 | Auto picks a harness for code, documents, data, research and lead-gen tasks | Auto | `e2e_full.py` |
| 10 | Start a chat, approve a tool call, get a reply | Code, Work, Pi, Hermes | `e2e_full.py` |
| 11 | Add to a running task; stop it (button, Esc) within 5 s | Code, Work, Pi, Hermes | `e2e_full.py`, `midrun.js` |
| 12 | Interrupt with a new message: the new message is answered, the old task is not continued | Code, Work, Pi, Hermes | `e2e_full.py`, `intr.js` |
| 13 | Approvals: allow, always allow, deny with a note | all | `midrun.js` |
| 14 | Switch model, plan mode, switch mode or harness mid-run, reload the page mid-run, two runs at once, double send | all | `midrun.js`, `e2e_full.py` |
| 15 | Rename, pin, search, archive and delete threads; undo (checkpoints) | all | `e2e_full.py`, `midrun.js` |
| 16 | Read a PDF and a Power BI file through the agent | Code, Pi | `e2e_full.py` |
| 17 | Write a script and run it | Code, Pi, Hermes | `journeys.py` |
| 18 | Use an imported skill in chat (`$claude-skill`) | Code, Work, Pi, Hermes | `journeys.py` |
| 19 | Create, toggle and remove a skill; import skills and MCP servers from Claude Code, Codex, Cursor, Claude Desktop | n/a | `e2e_full.py` |
| 20 | MCP: toggle servers, built-in servers start, an MCP tool is called in chat | Code, Pi, Hermes | `e2e_full.py`, `journeys.py` |
| 21 | Connectors: catalog, custom connector with a token, disable, remove, OAuth sign-in starts | n/a | `e2e_full.py` |
| 22 | Instructions, memory, scheduled tasks (add, pause, run now, reply, delete, bad schedule refused) | n/a | `e2e_full.py` |
| 23 | Web search answers or explains what is missing | n/a | `e2e_full.py` |
| 24 | Make a Word document with Meshpool Work | Work | `journeys.py` |
| 25 | Browser use: open a page, see its elements, fill a read-only date field | Code | `journeys.py` |
| 26 | A thread naming an agent an older version had (`claude-code`) opens in Meshpool Code | n/a | `e2e_full.py` |
| 27 | Command line: `meshpool check`, `meshpool run` | CLI | `journeys.py` |
| 28 | Code tools when turned on (`MH_OBSERVE`): probe | Code | `journeys.py` (second app) |
| 29 | Click every control in every state (home, finished thread, mid-run, waiting for approval), two levels deep | all | `crawl_all.js` |
| 34 | Memory saved in one agent reaches every agent; every agent can schedule a task | all | `memory_check.py` |
| 33 | Stop stops within 5 s for every harness, during a slow model reply, a long command or a slow tool; nothing from the stopped request appears later | Code, Work, Pi, Hermes | `stop_check.py` |
| 30 | Nothing jumps or flickers: layout stays still while a task runs and settles in one step on every click; scroll, menus and typing survive live updates | all | `jitter.js` |
| 31 | Update from inside the app: the update bar appears, Update now installs, Restart comes back on the new version | n/a | `update_flow.sh` |
| 32 | Real tasks on the real model (paid, about $1): code fixes, a web page, Word, Excel, PowerPoint, PDF, editing a contract, invoices to Excel, letters | Code, Work, Pi, Hermes, Auto | `practical/practical.py` |
| 35 | Upgrade from 0.0.9: old memory becomes facts, the old files are backed up, nothing is lost | all | `journeys_010.js` (upgrade app), `app/tests/test_memory_v3.py` |
| 36 | Memory page: browse, search, edit inline, pin, change scope, archive with Undo, restore from history | n/a | `journeys_010.js`, `shots_memory.js`, `app/tests/test_memory_api.py` |
| 37 | A noticed fact waits in "Meshpool thinks"; Yes makes it active, No archives it and it is not suggested again | all | `journeys_010.js`, `test_memory_v3.py` |
| 38 | A reply shows how many memories it was given, with Edit and Wrong on each | Code, Work | `shots_chat.js`, `journeys_010.js` |
| 39 | "What the agent will see" matches the context the agent received; a fact can be left out of one chat | all | `journeys_010.js`, `shots_chat.js` |
| 40 | Incognito chat reads and writes no memory, learns nothing, logs nothing for suggestions or summaries | all | `journeys_010.js`, `test_memory_v3.py` |
| 41 | First run: starters from the user's own file names, or sample files in an empty folder | n/a | `journeys_010.js`, `app/tests/test_firstrun_away.py`, `shots_home.js` |
| 42 | Accept a schedule suggestion: the form opens prefilled, "Only tell me when something changed" is on | n/a | `journeys_010.js`, `app/tests/test_insights.py` |
| 43 | Dismiss and mute a suggestion; a dismissed one never returns; at most two on the home screen, one per type | n/a | `journeys_010.js`, `test_insights.py` |
| 44 | Next-step chips under a reply; they go on the next message | n/a | `test_insights.py` |
| 45 | While you were away: finished runs, and only real changes for tasks set to tell you on change | n/a | `journeys_010.js`, `test_firstrun_away.py` |
| 46 | Your work page and the recap image (counts only, no chat text) | n/a | `journeys_010.js`, `shots_home.js`, `app/tests/test_yourwork.py` |
| 47 | Edit a deck Meshpool made: a "make these your defaults" suggestion, accepting writes learned facts | n/a | `app/tests/test_teach.py` |
| 48 | Suggestions off hides everything | n/a | `journeys_010.js`, `test_insights.py` |
| 49 | Code sessions grouped by folder: collapse, search, new session in a folder, group by date | Code | `shots_code.js` |
| 50 | Turn latency is not slower than 0.0.9 | all | `latency.py` |
| 52 | Browser use from a cold start: Playwright installs itself, an existing Chromium is used, a page opens | Code, Work | `app/tests/test_browser_out_of_the_box.py` |
| 51 | Import from other agents: preview, dry run, ticked items only, secrets left behind, logins never read, memories wait in the tray | n/a | `shots_import.js` (with `seed_agents.py`), `app/tests/test_agent_import.py` |

Not testable in this Linux container, so checked by other means:

- **Computer use:** it controls macOS and Windows desktops only. `journeys.py` checks that it fails with a clear
  message here. A real check needs a Mac or Windows machine.
- **ChatGPT sign-in:** it needs a real ChatGPT account. `app/tests/test_chatgpt_wire.py` checks it against a mock.
- **Installers (dmg, exe):** `packaging/mac/test.sh` on a Mac, NSIS on Windows.

## Set-up (no paid calls)

1. Start the mock model:

   ```sh
   python testkit/mock_llm.py 8911 /tmp/mock.jsonl
   ```

2. Install Pi and Hermes as `docs/BUILDING.md` describes, then link them as `app/vendor/pi/node_modules` and
   `app/vendor/hermes`. Install `packaging/requirements-cli.txt` plus Hermes' own dependencies (`python packaging/hermes_requirements.py app/vendor/hermes > req.txt`, then `pip install -r req.txt`; Hermes itself refuses to install as a package) into the Python that
   runs the app.
3. Make a fake home with one skill and one MCP server for each app the importer reads, at `$QA_DIR/fakehome`
   (`run_all.sh` sets it as `HOME`). `e2e_full.py` expects the skills `claude-skill` and `codex-skill` and the MCP
   servers `desk-server`, `cursor-server` and `codex-server`.
4. Remove every credential variable (`OPENAI_*`, `CODEX_*`, any other provider's, cloud and GitHub tokens) from the
   app's environment. Otherwise a subscription harness signs in with your own account and makes real, paid calls,
   and it can write that sign-in into the fake home. `run_all.sh` does this.
5. Start the app with:
   - `MESHPOOL_HOME` set to an empty folder that holds a `meshpool.config` pointing at the mock;
   - `HOME` set to the fake home;
   - `MH_BROWSER_HEADLESS=1`.

   For journey 28, start a second app with `MH_OBSERVE=1`.
6. Run, in order: `e2e_full.py`, `journeys.py`, `ui_crawl.js`, `about.js`, `intr.js`, `modes.js`, `v24.js`,
   `midrun.js`, `crawl_all.js`, `jitter.js`, `update_flow.sh`.
