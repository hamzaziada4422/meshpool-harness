# ws24-worktrees: first-class isolated copies (G-11)

## Problem

Sub-agents, parallel chats on one repository and scheduled runs all work in the same folder. Two agents edit the
same files, each turn's checkpoint sees the other's edits, and Undo refuses both (audit M18-02). The only git
plumbing in the code base is `engines/code/engine.py` (`_worktree_tree`, `snapshot`, `restore`: a working tree as a
tree id, and putting a working tree back to a tree exactly); nothing creates a separate working copy.

## Design

One module, `meshpool_harness/worktrees.py`, owns every isolated copy. Everything else calls it.

### Storage

- Copies live under `<MESHPOOL_HOME>/wt/<id>` (id = 8 hex chars). Short on purpose (Windows MAX_PATH), outside the
  user's repository (never shows up as untracked files there).
- Registry `<MESHPOOL_HOME>/wt/registry.json`, written only with `fsio.update_json` (atomic, cross-process lock).
  One record per copy: `id, kind (git|copy), source (resolved folder), path, branch, base (commit), mode
  (commit|apply), owner {kind: chat|subagent|schedule|cli, id}, title, state (creating|ready), created, merged`.
- A per-source lock (`fsio.file_lock(<home>/wt/locks/<sha1(source)>)`) serialises create, merge, discard and prune
  on one repository, across threads and processes.

### Create (`create(source, owner_kind, owner_id, title, base="head"|"worktree")`)

1. Write the record with `state: creating` first (so a crash leaves a trace), then do the work, then set `ready`.
2. Git folder: `git worktree add -b meshpool/<id> <path> <base>`.
   - `base="head"` (chats, schedules, CLI): the folder's current HEAD. Uncommitted changes in the folder are not
     carried over; the record notes `base_dirty` so the UI can say so.
   - `base="worktree"` (sub-agents): a snapshot commit of the folder's working tree (engine `_worktree_tree` +
     `git commit-tree -p HEAD`, no ref of the user's moves), so a sub-agent sees the parent turn's unsaved edits.
   - Folder with no commit yet: treated as non-git (copy).
3. Non-git folder (or unborn HEAD): copy the folder (skipping `checkpoints.SKIP_DIRS`) twice, once as the working
   copy and once as the read-only `base` used for diff and the three-way file merge. Refused above
   `checkpoints.MAX_FILES` files or 500 MB.
4. Any failure: remove whatever was made (worktree, branch, folder), drop the record, raise `WorktreeError`.

### Diff (`diff(id)`)

Git: tree of the copy's working tree (engine `_worktree_tree`, no commit, no index change) against the merge base of
the copy's branch and the folder's HEAD (so after a merge the diff is empty). Copy mode: `difflib` against `base`.
Returns `{files: [{path, status}], patch, stat}`.

### Merge (`merge(id)`) and Apply

- `mode=commit` (chats, schedules, CLI), git:
  1. Commit pending changes in the copy on its branch (identity fallback `Meshpool <meshpool@localhost>` when the
     user has none, `--no-verify`, harness folders `.run`/`.junie` excluded).
  2. Refuse when the folder is mid-merge/rebase/cherry-pick or on a detached HEAD.
  3. `git merge-tree --write-tree` in memory: conflicts are reported (`{ok: False, conflicts: [...]}`), nothing
     in the folder is touched.
  4. Paths the merge would change are compared with the folder's uncommitted and untracked paths
     (`git status --porcelain -z`); any overlap is refused with the list. Never overwrites the user's work.
  5. Fast-forward when HEAD is an ancestor of the branch (`git merge --ff-only`), else `git merge --no-ff`. On any
     unexpected failure `git merge --abort`.
- `mode=apply` (sub-agents), git: three-way merge of trees (`git merge-tree --write-tree --merge-base=<snapshot>`
  of the parent's current working tree and the sub-agent's working tree). Clean: the parent's working tree is moved
  to the result with engine `restore` (only paths the sub-agent changed are written; the index is kept). No commit:
  the parent turn's checkpoint and Undo cover it. Conflict: reported, branch kept, nothing touched.
- Copy mode: per-file three-way on hashes (base, copy, folder). A file changed in the copy is written when the
  folder's file still equals base (or already equals the copy); otherwise it is a conflict. All-or-nothing; on
  success `base` is updated to the merged state.

### Discard and prune

- `discard(id, keep_work=False)`: `git worktree remove --force`, branch deleted (`keep_work=True`: pending changes are
  committed on the branch first and the branch is kept when it has commits not in the folder's HEAD), folder
  removed (Windows: read-only files made writable, `\\?\` long paths), record dropped.
- `prune_owner(kind, id)`: discard every copy that owner holds; deleting a chat calls it (keep_work=True, so work
  that was never merged stays on its `meshpool/<id>` branch and is reported, not silently lost).
- `recover(alive)` at start-up: drop `creating` records (clean up their remains), records whose folder vanished
  (`git worktree prune`), records whose owner `alive(kind, id)` says is gone (sub-agents never survive a restart),
  and folders under `wt/` with no record (an orphan git worktree is detached from its repository through its `.git`
  file). Chat copies of chats that still exist survive restarts.

### Windows

All paths through `pathlib`; comparisons on `os.path.normcase(realpath)`; every git call gets
`-c core.longpaths=true` on Windows; `_long()` adds `\\?\` for removal of deep trees; `rmtree` with an `onerror`
that clears read-only bits (git pack files). A module flag `_WINDOWS` makes these paths testable on Linux.

## Integration (one seam each)

| Where | Change |
|---|---|
| `ui/agent_api.py` `create()` | `worktree: true` (Code chats): create a copy owned by the chat, the session runs in it; meta `worktree={id, source}`; project association and recent folders use the source. `worktree_id` (from the scheduler seam) attaches an existing copy. |
| `ui/agent_api.py` `Session.view()` | `worktree: {id, source, branch, kind}` |
| `ui/agent_api.py` `thread_action(delete)` | `worktrees.prune_owner("chat", sid)` |
| `ui/agent_api.py` `run_scheduled()` | folder from `assistant.run_folder(t)` (the one seam in assistant.py) |
| `assistant.py` | `run_folder(t) -> {workdir, worktree_id}`: git folder -> a copy by default (`t["worktree"]` false opts out) |
| `ui/worktree_api.py` (new) + `routes_extra.py` | `GET /api/worktree?workdir=` (suggest: other live chats in that folder), `GET /api/worktree/<chat>/diff`, `POST /api/worktree/<chat>` `{action: merge|discard}`. Discard returns the chat to the source folder. |
| `ui/server.py` start-up | `worktrees.recover(alive)` in a thread |
| `ui/worktree.js` (new), `index.html`, `app.js`, `shell.js`, `composer.js` | Composer "+" item "Work in an isolated copy (worktree)" for Code drafts; a suggestion banner when another chat works in the folder; panel "Isolated copy" (diff, Merge, Discard) for chats in a copy. |
| `agent/delegate.py` | Two or more editing sub-agents: each gets a copy (`base=worktree`, `mode=apply`); after all finish the parent applies them in order; conflicts are reported in the tool result and the branch kept. |
| `cli.py` | `--worktree` on the agent parser (session in a copy, summary printed at exit); `worktree` in `COMMANDS` -> `worktrees.cli(argv)`: `list`, `diff <id>`, `merge <id>`, `discard <id>`. |
| `checkpoints.py` | No change needed: `Checkpoints(root=<copy>)` already works per folder; tests prove Undo inside a copy leaves the source alone. |

## Tests (`tests/test_worktrees.py`, `tests/test_worktrees_app.py`)

- create/list/diff/discard on git; branch and folder gone after discard
- two chats editing the same repo in parallel threads: neither sees the other's edits; both merge (ff, then merge
  commit); the folder has both
- merge with conflict: reported, folder HEAD, files and status unchanged
- merge refused when the user has uncommitted changes to a file the merge touches; allowed when they don't overlap,
  and the user's change survives
- crash mid-create (git fails / exception after `git worktree add`): no record, no folder, no branch; a stale
  `creating` record is cleaned by `recover`
- `recover`: orphan folder removed, dead owner pruned, live chat kept
- delete chat prunes its copy (unmerged work kept on its branch)
- non-git fallback: copy, diff, merge, conflict, discard
- sub-agent apply mode: two copies from a dirty parent, both applied; a conflicting one reported and not applied
- scheduler seam: git folder -> copy; non-git or opt-out -> folder
- Windows: `_long()` and git `core.longpaths` with `_WINDOWS` monkeypatched; read-only file removal
- checkpoints: snapshot + revert inside a copy does not touch the source
- CLI: `worktree list|diff|merge|discard`, `--worktree` creates and reports
- API: suggest, diff, merge, discard through `worktree_api.handle`; app create with `worktree: true`

## Process

Owner's instruction was Opus plans, Sonnet executes, Opus double-checks. This session has no sub-agent tool, so
the same session executes and reviews; the review step (tests failing without the feature, break attempts) is kept.
