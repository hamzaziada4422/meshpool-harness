The first public beta of Meshpool: one desktop agent for code, documents, spreadsheets, decks and research.

**New in 0.0.7** (checkpoint build from the harness-router work; not yet merged into main)
- **Harness router.** Every chat is routed to a specialised harness: Chat, Debug, Build, Refactor, Review, Tests, Terminal, Explain, Research, Data, Documents, Browser or Automation. It decides in under a millisecond, with no model call unless unsure. The pick shows as a chip at the top of the chat; click it to switch.
- **Cheaper on every model we measured.** Same tasks, same model, same hour, graded by code: 35 to 45% less per solved task on DeepSeek V4.1 Flash, GPT-6 Luna, Claude Haiku 5.5, GLM 4.7, Meshpool Balanced and Kimi K3, with the same tasks solved.
- **Faster, shorter chat.** Skills load when needed instead of every skill going with every message (the Work prompt went from about 9,500 to 3,000 tokens). Answers lead with the result, and Meshpool asks questions only when a choice is really open.
- **Debugging that reproduces first.** When you name the failing command or test, Meshpool runs it before the model starts. Every edit to a code file comes back with an instant syntax and bug check (the newest Python on your computer, Ruff, node, JSON/TOML/YAML). A new diagnostics tool checks a whole project with tsc, cargo, go, javac and more.
- **Sturdier with any model.** A model that refuses a setting (for example Kimi and `temperature`) is adjusted and retried automatically. A spent budget or quota stops at once with a clear message instead of waiting on retries.
- Fixed: every chat in the sidebar read "NaN d ago".

**New in 0.0.6**
- The New chat button works again. Clicking it did nothing before; Ctrl+N and /new were not affected. If an earlier version saved the click as your folder, it is ignored, so updating fixes the home screen.
- Sub-agents for Meshpool's own chats: a chat hands self-contained parts of a job to sub-agents that work at the same time, each starting fresh, and report back. They use the chat's tools and permission mode, never read outside the project, and anything that would need your yes is refused (they cannot ask) unless you let them approve their own actions. They work inside the chat's turn, so Undo covers what they change and Stop stops them. Settings, Advanced: access, model, how many at once, steps, a time limit and levels.
- Decks research their facts: figures from SEC filings and the market data a valuation needs, with capital-allocation economics computed for you. Recommendations come with sized options, reviewers' changes apply to the whole deck in one pass, and a model that returns nothing usable fails the build instead of leaving a blank deck.
- Word, Excel and PowerPoint requests with no content, or sent to the wrong engine, are refused with a clear message instead of producing an empty file. Meshpool Work in other editors (over ACP) gets the same Office tools as the app.
- Meshpool Code shows the project lines a failing command points to, and rejects an edit that would change nothing.

- **Agents:** Meshpool Auto (picks per task: quality first, then cost and speed), Meshpool Code, Meshpool Work, Pi, Hermes, Codex and OpenCode, or add your own.
- **Runs on** your Meshpool API key, any OpenAI-compatible key, or your ChatGPT plan.
- **Office files** checked before they reach you: Word, Excel with live formulas, PowerPoint, PDF.
- **Decks** in a top strategy firm's style by default: the answer first in an executive summary, an agenda and section tracker, takeaway titles, a different exhibit on every page, Segoe UI and line icons. Add slides to your own deck in its look.

| Computer | Download |
|---|---|
| Mac with Apple silicon | Meshpool.dmg |
| Mac with Intel | Meshpool-Intel.dmg |
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

Apple silicon Macs need macOS 11 or later; Intel Macs macOS 10.15 Catalina or later (on 10.15, Pi, Codex, OpenCode and browser use need macOS 11). Meshpool is not yet signed by Apple or Microsoft, which is why your computer asks you to confirm it once. The pictures of the macOS and Windows prompts are illustrations; the wording on your computer may differ slightly.

This is a beta: when something breaks, click **Report this** next to the error, or the **Beta** badge.
