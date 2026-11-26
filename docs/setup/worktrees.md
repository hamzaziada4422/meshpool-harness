# Worktrees

When two chats or agents work in the same folder, each edits the other's files, and Undo for a turn can no longer tell whose change is whose. An isolated copy gives a task a folder of its own. You look at what it changed and merge it back when you are ready.

- In a git repository, the copy is a git worktree on its own branch, `meshpool/<id>`.
- In a folder without git, the copy is a plain copy of the files, plus a second copy of how they were at the start, so meshpool can show the diff and merge. A folder over 500 MB, or with more than 20,000 files, is too big for a plain copy. Make it a git repository to work in a copy.

Copies live in `wt/<id>` in the meshpool data folder, outside your repository, so they never show up as changes there.

## A chat in an isolated copy

- In a new Code chat with a folder, open the plus menu and tick Work in an isolated copy (worktree).
- When another open chat already works in that folder, the new chat offers it: Use an isolated copy. Work in the folder turns it off again.
- In a git repository, the copy starts from the folder's last commit. Uncommitted changes stay in the folder and are not in the copy; the panel says so when there were some.
- The chat is listed under the folder it came from, and its copy stays across restarts.

The chat's Isolated copy panel shows the changes the folder does not have yet, file by file, with the diff. Wait for the chat to finish, or stop it, before you use the buttons.

- Merge brings the changes into the folder. In a git repository, meshpool commits the changes on the copy's branch and merges that branch into the folder's current branch: a fast-forward when it can, else a merge commit. You can keep working in the copy and merge again later.
- Reset to folder deletes the changes and starts the copy again from the folder as it is now. The copy stays.
- Discard deletes the changes and removes the copy, so the chat cannot go on editing in it. The copy's branch is kept only when it holds commits the folder does not have, and meshpool tells you its name.

A merge never overwrites your work. meshpool works out the result first and changes nothing when:

- the changes conflict with the folder's own changes to the same lines;
- the folder has uncommitted changes to files the merge would change;
- the folder is in the middle of a merge, rebase, cherry-pick or revert, or is not on a branch.

The panel lists the files in the way. For a plain copy, a file is merged only when the folder's file is still as it was when the copy was made; otherwise nothing is merged.

When you delete a chat, its copy goes too. Changes that were not merged are committed to its branch first, and the branch is kept. A plain copy with changes is moved to `kept-copies/<id>` in the data folder.

## Sub-agents

When two or more sub-agents may edit files, each works in its own copy, made from the project's files as they are now, unsaved edits included. Read-only sub-agents, and a single sub-agent, work in the project itself. A plain copy is only made of a folder up to 50 MB. When no copy can be made, the sub-agent works in the project.

When they have all reported, the chat applies their changes to the project one by one, in task order, without a commit, so the turn's diff and Undo cover them. Changes that conflict with an earlier sub-agent's are not applied. The report says which files, and the work is kept on the copy's git branch, or in `kept-copies/<id>` for a plain copy.

## Scheduled tasks

A scheduled task whose folder is a git repository runs each time in a new copy, made from the folder's last commit. A run never edits the folder under an open chat or under an earlier run. The run's chat has the Isolated copy panel, with Merge, Reset to folder and Discard. A task with no folder, or a folder without git, runs in the folder itself, and so does a run whose copy could not be made.

## In the terminal

```bash
meshpool -w
```

`-w` (or `--worktree`) starts a new session in a copy of the current folder, or of the folder `--dir` names. It also works with `-p`. It cannot be combined with `-c` or `--resume`. When the session ends, meshpool says how many files changed and how to review, merge or discard them.

```bash
meshpool worktree list
```

```bash
meshpool worktree diff ID --stat
```

```bash
meshpool worktree merge ID
```

```bash
meshpool worktree discard ID
```

- `list` shows the copies of the current folder's repository, with who made each one (a chat, a sub-agent, a scheduled run or the terminal) and how many files it changed. `--all` shows every copy.
- `diff` prints the changes, and `--stat` lists only the files.
- `merge` works like Merge in the app and lists any files in the way.
- `discard` deletes the copy's changes and removes the copy. Its branch is kept only when it has commits the folder does not have.

`/worktree` in a terminal session runs the same commands: `/worktree list`, `/worktree diff ID`, and so on.

## Clean-up

At start, meshpool removes copies whose making never finished, copies whose folder is gone, copies of deleted chats, and copies left by sub-agents and scheduled runs whose process has ended. Work that was not merged is kept on its branch, as above. Copies of chats that still exist are kept.

## Safety

meshpool does not make a copy of a repository whose own git settings run programs, such as filters, fsmonitor or an external diff. The repository's git hooks do not run while meshpool makes, merges or removes a copy.

## Submodules

In a repository with git submodules, meshpool sets them up in each new copy. If that fails, for example without a network, the copy still works and meshpool says how to set them up by hand.

## Hooks

`WorktreeCreate` runs after meshpool makes a copy, for example to install dependencies in it. `WorktreeRemove` runs after a copy is discarded, whether by you, by a deleted chat, after sub-agents finish, or by the clean-up at start. Both run in the folder the copy came from, and get a `worktree` object on standard input with its `id`, `kind` (`git` or `copy`), `workdir`, `path`, `branch`, `source` and `title`. They may take up to 300 seconds unless the hook sets its own `timeout`. A failing hook never stops a copy being made or removed. See [hooks](hooks.md).
