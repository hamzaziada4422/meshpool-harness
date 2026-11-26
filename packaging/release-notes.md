The first public beta of Meshpool: one desktop agent for code, documents, spreadsheets, decks and research.

**New in 0.0.18**
- **Far fewer approvals.** In Ask and Accept edits, ordinary commands and file edits in the chat's folder now run without a question, inside the OS sandbox (bubblewrap on Linux, sandbox-exec on macOS). Those commands can write only in the folder and the temp folder, and they have no network. meshpool still asks before it deletes files, uses the internet, installs something, works elsewhere in your home folder or outside the folder, or writes over an Office file you have open. On a computer without the sandbox (Windows, or Linux without bubblewrap), the first question offers "Allow ordinary commands and edits in this folder in this chat", and one yes covers the rest of the chat. In the persona runs, summarising a PDF asked 6 times and a data job 17 times; replayed now, the steps in the folder run without a question and the ones outside it still ask.
  - Questions say what the step does ("Run a Python step on sales.csv?", "Read report.pdf?", "Use the internet (example.com)?"), with the command below.
  - Settings, Permissions is its own page, and search finds it. Its "When meshpool asks you" choice: only for risky actions (the default), before every command and change, or never (Full access). `MH_SANDBOX_AUTO=0` also asks as before.
- **Errors say what went wrong.** A failed model call used to reach the chat as "meshpool HTTP 200 but not a model reply: {...}", a raw web page or a Python exception. The chat, the terminal, `meshpool -p`, `meshpool check --live` and the office engines now say what happened in plain words, quote the service's own words, name the host and model, and say what to do next. A sign-in page from a proxy is called a web page, with its title. A model that does not exist is reported at once, not after five tries over half a minute. A key the service echoes back is hidden. While the model service is down, the chat keeps one line that says when it checks again. The app's own errors say what happened too: a locked file, a full disk, a save while meshpool is not responding. A failed memory export is no longer saved as the file.
- **Your folder is your project.** A folder you pick in onboarding, or with Add a folder in the sidebar, becomes a pinned project, and new chats in it work in that folder, not in a hidden app folder. A file you drop that is already in the folder is used where it is. Other dropped files stay with the chat and are not copied into your folder. Code chats in the folder are listed under the project. Undo says what it put back and names what it could not. The message it puts back sits in the message box with the cursor at the end, so you can edit it or press Enter to send it again.
- **Download.** The preview and each file card have a Download button that saves the file with your browser's own download.
- **Decks without internal errors or the product footer.** A slide whose content step failed no longer carries the model's error in its speaker notes, and the footer no longer says "Meshpool" unless your brand sets a footer. A deck saved under the name you asked for is moved there, not kept twice, and build files (qa.json, slides.json and others) no longer show among your files. A long deck job shows its stage while it works. When a deleted slide is still inside the file, the answer says so.
- **It looks at what it made before it says it is done.** When a task makes or changes a web page, meshpool opens it in a headless browser at desktop and phone width and tries it like a user: it clicks the buttons and links, submits the forms empty and filled, walks the page with the keyboard and opens the menus. It measures console errors, broken images, sideways scrolling, overlaps, cut text and low contrast. The model gets your request, what was found and the pictures, fixes what is wrong and is checked again, at most twice. Word files, sheets and decks it makes in a chat are drawn as pages and checked the same way, unless the Office engine already checked them. The answer ends with a "Checked:" line that says what was looked at and fixed. A separate reviewer, with a fresh context and none of the builder's reasoning, first names who would really use the thing. That is the end user, for example a customer ordering on her phone, not the person who asked. It tries the page as that person, asking for up to 4 actions of its own, and returns problems ranked blocker, major or minor. The builder fixes them and the same persona checks again, at most twice. The "Checked:" line names the persona. A reply the model gets wrong is retried once. The page is served only from your project folder on this computer, and every other request, other files and the internet included, is blocked while it is checked; text from the page reaches the model marked as data, not instructions. A check takes a few seconds and 1 to 2 CPU seconds, starts no server, stops when you press Stop and leaves no browser running; a page that freezes ends the check in seconds. The reviewer uses the chat's own model: at most 4 extra calls a turn, with at most 4 pictures each, counted in the receipt; the builder gets at most 2 review rounds. In `meshpool -p` the "Checked:" line goes to stderr and into the JSON result's own field, so scripts that read the answer are not affected. Turn it off in Settings, Features, "Look at web pages it makes".
- **Spreadsheet previews show the values Excel shows.** Numbers keep their format (412,000, $3.50, 12%, (1,234)), a formula shows on hover, hidden sheets are marked, and at laptop widths the columns fit instead of a long title pushing the rest out of view.
- **Office edits leave your open files alone.** An edit to a file that Word, Excel or LibreOffice has open goes to a copy and says so. A tracked change can now be made inside an earlier reviewer's insertion, where it used to fail.
- **A smoother app.** Measured in the browser: a streaming reply no longer redraws its paragraph on every word (840 elements thrown away in one long reply before, at most 10 now), so text you select while it streams stays selected. The sidebar, header and message box are updated in place, not redrawn. A chat opens at its end without a jump. The sidebar slides instead of laying out the page again for 200 ms. One-off motion ends within 200 ms, and reduced motion is respected. The open chat is clearly marked, keyboard focus shows in the message box, and the model name is no longer cut at 1024 px. A file dropped anywhere on the conversation is attached. A long wait for the model says so in the chat, with Stop. Settings search finds a page by what it holds, such as "folder" or "approval".
- **A calmer, clearer look.** A design pass after unslop by Damir Sokolovsky (CC BY 4.0): one easing curve and motion of 200 ms or less, with no spring, bounce, pulse or hover lift; hover and the selected item look different, and every tab uses one style; pool blue is the only accent; a new dark theme with readable contrast (4.5:1 or better); headings at a lighter weight in IBM Plex; spacing on one 4 px grid with rows of one height; one icon size; plain words in place of jargon, and no greeting. Opening a preview keeps the chat wide enough to read. Opening or closing the side panel or the sidebar no longer makes the chat jump: it glides to its new place, and opening a chat no longer shifts its text.
- **Command line.** `meshpool -c` resumes in the mode you give, or the default mode, never silently in the full access the chat last had, and on the `--model` you give. `--model` with a name the endpoint does not serve stops and lists the close names, instead of carrying on with another model. `-p` names each command it refused and a flag that allows it; with the sandbox, ordinary commands in the folder run. A stopped turn stays in the history, marked stopped, and `/exit` works while the agent works. Text wraps while it streams. In a Code chat, the Changes tab after Undo, long diff lines and the panel tabs at 1024 px are fixed.
- **One LibreOffice, kept running.** Deck renders, workbook recalculations, previews and document conversions used to start a new LibreOffice each time. meshpool now keeps one running and sends every conversion to it, then closes it after five minutes unused. On 8 different files: 1 start and 1.66 s of CPU, against 8 starts and 4.75 s before. It ends with the app, even when the app crashes, and when it is busy for more than 30 s a preview converts on its own instead of waiting. If it cannot start, conversions run the old way. Set `MH_LO_RESIDENT=0` to turn it off. The download is somewhat larger because it now carries LibreOffice's own Python, which drives the kept copy.
- **Less CPU in the app.** Measured with counting tests, and by the persona testers:
  - The chat view no longer re-reads the trust files on every poll: 350 file reads for 25 polls before, none now.
  - A sidebar refresh no longer reads 4 files of every project: 960 reads for 10 refreshes, none now.
  - The @ picker no longer re-reads every source file on each key, and asks git once per burst of keys instead of twice per key.
  - The open editor no longer hashes the file every 4 seconds.
  - Header redraws reuse the branch name instead of running git status each time.
  - A Python or JSON edit checks its syntax without starting a new Python process, and a JavaScript edit asks node once, not three times.
  - The hooks files, model hints and model profiles are read once while unchanged, not on every tool call or model step.
  - A long-running terminal command is polled every 2 seconds once its panel is closed, not 4 times a second for good.
  - In the persona runs against 0.0.17: a finance user's previews were 2 to 3 times faster after the first file, with about 35% less CPU and a third of the LibreOffice starts, and six tasks took 5.0 s of app CPU against 9.4 s. A developer's web Code session ran 28 git processes against 102 (7 against 22 for one edit turn), and an edit in the command line used 46% less CPU. A data analyst's session used 1.1 s of CPU against 1.9 s, and 26% less while a reply streamed.
- **Notifications come from meshpool.** On macOS a finished task's notification was filed under another app (people saw it as TextEdit). It is now posted by meshpool itself, and not at all when meshpool is not running as the app. On Windows, notifications showed as "Windows PowerShell". They now show as meshpool. Neither change has been checked on a real Mac or Windows computer yet.
- **The platform audit's second pass is closed.** docs/audit/LEDGER-pass2.md checks all 123 of its findings: 119 are fixed with a test, 1 needs no test and 3 could not be reproduced. Among the last fixes: iframes in the browser engine, Undo removing the folders it emptied, and file-name scans that stay fast on long messages.
- **287 bugs fixed.** docs/audit/LEDGER-0.0.18.md lists 287 bugs found after 0.0.17: by six area bug hunts, a later pass, the owner's security fixes, the release check, five persona testers and the hunts that followed them, a pre-release review, the design pass, the look review step and its independent reviewer. Each has the test that checks it, except two: a race that could not be made to happen on demand, and a list of search words. 21 items stay open for 0.0.19, and the ledger lists them. Among the fixes:
  - Security: the /files route and the memory settings could reach files outside their folders. A sandboxed command on Linux could change meshpool's own settings. The read-only shortcut ignored a command's folder. "Allow in this chat" for `uv run` or `npx` covered any program they started. The browser was opened with the app's lifetime key on its command line, and the desktop app wrote that key into its log. Another local server opened in the same browser received meshpool's session cookie and could use it to read the page's API token. The Office panel's page carried its access token, so any program on the computer could read it. The panel now pairs: it shows a 6-digit code, you allow the same code in meshpool, and only then does it connect. It pairs again each time it is opened and after meshpool restarts.
  - Office files: Word replacements could change the wrong text after a hyperlink, or change linked headers twice. A failed in-place save could destroy the original. Reads now include tracked insertions, footnotes, footers, content controls and hidden-sheet and hidden-slide marks. Windows CSVs keep their accents. A password-protected file says so. An Excel file with shapes, slicers, sparklines or threaded comments is edited as a copy, because saving it in place would lose them.
  - Converting a document while LibreOffice was open on the computer could quit that LibreOffice.
  - `meshpool scheduler` could delete the user's other cron entries when `crontab -l` failed.
  - A refused key is no longer sent again until retries run out, and part of a key can no longer show in an error message.
  - A reopened chat keeps each turn's steps in one group, and Stop no longer marks steps as failed.
  - On Windows, the letter à typed in the terminal is no longer lost.
  - A question meshpool asks in the Office panel can be answered there again.
  - A paired Office panel could read the chats another paired panel started. A one-time launch key could open the page a second time without its cookie.
  - Allowing "docker compose ps" for a chat also allowed `docker compose exec`. Grants now name the verb.
  - A chat saved on a model the endpoint no longer lists could not be opened.

**New in 0.0.17**
- **Less CPU.** Measured before and after on the same machine:
  - Five open chats start no helper processes until one is used, then share one (15 helper processes before). Idle CPU with them open fell about 5 times.
  - A reply streaming into a long chat uses about a third of the CPU it did, and reasoning about a sixth. The page no longer asks for git status on every streamed piece, and a code block is coloured when it settles, not on every word.
  - The Session panel checks git every 15 seconds when idle instead of every 4, and not at all while the window is hidden.
  - A deck starts LibreOffice once instead of three times, and a workbook twice instead of five times.
  - Looking at a web page or a table (view_output, ui_audit, ui_smoke) reuses one hidden browser instead of starting a new one for every look. It closes after two minutes unused. A second look takes about 0.5 s instead of 1.3 s.
  - The skills list, the git safety check and program versions are kept instead of being worked out again on every step, so a ten-step turn starts about half as many processes.
  - In the terminal, typing no longer runs git, and your status line command runs when its input changes.
- **Bugs fixed, each with a test.** docs/audit/LEDGER-0.0.17.md lists 602 bugs from three audits (the 0.0.16 workstream audit, a platform-wide audit and this release's own review), with the status of each and the test that checks it. 598 have a test. Among the fixes new in this release:
  - `meshpool setup` no longer saves a key the endpoint refused (a 4xx answer), and it no longer blames the network for it.
  - ChatGPT sign-in runs a pinned Codex (0.171.0), not whatever is newest.
  - A plain `pip install` now includes the default settings, profiles and fonts.
  - The Windows installer works in a folder whose path has an apostrophe.
  - A 16:9 slide master sized 10 x 5.625 in is scaled and used instead of refused.
  - The icon, template and brand caches are private to each user, so another user on the computer cannot plant files in them.
  - `--mcp-config` servers no longer start again in every command and child run.
  - The `probe` and `trace` tools ask like any command instead of failing.
  - A repository with submodules gets an isolated copy again, and meshpool's git calls in a copy never run the repository's own hooks or fsmonitor. A repository whose git settings run programs on checkout (a filter, an external diff) gets no copy.
  - Saved allow rules never cover a chained, PowerShell, non-ASCII or path-glob command, and read-only git commands in a repository whose git settings run programs ask first.
  - On Windows, a command no longer ends with "lang.sh: line 19: /usr/bin/locale: No such file or directory". The bundled Git has no locale program, so meshpool now sets LANG for its Git Bash from your Windows language when nothing else sets it.
  - Still open: the cost and speed targets against Codex are only partly met (see the 0.0.16 table), three tests still wait a fixed time, and typing speed in very long chats is not measured.
- **The browser follows a new tab.** When a link or a script opens a page in a new tab, the agent's browser now switches to it before it reports what it sees. Before, it still described the old tab for one step.
- **Release builds are sturdier.** LibreOffice is downloaded from the main site when the archive site is down, still checked against its pinned checksum. The release smoke test now makes a real tool call: the v0.0.16 smoke test passed on a reply that held a tool error.
- **The benchmark pages say what was measured.** The README and BENCHMARKS now carry the 0.0.16 office results against Codex (cheaper per solved task on every suite, fewer tasks solved on SpreadsheetBench and OfficeBench) instead of the older "not cheaper yet" line.
- **Office edits run on Codex's own request.** In a Work chat, an edit to an existing workbook, document or deck, or a file you name, now sends the request Codex CLI 0.171 sends: its prompt and its one command tool, with meshpool's permissions around it. A new deck, report or model and Word tracked changes still use the engines. `MH_WORK_OFFICE_STYLE=lean` brings back the 0.0.16 route. Measured with the settings of the 0.0.16 rows (raw rows in docs/results/office_c171/):

  | Suite | Tasks x runs | Solved, meshpool vs Codex | Cost per solved vs Codex (95% CI) | Time vs Codex | 0.0.16 on the same suite |
  |---|---|---|---|---|---|
  | Office-7 | 7 x 6 | 41 vs 41 | 0.89x (0.81-0.95) | 0.53x | 42 solved at 0.94x |
  | SpreadsheetBench (public sample), same hour as Codex | 6 x 6 | 33 vs 33 | 0.89x (0.78-1.03) | 1.22x | 31 solved at 1.04x |
  | OfficeBench (public sample), same hour as Codex | 4 x 6 | 24 vs 22 | 1.02x (0.63-1.31) | 1.07x | 21 solved at 0.83x |

  A new check before the answer makes sure that text from a named file, added to a Word document, is in its paragraphs as given (not hidden in a content control or reworded). It fired only on the OfficeBench task that failed before (ob-1-15-2: 12 of 12 runs pass now, 8 of 12 before). Where meshpool loses: OfficeBench costs about the same as Codex (1.02x, and the interval spans both cheaper and dearer), not less, and on SpreadsheetBench and OfficeBench it takes longer than Codex in the same hour (1.22x and 1.07x). Office-7 solves one task fewer than 0.0.16 did.
- **A project's own servers wait for your trust too.** The MCP servers a project lists in `.mcp.json` or `.meshpool/mcp.json` start only once you trust the folder, like its hooks and allow rules. The app now shows a note at the top of the chat with Trust this folder, and `meshpool trust`, `meshpool trust --revoke` and `meshpool trust --list` manage trusted folders from the command line.
- **Always in this folder.** An approval can now save a narrow rule for this folder (this command, file edits, or one tool) in your own settings, so the next chat there does not ask again.
- **Rules that always ask.** Next to allow and deny, a saved rule can now always ask, even with full access or an allow rule. Add one in Settings, Permissions.
- **See the model think.** When the model sends its reasoning, meshpool's own agent shows it as it comes, in a Thinking block in the app and on the status line in the terminal.
- **Live command output.** A command that is still running shows its output under its step as it comes, with keys and tokens hidden.
- **Code blocks you can use.** Code in replies is coloured, with the language shown and a Copy button.
- **Pictures in replies.** An image a reply names (a chart, a screenshot) shows in the reply; click it to open it.
- **Faster typing.** `@` in the message box lists matching files in the chat's folder, and functions and classes after them. Up and Down in an empty box bring back what you sent. A long paste becomes a chip. A one-line message starting with `# ` is saved to memory. In the terminal: Up reaches prompts from earlier sessions in the same folder, Ctrl+R searches them, Ctrl+S stashes the prompt, Alt with an arrow moves by word, and `!command` runs a command and passes its output to the agent.
- **Search inside files.** The Files panel search finds lines inside text files, not only file names.
- **Continue after a restart.** If meshpool closed while a task ran, the note in the chat has a Continue button.
- **meshpool in Excel, PowerPoint and Word (new, not yet checked in real Office).** A meshpool panel on the Home tab: ask about your selection, then insert the answer at the selection, in a new sheet, as a new slide, or in place of the selected text. Set it up in Settings, Plugins, Office apps, or with `meshpool office install`. It needs Microsoft 365, Office 2021 or later, or Office on the web.
- **Git panel.** Stage and unstage single files, commit only what is staged, and see recent commits.
- **Saved sub-agents.** Markdown files in `agents/`, `.claude/agents/` or `.meshpool/agents/` define named helpers with their own instructions, tools and model. They can preload skills, set an effort level, or work without the project's instructions. `/agents` lists them.
- **Secrets stay off the screen.** Keys, tokens and passwords in tool output are hidden on screen and in saved chats. The model still reads the real output.
- **Instructions in subfolders.** A subfolder's own `AGENTS.md` or `CLAUDE.md` applies the first time work reaches that folder.
- **No idle sleep during a task**, and `/compact what to keep` tells the summary what to keep in full. The terminal prints what a session took when you leave. Search and the `@` picker leave out files git ignores.
- **Ask on the side, work in two folders.** `/btw` asks a quick question without touching the running task. `/add-dir` lets the agent work in a second folder.
- **Try again keeps the old answer.** Arrows under the reply switch between versions.
- **Rules for some files only.** A rules file with `paths:` or `globs:` applies only when work touches a matching file.
- **Safer outside content.** You can list the sites agents may read, and the sites they must never read. What the web and connected apps return is marked as untrusted. A connector can be set to read only.
- **Smaller things.**
  - Scheduled tasks take a cron expression.
  - Chats can be kept for a set number of days.
  - The agent can read MCP resources.
  - Errors name the provider's request id.
  - A reply cut off by a dropped connection is kept.
  - You can set a line to add at the end of the agent's commits.
  - The terminal can show your own status line.
  - The Diff panel has a side-by-side view.
- **Terminal.** Esc stops the agent while it works, and what you type meanwhile is kept for the next prompt. New commands: `/context`, `/export` and `/copy`.
- **More instruction files.** `MESHPOOL.local.md` and `CLAUDE.local.md`, every file in `.meshpool/rules`, `.claude/rules` and `.cursor/rules`, and another agent's file (`GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md` and similar) when a project has none of its own.
- **Hooks.** `SubagentStart`, `PostToolUseFailure`, `PostCompact`, `Notification`, `SessionEnd`, `PermissionDenied`, `StopFailure`, `DirectoryAdded` and `InstructionsLoaded` now run, and so do `WorktreeCreate` and `WorktreeRemove` when meshpool makes or removes an isolated copy. A hook can run a program directly with `args` (no shell), in the background with `async`, once a chat with `once`, and can change a tool call's arguments with `updatedInput` (your rules then judge the changed call). `SessionStart` also runs after a long chat is summarised. Settings, Run hooks turns every hook off. See docs/setup/hooks.md.
- **Skills you can shape.** In a skill's front matter: `when_to_use`, `disable-model-invocation` (only when you ask for it with `$name`), `user-invocable: false`, arguments (`$ARGUMENTS`, `$1`), `disallowed-tools` and `paths`. A skill never grants itself permissions. See docs/setup/skills.md.
- **Scripts and CI.** `--system-prompt` and `--system-prompt-file`, `--append-system-prompt-file`, `--session-id`, `--fork-session`, `--no-session-persistence`, `--settings`, `--mcp-config` and `--strict-mcp-config`, `--subagent` (work as one of your saved sub-agents), `--disable-slash-commands`, `-n` (`--name`), `meshpool mcp add-json` and `meshpool completion bash|zsh|fish`. See docs/setup/terminal.md.
- **Check your setup.** `meshpool doctor` (or `/doctor`) finds broken settings, hooks, rules, MCP server lists, skills and sub-agents. It also names risky lines in skills you add or import, such as a download piped into a shell, and never opens key or credential files.
- **Reviews.** `/review quick`, `/review` and `/review deep` report up to 5, 10 or 25 findings (`--max N` to change it), and `/simplify` makes changed code simpler without changing what it does. In the terminal and the app.
- **Take back a message.** A message you send while the agent works can be taken back into the message box until the agent reads it.
- **Plans as files.** Each plan from plan mode is also saved as a Markdown file. `/plan file` shows where.
- **Connected apps.** A server's prompts work as commands (`/mcp__server__prompt`). A tool its server marks as one that deletes or overwrites data asks first in every mode.
- **The agent import** can bring command hooks from Claude Code, unchecked until you pick them.
- **Appearance.** Hide the model's reasoning, turn off code colours, or stop Esc from stopping a running task. Table previews sort by any column and filter rows.
- **More settings.** Your own instructions for the summary of a long chat, instruction files to skip, a plans folder, and extra headers or query parameters for the model provider. The terminal window's title shows the folder and the chat. Search skips `.jj` and `.sl` folders.
- **Steadier agent.** Search skips files git ignores and binary files, and every agent is told that what tools bring back is data, not instructions.

Not in this version, though earlier notes or the ledger may suggest otherwise: a phone app, a hosted web app, sync across devices, voice input and read aloud, and a per-request cost from the meshpool endpoint (asked of the gateway team).

**New in 0.0.16**
- **Hundreds of bugs fixed.** A full audit of the app, the agent, the terminal and every engine found and fixed several hundred problems, each with a test. Among them: undo of a rename that only changes letter case, Stop on a helper agent no longer throws away a report it already finished, the Look question is asked again, Anthropic keys work when the saved address differs, and slides, intro pages, merger heatmaps and DCF sheets no longer overflow or point at the wrong cell.
- **Small and local models work.** Meshpool learns a model's real context window from the server or from the first cut-off reply, and no longer shrinks to a tiny window by mistake. An unknown model gets 200k tokens instead of 128k; only a real Ollama or LM Studio server starts small. A model that keeps compacting is stopped instead of looping.
- **Less CPU.** The PDF reader and other built-in tools start once and are shared, not started again for every chat, and they only start when a file needs them.
- **More keys connect.** Custom auth headers (`api-key`, `x-token`, raw, basic) work again, Microsoft Entra tokens on Azure are sent the right way, and Test connection rejects a 200 reply that is not a model answer. A request that carries a key never follows a redirect; the error names the address it was sent to and what to set instead.
- **Fewer wasted calls.** When a model keeps sending a call Meshpool already refused, or keeps sending calls that fail without changing any file, it is told to change course and then stopped with a best-effort answer, instead of running until the time limit. A patch with a missing first or last marker, or a blank line between files, is repaired instead of failing. Cleanup such as `rm -f scratch.py` or `rm -rf __pycache__` inside the project is no longer refused as destructive, and a command whose pipes were sent as separate words runs as a pipeline.
- **Decks built on the company's own filings.** For a public company, research now reads its latest earnings release, 10-Q and 10-K for forward capex, buyback, dividend and credit-facility figures, each cited to the filing. Cash-floor slides anchor on the stated borrowing capacity. Set `MH_RESEARCH_GUIDANCE=0` to turn it off. Headline figures on the cover and two extra consistency checks are available as opt-in switches.
- **A coding agent built on Codex's own design.** Code chats send the request Codex CLI 0.171 sends: its prompt, its single command tool and its environment context, with Meshpool's permissions, sandbox, loop guards and compaction around it. On the code suites below it solved within one task of Codex (39 vs 38, 144 vs 145, 22 vs 22) at 0.71-0.79x Codex's cost per solved task. Set `MH_SMALL_REPO_STYLE=core` to use Meshpool's own lean prompt on small repositories instead.
- **Office files done the direct way.** In a Work chat, a change to an existing workbook, document or deck, or a new file you name ("make budget.xlsx"), is written with the file's own library, keeping formulas and styles. The deck, report and model engines are used for an open-ended deliverable ("a 10-slide pitch deck for investors"). Before answering, the agent is held to its own checks: a failed assertion, or a deck with a different number of slides than asked for, means the job is not done.
- **Measured against Codex 0.171.** Same model (deepseek-v4.1-flash), gateway and hour, two runs per task (six for the office suites), cost per solved task as a multiple of Codex's (95% interval), time as the median of per-task time ratios. Losses are listed with the wins.

  | Suite | Tasks x runs | Solved, Meshpool vs Codex | Cost per solved vs Codex (95% CI) | Median time vs Codex |
  |---|---|---|---|---|
  | SWE-bench Verified, SymPy (large repo) | 12 x 2 | 22 vs 22 | 0.76x (0.69-0.84) | 0.93x |
  | v2_dev code | 21 x 2 | 39 vs 38 | 0.79x (0.69-0.90) | 0.99x |
  | v2_eval + build, debug, deep, hard | 78 x 2 | 144 vs 145 | 0.71x (0.65-0.78) | 1.03x |
  | Work: DABench + v2_dev data tasks | 27 x 2 | 45 vs 44 | 0.51x (0.44-0.58) | 0.81x |
  | Office-7 (decks, documents, workbooks) | 7 x 6 | 42 vs 41 | 0.94x (0.72-1.18) | 0.76x |
  | SpreadsheetBench (public sample) | 6 x 6 | 31 vs 35 | 0.90x (0.74-1.05) | 1.07x |
  | OfficeBench (public sample) | 4 x 6 | 21 vs 24 | 0.83x (0.60-1.06) | 0.87x |

  Where Meshpool loses: SpreadsheetBench and OfficeBench solved fewer tasks than Codex (31 vs 35, 21 vs 24), and
  SpreadsheetBench ran 7% slower at the median and v2_eval 3% slower. The SpreadsheetBench row is the shipped build,
  with the date and formula checks, against Codex's runs from the hour before; two earlier SpreadsheetBench runs
  without the formula check came out at 1.04x and 1.16x Codex's cost per solved task. The office suites are small and
  were run six times each, so their intervals are still wide. The Office-7 and OfficeBench rows were measured before
  the last review and security fixes (permissions, the app's page token, bounded file checks). The Work row was
  measured before the last office-file changes, which only act when a chat writes a Word, Excel or PowerPoint file.

  v2_eval and v2_dev shaped Meshpool's prompts; the other suites did not.

**New in 0.0.14**
- **Azure keys connect the way Microsoft's current docs say.** A plain Azure OpenAI or Azure AI Foundry address now uses Azure's newer `/openai/v1` route, which needs no API version and serves every deployment, including DeepSeek, Llama and Grok. Before, Meshpool pinned an old version on the older route, and newer models were refused with "unsupported api version". If your resource only has the older deployment route, Test connection finds that out by itself, and Save keeps the route that worked. Paste a full portal address with `/deployments/...` or type an API version to use the older route on purpose. The API version box now says what to do with it.
- **A key can no longer follow a redirect.** The agent's own model calls refuse to follow a redirect to another host, as the settings page and Test button already did.

**New in 0.0.13**
- **Azure keys work, and so do most others.** Paste an Azure OpenAI or Azure AI Foundry address (or the whole endpoint URL from the Azure portal), the deployment name and the key, and Meshpool uses the right address and header for it. The same goes for OpenAI, Anthropic, OpenRouter, Groq, Together, Fireworks, Mistral, xAI, DeepSeek, Gemini, Bedrock, GitHub Models, NVIDIA, Cerebras, SambaNova, Perplexity, Ollama, LM Studio, vLLM and LiteLLM. Test connection checks the key without calling it bad when a service has no model list, and a wrong key, deployment or API version now says what to check. A key found in your environment is only offered for its own provider. The terminal login asks for the deployment and version too. Tested against stand-in servers for each service; real Azure and the other services have not been tried yet, so tell us if one does not connect. See docs/setup/connecting-a-model.md.
- **Plain asks get a plain answer.** A vague request for a newsletter, a plan, an email or a short report is answered in the reply, with one file only when you ask for one or the job needs it. Fewer steps, no extra tracker files, and no invented facts or [placeholders]. Skills that asked for a plan plus a tracker plus research were trimmed.
- **The reviewer no longer asks for the impossible.** If a change to a live system cannot be read back, it is marked as unchecked, and the agent is never sent back to write again.
- **Fewer false alarms from connected tools.** A tool result counts as a failure only when it says so in a structured way, or is one short error line.
- **Your project folder stays clean.** Screenshots of what Meshpool checks go to a temporary folder, not into your project.
- **The click-through test also tries touch drags and notification buttons.**
- **Connected tools at scale.** Tools served over HTTP streams, schemas with references and choices, and lists of hundreds of tools were tested and work.

**New in 0.0.12**
- **It finishes the job.** Give Meshpool a task that needs several tools and it carries on to the end: it fixes and retries when a tool fails, reads every page of a long list before it writes, and no longer stops to ask a question it can answer itself. It never tests with a real write on your live systems, and it does not repeat a write that already worked. The reviewer no longer demands extra writes to prove one.
- **Connected tools that fail are called failures.** An error from a connector, or a result that says it did not work, is marked as a failure so the agent changes its approach. If the same result comes back again and again, it stops and says so.
- **Hundreds of connector tools.** With a long tool list the agent sees a short index and loads each tool's details when it needs it, so every step costs far fewer tokens.
- **Cowork from the terminal.** `meshpool -p "..." --profile work` runs the work agent, and headless runs never stop to ask.
- **Better pages and apps.** The agent now uses what it builds before it says it is done: it clicks every control, submits forms empty and with nonsense, tabs through the page, reloads to see that your data stayed, and reads the console for errors. Build skills for apps, games, dashboards and sites now carry a checklist of what a person expects, and a new skill covers scripts, command line tools and APIs, with tests and a README.
- **The reviewer checks the click-through too**, and flags leftover template text such as [Studio name].

**New in 0.0.11**
- **A second pair of eyes on big jobs.** Before Meshpool tells you a big task is done, a separate reviewer that did not do the work checks it against what you asked for, in code, documents, decks, spreadsheets and research. If something is missing it goes back to be fixed, and the reply ends with a short "Independent check" in plain words. It is on by default, only runs on big tasks, and you can turn it off in Settings or check any reply yourself with "Check this" (or `/grade` in the terminal).
- **Nothing else opens on your computer.** Files and folders open in a built-in editor inside Meshpool, with a folder tree, search, line numbers, colouring, find, and Save with a warning when a file changed on disk. Markdown and JSON have a readable view. No text editor, Finder or Preview launches.
- **The browser stays inside Meshpool.** When the agent opens a page it runs in a hidden browser that ships with the app, never in your Chrome. A Browser panel shows what it is doing live, and you can click, type and scroll in it to sign in or pass a check. Settings has a switch to show the browser in its own window.
- **Sign in without leaving the app.** Connector and account sign-in pages open in that panel. If a site refuses to sign you in there, one link opens it in your own browser.
- **Meshpool's own window uses its own engine** before it ever looks for Chrome or Edge.
- **Everything it needs is built in.** ffmpeg, pandoc, speech to text (with its model), notebooks, charts and HEIC photos now ship inside the app, so no skill has to download or install anything. The Apple silicon audio and video converter needs macOS 12; speech to text, PDF and image tools work on macOS 11.
- **Safer builds.** Every Mac component is checked against the macOS 11 minimum before a release is built.

**New in 0.0.10**
- **See what Meshpool knows.** A new Memory page shows every fact, filed by category, with where it came from and which chats it was given to. Edit, pin, move or archive any of them, and every change has Undo.
- **Meshpool asks before it remembers.** Facts it notices wait in a "Meshpool thinks" tray. Nothing reaches an agent until you say yes. Two facts that disagree are shown side by side.
- **Nothing is lost.** Your old memory is copied to a backup and moved over safely. When memory fills up, Meshpool proposes a tidy-up instead of dropping the oldest facts.
- **See what a reply was given.** A small chip under a reply lists the memories it was given. A new "What the agent sees" button shows the exact background, and lets you leave a fact out of one chat.
- **Incognito chats.** Press Ctrl+Shift+N. Nothing is remembered, learned or logged.
- **A quiet nudge, only when it helps.** One short line above the message box, such as "Run this every week?". It never appears while Meshpool works, at most two a day, and never twice for the same idea.
- **Next steps in one click.** After a deck, a workbook or a report, a few ideas appear under the reply.
- **A first run built from your files.** Meshpool reads only file names and offers real starters, or runs a sample.
- **While you were away.** Finished scheduled runs, and only real changes for tasks set to "Only tell me when something changed".
- **Receipts you can trust.** Each reply shows the time, the cost where it is known, and why Meshpool picked that agent. Decks and reports list the checks that actually ran.
- **Your work.** A look back over 1, 3, 6 or 12 months, ending in things you can do, plus a recap image with counts only.
- **Summaries.** A one-line summary for each chat, and a living brief for each project with Catch me up.
- **Learns from your edits.** Change every slide title of a deck Meshpool made, and it offers to make that your default.
- **Code sessions by folder.** Sessions are grouped by folder, with a new-session button on each, search, and a dot when one needs you.
- **Simpler import.** Three steps and direct links to your old assistant's export page.
- **Browser use works the first time.** Playwright is now a normal part of the install. From a terminal or source install it sets itself up when first needed, and it uses a Chromium already on the computer before downloading a second one. This is what lets Meshpool open your app and click through it for UI and UX checks.
- **Bring your other agents.** Meshpool finds Claude Code, Codex, Cursor, OpenClaw and Hermes on your computer and lists their skills, tools, memories and instructions. You tick what you want and can preview first. Passwords and keys are never read, and tools come over without their secrets. Memories wait for your yes. From a terminal: `meshpool import agents --dry-run`.
- **Meshpool looks at what it makes.** A page, slide, chart, logo, PDF, document or workbook is turned into pictures and checked before Meshpool says it is done. Web pages are also audited at desktop, tablet and phone width, light and dark. Models that cannot see images get the text and measurements instead.
- **It can build interfaces properly.** A new Build a UI skill starts from a tested page, keeps one design system and fixes what the audit finds. Method adapted from the open Taste Skill (MIT).
- **Twenty-two more ready-made skills** for sites, games, apps, dashboards, charts, diagrams, logos, PDFs, images, resumes, invoices, newsletters, study packs, citations, databases, transcripts and more.
- **Safer by default.** Keys you saved in Meshpool are hidden from commands the agent runs. "Always allow" never covers deleting recursively, force-pushing, wiping a disk or piping a download into a shell. A chat is saved while it works, so a crash loses nothing. A single turn stops after 2,000 steps instead of running forever.
- **Power-user controls.** Hooks, saved allow and deny rules, instruction files from your home and parent folders, your own `/commands`, `@file` mentions, `meshpool -p --output-format stream-json`, tasks that run while the app is closed, notebook cell editing, Mermaid diagrams and maths in chat, Word tracked changes, an optional sandbox for commands, a switch to run tasks while Meshpool is closed, and in-place edits of Word, Excel and PowerPoint files.
- **Long tasks hold together.** Older steps are summarised into a note, /compact does it on demand, and long commands can run in the background.

**New in 0.0.9**
- **Meshpool for Linux.** A download for 64-bit Linux with everything inside (LibreOffice, Chromium, Node.js, Git and Python), tested in a bare Ubuntu 22.04 before it is published.
- **Decks built to the consulting standard.** Slides follow the BCG and McKinsey grammar. "From → to" rows show the old way and its cost, then the new way and its cost. Evidence lists put a company's logo next to one line of proof. Panel headings read as one argument from left to right. A callout adds the so-what.
- **Slides that point the way.** Each numbered point beside a chart marks the bar it explains with the same number. A pointer leads from the evidence to its so-what. Difference arrows and target lines never print over the chart's labels.
- **Cleaner pages, checked on decks written by the real model.** Footnotes go one per line and are never cut. Table columns are sized to their content. A layout that does not fit is never kept. There is no takeaway box with a bar down its side, and bullets are never cut mid-sentence. In blind reviews against a BCG deck, decks from four new briefs scored level with it.
- **Charts that show the point.** A short column chart carries its insight on the chart:
  - the multiple or gap as an arrow ("25-35x", "+7 days");
  - the goal as a dashed target line;
  - a short line under each bar saying what it is.
- **Say it once.** A point made on one slide is not repeated on later slides, and a callout never restates the cards beside it. The writer is sent back when it repeats itself.
- **Meshpool learns your preferences.** When you correct its work or state a preference ("never put a label over the title", "always use Segoe UI"), Meshpool saves it to your memory. It follows it from the next message, in every chat and with every agent, and the deck writer follows it too. You see "Noted for next time" when it learns something. Turning memory off in Settings stops it.
- **The same deck shape every time.** Before any model plans a deck, the brief is read for the kind of deck, the audience, the question and the options. It is then given a fixed consulting skeleton: the answer and the ask, where we stand, the options on the same measures, the economics and the plan. Different models start from the same plan.
- **Dated correctly.** The writer knows today's date. Plans never schedule work in the past.
- **A clean white top, in Segoe UI.** There is no label over the title. The section number sits top right.
- **Ask any chat for a PowerPoint.** It now produces one on every agent: 14 of 14 test runs made a deck, up from 10 of 14. A chat whose agent cannot make the file moves, with its history, to one that can.
- **Auto prefers MeshPool's own agents on a tie.** Free web search is bundled in the apps.
- **Decks with zero images.** Icons are native, editable vector shapes in your theme colours. Company logos are fetched from the internet and drawn as native shapes in their own brand colours. A deck with a picture fails its checks.
- **Nine new diagrams.** Risk matrix, 3x3 matrix, Gantt plan, issue tree, org chart, cycle, hub and spoke, pyramid and funnel, drawn natively and checked like every other slide.
- **Seven style packs** from 130 published decks, chosen with `--style` or in Settings > Company, and an optional title-band look.
- **Layout lookup.** Agents ask which layout proves a slide's message before writing it.
- **No two slides look alike.** The layout of each slide is chosen knowing its neighbours: a risk list next to a table becomes icon columns, next steps become a numbered process, and chart pages alternate their layout. Body text is larger (14 pt).
- **No gaps.** Blocks side by side end at the same line, next steps and risk tables use the whole slide, and short tables run full width with their takeaway under them. A check fails any slide where a column stops short of the one beside it.
- **Points, not bullet lists.** The argument beside a chart or diagram is drawn as cards, each with an icon that fits its meaning and a short header; four short points become a 2x2 of icon boxes.
- **91 built-in slide layouts** ship with the app as compact specs (the 50 that render clean are offered); the storyline picks one per slide and the writer fills its slots.

**New in 0.0.7**
- **A new look, built for everyday use.** Chats no longer need a folder. Projects hold their own instructions, files and memory. The sidebar has Pinned and Recents with unread dots, chats group by date, and every chat has a menu to rename, pin, move, archive or delete it. Search finds chats, projects, artifacts and scheduled tasks in one place. Settings is one window with plain sections. Streaming is smooth: no jumps, no flicker.
- **MeshPool Auto with a stronger engine.** Coding work runs on MeshPool's new engine, built on the open-source Codex design (Apache-2.0) with MeshPool's routing, loop guard and context trimming on top. On our 52-task hard suite (same model for every agent, graded by hidden tests) it solved 47/52, the same as Codex, about twice as fast as MeshPool's previous engine. Full numbers: docs/results/v3/.
- **Connect any model in three steps.** Enter a base URL and API key, press Test connection, pick your default model from the list it returns, save. Works with OpenAI- and Anthropic-style endpoints; switching endpoints never gets stuck on an old model list. MeshPool also finds a ChatGPT sign-in from the Codex command line and API keys in your environment, and uses them only if you say so.
- **Everything is inside the app.** LibreOffice (trimmed to what slides, sheets and documents need), Node, Git and a browser ship inside MeshPool. Nothing is borrowed from your computer or downloaded later.
- **Updates install themselves.** MeshPool downloads updates in the background, checks them against their published checksums, and installs when you restart or quit.
- **A new terminal app.** `meshpool` in a terminal: a clean input box, tool steps with timings, collapsible output, Markdown answers, slash commands (/model, /login, /help and more) and Shift+Tab to change permissions.
- **Session panel.** The repository, branch, pull request (with Mark ready), connectors used, files made and background tasks of the current chat, in one side panel. Start a session straight from a GitHub repository without cloning it yourself.
- **Mac with Intel** now needs macOS 11 or later. Codex and OpenCode are no longer bundled; they run through npx and download the first time you pick them.

**New in 0.0.6**
- The New chat button works again. Clicking it did nothing before; Ctrl+N and /new were not affected. If an earlier version saved the click as your folder, it is ignored, so updating fixes the home screen.
- Sub-agents for Meshpool's own chats: a chat hands self-contained parts of a job to sub-agents that work at the same time, each starting fresh, and report back. They use the chat's tools and permission mode, never read outside the project, and anything that would need your yes is refused (they cannot ask) unless you let them approve their own actions. They work inside the chat's turn, so Undo covers what they change and Stop stops them. Settings, Advanced: access, model, how many at once, steps, a time limit and levels.
- Decks research their facts: figures from SEC filings and the market data a valuation needs, with capital-allocation economics computed for you. Recommendations come with sized options, reviewers' changes apply to the whole deck in one pass, and a model that returns nothing usable fails the build instead of leaving a blank deck.
- Word, Excel and PowerPoint requests with no content, or sent to the wrong engine, are refused with a clear message instead of producing an empty file. Meshpool Work in other editors (over ACP) gets the same Office tools as the app.
- Meshpool Code shows the project lines a failing command points to, and rejects an edit that would change nothing.

- **Agents:** MeshPool Auto (picks the setup per task: quality first, then cost and speed), MeshPool Code, MeshPool Work, Pi and Hermes, or add your own.
- **Runs on** your MeshPool API key or any OpenAI- or Anthropic-compatible endpoint and key.
- **Office files** checked before they reach you: Word, Excel with live formulas, PowerPoint, PDF.
- **Decks** in a top strategy firm's style by default: the answer first in an executive summary, an agenda and section tracker, takeaway titles, a different exhibit on every page, Segoe UI and line icons. Add slides to your own deck in its look.

| Computer | Download |
|---|---|
| Mac with Apple silicon | Meshpool.dmg |
| Mac with Intel (macOS 11 or later) | Meshpool-Intel.dmg |
| Windows 10 or 11 | Meshpool-Setup.exe |
| Linux, 64-bit (glibc 2.35 or later) | Meshpool-Linux-x64.tar.gz |

**Setup guide** ([full guide](https://github.com/hamzaziada4422/meshpool-harness/blob/main/docs/SETUP.md))

*Mac, quickest:* open Terminal, paste this and press Return. It installs Meshpool and opens it with no security prompts:

```sh
curl -fsSL https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/install.sh | bash -s -- --app
```

*Mac, from the download:*
1. Not sure which Mac you have? Apple menu → About This Mac: **Chip: Apple M…** takes Meshpool.dmg, **Intel** takes Meshpool-Intel.dmg.
2. Open the .dmg and drag **Meshpool** onto **Applications**.
   <img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/mac-dmg-drag.png" alt="Drag Meshpool onto Applications" width="420">

3. Double-click Meshpool in Applications. macOS blocks it the first time: click **Done**.
   <img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/mac-not-opened.png" alt="Meshpool Not Opened: click Done" width="260">

4. Open **System Settings → Privacy & Security**, scroll to **Security**, click **Open Anyway** and enter your password. Meshpool opens, and opens normally from then on. (macOS 14 and earlier: right-click Meshpool in Applications, choose **Open**, then **Open**.)
   <img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/mac-open-anyway.png" alt="Privacy and Security: click Open Anyway" width="520">


*Windows:* run Meshpool-Setup.exe. If "Windows protected your PC" appears, click **More info**, then **Run anyway**.

*Linux:* unpack the download and run its installer, which adds Meshpool to your applications menu and the `meshpool` command to your PATH:

```sh
tar -xzf ~/Downloads/Meshpool-Linux-x64.tar.gz -C ~/.local/share && ~/.local/share/Meshpool/install.sh
```

LibreOffice, Chromium, Node.js, Git and Python ship inside the folder; it uses the desktop libraries every Linux desktop has. To update, unpack the new download over the old folder. Its checksum is in SHA256SUMS.txt.

<img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/windows-smartscreen.png" alt="Click More info" width="300"> <img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/windows-smartscreen-run-anyway.png" alt="Click Run anyway" width="300">

<img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/first-launch-key.png" alt="First launch: paste your key" width="420">

*First launch:* paste your Meshpool API key ([get one at meshpool.ai](https://meshpool.ai)) or sign in with ChatGPT, then choose the folder Meshpool may work in. Updates: click the "is available" bar, or Settings → About → **Check for updates**, then **Update now**.

Macs, Apple silicon and Intel, need macOS 11 or later (browser use needs macOS 13). Meshpool is not yet signed by Apple or Microsoft, which is why your computer asks you to confirm it once. The pictures of the macOS and Windows prompts are illustrations; the wording on your computer may differ slightly.

This is a beta: when something breaks, click **Report this** next to the error, or the **Beta** badge.
