# meshpool 0.0.18 bug ledger

Bugs found after 0.0.17 shipped, by six bug hunts (one per area: CPU, web UI, agent core, office, server, command line)
and by the lead. Each hunt worked in its own worktree. It wrote a test that failed before its fix, then reverted the
fix once to see the test fail again. Rows from the 0.0.17 ledger were not counted again.

## Summary

| Area | fixed | with a test | found, not fixed |
|---|---|---|---|
| CPU and process waste (CPU) | 13 | 13 | 2 |
| Web UI (UI) | 13 | 13 | 1 |
| Agent core and tools (AG) | 15 | 15 | 2 |
| Office files and engines (OF) | 19 | 19 | 2 |
| Server and security (SV) | 12 | 12 | 4 |
| Command line and setup (CL) | 13 | 13 | 3 |
| Lead (LD) | 8 | 7 | 0 |
| all | 93 | 92 | 14 |

The one fix without a test is LD-07. The race behind it could not be made to happen reliably.

## Lead

| ID | Where | What was wrong | Test |
|---|---|---|---|
| LD-01 | lo_session.py, lo_worker.py, forge/common.py, sheets/qa.py | Every conversion, render, recalculation and preview started a fresh LibreOffice with a new profile. Now one kept LibreOffice serves them all. On 8 files, CPU went from 4.75 s with 8 starts to 1.66 s with 1 start. | test_lo_session.py (12 tests) |
| LD-02 | agent/office_mcp_server.py convert_document | soffice ran without a profile of its own. A LibreOffice the user had open took the job over and then quit (also OF-12). | test_convert_document_never_borrows_the_users_libreoffice |
| LD-03 | agent/core.py _recalc_formulas | After converting to .xlsx, the code looked for `<name>.xlsm`, so an .xlsm workbook was never checked. | test_lo_session.py (recalculation) |
| LD-04 | scheduler_os.py | If `crontab -l` failed for any other reason than "no crontab", install and uninstall rewrote the crontab from an empty list. That deleted the user's other entries. | test_a_crontab_that_cannot_be_read_is_never_rewritten |
| LD-05 | core/tools.py Workspace._cwrite | The file path was put inside a `sh -c` string, so a name containing `$(...)` ran as code in the container. | test_container_writes_pass_the_path_as_data_not_as_shell_code |
| LD-06 | ui/shell.js renderBranch | Every header redraw ran git status on the server. 10 redraws meant 10 git runs. | test_header_redraws_do_not_run_git_status_each_time |
| LD-07 | ui/workbench.py term_run | Two terminal commands started at once raced on the command list, which could return a 500. | none (not reproducible on demand) |
| LD-08 | view.py, engines/icons | view_output pictures of a/report.pdf and b/report.pdf overwrote each other. Icon temp files were named by process only. | test_view_pictures_of_two_files_with_the_same_name_do_not_overwrite_each_other |

## CPU and process waste (app/tests/test_cpu018.py)

| ID | Where | What was wrong (before, then after) |
|---|---|---|
| CPU-01 | hrouter/profiles.py | Every model lookup parsed the profile YAML files: 186 parses for 31 lookups, now 6. |
| CPU-02 | core/modelcaps.py | model_hints.json was read on every request: 20 reads for 20 requests, now 0. |
| CPU-03 | trust.py | Each chat poll re-read the trust stores: 350 JSON reads for 25 polls, now 0. |
| CPU-04 | agent/extras.py | The hooks files were read before and after every tool call: 100 reads in a 10-call turn, now 0. |
| CPU-05 | project_store.py, ui/agent_api.py | Each sidebar refresh read 4 files of every project: 960 reads for 10 refreshes, now 0. |
| CPU-06 | ui/previews.py | Each page image opened the PDF twice: 16 opens for 8 pages, now 8, and 0 when cached. |
| CPU-07 | ui/files_api.py | The open editor hashed the whole file every 4 s: 15 reads in 15 checks, now at most 1. |
| CPU-08 | ui/files_api.py | The @ symbol picker re-read every source file on each keystroke: 360 reads for 9 keys, now 0. |
| CPU-09 | ui/files_api.py | The picker ran git ls-files twice per key: 9 runs for 5 keys, now 1. |
| CPU-10 | agent/core.py | Each exec_command walked the project tree 4 times: 12 walks for 3 commands, now 6. |
| CPU-11 | hrouter/packs.py | Each .py, .json, .toml or .yaml edit started a Python process for the syntax check: 7 processes for 7 checks, now 0. It compiles only and never runs the file. |
| CPU-12 | core/tools.py | A .js edit ran node --check 3 times: 11 runs for a write and 3 edits, now 4. |
| CPU-13 | ui/workbench.py | The session panel re-scanned every tool output on each refresh: 55 scans for 10 refreshes, now 10. |

## Web UI (app/tests/test_ui_bugs018.py)

| ID | What was wrong |
|---|---|
| UI-01 | A reopened chat gave every step its own group, finished groups kept spinning, and approvals fell out of their group. |
| UI-02 | A reopened chat drew a new "Sub-agents" box for each progress event. |
| UI-03 | A draft typed on a project page was restored in New chat instead, and lost from the project on reload. |
| UI-04 | A step cut short by Stop showed as failed and was counted as failed. |
| UI-05 | Terminal output after a tab switch went to a detached element, and Stop was hidden while the command still ran. |
| UI-06 | A long-running terminal command was polled 4 times a second for good, even with the panel closed. |
| UI-07 | A slow Git tab drew git status over the tab opened after it. |
| UI-08 | A slow Changes tab threw an error after the tab was left. |
| UI-09 | Enter on the folder picker's Cancel or Use button opened the highlighted folder instead. |
| UI-10 | The Progress tab lost the steps from before a message added while the agent worked. |
| UI-11 | Copy and Edit of a message with a picture included the picture's file name as words. |
| UI-12 | A refused "Make default" in Settings, Models showed nothing. |
| UI-13 | A project path inside a link or a Mermaid diagram broke the reply's markup. |
| UI-14 | Same bug as LD-06, counted there. |

## Agent core and tools (app/tests/test_fix_ag_0018.py)

| ID | What was wrong |
|---|---|
| AG-01 | Line numbers in read_file and grep went wrong after a form feed or U+2028. |
| AG-02 | apply_patch could not match the first line of a file starting with a BOM. |
| AG-03 | The output of a command that timed out reached the model without secret redaction. |
| AG-04 | bash_start and bash_output were not redacted. |
| AG-05 | Background command output was garbled on Windows (read in cp1252, not UTF-8). |
| AG-06 | The read-only shortcut in Ask mode ignored the command's workdir, so `cat id_rsa` in ~/.ssh ran without a question. |
| AG-07 | exec_command, probe and trace could write hooks.json, permissions.json or mcp.json without asking. |
| AG-08 | "Allow in this chat" for `uv run pytest` saved `uv run`, which then covered `uv run bash -c ...`. |
| AG-09 | The "killed twice" loop guard fired on any output containing "timed out". |
| AG-10 | Compaction kept no files, commands or plan in the default code lane. |
| AG-11 | `@file` imports in nested AGENTS.md and path-scoped rules could follow paths outside the project. |
| AG-12 | A hook that timed out left the programs it started running. |
| AG-13 | glob walked all of node_modules, build and .venv before dropping them. |
| AG-14 | read_file on a big file numbered and copied every line: a 135 MB log took 3.8 s and wrote a 152 MB copy. |
| AG-15 | A sandboxed command on Linux could write meshpool.config, permissions.json or hooks.json. |

## Office files and engines (app/tests/test_of_bughunt.py)

| ID | What was wrong |
|---|---|
| OF-01 | Word replace_text overwrote the wrong text after a hyperlink. |
| OF-02 | Linked headers and merged cells were replaced twice, and a read added empty header parts. |
| OF-03 | Text in Word content controls (a table of contents, form fields) was not read. |
| OF-04 | Tracked insertions were dropped from reads. |
| OF-05 | The footer was not read when the header had text. |
| OF-06 | Footnotes and endnotes were not read. |
| OF-07 | CSVs in Windows-1252 or UTF-16 lost characters or read as garbage. |
| OF-08 | The RTF reader dropped accented characters and kept font names. |
| OF-09 | A password-protected Office file gave a raw error, and its preview said "damaged". |
| OF-10 | view_output drew a semicolon CSV as one column and read the whole file. |
| OF-11 | Big logs and HTML files were read whole before being cut. |
| OF-12 | convert_document used the user's default profile, and a timeout left soffice.bin running. |
| OF-13 | A workbook with a chart sheet could not be read at all. |
| OF-14 | A failed edit left a half-made copy, and a failed in-place save could destroy the original. |
| OF-15 | The hidden-text check read reference.txt in the system encoding (wrong on Windows). |
| OF-16 | PowerPoint replace_text skipped tables and groups, and went wrong after a line break. |
| OF-17 | Best-of deck grading left temp folders behind. |
| OF-18 | Hidden slides were read like shown ones. |
| OF-19 | Two requests for the same preview page raced on one temp file. |

## Server and security (app/tests/test_sv_server_b018.py)

| ID | What was wrong |
|---|---|
| SV-01 | /files served files outside the runs folder through an encoded `..` run id. |
| SV-02 | /api/memory/exclude could write meta.json outside the chats folder. |
| SV-03 | A silent connection held a server thread until the app quit. |
| SV-04 | Deleting a chat wrote a second HTTP response into its open event stream. |
| SV-05 | Office Install renewed the certificate but kept serving the old one. |
| SV-06 | The desktop app wrote the full launch URL, key included, into app.log. |
| SV-07 | A certificate that does not load was kept for good. |
| SV-08 | /api/hub/account built the chat list two or three times per request. |
| SV-09 | Every chat view re-read the trust files. |
| SV-10 | An editor save that could not write returned a 500 with a raw PermissionError. |
| SV-11 | The browser was opened with the app's lifetime launch key on its command line. The key is now single-use. |
| SV-12 | A blank folder path became the app's own working folder. |

## Command line and setup (app/tests/test_cl_b018.py)

| ID | What was wrong |
|---|---|
| CL-01 | Error text was cut before the key was removed, so part of a key could show. |
| CL-02 | Requests refused with a 4xx (such as a bad key) were sent again until retries ran out. |
| CL-03 | doctor called a settings file with a BOM broken. |
| CL-04 | doctor crashed on a permissions.json whose "projects" was a list. |
| CL-05 | `meshpool mcp --json` crashed. |
| CL-06 | Slash commands split quoted words and Windows paths with spaces. |
| CL-07 | The key relay read an unread request body as the next request. |
| CL-08 | Every key press in the terminal read the settings and sign-in files. |
| CL-09 | `meshpool -c` resumed an empty chat instead of the last real one. |
| CL-10 | Thai, Devanagari and other marks were counted as a column, so lines wrapped too early. |
| CL-11 | On Windows the letter à was lost when typed or pasted. |
| CL-12 | `--settings FILE` kept the quotes around values and refused files with a BOM. |
| CL-13 | Unknown options were ignored silently, and `schedule add` had no `--cron`. |

## Found, not fixed

Needs the owner (a design decision):
- SV: The Office panel token is in taskpane.html, which any local process can fetch without a token. Office loads the
  page without a secret, so closing this needs a different design.
- SV: The mp_s cookie is not tied to a port. Another server on 127.0.0.1 opened in the same browser receives it.
- SV: The launch key printed in the terminal in browser mode still lasts for the app's life (testkit scripts reuse it).

Left for a later pass:
- AG: A command run in a container keeps running inside the container after a timeout. Only the docker client is
  killed.
- OF: Excel edits through openpyxl drop shapes, form controls, slicers, threaded comments and data bars.
- OF: merge.py could reuse non-image parts under names that collide with parts in the target deck.
- CPU: The session panel still runs about 7 git processes per refresh. The cache lasts 1.5 s.
- CPU: The page still polls for an update every 60 s while hidden.
- CL: `meshpool run-due` exits 0 even when a task failed to start.
- CL: `permissions list` does not show the ask rules.
- CL: providers.py reads the settings file several times per model call.
- SV: Uploads under runs/_uploads are never removed.
- UI: The insight nudge builds a selector from an id without escaping it (not proven to happen).
- AG: The macOS sandbox exposes the data folder only if MH_SANDBOX_WRITE is set to cover it.
