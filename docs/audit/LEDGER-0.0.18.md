# meshpool 0.0.18 bug ledger

Bugs found after 0.0.17 shipped, by six bug hunts (one per area: CPU, web UI, agent core, office, server, command line)
and by the lead. Each hunt worked in its own worktree. It wrote a test that failed before its fix, then reverted the
fix once to see the test fail again. Rows from the 0.0.17 ledger were not counted again.

A second round followed. Five persona testers ran this build against 0.0.17 on the live model (see Persona testing).
Their findings went to hunts for error messages (ER), functional fixes (PF), workflow (WF), office and first run (SR),
UI smoothness (UX), the developer command line (OM) and approvals (AP). A pre-release review (RR) and the merges (MG)
added the rest. A row that repeats another is listed but counted once, where the row says.

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
| all | 239 | 237 | 17 |

"Still open" counts what the "Found, not fixed" section lists today: two questions for the owner and fifteen known
limits and open bugs, all open for 0.0.19. What the first pass found but left was fixed in the later pass.

Rows listed but not counted: WF-E (counted under WF-07), SR-12 (under UX-20), SR-14 (under AP), AP-08 (under UX-33),
OM-07 (not a bug) and UI-14 (under LD-06).

Two fixes have no test. LD-07: the race behind it could not be made to happen reliably. MG-02: a word list.

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
UX tests open the top-level Permissions page (03fad180).

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
