---
name: build-app
description: Build a small working app or tool: a calculator, tracker, form, to-do list, converter or anything with buttons and saved data. Use when the user asks for an app or a tool that does something.
---

# Build app

1. One HTML file with inline JavaScript unless the user names a framework. Save data in localStorage and add an export button.
2. Read the build-ui skill for layout and checks. Every control has a label and a keyboard path. Empty, error and success states exist.
3. Test it: open it with the browser tools, use it the way a person would, and confirm the data survives a reload.
4. For a framework app, scaffold it, run it with bash_start on a local port and audit that address, then stop it with bash_stop.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
