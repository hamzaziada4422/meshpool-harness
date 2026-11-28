# meshpool 0.0.20 bug ledger

0.0.20 includes the fixes planned for 0.0.19 (never published): what a user hit in the released 0.0.18 app, and what an independent review found in those fixes.
Each row names the test that checks it. Each fix's test was seen to fail on the old code and pass on the new.

## Summary

| Area | fixed | with a test |
|---|---|---|
| Browser use (BR) | 4 | 4 |
| Bundled programs and paths (BP) | 6 | 6 |
| Tool health check before a release (TH) | 2 | 2 |
| Fixes learned from other agent apps (UP) | 6 | 6 |
| Resizable panes (PN) | 3 | 3 |
| First hour, from a new user's run of 0.0.18 (FH) | 10 | 10 |
| Release review (RV) | 9 | 9 |
| Flaky UI tests (FL) | 6 | 6 |

## Rows

| Id | What was wrong | Test |
|---|---|---|
| BR-01 | On macOS every browser tool failed: it looked for chromium_headless_shell, which the app does not ship. It now starts the app's own full Chromium, headless, on every system. | test_browser_bundled_chromium.py |
| BR-02 | The browser was looked for only in the user's cache. The app's own vendor folder is searched first. | test_browser_bundled_chromium.py |
| BR-03 | A failed launch gave a bare Playwright error. It now names every program tried and what to do. | test_browser_bundled_chromium.py |
| BR-04 | A broken screenshot could reach the chat as an image. It is checked first. | test_browser_bundled_chromium.py |
| BP-01 | Bundled Git on Windows was not found in its ucrt64, mingw64 and clangarm64 folders. | test_bundled_layouts.py |
| BP-02 | `python3` did not exist in the Windows build. | test_bundled_layouts.py |
| BP-03 | Office fonts bundled with the app were not used. | test_bundled_layouts.py |
| BP-04 | pytest was not bundled, and the bundled Python's commands broke after the app was moved. | test_bundled_pytest.py |
| BP-05 | Linux had no command sandbox unless bubblewrap was installed. The app ships a static one. | test_sandbox_bundled.py |
| BP-06 | HTTPS did not trust the system's certificates, so company proxies failed. truststore is on at start-up. | test_system_trust.py |
| TH-01 | No check called each tool before a release. tool_health calls all 90 on the built app. | test_tool_health.py |
| TH-02 | Web fetch ignored MH_ALLOW_PRIVATE_FETCH, so the check could not reach its local test page. | test_tool_health.py |
| UP-01 | A tool argument of the wrong type (a list sent as text) failed. It is corrected first. | test_upstream_lessons.py |
| UP-02 | Background git took the index lock and could block the user's own git. | test_upstream_lessons.py |
| UP-03 | Raw OpenRouter, context-length and certificate errors reached the chat. | test_upstream_lessons.py |
| UP-04 | MCP servers could not list the tools to offer or hide. | test_upstream_lessons.py |
| UP-05 | A plain read after `cd FOLDER &&` inside the project asked for approval. | test_upstream_lessons.py |
| UP-06 | MCP argument coercion failed for a server whose tool list was not loaded. | test_delegate.py |
| PN-01 | The sidebar, Progress and preview could not be resized. | test_ui_panes_resize.py |
| PN-02 | Resizing lost the reading position. | test_ui_panes_resize.py |
| PN-03 | The handles sat on top of the scrollbars. | test_ui_panes_resize.py |
| FH-01 | Chat and Code could not search the web or fetch a page when asked. | test_first_hour.py |
| FH-02 | Undo left the reply's unpushed commits in place. | test_first_hour.py |
| FH-03 | Approval titles were vague. | test_first_hour.py |
| FH-04 | An attached PDF was not treated as the chat's own file. | test_first_hour.py |
| FH-05 | Chats could not be searched. | test_first_hour.py |
| FH-06 | Reveal in folder failed. | test_first_hour.py |
| FH-07 | The model service being down or back was not shown. | test_first_hour_ui_points.py |
| FH-08 | Chat titles and onboarding points from the new user's run. | test_first_hour_ui.py, test_first_hour_ui_points.py |
| FH-09 | A deck with no source file could carry made-up figures unmarked. | test_deck_unsourced_figures.py |
| FH-10 | The README's Codex table times did not match the raw rows. | test_ledger017_agentcore.py (K-04) |
| RV-01 | A leading `cd` with a newline or braces could make a read outside the project run without a question. | test_upstream_lessons.py |
| RV-02 | MCP arguments were typed after the approval check, so a list sent as text slipped past it. | test_upstream_lessons.py |
| RV-03 | Undo could drop a pushed commit when a reply made two. Undo also ran the repository's hooks. | test_first_hour.py |
| RV-04 | An approval title for a path climbing out of the folder read "in this folder". | test_first_hour.py |
| RV-05 | A tool turned back on in MCP settings stayed hidden; a read-only server was offered write tools. | test_upstream_lessons.py |
| RV-06 | The tool check reported a working sandbox as a failure. | test_sandbox_bundled.py |
| RV-07 | test_system_trust failed on Python 3.10. | test_system_trust.py |
| RV-08 | The Windows and Mac smoke steps had too little time for the tool check. | test_tool_health.py |
| RV-09 | A merge left an unguarded tool lookup in the delegate path. | test_delegate.py |
| FL-01 | UI tests used a wait that does not await a Promise. | uiwait.py users |
| FL-02 | The tips test read the setting before it was saved; the app drew a tip during the save. | test_omar_r3_ui.py |
| FL-03 | The picture test used a file that did not exist. | test_round3_020.py |
| FL-04 | The sidebar redraw test used a fixed wait. | test_ux018.py |
| FL-05 | Page helpers returned before start-up finished. | test_first_hour_ui_points.py, test_robust_waits.py |
| FL-06 | A fake chat's 404 drew "This chat is gone." under the test's chat. | test_first_hour_ui_points.py |

## Still open

- The right pane's 8 px handle covers the left edge of the preview's text editor.
- If a picture shown large fails to load, no file card is shown in its place.
- A quoted output file name with a space gets the approval title "Write my?".
- checks._latest_details can keep a stale detail recorded within 1 s of a later pass.
- The bundled MinGit has no sleep.exe, so `sleep` fails on Windows without Git for Windows.
- OCR is not bundled (its library needs macOS 13).
