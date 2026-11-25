---
name: git-help
description: 'Do git work: commit, branch, merge, undo a mistake, resolve a conflict or write a pull request. Use when the user asks about git or a repository history.'
---

# Git help

1. Look first: git status, git log --oneline -10, git diff --stat. Explain what you see in one line.
2. Never run a destructive command (reset --hard, push --force, clean, branch -D) without saying exactly what will be lost and getting a yes. Prefer a new branch or a stash.
3. Commit messages: a short imperative line, then why it changed. Do not push unless asked.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
