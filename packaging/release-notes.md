The first public beta of Meshpool: one desktop agent for code, documents, spreadsheets, decks and research.

**New in 0.0.16**
- **Hundreds of bugs fixed.** A full audit of the app, the agent, the terminal and every engine found and fixed several hundred problems, each with a test. Among them: undo of a rename that only changes letter case, Stop on a helper agent no longer throws away a report it already finished, the Look question is asked again, Anthropic keys work when the saved address differs, and slides, intro pages, merger heatmaps and DCF sheets no longer overflow or point at the wrong cell.
- **Small and local models work.** Meshpool learns a model's real context window from the server or from the first cut-off reply, and no longer shrinks to a tiny window by mistake. An unknown model gets 200k tokens instead of 128k; only a real Ollama or LM Studio server starts small. A model that keeps compacting is stopped instead of looping.
- **Less CPU.** The PDF reader and other built-in tools start once and are shared, not started again for every chat, and they only start when a file needs them.
- **More keys connect.** Custom auth headers (`api-key`, `x-token`, raw, basic) work again, Microsoft Entra tokens on Azure are sent the right way, and Test connection rejects a 200 reply that is not a model answer. A request that carries a key never follows a redirect; the error names the address it was sent to and what to set instead.
- **Fewer wasted calls.** When a model keeps sending a call Meshpool already refused, or keeps sending calls that fail without changing any file, it is told to change course and then stopped with a best-effort answer, instead of running until the time limit. A patch with a missing first or last marker, or a blank line between files, is repaired instead of failing. Cleanup such as `rm -f scratch.py` or `rm -rf __pycache__` inside the project is no longer refused as destructive, and a command whose pipes were sent as separate words runs as a pipeline.
- **Decks built on the company's own filings.** For a public company, research now reads its latest earnings release, 10-Q and 10-K for forward capex, buyback, dividend and credit-facility figures, each cited to the filing. Cash-floor slides anchor on the stated borrowing capacity. Set `MH_RESEARCH_GUIDANCE=0` to turn it off. Headline figures on the cover and two extra consistency checks are available as opt-in switches.
- **A coding agent built on Codex's own design.** Code chats send the request Codex CLI 0.171 sends: its prompt, its single command tool and its environment context, with Meshpool's permissions, sandbox, loop guards and compaction around it. On the code suites below it solved within one task of Codex (39 vs 38, 144 vs 145, 22 vs 22) at 0.71-0.79x Codex's cost per solved task. Set `MH_SMALL_REPO_STYLE=core` to use Meshpool's own lean prompt on small repositories instead.
- **Office files done the direct way.** In a Work chat, a change to an existing workbook, document or deck, or a new file you name ("make budget.xlsx"), is written with the file's own library, keeping formulas and styles. The deck, report and model engines are used for an open-ended deliverable ("a 10-slide pitch deck for investors"). Before answering, the agent is held to its own checks: a failed assertion, or a deck with a different number of slides than asked for, means the job is not done.
- **Measured against Codex 0.171.** Same model (deepseek-v4.1-flash), gateway and hour, two runs per task, cost per solved task as a multiple of Codex's (95% interval). Losses are listed with the wins.

  | Suite | Tasks x runs | Solved, Meshpool vs Codex | Cost per solved vs Codex (95% CI) | Median time vs Codex |
  |---|---|---|---|---|
  | SWE-bench Verified, SymPy (large repo) | 12 x 2 | 22 vs 22 | 0.76x (0.69-0.84) | 0.93x |
  | v2_dev code | 21 x 2 | 39 vs 38 | 0.79x (0.69-0.90) | 0.99x |
  | v2_eval + build, debug, deep, hard | 78 x 2 | 144 vs 145 | 0.71x (0.65-0.78) | 1.03x |
  | Work: DABench + v2_dev data tasks | 27 x 2 | 45 vs 44 | 0.51x (0.44-0.58) | 0.81x |
  | Office-7 (decks, documents, workbooks) | 7 x 2 | 13 vs 13 | 0.89x (0.66-1.15) | 0.50x |
  | SpreadsheetBench (public sample) | 6 x 2 | 10 vs 12 | 0.96x (0.59-1.95) | 1.46x |
  | OfficeBench (public sample) | 4 x 2 | 7 vs 8 | 0.80x (0.43-1.46) | 0.94x |

  Where Meshpool loses: SpreadsheetBench and OfficeBench solved fewer tasks than Codex (10 vs 12, 7 vs 8),
  SpreadsheetBench ran 1.46x slower at the median and v2_eval 3% slower. The office suites are small, so their
  intervals are wide. The Work row was measured on the build before the last office-file changes, which only act
  when a chat writes a Word, Excel or PowerPoint file.

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
