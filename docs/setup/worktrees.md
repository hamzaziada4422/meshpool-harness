# Worktrees

A worktree is a second checkout of the same git repository, in its own folder and on its own branch. A task that works in a worktree never edits the files you have open, and several tasks can work at once without touching each other's changes. meshpool keeps its worktrees in `.meshpool/worktrees/<name>` inside the repository, on branches named `meshpool/<name>`, and adds that folder to `.git/info/exclude` so it never shows up as a change.

## Start a task in a worktree

- In the app: the plus menu in a new chat, Work in a new worktree.
- In the terminal: `meshpool -w` (or `meshpool -w name`). When you leave, meshpool asks whether to keep the worktree or remove it.
- For an open chat: the menu behind the chat's name, Move to a new worktree. The conversation goes with it, and you choose whether your uncommitted changes go too. Move back to the main checkout works the same way.

Files that a `.worktreeinclude` file lists (glob patterns, for example `.env`) and that are not in git are copied into each new worktree. Links are never followed.

## Sub-agents and scheduled tasks

- A sub-agent started with `isolation: "worktree"` works in its own worktree, so sub-agents editing code at the same time never collide. A worktree it did not change is removed; a changed one is kept, and its report names the branch.
- A scheduled task with "Each run in a new worktree" starts every run in a new worktree of its folder, so unattended runs never change your working copy. If a worktree cannot be made, the run does not start and says why.

## See and clean up

- The Git panel lists meshpool's worktrees with their branch and whether they have uncommitted changes or commits of their own. From there you can start a new chat in one, make a new one, or remove one. Removing asks first, and uncommitted changes are only discarded when the dialog says so.
- In the terminal:

```bash
meshpool worktree list
```

```bash
meshpool worktree remove NAME
```

```bash
meshpool worktree prune --keep 5
```

`prune` removes the oldest worktrees beyond the newest five, but only ones with no uncommitted changes and no commits of their own. `MH_WORKTREE_KEEP=5` does the same each time a worktree is made.

## Options

- `meshpool worktree add NAME --base remote` starts from the remote's default branch (fetched first); `--base main` or a commit starts from there. The default is the current commit.
- `--sparse src,docs` checks out only those folders, which is much faster in a big monorepo.
- Each worktree is locked while meshpool uses it, so `git worktree prune` never drops it; removing it through meshpool unlocks it.

## Safety

- In a worktree chat, a command that reaches into the main checkout (its path, `git -C`, `--git-dir`, `GIT_DIR=`) always asks first, whatever the permission mode.
- meshpool does not make worktrees in a repository whose own git settings or hooks run programs, and git hooks are off while it makes one.
- Hooks: `WorktreeCreate` runs after a worktree is made (for example to install dependencies in it) and `WorktreeRemove` after one is removed. They get the worktree's name, path and branch as JSON on standard input.
