# Audit ledger: Meshpool 0.0.9 findings

Every finding from the 0.0.9 bug and benchmark audit, plus the unnumbered items (failed runs `X`, gaps `D`, performance `P`, harness bug classes `H`, missing features `G`, cost and speed targets `K`, CLI parity `CLI`). Each one has one owner workstream and ends in one of these states:

- **fixed**: changed in code, with a regression test that fails on the old behaviour
- **already fixed**: fixed before this pass, proven by a test added now
- **not reproducible**: a test that reproduces the reported scenario passes, and is kept
- **open**: not done yet

Per-workstream detail (fix, files, test) is in `docs/audit/ledger/<workstream>.md`.

| ID | Workstream | Finding | Status | Test |
|---|---|---|---|---|
| B1 | ws19-ui | A finished chat can lose its Undo and "files changed" buttons: app.js calls pvTurnCards before preview.js has loaded (bug (race condition)) | open | |
| B2 | ws19-ui | A damaged image in the Code file viewer shows a broken image and no message (media error not shown) | open | |
| B3 | ws19-ui | Video and audio files cannot be played or opened: "Binary file. Open it from the folder." with no button (missing functionality) | open | |
| B4 | ws19-ui | An empty .jpg opens in the text editor with a Save button (bug) | open | |
| B5 | ws19-ui | On a touch screen the message actions (Copy, Edit and send again) are invisible (unreachable buttons) | open | |
| B6 | ws19-ui | Corrupt Word/PDF files show raw developer errors, and the Word one is misleading (media error message) | open | |
| B7 | ws19-ui | On a phone-size window a Code side panel traps the user: it covers the whole screen and its Close button is off-screen (unreachable buttons) | open | |
| B8 | ws19-ui | On laptop widths (1024-1366 px) the side panel's Close button is hidden past the end of the tab strip (unreachable buttons) | open | |
| B9 | ws19-ui | Keyboard focus escapes the Settings dialog into the page behind it (unreachable by keyboard / accessibility) | open | |
| B10 | ws19-ui | Connectors > Add another app: pressing Add with empty fields gives no message (missing error state) | open | |
| B11 | ws16-mcp | After about 17 chats in 15 minutes, Send silently stops working: every chat holds 5 helper processes and about 15 open files, and macOS give… | open | |
| B12 | ws06-forge | On Python 3.10 and 3.11 the deck engine cannot be imported, but the installer and pyproject accept 3.10+ (platform / setup) | open | |
| C-01 | ws13-config | A UTF-16 settings file (PowerShell 5.1 >) crashes every command before anything starts (re-run by the lead) | open | |
| C-02 | ws13-config | Without MESHPOOL_HOME (README dev steps, terminal installs), mcp.json is never created, so memory, schedules, web and browser are silently m… | open | |
| C-04 | ws13-config | Two app instances on one data folder are not detected; a chat turn was lost | open | |
| C-08 | ws13-config | A permission-mode change in the file or CLI does not reach new chats, while Settings shows the new value | open | |
| C-09 | ws13-config | PATH= or MESHPOOL_HOME= in the settings file replaces the process value after the first turn | open | |
| C-21 | ws13-config | install.ps1 -App (documented) does a terminal install instead | open | |
| C-23 | ws13-config | app/requirements.txt (README dev steps) is missing five packages the app imports | open | |
| C-90 | ws13-config | config.py:89-93 falls back to the SOURCE TREE when MESHPOOL_HOME is unset, so running app code from source created src/skills/ (103 skills) … | open | |
| CLI-01 | ws23-cli | CLI PARITY (owner request): the terminal CLI must match the desktop/web app, not be a thin raw-model client. Everything a user can do in the… | open | |
| D-01 | ws07-engines | Engines default to deepseek-v4.1-flash when the configured model is not passed through: engines/{browser,code,credit,data,general,research}/… | open | |
| D-02 | ws20-cost-bench | Benchmark adapters missing: docs/BENCHMARKS_V2.md says OfficeBench adapter still to build; SpreadsheetBench needs LibreOffice. Office qualit… | open | |
| D-03 | ws17-acp | Hermes, Pi and Codex ACP adapters: findings rest on code reading only (not installable in the audit). Make them installable/testable in CI w… | open | |
| D-04 | ws08-routing | No Code adapter for office tasks: harness_router routes 'data words in a Code chat' to meshpool-work/cowork; Code mode silently becomes Work… | open | |
| F-01 | ws12-auth | the Code-mode repository picker silently uses the computer's own GitHub login (privacy / isolation) | open | |
| F-02 | ws13-config | a per-chat "Full access" choice silently becomes the default for every later new chat | open | |
| F-03 | ws08-routing | meshpool work never shows (or reports) the answer | open | |
| F-04 | ws10-persist | "Chat deleted" is not true if the window is reloaded or closed within 6 seconds | open | |
| F-05 | ws19-ui | "Folders I choose" and "Add a folder" do nothing when the native picker cannot open | open | |
| F-06 | ws16-mcp | raw Python error "'_Stdio' object has no attribute 'reader_error'" shown for MCP servers that exit | open | |
| F-07 | ws15-memory | search cannot find a chat by the name the user gave it | open | |
| F-08 | ws08-routing | memory and scheduling do not work in Code-mode chats, although every agent is meant to have them | open | |
| F-09 | ws23-cli | CLI subcommands ignore -h; meshpool app -h starts a second app | open | |
| F-10 | ws23-cli | CLI import and work show raw tracebacks for ordinary mistakes | open | |
| F-11 | ws12-auth | meshpool login without a terminal says there is no API key | open | |
| F-12 | ws23-cli | meshpool -p "" sends an empty request to the model | open | |
| F-13 | ws23-cli | meshpool -p without --auto denies every edit and command with no hint why | open | |
| F-14 | ws01-keys-setup | "Test connection" passes with a wrong API key | open | |
| F-15 | ws01-keys-setup | an invalid base URL gets a message about the API key | open | |
| F-16 | ws01-keys-setup | provider wording assumes a Meshpool key when another endpoint is used | open | |
| F-17 | ws15-memory | onboarding import step: nothing pre-ticked, imports switch broken servers on, imported sessions are hidden | open | |
| F-18 | ws08-routing | onboarding says tasks start in "Meshpool Projects", but chats run in hidden per-chat folders | open | |
| F-19 | ws13-config | three names for the same permission levels | open | |
| F-20 | ws19-ui | duplicate model names with no way to tell them apart in the composer model menu | open | |
| F-21 | ws19-ui | "Changed: hello.py (new)" for a file that already existed and did not change | open | |
| F-22 | ws04-usage | Usage "By model" lists agents, not models | open | |
| F-23 | ws16-mcp | Add another app with a URL that is not an MCP server says it "needs a token" | open | |
| F-24 | ws19-ui | spreadsheet preview shows formula cells empty when the file has no cached values | open | |
| F-25 | ws19-ui | brand spelled "MeshPool" in chats and menus | open | |
| F-26 | ws19-ui | small wording and display issues | open | |
| F-27 | ws18-browser | Browser use looks available in the UI | open | |
| F-28 | ws14-sched | a one-time scheduled task dated in the past is accepted and runs at once; the date error uses API words | open | |
| F-29 | ws16-mcp | "Add" in the MCP library throws an uncaught page error | open | |
| F-30 | ws11-updater | the sidebar offers "Update" on a git checkout, but there is nothing to click on the page it opens | open | |
| F-31 | ws16-mcp | "Check servers" paints the whole result red even when most servers are fine | open | |
| F-32 | ws15-memory | export "learning" uses a hard-coded model and stores whatever comes back as memory | open | |
| F-33 | ws15-memory | importing the same export again says "2 chats imported" again | open | |
| F-34 | ws10-persist | a file attached to the first message of a new chat is saved in the wrong folder | open | |
| F-35 | ws10-persist | attaching a file in a read-only folder shows a raw server error and leaves an empty chat | open | |
| F-36 | ws19-ui | the Export button loses its accessible name when the side panel is open | open | |
| F-37 | ws18-browser | on Linux, Capabilities offers Computer use as if it worked | open | |
| F-38 | ws01-keys-setup | a key pasted as "Bearer sk-..." is always rejected, although the code means to accept it | open | |
| F-39 | ws19-ui | controls that are offered but do nothing here, or are disabled with no reason | open | |
| G-01 | ws22-tools | No OS sandbox for agent commands (Codex uses Seatbelt/Landlock; Claude Code and Gemini CLI sandbox). Commands run as the user with full disk… | open | |
| G-02 | ws22-tools | No network restriction for agent commands (Codex blocks network in its sandbox by default). Add a network-off option tied to the sandbox. | open | |
| G-03 | ws22-tools | No secret filtering for the shell: the bash tool inherits the whole environment (env = dict(os.environ, ...) core/tools.py ~855); root of S-… | open | |
| G-04 | ws05-turnloop | Per-turn step limit: now 2000 by default (agent/core.py ~771). Verify a sane default that bounds spend (S-08: 1,854 requests in 120 s), add … | open | |
| G-05 | ws05-turnloop | Granular permission rules: 'Allow in this chat' allows EVERY command; Supervised asks even for ls. 0.0.12 added saved allow/deny rules — ver… | open | |
| G-06 | ws23-cli | User hooks around tool calls: 0.0.12 added hooks — verify Pre/PostToolUse work in app and CLI; hooks in imported plugins are skipped (plugin… | open | |
| G-07 | ws23-cli | Machine-readable headless output: 0.0.12 added --json/stream-json — verify usage (incl. nested calls), final-message file option, exit codes… | open | |
| G-08 | ws23-cli | Reasoning-effort control exists only as MH_EFFORT in the TUI (agent/tui.py:125). Add to app settings, per-chat menu and CLI flag; pass throu… | open | |
| G-09 | ws04-usage | Accurate usage and cost including sub-calls (S-02) and per-model pricing (W-14). | open | |
| G-10 | ws10-persist | Rewind or fork a conversation (Claude Code rewind/fork, Codex resume): only -c/--resume and file Undo exist. Add conversation rewind-to-mess… | open | |
| G-11 | ws24-worktrees | WORKTREES (owner asked 'how do we not have work trees'): sub-agents, parallel chats on the same repo, and scheduled runs all share one folde… | open | |
| G-12 | ws10-persist | Atomic chat saves (M09-01, critical). | open | |
| G-13 | ws16-mcp | Cancel a running tool on Stop for MCP and office tools (M22-01, M27-01). | open | |
| G-14 | ws03-compat | Compaction sized to the model's real context window (8K models fail; 32K chats die at exchange 16). | open | |
| G-15 | ws22-tools | Lint/test loop after edits (Aider --auto-lint/--auto-test): only mutation checks in auto mode. Add an optional post-edit lint/test step for … | open | |
| G-16 | ws23-cli | Telemetry export (OpenTelemetry) like Codex and Claude Code: opt-in OTLP export of spans/metrics (turns, model calls, tokens, cost, tool cal… | open | |
| H-01 | ws22-tools | Harness bug class: Background child keeps the bash tool's pipes open: the tool call hangs until the timeout (every code user who starts a se… | open | |
| H-02 | ws03-compat | Harness bug class: Local model servers: the default context window silently cuts the prompt (Ollama num_ctx), so tools 'do nothing' | open | |
| H-03 | ws02-stream | Harness bug class: Tool calls returned as text in content (no tool_calls): the turn ends and the raw JSON/XML is the 'answer' | open | |
| H-04 | ws01-keys-setup | Harness bug class: Python urllib with no CA bundle (macOS python.org Python) or a corporate TLS proxy: every HTTPS call fails (Partly handle… | open | |
| H-05 | ws03-compat | Harness bug class: Strict chat templates: two user messages in a row (or a non-leading system message) give HTTP 400/500 (Mistral/Devstral/G… | open | |
| H-06 | ws03-compat | Harness bug class: Timeouts sized for cloud APIs: slow local models (CPU, big prefill) time out before the first byte | open | |
| H-07 | ws05-turnloop | Harness bug class: Runaway loops: no step cap per turn, loop guards only add a note, greedy sampling for unknown models | open | |
| H-08 | ws22-tools | Harness bug class: Command output is buffered whole in memory (no cap while reading): cat of a huge log or a chatty build eats RAM | open | |
| H-09 | ws03-compat | Harness bug class: Context overflow that compaction cannot fix: the chat stays broken ('prompt is too long' on every message) (Partly) | open | |
| H-10 | ws20-cost-bench | Harness bug class: Too many or too large tool definitions (many MCP servers): provider limits (128 tools) and small windows | open | |
| H-11 | ws22-tools | Harness bug class: In-process grep/glob walk everything (node_modules, .venv, build dirs) and read whole files; Stop cannot cut them | open | |
| H-12 | ws06-forge | Harness bug class: 'Supports Python 3.10+' broken by newer syntax: deck engine validator does not compile on 3.10/3.11 (same as B12) | open | |
| H-13 | ws10-persist | Harness bug class: Undo/checkpoint stores grow without bound (full copies per chat), and every turn walks the whole folder | open | |
| H-14 | ws21-server-perf | Harness bug class: Opening a chat parses every saved chat; big transcripts make start-up and chat switching slow | open | |
| H-15 | ws05-turnloop | Harness bug class: Approvals: in Supervised mode every shell command asks, even ls; 'Allow in this chat' then allows every command (Partly) | open | |
| H-16 | ws16-mcp | Harness bug class: MCP servers see a different environment than the terminal: GUI app imports only PATH, and 'secret-looking' variables are … | open | |
| H-17 | ws02-stream | Harness bug class: Reasoning models through local/compatible servers: <think> text in answers, budget spent on thinking, no progress shown (… | open | |
| H-18 | ws16-mcp | Harness bug class: Process trees not killed: MCP servers via npx/uvx on Windows and test commands with a timeout leave orphans | open | |
| H-19 | ws03-compat | Harness bug class: Tool-call ids that one provider accepts and another refuses (format or uniqueness), after a model/provider switch | open | |
| H-20 | ws16-mcp | Harness bug class: Every chat starts its own copies of the MCP servers: memory grows with open chats and scheduled runs (Partly) | open | |
| H-21 | ws03-compat | Harness bug class: MCP tool schemas passed through unchanged to providers with a schema subset (Gemini, some strict servers) | open | |
| H-22 | ws22-tools | Harness bug class: Windows + Git Bash: models write 2>nul, which creates an undeletable file named nul in the project (Partly) | open | |
| H-23 | ws21-server-perf | Harness bug class: Logs that only grow: per-server MCP logs and the app log are never rotated while the app runs (Partly) | open | |
| H-24 | ws19-ui | Harness bug class: IME composition: Enter that confirms a Japanese/Chinese conversion sends the message (WKWebView on macOS) (Unclear) | open | |
| H-25 | ws05-turnloop | Harness bug class: Interrupted turn leaves tool_use without tool_result: every later request 400s (audit says NOT present — prove with a reg… | open | |
| H-26 | ws02-stream | Harness bug class: Lone surrogates / invalid UTF-8 in request bodies (audit: residual risk — guard and test) | open | |
| H-27 | ws22-tools | Harness bug class: CRLF / line-ending mismatches break edits or rewrite whole files (audit: not present — prove with test) | open | |
| H-28 | ws22-tools | Harness bug class: Windows desktop app: console windows flash for helper commands (audit: not present — prove with test of CREATE_NO_WINDOW … | open | |
| H-29 | ws21-server-perf | Harness bug class: Windows: second server binds a port already in use (SO_REUSEADDR) (audit: not present — prove with test) | open | |
| K-01 | ws20-cost-bench | COST: on 7 office tasks (same model) Meshpool Work cost 3.7x Codex in total (Work cheaper only on the two Word tasks; 2.3-8.6x on xlsx edit,… | open | |
| K-02 | ws20-cost-bench | COST (code): Meshpool Code dearer than Codex on rs-macros; CLI default 2.5x Codex on Forth. TARGET: Meshpool <= Codex and Pi on every coding… | open | |
| K-03 | ws20-cost-bench | SPEED: office tasks took 78-567 s (4.7-9.5 min for spreadsheet/invoice/deck). TARGET: wall-clock <= Codex and Pi per task at equal quality. | open | |
| K-04 | ws20-cost-bench | Public claims must match measurements: README 'all 7 passed' (quality held, cost did not), BENCHMARKS 'cheapest on every run', 'Any OpenAI-c… | open | |
| K-05 | ws04-usage | Auto routing's cost basis leaves out nested engine calls (shown 121k input vs 1.17M billed on the xlsx edit), so Auto compares harnesses on … | open | |
| M01-01 | ws09-turns-ui | Two concurrent POST /send (second tab, or a second Enter during the up-to-50 s office-tools wait) both pass the unlocked idle check and run … | open | |
| M01-02 | ws05-turnloop | Stop pressed while send() is still in the pre-turn window (office tools starting) is wiped by _start_turn/Agent.send resetting the stop flag… | open | |
| M01-03 | ws09-turns-ui | An exception in the turn epilogue (write_meta on ENOSPC or a Windows lock) kills the turn thread and leaves the chat working forever: Stop, … | open | |
| M01-04 | ws09-turns-ui | A queued/goal/auto-resume follow-up turn refused by the key check returns without a thread, leaving the chat stuck in working. | open | |
| M01-05 | ws09-turns-ui | Early-return turns (bad key, stopped before start) emit user without turn_end, so transcript.json says running and a restart claims the app … | open | |
| M01-06 | ws09-turns-ui | The MCP start thread unlocked check-then-set can overwrite working with idle while the first turn (often a scheduled run) runs, enabling a s… | open | |
| M02-01 | ws05-turnloop | Stop while an approval waits saves "The user denied this action. Do not retry it." in the model history, so the next turn refuses what the u… | open | |
| M02-02 | ws09-turns-ui | Approval ids restart at 1 after a restart and collide with saved cards, so approval_done closes the wrong card and a click on a stale card a… | open | |
| M02-03 | ws09-turns-ui | An approval open when the app exits stays a live Allow/Deny card in the reopened chat (no approval_done is synthesised on resume). | open | |
| M02-04 | ws09-turns-ui | Same root cause as M23-02: A harness permission request answered after the turn ended sets Session.state to 'working' with no turn running: … | open | |
| M02-05 | ws09-turns-ui | "Allow ... in this chat" lives only in memory and is lost when the reaper closes an unviewed chat, on restart or on handoff. | open | |
| M03-01 | ws09-turns-ui | A message sent with Enter right after Stop (Send stays enabled while stopping) is steered into the stopping turn and not run; at best the ch… | open | |
| M03-02 | ws17-acp | On ACP harnesses a message queued after Stop is dropped silently by the epilogue and its Queued chip never clears, although the user was tol… | open | |
| M03-03 | ws09-turns-ui | Send now on a turn that needs over 15 s to stop (sub-agents, office job, harness restart) falls into the steer/queue path and is never run. | open | |
| M03-04 | ws09-turns-ui | The _interrupting flag survives an interrupt that raced the turn end and mislabels the next unrelated Stop. | open | |
| M04-01 | ws05-turnloop | Any outage-looking error event in a turn (Pi emits one before its own retry) triggers an extra auto-resume turn and makes the goal loop igno… | open | |
| M04-02 | ws04-usage | The Spending limit per task setting is enforced only in engine loops, so chats, goal loops and scheduled tasks never stop on it (96 calls, a… | open | |
| M05-01 | ws10-persist | Undo books every folder change made during a turn to the agent, so a file the user created in their own editor meanwhile is deleted (edits r… | open | |
| M05-02 | ws10-persist | A turn started by a late addition (resume_steers) runs after end_turn and gets no checkpoint, so its file changes cannot be undone and disag… | open | |
| M05-03 | ws10-persist | Undo in one chat while another chat works in the same folder is booked as that turn's change, so undoing it later restores what the user und… | open | |
| M05-04 | ws10-persist | turns.json is written in place, so a failed write (disk full, crash) erases every checkpoint of the chat and renumbers turns under stale sha… | open | |
| M05-05 | ws17-acp | Undo on ACP/Pi chats cuts the transcript but the harness session keeps the undone turns, so the agent remembers and may redo them. | open | |
| M06-01 | ws10-persist | Deleting a running chat stops it at once, so clicking Undo restores a chat whose task was already killed. | open | |
| M06-02 | ws10-persist | The delete lives only in a 6 s page timer, so closing or reloading the page right after "Chat deleted" leaves the chat and its file copies o… | open | |
| M06-03 | ws16-mcp | close() before the MCP start returns (two concurrent reopens, delete just after open) leaks the chat's MCP server processes until the app ex… | open | |
| M06-04 | ws10-persist | A delete landing while get() rebuilds the chat lets the deleted chat back into LIVE with its processes until the reaper runs. | open | |
| M07-01 | ws09-turns-ui | A question open when the app exits comes back as a live card whose answer is rejected and which hides the Working status of later turns. | open | |
| M08-01 | ws09-turns-ui | A reopened chat greys out Send for up to 45 s while MCP starts, although Enter still sends and the server accepts it. | open | |
| M09-01 | ws10-persist | session.json is rewritten in place, so a save cut off by a full disk, crash or kill truncates it and the whole chat vanishes ("This chat is … | open | |
| M09-02 | ws10-persist | All threads share one transcript.json.tmp, so the transcript is regularly a half-written file and a crash then loses stop notes, approvals a… | open | |
| M10-01 | ws11-updater | After a failed helper (Mac admin prompt cancelled, Windows installer error) ready.json is kept, so every quit re-runs the helper, which reop… | open | |
| M10-02 | ws11-updater | A downloaded release that the maintainers withdraw stays ready (and survives restarts) and is installed at the next quit. | open | |
| M10-03 | ws11-updater | A failed periodic check overwrites ready (or downloading) with error, so install-on-quit is skipped and the Restart bar disappears. | open | |
| M10-04 | ws11-updater | When the downloaded installer is quarantined or deleted, Restart says it will be downloaded again but tick() short-circuits on ready+same ta… | open | |
| M10-05 | ws11-updater | A Windows code-only release whose code update needs the full installer errors every 15 minutes forever; the promised installer download neve… | open | |
| M10-06 | ws11-updater | A release with a wrong checksum makes every copy re-download the full installer every 15 minutes with no backoff. | open | |
| M10-07 | ws11-updater | A staged Mac app beside /Applications/Meshpool.app is never removed when a newer release supersedes it (or a helper fails). | open | |
| M10-08 | ws11-updater | The background tick() calls status(), which consumes the one-shot result.json, so the user may never see that the update failed. | open | |
| M10-09 | ws11-updater | A file accepted against re-read sums is recorded with the old hash, so the Windows helper refuses it on every install attempt. | open | |
| M10-10 | ws11-updater | Turning on automatic download leaves an available update undownloaded until the next 6-hourly check. | open | |
| M11-01 | ws11-updater | After an in-place update the running process keeps the old version in memory but new files on disk: the same update is offered and installed… | open | |
| M11-02 | ws11-updater | Restart-to-update and Update-now check _running() once; a scheduled task or message that starts during the (seconds-long) verify or download… | open | |
| M11-03 | ws11-updater | Restart spawns the new copy with a fixed 3 s wait before closing chats; if closing takes longer the new desktop copy sees the old instance o… | open | |
| M11-04 | ws11-updater | On a prerelease Mac/Windows app the Download update button says it is downloading but the pipeline refuses prereleases, so nothing ever down… | open | |
| M11-05 | ws11-updater | New packages are pip-installed before the code swap; when the swap fails the user is told nothing changed while the old code now runs on new… | open | |
| M11-06 | ws11-updater | A kill or power loss between the two renames of a code folder leaves no meshpool_harness folder and nothing at start-up recovers it. | open | |
| M11-07 | ws11-updater | Two Update-now clicks (the panel re-offers the button mid-install) run two unlocked installs that share APP/.meshpool_harness.new; one repor… | open | |
| M12-01 | ws11-updater | With auto-download off, a release found by the background check is never shown while the page stays open, and About keeps saying you have th… | open | |
| M12-02 | ws11-updater | The status the page polls stays idle during an in-place install, so the 60 s re-render shows an enabled Update now mid-install and invites t… | open | |
| M12-03 | ws11-updater | restartApp reloads on any /healthz answer after 2.5 s; /healthz carries no process identity, so the page can reload onto the old server that… | open | |
| M13-01 | ws12-auth | A refresh rejected (400 refresh_token_reused) because the Codex CLI/Codex harness already rotated the pair is reported as "sign in again" al… | open | |
| M13-02 | ws12-auth | A token refresh in flight writes the old auth.json back after codex logout or codex login as another account, resurrecting a signed-out sess… | open | |
| M13-03 | ws12-auth | A network error during the token refresh escapes chat() as a raw URLError: the agent does not retry it and the relay drops Pi/Hermes connect… | open | |
| M13-04 | ws12-auth | On Windows the refresh lock is treated as held after 30 s of failed attempts, so two processes refresh with the same single-use token and th… | open | |
| M13-05 | ws12-auth | A torn read of auth.json (another program rewriting it) becomes NotSignedIn, which the agent never retries, ending the turn with a sign-in e… | open | |
| M13-06 | ws12-auth | The usage-limit note and plan model list are process-wide, so after switching ChatGPT account the menu keeps saying the limit is reached unt… | open | |
| M14-01 | ws12-auth | A refresh that fails only because the network is down or the server 5xx-es marks the connector expired, so new chats silently start without … | open | |
| M14-02 | ws17-acp | Connector tokens are copied into Hermes/Pi/ACP harness sessions as static headers, so after about an hour every connector tool call in that … | open | |
| M14-03 | ws12-auth | The connector lock is held across the network refresh, so one slow auth server stalls every new chat's MCP start, the OAuth callback and eve… | open | |
| M14-04 | ws12-auth | Each MCP client refused with 401 forces its own refresh, so N chats on one connector refresh N times and can revoke each other's new tokens. | open | |
| M14-05 | ws12-auth | Disconnect does not cancel a pending OAuth flow, so a consent tab finished later reconnects and re-enables a connector the user removed. | open | |
| M14-06 | ws12-auth | GitHub device sign-in ignores slow_down and a second start replaces the first, so the sign-in can sit at "enter this code" until the code ex… | open | |
| M15-01 | ws01-keys-setup | Saving a local endpoint without a key in the provider page keeps the hosted endpoint's key, which is then sent with every call to the local … | open | |
| M15-02 | ws12-auth | "Signed in" and "ready" only mean auth.json exists, so an API-key Codex login or a dead refresh token leaves the app ready while every ChatG… | open | |
| M15-03 | ws12-auth | The ChatGPT sign-in spawns an untracked detached codex login; double clicks start several that fight over the callback port and an abandoned… | open | |
| M15-04 | ws01-keys-setup | Web onboarding saves the key and the provider in two requests; closing in between leaves a working key unused and onboarding shown again. | open | |
| M16-01 | ws13-config | A value cleared in meshpool.config by the CLI or a hand edit stays in os.environ (load_into_env skips empty values), so the app keeps using … | open | |
| M16-02 | ws16-mcp | The chat's project goes to MCP servers via the process-global MESHPOOL_PROJECT, so two chats (or probe_mcp, a scheduled task) starting toget… | open | |
| M16-03 | ws13-config | Two favourite stars clicked quickly lose one, because the list is read outside config.write's lock. | open | |
| M16-04 | ws17-acp | One shared config file per outside harness (Hermes, Codex, OpenCode) is rewritten in place at every chat start, so a concurrently starting c… | open | |
| M16-05 | ws13-config | meshpool.config writes are serialised only within one process, so the CLI and the app writing at the same time lose one side's keys. | open | |
| M17-01 | ws15-memory | Skill switches are an unlocked read-modify-write of .disabled.json written in place, so concurrent switches lose updates and a torn read re-… | open | |
| M17-02 | ws15-memory | Removing a skill leaves its id in the disabled list, so a skill later created or imported under the same name starts switched off. | open | |
| M17-03 | ws15-memory | New default skills are installed inside every skill read; two first reads after an update race on copytree (FileExistsError) and a torn mark… | open | |
| M18-01 | ws14-sched | An unattended scheduled run in the default Supervised mode waits forever on its first approval (never reaped, keeps the app open), and each … | open | |
| M18-02 | ws14-sched | due() starts a task again while its previous run is still working/waiting, so two agents edit the same folder (Undo then refuses both) and b… | open | |
| M18-03 | ws14-sched | A run that cannot start is consumed (last_run set, once-task disabled, shown as Paused) with only a desktop notification, none on Linux; no … | open | |
| M18-04 | ws14-sched | One malformed row (bad time/when, past next_run) makes due() raise every 20 s, so no scheduled task ever fires again, silently. | open | |
| M18-05 | ws14-sched | An unreadable (sharing violation) or corrupt schedules.json reads as an empty list and the next add/update/set_thread replaces the file, del… | open | |
| M18-06 | ws14-sched | Session.scheduled is never cleared, so the user's own follow-up in a scheduled chat gets ask_user defaults instead of waiting for them (unti… | open | |
| M18-07 | ws14-sched | Run now is not claimed or guarded: a double click, or Run now just before the due time, starts two runs of the same task. | open | |
| M18-08 | ws14-sched | A run missed while the app was closed fires whenever the app opens (late flag computed, never used): a morning briefing at 19:00 with no hin… | open | |
| M18-09 | ws14-sched | A finished one-time task shows as Paused; Resume makes it Active with next_run None, so it never runs. | open | |
| M18-10 | ws14-sched | Hourly and every-N-minutes schedules use naive local time and skip an hour at the autumn DST change. | open | |
| M18-11 | ws14-sched | delete_task by name removes every task with that name (agent delete_scheduled_task('Daily report') deletes both client variants). | open | |
| M19-01 | ws15-memory | Settings Save writes back the page's snapshot of memory and instructions, deleting facts saved meanwhile by agents, the learning loop or the… | open | |
| M19-02 | ws15-memory | A learned preference the user deleted (or with memory turned off) is still injected into the chat's next message as 'saved to memory; follow… | open | |
| M19-03 | ws15-memory | memory()'s read-side clean-up rewrite runs outside the lock and can overwrite a fact another process just saved under it. | open | |
| M19-04 | ws15-memory | Pending learnings live only in memory: after a restart the chat that taught a preference never hears it. | open | |
| M20-01 | ws15-memory | A chat moved to another project keeps its old .project/link binding, so its project_note facts land in (and are later injected into) the old… | open | |
| M20-02 | ws16-mcp | Same root cause as M16-02: The chat's project goes to MCP servers via the process-global MESHPOOL_PROJECT, so two chats (or probe_mcp, a sch… | open | |
| M20-03 | ws15-memory | Stop before the first turn starts consumes ctx_pending/project_ctx/learn pending; the re-sent first message has no instructions, memory or p… | open | |
| M20-04 | ws15-memory | create() makes the scratch folder (with project file copies) before Session() can fail, leaving an orphan per failed start (hourly for a sch… | open | |
| M20-05 | ws15-memory | Project memory and project.json use only a per-process lock (touch none): two chats' project notes lose 4-21 of 50, and a rename can be reve… | open | |
| M20-06 | ws15-memory | ctx_pending is memory-only: a chat reaped or restarted before its first message never receives its instructions/memory/project context. | open | |
| M20-07 | ws15-memory | Start-up migration turns every plain folder chat into a linked project the chat is not in; its project notes silently switch target. | open | |
| M21-01 | ws15-memory | A conversation whose write failed mid-import leaves a folder that makes every later import skip it, and the sidebar never shows it. | open | |
| M21-02 | ws15-memory | Deleting an imported chat leaves no tombstone: re-importing a newer export brings every deleted chat back after the next restart. | open | |
| M21-03 | ws15-memory | An upload aborted by a connection reset or a full disk leaves its temp .zip (up to 8 GB) behind. | open | |
| M21-04 | ws15-memory | The background context import reports context_pending but its failure is swallowed; nothing ever says the facts did not land. | open | |
| M22-01 | ws16-mcp | Stop abandons a running MCP tool call without notifications/cancelled, so the server keeps working and its file (deck, browser submit) lands… | open | |
| M22-02 | ws16-mcp | Images returned by a stopped MCP call are left in the chat-wide Manager.last_images and attached to the next request's first tool batch. | open | |
| M22-03 | ws16-mcp | A stdio MCP server that exits mid-request raises AttributeError (_Stdio.reader_error never initialised), shown as the server error and recor… | open | |
| M22-04 | ws16-mcp | A crashed stdio MCP server stays 'connected' and is never restarted, so every later call in that chat fails until the chat is reaped or the … | open | |
| M22-05 | ws16-mcp | Same root cause as M16-02: The chat's project goes to MCP servers via the process-global MESHPOOL_PROJECT, so two chats (or probe_mcp, a sch… | open | |
| M22-06 | ws16-mcp | A server start that only timed out (cold npx download during probe_mcp) is cached as failed for 30 minutes and skipped by every new chat and… | open | |
| M23-01 | ws17-acp | Stop while an ACP harness is still starting (initialize/authenticate) is ignored for up to 300 s; the turn then hard-kills the harness it ju… | open | |
| M23-02 | ws09-turns-ui | A harness permission request answered after the turn ended sets Session.state to 'working' with no turn running: messages queue forever, Sto… | open | |
| M23-03 | ws17-acp | When a resumed harness answers a cancelled prompt with 'refusal', Meshpool opens a fresh session and sends the stopped message again. | open | |
| M23-04 | ws10-persist | Same root cause as M09-01: session.json is rewritten in place, so a save cut off by a full disk, crash or kill truncates it and the whole ch… | open | |
| M23-05 | ws17-acp | Every Hermes launch rewrites the one shared hermes/config.yaml (model, endpoint, key), so a running Hermes that re-reads it picks up another… | open | |
| M24-01 | ws17-acp | Adding a message while Pi streams deadlocks Session.lock against Pi's reader: the chat freezes 30 s, the message is delivered to Pi but neve… | open | |
| M24-02 | ws17-acp | When Pi crashes mid-turn the reader sets _done, so the 'Pi exited during the turn' error is skipped and the half reply is shown as a finishe… | open | |
| M24-03 | ws17-acp | Every Pi launch rewrites the single shared /pi config (mcp.json with the project path, models.json), so concurrent Pi chats can start with a… | open | |
| M24-04 | ws17-acp | A message added while Pi is still starting is rejected with a misleading 'Pi stopped' 500 error. | open | |
| M25-01 | ws17-acp | Stop in Hermes/Codex/Pi reaches sub-agents in the shared assistant MCP server only if the harness forwards notifications/cancelled; otherwis… | open | |
| M26-01 | ws21-server-perf | Terminal panel commands run in their own session and no quit hook stops them, so a dev server keeps running (and holding its port) after Mes… | open | |
| M26-02 | ws21-server-perf | Terminal panel Stop sends only SIGTERM with no SIGKILL escalation, so a command that ignores TERM shows 'running' forever. | open | |
| M27-01 | ws16-mcp | The Office MCP server runs engine jobs without should_stop, so a cancelled/stopped make_deck or make_spreadsheet keeps building for up to 30… | open | |
| M27-02 | ws16-mcp | Stopping or timing out an engine job kills only the engine process; LibreOffice and model-written scripts it started keep running and writin… | open | |
| M27-03 | ws06-forge | The office rebuild counter is global to the app process, so a chat's first deck can be reported as 'build 3, stop rebuilding' because other … | open | |
| M28-01 | ws05-turnloop | A second image sent to a text-only model ends that turn and every later turn with HTTP 400 until restart, because _no_vision makes the image… | open | |
| M28-02 | ws04-usage | Engine (LiveClient), reviewer and helper model calls bypass the MH_MAX_INFLIGHT gate and endpoint health, so office/engine work exceeds the … | open | |
| M28-03 | ws05-turnloop | Stop does not cut the reviewer model call or the mutation check of a code turn (direct _post, no _wait), so the chat stays Working for up to… | open | |
| M28-04 | ws05-turnloop | On MH_WIRE=responses the model call runs outside _wait, so Stop has no effect for up to 600 s. | open | |
| M28-05 | ws05-turnloop | The 429 backoff counter is not reset when a turn ends on Stop or budget, so the next turn first waits 60 s instead of 3 s. | open | |
| M28-06 | ws05-turnloop | After every chat gave up on an outage, Health stays DOWN with nobody probing and the next message waits silently up to 60 s for the stale ba… | open | |
| M28-07 | ws04-usage | Stop before the connection object exists releases the gate slot while the abandoned request still goes out, exceeding MH_MAX_INFLIGHT. | open | |
| M29-01 | ws07-engines | meshpool run lets a failed routing-hint model call escape router.route, so a vague request fails (after ~11 min of LiveClient retries in an … | open | |
| M29-02 | ws08-routing | Auto has no "failed to start" transition: an installed agent whose process cannot start keeps being picked for every new Auto chat. | open | |
| M29-03 | ws08-routing | The routing outcome log flags a turn as failed if any of the chat's last 30 events was an error, so good turns after a failure are logged as… | open | |
| M29-04 | ws08-routing | outcomes.mark() has no caller, so undo/retry never mark a turn failed and refit()'s undo branch is dead. | open | |
| M30-01 | ws07-engines | A sheets run that ends aborted, on three tool-less replies or need_context skips salvage and review delivery, returning ok False with no art… | open | |
| M30-02 | ws07-engines | An input .xlsx copied into the job folder (shutil.copy, new mtime, original path in inputs) counts as saved by the run, so BUILD NOW never f… | open | |
| M30-03 | ws07-engines | With review on, delivering an earlier reviewed round copies it over the newer failing model.xlsx and keeps no copy of the newer version. | open | |
| M30-04 | ws07-engines | FINISH NOW is sent without a saved workbook, telling the model both to save a first workbook and to finish without changing it. | open | |
| M31-01 | ws07-engines | A research run that ends on a cap, abort or tool-less replies returns ok False with no artifacts even when report.md passes the gate, and th… | open | |
| M31-02 | ws07-engines | Same root cause as M35-01: The repeat_failure guard normalises digits, so a checker going FAIL 7 -> 6 -> 5 -> 4 -> 3 issues counts as five i… | open | |
| M31-03 | ws07-engines | sources.json is truncated and rewritten in place, so a save cut short leaves a corpus every later Corpus(".") and finish_check fail to load. | open | |
| M32-01 | ws18-browser | A browser call that times out (240 s chat server, 60 s ctl client) stays queued and runs later, so an action the agent was told failed (and … | open | |
| M32-02 | ws18-browser | Every chat starts its own browser server on the same persistent profile, so a second chat cannot launch the browser while the first holds it… | open | |
| M32-03 | ws18-browser | A ctl daemon whose page or Chromium died still answers _outdir, so start reports "already running" and every later browser task fails on eve… | open | |
| M32-04 | ws18-browser | The shared ctl daemon keeps the previous task's page, cookies and sign-ins, and the prefetch tells the next task it is already on that page;… | open | |
| M32-05 | ws18-browser | The ctl daemon never follows a tab opened by a target=_blank link, so the model keeps seeing the old page. | open | |
| M32-06 | ws18-browser | Computer use maps clicks with scale 1.0 until the first screenshot of the server process, so a resumed chat reusing coordinates from history… | open | |
| M33-01 | ws06-forge | The forge logo cache is written in place and trusted when non-empty, so a logo cut short by a killed or concurrent office job is served to e… | open | |
| M34-01 | ws05-turnloop | A real task added to a running quick turn (or sent as it ends) keeps the quick budget, so after 3 rounds the agent is told "time is up, no m… | open | |
| M34-02 | ws05-turnloop | Pi quick-request wrap-up is sent through steer, so "[Meshpool] Time is up..." shows as a message the user typed and is kept in the transcrip… | open | |
| M34-03 | ws05-turnloop | Switching plan mode while a turn runs swaps that turn's tools mid-way: the next shell call fails "unknown tool" under the Codex-style engine… | open | |
| M34-04 | ws05-turnloop | On a model that once refused tool_choice the quick wrap still offers tools, so "Do not call more tools" is never enforced. | open | |
| M34-05 | ws05-turnloop | "Build this plan" is drawn from the current plan mode, so after a reload in plan mode every old answer shows a build card that sends "implem… | open | |
| M35-01 | ws07-engines | The repeat_failure guard normalises digits, so a checker going FAIL 7 -> 6 -> 5 -> 4 -> 3 issues counts as five identical failures and abort… | open | |
| M35-02 | ws04-usage | With MH_BUDGET_USD set, the reply that crosses the budget is discarded even when it is an acceptable finish, so a finished task is reported … | open | |
| M35-03 | ws07-engines | A reply ordered [finish, action] is accepted at finish and the action never runs, while the run reports success (prompts invite action+finis… | open | |
| M36-01 | ws17-acp | In 'meshpool acp' a session/prompt sent right after session/cancel resets Agent._stop, so the cancelled turn keeps running alongside the new… | open | |
| P-01 | ws20-cost-bench | Fixed request overhead: ~11k tokens before the user types with 54 tool schemas (30 KB); 4.3k-6.4k in CLI runs (15 tools ~4.3k; default MCP s… | open | |
| P-02 | ws03-compat | Compaction ignores the model's context size: 8K models fail every message; 32K chats die at exchange 16 (same as W-03/G-14). | open | |
| P-03 | ws09-turns-ui | Reopened chats revive old state: tool groups spin forever, old approval cards come back with live Allow/Deny; the idle browser tab was 90% b… | open | |
| P-04 | ws21-server-perf | Corrupt-file preview: LibreOffice ran 15 min at 100% CPU and 1.27 GB and outlived the app; the 120 s timeout does not stop it (kill the proc… | open | |
| P-05 | ws10-persist | Whole-chat rewrite on every save: 213 MB written for a folder that ends at 1.4 MB; Undo snapshots are never pruned. | open | |
| P-06 | ws03-compat | Prompt caching: with more than 4 images only 27% of the prompt is cacheable. Keep a stable cacheable prefix. | open | |
| P-07 | ws21-server-perf | Many chats: opening, search, Usage and startup read every chat in full (65 MB at 1000 chats); the chat list stops at 400 chats. Use an index… | open | |
| P-08 | ws16-mcp | Helpers per chat: 5 MCP processes and ~15 fds per chat for 15 min. Soak: 180 processes, ~550 fds, ~1.5 GB (same as B11/S-13). | open | |
| P-09 | ws16-mcp | The idle reaper starts only when a chat is created; after a restart reopened chats are never cleaned up (ui/agent_api.py ~1193). | open | |
| P-10 | ws16-mcp | Closed MCP helpers' pipes stay open: 200 stale fds after the soak (agent/mcp.py _Stdio.close). | open | |
| P-11 | ws21-server-perf | Browser 6-connection limit: 20 tabs on one streaming chat, 14 stayed blank for 31 s; 'Meshpool isn't responding'. Multiplex streams (one SSE… | open | |
| P-12 | ws04-usage | Model-call gate: 4 slots per endpoint+key; with 10 chats, 6 waited 8-16 s (core/gateway.py:27-45). Size the gate to the provider, show waiti… | open | |
| P-13 | ws21-server-perf | Server listen backlog 5: bursts stall 1-3 s; 10 tabs pressing Enter together lost one message. | open | |
| P-14 | ws07-engines | Office runs are slow: 4.7-9.5 min for spreadsheet, invoice and deck tasks vs 78-84 s for Word. | open | |
| S-01 | ws01-keys-setup | An env-supplied MESHPOOL_API_KEY is sent to the default llm.meshpool.ai gateway when the settings file was copied from the defaults (empty k… | open | |
| S-02 | ws04-usage | Session and UI usage leave out nested office-engine calls (spreadsheet edit 121k shown vs 1.17M billed; invoices 41 hidden calls; budget 23) | open | |
| S-03 | ws06-forge | Slide engine reviewers call undeployed fixed models and send temperature; 66% of deck requests failed | open | |
| S-04 | ws08-routing | Code-mode office prompts are routed to the Work agent without telling the user | open | |
| S-05 | ws08-routing | Design: files attached in Work chats are saved and edited under attachments/ in the chat folder, so the "in place" result is not at the fold… | open | |
| S-06 | ws22-tools | Provider zoo + fault injection | open | |
| S-07 | ws01-keys-setup | 301/302/303 redirects to another host carry the Authorization header (confirms W-01); 307 is not followed | open | |
| S-08 | ws05-turnloop | No per-turn step cap: 1,854 requests in 120 s on a repeating tool call | open | |
| S-09 | ws02-stream | A malformed SSE stream ends with exit 0 and a truncated answer (confirms W-19) | open | |
| S-10 | ws02-stream | A tool call returned as text is printed raw as the answer (confirms harness class 3) | open | |
| S-11 | ws03-compat | A self-signed TLS endpoint: clear error, but -p keeps re-checking for up to 30 min instead of exiting | open | |
| S-12 | ws20-cost-bench | Fixed per-request overhead: ~4.3k tokens with 15 tools; ~6.4k with the default MCP servers (38 tools) | open | |
| S-13 | ws16-mcp | Each chat keeps 5 MCP helpers (15–68 MB each) for ~15 min; 20 helpers next to a 128 MB server during the deck runs | open | |
| W-01 | ws01-keys-setup | chat calls, streamed calls, the Anthropic wire and engine calls follow a 301/302/303 redirect. The Bearer or x-api-key header is sent to the… | open | |
| W-02 | ws03-compat | models or servers without tool calling fail every turn. This covers Ollama's "does not support tools" and vLLM's "auto tool choice requires…… | open | |
| W-03 | ws03-compat | max_tokens is always 8192. A small-context vLLM server or a model with a 4096 output cap rejects every call. On the context error Meshpool a… | open | |
| W-04 | ws03-compat | strict endpoints break every chat. - A field that Azure or Gemini rejects (prompt_cache_key) cannot be dropped, because the code that remove… | open | |
| W-05 | ws02-stream | a server that ignores stream: true costs 4 calls (3 wasted generations plus a probe) and 7 s per step. Engine calls on such a server get an … | open | |
| W-06 | ws01-keys-setup | the onboarding check reports OK for any HTTP 200 (LM Studio's 200 error body, an HTML page). Chats on that endpoint then end with "(no reply… | open | |
| W-07 | ws02-stream | parallel tool calls that share index 0 are merged into one call. | open | |
| W-08 | ws02-stream | tool arguments sent as an object instead of a string crash the turn. | open | |
| W-09 | ws02-stream | engine and ping calls wait forever on a stream that only sends keep-alives. The app agent's own reader has the guard; a control test shows i… | open | |
| W-10 | ws02-stream | Retry-After problems: - the HTTP-date form is ignored; - a reset past the 30-minute wait budget is still waited out in full; - engines ignor… | open | |
| W-11 | ws02-stream | the Anthropic wire drops prompt caching (no cache_control). | open | |
| W-12 | ws02-stream | the Anthropic wire is never streamed. A reply that takes longer than the 120 s first-byte timeout is generated, and billed, twice. | open | |
| W-13 | ws02-stream | tool_choice: "none" is turned into "auto" on the Anthropic wire. | open | |
| W-14 | ws04-usage | the per-task budget cap uses one fixed DeepSeek-level price for every model, so it trips 20–100x too late on expensive models. A reply witho… | open | |
| W-15 | ws01-keys-setup | with HTTP_PROXY set, calls to a 127.0.0.1 model server go through the proxy, carrying the key. | open | |
| W-16 | ws01-keys-setup | 0.0.0.0:port gets https://, and the TLS failure is reported as "No server". | open | |
| W-17 | ws01-keys-setup | Anthropic-compatible endpoints without /models are saved as the openai wire. | open | |
| W-18 | ws01-keys-setup | a non-HTTP port crashes the setup test with an HTTP 500. | open | |
| W-19 | ws02-stream | a cut stream is accepted as a complete reply. | open | |
| W-20 | ws02-stream | a non-object data: line crashes the turn. | open | |
| W-21 | ws02-stream | multi-line SSE data fields are dropped. | open | |
| W-22 | ws02-stream | reasoning_content is removed from the history. | open | |
| W-23 | ws01-keys-setup | onboarding does not clean up the address. A bare host like localhost:11434, or a pasted /v1/chat/completions, gets a 404, although the Setti… | open | |
| X-01 | ws06-forge | Chart-deck task (pptx from quarterly.csv, 'revenue by quarter for each region') produced ONE chart with ONE series 'Q4 revenue' and failed t… | open | |
| X-02 | ws20-cost-bench | CLI default (FULL profile) on the Forth coding task: passed 54/54 but 7 of 8 edits failed before it recovered; 252k input tokens (2.8x Codex… | open | |
| X-03 | ws10-persist | Multi-turn: a file attached to the FIRST message of a new chat is rejected (empty root), shows a double toast and leaves an empty chat; rese… | open | |
| X-04 | ws06-forge | Sales-deck run took 24.5 min: 4 extra decks and 3 engine runs for one request. After a page reload, 36 live approval buttons and 30 spinners… | open | |
| X-05 | ws10-persist | Undo after a turn: an automated check could not find the Undo button; whether it is really missing is unconfirmed (B1 is the confirmed race)… | open | |
| X-06 | ws09-turns-ui | A transient provider retry warning (e.g. 429/5xx retry notice) stays on screen after the answer succeeded. Clear/resolve it when the retry s… | open | |
| X-07 | ws01-keys-setup | `meshpool check --live` asks for max_tokens/max_output_tokens=5; Responses API endpoints require >=16, so the setup test can never pass on A… | open | |
| X-08 | ws20-cost-bench | Benchmark grader: the PDF report task's chart check is not required, so a PDF with no chart passes. Make the grader's chart check required (… | open | |
| X-09 | ws20-cost-bench | Old grader grade_uc.py: restored tests in place, counted Codex command/file items as calls, and for Rust read only the doc-test line (0 or 2… | open | |
| X-10 | ws04-usage | Engine nested-usage estimate (transcript chars/4) undercounts input 14-25% and output 4-46% vs the provider. Use provider-reported usage eve… | open | |

## Workstreams

| Workstream | Scope | Items |
|---|---|---|
| ws01-keys-setup | Keys, redirects and provider setup | 17 |
| ws02-stream | Streaming and response parsing | 17 |
| ws03-compat | Endpoint compatibility, context windows and compaction | 13 |
| ws04-usage | Usage, cost and budgets (incl. nested calls) | 11 |
| ws05-turnloop | Agent turn loop: step caps, guards, quick turns, stop | 19 |
| ws06-forge | Deck engine (forge/slides): reviewers, models, content checks | 7 |
| ws07-engines | Sheets, research, data, general engines | 12 |
| ws08-routing | Routing, modes, Work output location | 9 |
| ws09-turns-ui | Chat turn state machine: send, stop, queue, approvals, questions | 17 |
| ws10-persist | Persistence, Undo, delete, attachments, rewind/fork | 19 |
| ws11-updater | Auto-update pipeline | 21 |
| ws12-auth | ChatGPT sign-in, connectors OAuth | 15 |
| ws13-config | Config, install, Python versions | 13 |
| ws14-sched | Scheduled tasks | 12 |
| ws15-memory | Memory, projects, skills, import | 21 |
| ws16-mcp | MCP lifecycle, shared helpers, office jobs cancel | 24 |
| ws17-acp | External harnesses: ACP, Pi, Hermes, Codex | 14 |
| ws18-browser | Browser and computer use | 8 |
| ws19-ui | Front-end UI bugs | 19 |
| ws20-cost-bench | Cost/speed vs Codex and Pi: benchmark harness and reductions | 11 |
| ws21-server-perf | Server performance, terminal panel, logs | 9 |
| ws22-tools | Shell/file tools, sandbox, secrets, lint-test loop | 11 |
| ws23-cli | CLI parity with the app, CLI UX, headless, effort, hooks, OTel | 9 |
| ws24-worktrees | First-class git worktree isolation | 1 |
