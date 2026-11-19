# MeshPool chat UX: audit, jitter measurements, target design, phased plan

Owner direction: chat quality better and easier, not technical; UI/UX jitter is the pressing issue; usability as good
as Claude (chat and cowork in one, simple, few moving parts). Normal chats need no folder. A Project has its own
memory, instructions and context files. Build MeshPool the same way.

Status: plan only, no app code changed. Everything below was measured on the real app (headless Chromium, 1440x900)
against the mock model (`testkit/mock_llm_long.py`, a thin wrapper around `mock_llm.py` that adds a `stream long`
rule: three tool calls, then a ~700-word Markdown answer streamed word by word at 20 ms/word).

Artifacts of this audit:

| File | What |
|---|---|
| `testkit/ux_jitter_audit.js` | The jitter regression script (section 2.4). Prints CLS, long tasks, mutations per frame, scroll jumps, rebuilds. `--strict` exits 1 on any FAIL. |
| `testkit/mock_llm_long.py` | Mock model with the long streaming rule. |
| `testkit/ux_audit_shots.js` | Takes the screenshots below. |
| `docs/screenshots/ux-audit-*.png` | 16 desktop screenshots (+ onboarding is covered by `ui_crawl.js`). |
| `docs/plan/ux_jitter_baseline.{json,txt}` | Baseline numbers quoted in section 2. |

---

## 1. Current state

### 1.1 How the app is organised today

Local web UI served by `app/meshpool_harness/ui/server.py` (HTTP, token in page), one vanilla-JS file
`ui/app.js` (about 2,600 lines), `ui/app.css`, a tiny `index.html`. The browser polls; there is no push channel.

**Two modes, chosen by the user, before they type.** The sidebar top has a `Work | Code` switch
(`shell()` in app.js, `setAppMode`, `S.appMode`). The mode decides:
- which agent ("harness") a new chat uses: `meshpool-work` (documents, research) or `meshpool-code` (code)
  (`modeHarness`, `harnesses/__init__.py` BUILTIN);
- which chats the sidebar shows (`renderSide` filters by `t.kind`), so a chat started in Work disappears when you
  switch to Code;
- the hero text and starter chips (`renderHero`), the composer placeholder (`renderBar`) and the right-hand
  panel tabs (`PANELS`: Work gets Progress/Files; Code gets Diff/Files/Terminal/Git and CI/Preview).

There is a third thing called Meshpool Auto (`harnesses/__init__.py` AUTO, `harness_router.py`, `hrouter/`): it picks
the best agent for the task text from a benchmark table, with no model call. It already exists and works, but it
is hidden at the top of the "Agent" dropdown and is not the default. `agent_api.create()` (line ~907) also runs
`hrouter.route()` to pick a specialised "spec" (docs, spreadsheets, ...) for every new chat.

**Folders are mandatory.** `agent_api.Session.__init__` refuses to start without a working folder
("Project folder not found"). `create()` falls back to `~/Meshpool Projects` when none is chosen. Everything the
agent makes lands in that folder. Attachments are written into it too (`workbench.attach`). A chat belongs to
exactly one folder forever (`session.json: workdir`).

**"Projects" in the sidebar are folders** (`workbench.projects()` reads `<home>/projects.json`:
`{path, name, added}`). The sidebar groups chats by folder (`renderSide`), the top bar shows a folder chip
(`renderTop` `#projChip`), the hero says "...done for you in `Meshpool Projects`" with a link to change it
(`renderHero`, `projectPop`). A folder can also be a managed GitHub clone (`workbench.github_workspace`,
`<home>/workspaces/`), surfaced as "New session from GitHub".

**Sessions (chats).** Stored under `runs/sessions/<id>/` (`agent/core.py: SESSIONS`): `session.json` (messages,
events, workdir, harness, kind), `meta.json` (title, pinned, archived, kind, mode, model, goal, plan, route),
`checkpoints/`. The sidebar list is `agent_api.listing()`; live state is in memory (`LIVE`). A chat has its own
permission mode (Supervised / Auto-accept edits / Full access), plan mode, goal, model and harness, all changeable
from composer pills.

**Memory.** `assistant.py`, files in `<home>/memory/`: `instructions.md` (one global block), `memory.md` (global
facts, one per line), `projects/<sha1 of folder path>.md` (per-folder notes, written by the `project_note` tool),
`settings.json` (two on/off switches). `context_block()` is injected, hidden, into the first message of every new
chat: date, honesty and style rules, global instructions, recalled facts, the folder's notes. Edited in
Settings > "Instructions and memory" as two raw text boxes.

**Settings** is a modal with 17 sections (`SECTIONS`, app.js ~1748): Account and key, Appearance and shortcuts,
Models, Instructions and memory, Company and brand, Import chats and context, Scheduled tasks, Web search,
Connectors, Skills, Plugins, Agents, Tools, Permissions, Usage, Terminal command, Advanced, About.

**Composer** (`renderBar`): pills for Agent, Model, Access mode, Plan, an attach button, then Send. Slash commands
(`/model /agent /plan /goal /usage /review /qa /diff ...`) and `$skill` mentions.

**Session panel** (`spHTML` and `workbench.session_panel`): Environment (folder or GitHub repo), branch switcher,
context-window bar, connectors, outputs, changes, PR card, background tasks. Already close to what a linked repo
needs. Plus a full Pull Requests view (`openPRView`).

### 1.2 Screens (see `docs/screenshots/ux-audit-*.png`)

| Screenshot | What it shows |
|---|---|
| `01-home-work`, `02-home-code` | Two different homes. "What should we get done?" / "What are we building?", starter chips, a folder link (`Meshpool Projects`). |
| `03-agent-picker` | "Agent" dropdown: Automatic, For work, Other agents (Hermes, Pi, Codex ...), with benchmark prose. |
| `04-model-picker` | Provider tabs ("127.0.0.1" / "ChatGPT set up"), model ids and duplicates, star favourites, "Providers and keys". |
| `05-access-picker` | Supervised / Auto-accept edits / Full access. |
| `06-folder-picker` | Absolute paths, "New session from GitHub", "Choose a folder", "Use my whole home folder". |
| `07-chat-open`, `08-side-panel` | A streaming reply (raw Markdown visible while it streams), Session / Progress / Files tabs. |
| `09`..`15` | Settings pages: Account and key, Models, Instructions and memory, Agents, Tools, Permissions, Advanced. |
| `16-code-chat-git-panel` | Code chat with the Git and CI panel: branch select, commit box, open-PR form. |

### 1.3 Everything technical or confusing for a non-developer

Grouped; file references are where the text or concept lives today.

**A. Choices the user must make before saying anything**
1. `Work | Code` switch: two modes, two homes, two sidebars (app.js `shell`, `setAppMode`). A person asking "help me
   write an email" and later "fix this script" has to know which to pick; a chat disappears from view when the
   other mode is selected.
2. "Meshpool Work", "Meshpool Code", "Meshpool Auto", "Hermes", "Pi", "Codex", "OpenCode" in an **Agent** pill
   (`harnessPop`). "Agent" and "harness" are internal terms; descriptions quote SWE-bench scores and "$0.008 each"
   (`harnesses/__init__.py` BUILTIN descriptions). Auto, the right default, is the least visible entry.
3. **Model** pill with provider tabs, raw ids (`deepseek-v4.1-flash` next to `deepseek/deepseek-v4.1-flash`),
   "127.0.0.1" as a provider name, and silent side effects: picking a model the current agent cannot run
   **starts a new chat** with a toast (`pickModel`, `harnessPop`: "Each chat keeps its agent. Started a new chat").
4. **Access pill**: "Supervised / Auto-accept edits / Full access" (`MODES`). A permission model, not a choice a
   writer understands; combined with a **Plan** pill (plan mode) it is four permission-ish toggles on one row.
5. A **folder** is required and its absolute path is displayed (`projectPop`, hero link, top-bar chip). For a
   normal conversation "which folder?" has no answer. "Use my whole home folder" is offered as a button.

**B. Jargon in the product surface**
6. "Harness", "agent", "MCP", "Tools" (Settings > Tools means MCP servers), "Skills", "Plugins", "Connectors",
   "Terminal command", "Context 82%", "N in, 82% cached" tokens in the title bar (`renderTop` usage chip),
   "Context" progress bar in the session panel with "(window estimated)", "Checkpoints", "Epoch" (internal).
7. Onboarding asks for "Meshpool key / another OpenAI-compatible key / ChatGPT plan" before the first message
   (`showOnboarding`), then a permissions step and an import step: three steps before any value.
8. Settings > Account and key: "Base URL ... OpenAI-compatible endpoint, ending in /v1", "Runs on". Settings >
   Agents: "Default for Work / Default for Code", benchmark claims. Settings > Models has its own list again.
9. 17 settings sections; several overlap (Models vs Account default model vs the Model pill; Agents vs Agent
   pill; Tools vs Connectors vs Plugins vs Skills; Permissions vs the Access pill vs Advanced).

**C. Projects, memory and instructions do not match what people expect**
10. Memory and instructions are **global only** from the user's view: two text boxes in Settings. The per-folder
    memory exists (`remember_project`) but is invisible: no UI, keyed by a hash of the path.
11. A "project" cannot have instructions, files or its own memory the user can see. You cannot start a chat
    "in a project" without it being a folder on disk. No way to say "use these 3 documents for every chat here".
12. Attaching a file silently copies it **into the project folder** and tells the agent "Attached files (in the
    project folder)" (`sendNow`, `workbench.attach`). With the default `~/Meshpool Projects` folder, all attachments
    from all chats pile into one directory and the agent in chat B can see chat A's files.
13. Outputs: files the agent made are in the folder or in a "Files" tab; the main chat does not show them as
    result cards ("Artifacts") the way Claude does.

**D. In-chat behaviour that exposes internals**
14. Steps list, "Working / 3 steps", raw tool names, approval cards ("Run this command?"), plan mode "Build this
    plan", "Added while working" tags, `/goal`, "Meshpool Auto picked Hermes. Quality first, then cost" notes
    (`applyEv` status/route events), a "Harness: ... (reason)" route chip. Useful for a developer, noise otherwise.
15. While streaming, the reply shows **raw Markdown** (`**bold**`, `###`) and only becomes formatted when finished
    (see jitter, section 2). Looks broken to a normal user.
16. Two-line hint under the box ("Enter adds to the task - Esc stops") appears and disappears, moving the composer.
17. Right panel has different tab sets per mode (`PANELS`) and different names for the same idea: "Files" is
    both `outputs` (Work) and `files` (Code); "Session" panel mixes environment, context, connectors, git.

**E. Top bar and sidebar**
18. Beta pill, "Projects" header with a pull-request icon and a plus whose meaning differs (add folder vs GitHub
    session), folder rows with chevrons that both open a chat and collapse, per-thread dots, "Show archived".
    Title bar: title, folder chip, branch chip (shown in Code mode), CI chip, usage chip, panel buttons, kebab.

**Five most confusing, in order:** (1) Work/Code switch plus Agent pill when Auto already exists, (2) a folder is
required and shown as an absolute path, (3) Model + Access + Plan pills with side effects (new chat started
silently), (4) "Projects" are folders: no instructions, no files, no visible per-project memory, (5) the streaming
reply is raw Markdown that jumps when finished.

---

## 2. Jitter: measurements and causes

### 2.1 Method

`testkit/ux_jitter_audit.js` (Playwright, Chromium, no options, 1440x900) injects, before page load:
- `PerformanceObserver` for `layout-shift` (CLS) and `longtask`;
- a `requestAnimationFrame` sampler recording per frame: DOM mutation count (via a `MutationObserver` on
  `document.body`), `#conv` scrollTop/scrollHeight/distance-from-bottom, number of `#col` children, and the
  bounding boxes of `#composer`, `#topbar`, `#sideList`, `.side`, `#conv`, `#col` (x,width), `#panel`;
- counters for removals of several messages at once from `#col` (list rebuild), moves of the `#working` indicator,
  re-writes of the live `.msg-ai`, and rebuilds of the header/composer-bar/sidebar/panel regions;
- request counters for `/api/agent/<id>?since=` (thread poll) and `/api/agent` (list).

Scenarios: (a) open the app; (b) new chat, send `stream long please`, stream to completion (3 tool calls, then
~700 words of Markdown with headings, a list, a table, a code block); (c) 8 switches among 4 existing chats;
(d) hide/show sidebar, open/close Progress, Files and Session panels, open Settings, open Model and Agent pickers.
Full-access mode is used so no approval card stops the run.

### 2.2 Baseline numbers (`docs/plan/ux_jitter_baseline.txt`)

| Scenario | CLS | Long tasks | What moved |
|---|---|---|---|
| (a) open | 0.0001 | none | Nothing worth fixing. (`#betaBtn` shifts once.) |
| (b) stream, 23.8 s | **0.002** (source: `div.msg-ai`, at the end) | none | 1 backward scroll step of **-72 px** and a transcript shrink of -72 px at completion; one **+208..254 px** forward scroll jump inside one 400 ms poll; growth events only **1.5 per second** (median 23 px each), i.e. the text arrives in lumps; max **119-126 DOM mutations in one frame**; 959 rewrites of the live message node; `#working` moved 8-10 times; composer geometry changed twice (hint line); Progress panel rebuilt **167 times**; header 5 and composer-bar 5 rebuilds; **54 thread polls in 23.5 s = 2.3 per second**. |
| (c) 8 chat switches | 0 | none | Message list rebuilt 8 times (expected) with one burst of **3,037 mutations in one frame**; header and composer bar rebuilt 16 times each (2 per switch), side panel 48 times (6 per switch); 1 blank frame per switch; the whole history is re-fetched each time. |
| (d) sidebar and panels | 0 | none | Each panel open/close reflows the whole transcript and shifts scroll by **-46 px** (4 of 4 opens) because the reading position is not anchored; column width changes in 2 steps. |

Honest reading: classic CLS is already low (the earlier work on scrollbar gutters, sidebar diffing and
`jitter.js` paid off), and nothing blocks the main thread. The "jittery" feeling is the combination of
**lumpy streaming (2.3 updates/s), a layout change when the reply finishes, scroll shifts, and regions rebuilt
many times**. A real model emits much smaller, faster deltas than the mock, which makes the polling lumps more
noticeable, not less. The numbers above are the baseline each phase must improve.

### 2.3 Causes, with file:line and fix

Line numbers are from `app/meshpool_harness/ui/app.js` at the time of the audit (another agent is editing the
file; search for the quoted text if lines drift).

| # | Cause | Where | Measured | Fix |
|---|---|---|---|---|
| J1 | **Streaming is polled, not pushed.** Active chat is polled every 400 ms; every poll returns a batch of `text_delta` events applied in one task. Text appears in 2-3 lumps per second. | `poll()`: `setTimeout(poll, busy ? 400 : ...)` app.js:1079; server `agent_api.view()` returns events since an index | 2.3 polls/s, growth 1.5 events/s | Add a Server-Sent Events (or chunked) endpoint `GET /api/agent/<id>/events?since=` in `server.py`; client appends events as they arrive and **drains text through a rAF-paced buffer** (a few characters per frame, accelerate when behind). Keep polling as the fallback and for background chats. |
| J2 | **Raw text is replaced by rendered Markdown when the reply ends.** While streaming the node is `white-space: pre-wrap` raw text; at `assistant` the whole node's `innerHTML` is swapped for `md(txt)` (headings get margins, tables, list indentation, `**` removed). Height changes, text re-wraps, scroll clamps back. | `case "text_delta"` app.js:1158-1160 (`cur.live.textContent = cur.liveText`), `case "assistant"` app.js:1164 (`cur.live.innerHTML = linkFiles(md(txt))`) | -72 px shrink, -72 px backward scroll, CLS source `div.msg-ai` | Render Markdown **incrementally while streaming**: keep a block-level parser (split on blank lines/fences); finished blocks are rendered to HTML once and never touched again, only the last open block is re-rendered (as Markdown, so `**` disappears immediately). At `assistant`, do not replace; just mark done. Use the same renderer for history so the stream and the finished chat look identical. |
| J3 | **`textContent = whole text` on every delta** is O(n) per event and O(n^2) per reply, and replaces the text node each time (959 mutations in 23 s). | app.js:1160 | 959 live rewrites; 126 mutations in one frame | Append to a text node (`appendData`) or, after J2, re-render only the open block, once per frame. |
| J4 | **Scroll-to-bottom is done per poll, with a fixed 200 px "stick" threshold, not per layout change.** Content that grows between polls is not followed smoothly, and layout changes outside polls (panel open, image load, details toggle) leave the reader displaced. | `scrollConv` app.js:1082; `poll()` computes `stick` before applying events | +208..254 px jump; -46 px per panel open | One `ResizeObserver` on `#col`: if "pinned" (user within ~80 px of bottom, tracked from `scroll` events with `{passive:true}`), set `scrollTop` in the same frame; otherwise preserve the anchor (`overflow-anchor: auto` on `.conv`, plus `scroll-anchor` via `anchor` element when the panel opens). Show a "Jump to latest" pill when not pinned. |
| J5 | **Panel is rebuilt wholesale on every poll that has events** while Progress is open (cowork opens it by default at >= 1400 px: `togglePanel("progress", true)` in `sendNow`). | `poll()` app.js:1066 `renderPanel()`; `panelProgress` rebuilds `innerHTML` | **167 rebuilds** in one reply | Patch rows by key (`ev.id`) or re-render at most once per 500 ms and only when the step list changed. |
| J6 | **Header and composer bar are rebuilt (`innerHTML`) on every state change**, twice per chat switch, destroying hover/focus state and flashing pills. | `renderTop` app.js:546, `renderBar` app.js:633, called from `poll()` on `first || state/goal/title change`, `openThread`, `newThread` | 5+5 per reply, 16+16 per 8 switches | Render the skeleton once; update text/attributes of the existing nodes; make `renderBar` a pure "apply state" function. |
| J7 | **Layout depends on run state.** The hint line under the composer (`#hintline`) is empty when idle and has text while busy, so the composer (bottom-anchored) moves up and down; the Send button changes width (Stop + Interrupt + Send). | `renderBar` app.js:658 | composer geometry changes 2 per run | Reserve the hint line height always (`min-height`) or move the hint into the box; fixed-size button slots. |
| J8 | **"Working" indicator is detached and re-appended to the end of `#col` on every poll** (so it jumps below each new block) and is removed when text starts, then re-added. | `poll()` ~app.js:1068-1074 (`$("#col").appendChild(wk)`), `sendNow` app.js:714 | 8-10 moves per reply | One persistent status slot inside the composer area or a fixed-height row after the transcript; update its text in place. |
| J9 | **Each new child of `#col` animates in** (`translateY(6px/12px)`, spring overshoot on user messages). During a tool-heavy turn that is a stream of small movements stacked on top of the autoscroll. | app.js:2524 `MutationObserver` adding `.enter`; app.css:621-622, `mp-in`/`mp-rise` (`--spring` overshoots) | not counted by CLS (transforms) but visible | Opacity-only fade for assistant blocks; no translate/overshoot inside the transcript; keep motion for popovers. |
| J10 | **Switching chats re-fetches the whole history and re-applies it synchronously**, clears `#col`, then renders top/bar twice; no cache, no virtualisation. | `openThread` app.js:1026 (`$("#col").innerHTML = ""`, `poll()` with `since=0`), `applyEv` per event | 3,037 mutations in one frame; 1 blank frame | Keep the last N transcripts' DOM or event arrays in memory and swap by `display`; build with a `DocumentFragment`; `content-visibility: auto; contain-intrinsic-size` on messages older than the viewport; fetch only deltas. |
| J11 | **Fonts swap and header logo load late.** Seven `@font-face` with `font-display: swap`, none preloaded, so text reflows when Plex/Bricolage arrive. Logo `<img class="mlogo">` has no width/height attributes. | app.css:1-7; index.html (no `<link rel="preload">`); `shell()` `<img class="mlogo">` | CLS on open only 0.0001 on a local server (fast), will be larger on a slow disk/webview | `<link rel="preload" as="font" crossorigin>` for the 3 faces used above the fold; `size-adjust` fallbacks; width/height on logo. |
| J12 | **Polling that rebuilds lists.** `loadThreads()` every 4 s while any chat works; `renderSide` has a good shape-diff (rewrite only when structure changes) but still runs the full string build and diff each time. Background chats poll too. | app.js:2551; `renderSide` app.js:336; server `agent_api.listing()` | `sideList` rebuilt 1-2 times per reply (acceptable) | Keep; move to the same SSE channel (one stream for list state changes), and build rows incrementally. Low priority. |
| J13 | **Panel open/close and sidebar toggle change `#conv` width instantly**, re-wrapping the entire transcript in one frame. | `togglePanel` app.js:1343; app.css `.panel` | -46 px scroll shift each | Animate the panel as an overlay on narrow widths; on desktop anchor the first visible block (J4) and transition width over 150 ms with `contain: layout` on messages. |

Not causes (checked): whole-list `innerHTML` re-render per token does **not** exist (events are applied
incrementally, 0 list rebuilds while streaming); sidebar does not rebuild per poll; scrollbar gutter is already
reserved; images in the transcript are rare. The older `testkit/jitter.js` keeps guarding those.

### 2.4 The regression script

```
python3 testkit/mock_llm_long.py 8911 /tmp/mock.jsonl &
# app home with MESHPOOL_BASE_URL=http://127.0.0.1:8911/v1 and MESHPOOL_API_KEY=test-key-1234 (see run_all.sh)
node testkit/ux_jitter_audit.js 8799 /path/to/project [--json out.json] [--strict]
```
Output per scenario: CLS and top shifting elements, long tasks (n/total/max), mutations per frame (mean/max),
scroll jumps (forward > 150 px, any backward step, transcript shrinks), max lag behind the bottom, growth
events per second, list rebuilds, `#working` moves, live rewrites, region rebuild counts, landmark geometry
changes, blank frames per chat switch, requests per second. `THRESHOLDS` at the top of the file holds the target
numbers used for PASS/FAIL. At baseline 13 of 19 checks pass; the six failures are J1/J2/J4/J7 in the stream
scenario and are the first phase's goal.

---

## 3. Target design (modeled on Claude: chat + projects)

### 3.1 Principles
1. One composer, one kind of conversation. No Work/Code switch, no agent picker. MeshPool decides how much machinery
   a request needs (Auto) and says so in plain words only when it matters.
2. A conversation never needs a folder. Folders and repos are an optional property of a **Project**.
3. Projects hold what Claude Projects hold: **name, instructions, knowledge files, memory**, plus (MeshPool
   extra) an optional **linked folder or GitHub repo**.
4. Technical controls exist but live behind one small "..." or in Settings > Advanced.
5. Results are shown as **artifacts** (files, documents, decks, diffs), not as tool logs.

### 3.2 Screen model

```
Sidebar                      Main
  [New chat]  [search]         Title (editable)            [Share-less: Artifacts] [...]
  Chats                        --------------------------------------------------------
    Recent (no project)        conversation
  Projects                     --------------------------------------------------------
    Q3 report  (3)            [ + attach ]  Message MeshPool...        [model chip]  [send]
    shop-site  (repo)           (shows "Project: Q3 report" chip when in a project)
  Settings (profile menu)
```
- **Home** (no chat open): greeting, composer, 4 starters. In a Project: the project page (below).
- **Composer**: text, attach (paperclip, drag, paste; files and images), one small model chip (default "Auto /
  MeshPool default"; click opens a short list: Auto, then 3-4 curated models, "More models" in Settings), send/stop.
  Everything else (access mode, plan, goal, agent) moves to the chat's "..." menu under "Advanced for this chat".
- **Auto routing**: first message in a chat runs `hrouter.route()` + `harness_router.route()` (already no-model
  and instant). Result chooses a tool profile:
  - `chat` (new): no folder tools; web search/fetch, memory, document/deck/sheet makers writing into the chat's
    own outputs folder, no shell. Fast path for plain questions.
  - `work` (`meshpool-work`): documents, research, data, when files or office output are involved.
  - `code` (`meshpool-code`/Pi/Codex): when the project has a linked folder/repo, or the text is a coding task.
  The profile can **escalate mid-chat** (a plain chat that becomes "now fix this script" switches profile on the
  next turn, and a small note "Using code tools" appears once). The route chip becomes an unobtrusive "Used: code
  tools" line under the answer with "Why?"; no benchmark text.
- **Artifacts panel** (replaces Progress/Files/Outputs/Diff tabs for the default view): a right panel listing
  what the chat produced (documents, spreadsheets, decks, PDFs, images, code files, a "Changes" card for repo
  work). Click opens a preview; the chat shows a card for each artifact inline. Steps stay available as a
  collapsed "Show steps" disclosure under each answer (exists: `.work` groups, `toggleSteps`).
- **Project page** (click a project): name, description, **Instructions** (editable box), **Knowledge** (file
  list with add/remove, drag and drop), **Memory** (readable list of facts, each deletable; "Project memory is
  separate from your global memory"), optional **Linked folder / GitHub repo** with branch, and the project's
  chats below with a composer at the bottom ("Start a chat in this project").
- **Linked repo/folder session view**: when the chat is in a project with a link, the right panel shows the
  existing session panel (`spHTML`: repo, branch, changes, PR, CI, Terminal, Preview) as tabs "Changes / Files /
  Terminal / Preview". Those code tools appear only for such chats; they are not part of the default UI.

### 3.3 Features, what is reusable, what changes

| Feature | Exists today (reuse) | Must change |
|---|---|---|
| **Chat without a folder** | Session machinery (`agent_api.Session`, events, resume, checkpoints), `Agent` profiles (`agent/core.py`), `default_project()` fallback | Allow `workdir=None`: create a private per-chat scratch dir `<home>/chats/<id>/outputs/` as the workdir (so file-making tools, checkpoints and outputs keep working unchanged). Remove "Project folder not found" gate for the no-folder case (`Session.__init__`, `create()`). Add a `chat` tool profile. Do not show the scratch path anywhere. |
| **Auto routing** | `hrouter/` (spec per task), `harness_router.py` (best harness), AUTO harness, `agent_api.create()` already routes | Make Auto the only visible path; `kind` derived from route result, not from a tab; allow per-turn re-route; hide benchmark text. Keep manual override in the chat "..." menu and Settings > Advanced. |
| **Projects (name, instructions, knowledge, memory)** | `workbench.projects()` list and `projects.json`; `assistant.project_memory/remember_project` (memory per project already supported, keyed by folder); `context_block()` already injects project notes | New `projects` store (3.4). UI: project page. `context_block()` gains `project_id`: inject project instructions, relevant knowledge excerpts, project memory. Knowledge retrieval: reuse `docread.py` (text extraction, OCR in `ocr.py`) + the `recall()` term matcher in `assistant.py` for relevance; small files go in whole, large ones are chunked and the top chunks are attached. |
| **Start a chat inside/outside a project** | Sidebar `+` per folder (`data-newin`), `newThread(project)` | "New chat" = outside; project page composer and a `+` on the project row = inside. `create()` gets `project_id` instead of required `workdir`. |
| **One composer with attachments** | `addAttachments`, drag/drop, paste, images to the model (`/api/agent/.../send images`), `/api/attach` | Attachments go to the chat's own `attachments/` folder (not the project folder). Images and PDFs shown inline in the message. Option "Add to project knowledge" on a file chip when in a project. |
| **Code against a folder or GitHub repo** | `workbench.github_workspace`, `github_repos/branches`, `git_status`, `session_panel`, PR view, CI chips, checkpoints/undo, diff panel | A project can carry `link: {type: folder|github, path|repo, branch}`. Chats in it get `workdir = link path` and the code profile + code panels. "New session from GitHub" becomes "Link a GitHub repository" on the project page (same API). Chats outside a project can still be pointed at a folder from the chat's "..." menu ("Work in a folder") for the occasional case. |
| **Artifacts / outputs** | `workbench.session_outputs`, `panelOutputs`, Files panel, `make_document/make_deck/make_spreadsheet` tools, file preview (`/raw`, `openFile`), `linkFiles` in messages | One Artifacts panel for all chats; inline artifact cards from `tool_end` of the make_* tools and from `write_file` in outputs; diff/branch card only for linked repos. |
| **Memory** | Global `memory.md`, `memory_save` and `project_note` tools, `forget`, `recall` | Global memory stays ("About you" in Settings with a readable list). Per-project memory file lives in the project folder of the data dir and is shown on the project page. The model's `project_note` tool writes to the **chat's project** (not a folder hash). |
| **Instructions** | Global `instructions.md` | Global instructions stay in Settings > Personal; per-project instructions added. Order injected: global then project (project wins on conflict, said in the prompt). |
| **Settings reduced** | All 17 sections exist and work | Target 6 sections: **General** (appearance, shortcuts, update), **Account and models** (provider, key, default model, one model list, ChatGPT sign-in), **Personalisation** (global instructions, memory list, import chats), **Connectors and tools** (Connectors, MCP, Skills, Plugins as tabs inside), **Privacy and permissions** (macOS permissions, default access mode, web search), **Advanced** (agents/harnesses, terminal command, usage, scheduled tasks moved to the sidebar "Scheduled" item, company brand, about). Pure re-grouping: `renderSection` code is reused. |

### 3.4 Data model (on disk)

Existing layout stays; additions are new files only. Home = `C.home()` (the Meshpool data folder).

```
<home>/
  projects.json                 existing: [{path,name,added}] folder list. Kept untouched (rollback + backup).
  memory/memory.md              existing global facts
  memory/instructions.md        existing global instructions
  memory/projects/<sha1>.md     existing per-folder notes. Kept; read by migration.
  runs/sessions/<id>/session.json, meta.json, checkpoints/   existing chats
  chats/<id>/outputs/  attachments/      NEW: scratch workdir for chats without a folder or project link
  projects/                              NEW
    <pid>/project.json        {id, name, description, created, updated, link: null | {type:"folder",path} |
                               {type:"github",repo:"owner/name",branch:"main"}, legacy_path: "<old folder path>"}
    <pid>/instructions.md     project instructions (<= 8,000 chars like the global one)
    <pid>/memory.md           project memory, one fact per line (same format and caps as memory.md)
    <pid>/knowledge/          the user's files (copied in; originals untouched)
    <pid>/knowledge/.index.json   per-file {name, size, hash, text_chars, chunks:[{o,len}]} extracted text cache
```
- `<pid>` = `p_` + 10 random hex chars. Project names are free text and need not be unique.
- Chat to project: add `project_id` (string or absent) to `meta.json`. `listing()` already merges meta over
  session rows, so it is one more field; the sidebar groups by `project_id`, then "Chats".
- Single-writer rules and atomic writes: reuse `C.replace_file` and the lock pattern in `workbench._projects_locked` and
  `assistant._memory_lock`.
- A linked folder or repo keeps working exactly as now: the chat's `workdir` is the linked path (managed
  clones remain under `<home>/workspaces/`).
- Context injection (`assistant.context_block(task, root, project_id)`): date and style rules, global instructions,
  **project instructions**, global memory recall, **project memory**, **knowledge excerpts** (top-k chunks for the
  task, plus the file list), all inside the existing `fence()` so they stay data, not commands.

### 3.5 Migration (nothing lost)

Runs once at startup, idempotent, read-only on the old data, recorded in `<home>/projects/.migrated.json`.
1. For every row in `projects.json` (and for every distinct `workdir` found in session metas that is not in it)
   create a Project: `name` = the existing name, `link = {type:"folder", path}` (or `github` when the path is under
   `<home>/workspaces/` and its `origin` remote parses with `workbench._github_slug`), `legacy_path` = path.
2. Move nothing: copy `memory/projects/<sha1(path)>.md` into `<pid>/memory.md` (same `_project_file` hash).
   Old file stays so an older app version still works.
3. Chats keep `workdir` and `session.json` unchanged. Map by path at read time: `listing()` derives
   `project_id` from `workdir == legacy_path` when `meta.project_id` is absent (no rewrite of old chats); new chats
   write `project_id` to `meta.json`.
4. Chats whose `workdir` is `~/Meshpool Projects` (the default folder), the home folder, or a folder the user
   never named become **plain Chats** (no project), still keeping their folder as an opened-from note, so nothing
   disappears. Their old `kind` (cowork/code) is just metadata; harness stays as stored, so resume behaves as
   before.
5. Folders in `projects.json` with no chats are kept as empty Projects with a link (shown, can be archived).
6. Rollback: delete `<home>/projects/` and `.migrated.json`; the old files were never modified.
7. Global memory and instructions are untouched.

---

## 4. Phased implementation plan

Rules: each phase is shippable alone, changes UI code in small commits, and is gated by
`testkit/ux_jitter_audit.js --strict` plus the existing suites (`ui_crawl.js`, `modes.js`, `midrun.js`,
`jitter.js`, `journeys.py`, `run_all.sh`). Existing scripts that click `.modesw [data-mode]` are updated in the
phase that removes it (P2).

### Phase 0: Jitter fixes first (target: baseline 13/19 to 19/19)
Smallest, highest-value steps, in this order, each separately shippable:
- **0.1 Stable chrome (J7, J8, J6, J5).** Reserve the hint line; one persistent status slot instead of moving
  `#working`; `renderTop`/`renderBar` update in place; Progress panel patched by row key and throttled.
  *Accept:* composer/top bar/sidebar geometry unchanged during a run (landmark moves 0); `#working` moves <= 1;
  header + composer-bar rebuilds <= 1 per run; panel rebuilds <= 5 per reply (was 167).
- **0.2 No end-of-reply swap (J2, J3).** Incremental Markdown renderer shared by stream and history; append-only
  updates. *Accept:* transcript shrinks 0, backward scroll steps 0, CLS during stream < 0.02 (target < 0.005),
  live-message node never replaced (rewrites 0).
- **0.3 Scroll that follows layout (J4, J13).** `ResizeObserver` pin + `overflow-anchor`; "Jump to latest" pill
  when the user scrolled up; panel/sidebar toggles keep the anchor. *Accept:* forward jumps > 150 px 0; panel
  open/close scroll shift 0 px; reading earlier messages is never interrupted (existing `jitter.js` check).
- **0.4 Smooth delivery (J1, J9).** SSE endpoint + rAF-paced text drain; opacity-only entrance in the transcript.
  Polling remains the fallback. *Accept:* growth events >= 20 per second during a stream; max mutations in a
  frame <= 60; thread requests during a stream <= 0.5/s (was 2.3/s); no long task > 100 ms.
- **0.5 Fast chat switching and fonts (J10, J11, J12).** Transcript cache, fragment build, `content-visibility`,
  font preload, logo size. *Accept:* switch: blank frames <= 2 per switch... target 0; <= 500 mutations in the
  biggest frame; open CLS < 0.01 with the CPU throttled 4x and font cache disabled.
Exit of phase 0: `node testkit/ux_jitter_audit.js ... --strict` passes all 19 checks; `jitter.js` and
`midrun.js` still pass.

### Phase 1: Say less, ask less (no data model change, 3-4 days)
- Hide Agent, Access, Plan, goal from the composer; default to Auto (`meshpool-auto`) for every new chat; model
  chip reduced to a short list; "Advanced for this chat" in the chat menu keeps them (code paths unchanged).
- Plain-language pass: "Steps" disclosure under answers, no route chip unless the profile escalates, remove token
  chips from the title bar (move to the chat menu), human tool labels everywhere (`TOOL_RUN` already exists).
- Remove silent "started a new chat" behaviour: a model change applies to the next message in the same chat
  when possible (`pickModel`), and a clear confirm when not.
*Accept:* first-time user can send a message with zero clicks besides typing; composer shows at most: attach,
model chip, send. Existing `modes.js`/`midrun.js` updated; audit still 19/19.

### Phase 2: One chat space, no folder required (4-5 days)
- Remove the Work/Code switch and per-mode homes. One sidebar: Chats list (recent first) and the folder groups
  as today for now.
- Backend: `workdir` optional; per-chat scratch dir; `chat` tool profile; `create()` routes `kind` from Auto.
- Attachments into the chat's own `attachments/` dir.
- Home hero: "What can I help with?" with 4 mixed starters. Remove folder text from the hero and top bar unless
  the chat is linked.
*Accept:* a new chat starts and answers with no folder anywhere in the UI (`journeys.py` new test: chat with no
folder, attach a file, make a document, find it in Files); old chats open unchanged; no regression in
`e2e_full.py`; jitter audit 19/19.

### Phase 3: Projects (5-7 days)
- Data model 3.4, migration 3.5 (with a test that migrates a fixture home and checks every old chat, folder and
  per-folder note is reachable; includes a rollback test).
- Project page: name, description, instructions, knowledge upload and list, memory list.
- `context_block(project_id)`; knowledge retrieval; `project_note` tool writes the project's memory.
- Sidebar: Projects section with project rows (+ "New chat in project"), plain Chats below.
*Accept:* a project's instructions change the mock model's observed system text (mock logs `has_ctx`-style flags,
extend `mock_llm.py` markers); knowledge excerpt appears in the first message of a project chat; memory written in
project A is absent from project B and from a plain chat; migration test passes; jitter audit 19/19 including a
scenario "open project page, start chat" (add to the script).

### Phase 4: Link a folder or GitHub repo to a project (3-4 days)
- Link control on the project page (folder picker, GitHub picker reusing `github_repos/branches/workspace`).
- Chats in a linked project: `workdir` = link, code profile, session panel tabs (Changes, Files, Terminal,
  Preview, PR) shown only for these chats; "Work in a folder" in chat menu for ad-hoc.
*Accept:* existing Code journeys pass against a linked project (`journeys.py`, `modes.js` ported); branch/changes
chips render without moving layout (audit scenario with a linked git project: landmark moves 0).

### Phase 5: Artifacts and Settings (4-5 days)
- Artifacts panel and inline cards; remove Progress/Outputs duplicates.
- Settings regrouped to the 6 sections of 3.3; Advanced holds agents, terminal command, usage.
- Onboarding cut to: key (or sign in) then straight to a chat; import offered later in Personalisation.
*Accept:* `ui_crawl.js` updated and green (every settings page and button, no page errors); `about.js`, `intr.js`
green; Settings sections count <= 6; time-to-first-message from a fresh install <= 3 screens.

### Phase 6: Polish and remove dead paths (2-3 days)
- Delete `Work/Code` state (`S.appMode`, `PANELS` per mode), the kind filter, mode-specific copy.
- Keyboard shortcuts, mobile/narrow layout pass (sidebar overlay, panel as sheet), empty states.
- Add `ux_jitter_audit.js --strict` to `testkit/run_all.sh` and CI (`.github/workflows/tests.yml`).
*Accept:* full `run_all.sh` summary green; jitter audit strict green at 1440x900 and `VP=390x844`.

### Standing thresholds (every phase)
| Metric (script output) | Threshold |
|---|---|
| CLS while streaming / opening / switching / panels | < 0.02 (target < 0.005) |
| Backward scroll steps, forward jumps > 150 px, transcript shrinks while streaming | 0 |
| Message-list rebuilds while streaming | 0 |
| Max DOM mutations in a single frame while streaming | <= 60 |
| Growth events per second while streaming | >= 20 |
| Composer, top bar, sidebar geometry changes during a run | 0 |
| Long tasks | none > 100 ms during stream, > 150 ms in switch/panels |
| Blank frames per chat switch | <= 2 (target 0) |
| Thread polls per second during a stream (after 0.4) | <= 0.5 |

### Risks and notes
- The jitter audit's streaming numbers come from the mock (20 ms/word). Real models vary; the thresholds are
  structural (no swap, no rebuilds, paced rendering), so they hold regardless of speed.
- Auto routing quality gates the "no choice" design: keep the manual override reachable (chat menu, Settings >
  Advanced) and keep logging the route (`meta.route`) so wrong picks can be seen.
- The `chat` tool profile needs a short safety review (no shell, no file writes outside the chat's scratch dir).
- Other agents are editing `ui/app.js`, `app.css`, `server.py`, `workbench.py` now; phase 0 should start from a
  clean checkout of whatever lands first, and the line numbers in section 2.3 should be re-found by searching
  for the quoted code.
