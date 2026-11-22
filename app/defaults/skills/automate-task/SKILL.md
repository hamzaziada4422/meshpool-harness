---
name: automate-task
description: Write a script or schedule that does a repeated job on its own. Use when the user says every day, every week, automatically or keeps doing the same thing by hand.
---

# Automate task

1. Restate the job in one sentence: what starts it, what it does, where the result goes.
2. If it is a prompt Meshpool can run, use schedule_task. If it needs code, write a small Python script with clear settings at the top and run it once to prove it works.
3. Never put passwords or keys in a script; read them from environment variables and tell the user where to set them.
4. Say how to stop it.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
