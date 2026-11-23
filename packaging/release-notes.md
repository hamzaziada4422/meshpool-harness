The first public beta of Meshpool: one desktop agent for code, documents, spreadsheets, decks and research.

**New in 0.0.11**
- **A second pair of eyes on big jobs.** Before Meshpool tells you a big task is done, a separate reviewer that did not do the work checks it against what you asked for, in code, documents, decks, spreadsheets and research. If something is missing it goes back to be fixed, and the reply ends with a short "Independent check" in plain words. It is on by default, only runs on big tasks, and you can turn it off in Settings or check any reply yourself with "Check this" (or `/grade` in the terminal).

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
- **Mac with Intel** now needs macOS 11 or later. Codex and OpenCode are no longer bundled, so ChatGPT sign-in is not offered in the app.

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

LibreOffice, Chromium, Node.js, Git and Python ship inside the folder; it uses the desktop libraries every Linux desktop has. To update, unpack the new download over the old folder. Its checksum is in Meshpool-Linux-x64.tar.gz.sha256.

<img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/windows-smartscreen.png" alt="Click More info" width="300"> <img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/windows-smartscreen-run-anyway.png" alt="Click Run anyway" width="300">

<img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/first-launch-key.png" alt="First launch: paste your key" width="420">

*First launch:* paste your Meshpool API key ([get one at meshpool.ai](https://meshpool.ai)) or sign in with ChatGPT, then choose the folder Meshpool may work in. Updates: click the "is available" bar, or Settings → About → **Check for updates**, then **Update now**.

Macs, Apple silicon and Intel, need macOS 11 or later (browser use needs macOS 13). Meshpool is not yet signed by Apple or Microsoft, which is why your computer asks you to confirm it once. The pictures of the macOS and Windows prompts are illustrations; the wording on your computer may differ slightly.

This is a beta: when something breaks, click **Report this** next to the error, or the **Beta** badge.
