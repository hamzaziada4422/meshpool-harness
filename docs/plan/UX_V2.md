# MeshPool UX v2: what changed

Implements phases 1 to 5 of `docs/plan/CHAT_UX.md` and the Settings direction that came after it. Same patterns as the
best chat apps, MeshPool's own look and words. Phase 0 (jitter) was already done and still holds: the jitter audit is
19/19 (`node testkit/ux_jitter_audit.js PORT DIR --strict`) and `testkit/jitter.js` is green.

## What a person sees now

**One chat space.** No Work/Code switch, no agent, access or plan pills. A new chat needs no folder: Meshpool gives it a
private working folder and Auto picks the agent (and so whether it needs code tools). The message box has an attach
button, one quiet "Chat settings" button (permissions, plan first, agent, work in a folder; a dot says something is
changed) and one small model chip.

**Sidebar.** New chat, search (chats and projects), Projects, then chats grouped Today, Yesterday, Previous 7 days,
Previous 30 days, then by month. Pinned chats sit on top. Sections fold and remember it. Hovering a row swaps the time
for a `...` button (no layout shift). The menu, also on right-click and on the Menu key: Rename (inline, Enter saves,
Esc cancels, F2), Pin or Unpin, Add to project or Move to project (submenu, with "New project..." and "Remove from
project"), Archive or Restore, Delete (confirm, then a 6 second undo). Rows are keyboard reachable: arrows move, Enter
opens, F2 renames, Delete deletes. The sidebar collapses with an animation and remembers it. Tooltips name each control
and its shortcut.

**Projects.** A page with name, description, Instructions, Files (upload, drop, list, remove), Memory (view, edit, clear)
and an optional linked folder or GitHub repository, plus the project's chats. The message box at the bottom starts a
chat in the project. Chats show their project as a breadcrumb in the top bar.

**Right panel.** One toggle (`Ctrl/Cmd .`). Tabs: Session (plan, context, connectors, artifacts, background tasks; for a
linked repository also environment, branch, changes, pull request), Steps, Artifacts. A chat in a linked folder also
gets Changes, Files, Terminal, Git and CI and Preview. Open or closed state and width are remembered.

**Settings.** Seven sections with tabs inside (every old page is still there):

| Section | Tabs |
|---|---|
| General | Profile (name, what to call you, reply language, default model), Appearance (theme, text size, chat width, density, steps), Notifications |
| Capabilities | Features (memory, web search, browser use, sub-tasks, plan first, computer use), Instructions and memory, Web search |
| Connectors | Directory (connect, sign in, add your own), Skills, Plugins, Custom tools |
| Models and accounts | Account and key, Models, Agents |
| Usage and billing | Usage (spent on the key, by day, by model), Plan and billing (opens meshpool.ai) |
| Privacy and data | Permissions, Your data (where, export, delete chats), Import chats |
| Advanced | About and updates, Scheduled tasks, Terminal command, More options, Company and brand |

## Data model

Everything is new files in the data folder; nothing old is changed.

```
<home>/projects/<pid>/project.json     {id, name, description, created, updated, link, legacy_path}
                      instructions.md  the user's directions for every chat in the project (8,000 characters)
                      memory.md        one fact per line (6,000 characters)
                      knowledge/<file> copies of the user's files, plus .index.json (text cache for recall)
<home>/projects/.migrated.json         folders already turned into projects
<home>/chats/<id>/                     the private folder of a chat with no linked folder
<home>/chats/<id>/.project             the project id, so a project note reaches the right memory
<home>/memory/profile.json             name, what to call them, reply language, plan first
runs/sessions/<sid>/meta.json          + project_id ("" = taken out of its project), scratch, project_ctx
```

`link` is `null`, `{type: "folder", path}` or `{type: "github", path, repo, branch}` (path is the managed clone).
`pid` is `p_` plus 10 hex characters and is validated before any disk access.

### How a project reaches the agent

`assistant.context_block(task, root, project_id)` is still added once, out of sight, to the first message of a new chat
(never the system prompt, so the cached prefix is untouched). Order: date and rules, profile line, global
instructions, global memory (fenced), then the project: its instructions (the user's directions, said to win over the
general ones), then project memory and knowledge inside the memory fence (data, not commands). Knowledge: a one-line
summary per file; files under 1,500 characters whole; for larger ones the passages that match the first message (up to
6,000 characters in all). Deterministic for the same message. The two switches in Settings still apply (instructions
off removes project instructions, memory off removes project memory). A chat moved into a project later gets the project
block once, on its next message (`project_ctx` in `meta.json`). Edits to a project reach new chats; open chats keep what
they started with (said in the UI). The `project_note` tool writes to the chat's project memory: `for_root()` maps a
chat's private folder (`.project`) or a linked folder to its project, otherwise it writes the old per-folder note.

Chats in a project with a private folder also get the project's files hard-linked under `knowledge/` so the agent can
open them in full.

### Backend routes added

`GET /api/pj`, `GET /api/pj/<pid>`, `GET /api/pj/<pid>/file?name=`, `POST /api/pj` (create), `POST /api/pj/<pid>` with
`action` = update, delete, memory_set, memory_add, memory_clear, knowledge_add, knowledge_remove;
`POST /api/threads/<id>` gained `move` (project_id); `POST /api/agent` takes `project_id` and no longer needs `workdir`.
Settings: `GET/POST /api/profile`, `/api/capabilities`, `/api/data` (export, clear_chats), `GET /api/spend`,
`POST /api/notify`. Code: `app/meshpool_harness/project_store.py`, `ui/settings_v2.py`, `ui/agent_api.py`,
`ui/server.py`; UI: `ui/app.js`, `ui/projects.js`, `ui/settings2.js`, `ui/app.css`.

## Migration and rollback

On start (`server.serve`), once per folder and read-only on old data: every folder in `projects.json` and every folder
an old chat worked in becomes a project with `link = {folder or github}` and `legacy_path`; the folder's old
`memory/projects/<sha1>.md` notes are copied into the project's memory. The default `Meshpool Projects` folder and the
home folder do not become projects (their chats are plain chats). Old chats are not rewritten: a chat with no
`project_id` in its meta finds its project through its folder (`agent_api._project_of`). A chat taken out of a project
on purpose stores `project_id: ""`. Nothing is deleted. Deleting a project removes only its own folder under
`projects/`; its chats stay as plain chats and a linked folder is never touched.

**Rollback:** delete `<home>/projects/` (and `<home>/chats/` only if you want the private folders of no-folder chats
gone; those chats then cannot reopen). `projects.json`, `memory/` and every chat were never modified, so an older
app opens the same data. Tested in `test_projects_v2.py::test_migration_keeps_old_folders_chats_and_notes`.

## Tests

`app/tests/test_projects_v2.py` (store, knowledge, memory, context, migration and rollback, private chat folders,
listing and move, HTTP routes), `app/tests/test_settings_v2.py` (profile, capability switches, spend never returns the
key, export). UI: `testkit/ux_v2_flows.js` drives the new screens by click and key (39 checks);
`testkit/ux_jitter_audit.js --strict` (19/19); `testkit/jitter.js`; `testkit/ux_v2_shots.js` takes the screenshots.
`testkit/ui_crawl.js`, `midrun.js`, `modes.js`, `about.js`, `v24.js` were updated for the removed controls.

## Screenshots

`docs/screenshots/ux-v2-<light|dark>-<desktop|narrow>-<name>.png` (desktop 1440x900, narrow 390x844). Names: 01-home,
02-sidebar, 03-chat-menu, 04-add-to-project, 05-rename-inline, 06-project-menu, 07-project-page,
08-instructions-dialog, 09-project-chat, 10-side-panel-session, 11-side-panel-artifacts, 12-side-panel-steps,
13-chat-settings, 14-model-picker, 15-chat-options-menu, 16-linked-project-chat-panel (desktop), 17-delete-confirm,
18-search, 19-sidebar-collapsed (desktop), 20 to 33 settings (profile, appearance, notifications, capabilities, memory,
connectors, account, models, usage, billing, permissions, data, about, advanced; narrow has profile, capabilities,
connectors, usage).

## Left for later

- A `chat` tool profile (no shell) for chats with no folder. They use the same tools as before, inside their private
  folder; the safety review CHAT_UX.md asks for is not done.
- Per-connector, per-tool permissions (always allow, ask, never) and plain-language fixes for each connector failure:
  the Directory shows status, connect, disconnect and "add your own", but not per-tool rules.
- "Code execution" and "File creation" are not switches (the engine has no flag for them); the Capabilities page says
  they are always available and lets you set the default permission instead.
- "Add to project knowledge" on an attachment chip in the message box; knowledge is added on the project page.
- Notifications use the OS notification helper the scheduled tasks already used; whether it shows depends on the
  system's notification permission. The Usage page's key spend needs a service that returns the LiteLLM spend headers;
  without them it says so.
- Onboarding still has its three steps (key, permissions, import); only its wording was touched.
- The old per-folder project rows are gone from the sidebar; "Settings, Privacy and data, Permissions" still lists
  the folders Meshpool may open.
