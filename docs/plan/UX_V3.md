# UX v3: the app shell rebuilt to the design brief

Status: built on branch `worktree-agent-a0fc9210e11c6b46e` (merged from `claude/cool-hawking-qen4hs`). Reference: the
front-end brief (BRIEF.md and its screenshots, read-only, outside the repo; none of its images or sample data are in
this repository). Our screenshots: `docs/screenshots/ux-v3-<dark|light>-<desktop|narrow>-NN-<name>.png`, desktop at
1274 x 744 like the brief, narrow at 390 x 844.

## What changed, in one paragraph

The app is now a shell with two modes, Chat and Code. A 288 px sidebar (header with the Meshpool logo, mode buttons, nav
rows, Pinned with expandable projects, Recents, account and apps buttons), a 44 px top bar, a composer with a row of
selectors under it, a search dialog, four gallery pages that share one pattern, an account menu, a Settings modal with a
left navigation, and a Code home with environment and repository selectors. The chat engine (streaming, tools, approvals,
question cards, plan, panels, git and pull request views, the update bar) is unchanged and sits inside the new frame.

## Files

| File | Holds |
| --- | --- |
| `ui/shell.js` | `shell()`, sidebar (`renderSide`, rows, status dots, project expansion), top bar (`renderTop`, title menu, Step button, device popover, Export), search dialog, account and apps menus, keyboard shortcuts, data loads |
| `ui/composer.js` | plus menu, permission and model menus, project, environment and repository selectors (`listPop`), `renderBar`, the row under the box, Chat home and Code home |
| `ui/pages.js` | Projects, Artifacts, Scheduled tasks and Plugins pages, the task dialog |
| `ui/settings2.js` | the Settings modal frame and navigation, Appearance, Models and providers (Connection) |
| `ui/shell.css` | all shell, menu, dialog, gallery and settings-frame styling (loaded after `app.css`, which now holds the tokens and the older components) |
| `ui/hub.py` | data for the new pages (see Backend) |
| `app.js`, `projects.js`, `company.js` | engine, project page, Company settings; edited where the shell changed (menus, toasts, appearance, message actions, tool timers) |

## Tokens

Dark is the reference theme: app `#191919`, sidebar and dialogs `#1F1F1F`, raised surfaces `#303030`, hover and active
`#404040`, selected `#3B3B3B`, muted text `#B7B7B7`, text `#FFFFFF`, accent `#237DE0` (toggles, progress, unread dots,
links). Light is derived with the same roles (app `#FBFBFB`, sidebar `#F3F3F3`, surfaces `#FFFFFF`, hover `#E8E8E8`,
selected `#E2E2E2`, muted `#686868`, accent `#1F6FD0`); text and muted text pass AA on every surface in both themes.
Measurements follow the brief: sidebar 288, nav rows on a 34 px pitch with 8 px corners, composer 656 wide, popovers
230 to 300 wide, search dialog 672, Settings modal 1022 x 710 with a 212 px navigation, top bar 44 px.

Motion: menus, popovers, tooltips and the search dialog 150 ms ease-out; sidebar 200 ms; hover colours 100 ms; toasts
about 5 s (or until closed with Settings > Appearance > Popups); `prefers-reduced-motion` and Settings > Appearance >
Motion switch every transition and animation off.

## Brand boundaries

| The brief has | Meshpool uses |
| --- | --- |
| the word "Claude" | "Meshpool" |
| orange asterisk logo and serif wordmark | `brand/logo-*.png` (mark and wordmark, per theme) and `brand/mark.png` in the greeting and Code home |
| brand orange `#d37358` (running dots, waiting, Code indicator) | the teal from the logo's highlight, `--brand` `#5FC4CC` dark and `#0F8591` light (running dots, the Code indicator, tool-activity dots). The amber "Waiting" label stays amber, as in the brief. The logo is blue like the accent, so the second colour had to come from its highlight (`favicon.svg` uses the same teal) |
| Anthropic Sans | IBM Plex Sans (already bundled), or the system font (Settings > Appearance) |
| serif display face | Bricolage Grotesque 700, Meshpool's headline style, for the greeting, page titles and the Code wordmark |
| pixel-art mascot | not copied: the logo mark sits above the waiting-sessions card |
| copy such as "Back at it," and the AI disclaimer | own copy: time-of-day greeting, "Answers can be wrong. Check what matters." |
| sample names, emails, numbers | fixtures in `testkit/ux_v3_shots.js` |

`tests/test_ui_brand.py` fails when another company's name or the old orange appears in the UI files. Two kinds of lines
are allowed: comments, and the Models and providers page, which names sign-ins found on the computer (a Claude Code
install, `ANTHROPIC_API_KEY`) as the product owner's contract for that page asks. Import wording elsewhere ("Coming from
ChatGPT, Codex or another assistant?") is generic now.

## Screen by screen

### App shell and sidebar (`shell.js`, `shell.css`)
- Header: sidebar toggle (first icon; when hidden it is the only thing left at the top left, with a blue dot when
  something needs you), logo, Chat and Code buttons. The Code button gets the brand dot and the tooltip "Code, awaiting
  your input" when a code session is waiting.
- Nav rows: Chat mode New, Search, Projects, Artifacts, Scheduled (blue dot with unread scheduled results), Plugins.
  Code mode New, Search, Artifacts, Plugins, More (Scheduled, Projects). Nav rows scroll with the list; header and footer
  stay fixed.
- Pinned: pinned projects (stack icon; click the icon to expand in place to six chats and "View all") and pinned chats.
  Recents: all other chats. A lightning icon marks a chat a scheduled task made. Right-hand status: blue unread dot,
  brand dot while running, amber dot while waiting; on hover or when open the row shows the "..." button.
- Code mode Recents: code sessions only, with a filter button on hover (All, Waiting, Unread, With a pull request), status
  icons (pull request, GitHub workspace cloud, warning triangle when waiting) and the unread dot. The pull request icon
  opens a card on hover (title, Draft or Open, repository and number, +/- diff counts).
- Footer: account button (initial, first name, "Key" or "No key") and the apps button (Chat) or a gear (Code).
- Data: `/api/agent` rows (now with `unread`, `scheduled`, `pr`), `/api/pj` (now with `pinned`), `/api/hub/account` for
  counts and the footer.

### Search (`openSearch`)
Dialog with field, close, six tabs, groups Needs attention, Recents, Actions (empty query) or typed results grouped by
type, first row highlighted, "..." and return glyph on the active row, footer with Close, Change type, Open, Actions
(Alt+Enter opens the chat's menu), Compose (Tab starts a chat with the query). Loading shows a spinner and five skeleton
rows. Data: `/api/hub/search?q=&tab=` (chats, code sessions, projects, artifacts, scheduled tasks; the chat text search is
the existing `A.search`). The "Filters (/)" hint of the brief is not shown: there are no filters to open.

### Home and composer (`composer.js`)
Greeting by time of day with the logo mark; the composer box; under it, left Project selector (Chat) or Environment and
Repo selectors (Code), right model button with the effort in muted text and the permission button. A setup card appears
when there is something to set up (no key, GitHub not connected, no projects) and its dismissal is remembered. Plus
menu: Upload or add files (Ctrl or Cmd+U), Plan first, Memory, Skills, Plugins, Agent, Work in a folder. Typing `/` lists
commands and skills together. Model menu: up to four models (selected, default, favourites) with a check, keys 1 to 4,
Effort and More models (the existing searchable picker with providers). Permission menu: Manual, Auto, Full access.
Project selector: search, Pinned and Recent groups, New project. Data: `/api/models`, `/api/pj`, `/api/capabilities`,
`/api/connectors`, `/api/skills`, `/api/harnesses`, `/api/github/repos`.

### Galleries (`pages.js`)
One pattern: display title, tabs, toolbar icons, optional banner, card grid.
- Projects: two-column cards (stack icon, name, relative date or short date, pin icon), search, sort, New project;
  card menu pins, renames and deletes. Data: `/api/pj`.
- Artifacts: type tabs built from the data, a dismissible banner, a "Make something new" strip of tinted tiles (each
  starts a chat with a prompt), three-column cards with a thumbnail (images are real previews), name, "Type, date";
  list or grid toggle. Data: `/api/hub/artifacts` (files made across all chats; saved chats are read from their
  messages and cached per chat).
- Scheduled: Yours and Templates tabs, cards (name, two-line prompt, status line, project chip, unread dot), New task
  dialog, card menu (open last run, run now, edit, pause or resume, delete). Data: `/api/hub/scheduled`, writes through
  `/api/assistant`.
- Plugins: search, All, Connectors, Skills, Add menu, Your plugins (connected connectors, skills, installed plugins) with
  row menus, and connectors available to connect. Data: `/api/connectors`, `/api/skills`, `/api/plugins`.

### Account menu
Muted header (key status), Settings, Usage, Language (Settings > Account), Get help, Learn more, View all plans and Get
API keys (open meshpool.ai), Get apps and tools (command line tool installer, downloads, updates), Quit Meshpool
(desktop only).

### Chat session
Top bar: title with a chevron (menu: Pin P, Rename R, Add to project, Export chat, Show every step, Archive A, Delete D;
the letters work while the menu is open), a device button with a green dot (this computer, Connected, open the
folder), branch and checks chips kept from before, a Step i of n button (from the plan; "Progress" when there is none)
that toggles the right panel, and Export (copy as Markdown or save a file). The panel is the existing Session, Progress
and Artifacts content (Plan lives in Session and drives the Step label). User messages show edit-and-resend and copy
buttons on hover. Tool activity groups show moving brand-coloured dots and a live timer in whole seconds while running.
The running composer shows a square stop button next to send ("add to the task") and "Send now".

### Code mode
Home: "New session" label, the logo mark above a card of up to three waiting sessions (amber "Waiting" and the title) or a
short prompt, a dismissible tip, the composer ("Describe a task or ask a question"), Environment selector (local folders
and GitHub workspaces, "Choose a folder...", "Work on a GitHub repository..."), Repo selector (GitHub repositories with
search, refresh and connector settings, "No repository", link to connect GitHub), model and permission buttons. A session
started here is created with `kind: "code"` and needs a folder or repository. Chat mode lists everything else, Code mode
lists code sessions (kind code with a real folder or workspace).

### Settings (`settings2.js`)
Modal 1022 x 710 with a "Settings" header and close button, a navigation with search (filters pages by name and by the
names of their sections) and groups: Account, Billing, Usage; Appearance, Your Meshpool, Capabilities, Notifications,
Privacy; Plugins, Code; Models and providers, Scheduled tasks, Company and brand, About and updates; API keys (opens
meshpool.ai). Pages with several old sections show them as tabs. The hash follows the page (`#settings/appearance`).
All 23 sections that existed before are reachable. Mapping: Account = Profile; Billing = Plan and billing (opens
meshpool.ai); Usage = key spend and local usage; Appearance = new rows page; Your Meshpool = Instructions and memory,
Import chats; Capabilities = Features, Web search; Notifications; Privacy = Your data, Permissions; Plugins = Directory,
Skills, Plugins, Custom tools; Code = Terminal command, More options; Models and providers = Connection, Models, Agents.

Models and providers > Connection follows the product owner's flow: "Use an existing login" (only what was found:
ChatGPT through the Codex CLI, Claude Code, API keys in the environment), then Base URL and API key (a saved key reads
"•••• 1234" with Change; it is never displayed), Test connection (result line with latency, then the endpoint's models
as a searchable list, nothing preselected), pick the default model, Save. When an endpoint lists no models a model name
can be typed. Switching endpoints starts from a fresh test, so an old model list never blocks it. Routes:
`/api/provider`, `/api/provider/test`, `/api/provider/save`, `/api/provider/logins`, `/api/provider/use`
(`provider_setup.py`, built by the backend track).

## Backend added (`ui/hub.py`, routes in `server.py`, tests in `tests/test_hub_v3.py`)
- `GET /api/hub/artifacts`: files made or changed across all chats (saved chats parsed from their messages, per-chat cache,
  code-session edits left out, only files that still exist), with type, tint, size, date, chat and project.
- `GET /api/hub/scheduled`: scheduled tasks as cards (status, schedule text, project, unread, last chat) and templates.
- `GET /api/hub/search?q=&tab=`: groups across chats, code sessions, projects, artifacts and scheduled tasks; with an empty
  query: Needs attention, Recents, Projects, Artifacts, Scheduled, Actions.
- `GET /api/hub/account`: name, initial, key state, device, counts for the dots (Code waiting, unread, scheduled unread).
- Unread flags: the session writes `unread` to its meta when a turn ends, `POST /api/threads/<id> {action: "seen"}` clears
  it (the page does that when a chat is opened or is in front); the browser-only `S.unseen` set is gone.
- `pinned` on projects (`PS.update(pid, pinned=...)`, which does not touch the date).
- `pr` on session rows: the last pull request seen by `/api/session/<id>/pr` is remembered in meta so rows can show the
  icon without asking GitHub; the card refreshes on hover (never while a list is drawn).

## Deviations and why
- **Home composer is docked at the bottom, not centred.** `testkit/ux_jitter_audit.js` requires the composer to keep one
  geometry from the home screen through a streamed reply. A centred home composer moves when the first message is sent,
  which fails that check, and the audit is a hard requirement. The greeting and cards are centred in the space above.
- **No Design page.** Meshpool has no separate design product; decks and documents are Artifacts ("Slides", "Docs" tiles).
- **No incognito.** Nothing in Meshpool can run a chat without saving it. The top-right control on home is "Report a
  problem". Shift+Cmd+I is not bound.
- **No microphone or voice mode.** Meshpool has no speech input; the controls and dictation settings are left out.
- **Effort.** Meshpool sizes effort to each request (`pace.py`); the menu shows a read-only "Auto" with that explanation
  rather than levels that would do nothing.
- **Manual and Auto.** Meshpool has three access modes. Manual = ask first, Auto = edit freely, and Full access is kept
  (amber when selected) so no existing setting is lost.
- **Share.** There is nothing to share to, so the button is Export: copy the chat as Markdown or save it.
- **Message actions.** Edit (puts the text back in the composer) and copy; no bookmark.
- **Remote Control machines, cloud environments and "Download" badges.** The environment selector lists local folders and
  GitHub workspaces, which is what Meshpool has.
- **Artifacts tabs.** "Yours" and "Shared with you" have no meaning without sharing, so the tabs are by file type.
- **Account menu.** No email or log out: the header says whether a key is saved, and Quit Meshpool shows in the desktop app.
- **Settings.** Wellbeing, the sign-in and device tables, passkeys, dynamic conversations and code colour themes have no
  Meshpool equivalent and are not shown. Code font and size, interface and response font, motion, contrast and popups are
  real settings. Notifications keeps Meshpool's three toggles.
- **Search hints.** "Filters (/)" is omitted; "Open" is added.

## What the brief lists as not captured, and what was done
- Project detail page and New project dialog: the existing project page and dialog, restyled by tokens.
- Artifact viewer and editors: files open in their own app (`/api/open-file`).
- A finished chat with long output: the existing renderer.
- Voice mode, Share, Rename and Delete confirmations, Add to project submenu: not applicable (voice), Export (share);
  rename, delete and the project submenu already existed and use the new menu component.
- Scheduled creation flow and Templates tab: a task dialog; four starter templates written for Meshpool.
- Light theme: derived (see Tokens).
- Narrow layouts: sidebar becomes an overlay below 760 px, panels full width, selectors shorten, pages go single column;
  `ux_v3_flows.js` checks there is no sideways scroll.
- Empty, error and offline states: written for each page and menu ("No projects yet", "Nothing matches...", load errors).
- Second-level submenus: Plugins, Skills, Agent, Effort, Learn more, Add to project are built from Meshpool data.

## Tests
- `tests/test_hub_v3.py` (9), `tests/test_ui_brand.py` (3), the provider tests of the backend track.
- `testkit/ux_v3_flows.js` (105 checks): the v2 checks ported (projects, rows, rename, pin, move, delete, keyboard, panel
  and sidebar memory, every settings page reachable) plus shell, search, menus and shortcuts, Code mode, galleries,
  account menu, Settings modal and hash, Appearance, Models and providers, unread dots, reduced motion, narrow width.
- `testkit/ux_jitter_audit.js --strict`: 19 of 19. `testkit/ux_v3_shots.js` writes the screenshots.
- `ux_v2_flows.js` and `ux_v2_shots.js` describe the v2 DOM and are kept only as history.

## Open items
- Other older scripts in `testkit/` (`ui_crawl.js`, `modes.js`, `intr.js`, `v24.js`) still use v2 selectors such as `#q`
  and `#oPill`; they are not part of CI and were not ported.
- Nothing yet measures the pull request card's content against a real GitHub repository; it shows what the session
  panel last fetched.
- Dead rules from the old sidebar, composer and menus remain in `app.css` (overridden by `shell.css`); they can be pruned.
