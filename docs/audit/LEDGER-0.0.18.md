# meshpool 0.0.18 bug ledger

Bugs found after 0.0.17 shipped, by six bug hunts (one per area: CPU, web UI, agent core, office, server, command line)
and by the lead. Each hunt worked in its own worktree. It wrote a test that failed before its fix, then reverted the
fix once to see the test fail again. Rows from the 0.0.17 ledger were not counted again.

A second round followed. Five persona testers ran this build against 0.0.17 on the live model (see Persona testing).
Their findings went to hunts for error messages (ER), functional fixes (PF), workflow (WF), office and first run (SR),
UI smoothness (UX), the developer command line (OM) and approvals (AP). A pre-release review (RR) and the merges (MG)
added the rest. A row that repeats another is listed but counted once, where the row says.

A third round followed on the same branch: the design pass (US, UV), the look review step (RV, UXR, VF, LP2) and the
independent persona reviewer (PR). The feature rows, UXR-01 and PR-01, count as fixed with tests like the rest.

Four more persona rounds followed on the same branch, with the testers on the live model (R1 to R4), then a review of
those fixes (RF) and a few robustness fixes (RB). Each row names its test.

## Summary

| Area | fixed | with a test | still open |
|---|---|---|---|
| CPU and process waste (CPU) | 13 | 13 | 0 |
| Web UI (UI) | 13 | 13 | 0 |
| Agent core and tools (AG) | 15 | 15 | 0 |
| Office files and engines (OF) | 19 | 19 | 0 |
| Server and security (SV) | 14 | 14 | 2 |
| Command line and setup (CL) | 13 | 13 | 0 |
| Lead (LD) | 8 | 7 | 0 |
| Later pass (LP, LQ) | 12 | 12 | 3 |
| Release check (RC) | 1 | 1 | 0 |
| Error messages (ER) | 24 | 24 | 2 |
| Functional fixes from the persona runs (PF) | 8 | 8 | 3 |
| Workflow: folders, outputs and Undo (WF) | 16 | 16 | 1 |
| Office and first run (SR) | 12 | 12 | 1 |
| UI smoothness and polish (UX) | 40 | 40 | 3 |
| Developer command line and Code panel (OM) | 9 | 9 | 1 |
| Approvals (AP) | 7 | 7 | 1 |
| Pre-release review (RR) | 12 | 12 | 0 |
| Merge fixes (MG) | 3 | 2 | 0 |
| Design pass (US) | 17 | 17 | 0 |
| Design pass check (UV) | 5 | 5 | 0 |
| Look review step (RV, UXR) | 4 | 4 | 1 |
| Fixes from the review step's live check (VF) | 5 | 5 | 0 |
| Review step polish (LP2) | 6 | 6 | 2 |
| Independent persona reviewer (PR) | 5 | 5 | 1 |
| Layout jumps (JT) | 6 | 6 | 0 |
| Persona round 1: Priya on the release build (R1) | 18 | 18 | 0 |
| Persona round 2 (R2) | 63 | 63 | 0 |
| Persona round 3 (R3) | 69 | 69 | 0 |
| Persona round 4 (R4) | 21 | 21 | 0 |
| Release review of the persona fixes (RF) | 23 | 23 | 0 |
| Robustness and others (RB) | 3 | 2 | 0 |
| all | 484 | 481 | 21 |

"Still open" counts what the "Found, not fixed" section lists today: two questions for the owner and nineteen known
limits and open bugs, all open for 0.0.19. What the first pass found but left was fixed in the later pass.

Rows listed but not counted: WF-E (counted under WF-07), SR-12 (under UX-20), SR-14 (under AP), AP-08 (under UX-33),
OM-07 (not a bug) and UI-14 (under LD-06).

Three fixes have no test. LD-07: the race behind it could not be made to happen reliably. MG-02: a word list. RB-03: a
one-line guard for an empty command list.

The later pass fixed the items the first pass found but left, and closed PA-skills-13 from the 0.0.17 ledger (three
tests that slept a fixed time now wait for an event).

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
| SV-13 | The mp_s session cookie reached every other server on 127.0.0.1 the user opened in the same browser, since cookies are not scoped by port. That server could replay it to GET / and read the API token. The cookie alone now gets a page without the token. The token comes back only when the cookie arrives with a per-launch page secret that the meshpool page keeps in localStorage, which is scoped to its own origin and port. |
| SV-14 | The Office panel token was baked into taskpane.html, which any local process can fetch without a token. The page now carries none: the panel pairs, the user allows its code in the app, and only then does it get a session token (ui/office_pair.py; app/tests/test_sv_office_pair.py). |

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

## Later pass (app/tests/test_lp_0018.py, test_lq_bugs018.py, test_lq_ui018.py)

| ID | What was wrong |
|---|---|
| LP-01 | A command run in a container kept running inside it after a timeout or Stop, and its output was not capped. |
| LP-02 | `meshpool run-due` exited 0 even when a task could not start. |
| LP-03 | `meshpool permissions list` did not show the ask rules. |
| LP-04 | One round of provider lookups read the settings file 18 times. Now 0 while it is unchanged. |
| LP-05 | Uploads under runs/_uploads were never removed. Now an upload is removed after 30 days, unless a company profile or project points at it. |
| LP-06 | On macOS, MH_SANDBOX_WRITE covering the data folder let sandboxed commands write meshpool's settings. |
| LP-07 | PA-skills-13: three tests slept a fixed time before a negative check. Each now waits for an event. |
| LQ-01 | Excel edits through openpyxl dropped shapes, form controls, slicers, threaded comments, data bars and sparklines, including from the user's own file. Such a workbook is now edited as a copy, and the result names what the copy lacks. |
| LQ-02 | Merging decks wrote tags and media twice under the same name, and a slide link pulled in the source deck's slide, layout and master. |
| LQ-03 | Each Session panel refresh ran 8 git processes: 40 for 5 refreshes, now 5. An edit still shows on the next refresh. |
| LQ-04 | The update status was polled every minute while the tab was hidden: 5 requests in 5 hidden minutes, now 0. |
| LQ-05 | An insight card whose id held a quote never showed. |

## Release check (app/tests/test_work_office_c171.py, testkit/office_panel.js)

| ID | What was wrong |
|---|---|
| RC-01 | Office panel chats were caught by the Codex office route (the panel's preamble names Excel), and the Work route had no ask_user tool: a question asked in the panel failed with "unknown tool". Panel chats now stay on the Work route with ask_user. Found by the live release check; office_panel.js passes 22 of 22. |

## Error messages (app/tests/test_err018.py, test_err_ui018.py)

From commits 362b5aba and ca5f0bf1. core/errtext.py turns the text a failed call already holds into the service's own
words, the host and model, and the next step. It sends no request and keeps the key out.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| ER-01 | model reply | A 200 answer with an error body (`error`, `detail` or `message`) reached the chat as "meshpool HTTP 200 but not a model reply: {...}". | The chat names the host and model, quotes the service and says what to check: the model name and address, or the key for a sign-in error. | test_er01_200_error_body_names_the_service_words, test_er01_200_auth_error_points_at_the_key | fixed |
| ER-02 | model reply | A 200 web page (a proxy sign-in, a router) showed as raw HTML. | It says the host sent a web page, gives the page's title and points at the endpoint address. | test_er02_200_html_page_is_named_as_a_web_page | fixed |
| ER-03 | model reply | A 200 with an empty body said the endpoint "answers small requests". | It says the reply was empty and what to check. | test_er03_200_empty_body_says_the_reply_was_empty | fixed |
| ER-04 | model reply | A reply stream that closed in the middle gave no plain reason. | It says the reply stopped before it was complete, and that a proxy may be cutting long replies. | test_er04_cut_stream_says_the_reply_stopped | fixed |
| ER-05 | model reply | 4xx bodies of every shape (OpenAI error, FastAPI detail list, message, HTML 404) showed raw. | A plain head with the status in words, then the service's words, host, model and the next step. | test_er05_4xx_shows_service_words_host_model_and_next_step | fixed |
| ER-06 | model reply | The detail after the plain sentence was the raw body. | The detail is the status, host, model and the service's words, with a Retry-After in hours or minutes. A key in a 401 body is masked. | test_er06_server_errors_carry_service_words, test_er06_rejected_key_names_the_host | fixed |
| ER-07 | model reply | A key the endpoint echoed back in its error body reached the chat. | It is hidden. | test_er07_key_echoed_in_an_error_body_is_hidden | fixed |
| ER-08 | model reply | A proxy that asked for a sign-in (407) was not named. | It says the proxy wants a sign-in and how to give one (HTTPS_PROXY with a user and password). | test_er08_proxy_sign_in_is_named | fixed |
| ER-09 | model reply | A connection that got no answer showed `<urlopen error [Errno ...]>`, and the next message lost the reason. | A plain reason (nothing answered, the name could not be found), kept for the next message. | test_er09_connection_refused_is_plain, test_er09_dns_failure_keeps_its_reason_on_the_next_message | fixed |
| ER-10 | ChatGPT | A ChatGPT stream that stopped mid-reply was taken as a whole reply. | It is an error, retried like any passing failure. | test_er10_chatgpt_stream_without_completion_is_an_error | fixed |
| ER-11 | command line | `meshpool check --live` on a 200 web page showed the raw answer. | The same plain words as the chat. | test_er11_check_live_shows_plain_words | fixed |
| ER-12 | office engines | An engine that stopped on a failed model call gave "meshpool HTTP 200 but not a model reply: {...}" as its reason. | A plain reason with host and model. | test_er12_engine_failure_reason_is_plain | fixed |
| ER-13 | app server | The app's own server answered a 500 with a bare Python exception. | Plain words: a file meshpool may not open, a full disk, or what failed and what to do. | test_er13_server_500_is_plain_words, test_er13_unexpected_error_text_says_what_to_do | fixed |
| ER-14 | tool cards | Tool cards showed raw PermissionError, WinError 32 and PackageNotFoundError text, also in a reopened chat. | The card says the file is locked, open in another program or damaged. The model still gets the full text. | test_er14_tool_card_for_a_locked_file, test_er14_tool_card_for_a_damaged_document_and_on_reopen | fixed |
| ER-15 | connection check | The check could not read FastAPI detail lists or 200 error bodies. | It reads every shape and quotes the service. | test_er15_connection_check_reads_detail_lists_and_200_bodies | fixed |
| ER-16 | Office panel | The panel said only "meshpool answered 500." | It says what happened and what to do, or shows the server's own sentence. | test_er16_office_panel_failure_words | fixed |
| ER-17 | model reply | A 200 error body that names a lasting cause (a model that does not exist) was tried 6 times over about 31 s. A quota error kept an internal "QUOTA" prefix. | Reported at once. Passing causes still retry. No internal prefix. | test_er17_permanent_200_error_is_not_retried, test_er17_transient_200_error_is_still_retried, test_er17_quota_200_detail_has_no_internal_prefix | fixed |
| ER-18 | terminal | The terminal pointed to a Settings page it does not have, `-p` said to send "continue", and `meshpool check` said "Ready to use" when the live test failed. | The terminal points to `meshpool setup`, `-p` says to run again with -c, and check says it is not ready yet. | test_er18_terminal_wording, test_er18_check_does_not_claim_ready_when_live_fails | fixed |
| ER-19 | web app | A 5xx with no JSON body showed only "Something went wrong on Meshpool's side". A 200 that was not JSON came back as null, and the caller then failed with "Cannot read properties of null". | The message names the status and what to do. A 200 that is not JSON is an error. | test_er19_a_failed_request_without_an_explanation_still_says_what_happened | fixed |
| ER-20 | web app | A failed memory export saved `{"error": ...}` as what-meshpool-knows.md, with no message. | The failure is shown and nothing is saved. | test_er20_a_failed_memory_export_is_shown_not_saved_as_the_file | fixed |
| ER-21 | first run | The connection check replaced the server's sentence with a guess from its status and words. | It shows the check's own words. | test_er21_connection_check_shows_the_checks_own_words | fixed |
| ER-22 | editor | A save while the app's server did not answer said only "Failed to fetch". | It says meshpool is not responding, nothing was saved and the text is still there. | test_er22_saving_a_file_while_meshpool_is_down_says_so | fixed |
| ER-23 | preview | An image preview that could not load dropped the server's reason. | It shows the reason. | test_er23_an_image_preview_that_cannot_load_says_why | fixed |
| ER-24 | office engines | An engine's "retrying" notice showed the raw reply. | It names the cause: "The model did not answer (502 bad gateway). Retrying for up to 120 s." | test_er24_engine_retry_notice_is_plain | fixed |

## Functional fixes from the persona runs (app/tests/test_pf_persona_fixes.py, test_textcalls_xml.py)

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| PF-01 | agent loop | Tool calls a model wrote as text (`<invoke name=...>`, bare or inside `<function_calls>` or `<tool_calls>`) were neither run nor removed, so the raw XML became the answer. | core/textcalls.py recover() reads that form for offered tools, and strip_markup() removes it from a final answer when tools are off. | test_textcalls_xml.py (7 tests) | fixed |
| PF-02 | office | edit_office_file with in_place wrote over a file another app had open (Halden-memo.docx open in LibreOffice). | It looks for the lock file (`.~lock.<name>#`, `~$<name>` or Word's shortened form) and saves a copy instead, saying the original is open. An explicit output that is open gets a "(copy)" name. | test_pf02_open_file_gets_a_copy_not_an_in_place_write, test_pf02_excel_owner_file_and_a_named_output_that_is_open, test_pf02_no_lock_still_edits_in_place | fixed |
| PF-03 | chat titles | The agent cut the title at 80 characters inside a path, and nice_title then dropped the first folder. "Hi! The file ..." was titled "Hi". | The agent cuts between words. nice_title skips a greeting and shows a path as its file name. | test_pf03_titles_keep_paths_whole_and_skip_greetings, test_pf03_agent_title_does_not_cut_a_path_at_80_characters | fixed |
| PF-04 | onboarding | The box to copy another app's chats was ticked by default. The toast said "1 chats imported". With a key already set, the first card said "Step 2 of 3". | Unticked, with a label that says what it copies. The plural fits, and it counts two steps from step 1. Overlaps UX-21 and UX-30. | test_pf04_chat_import_is_opt_in_with_a_plural_that_fits, test_pf04_a_start_without_the_model_step_says_step_1 | fixed |
| PF-05 | keep-awake | With no D-Bus, systemd-inhibit exited at once and stayed a zombie child for the whole turn, one per turn. | A small thread reaps it. Once it ends by itself it is not started again in this run. | test_pf05_a_holder_that_exits_is_reaped_and_not_started_again | fixed |
| PF-06 | memory | "Write in British English and sign emails as Daniel, Finance Director" was filed under People. | The language, spelling and sign-off of what the user writes are a preference ("How you like work"). Only the rule changed. | test_pf06_writing_instructions_are_how_you_like_work, test_pf06_a_stated_fact_lands_under_how_you_like_work | fixed |
| PF-07 | search | Past-chat search matched the query as one phrase: "dropped" found a chat, "dropped rows" did not. | A chat matches when every word is in it, in any order. | test_pf07_search_finds_every_word_in_any_order | fixed |
| PF-08 | settings | Your data counted every saved session as a chat, so it said "2 chats" next to a list of one. | It counts chats and code sessions the way the sidebar does. | test_pf08_settings_counts_chats_as_the_sidebar_does | fixed |

## Workflow: folders, outputs and Undo (app/tests/test_wf_bugs018.py, test_wf_ui018.py)

From the merge of e169d1d7 (commits 37ad33a0, 497ff202, 7a3d9529, e169d1d7). The hunt used numbers and letters.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| WF-01 | folder picker | After "Folders I choose", the picker's default button was "Use the whole home folder", and a click on a folder only highlighted it. | The button names the folder you click. In onboarding it waits for one. | test_the_folder_picker_uses_the_folder_you_click_and_never_defaults_to_home | fixed |
| WF-A | folders | A folder picked in onboarding only joined the permission list. It was in no sidebar or project picker, and chats worked in a hidden app-data folder (Lina's agent then searched / for her folder). | It becomes a pinned project linked to the folder. New chats start in it, also after a reload. The sidebar has "Add a folder". | test_a_folder_picked_in_onboarding_becomes_a_pinned_project_and_new_chats_work_in_it, test_a_folder_picked_in_onboarding_is_a_project_in_the_sidebar_and_new_chats_start_in_it | fixed |
| WF-02 | attachments | A dropped file that already lived in the chat's folder was copied again at the top level, without a word. | It is used where it is. A file from elsewhere is copied, and the app is told where the copy went. | test_a_dropped_file_already_in_the_folder_is_used_where_it_is | fixed |
| WF-03 | decks | A slide whose content call failed carried the model's error in its speaker notes. Every footer said "Meshpool". | The error goes to the log and the engine's answer. With no brand set, the footer is the page number. | test_a_failed_slide_writes_no_model_error_into_the_speaker_notes, test_a_deck_without_a_brand_has_no_product_name_in_its_footer | fixed |
| WF-04 | decks | The deck showed twice (deck.pptx and the name the user asked for), and deck.pptx went stale after an edit. Build files (qa.json, slides.json and others) showed in Artifacts, Your work and the outputs list, and the footer said "12 files changed" for one deck. | The job's file is moved to the asked name. Build files are left out of every list and count. | test_a_deck_saved_under_the_users_name_is_moved_not_copied, test_build_and_check_files_are_not_the_users_files, test_build_files_are_not_counted_and_a_long_job_shows_its_stage | fixed |
| WF-05 | Your work | "What you worked on" showed "user", "number" and "wanted" with their counts. | Topics are real words used in two chats or more. | test_your_work_topics_are_real_words_not_summary_filler | fixed |
| WF-06 | file cards | A long name ran over the card's edge. After Undo every card lost its thumbnail. | The name fits the card. A thumbnail waits until its card is on the page. | test_file_cards_keep_their_names_inside_and_their_thumbnails_after_a_rebuild | fixed |
| WF-07 | Undo | The Undo line printed the full checkpoints path, and the message Undo put back was sent for skill suggestions as if typed (a "Use $invoice-quote" chip). | The backup path is in the details only. A message put back asks for no suggestions. | test_undo_says_what_it_restored_and_keeps_the_backup_path_in_the_details, test_undo_shows_the_backup_path_only_in_details_and_puts_back_no_skill_chips | fixed |
| WF-08 | preview | The preview had no Download. Its menu offered only Copy path and Show in file manager. | Download saves the file with the browser's own download, through /raw with dl=1. | test_the_preview_and_its_menu_download_the_file_through_the_app | fixed |
| WF-09 | long jobs | A deck took 4 min 51 s and the chat showed only "2 steps". | The engines' stages reach the chat as job_stage events and show in the working line. They are not counted as checks. | test_engine_stages_reach_the_chat_as_job_stage_events_not_checks, test_build_files_are_not_counted_and_a_long_job_shows_its_stage | fixed |
| WF-C | Undo | Undo said "Files are back to how they were" while a file outside the chat's folder kept the agent's change. | Undo names what it restored and what it could not. With the project folder as the chat's folder, it puts the user's own file back. | test_undo_says_what_it_restored_and_keeps_the_backup_path_in_the_details, test_undo_restores_the_project_folder_and_names_writes_outside_it | fixed |
| WF-D | Undo | After Undo, the preview still showed the file Undo had removed. | Undo closes that preview. | test_undo_closes_the_preview_of_a_file_it_removed | fixed |
| WF-E | Undo | The skill chips of WF-07, seen in Lina's run. | Same fix as WF-07. | as WF-07 | fixed, counted under WF-07 |
| WF-G | Artifacts | "Browse files" only scrolled to a list already on screen. | It opens the folder's file list in the app. | test_browse_files_opens_the_folder_in_the_app | fixed |
| WF-H | files | Sara's dropped Budget_2026.xlsx was edited as a copy in the chat's own folder, and the panel named that folder "dab817694f87". File cards had no Download. Artifacts went stale after Undo, because its cache followed session.json only. | The card says the file is a copy in this chat, and the folder reads by the chat's name. Each card has Download. Artifacts also follow the checkpoints. | test_a_file_in_a_chats_own_folder_says_it_is_a_copy_there, test_file_cards_keep_their_names_inside_and_their_thumbnails_after_a_rebuild, test_artifacts_follow_an_undo_even_when_the_transcript_file_did_not_change | fixed |
| WF-I | projects | Lina's question in a project became a Code session and was gone from the project's list. | A Code session whose folder is a project's linked folder is listed under the project. One taken out on purpose stays out. See also UX-27. | test_a_code_session_in_a_projects_folder_is_listed_under_the_project, test_a_projects_sidebar_row_lists_its_code_sessions | fixed |
| WF-J | Undo | The message Undo put back came back focused, so one Enter sent the undone request again (Maya). | The box is not focused, and a note says the message is a draft. | test_undo_shows_the_backup_path_only_in_details_and_puts_back_no_skill_chips | fixed |

## Office and first run (app/tests/test_sr_*.py)

From the merge of bd0c9393 (commits 6d44d733 to bd0c9393).

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| SR-01 | sandbox | LibreOffice run by the agent's shell in the sandbox crashed on its first start (DeploymentException): the sandbox keeps ~/.config read-only, and LibreOffice writes its profile there. | The sandbox gives it a profile in ~/.cache/meshpool, made once and reused. A command's own -env:UserInstallation still wins. | test_sr_sandbox_office.py (4 tests) | fixed |
| SR-02 | sheet preview | Numbers showed without their format (412000 where Excel shows 412,000, a price as 3.5). At 1024 and 1366 px a long title in A1 made column A so wide that the other columns were out of view. | Numbers show in the cell's format: digit groups, decimals, percent, currency, negative and zero sections. The widest columns give way so the sheet fits, and one that still does not fit scrolls sideways. The price-list case is marked SR-14 in the test. | test_number_formats, test_sheet_preview_fits_and_shows_values_as_formatted | fixed |
| SR-03 | sheet preview | A formula's cell showed its cached value, and nothing told the page what the formula was. | The formula shows on hover. A file never calculated still shows the formula. | test_cached_values_show_in_the_cell_format_with_the_formula_beside, test_a_formula_never_calculated_still_shows_the_formula | fixed |
| SR-04 | sheet preview | Hidden sheets looked like any other sheet. | Their tab is marked. | test_hidden_sheets_are_marked | fixed |
| SR-05 | connection check | A 404 whose reply said "The model `x` does not exist" was reported as "no such address or model. Check the base address". | It says the endpoint has no model named x. | test_explain_names_the_missing_model, test_onboarding_says_the_model_is_missing | fixed |
| SR-06 | onboarding | For an address with no path, onboarding then tried the /v1 variant, and that one's plain 404 replaced the reply that named the model. | It stops trying the variant after such a reply. | test_onboarding_keeps_the_reply_that_named_the_model | fixed |
| SR-07 | Undo | Undo, Rewind and Edit put the internal line "Attached files (in this chat's folder): ..." in the message box, and a second Undo ran two messages together. | The line is taken out and the files come back as chips, named again when the message is sent. A second fill replaces the first. | test_message_put_back_by_undo_has_no_internal_line_and_keeps_its_files | fixed |
| SR-08 | Office panel | A panel chat was titled by the panel's context block ("[I am in Excel, using the me..."). | Titled with the app and the user's words, for example "Excel: What is the Q3 total". | test_sr_panel_title.py (3 tests) | fixed |
| SR-09 | Word | edit_office_file could not make a tracked change to text inside an earlier insertion, or to a phrase that ran into one. On a policy with two reviewers' changes, 9 steps failed. | Plain runs and earlier insertions are searched as one stretch, and the earlier insertion is split around the new text, so earlier revisions stay. Deleted text is never matched. | test_sr_tracked_in_revision.py (3 tests) | fixed |
| SR-10 | ChatGPT sign-in | A codex login started in onboarding kept running more than 16 minutes after the user picked another option. One left by a killed meshpool was never stopped. | It runs under `timeout` where there is one. A new meshpool stops a sign-in whose app is gone. Onboarding stops it when another option is picked or onboarding ends. | test_sr_login_leak.py (4 tests), test_onboarding_stops_the_chatgpt_sign_in_when_another_option_is_picked | fixed |
| SR-11 | chat | While the model service was down, each check added a line (7 for one outage) with the raw "HTTP 0: <urlopen error [Errno 111] ...>", and the status said Thinking. | One line, updated in place ("Checking again in 30 s, attempt 3"). The status says Waiting for the model service. | test_endpoint_down_checks_are_one_updatable_line, test_model_service_down_is_one_line_and_the_status_says_so | fixed |
| SR-12 | chat | The user's bubble showed the internal attachments line. | The files show as chips that open them. | test_user_bubble_shows_attachments_as_chips | fixed, same bug as the first part of UX-20 and counted there |
| SR-13 | billing | Plan and billing said "A Meshpool key is saved on this computer" to someone on another provider or a ChatGPT plan. | It says where usage is billed for each provider. | test_billing_text_matches_the_provider | fixed |
| SR-14 | approvals | Left to the approvals work. | AP-01 to AP-03. | see AP-01 to AP-03 | fixed, counted under AP |

## UI smoothness and polish (app/tests/test_ux018.py)

From the merge of 12a86add (commits 4dc3fa3b, 0713befb, 12a86add). UX-01 to UX-11 measure what moves on the page; UX-12
on come from the persona runs.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| UX-01 | sidebar | The open chat's row and a hovered row had the same fill, so two rows looked marked. | Selected is a step stronger than hover (luminance ratio at least 1.08), and the open row keeps its fill. | test_the_open_chat_stays_marked_while_another_row_is_hovered | fixed |
| UX-02 | sidebar | Any change to the chat list rewrote the whole sidebar: rows were drawn again, hover dropped and unread dots replayed their pop. | Rows are patched in place. A renamed chat costs at most 2 removed elements. | test_a_sidebar_redraw_keeps_the_rows_that_did_not_change | fixed |
| UX-03 | chat | Every frame of a streaming reply threw away the block being written: 840 elements removed in one long reply, and a selection was lost at the next word. | The open block is patched: at most 10 elements removed over the whole reply, and a selection survives. | test_a_streaming_reply_keeps_its_elements_and_the_readers_selection | fixed |
| UX-04 | composer | The composer bar was rebuilt when a run started and ended, so focus was lost and hover flashed. | Only Stop and Send come and go (at most 3 removed elements per run), and focus stays. | test_a_run_starting_and_ending_keeps_the_composer_buttons | fixed |
| UX-05 | header | A new title or run state rewrote the top bar, and the progress button opened and closed in one click. | The header is patched (at most 1 removed element for a new title), and each button has one click handler. | test_a_header_redraw_keeps_its_buttons_and_binds_them_once | fixed |
| UX-06 | chat | An opened chat was painted at its top or middle and jumped to the end a frame later. Some opens stayed at the top. | Once drawn, no frame shows it more than 40 px above where it settles, and every open ends at the end. | test_an_opened_chat_is_painted_at_its_end_and_stays_there | fixed |
| UX-07 | sidebar | Opening or closing the sidebar animated the grid's width, laying out the whole page on every frame for 200 ms. | The columns change once and the sidebar slides with transform and opacity. | test_hiding_and_showing_the_sidebar_lays_the_page_out_once | fixed |
| UX-08 | motion | The streaming reply's eased height ignored reduced motion, and kept the frame loop running. | With reduced motion the reply is never eased. | test_with_reduced_motion_a_reply_grows_without_an_eased_height | fixed |
| UX-09 | focus | Tabbing into the message box changed its border by a 1.2:1 contrast, which no one could see. | Reached with Tab, it shows the app's focus ring (at least 3:1). A click keeps the quiet border. | test_keyboard_focus_in_the_message_box_is_visible | fixed |
| UX-10 | composer | At 1024 px the model name was capped at 120 px ("DeepSeek V4.1 F..."). | It keeps 200 px down to a 640 px column. | test_the_model_name_is_not_cut_at_1024_pixels | fixed |
| UX-11 | motion | One-off motion ran long: the unread dot popped for 400 ms, and Send and new messages used 260 ms. | Menus, dialogs, toasts, the sidebar, Send and the dot all finish within 200 ms. | test_one_off_motion_lasts_200ms_or_less | fixed |
| UX-12 | sidebar | The open row had no steady mark of its own, and hovering or opening a running chat hid its running dot. | The open row has an accent edge, and a running or waiting dot stays visible. | test_the_open_chat_has_an_accent_edge_and_a_running_dot_stays_visible | fixed |
| UX-13 | header | The Progress button always had the hover fill, so it looked pressed with its panel closed. | No fill when closed, the selected fill when open. | test_the_progress_button_looks_pressed_only_while_its_panel_is_open | fixed |
| UX-14 | side panel | The side panel appeared at once, and with it open the model button lost "Auto" and its name was cut to 96 px. | The panel slides in, and the model button keeps its name and "Auto". | test_the_side_panel_slides_in_and_the_model_button_keeps_auto | fixed |
| UX-15 | chat | An answered question card eased its height down over 180 ms, moving everything under it on every frame. | It takes its answered size at once and fades its answer in: at most one layout shift. | test_answering_a_question_card_shifts_nothing_below_it | fixed |
| UX-16 | memory | An empty memory page showed two empty states, filters over nothing and more than one way to add. | One empty state and one Add memory button. | test_an_empty_memory_page_shows_one_empty_state_and_one_add_button | fixed |
| UX-17 | memory | The page title said "What Meshpool knows". | The product name is lowercase. | test_an_empty_memory_page_shows_one_empty_state_and_one_add_button | fixed |
| UX-18 | chat | A plain https address in a reply was text, not a link. | http and https addresses become links that open outside the app. javascript: and other schemes never do. | test_bare_links_are_clickable_and_short_code_does_not_break | fixed |
| UX-19 | chat | A turn that ended in an error still showed copy, check and thumbs under it. | Only Try again is left. | test_a_turn_that_failed_offers_try_again_but_no_thumbs | fixed |
| UX-20 | chat | The internal "Attached files (in this chat's folder): ..." line showed in the user's bubble, and a chat's own folder showed by its id in the Progress panel. | The files are chips, Copy gives what the user typed, and the folder reads by the chat's name. | test_attached_files_show_as_chips_and_the_chats_folder_by_name | fixed |
| UX-21 | toasts | Toasts sat on top of Send, and one import said "1 chats". | Toasts show under the header, and the count reads right. | test_a_toast_never_covers_the_message_box | fixed |
| UX-22 | projects | The project brief's card was drawn empty before or without its text. | No empty card. | test_a_project_page_has_no_empty_card_and_the_breadcrumb_keeps_the_project | fixed |
| UX-23 | header | At 1024 px the header dropped the project name from a project chat's breadcrumb. | It keeps the project. | test_a_project_page_has_no_empty_card_and_the_breadcrumb_keeps_the_project | fixed |
| UX-24 | onboarding | The sticky Continue and Start buttons covered the API key field at 1024x700 and "3. Add to memory" on step 3. | The button closes the card in the flow and covers none of the step's fields. | test_the_onboarding_button_never_covers_the_steps_fields | fixed |
| UX-25 | chat | A short inline code token such as `2025-12` broke at its hyphen at the end of a line. | Short tokens never break. | test_bare_links_are_clickable_and_short_code_does_not_break | fixed |
| UX-26 | chat | A reply that pointed at the chart it saved (`![Monthly revenue](monthly_revenue.png)`) showed the Markdown as text. | An image in the chat's folder shows inline through the token-checked /raw route. A remote image or a path outside the folder stays text. | test_a_chart_in_the_chats_folder_shows_as_an_image | fixed |
| UX-27 | sidebar | A chat started from a project's page and routed to Code was missing under the project, which said "No chats yet". | The project row lists all its chats. See also WF-I. | test_a_pinned_project_lists_its_code_chats_too | fixed |
| UX-28 | chat | Router notes were written for engineers ("no strong signal", "data words in a Code chat"). | The receipt says "chosen for data" or nothing, and the reason stays on the tooltip. | test_the_router_note_is_kept_out_of_the_chat_in_plain_words | fixed |
| UX-29 | attachments | A file dropped on the conversation, not on the message box, was opened by the browser in place of the app. | The whole chat pane takes it as an attachment. A file dropped anywhere else is ignored. | test_a_file_dropped_on_the_conversation_is_attached | fixed |
| UX-30 | onboarding | With the key already set, onboarding opened on "Step 2 of 3", and the logo moved about 10 px between steps. | "Step 1 of 2", and every step starts at the same top. Overlaps PF-04. | test_onboarding_counts_its_steps_and_keeps_the_logo_still | fixed |
| UX-31 | receipts | Every reply on a model without a price list ended "cost not known (unpriced model)". | Said once, in the tooltip. | test_an_unknown_cost_is_not_written_under_every_reply | fixed |
| UX-32 | settings | On the Permissions page the new-rule field was shorter than its buttons and only as wide as its placeholder. | As tall as the buttons, and it fills the row. | test_the_rule_field_lines_up_with_its_buttons | fixed |
| UX-33 | settings | Drawing the Permissions page ended in a TypeError (a stray drawRules), so "Add a folder" had no handler and a second, empty rules box was added. Also in 0.0.17. | It draws cleanly, with one rules box and a working button. Fixed again in the approvals branch (AP-08). | test_the_permissions_tab_draws_without_an_error_and_add_a_folder_works | fixed |
| UX-34 | chat | A suggestion floated over the last lines of the reply. | It sits in the flow above the message box. | test_a_suggestion_takes_its_own_room_above_the_composer | fixed |
| UX-35 | chat | "Jump to latest" sat on top of the model name under the box. | It sits just above the composer at 1024, 1280 and 1366 px. | test_jump_to_latest_sits_above_the_composer | fixed |
| UX-36 | settings | The check list said "context 1,000k (from provider); older steps are summarized above 120k tokens". | "Up to 1,000k tokens per chat (...). Past 120k, older steps are summed up to make room." | test_the_context_window_line_in_settings_reads_plainly | fixed |
| UX-37 | chat | While the model took minutes to answer, the only sign was a small "Thinking 247s". | After 20 s with nothing arriving, the chat says "Still waiting for the model (1 min)" with Stop, drawn by the existing tick. | test_a_long_wait_on_the_model_says_so_in_the_chat_with_stop | fixed |
| UX-38 | settings | Settings search matched page names only: "folder", "approval" and "permission" found nothing, and "ask" found Scheduled tasks. | Each page carries its own words, a query matches word starts, and Enter opens the page. | test_settings_search_finds_what_a_page_holds | fixed |
| UX-39 | file cards | A long name on a file card was cut at the edge with no ellipsis. | It fits the card and ends in an ellipsis. | test_a_long_file_name_on_a_card_ends_in_an_ellipsis | fixed |
| UX-40 | Code chat | Steps showed tool names ("exec command", "apply patch"), and a group read "1 step, 1 failed". | "Ran a command", "Edited files", and "1 step, 1 did not work". Display only. | test_code_steps_are_named_in_words | fixed |

## Developer command line and Code panel (app/tests/test_om_bugs018.py, test_ui_om018.py)

From the merge of c4bf4c42 (commits 128804b9 to c4bf4c42).

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| OM-01 | -p | Without a sandbox, `-p` refused find, a pipe and &&. The summary said "Run this command?" five times and advised `--permission-mode edits`, under which no command runs. | Each refused command is named with a rule that allows it, edits mode is offered only for a file edit, and the hint points to `--permission-mode auto`. In the sandbox, ordinary commands run (AP-01). | test_om01_p_names_each_refused_command_and_a_flag_that_really_allows_it, test_om01_in_the_sandbox_p_runs_ordinary_commands_without_a_question | fixed |
| OM-02 | routing | "report" in report.py and the word "docs" counted as a request for a Word document, so a code request got a hidden note to build one. | They no longer count. Real requests for a document still do. | test_om02_a_code_file_or_the_word_docs_is_not_a_word_document, test_om02_real_requests_for_a_document_still_count | fixed |
| OM-03 | Undo | Dragging sample.csv into a Code chat left an untracked "sample (2).csv" in the repository, and Undo of the turn did not remove it. | Undo of the turn removes the copy while it still holds what was dropped. The same file dropped again is used in place (WF-02). | test_om03_undo_of_a_turn_removes_the_copy_of_a_file_dropped_into_it | fixed |
| OM-04 | --model | `--model nope-model` printed "no longer available" and carried on with another model. | A name given on the command line stops the run and lists the close names the endpoint serves. A chat's saved model that went away still falls back. | test_om04_a_model_named_on_the_command_line_that_does_not_exist_stops_with_close_names, test_om04_a_saved_model_that_disappeared_still_falls_back | fixed |
| OM-05 | Changes tab | After Undo, the Changes tab asked for the undone turn and showed "No such turn.". | It says the turn was undone and shows the chat's remaining changes. | test_om05_the_changes_tab_of_an_undone_turn_says_it_was_undone | fixed |
| OM-06 | terminal | A stopped turn left no trace, so the next request read the pasted stack trace as still to answer. /exit typed while a reply streamed did nothing until the reply ended. | The history keeps the message, the reply so far and a stopped mark, and `-c` sends them. /exit stops the turn and quits, and the turn is saved. | test_om06_a_stopped_turn_is_kept_with_what_it_said_and_marked_stopped, test_om06_exit_typed_while_the_agent_works_stops_the_turn_and_quits | fixed |
| OM-07 | search | "dedupe" did not find his chat about dedupe.py. | Not a bug: the words were in a turn he had undone. Search reads message text (PF-07). | test_om07_search_finds_a_code_chat_by_the_words_of_its_messages | not a bug |
| OM-08 | terminal | The streaming answer showed as one clipped line. `-p` printed "ctrl+o to expand" and exec_command's lines meant for the model (Chunk ID, Wall time). The /undo picker read "1. 1. explain storage.py", and after Undo the terminal said only "No files had changed". | Word-wrapped rows while streaming, no hint or model-only lines in `-p`, each turn numbered once, and Undo says what was undone. | test_om08_a_streaming_paragraph_shows_as_wrapped_rows_while_it_streams, test_om08_p_prints_no_ctrl_o_hint_and_no_exec_command_header_lines, test_om08_undo_picker_numbers_each_turn_once_and_says_what_was_undone | fixed |
| OM-09 | -c | `-c` ignored `--model`, and a chat last used in full access came back in full access without a word. | It resumes in the mode given (or the default mode, ask) and on the model given, and says so. | test_om09_a_resumed_chat_takes_the_mode_and_model_from_the_command_line | fixed |
| OM-10 | Code panel | The Changes diff and the approval card cut long lines. At 1024 px "Git and CI" was cut and the tabs after it could not be reached, and a hovered tab looked open. The chat said "context 200k (assumed)" and the panel "of 128K tokens". | Long lines wrap, every tab shows whole and only the open one looks open, and the panel reads the context size from the same place as the chat. | test_om10_the_panel_names_the_context_size_the_chat_names, test_om10_a_long_line_in_the_changes_diff_wraps_instead_of_being_cut, test_om10_a_long_command_in_the_approval_card_wraps, test_om10_at_1024_px_every_panel_tab_shows_whole_and_only_the_open_one_looks_open | fixed |

## Approvals (app/tests/test_sandbox_auto.py, test_ui_permissions_page.py)

Merged from claude/sleepy-faraday-tkjcox (commits bbcbc7fb, 37271cb3, 18d6de94, a28726e4, c0187b10). The owner: "way too
many approvals for normal users". Persona runs on 0.0.18 asked 6 times to summarise a PDF and 17 times in a data job.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| AP-01 | approvals | Ask and Accept edits asked before ordinary commands and edits in the chat's own folder. | Inside the OS sandbox (bubblewrap on Linux, sandbox-exec on macOS: writes only in the folder and temp), commands and edits in the folder run without a question, and those commands run with the network cut. Still asked: destructive commands, the network and installs, paths elsewhere in the home folder, meshpool's own rule files and anything outside the folder. Saved rules and hooks come first. MH_SANDBOX_AUTO=0 asks as before. | test_in_the_sandbox_ordinary_commands_run_without_a_question, test_in_the_sandbox_risky_commands_still_ask, test_in_the_sandbox_edits_in_the_folder_run_without_a_question_and_outside_ask, test_lean_shell_and_codex_exec_command_get_the_same_treatment, test_the_sandbox_cuts_the_network_only_for_commands_that_ran_without_a_question, test_off_by_setting_and_never_in_full_access_changes | fixed |
| AP-02 | approvals | The first version of AP-01 read paths only from the command's words, not from heredocs or `python -c` code, and let `find /` or `ls /home` run without a question. | Paths are read from the whole command text, and a folder above the user's home asks. The persona traces (17 and 6 questions) are a test: the ordinary ones run, the ones outside the folder still ask. | test_paths_in_the_users_home_outside_the_project_still_ask, test_the_persona_traces_run_without_a_question_and_the_risky_ones_still_ask | fixed |
| AP-03 | approvals, Windows | Without a sandbox, every ordinary command and edit asked. | The first question offers "Allow ordinary commands and edits in this folder in this chat": one yes covers the rest of the chat. | test_without_a_sandbox_one_yes_covers_the_chat | fixed |
| AP-04 | approval cards | Every card was a raw `D=/tmp/...; python3 -I -c "..."` line no one could judge, and Office tool cards named a temp path. | Cards say what the step does ("Run a Python step on sales.csv?", "Read report.pdf?", "Use the internet (example.com)?", "Change deck.pptx?"), with the command below. | test_approval_cards_say_what_the_step_does, test_office_tool_cards_name_the_action_and_the_file | fixed |
| AP-05 | approval cards | "Always in this folder" was offered in chats with no folder. | Not offered there. | test_always_in_this_folder_is_not_offered_in_a_chat_with_no_folder | fixed |
| AP-06 | settings | Permissions was a tab inside another page: Maya could not find how to stop the questions or turn on Full access, and Daniel could not find Permissions at all. | Permissions is its own page, found by search, with a "When meshpool asks you" choice: only for risky actions (the default), before every command and change, or never (Full access, the default mode for new chats). The choice applies without a restart. | test_permissions_is_its_own_page_and_search_finds_it, test_the_three_choices_save_what_they_say | fixed |
| AP-07 | approvals, office | Once ordinary work stopped asking, `cp new.pptx Q3_Quarterly_Review.pptx` replaced the deck Sara had open in PowerPoint. | A command or edit that can write over an Office file with a lock next to it (`~$name` or `.~lock.name#`) asks again. Reading it does not. | test_a_file_open_in_office_asks_before_a_command_or_edit_replaces_it | fixed |
| AP-08 | settings | The Permissions page kept an old copy of the rules list that shadowed drawRules: opening the page threw, the per-folder rules never showed and "Add a folder" did nothing. Also in 0.0.17. | One rules panel: always allowed, always asks, never allowed, per folder. | test_the_three_choices_save_what_they_say | fixed, same bug as UX-33 and counted there |

## Pre-release review (commits 2dc89bfe, daa8b508, 72eda711, 3eff2941)

A review of the release candidate by area, before the persona fixes were merged.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| RR-01 | Office panel | A paired panel reached the chats any paired panel had started, so a second session allowed by mistake could read the real panel's chats and answer its approvals. | Each panel session owns the chats it started. A disconnected panel owns none. | test_a_panel_reaches_only_the_chats_it_started_not_another_panels | fixed |
| RR-02 | app server | Within the grace time, a one-time key opened the page again without the cookie, for any process that read the address from the process list. | A second load needs the session cookie the first load set. | test_a_one_time_key_reused_within_the_grace_needs_the_windows_cookie | fixed |
| RR-03 | app server | compare_digest raised on non-ASCII input (a pairing id, a panel token, the mp_s cookie, a ?k= key) and dropped the connection with a traceback. | All of them compare bytes. | test_non_ascii_pairing_ids_and_tokens_are_refused_not_crashed | fixed |
| RR-04 | approvals | A grant for "docker compose ps" saved "docker compose", which covered `docker compose exec web sh -c ...`, and "uv tool run ruff" saved "uv tool". | Grants name the verb. docker compose and container run and exec are never granted, and uv tool run is judged like uvx. | test_docker_compose_and_container_grants_name_the_verb, test_uv_tool_run_is_judged_like_uvx | fixed |
| RR-05 | decks | Asked to delete a slide, the agent (2 of 3 runs, and Codex 3 of 3) removed it from the slide list only, so its text, notes and pictures stayed in the file. | A deck written this turn that still carries such slides gets one note naming them and how to drop them. | test_pptx_orphan_slides_check.py (3 tests) | fixed |
| RR-06 | models | The model-list check ran on every set_model, also when a chat was opened: a chat saved on a model the list no longer has could not be opened, and valid names (gpt-4o:nitro, the default, ChatGPT's hidden models) were refused. | The check runs only for a pick in the model menu, counts a :variant as its base model, knows the default and skips ChatGPT. | test_p2_uiserver_16_set_model_checks_the_known_model_list | fixed |
| RR-07 | file reading | docread's disk cache outlived upgrades. | The reader version is part of the key. | test_the_reader_cache_key_changes_with_the_reader_version | fixed |
| RR-08 | file reading | A UTF-16 file without a BOM was read as UTF-8 with NUL bytes. | The UTF-16 test comes first. | test_utf16_without_a_bom_is_read_as_utf16_not_as_utf8_with_nuls | fixed |
| RR-09 | instructions | Nested instruction files in a project without git could not import from the rest of the project. | The fence is the project's root, not the subfolder. | test_nested_instructions_may_import_from_anywhere_in_a_non_git_project | fixed |
| RR-10 | LibreOffice | When the app crashed or was killed, the kept LibreOffice kept running with --accept and its profile stayed in the temp folder. | The worker ends LibreOffice once the app's pipe closes. | test_libreoffice_ends_when_the_app_goes_away_without_closing_it | fixed |
| RR-11 | LibreOffice | Every caller waited on the kept session with no limit, so a preview waited out a 240 s deck render before its own timeout started. | Busy for more than 30 s, the session hands the job back and the caller converts on its own, as before. | test_a_busy_session_hands_the_job_back_instead_of_waiting | fixed |
| RR-12 | Windows installer | The signed uninstaller did not remove the notification AppUserModelID key the installer writes. | It removes it. | test_the_signed_uninstaller_removes_the_id_too | fixed |

## Merge fixes by the coordinator

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| MG-01 | settings | After the approvals branch was merged, settings2.js declared SET_WORDS twice, a SyntaxError that stops the settings script from loading. Never released. | One search list holds both sets of words (03fad180). | test_settings_search_finds_what_a_page_holds, test_permissions_is_its_own_page_and_search_finds_it | fixed |
| MG-02 | settings | Settings search words named another brand. | Removed (3b3cf16e). | none | fixed |
| MG-03 | chat | Copy on a message of yours gave the internal attachments line with the text. | Copy gives the typed words (f4821ab7), and says "Copied" again: that edit had put the toast inside a line comment. | test_copy_on_your_message_gives_your_words_and_says_copied | fixed |

Tests changed with no product change: test_fix_pass2_open_server.py follows the ER detail wording, test_awake.py resets
the holder's give-up flag that PF-05 added (4f3c9b2f), a panel test waits for the UX-14 slide to end (3b3cf16e), and the
UX tests open the top-level Permissions page (03fad180). Later: the live-line test and the polish UI test skip the fake
chat's "This chat is gone" note, which they counted one run in four (38551047, ca09b450). The local Claude adapter
follows a format the instructions ask for, so the reviewer's JSON reads the same as on the real adapter (ff36e256). None
of these is counted. The Copy toast of the same merge is MG-03.

## Design pass (app/tests/test_unslop018.py)

Merge of d0e23e00 (the design pass branch, with the look review merged into it). After unslop by Damir Sokolovsky
(CC BY 4.0). The owner approved the rules below, and each test holds one of them. Most tests read the UI files. The last
two drive the real app in Chromium and are skipped when no Chromium can start.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| US-01 | motion | The spring curve overshot on every button transform. It ran beside a second easing and the ease, ease-in, ease-out and ease-in-out words. Some keyframes squashed, rotated or pulsed a ring. | One easing curve. No keyframe overshoots, squashes, rotates or pulses. | test_one_easing_and_no_spring_or_overshoot | fixed |
| US-02 | motion | Motion ran from 70 to 900 ms. | Hover and press take 120 ms, menus 160, panels 200. A longer loop is allowed only for live work, and only when it fades or turns a spinner. | test_durations_are_200ms_or_less_except_opacity_fades | fixed |
| US-03 | motion | People who ask for less motion still got some. | Reduced motion turns everything off, pseudo-elements included. | test_reduced_motion_turns_everything_off | fixed |
| US-04 | motion | The side panel and each child faded in, which is twice the work for one change. The progress bar animated its width, a layout on every frame. | The panel alone slides. The bar scales. | test_the_panel_slides_once_and_its_bar_scales_instead_of_growing | fixed |
| US-05 | type | Display headings were weight 700 in Bricolage Grotesque with negative tracking. | IBM Plex only. Display at 500 with no negative tracking below 40 px. Bold defaults to 600. | test_no_700_weights_and_no_bricolage | fixed |
| US-06 | type | Sizes of 10, 11, 15, 15.5, 18, 26 and 32 px were off the scale. The receipt line had no one look. | Sizes sit on the scale. The receipt line is Plex Mono 12/16 with tabular figures. | test_sizes_are_on_the_scale_and_the_receipt_is_the_signature | fixed |
| US-07 | spacing | 14 px had 49 uses and 18 px had 13. Values of 5, 7 and 9 were scattered. | Every padding, margin and gap in the UI CSS, and in the inline styles the scripts write, is a multiple of 4. A 2 px nudge is allowed. | test_every_padding_margin_and_gap_is_on_the_4px_grid | fixed |
| US-08 | spacing | Sidebar rows were 34 px, the settings nav 34 px and menu rows 34 px, beside 36 px popovers. | Navigation, chat rows, settings nav and menu rows are 32 px. | test_rows_in_a_list_share_one_height | fixed |
| US-09 | copy | "Meshpool" was in about 149 UI strings. | Every string the scripts show says lowercase "meshpool". A header name, the outputs folder and the macOS app bundle keep their spelling. | test_the_product_name_is_lowercase_in_every_ui_string | fixed |
| US-10 | copy | The home said "Good morning" and the first run said "Welcome to meshpool". Costs read $0.0005 beside $0.0020 and durations 0.6 s beside 3 min 45 s. The receipt named the model differently from the composer. | No greeting. One cost format and one duration format (whole minutes read "3 min"). The receipt names the model as the composer does. | test_copy_has_no_greeting_and_one_cost_and_duration_format | fixed |
| US-11 | color | On the light sidebar, hover (#E8E8E8) and selected (#DCDCDC) looked the same. The open chat stacked a fill, a 3 px blue edge and a dot. | Selected is a clear step past hover in both themes (a luminance ratio of at least 1.08). The selected row adds weight 500 and a hairline, not the accent edge. | test_hover_and_selected_read_apart_in_both_themes | fixed |
| US-12 | color | Four selected styles (a blue underline, grey tabs, a black Memory chip, the nav fill). The settings search showed a focus ring on every click. | One selected fill with ink text. The settings sub-tab underline is ink. The search rings only when reached by Tab. | test_one_tab_style_and_no_focus_ring_until_the_keyboard | fixed |
| US-13 | color | Two accents (pool blue and the logo's teal), a hard-coded #3FB37F and coloured gallery tiles. | Every coloured token is pool blue or a semantic colour (add, delete, warn). No rule outside the tokens writes a colour. Teal is gone. | test_pool_blue_is_the_only_accent_in_the_chrome | fixed |
| US-14 | color | Dark text was pure white on large surface steps. | A graphite ramp. All three text roles reach 4.5:1 on the page, sidebar, cards and menus, in light and dark. | test_text_reaches_4_5_to_1_on_every_surface_in_both_themes | fixed |
| US-15 | icons | Icons were 12, 13, 14, 15, 16 and 18 px with a 1.8 stroke. | The chrome draws icons at 16 px with a 1.5 stroke. Only the gallery's type tiles and the file card's type icon are larger. | test_one_icon_size_and_stroke_in_the_chrome | fixed |
| US-16 | layout | At 1280 px the preview squeezed the chat to about 400 px and cut the model name. | Below 1366 px the sidebar folds away while the preview is open. The chat keeps 560 px or more, with the model name whole. | test_the_preview_at_1280_keeps_the_chat_560px_wide | fixed |
| US-17 | layout | The home title was a greeting, "New" was drawn as the current page, and the composer's model, permission, eye and lock buttons had no name. | The home title is the context. "New" is an action and is not selected. The composer buttons say what they are. | test_home_has_no_greeting_new_is_not_selected_and_composer_buttons_are_labelled | fixed |

## Design pass check (app/tests/test_unslop_verify018.py)

Commits 1c9357bf and c030ea4d. A separate look at the design pass, driven in the real app in Chromium, found five
regressions. Each test failed before its fix. The rules of the design pass still hold.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| UV-01 | chat steps | The step chevron sat 3 px above its text (an inline icon on the text baseline). | The chevron is an inline grid, so it is centred on the line. | test_the_step_chevron_is_centred_on_its_text | fixed |
| UV-02 | side panel | Four panel tabs wrapped onto two rows at 1024 px once the tab padding went to 12 px. | Tab padding is 4 px by 8 px. The tabs share one row. | test_four_panel_tabs_share_one_row_at_1024 | fixed |
| UV-03 | links | A link button had no hover, so it looked dead. | Hover sets the ink colour and a thicker underline. | test_a_link_button_reads_as_hovered | fixed |
| UV-04 | home | The links in the home line kept their 4 px padding, so the full stop floated away from "Home". | Links in the home line have no padding. | test_the_hero_links_keep_the_full_stop_beside_them | fixed |
| UV-05 | menus | The checked menu item carries the class the composer pills use for a fixed 32 px height, so a long description spilled over the next item. | A checked item grows with its description. | test_a_checked_menu_item_grows_with_its_description | fixed |

## The look review step (app/tests/test_look_review.py, test_review_interact.py)

Branch merged at 5243ae55 (commits 10808b05, 0c624b68 and 5243ae55, with the interaction module 20c13e7d merged in).
Before this step the agent never looked at what it made unless the user asked.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| RV-01 | agent/look.py | The agent never looked at a web page it built. A Word file, sheet, deck, PDF or image made in a chat was not drawn either. | At the final answer of a turn that wrote or changed a web page, meshpool opens it in one headless Chromium at 1280x800 and 390x844. It collects console errors, failed requests, broken images, sideways scroll, overlap, cut text, low contrast, placeholder text and empty lists. The model gets one review message with the request, the findings and (when it takes images) the pictures, fixes what is wrong and is checked again, at most two rounds. The answer ends with a "Checked:" line that also goes in the receipt. Files made in a chat are drawn through view.render and the kept LibreOffice, unless an engine check or the agent's own look ran after the last write. No server is started. Off with MH_LOOK_REVIEW=0 or the Settings switch "Look at web pages it makes". | test_overflow_and_console_error_reach_the_review_and_the_fix_is_checked, test_real_render_closes_its_browser_and_measures, test_at_most_two_review_rounds, test_no_second_look_when_nothing_changed_after_the_review, test_off_switch, test_settings_switch_turns_it_off, test_plain_answer_has_no_review, test_dev_server_the_turn_started_is_used_and_none_is_started, test_docx_made_in_a_chat_gets_a_render_check, test_no_second_render_when_engine_qa_already_ran, test_mock_model_chat_turn_is_reviewed_and_fixed, test_print_mode_reviews_without_an_approval_prompt and the other tests in test_look_review.py | fixed |
| RV-02 | agent/look.py | Each review round added two to four screenshots to the chat history, so a second round sent all of them again on every later request. | Older review pictures become a short note, as screenshots from tools already do. | test_second_round_replaces_the_first_rounds_pictures | fixed |
| RV-03 | agent/look.py | At phone width, an inline label that wraps has a box that covers the label beside it. A plain form was reported as "label Name overlaps label Email", and the review sent the model after a problem nobody sees. | Overlap is measured per line box. The review uses the merged interaction check (UXR-01), looked up through importlib, with the review's one browser. A missing module means no interaction checks. | test_wrapped_inline_labels_are_not_called_overlapping, test_dead_button_and_unchecked_form_reach_the_review, test_interaction_findings_and_shots_join_the_review, test_missing_interaction_module_means_no_interaction_checks, test_broken_interaction_module_does_not_break_the_review | fixed |
| UXR-01 | review_interact.py (feature) | Nothing tried a page the way a user would. A dead button or a form that takes empty fields passed the measured checks. | exercise(target, request, out_dir) opens the page at each width and ranks the buttons, links, forms, menus, dialogs and tabs by how closely the request names them. It clicks, fills and submits forms (also empty), opens and closes menus and dialogs, tabs through the page, hovers the main buttons and opens the phone menu. It writes what went wrong in plain words ("The 'Order now' button does nothing when clicked"). It makes no model calls, opens only local pages, blocks other hosts and never raises. It closes its browser, and kills it at the time limit. | test_review_interact.py (22 tests) | fixed |

## Fixes from the review step's live check (app/tests/test_look_live_line.py, test_look_review.py)

Commits 2dfe978f and fae0c1b4. Found by running the review step on the live model and in the web UI.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| VF-01 | web UI | The Checked line arrived as a second short assistant bubble until the chat was reloaded. | The line joins the reply's own log entry and bubble as it arrives, and a reopened chat shows the same. A line with no reply before it still shows. | test_checked_line_joins_the_reply_live, test_checked_line_is_the_same_as_a_reopened_chat, test_a_line_with_no_reply_before_it_still_shows | fixed |
| VF-02 | agent/look.py | make_document, check_document, make_workbook and check_workbook never draw a page, but counted as a look. A Word file made through the office engine was never drawn and got no Checked line. | Only engine steps that draw pages (check_deck, make_deck and the viewing tools) skip the look. | test_a_structure_only_check_is_not_a_look | fixed |
| VF-03 | web UI | While the look ran, the footer did not say what it waited for. | The status event for the look carries look=true, and the footer says it is looking. | test_looking_line_goes_when_the_checked_line_arrives_and_the_footer_names_the_wait | fixed |
| VF-04 | web UI | The grey "Looking at the result" line stayed in the thread after the Checked line arrived. | The line goes when the Checked line joins the reply. | test_looking_line_goes_when_the_checked_line_arrives_and_the_footer_names_the_wait | fixed |
| VF-05 | agent core | The harness skips notes that start with "[Meshpool]" when it looks for the user's request, images and insights. The review's own note says "[meshpool]", so it was taken for the user's words. | The checks in core, grader, compat, insights and the terminal accept the lowercase form. | test_the_review_message_says_lowercase_meshpool_and_is_still_a_harness_note | fixed |

## Review step polish (app/tests/test_look_polish.py, test_look_polish_ui.py)

Merge of de56c7f3, and the coordinator's commit 38551047. The prefix is LP2 because LP is taken (the later pass).

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| LP2-01 | agent, web UI, terminal | The reply written before a review round stayed as if it were the answer. It was the one to copy, and the Checked line joined it. | Each review round marks the reply before it as a first answer (a review_round event). The web UI folds it under "First answer, before the check", and the Checked line joins the last answer. The terminal prints a dim note. In print mode only the last answer is written. | test_each_review_round_marks_the_reply_before_it_as_a_first_answer, test_print_mode_writes_the_last_answer_only, test_print_mode_still_writes_an_answer_that_no_review_follows, test_the_json_result_holds_the_last_answer, test_the_first_answer_folds_and_the_last_one_carries_the_checked_line | fixed |
| LP2-02 | agent/look.py | The review sent the model after a control the request itself called a placeholder ("a Buy button that does nothing yet"). | Findings and steps about a control the request calls inert are dropped, and the review message says the user's choices win. | test_a_control_the_request_calls_a_placeholder_is_not_a_finding, test_the_review_message_says_the_users_choices_win, test_real_page_dead_button_follows_the_request | fixed |
| LP2-03 | agent/look.py | The Checked line named what was fixed in general words and said "updated it after the review". | It names what was fixed and what is still open, from the findings before and after ("low contrast on 'See prices'"). It keeps four and counts the rest. With nothing left open it says the page was changed after comparing it with the request. | test_checked_line_names_what_was_fixed_and_what_is_open, test_checked_line_without_findings_does_not_say_updated_after_the_review, test_checked_line_in_a_real_turn | fixed |
| LP2-04 | agent/look.py | Round two began with two "[meshpool]" headers. | One header. | test_round_two_has_one_meshpool_header | fixed |
| LP2-05 | web UI | The "Looking at the result" line stayed in the thread when no Checked line followed (an error, a stop, a turn that ends). | The line is removed at the end of the turn. After an error or a stop it says "Could not finish the check" and why. | test_looking_line_does_not_stay_without_a_checked_line | fixed |
| LP2-06 | agent/look.py | Tools named make_report or build_* counted as a look. A connected server may use those names and only write the file, so the page was never looked at. | Only meshpool's own tools that draw pages (check_deck, view_output, read_document, measure_office_file, make_deck) skip the review. | test_only_tools_that_draw_pages_skip_the_review | fixed |

## The independent persona reviewer (app/tests/test_look_reviewer.py, app/meshpool_harness/agent/lookreview.py)

Commits eca33e6f, ca09b450, a1758ddf, 2500e27a, c4bf300e and 45beef42. The coordinator's commit ff36e256 (the testkit
shim) is not counted.

| ID | Area | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| PR-01 | agent/lookreview.py (feature) | The builder reviewed its own work in its own conversation, with its own reasoning in view. | A separate model call with a fresh context (none of the builder's messages, system prompt or reasoning) first names who would really use the thing, such as a customer ordering on her phone, never the person who asked. It then tries a page as that person, with up to 4 actions of its own in the same browser, and returns problems ranked blocker, major or minor. The builder fixes them and the same persona checks again, at most twice. Files get a reader persona and no actions. The Checked line names the persona. The calls run on the chat's own model and gate, and are counted in the receipt. When the reviewer cannot be had, the builder gets the measured findings and the line says "Checked: measured only". | test_reviewer_call_has_a_fresh_context_and_none_of_the_builders, test_reviewer_text_never_shows_as_an_assistant_reply, test_persona_is_derived_kept_for_round_two_and_shown_in_the_note, test_action_requests_run_and_their_results_go_back_to_the_reviewer, test_actions_run_in_the_browser_that_drew_the_pictures_and_it_is_closed_after, test_a_pass_ends_the_loop_with_no_second_model_loop, test_at_most_two_rounds_when_the_reviewer_never_passes, test_blockers_reach_the_builder_first_with_the_persona, test_explicit_user_choices_win_over_the_reviewer, test_receipt_counts_the_reviewers_calls_and_tokens, test_a_bad_reviewer_reply_falls_back_to_the_measured_findings, test_a_docx_gets_a_reader_persona_and_no_action_requests, test_the_persona_is_the_end_user_never_the_requester, test_mock_server_full_loop_with_the_independent_reviewer and the other tests in the file | fixed |
| PR-02 | agent/lookreview.py | On the live Claude adapter, a verdict with a stray closing bracket made the whole review fall back. The plan reply called a problem "problem" instead of "what". A "look at" action told a reviewer with no picture nothing, and an action the browser could not run was not reported. | The reply is read with the stray bracket left out (brackets inside text are kept), "problem" is read as "what", "look at" reports the text of the section, and an action the browser could not run is said to be "not tried". | test_replies_recorded_from_the_adapter_are_read, test_a_recorded_verdict_with_a_stray_bracket_no_longer_ends_the_review, test_look_at_tells_a_reviewer_with_no_picture_what_is_there, test_a_missing_browser_is_said_not_hidden | fixed |
| PR-03 | agent/lookreview.py | A reply the tolerant reader could not parse sent the whole review back to measured only. | One retry in a fresh context that shows the reviewer its own reply and asks for only the JSON object. The retry is counted in the turn's usage. Only a second failure falls back. | test_a_reply_only_the_retry_fixes_is_retried_once_in_a_fresh_context_and_counted, test_a_reply_the_retry_cannot_fix_falls_back_after_one_retry, test_a_reply_the_tolerant_parser_reads_needs_no_retry, test_a_failing_reviewer_call_falls_back_too | fixed |
| PR-04 | agent/lookreview.py | A "look at" action that names a section by its heading ("Hours") looked for a link or button with that name, so the section was not found. | look_at finds a section by its heading first, then by the old lookup. | test_look_at_a_section_name_finds_the_section_not_the_nav_link_to_it | fixed |
| PR-05 | agent/lookreview.py | The Checked line carried the reviewer's whole sentences, cut in the middle of a word at 90 characters, with the reason after a comma ("..., so a keyboard user cannot see where they are"). | The reviewer is asked for each problem as a short phrase. The line keeps the thing and leaves out the why, and a long phrase ends on a word. | test_problem_phrases_in_the_checked_line_are_short_and_end_on_a_word | fixed |

## Layout jumps (app/tests/test_jitter018.py)

Measured with a layout-shift observer in the real app after the design pass. The owner's main complaint was that the
app felt shaky; these were the jumps left.

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| JT-01 | ui/app.js flipLayout, fitPanel | Opening or closing the side panel or the sidebar snapped the chat to its new width: layout shift 0.175 to 0.226 at 1280 and up to 0.33 at 1024. | The layout changes in one frame, then the chat glides from its old place by transform (200 ms, one easing, off with reduced motion). Below 1366 px the sidebar folds while the panel is open, so the chat keeps its width. Now at most 0.0043. | test_the_chat_does_not_jump (1280, 1024) and the column-width and reduced-motion tests | fixed |
| JT-02 | ui/app.css .settle, index.html | Opening a chat shifted the layout by 0.038 to 0.050: the newest messages lost their containment while settling, and bold and code fonts swapped in late. | The settling messages keep their containment, and the two fonts are preloaded. Now 0.0025 or less. | test_opening_a_chat_shifts_the_layout_by_0_01_or_less | fixed |
| JT-03 | ui/shell.css, shell.js | In Code mode at 1024 px the seven panel tabs wrapped to two or three rows, and the Changes select was cut ("All changes in t"). | One row that scrolls sideways, with the open tab brought into view; the select reads "All changes" and ellipsizes, with the full text as a title. | test_the_panel_tabs_are_one_row_without_cut_text, test_the_open_tab_is_brought_into_view | fixed |
| JT-04 | ui/app.css .notedet.fold | The "First answer, before the check" fold was louder than the notes around it (ink, underlined) and its opened text was small (12 px). | A quiet note-size summary with a chevron; the opened reply reads at reply size. | test_the_review_fold_is_a_quiet_note | fixed |
| JT-05 | ui/app.css (dark) | The dark settings dialog barely stood off its dimmed page (1.03 times as light). | The dialog uses the surface step and its side list the page colour: at least 1.1. | test_the_dark_settings_modal_stands_off_its_scrim | fixed |
| JT-06 | ui/app.js onboarding, app.css .onb-body | Onboarding step 3's Start button sat far below the fold (y 1473 in an 800 px window). | Each step's fields scroll inside the card and the button row stays under them, never covering a field. | test_onboarding_step_3_buttons_are_reachable_without_scrolling_and_cover_no_field, test_onboarding_steps_2_and_3_are_readable_in_both_themes | fixed |

## Persona round 1: Priya on the release build (app/tests/test_priya018.py)

The persona runs went on with the release build. These are what Priya found, in the order of her run (commits 16343a1e, 4d4022ee).

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| R1-01 | office checks | The Word file check talked about repair and schemas when nothing was wrong. | It says nothing about repair or schemas unless a real problem was found. | test_the_word_file_check_says_nothing_about_repair_or_schemas | fixed |
| R1-02 | checks line | The line that says what was checked read in technical terms. | It says it in plain words, for example "Checked the workbook: 16 of 20 checks passed". | test_the_checked_line_says_what_was_checked_in_plain_words | fixed |
| R1-03 | checks line | The checks that did not pass were not listed anywhere. | The line has a details list of what did not pass. | test_the_checks_line_has_a_details_list_for_what_did_not_pass | fixed |
| R1-04 | chat | Jargon showed in the chat. | Plain words in its place. | test_jargon_stays_out_of_the_chat | fixed |
| R1-05 | sidebar | The Code sessions group held chats that were not made from the Code tab. | Only chats made from the Code tab are in it. | test_the_code_sessions_group_is_only_for_code_chats | fixed |
| R1-06 | settings | The saved rules were a bare list. | A plain sentence comes first. | test_saved_rules_begin_with_a_plain_sentence | fixed |
| R1-07 | file cards | A card for an html file did not open the preview and had no type picture. | A click on the card opens the preview, and the card has a type icon. | test_an_html_file_card_opens_the_preview_and_has_a_picture | fixed |
| R1-08 | sidebar | Recents said how to start a chat even when chats existed. | The empty state shows only when there are no chats. | test_recents_say_nothing_about_starting_a_chat_once_chats_exist | fixed |
| R1-09 | sidebar | With the sidebar folded there was no way to start a new chat from the top bar. | A New chat button stays in the top bar while the sidebar is folded. | test_a_new_chat_button_stays_in_the_top_bar_while_the_sidebar_is_folded | fixed |
| R1-10 | layout | The sidebar folded and the chat column narrowed when a panel opened, also when there was room. | The sidebar folds by the room the chat would have, and the column narrows only when there is none. | test_the_chat_column_only_narrows_when_there_is_no_room | fixed |
| R1-11 | chat | A reader who had scrolled up lost their place when a panel opened. | The reader keeps their place. | test_the_reader_keeps_their_place_when_a_panel_opens | fixed |
| R1-12 | chat | Suggestion chips were offered under replies they had nothing to do with. | Chips show only when they relate to the reply. | test_chips_are_only_offered_when_they_relate_to_the_reply | fixed |
| R1-13 | changed files | A copy of an attached file that was not changed was listed as changed. | A copy of an unchanged file is not a change. | test_a_copy_of_an_attached_file_is_not_a_change | fixed |
| R1-14 | office tools | An empty office call was shown to the user as a failure. | An empty call fails back to the model, not to the user. | test_an_empty_office_call_is_not_put_to_the_user | fixed |
| R1-15 | web UI | The jump-to-latest pill moved with the layout slide. | The pill uses its own transform and stays where it is. | test_the_jump_to_latest_pill_is_not_moved_by_the_layout_slide | fixed |
| R1-16 | web UI | A Markdown image written inside inline code was drawn as a picture. | It stays text. | test_a_markdown_image_inside_inline_code_stays_text | fixed |
| R1-17 | web UI | Front-end review nits: a dead binding, a misplaced comment, and a test of edit turns that did not wait. | Each one is gone. | test_front_end_nits_are_gone | fixed |
| R1-18 | server | own_changes hashed every output on every poll. | Each output is hashed once per version. | test_own_changes_reads_each_output_once_per_version | fixed |

## Persona round 2 (app/tests/test_rescore2_behaviour.py, test_round2_019.py, test_omar_dev1.py to test_omar_dev4.py, test_daniel019_round2.py)

The second persona round: Priya, Lina, Maya and Sara (engines, Undo, steps, settings and the web app), Omar (the command line) and Daniel (decks). Commits 34c966c8, e553ef64, 788499d9, d4f438ae, the "round 2 items" commits and the ones named in each row.

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| R2-01 | outside engines | An engine's error reached the chat as raw text. | The error reads in plain words, and the raw text is kept in the event's detail. | test_engine_errors_are_plain_words_with_the_raw_text_kept | fixed |
| R2-02 | outside engines | An engine that Auto chose could fail before any work (HTTP error, nothing answering, cannot start) and the message was lost. | The message runs again on meshpool's own agent, with one plain line and the details in a fold. | test_an_engine_auto_chose_that_fails_runs_the_message_on_meshpools_own_agent, test_a_start_failure_moves_to_meshpools_own_agent_with_one_line | fixed |
| R2-03 | outside engines | An engine the user picked that failed gave a raw error. | A plain error, and a one-click way to meshpool's own agent. | test_an_engine_the_user_picked_keeps_a_plain_error_and_a_way_to_meshpools_agent | fixed |
| R2-04 | outside engines | A dead model service under an outside agent showed no clear line for a long time. | The service-down line shows within seconds. | test_a_dead_model_service_under_an_outside_agent_is_the_service_down_line_at_once | fixed |
| R2-05 | retry | Try again showed the stopped attempt and the retry as two copies of the message. | Try again is one server call. The stopped attempt leaves the chat, and the button shows after 90 s. | test_try_again_runs_the_message_once_more_without_a_second_copy_or_a_stopped_line | fixed |
| R2-06 | Undo | After Undo the model did not know which files had gone back. | Undo leaves a hidden note for the model that names them. | test_after_undo_the_model_is_told_which_files_went_back | fixed |
| R2-07 | office notes | The PDF note fired for a PDF the user already had, and held a line about secrecy. | It no longer fires for the user's PDF, and says not to mention it. | test_a_pdf_the_user_has_is_not_a_pdf_to_make_and_the_note_has_no_secrecy_line | fixed |
| R2-08 | office tools | A result did not say that meshpool stores the formula values after the turn. | Office tool results say so. | test_a_workbook_whose_values_meshpool_stores_after_the_turn_says_so_in_the_tool_result | fixed |
| R2-09 | office tools | The clarity pass was news to the user. | The note says it is not news for the user. | test_the_clarity_pass_is_not_news_for_the_user | fixed |
| R2-10 | outside engines | An engine that stopped before it made the file left no clear answer. | The answer says it stopped before making the file. | test_an_engine_that_stopped_before_making_the_file_says_so | fixed |
| R2-11 | steps | Failed steps that a later step redid still counted as problems. | The server marks them with a steps_retried event, their rows read "retried" and they are not counted. | test_failed_steps_a_later_step_redid_are_marked_retried, test_steps_the_server_marks_retried_are_not_counted_as_problems | fixed |
| R2-12 | sheets | A sheet the request named ("Rooms") was not counted as a rename it asked for, so the sheets gate failed. | A sheet named in the request counts as a rename it asks for. | test_the_rooms_summary_passes_the_sheets_gate | fixed |
| R2-13 | look step | The model was not told that the check shows under its answer. | The builder message says to fix what is wrong and not to mention the check, the reviewer or any engine. | test_the_look_step_tells_the_model_the_check_is_shown_under_its_answer | fixed |
| R2-14 | office reports | The engine's own file name and its notes about not recalculating showed in the report. | The file is under the user's name before the report is written, the notes are dropped and the agent is told not to mention the engine. | test_the_engines_own_file_name_and_recalc_notes_stay_out_of_the_tool_result | fixed |
| R2-15 | Undo | Undo could not remove or restore a large file. | Files over 2 MB keep a saved copy up to 200 MB (1 GB a chat), and past that their hash, so Undo still removes a file the turn made. Files it leaves are named plainly. | test_undo_removes_a_big_file_the_turn_made_and_puts_back_a_big_file_it_changed, test_a_file_too_big_to_copy_is_still_removed_and_a_changed_one_is_named_plainly | fixed |
| R2-16 | PDF | Converting the same file again added "name (2).pdf". | A conversion of the same source in the same chat replaces its own earlier output, and two identical calls at once make one file. | test_converting_the_same_deck_twice_in_a_chat_replaces_its_own_pdf | fixed |
| R2-17 | office edits | An in-place edit pointed the user to a backup. | It says Undo takes the change back, and no scratch backup is offered. | test_an_in_place_edit_points_to_undo_not_to_a_backup | fixed |
| R2-18 | approvals | Removing an Office lock file was titled as something else. | It is titled "Remove the Office lock file for name?". | test_removing_an_office_lock_file_is_titled_as_that | fixed |
| R2-19 | approvals | The open-in-Office card did not say what the tool does. | It says the changes will be saved as a copy, with the copy's name. | test_the_open_in_office_card_says_a_copy_will_be_saved | fixed |
| R2-20 | chat steps | Step rows read "Ran a command: ls -la", a whole heredoc, or "Used a connector" with a path. | The server gives each step the plain title the approval cards use, and no title holds file text. | test_plain_step_titles_come_from_the_server, test_the_view_carries_the_plain_title, test_chat_mode_rows_are_plain_and_code_mode_is_unchanged | fixed |
| R2-21 | chat steps | "3 steps, 1 had a problem" for a find that found nothing. | Only a failure the turn ended with, or one the reply reports, is counted. | test_a_harmless_failed_step_is_not_counted_as_a_problem | fixed |
| R2-22 | checks line | The closing line named totals for a workbook that has none. | Totals are named only when the sheet has them, and the validator records whether it does. | test_totals_are_named_only_for_a_workbook_that_has_them, test_the_real_validator_records_whether_a_sheet_has_totals | fixed |
| R2-23 | checks line | A failing deck rule was not named in the line. | The deck rule is named, and its name matches the validator's. | test_a_failing_deck_rule_is_named_in_the_one_line, test_deck_problem_names_match_the_validators | fixed |
| R2-24 | checks line | The look and the checks each said their own line. | One closing line holds both, and the page drops the look line it replaces. | test_one_line_when_both_the_look_and_the_checks_ran, test_the_page_drops_the_look_line_that_the_merged_line_replaced, test_a_bare_look_line_merges_with_the_checks_line | fixed |
| R2-25 | look review | An "also noticed" line could be about meshpool's own check. | It is never about the own check. | test_an_also_noticed_line_is_never_about_our_own_check | fixed |
| R2-26 | preview | A spreadsheet preview kept the old prices after an edit until the card was clicked again. | When the turn's checkpoint names the open file, the preview reads it again, on the same sheet tab and at the same scroll. Sheets, pages and notes refresh too. | test_an_open_spreadsheet_preview_refreshes_after_an_edit_and_keeps_its_place | fixed |
| R2-27 | file cards | A name read "Bouquet Price Li..." with one generic icon. | A name may take two lines, ends in an ellipsis only when longer, keeps its extension, and each file type has its own icon. | test_file_cards_show_the_name_in_two_lines_with_the_extension_and_a_type_icon | fixed |
| R2-28 | Chat mode | The footer, the thread and the home screen named the engine and the model ("Codex on DeepSeek V4.1 Flash"). | Chat mode shows no engine or model names there. Code mode keeps them. | test_chat_mode_shows_no_engine_names_but_code_mode_keeps_them | fixed |
| R2-29 | settings | "Models and providers", "API keys" and "Code" sat in the main list, and Permissions showed rule syntax. | The technical pages are under Advanced and the rule syntax is behind Advanced rules. | test_settings_keeps_technical_pages_under_advanced_and_rule_syntax_behind_advanced_rules | fixed |
| R2-30 | settings | Permissions showed "Linux does not need extra permissions". | The line is gone. | test_permissions_has_no_linux_line | fixed |
| R2-31 | onboarding | "Folders I choose" looked selected before anything was added, and after a cancelled picker. | A folder choice is marked only when it happened. | test_onboarding_marks_a_folder_choice_only_when_it_happened | fixed |
| R2-32 | onboarding | Step two read "Skill", "Instructions" and "SOUL.md", and had an Import button and a tick box for the same chats. | Plain labels and one control for past chats. | test_onboarding_step_two_uses_plain_labels_and_one_control_for_past_chats | fixed |
| R2-33 | search | Results had no snippet, and the footer showed Code keys to people who never use Code. | A snippet around the match, and no developer key hints for chat users. | test_search_shows_a_snippet_and_no_developer_key_hints_for_chat_users | fixed |
| R2-34 | Undo | The Undo details showed technical text. | They show nothing technical, also in a saved chat. | test_undo_details_hold_nothing_technical, test_the_page_drops_a_checkpoint_path_from_undo_details_even_in_a_saved_chat | fixed |
| R2-35 | folder picker | The folder picker showed long paths. | It shows the last three parts with an ellipsis. | test_the_folder_picker_shows_the_last_three_parts_with_an_ellipsis | fixed |
| R2-36 | layout | Opening a preview folded the sidebar and slid the chat about 460 px, while Progress did not, "New" shrank to a small "+", and at 1024 x 700 the first click on Progress only marked the button. | Preview and Progress fold the sidebar by one rule (only when the chat would go under 640 px), New chat stays labelled, and one click shows Progress. | test_preview_and_progress_fold_the_sidebar_by_the_same_rule, test_one_click_on_progress_shows_it_while_a_preview_is_open | fixed |
| R2-37 | checks line | The Checked line claimed more than the last verdict stated. | It claims only what the last verdict states, and is short. | test_the_checked_line_claims_only_what_the_last_verdict_states | fixed |
| R2-38 | look review | A change request was judged on style it did not ask for. | The review is held to the request. | test_a_change_request_is_not_held_to_style_it_did_not_ask_for | fixed |
| R2-39 | look review | A reviewer that had no pictures still made visual claims. | It makes none unless they were measured. | test_a_reviewer_without_pictures_makes_no_visual_claims_unless_measured | fixed |
| R2-40 | look review | An "also noticed" offer was made for something that had been fixed. | No offer for what was fixed. | test_no_also_noticed_offer_for_what_was_fixed | fixed |
| R2-41 | command line | In `-p`, narration shared stdout with the answer. | stdout holds only the final answer. Narration goes to stderr in a terminal, and nowhere otherwise. | test_p_stdout_holds_only_the_final_answer, test_p_narration_goes_to_stderr_when_it_is_a_terminal | fixed |
| R2-42 | command line | `git stash` over the user's own edits ran without a question. | It asks with a plain title over the user's changes. It runs on a clean tree or over the chat's own edit, and pop and list never ask. | test_2_stash_over_the_users_hand_edit_asks_with_a_plain_title, test_2_stash_on_a_clean_tree_or_over_the_chats_own_edit_runs, test_2_stash_pop_and_list_never_ask | fixed |
| R2-43 | command line | The model sent `git reset --mixed HEAD~1` twice in one reply and both ran, so a commit was lost. | An identical command is refused the fourth time in a row, and a stash and pop round trip is stopped after three stashes. Different commands are not a loop. | test_2_an_identical_command_is_not_run_a_fourth_time_in_a_row, test_2_a_stash_and_pop_round_trip_is_stopped_after_three_stashes, test_2_different_commands_are_not_a_loop | fixed |
| R2-44 | approvals | A discard card did not say what it throws away. | Discard titles say what they discard. A branch switch and unstaging are not discards. | test_6_discard_commands_say_what_they_discard, test_6_a_branch_switch_and_unstaging_are_not_discards | fixed |
| R2-45 | approvals | `git clean -n` asked, though it deletes nothing. | A dry run runs without a card, and `git clean -f` still asks. | test_6_git_clean_dry_run_deletes_nothing_and_needs_no_card, test_6_git_clean_dry_run_runs_without_a_card_and_clean_f_asks | fixed |
| R2-46 | command line | The `-p` hint for a refused discard did not name the flag that allows it, and the plain bash hint claimed to exclude deletes. | The hint names the flag, and the claim is gone. | test_6_the_p_hint_for_a_discard_names_the_flag_that_allows_that_command, test_6_the_plain_bash_hint_no_longer_claims_to_exclude_deletes | fixed |
| R2-47 | approvals | A heredoc script was titled from its first lines. | It is titled from what it does. | test_6_a_heredoc_script_is_titled_from_what_it_does_not_from_its_lines | fixed |
| R2-48 | agent core | A patch given as a quoted argument to apply_patch ran as a plain command. | The quoted form is applied as a patch, also with commands after it, and the step row reads as an edit. Other quoted text and piped forms are not patches. | test_3_the_quoted_form_is_a_patch_not_a_command, test_3_the_quoted_form_with_commands_after_it_splits, test_3_other_quoted_text_and_piped_forms_are_not_patches, test_3_exec_command_applies_the_quoted_patch, test_3_the_step_row_reads_as_an_edit_not_the_whole_patch | fixed |
| R2-49 | receipt | A stash round trip or a read-only git command after a passing test run reset the verified receipt. | Reversible and read-only commands do not change the files, so the receipt stays verified. | test_4_reversible_and_read_only_commands_do_not_change_the_files, test_4_a_stash_round_trip_after_a_passing_test_run_keeps_the_receipt_verified | fixed |
| R2-50 | command line | The web and the terminal described each access mode in different words, and not as the mode works. | One text per mode, the same in both, matching what each mode does. | test_7_the_web_and_the_terminal_describe_each_mode_in_the_same_words, test_7_the_descriptions_say_what_each_mode_really_does, test_7_the_terminal_shows_the_same_text_in_status_and_first_run | fixed |
| R2-51 | model calls | The wait line for a model call named the setting. | The wait line is plain and names how many of how many calls are running. | test_5_the_shared_wait_names_how_many_of_how_many_without_the_setting_name | fixed |
| R2-52 | agent core | A reply that was only a JSON tool call was shown as the answer, and one after words with a raw newline inside was not run. | A JSON call at the end of a reply runs as a call and keeps the words. A tool that is not offered, or JSON in the middle, is left alone, and with tools off the call is never printed as the answer. | test_8_a_reply_that_is_only_a_json_call_runs_as_a_call, test_8_words_then_a_json_call_with_a_raw_newline_inside_runs_and_keeps_the_words, test_8_a_tool_that_is_not_offered_or_json_in_the_middle_is_left_alone, test_8_with_tools_off_the_json_call_is_never_printed_as_the_answer | fixed |
| R2-53 | web UI | Side panel tabs that were out of sight could not be reached. | An arrow at each end where tabs are out of sight, and the open tab comes into view with the arrows in place. Arrow icons are 16 px. | test_9_every_side_panel_tab_is_reachable_and_the_clipped_ones_show_an_arrow | fixed |
| R2-54 | deck checks | Daniel's round 2 deck had flaws the checks did not catch: a blank grid or table row under a filled column, source lines that repeat, pointers and heads crowding the content, a two-column cover that left its page empty. | P24 fails a blank row under a filled column. New P30 (source lines name the report, once), P31 (pointers, brackets and heads keep clear of the content) and P32 (a two-column cover fills its page). | test_the_checks_fail_each_flaw_daniel_saw_in_his_round_2_deck, test_empty_cells_in_a_filled_table_column_fail_the_checks | fixed |
| R2-55 | deck checks | P24 flagged a column left blank by design (an actual-year column under forecast rows). | P24 flags blank cells only in a column filled in at least half its rows. | test_empty_cells_in_a_filled_table_column_fail_the_checks | fixed |
| R2-56 | deck renderer | The flow pointer, difference arrows, next-step heads, details column, repeated footnote and cover layout drew the flaws above. | The pointer gets 0.12 in of air each side, arrows end a label's height plus 0.15 in over the bars, heads fit one line, a details column is drawn only when every step fills it, a footnote that repeats the source line goes, and the cover spreads over its field. | test_the_renderer_draws_daniels_slides_without_those_flaws | fixed |
| R2-57 | deck renderer | A so-what under its evidence squeezed or dropped the pointer. | It takes up to 0.25 in from the exhibit above so the gap is 0.5 in and the pointer fits. | test_a_pointer_never_squeezes_into_a_gap_and_a_bracket_clears_the_labels | fixed |
| R2-58 | decks | Source lines named "the project fact base", "dossier" or fact ids. | They name the report instead. | test_source_lines_name_the_report_never_the_working_files, test_the_source_lines_of_the_built_deck_name_the_report | fixed |
| R2-59 | decks | A deck carried a target line, an owner, a date, a milestone or a cover occasion that the sources and the request do not give. | Before rendering, these are taken out, and a step with no sourced owner says "Owner to confirm". After rendering, a number, date or name that is not given fails check P29 and goes to the repair loop. The plan prompts no longer ask for them. | test_the_deck_adds_no_target_owner_date_or_meeting_the_sources_do_not_give, test_the_invented_material_check_flags_daniels_deck, test_a_next_step_the_sources_name_no_owner_for_says_so | fixed |
| R2-60 | decks | Speaker notes were cut short. | They keep whole sentences, up to about 120 words a slide unless the request asks for more. | test_speaker_notes_keep_to_about_120_words | fixed |
| R2-61 | decks | Slides added to a deck went into a new copy. | They are saved into that deck, and Undo keeps the earlier one. A "(updated)" copy is made only for a lock file or an explicit "save as". | test_slides_added_to_a_deck_are_saved_into_that_deck, test_a_copy_only_for_a_lock_file_or_an_explicit_save_as | fixed |
| R2-62 | decks | A slide the forge added had a plain shape named "Title 1" in place of the title placeholder, so outline view and the checks missed it. | The slide keeps its title as the layout's title placeholder. | test_a_slide_added_to_a_deck_keeps_its_title_in_the_title_placeholder | fixed |
| R2-63 | Word files | A memo did not follow the brand the request spelled out. | make_document reads the brand from the request or the chat's deck brief: heading and title colours, the title rule, fonts, and the footer text, with "Confidential" moved into the footer. | test_the_memo_carries_the_brand_and_the_footer, test_the_memo_follows_the_brand_of_the_chats_deck_brief | fixed |

## Persona round 3 (app/tests/test_round3_020.py, test_rescore3_behaviour.py, test_omar_r3.py, test_omar_r3_ui.py, test_sara_round3.py, test_daniel019_round3.py)

The third persona round: Priya, Lina, Maya, Sara and Daniel (office work, the web app and Codex), and Omar (the command line).

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| R3-01 | search | Snippets showed raw Markdown cut in the middle of a word. | A snippet is plain text that starts and ends at a word. | test_a_search_snippet_is_plain_text_that_starts_and_ends_at_a_word | fixed |
| R3-02 | search | A search for "open house" did not list open-house.html. | A search finds a file by its name, with spaces, hyphens and underscores alike. | test_search_finds_a_file_by_its_name_with_spaces_hyphens_and_underscores_alike | fixed |
| R3-03 | search | A result did not open its message with the word marked, and showed no date. | It opens the matching message with the word marked and shows the date. | test_search_opens_the_matching_message_with_the_word_marked_and_shows_the_date | fixed |
| R3-04 | search | In Chat mode the empty search said "new session". | It says "new chat". | test_the_empty_search_says_new_chat_and_not_new_session_in_chat_mode | fixed |
| R3-05 | outside engines | A step read "Reading List files in 'rooms-summary.xlsx'" four times: the engine's own title was put after the word Reading. | An outside agent's step titles are plain words. | test_an_outside_agents_step_titles_are_plain_words | fixed |
| R3-06 | Progress | Repeated steps filled the Progress list. | Progress collapses repeats, ticks everything at the end and shows every step. | test_progress_collapses_repeats_ticks_everything_at_the_end_and_shows_every_step | fixed |
| R3-07 | Progress | A file was listed again for each step that touched it. | Each file is listed once. | test_progress_lists_each_file_once | fixed |
| R3-08 | approvals | An approval card showed a scratch path. | It shows the file under its name. | test_the_approval_view_shows_the_file_under_its_name_and_no_scratch_path | fixed |
| R3-09 | approvals | A file write card showed the script. | It shows the script under its name and folds the command. | test_a_file_write_card_shows_the_script_under_its_name_and_folds_the_command | fixed |
| R3-10 | approvals | A Python step card was a long block. | It reads in one plain line and folds the code. | test_a_python_step_card_reads_in_one_plain_line_and_folds_the_code | fixed |
| R3-11 | file cards | A picture shown large in the chat also had a card. | A picture shown large is not also a card. | test_a_picture_shown_large_is_not_also_a_card | fixed |
| R3-12 | layout | At 1366 px the sidebar folded beside Progress and the preview. | It stays beside both, with one width. | test_the_sidebar_stays_beside_progress_and_the_preview_at_1366_with_one_width | fixed |
| R3-13 | layout | The preview was wider than Progress even when the chat lost its column. | It is wider only when the chat keeps its column. | test_the_preview_is_wider_than_progress_only_when_the_chat_keeps_its_column | fixed |
| R3-14 | layout | After a reload with a panel open, New was out of reach. | New is still reachable. | test_after_a_reload_with_a_panel_open_new_is_still_reachable | fixed |
| R3-15 | layout | The message box moved when a message was sent. | It does not move. | test_the_composer_does_not_move_when_a_message_is_sent | fixed |
| R3-16 | layout | The footer changed height, and a line under the reply pushed the card. | The footer keeps its height, and the line does not push the card. | test_the_footer_keeps_its_height_and_a_line_under_the_reply_does_not_push_the_card | fixed |
| R3-17 | status line | The status line kept saying "Checking it as: a guest on a phone..." after the turn ended. | It is cleared when the turn ends. | test_the_status_line_does_not_keep_checking_it_as_after_the_turn | fixed |
| R3-18 | Chat mode | In Chat mode the agent choice was not under Advanced. | The agent choice sits under Advanced as "other agents". | test_chat_mode_puts_the_agent_choice_under_advanced_as_other_agents | fixed |
| R3-19 | paths | Cards, the folder picker and Permissions showed the full home path. | Paths inside home read relative to home, and the picker's trail starts at home. | test_paths_inside_home_read_relative_to_home, test_the_folder_picker_starts_its_trail_at_home, test_permissions_shows_folders_relative_to_home | fixed |
| R3-20 | preview | A preview followed the user into a new chat, and attaching did not return focus. | The preview stays with its chat, and attaching returns focus. | test_a_preview_does_not_follow_you_into_a_new_chat_and_attaching_returns_focus | fixed |
| R3-21 | sidebar | The new-folder row wrapped and its placeholder was long. | The row does not wrap and the placeholder is short. | test_the_new_folder_row_does_not_wrap_and_its_placeholder_is_short | fixed |
| R3-22 | import | The chat import used technical words, and its loading text flashed on a quick answer. | It reads in plain words, and the loading text waits 300 ms. | test_the_import_reads_in_plain_words_and_loading_text_waits_300_ms | fixed |
| R3-23 | web UI | A toast covered the preview bar. | Toasts sit clear of it. | test_toasts_sit_clear_of_the_preview_bar | fixed |
| R3-24 | web UI | Server messages wrote the product name with a capital letter. | They use the lowercase name. | test_visible_server_strings_use_the_lowercase_name | fixed |
| R3-25 | memory | "Noted for next time" showed after a plain email request ("don't make it too formal"). | A plain request is not taken for a standing preference. | test_a_plain_request_is_not_taken_for_a_standing_preference | fixed |
| R3-26 | routing | Auto sent office requests to an outside engine, and an office turn in such a chat stayed there. | Any request whose main input or output is an office file goes to meshpool's own agent (Work in Chat mode, Code in the Code tab), and an office turn in a chat Auto gave to an outside engine moves there. Code work still goes by the table, and a picked outside agent stays available. | test_priyas_workbook_request_on_auto_runs_on_meshpool_work_with_codex_installed, test_office_files_route_to_meshpools_own_agent_in_both_modes, test_daniels_deck_and_memo_in_a_code_chat_with_a_folder_run_on_meshpools_own_agent, test_an_office_turn_in_a_chat_auto_gave_to_codex_moves_to_meshpools_own_agent, test_code_work_still_goes_by_the_table_and_a_picked_outside_agent_stays_available | fixed |
| R3-27 | session | The reopened-session line named an engine. | It names none. | test_the_reopened_session_line_names_no_engine | fixed |
| R3-28 | office files | A workbook an outside agent made had no formula values. | meshpool fills the values for every harness at the turn's end, only in workbooks the turn wrote. | test_a_workbook_an_outside_agent_made_gets_its_values_at_the_turns_end, test_value_filling_skips_a_workbook_the_turn_only_read | fixed |
| R3-29 | agent core | A note told the model to keep something from the user. | No text sent to the model asks it to keep something from the user. | test_no_text_sent_to_the_model_asks_it_to_keep_something_from_the_user | fixed |
| R3-30 | Word files | A Word save could change more than typos. | A save fixes typos only and says which. | test_the_save_fixes_typos_only_and_says_so_in_plain_words | fixed |
| R3-31 | look review | The look result was a sentence in the reply. | It is a short muted footer line ("Checked · all good") with the sentence in its fold. | test_the_look_line_is_not_added_to_the_reply, test_the_footer_line_is_short_with_the_sentence_in_its_fold, test_the_turn_footer_carries_the_look_and_the_reply_has_no_checked_sentence | fixed |
| R3-32 | Chat mode | Chat mode asked for code or file edits. | It asks for outcomes and offers to do things. | test_chat_mode_gets_one_paragraph_about_outcomes_and_the_code_tab_does_not | fixed |
| R3-33 | charts | A picture saved in the folder was not said to be shown in the chat. | It is said to be shown. | test_a_chart_saved_in_the_folder_is_said_to_be_shown_in_the_chat | fixed |
| R3-34 | agent core | A command, markup or a path could end a turn as the final answer. | It is nudged once, then the turn ends on what it made. An outside engine is asked once, then ends plainly. | test_a_raw_command_as_the_final_answer_is_nudged_once, test_a_second_raw_command_ends_on_what_the_turn_made, test_an_outside_engine_that_answers_with_a_command_is_asked_once_then_ends_plainly, test_what_counts_as_a_command_instead_of_an_answer | fixed |
| R3-35 | attachments | A dropped file was copied into the user's folder. | It is kept with the chat in meshpool's data. A file already in the folder is used where it is. | test_a_dropped_file_is_kept_with_the_chat_and_one_in_the_folder_is_used_where_it_is | fixed |
| R3-36 | agent core | Tool markup reached the reply and the transcript. | It never does. | test_tool_markup_in_a_reply_is_not_shown_or_saved | fixed |
| R3-37 | look review | A form that only says thank you passed the review. | The reviewer and a measured check flag it, and an honest form is not flagged. | test_a_form_that_only_says_thank_you_is_flagged_and_an_honest_one_is_not | fixed |
| R3-38 | agent prompts | The address rule and the backup rule pushed Pi's coding-chat context past its 450-character budget. | Pi gets a shorter address line and no backup rule. | test_the_prompt_says_you_never_the_third_person | fixed |
| R3-39 | agent core | The same history move (a reset, a rebase, a checkout of a ref, a revert, an amend, `branch -D` or a stash drop) sent twice in a row ran twice. | The second one is answered with "Skipped: this is the same command you just ran". A look in between allows the move again. | test_1_the_same_history_move_twice_in_one_reply_runs_once, test_1_a_look_in_between_allows_the_same_move_again, test_1_history_key_names_the_moves | fixed |
| R3-40 | agent core | A true/false or a number where a command goes reached the screen as a raw error. | The model gets a plain message that says what a valid call looks like. | test_3_a_bool_for_cmd_is_a_plain_tool_error_to_the_model, test_3_the_tool_workspace_itself_does_not_say_empty_for_a_bool | fixed |
| R3-41 | approvals | A local `git commit` with a heredoc message was denied in `-p` default and edits mode. | The quoted-delimiter message body is taken out before the command is judged, so it is only a message. Other forms still ask. | test_4_a_local_commit_with_a_heredoc_message_runs_in_p_mode, test_4_the_rule_for_local_git_writes_knows_the_heredoc_forms, test_4_other_forms_still_ask | fixed |
| R3-42 | terminal | The footer said "ask before changes" while edits and commits ran with no prompt. | The footer and the `--permission-mode` help use the mode names of config.MODE_INFO ("Ask: runs in this folder, asks before risky steps"). | test_5_the_terminal_footer_uses_the_mode_words_of_config, test_5_permission_mode_help_shows_both_names | fixed |
| R3-43 | settings | Tips could not be turned off. | A Show tips setting (MH_TIPS, on by default) hides tip cards in the app and the Try suggestion in the terminal. The environment wins for one process. | test_6_show_tips_is_a_setting_on_by_default_and_the_terminal_follows_it, test_6_the_environment_wins_for_one_process | fixed |
| R3-44 | web UI | A tip card had no way to turn tips off. | Each tip card has a "Never show tips" link. A card that is not a tip has none. | test_6_a_tip_card_has_a_never_show_tips_link_that_turns_tips_off, test_6_a_card_that_is_not_a_tip_has_no_never_link | fixed |
| R3-45 | Session tab | The Session tab listed every connector, and "Working as" was unclear. | Connectors start folded as "Connectors (N)", and "Working as" reads "Kind of task" with a tooltip. | test_7_the_session_tab_folds_connectors_and_explains_the_route | fixed |
| R3-46 | agent core | A turn that ended on "I'll patch both." was not nudged, and an unreadable call written after a promise hid it. | The promise check knows "patch" and ignores unreadable call junk, in `-p` default and edits mode. | test_8_the_promise_check_knows_patch_and_ignores_unreadable_call_junk, test_8_a_promise_with_a_malformed_call_is_pushed_on_in_p_mode | fixed |
| R3-47 | receipt | A patch followed by a test run in one step was counted as an edit made after the test, so the receipt said "not verified". | A test in the same step as the edit verifies it. A script that writes and then tests counts as tested. | test_2_a_test_run_in_the_same_step_as_the_edit_verifies_it, test_2_a_script_that_writes_then_tests_counts_as_tested | fixed |
| R3-48 | receipt | A search that exits 1 with no output was listed as a failure. | "No match" is not a failure. The receipt skips such a search and names the run that verified the change ("Verified: pytest, 14 passed"). | test_2_a_search_with_no_match_is_not_a_failure, test_2_the_receipt_skips_a_no_match_search_and_names_the_test_run, test_2_the_runner_and_counts_are_named | fixed |
| R3-49 | outside engines | A silent Codex ended the turn on "Codex sent nothing for 300 seconds" with the name of a setting. | The turn ends with "The model stopped answering. Your files are as the last step left them." and a Try again button. | test_a_silent_codex_ends_the_turn_in_plain_words_with_try_again, test_the_stall_line_has_a_try_again_button | fixed |
| R3-50 | outside engines | A stall before any step was not an engine failure, so Auto kept the chat on the stalled engine. | It counts as an engine failure, and Auto moves the message to meshpool's own agent. | test_a_codex_silent_before_any_step_counts_as_an_engine_failure | fixed |
| R3-51 | PDF | "Make a PDF of X" failed in Codex's sandbox, which refuses the local socket LibreOffice needs. It took 9 steps and ended on a card with the raw soffice command and a JSON blob. | It runs on meshpool's own converter in one step, with the plain card "Convert X to PDF". A request with more work goes to the engine. | test_a_pdf_request_is_one_conversion_with_a_plain_card, test_a_pdf_request_with_more_work_goes_to_the_engine | fixed |
| R3-52 | approvals | A raw soffice permission card from Codex read as a command. | The card's title is the plain "Convert Board_Update_Q3.pptx to PDF" and the command goes in the Details fold. | test_a_raw_soffice_permission_card_reads_in_plain_words | fixed |
| R3-53 | outside engines | Codex said "LibreOffice would not convert in this environment", or spent 9 steps and asked to run it outside. | In Ask and Accept edits, a chat with the office tools tells Codex once per session to make PDFs and page pictures with meshpool's convert_document, which runs outside the sandbox. | test_codex_is_told_to_convert_and_render_with_meshpool_s_office_tools, test_soffice_cannot_run_inside_codex_s_sandbox_but_meshpool_s_converter_can | fixed |
| R3-54 | outside engines | A reply linked a backup an outside agent had made in a temporary folder, and the link opened "That folder is not one of your projects". | Outside agents are told not to make backups outside the project, and a reply never links one. | test_a_reply_never_links_a_backup_outside_the_project, test_outside_agents_are_told_not_to_point_to_scratch_copies | fixed |
| R3-55 | outside engines | Codex could change a workbook that was open in Excel without asking. | It asks first (office_guard). A closed file gets no question, and a command that saves over an open workbook asks even in Full access. | test_codex_asks_before_changing_a_workbook_open_in_excel, test_the_open_file_card_is_not_asked_for_a_question_or_a_closed_file, test_a_command_that_saves_over_an_open_workbook_asks_even_in_full_access | fixed |
| R3-56 | look review | The look did not draw the pages a change touched (tracked changes, "section 3"). | It draws the pages the request names and says how many it saw. | test_the_look_picks_the_pages_the_request_names, test_the_look_draws_section_3_of_a_five_page_policy | fixed |
| R3-57 | look review | The Checked line said "all good" over a blind spot or a reply that ended unsure. | It claims only what it saw, and never says all good under a reply that ends unsure. | test_the_checked_line_claims_only_what_it_saw, test_no_all_good_under_a_reply_that_ends_unsure | fixed |
| R3-58 | agent prompts | A reply spoke of the user in the third person ("she appears to have the workbook open"). | The prompt tells the model to say "you", also when a lock file or author field names the user. | test_the_prompt_says_you_never_the_third_person | fixed |
| R3-59 | file cards | An .xlsx file card had a generic icon, while .pptx, .docx and .pdf cards had thumbnails. | It gets a thumbnail of the first sheet. | test_an_xlsx_card_gets_a_first_sheet_thumbnail | fixed |
| R3-60 | chat | A long wait said only "Still waiting for the model". | The long-wait line names the last step. | test_a_long_wait_names_the_last_step | fixed |
| R3-61 | decks | Some slide titles were not in a title placeholder. | Every slide title is in one. | test_every_slide_title_is_in_a_title_placeholder | fixed |
| R3-62 | decks | A deck got no speaker notes unless the request asked for them. | Notes are written by default and turned off by "no notes". | test_notes_are_written_by_default_when_the_request_does_not_mention_them, test_no_notes_turns_them_off, test_notes_wanted_reads_the_request | fixed |
| R3-63 | decks | Long category names were tilted to 45 degrees. | A column chart whose names do not fit is drawn as bars with flat names, and an annotated one sets its labels horizontal so they wrap. | test_long_category_names_are_never_tilted_to_45_degrees | fixed |
| R3-64 | decks | A source line showed the file name in brackets after the report's title. | It drops the file name once the title is known. | test_the_source_line_drops_the_filename_in_brackets_when_the_report_title_is_known, test_a_built_deck_has_no_filename_in_a_source_footer | fixed |
| R3-65 | decks | Slides carried caveats nobody asked for and "N/A" or "not stated". | The slide writer is told to leave out such caveats and N/A, and a table gap prints neither. | test_the_deck_writer_prompt_bans_caveats_nobody_asked_for_and_na_on_a_slide, test_a_gap_is_not_printed_as_na_or_not_stated_on_a_slide | fixed |
| R3-66 | attachments | An attached PDF in a chat with no folder reached the model as three chart pages and no text. | The text goes with the message, with the figure pages as images. | test_an_attached_pdf_reaches_the_model_as_text_and_figure_pages_in_a_chat_with_no_folder | fixed |
| R3-67 | no-folder chat | A file asked for in a chat with no folder had nowhere to go, and the model did not know it. | It is saved in the chat's own folder, and the note reaches the model. | test_a_file_asked_for_in_a_chat_with_no_folder_is_saved_in_the_chats_own_folder, test_the_note_reaches_the_model_in_a_chat_with_no_folder | fixed |
| R3-68 | no-folder chat | A reply sent the user elsewhere to do the work. | It becomes a plain offer, and the session adds an "Add a folder" action under it. | test_a_reply_that_sends_the_user_elsewhere_becomes_a_plain_offer, test_the_session_adds_an_add_a_folder_action_under_such_a_reply | fixed |
| R3-69 | Undo | Undo put the message back as a draft, but Enter did nothing because the box was not focused. | The message is in the focused box with the cursor at the end, and Enter sends it. | test_after_undo_the_message_is_in_the_focused_box_with_the_caret_at_the_end_and_enter_sends_it | fixed |

## Persona round 4 (app/tests/test_maya_round4.py, test_maya_round4_ui.py, test_omar_r4.py)

The fourth persona round: Maya, with Lina, Priya, Sara and Daniel, and Omar (the command line).

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| R4-01 | agent core | A reply said its tool was not available while 24 tools were offered ("the tool I use to edit your page wasn't available just now"), and the turn ended with no call. In a look fix round the turn ended the same way. | A reply that says its tools are unavailable while tools were offered is sent back once: "Your tools are available: <names>. Use them to do the change." The note is given once per turn, and the fix round runs. | test_replies_that_say_the_tools_are_unavailable_are_recognised, test_ordinary_replies_are_not_taken_for_a_missing_tool, test_a_reply_that_says_its_tool_is_unavailable_is_sent_back_once_with_the_tools, test_the_tools_note_is_given_once_per_turn, test_the_look_review_fix_round_runs_after_a_tool_unavailable_reply, test_a_web_chat_that_says_its_tools_are_missing_is_nudged | fixed |
| R4-02 | agent core | "I'm fixing that line and running it again." ended the turn, and Progress said Done. | Work said to be under way counts as a promise, so the nudge fires, also in a web chat. | test_work_said_to_be_under_way_is_a_promise, test_a_web_chat_that_stops_on_work_under_way_is_nudged | fixed |
| R4-03 | look review | Every follow-up after a look review carried the review's two page pictures. | Pictures of an earlier turn are dropped when a new turn starts. | test_a_new_turn_carries_no_pictures_from_an_earlier_look | fixed |
| R4-04 | look review | A fix round's reply led with "I couldn't fix the problem" though the rest was done. | The answer leads with what was made. | test_a_fix_round_answer_leads_with_what_was_made | fixed |
| R4-05 | Chat mode | Reading a price list ran "cat; cp; unzip" and asked "Run 5 steps?", and a coding chat in Chat mode had no office tools until a message named an office file. | Chat mode keeps read_document in coding chats and reads office files with it, not with unzip or a script. Without the reader the command still runs. | test_chat_mode_reads_an_office_file_with_the_reader_not_unzip, test_without_the_reader_the_command_runs, test_chat_mode_keeps_the_reader_in_a_coding_chat, test_chat_mode_context_says_to_read_office_files_with_the_reader | fixed |
| R4-06 | Chat mode | "Freeze the top row" went to a hand-written openpyxl script that rewrote the workbook. | Chat mode changes a file the user has with edit_office_file, which has a new set_column_width op. | test_chat_mode_changes_a_workbook_the_user_has_with_the_editor, test_the_editor_sets_a_column_width | fixed |
| R4-07 | Word files | "Shorten section 3 by about a third" lost half its words, and the reply said a third. | A tracked edit reports the words the reader sees before and after. | test_a_tracked_cut_reports_the_words_the_reader_sees | fixed |
| R4-08 | charts | A chart request counted as a quick text answer, whose note says "Do not create or change files", so the chart came as bars of text. | Chat mode asks for a chart as a PNG picture, and a chart request is no longer a quick answer. | test_chat_mode_asks_for_a_chart_as_a_picture, test_a_chart_request_is_not_a_quick_answer | fixed |
| R4-09 | footer | The footer said only that something was open. | It names a single open point ("1 to look at: legend sits on ..."). A count such as "Still open: 1 thing" names nothing, so the footer keeps "1 to look at" then. | test_the_footer_names_the_one_open_point | fixed |
| R4-10 | footer | "I haven't opened it to confirm... My check of the workbook didn't finish." sat under "Checked · all good". | open_doubt reads the first paragraph and phrases such as "haven't opened", "didn't finish" and "unchecked", so the footer is not all good. | test_a_reply_unsure_of_its_own_check_is_not_all_good | fixed |
| R4-11 | copy | Receipts and the start line wrote the product name with a capital letter. | They say lowercase meshpool. | test_receipts_name_meshpool_in_lowercase, test_the_start_line_says_meshpool_in_lowercase | fixed |
| R4-12 | Undo | After Undo the chat was back on the invoice question but still read "Bouquet price list". | Undo puts the chat title back when no file it was named after is left. | test_undo_puts_the_title_back | fixed |
| R4-13 | file cards | A chart came as two file cards, a PDF and a PNG of the same picture. | One card shows the picture with both formats. | test_one_picture_in_two_formats_is_one_card | fixed |
| R4-14 | receipt | A read-only command after a green test run was taken as the check, and an earlier run could win over the last one. | The receipt names the last test run after the last edit, for pytest and the other runners. | test_a_read_only_command_after_the_tests_is_not_the_check, test_the_last_test_run_wins_over_an_earlier_one, test_other_runners_count_too | fixed |
| R4-15 | receipt | Failures that were there before the turn's first edit made the line "Not verified". | Failures with the same test ids as before do not, a new failure still does, and with no earlier ids the line gives counts. One error counts as one error. | test_failures_that_were_there_before_the_first_edit_do_not_make_it_not_verified, test_a_new_failure_is_still_not_verified, test_without_the_earlier_ids_it_says_the_counts_and_not_not_verified, test_one_error_is_an_error | fixed |
| R4-16 | receipt | A test run piped, or followed by `|| true`, with no clean summary looked verified. | It is Not verified. | test_a_piped_or_ignored_test_run_without_a_clean_summary_is_not_verified | fixed |
| R4-17 | command line | A denied write command in `-p` gave the model no way forward. | It is told to use the edit tool, and a denied command that writes nothing gets no such hint. | test_a_denied_write_command_in_p_tells_the_model_to_use_apply_patch, test_a_denied_command_that_writes_nothing_gets_no_edit_tool_hint | fixed |
| R4-18 | command line | Receipt lines printed raw asterisks in `-p` and the terminal UI. | They print without asterisks, and the terminal UI shows bold labels. | test_p_output_shows_the_receipt_without_asterisks, test_the_terminal_ui_shows_bold_labels_not_asterisks | fixed |
| R4-19 | terminal | Tips could not be switched off from the terminal. | `/tips on` and `/tips off` save the Show tips setting, hide the Try placeholder, and are listed in /help and the slash menu. | test_tips_off_hides_the_try_placeholder_in_the_terminal_ui, test_tips_off_command_saves_the_setting_and_hides_the_placeholder, test_tips_is_listed_in_help_and_in_the_slash_menu | fixed |
| R4-20 | command line | A final reply that ended in a promise paragraph kept it. | The trailing promise paragraph is dropped from the answer and kept in the log. A reply that is only a question or a plain answer is left alone, and `-p` writes the trimmed answer. | test_a_final_reply_ending_in_a_promise_is_shown_without_that_paragraph, test_a_reply_that_is_only_a_question_or_a_plain_answer_is_left_alone, test_print_mode_writes_the_trimmed_answer | fixed |
| R4-21 | step log | The key=value redactor read the "auth" in Co-Authored-By as a secret name and hid the value. | A commit trailer keeps the word Claude in the step log. Names that are real secrets are still hidden. | test_a_commit_trailer_keeps_the_word_claude_in_the_step_log, test_real_secrets_are_still_hidden | fixed |

## Release review of the persona fixes (app/tests/test_release_review_018.py and the tests named in each row)

A review of the persona fixes, commits 127238cf to e1dad99e, before the release. The reviewer's own findings are P1 to P6 (RF-01 to RF-06). The rest were found by the hunts that went through the same range.

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| RF-01 | approvals | A heredoc commit message ended at a later `EOF` line, so lines between that bash runs were taken as the message: `cat $HOME/.ssh/id_rsa > leak.txt` ran with no card. | The message ends where bash ends it, at the first line that is exactly the delimiter. Only the commit's own -m is rewritten. Local git writes in Accept edits run in the offline sandbox too. | test_a_command_hidden_after_a_commit_message_asks, test_a_real_heredoc_commit_message_still_runs | fixed |
| RF-02 | approvals | `git restore` and `git checkout` of a file with uncommitted changes the chat did not make ran unasked after a `cd` or `git -C`, and `checkout -f` and `switch -f` ran unasked. | They ask, also after a `cd` or `git -C`. A file with nothing to lose still runs. | test_losing_the_users_uncommitted_work_asks, test_nothing_to_lose_still_runs | fixed |
| RF-03 | attachments | A chat could read the dropped files of another chat, or the folder that lists them all. | A chat reads only its own dropped files and the files that belong to no chat (config.upload_roots), also through the office tools. | test_upload_roots_are_this_chats_and_no_other, test_an_agent_reads_its_own_attachments_and_asks_for_another_chats, test_the_office_tools_read_only_this_chats_attachments | fixed |
| RF-04 | approvals | An office title covered another command: `env rm -rf ~/Documents; soffice ...` read as "Convert a.docx to PDF". | Office titles judge what `env` or `timeout` runs and refuse `$( )`. A conversion alone is still titled plainly. | test_an_office_title_never_covers_another_command, test_a_conversion_alone_is_still_titled_plainly | fixed |
| RF-05 | approvals | A "Save file" card could cover a command that did more than save. | The card shows only when writing the file is all the command does, and it says where the file goes: this folder, a temporary folder, or outside this folder with its path. A chat with no folder is "this chat's folder", not its id. | test_a_saved_file_card_only_when_saving_is_all_the_command_does, test_a_saved_file_card_says_where_the_file_goes | fixed |
| RF-06 | approvals | Looking at a price list showed "Run 5 steps: cat, cp, unzip?" with the same line again under the title. | A chain that only reads, or copies and unpacks into a temporary folder, is "Read price list.xlsx and page.html?". A chain that writes the user's files, changes permissions or deletes keeps its own title, and the page leaves out the plain line when it says the same as the title. | test_a_chain_that_only_reads_says_read, test_a_chain_that_writes_the_users_files_is_never_read, test_the_page_never_shows_the_title_twice | fixed |
| RF-07 | office files (B1) | A formula cell with no value let the match run on to the next empty value, and the whole span was written back as one cell, deleting the cells between. | Each cell is matched on its own, and only a cell with a formula and an empty or missing value changes. | test_each_cell_is_filled_on_its_own_and_no_cell_is_lost, test_only_formula_cells_with_an_empty_or_missing_value_change, test_a_real_workbook_keeps_every_cell_and_gets_its_values | fixed |
| RF-08 | no-folder chat (B2) | The check that rewrites a reply in a chat with no folder took 36 s on 2 KB, on every assistant event, rewrote correct replies and cut file names ("plot.py" became "...again.py"). | Each sentence is checked with flat keyword searches, only whole sentences are replaced, and a turn that made a file is left alone. | test_a_long_adversarial_reply_is_checked_in_well_under_a_fifth_of_a_second, test_correct_replies_are_left_unchanged, test_a_real_deflection_is_still_rewritten_and_file_names_stay_whole, test_a_turn_that_made_a_file_is_not_rewritten, test_the_session_keeps_the_reply_when_the_turn_wrote_a_file | fixed |
| RF-09 | charts (docfigures) | Finding the document a question names took 2.7 s on 20 KB and 56 s on 80 KB, on send for any message naming a chart. | Each .pdf, .docx or .pptx is found first, and only the name characters just before it (at most 160) are read, on at most 8,000 characters of the message. | test_a_long_message_that_names_a_chart_is_searched_quickly, test_the_document_a_question_names_is_still_found | fixed |
| RF-10 | routing (office_intent) | An office file name beat the code words, so "fix the bug where report.xlsx export crashes", "Add PDF export to the app" and "fix the slides carousel" left Code and Auto chats for Work. | Code cues (bug, implement, importer, handler, component, carousel, animation, a code identifier, an export or upload feature, the app, tests) win. A sheet function such as VLOOKUP is not code. | test_code_requests_that_name_an_office_file_stay_code, test_office_deliverables_and_inputs_are_still_office_work | fixed |
| RF-11 | attachments (chips) | The chip for a dropped file did not open it, because files_api refused its absolute path. | The chip opens the file from the runs folder by a relative path, only from its own chat, and a search never walks into another chat's folder. The label is the file name on Windows too, and every "Attached files" line is taken out of the message. A later change made files_api use the same upload_roots rule as the agent tools. | test_a_chat_opens_its_own_dropped_file_from_the_runs_folder, test_chat_a_cannot_open_chat_bs_dropped_file, test_both_attached_lines_go_and_windows_paths_show_the_file_name, test_a_chip_for_a_dropped_file_opens_it_in_its_own_chat_only | fixed |
| RF-12 | look review (look count) | An open problem whose text was under three words was dropped, so the footer said "Checked · all good" while a major problem was open. | It is counted: "Still open: 1 thing." | test_a_short_open_problem_is_counted_and_the_footer_is_not_all_good, test_a_fix_and_a_short_problem_left_open, test_several_short_problems_are_all_counted, test_a_whole_open_problem_is_still_said_in_words | fixed |
| RF-13 | decks (deck outside the folder) | A deck outside the folder that the chat's Undo covers (for example on the Desktop) was changed in place, where Undo cannot put it back. | The office job tells the engine the chat's folder, and a deck outside it gets its new slides in a copy "<name> (updated).pptx" in that folder. The answer says so. | test_deck_target_in_place_only_inside_the_chat_folder, test_a_deck_inside_the_chat_folder_is_changed_in_place, test_a_deck_outside_the_chat_folder_is_copied_into_it, test_the_office_job_tells_the_engine_the_chat_folder | fixed |
| RF-14 | office tools (set_notes) | The engine's notes cleaner turned "Thank the engine team." into an empty string, the slide's notes were wiped, and the tool still said "notes set". | Notes the user asked for lose only [QA] check lines. Empty notes are not written and are reported as not set, and an edit where nothing was done writes no file and says so. | test_the_users_own_notes_are_kept_as_written, test_only_qa_lines_come_out, test_no_success_is_reported_when_nothing_was_set, test_one_good_op_and_one_empty_note | fixed |
| RF-15 | decks (dates) | A date the user wrote (2027-01-29, 29/01/2027, "29th of January 2027") was read as a bare year or month, so it was taken out of the next steps and P29 called it invented. | ISO and numeric dates are whole dates (a numeric date that reads both ways counts both ways in the sources), and "29th of January" keeps its day. | test_the_users_date_is_a_whole_date, test_ground_keeps_the_users_date, test_p29_passes_the_users_date_on_a_slide, test_an_ambiguous_numeric_date_reads_both_ways_in_the_sources_only | fixed |
| RF-16 | agent core (textcalls) | Reading a trailing JSON call went through every JSON-looking line to the end of the text and copied the rest each time: 2 MB took 5 s. A JSON example the user asked for could be stripped. | A value that reads is skipped whole, a broken one up to its break, lines that cannot start JSON are not read, and failed reads are capped. With tools off, a JSON block is stripped only when it has a name and arguments and no code fence. | test_a_long_reply_of_json_lines_is_read_in_linear_time, test_a_call_nested_after_an_open_bracket_line_is_still_found, test_a_json_example_the_user_asked_for_stays | fixed |
| RF-17 | deck checks (P0) | A deck that opens but that the checker could not measure was reported as a P0 failure. | Its P0 is "not run". A deck that does not open still fails. | test_a_deck_that_opens_but_was_not_measured_is_not_a_p0_failure, test_a_deck_that_does_not_open_still_fails_p0 | fixed |
| RF-18 | retry | Try again sent the last message again without its images, and held the HTTP thread for up to 25 s while the old turn stopped. | The images are read back from the model's copy of the message (or the last send), and a running turn is waited for in the background: the route returns {"retrying": true} at once and the new attempt streams in. A failure shows as an error line. | test_try_again_while_a_turn_hangs_returns_at_once_and_sends_the_images_again, test_try_again_on_an_idle_chat_sends_the_images_again | fixed |
| RF-19 | search (srchMark) | A search for "amp" or "lt" put marks inside &amp; and &lt; and garbled the snippet. | The text is split on the words first and each piece is escaped on its own. | test_search_marks_words_in_the_text_not_in_entities | fixed |
| RF-20 | steps (small) | steps_retried was not in the transcript keep list, so it was lost when the chat was saved soon after. | It is kept with the transcript. | test_steps_retried_is_kept_with_the_transcript | fixed |
| RF-21 | settings (small) | Linux onboarding showed an empty permissions note. | The page draws no empty note, and the desktop text stays as it was. | test_an_empty_permissions_note_is_hidden | fixed |
| RF-22 | outside engines (small) | In Full access an outside engine changed a file open in Office without asking. | A live lock means the user has the file open, so it asks in Full access too. Other approvals are unchanged. | test_an_outside_engine_asks_before_changing_a_file_open_in_office_in_full_access | fixed |
| RF-23 | model calls (gateway) | The cap for a local or Ollama endpoint went from 2 to 6 calls at once, which makes a model on one machine thrash and can push it out of memory. | A local host or an Ollama server (port 11434 or the name) is back to 2. Other endpoints keep their caps. | test_a_local_or_ollama_endpoint_takes_two_calls_at_once | fixed |

## Robustness and others after the persona rounds (app/tests/test_loop_guard.py, test_delegate.py, test_grader.py)

| ID | Where | What was wrong | Fix | Test | Status |
|---|---|---|---|---|---|
| RB-01 | agent core | The three-in-a-row repeat rule looked only at edit-tool counts, so a rerun after a script or the agent changed a file was refused as a loop, as was a fixed command. | The guard counts a file changed outside the edit tools. Git commands that move state still count by edits only, so a stash and pop round trip is caught. | test_three_in_a_row_counts_a_file_changed_outside_the_edit_tools, test_a_pair_taking_turns_that_flips_the_tree_is_still_caught, test_a_command_rerun_with_nothing_changed_is_still_refused_the_fourth_time | fixed |
| RB-02 | agent core | A JSON report from a sub-agent, from the grader or from run_subtasks was taken for a malformed reply and replaced. That broke four delegate tests. | These agents set reports_to_agent: they answer the parent model, so the check for a malformed reply applies only to replies to the user. | test_delegate.py, test_grader.py (existing tests, which failed before the fix) | fixed |
| RB-03 | agent core | A shell call with an empty command list raised an error. | shell_script returns an empty script. | none (a one-line guard, not tested on its own) | fixed |

Tests changed with no product change (not counted): fixtures that named scratch folders of a test machine now use a neutral path (8e35eac4), the brand test allows the chat import's "ChatGPT or Claude" (2d0eb838), a round 3 test counts failures with no earlier run (890f8ab7), a round 2 deck is a text fixture the test rebuilds (10c88601), round 3 search tests wait for real rows (4a63d3ff), a card test uses a longer name (7e3cdfbc), and some tests follow round 4 wording (074b2b26). The same commit also shortened the address rule and the edit tool's description to fit their budgets.

## Persona testing

Five people were played on the live model, each on 0.0.17 and on this build, with the same tasks on both. Their reports
were given in the session and not saved as files. The numbers below come from those reports and the run logs. Which rows
answer each person comes from the tests, which name the person or their files.

| Persona | Score | Measured against 0.0.17 | Rows that answer their findings |
|---|---|---|---|
| Sara, finance, office files | 6/10 | Previews 2 to 3 times faster after the first file, about 35% less CPU, a third of the LibreOffice starts (LD-01). Over six Sonnet tasks: app CPU 5.0 s against 9.4 s, and 21 approvals against 28. | SR-01 to SR-14, PF-02, WF-H, AP-07, the ER rows |
| Daniel, consultant, decks | 5/10, experience 4/10 | No like-for-like CPU or approval count was recorded. | WF-03, WF-04, WF-09, PF-03, PF-06, UX-12 to UX-36 (UX-15 from his run), AP-01, AP-02, AP-06 |
| Lina, data analyst | 6/10. After the fresh-install addendum she would not switch yet. | App CPU 1.1 s against 1.9 s. Streaming CPU 26% lower. | WF-A, WF-C, WF-D, WF-E, WF-I, UX-12 to UX-36 (UX-26 from her chart), AP-01, AP-02 |
| Omar, developer, command line | 6/10 | A web Code session ran 28 git processes against 102, and an edit turn 7 against 22. A mock edit in the command line used 46% less CPU (0.97 s against 1.79 s). | OM-01 to OM-10, AP-01 |
| Maya, first-time user | 4/10 | Idle: 0.01 s CPU per 30 s. | UX-37 to UX-40, WF-J, AP-06 |

## Found, not fixed (open for 0.0.19)

Needs the owner (a design decision):
- SV: The launch key printed in the terminal in browser mode still lasts for the app's life (testkit scripts reuse it).
- PF: Onboarding still ticks the skills by default.

Known limits and open bugs:
- SV-14: A process that reads the Office webview's memory, or one that the user lets in by allowing its code, can
  still use the panel's routes. A paired panel can answer approvals "yes" once, as before; "always" stays in the app.
- LP-01: In a container without `timeout` (coreutils or busybox), Stop ends only the top process.
- LP-05: An old chat that names an upload by path loses that file after 30 days. Today only company profiles upload,
  and learning a profile copies the files into the brand store.
- LQ-02: A merged slide's comments keep author ids from the source deck. This was broken before as well.
- ER: A persona saw a "Read: ... FileNotFoundError" line. Where that line comes from was not found, so it is not fixed.
- ER: The connection check still says "The address answered, but not like a model API." for a 200 web page or an
  empty 200 body (provider_setup.py), where the chat now names both (ER-02, ER-03).
- PF: The other agent adapters (harnesses/acp.py, harnesses/pi_rpc.py) still cut chat titles at 80 characters.
- PF: With tools on, XML that names a tool the model was not offered is shown raw.
- WF: Undo cannot put back files a shell command changed outside the chat's folder. It names them (WF-C).
- SR: A file name that contains ", " shows as two chips, because the attachments line is split at ", ".
- UX: Two chats with the same title cannot be told apart in the sidebar.
- UX: Past-chat search results show no snippet of the match.
- UX: Dialogs close at once, with no motion.
- OM: The note the model gets when `-p` refuses a step (DENIED_NOTE in agent/tui_session.py) still suggests
  `--permission-mode edits`.
- AP: Keynote and Pages leave no lock file, so a Keynote or Pages file open on a Mac can be replaced without a question.
- PR: The persona reviewer cannot run on the ChatGPT-subscription wire (_aux_complete refuses there). The review falls
  back to the measured findings, and the line says "Checked: measured only".
- LP2: In the interactive terminal, the first answer before the check has already streamed and cannot be folded. A dim
  note says a final answer follows.
- LP2: Only the look review sends the review_round fold. The grader and independent-review rounds do not, so their
  first answers are not folded.
- RV: The last builder fix after round 2 is not reviewed again (the two-round cap).
