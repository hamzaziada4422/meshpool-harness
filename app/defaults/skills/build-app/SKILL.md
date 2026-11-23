---
name: build-app
description: Build a small working app or tool: a calculator, tracker, form, to-do list, timer, notes, board, converter or anything with buttons and saved data. Use when the user asks for an app or a tool that does something.
---

# Build app

An app is done when every feature a person expects from it works, not when the page renders.

1. Write the feature list first, as your todo plan. Include the features every version of this kind of app has, not only the words of the request. Examples:
   - to-do list: add with Enter, complete, edit, delete (with undo or confirm), filter all/open/done, counts, clear done, empty state, saved on reload
   - tracker or log (expenses, habits, workouts): add with date and category, edit, delete, totals by period and category, sort and filter, CSV export, empty state, saved on reload
   - timer or clock: start, pause, resume, reset, settings, the tab title shows the time, a sound or alert at the end, keeps true time when the tab is in the background (use timestamps, not tick counts)
   - notes or board (kanban): create, edit, delete, search or filter, drag and drop AND a keyboard or button way to do the same, saved on reload
   - calculator or converter: every key and keyboard input, bad or empty input handled without NaN, copy result
2. One HTML file with inline JavaScript unless the user names a framework. Read the build-ui skill and copy its starter.html for the look. Save data in localStorage under one versioned key, load it on start inside try/catch, and offer export. Build one place that renders state from data, so every action goes through the same path.
3. Inputs: trim, reject empty and nonsense with a message next to the field, never NaN or "undefined" on screen. Insert user text as text (textContent), never as HTML. Long words must wrap on a phone.
4. Every control has a label and a keyboard path (Enter submits, Escape cancels, buttons are real buttons). Destructive actions can be undone or confirmed. Empty, error and success states exist and read well.
5. Test it by using it. Run `ui_audit`, then `ui_smoke` (it clicks every control, submits bad input, reloads) and fix every item. Then tick off your own feature list in a real browser (browser_* tools or a Playwright script): for each feature, do it, see the result, reload, and see the data is still there. Fix what fails and repeat. Do not report done while a listed feature is untested.
6. For a framework app, scaffold it, run it with bash_start on a local port, run `ui_audit` and `ui_smoke` on that address, then stop it with bash_stop. Put run steps in a short README.

Reply with the file path, how to use it in two sentences, and the checks you ran (and any feature you chose to leave out and why).

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done.
