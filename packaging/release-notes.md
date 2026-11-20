The first public beta of Meshpool: one desktop agent for code, documents, spreadsheets, decks and research.

**Unreleased**
- **Decks with zero images.** Icons are native, editable vector shapes in your theme colours. Company logos are fetched from the internet and drawn as native shapes in their own brand colours. A deck with a picture fails its checks.
- **Nine new diagrams.** Risk matrix, 3x3 matrix, Gantt plan, issue tree, org chart, cycle, hub and spoke, pyramid and funnel, drawn natively and checked like every other slide.
- **Seven style packs** from 130 published decks, chosen with `--style` or in Settings > Company, and an optional title-band look.
- **Layout lookup.** Agents ask which layout proves a slide's message before writing it.
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

<img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/windows-smartscreen.png" alt="Click More info" width="300"> <img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/windows-smartscreen-run-anyway.png" alt="Click Run anyway" width="300">

<img src="https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/docs/setup/first-launch-key.png" alt="First launch: paste your key" width="420">

*First launch:* paste your Meshpool API key ([get one at meshpool.ai](https://meshpool.ai)) or sign in with ChatGPT, then choose the folder Meshpool may work in. Updates: click the "is available" bar, or Settings → About → **Check for updates**, then **Update now**.

Macs, Apple silicon and Intel, need macOS 11 or later (browser use needs macOS 13). Meshpool is not yet signed by Apple or Microsoft, which is why your computer asks you to confirm it once. The pictures of the macOS and Windows prompts are illustrations; the wording on your computer may differ slightly.

This is a beta: when something breaks, click **Report this** next to the error, or the **Beta** badge.
