# Session side panel and GitHub sessions

The "Session" tab (first tab of the right panel, both Work and Code modes) shows, for the open chat, where it runs,
what it has used and what it changed. Sessions can also start from a GitHub repository, with no local copy first.

## Where each section reads from

| Section | Source |
| --- | --- |
| Environment | `workbench.session_panel`: the session's folder; "managed workspace" when it is under `<data>/workspaces`; `git_status` for repo slug (linked to github.com) and branch list. Branch switch/create reuses `POST /api/git` (`checkout`). |
| Context | `workbench.context_info`: harness-reported `agent.context` (ACP, Pi: used/size) if present; else the prompt size of the latest `usage` event; else message characters / 4. Window size: the endpoint's model list (`context_length`) if it gave one, else a rule of thumb by model name, else 128K. Both are labelled "estimated" in the UI when guessed. |
| Connectors | Enabled: `session.mcp.status()`. Used: `tool_start` events named `mcp__<server>__<tool>`, counted per server. |
| Outputs | `write_file`/`edit` tool calls, "Saved/Wrote/Created <file>" in tool output, and checkpoint turns. Only files that still exist inside the folder; hidden folders and caches are skipped. Click opens the existing file viewer (`openFile`). |
| Changes | Working tree: `git diff --numstat HEAD` plus `git_status` count. PR: `session_pr` (title, number, +/-, draft, checks verdict via `_checks_for`/`_verdict`) and other open PRs. "Mark ready" uses `POST /api/git/pr {action: ready}`; "Open a draft PR" uses `POST /api/git {action: pr, draft: true}`. |
| Background tasks | Sub-agents from `subagent` events (start without done, only while the turn is running), terminal commands (`TERMS` whose cwd is the session folder), and the scheduled task that started the thread. Stop: `POST /api/session/<id>/bg/stop`. "N finished" counts ended ones. |
| Buttons | Terminal, Browser (the Preview tab), Changes (Diff), Files switch to the existing tabs. |

The panel polls `/panel` every 4 s and `/pr` every 30 s while it is open, and never rebuilds while a field in it has focus.

## Routes added (server.py, implemented in workbench.py)

- `GET  /api/session/<id>/panel` local facts, fast, no network.
- `GET  /api/session/<id>/pr` GitHub: PR for the branch and other open PRs; failures come back in `error`.
- `POST /api/session/<id>/bg/stop` `{kind: subagent|terminal|scheduled, id}`.
- `GET  /api/github/repos?q=` the token's repositories (cached 60 s), filtered; a typed `owner/name` is looked up directly.
- `GET  /api/github/branches?repo=owner/name`.
- `POST /api/github/workspace` `{repo, branch?, create?, base?}` clones or reuses, switches or creates the branch, adds it as a project named `owner/name`, returns `{path, name, branch, reused}`.
- `/api/git {action: pr}` accepts `draft: true` (API and `gh --draft`).

## GitHub sessions

- UI: "New session from GitHub" in the Projects header (next to + for a local folder) and in the project picker. A searchable repo list, a branch select, and "New branch from it". The user sees `owner/name`; the clone's folder is not shown anywhere (the project list marks it `managed`).
- Clone location: `<data>/workspaces/<owner>__<repo>`. One clone per repository; a second session on the same repo reuses it (`git fetch --prune`, then switch).
- Token: the same one `_gh_api` uses (`mcp_apps.github_token()`: saved `GITHUB_TOKEN`, else a login found on this computer). For clone, fetch, pull and push in a managed workspace, `_git` adds `-c credential.helper=` (resets stored helpers) and an inline helper that answers git from `$MESHPOOL_GH_TOKEN`, set only in that child process's environment. The token is never in argv, in `.git/config` (the remote is the plain `https://github.com/o/r.git`) or in error text. Other (user-added) folders are unchanged.
- Commit, push and PR use the existing git/PR actions; pushes from a managed clone now sign in with the token.

## Open decisions for the owner

1. One clone per repo means two sessions on different branches of the same repo share a working tree. Alternative: `git worktree` per branch (isolated, more disk and more moving parts).
2. Removing a managed clone: there is no UI yet (it is removable like any project, but the folder stays on disk). A "Free up space" action would be needed for large repos.
3. The built-in agent does not report its context size. The estimate above is good enough for a bar. A hook in `agent/core.py` (owned by another agent) would make it exact:
   after the `usage` event is emitted in the model call (where `self.usage` is updated), add
   `self.context = {"used": d["in"], "size": <model window>}`; the panel already prefers `agent.context` when set.
4. The branch select lists local branches (existing `git_status`); branches only on the remote appear after a fetch. A "remote branches" option can be added with `git branch -r`.
5. Draft PR from the panel pushes and opens with the branch name as the title (asked in a prompt); a fuller form (body, base) stays in Git and CI.
