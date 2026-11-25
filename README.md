<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="app/meshpool_harness/ui/brand/logo-dark.png">
    <img src="app/meshpool_harness/ui/brand/logo-light.png" alt="Meshpool" width="360">
  </picture>
</p>

<p align="center"><strong>One desktop agent for code, documents, spreadsheets, decks and research, on any model.</strong></p>

<p align="center">
  <a href="https://github.com/hamzaziada4422/meshpool-harness/releases"><img alt="Latest version (beta)" src="https://img.shields.io/github/v/release/hamzaziada4422/meshpool-harness?include_prereleases&label=version%20(beta)&color=1670E8"></a>
  <img alt="Mac, Windows and Linux app" src="https://img.shields.io/badge/app-Mac%20%7C%20Windows%20%7C%20Linux-16181D">
  <img alt="Command line on macOS, Linux and Windows" src="https://img.shields.io/badge/CLI-macOS%20%7C%20Linux%20%7C%20Windows-16181D">
</p>

Meshpool runs on your own computer. It does research, writing, analysis, spreadsheets, decks and code with any model on the Meshpool endpoint, your own OpenAI-compatible key, or the ChatGPT plan you already pay for.

> [!NOTE]
> **Meshpool is a beta (currently 0.0.10).** Some things will break. When one does, Meshpool shows what went wrong in the chat, and **Report this** (or the **Beta** badge) opens a GitHub issue with your version and system filled in. Your chats and files are never included.

## Why Meshpool

- **Every strong agent in one app.** Meshpool Code, Meshpool Work, Pi, Hermes, Codex and OpenCode, switchable per chat, with the same files, skills and connectors. Add any other agent that speaks the Agent Client Protocol in Settings.
- **Meshpool Auto picks the agent for you.** For each task it takes the best measured quality for that kind of work, then the lowest cost, then the fastest. It never trades quality for price: an agent is only a cheaper choice when it scores as well (see [ROUTING.md](docs/ROUTING.md)).
- **Any model on one key.** One Meshpool key reaches every model on the Meshpool endpoint. Any OpenAI-compatible endpoint works too, and so does signing in with your ChatGPT plan.
- **Office files that open clean.** Real Word, Excel (with live formulas) and PowerPoint. Every deck is checked against 20 design rules, every workbook against 13, and every file against the Office file schemas before it reaches you.
- **Local by design.** Your files stay on your computer, and connector sign-ins go straight from your computer to the app, with no Meshpool server in between.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/home-dark.png">
    <img src="docs/screenshots/home.png" alt="Meshpool: a new chat in Work mode, with starters for documents, spreadsheets, decks and research" width="900">
  </picture>
</p>

| You stay in control | Pick the agent, or let Auto pick |
|---|---|
| <img src="docs/screenshots/chat.png" alt="An approval card: Meshpool asks before writing a file and shows the change"> | <img src="docs/screenshots/agents.png" alt="The agent picker: Meshpool Auto, Meshpool Code, Codex, Meshpool Work and more, each with what it does best"> |
| Every change shows up as a diff you approve, or you let it run. Undo reverses any reply. | Each agent says what it does best. Meshpool Auto picks per task from the benchmarks. |

### Measured, not claimed

Each agent ran on the same model (DeepSeek V4.1 Flash), on the same tasks with the same graders, at the same time. The full tables are in [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

| Agent | Best at | Result |
|---|---|---|
| **Meshpool Code** | Coding in your project | Against Codex CLI 0.171 on the same model, gateway and hour (0.0.16): SWE-bench Verified (SymPy) 22 vs 22 solved at 0.76x Codex's cost per solved task; 144 vs 145 on 156 coding runs at 0.71x; 39 vs 38 on v2_dev at 0.79x. |
| **Meshpool Work** | Word, Excel, PowerPoint and PDF work, data tasks | Data tasks: 45 vs 44 solved at 0.51x Codex's cost per solved task. Office (0.0.16): Office-7 42 vs 41 at 0.94x; SpreadsheetBench 31 vs 35 at 0.90x; OfficeBench 21 vs 24 at 0.83x. Cheaper than Codex on every suite, but it still solves fewer SpreadsheetBench and OfficeBench tasks; see [BENCHMARKS.md](docs/BENCHMARKS.md). |
| **Meshpool's agent loop, browser use** | Web forms and web apps | MiniWoB++: 97 of 102 at $0.0027 and 17 s per solved task. In a same-time round, $0.0009 per solved task against $0.0025 for Codex CLI. |

## Install

| Computer | Download |
|---|---|
| Mac with Apple silicon | [Meshpool.dmg](https://github.com/hamzaziada4422/meshpool-harness/releases/latest/download/Meshpool.dmg) |
| Mac with Intel | [Meshpool-Intel.dmg](https://github.com/hamzaziada4422/meshpool-harness/releases/latest/download/Meshpool-Intel.dmg) |
| Windows 10 or 11 | [Meshpool-Setup.exe](https://github.com/hamzaziada4422/meshpool-harness/releases/latest/download/Meshpool-Setup.exe) |
| Linux, 64-bit Intel or AMD | [Meshpool-Linux-x64.tar.gz](https://github.com/hamzaziada4422/meshpool-harness/releases/latest/download/Meshpool-Linux-x64.tar.gz) (see [SETUP.md](docs/SETUP.md)) |

**Setup guide** ([full guide](https://github.com/hamzaziada4422/meshpool-harness/blob/main/docs/SETUP.md))

*Mac, quickest:* open Terminal, paste this and press Return. It installs Meshpool and opens it with no security prompts:

```sh
curl -fsSL https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/install.sh | bash -s -- --app
```

*Mac, from the download:*
1. Not sure which Mac you have? Apple menu → About This Mac: **Chip: Apple M…** takes Meshpool.dmg, **Intel** takes Meshpool-Intel.dmg.
2. Open the .dmg and drag **Meshpool** onto **Applications**.
   <img src="docs/setup/mac-dmg-drag.png" alt="Drag Meshpool onto Applications" width="420">

3. Double-click Meshpool in Applications. macOS blocks it the first time: click **Done**.
   <img src="docs/setup/mac-not-opened.png" alt="Meshpool Not Opened: click Done" width="260">

4. Open **System Settings → Privacy & Security**, scroll to **Security**, click **Open Anyway** and enter your password. Meshpool opens, and opens normally from then on. (macOS 14 and earlier: right-click Meshpool in Applications, choose **Open**, then **Open**.)
   <img src="docs/setup/mac-open-anyway.png" alt="Privacy and Security: click Open Anyway" width="520">


*Windows:* run Meshpool-Setup.exe. If "Windows protected your PC" appears, click **More info**, then **Run anyway**.

<img src="docs/setup/windows-smartscreen.png" alt="Click More info" width="300"> <img src="docs/setup/windows-smartscreen-run-anyway.png" alt="Click Run anyway" width="300">

<img src="docs/setup/first-launch-key.png" alt="First launch: paste your key" width="420">

*First launch:* paste your Meshpool API key ([get one at meshpool.ai](https://meshpool.ai)) or sign in with ChatGPT, then choose the folder Meshpool may work in. Updates: click the "is available" bar, or Settings → About → **Check for updates**, then **Update now**.

Macs, Apple silicon and Intel, need macOS 11 or later (browser use needs macOS 13). Meshpool is not yet signed by Apple or Microsoft, which is why your computer asks you to confirm it once. The pictures of the macOS and Windows prompts are illustrations; the wording on your computer may differ slightly.


**Command line** (macOS, Linux, Windows). One command installs it and runs setup; then type `meshpool` in any project folder, or `meshpool ui` for the full app in your browser. The terminal install does not include LibreOffice, so decks are not rendered for review and workbook formulas are not recalculated; the desktop app has both.

```sh
curl -fsSL https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/hamzaziada4422/meshpool-harness/main/install.ps1 | iex
```

## What it does

- **Work and Code modes.** Work is a chat with a live progress panel and the files the task made. Code adds diff, terminal, Git, pull request and CI panels.
- **Agents:** Meshpool Auto, Meshpool Code, Meshpool Work, Pi, Hermes, Codex and OpenCode, or your own, on your Meshpool key, any OpenAI-compatible key, or your ChatGPT plan ([providers](docs/PROVIDERS.md)).
- **Files in and out:** PDF, Word, Excel, PowerPoint, Power BI (the model schema; the data model needs `pip install pbixray`), CSV and images in; Word, Excel, PowerPoint and PDF out. Reading text in scanned PDFs and pictures (OCR) is optional and not included: run `pip install rapidocr onnxruntime` (macOS 13 or later, Windows, Linux) in the Python Meshpool uses; without it you get the file's own text only.
- **Web research** across many sources, with links you can check, and **browser use** that works out of the box (it opens pages, reads the elements, clicks and types; Playwright and Chromium set themselves up on first use).
- **58 connectors** (GitHub, Linear, Jira, Slack, Notion, Google Drive, HubSpot, Stripe and more) plus any remote MCP server. Sign-in goes straight from your computer to the app.
- **About 100 skills**, and the skills, MCP servers, memories and instructions you already have in Claude Code, Codex, Cursor, OpenClaw, Hermes and others, imported in a click with a preview (`meshpool import agents --dry-run`). Logins are never read.
- **Memory you can see.** One page lists everything Meshpool knows, where each fact came from and where it was used. Edit or remove any of it; incognito chats keep nothing.
- **Quiet suggestions.** At most one short nudge at a time, only when a chat ends or you open the app, and always with what Meshpool saw. Switch it off in Settings.
- **Receipts.** Time, cost where known, the agent chosen and the checks that ran, under each reply.
- **Scheduled tasks and parallel sub-agents.**
- **Isolated copies:** a Code chat, a terminal session (`meshpool -w`), sub-agents that edit at the same time and each scheduled run on a git folder can work in a copy of their own (a git worktree, or a plain copy without git), so they never edit your working copy. You review the diff, then merge or discard ([worktrees](docs/setup/worktrees.md)).
- **meshpool in Excel, PowerPoint and Word** (new, not yet checked in real Office): a panel on the Home tab that answers about your selection and inserts the answer ([setup](docs/setup/office-add-in.md)).
- **You stay in control:** Enter adds to the task, Interrupt redirects it, Esc stops, and Undo reverses any reply.
- **For scripts and your own setup:** one-shot runs with JSON output ([terminal](docs/setup/terminal.md)), [hooks](docs/setup/hooks.md), [skills](docs/setup/skills.md) and [sub-agents](docs/setup/sub-agents.md).

## For developers

```sh
cd app
pip install -r requirements.txt
python -m meshpool_harness ui
```

`app/` holds the agent, the desktop UI, the office engines and the tests; `packaging/` the installers and launchers; `testkit/` the mock model and end-to-end checks; `docs/` [benchmarks](docs/BENCHMARKS.md), [routing](docs/ROUTING.md), [building](docs/BUILDING.md) and [releasing](docs/RELEASING.md).

## Credits

Meshpool bundles third-party open-source software, each under its own license: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The Meshpool name and logo belong to Meshpool.
