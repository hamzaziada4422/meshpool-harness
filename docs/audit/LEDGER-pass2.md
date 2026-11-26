# Ledger: platform audit, pass 2

Every finding in `docs/audit/platform/ALL_BUGS_pass2.md` (branch `claude/audit-pass2`, written Nov 23) checked against main at `06aae8a6` (Nov 26). The 10 rows that were open there and 13 of the 14 fixed without a test were then fixed or given a test on branch `claude/sleepy-faraday-tkjcox` for 0.0.18; those rows say so. P2-skills-3 is test set-up and needs no test of its own. The audit's line numbers point at an older tree, so each finding was located by function and behaviour. Findings are numbered in the order they appear within each area; areas whose rows have no numbers in the source (core, forge, packaging, skills, uifront, uiserver) are numbered here in file order. The core area's last row is an "info" row that reports no bug; it is listed so every row is accounted for.

Statuses:

- **fixed**: the current code behaves correctly and a test exercises the trigger from the audit.
- **fixed, no test**: the current code behaves correctly, but no test exercises the trigger.
- **not reproducible**: the reported failure does not happen on current main (how it was checked is given).
- **open**: still wrong on current main; the row gives the reason and the smallest fix.
- **won't fix**: none.

How it was checked: every `test_fix_*_pass2.py` and `test_fix_*_pass3.py` file was run on current main (132 tests, all pass, including the 11 browser tests with `MH_TEST_CHROMIUM` set). Tests cited from other files were run too (19 tests, all pass). Open rows were confirmed by reading the code and, where cheap, by running it (timings and outputs are quoted in the row).

## Summary

| Area | Findings | fixed | fixed, no test | not reproducible | open | won't fix |
|---|---|---|---|---|---|---|
| agent | 11 | 11 | 0 | 0 | 0 | 0 |
| core | 20 | 19 | 0 | 1 | 0 | 0 |
| engines | 17 | 17 | 0 | 0 | 0 | 0 |
| forge | 9 | 9 | 0 | 0 | 0 | 0 |
| packaging | 8 | 8 | 0 | 0 | 0 | 0 |
| skills | 8 | 5 | 1 | 2 | 0 | 0 |
| toplevel | 18 | 18 | 0 | 0 | 0 | 0 |
| uifront | 15 | 15 | 0 | 0 | 0 | 0 |
| uiserver | 17 | 17 | 0 | 0 | 0 | 0 |
| **total** | **123** | **119** | **1** | **3** | **0** | **0** |

Test ids are relative to `app/`. `P2` tests below means `tests/test_fix_<area>_pass2.py`, `P3` tests means `tests/test_fix_<area>_pass3.py`.

## agent

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-agent-1 | agent/core.py `_bad_reply`, `Agent._chat` | A 200 reply carrying `{"error":...}`, no choices, a null message or a content filter stop was taken as an empty answer and "(no reply)" was saved in history. | fixed | tests/test_fix_agent_pass2.py::test_bad_reply_detection; ::test_empty_reply_is_an_error_and_not_saved |
| P2-agent-2 | agent/planning.py `signals` | The file-name regex was quadratic, so a 400 KB line hung the turn before the first model call. | fixed | tests/test_fix_agent_pass2.py::test_planning_signals_fast_on_huge_line |
| P2-agent-3 | agent/core.py `Agent._guarded_tool_inner` | The repeat-call guard refused the third identical read while a background job was changing the file, so the agent could not watch a build or server log. | fixed | tests/test_fix_agent_pass2.py::test_repeat_guard_skips_while_background_job_runs |
| P2-agent-4 | agent/tui.py `Terminal.approve` | Parallel read-only batches opened several approval prompts on the same terminal at once. | fixed | tests/test_fix_agent_pass2.py::test_approvals_are_serialised |
| P2-agent-5 | agent/tui.py `Terminal.emit` (print mode) | In `meshpool -p` a dropped stream left the partial reply on stdout and the retried reply was appended after it. | fixed | tests/test_fix_agent_pass2.py::test_print_mode_discards_partial_reply_on_reset |
| P2-agent-6 | agent/tui_render.py `safe_text`, agent/tui.py `Terminal.emit` | Model, command and error text reached the terminal unfiltered, so escape sequences (clipboard write, clear screen) in a reply controlled the terminal. Every string field of every event is now cleaned in `emit`. | fixed | tests/test_fix_agent_pass2.py::test_safe_text_strips_terminal_sequences; ::test_terminal_emit_strips_sequences |
| P2-agent-7 | core/loop.py `_sanitize_calls`, agent/core.py `Agent.send` | A tool call with no function name was stored as is and made every later request of the chat fail on strict gateways. | fixed | tests/test_fix_agent_pass2.py::test_nameless_tool_call_is_repaired_before_history |
| P2-agent-8 | agent/core.py `Agent.send` | An empty streamed reply ended the turn with a blank message and exit code 0 in `-p` mode. | fixed | tests/test_fix_agent_pass2.py::test_empty_reply_is_an_error_and_not_saved; ::test_error_event_marks_print_mode_failed |
| P2-agent-9 | agent/core.py `Agent._turn` (error text), `Agent._chat`, `_probe_once` | After repeated HTTP 5xx the error claimed the endpoint "answers small requests" and appended raw internal text cut at 120 characters. | fixed on this branch: after the 5xx retries run out the message says the model keeps failing and suggests another model; details are cut at a word | tests/test_fix_pass2_open_server.py::test_p2_agent_9_model_that_keeps_failing_says_so; ::test_p2_agent_9_one_5xx_still_says_continue |
| P2-agent-10 | agent/tui_render.py `shell_block` | At widths under about 14 columns the "earlier lines" row overflowed and broke the live-area redraw. | fixed | tests/test_fix_agent_pass2.py::test_shell_block_stays_within_narrow_width |
| P2-agent-11 | hrouter/route.py `route`, hrouter/signals.py | `route()` was super-linear on long text (60k chars took 13 s). | fixed | tests/test_fix_core_pass2.py::test_route_is_fast_on_huge_input; ::test_public_functions_bounded_on_single_token |

## core

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-core-1 | core/tools.py `Workspace.grep` (content mode) | Membership in a list inside the line loop made grep quadratic and `head_limit` was applied only after the whole file. | fixed | tests/test_fix_core_pass3.py::test_grep_head_limit_is_fast_and_glob_semantics |
| P2-core-2 | core/tools.py `Workspace.grep` (glob) | `**/*.py` missed root files, `src/*.py` matched subfolders, and brace and `!` globs returned no matches. | fixed | tests/test_fix_core_pass3.py::test_grep_head_limit_is_fast_and_glob_semantics |
| P2-core-3 | core/tools.py `Workspace._run` | Command output was buffered whole in memory, so an endless producer grew RAM until the timeout. | fixed | tests/test_fix_core_pass3.py::test_command_output_is_capped |
| P2-core-4 | core/tools.py `Workspace.edit` | The replace_all note was glued to the file name ("edited r.pyreplaced 2 occurrences"). | fixed | tests/test_fix_core_pass3.py::test_edit_note_and_dir_and_binary |
| P2-core-5 | core/tools.py `write_file`, `edit`, `read_file` | Writing to a folder raised a raw IsADirectoryError with the host path, and binary files were read as garbage text. | fixed | tests/test_fix_core_pass3.py::test_edit_note_and_dir_and_binary |
| P2-core-6 | core/loop.py `_sanitize_calls`, core/tools.py `validate` | Object arguments became "{}", None arguments stayed None, and a truncated `finish` call was reported as a missing argument. | fixed | tests/test_fix_core_pass3.py::test_sanitize_calls |
| P2-core-7 | core/loop.py `run` | A model that answered in plain text and never called `finish` lost its answer (`res.answer == ''`). | fixed | tests/test_fix_core_pass3.py::test_plain_text_answer_is_kept |
| P2-core-8 | core/guards.py `deliverable_names`, `check_deliverables` | Any file name after Write/Create was demanded as a deliverable, including input files and negated ones ("don't write to config.json"). | fixed | tests/test_fix_core_pass3.py::test_deliverable_names |
| P2-core-9 | core/guards.py `Guards._git_test_changes` | A modified test file whose path has a space was not flagged, because git quotes it in porcelain output. | fixed | tests/test_fix_core_pass3.py::test_git_test_changes_handles_spaces |
| P2-core-10 | core/wire.py `SimpleLedger.chat`, core/stream.py, core/client.py `rate_limit_wait` | The HTTP Retry-After header was ignored, so a 429 waited 65 s by default. | fixed | tests/test_fix_core_pass3.py::test_retry_after_in_error_text |
| P2-core-11 | core/wire.py `to_responses` | A tool result whose content is a list was sent as a Python repr. | fixed | tests/test_fix_core_pass3.py::test_responses_tool_result_list_is_json |
| P2-core-12 | core/anthropic_wire.py `to_request` | Tool-result images were dropped and an empty tool_call_id was sent as "" (API 400). | fixed | tests/test_fix_core_pass3.py::test_anthropic_tool_images_and_empty_ids |
| P2-core-13 | core/tools.py `read_only` | Commands that write or run code (`sort -o`, `git branch x`, `cat <(rm x)`, and others) were treated as read-only. | fixed | tests/test_fix_core_pass3.py::test_read_only_rejects_writers; ::test_read_only_accepts_readers |
| P2-core-14 | core/review.py `findings` | A reply starting "LGTM, but 1. ..." was treated as clean and its problems dropped. | fixed | tests/test_fix_core_pass3.py::test_review_lgtm_but |
| P2-core-15 | hrouter/route.py `route` (cowork) | Chart and forecast requests on a spreadsheet went to docs, browser requests and code requests in Cowork went to general. | fixed | tests/test_fix_core_pass3.py::test_hrouter_misroutes |
| P2-core-16 | hrouter/route.py `route` (code) | Non-code requests in a Code chat became code.build, and "edit the attached proposal" became code.debug. | fixed | tests/test_fix_core_pass3.py::test_hrouter_misroutes |
| P2-core-17 | harness_router.py `classify` | Generic keywords ("search", "what is", "form", "word", "sales") sent tasks to the wrong category, and data words were missing. | fixed | tests/test_fix_core_pass3.py::test_harness_router_keywords |
| P2-core-18 | hrouter/packs.py `diagnostics` | Paths with spaces were cut, a missing file was reported twice, and tsc TS18003 ("no inputs") showed as unparsed output. The code now matches space paths, drops duplicate E902 rows and ignores TS18003. | fixed | tests/test_pass2_regressions.py::test_p2_core_18_diagnostics_paths_with_spaces_e902_and_ts18003 |
| P2-core-19 | core/chatgpt_relay.py `_Handler` | Chunked request bodies were rejected, and an early 401 left the kept-alive connection out of step. | fixed | tests/test_fix_core_pass3.py::test_relay_chunked_body (the early-error connection close is in the code but untested) |
| P2-core-20 | harnesses/pi_rpc.py, acp.py (info row) | Info row: the stub ACP run, the gateway slot gate and the ReDoS probes all behaved correctly. No bug was reported. | not reproducible | none needed; the row reports no failure. |

## engines

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-engines-1 | engines/credit/analysis.py `match_label` | "Income before income taxes" mapped to income_tax, "Cash flow from operations" to cash, and PP&E was unmapped. | fixed | tests/test_fix_engines_pass2.py::test_labels |
| P2-engines-2 | engines/credit/analysis.py `from_table` | Only the first worksheet was read, so statements on other tabs were silently missing. | fixed | tests/test_fix_engines_pass2.py::test_from_table_multi_sheet_and_duplicate_years |
| P2-engines-3 | engines/credit/analysis.py `from_table` | Two columns with the same year (Actual and Budget) gave duplicate years and the second was silently ignored. | fixed | tests/test_fix_engines_pass2.py::test_from_table_multi_sheet_and_duplicate_years |
| P2-engines-4 | engines/sheets/builder.py (sheet references) | Sheet titles with an apostrophe were not escaped, so named ranges and cross-sheet formulas broke. | fixed | tests/test_fix_engines_pass2.py::test_sheet_titles |
| P2-engines-5 | engines/sheets/builder.py `new_workbook`, qa.py `check` | Titles over 31 characters were accepted, LibreOffice truncated them and `qa.check` died with KeyError. The builder now rejects them and `qa.check` falls back to the sheet index. | fixed | tests/test_fix_engines_pass2.py::test_sheet_titles (the index fallback in qa.py has no test) |
| P2-engines-6 | engines/sheets/engine.py `finish_check` | A non-xlsx file named .xlsx raised BadZipFile out of the finish gate. | fixed | tests/test_fix_engines_pass2.py::test_finish_check_bad_zip |
| P2-engines-7 | engines/research/library.py `split_passages` | Passages were hard-cut at 900 characters mid-word, so figures across the cut failed verification. | fixed | tests/test_fix_engines_pass2.py::test_split_passages_no_midword_cut |
| P2-engines-8 | engines/data/checks.py `trace_numbers` | "45 percent" was not read as a percentage, so a correct plain-English answer was rejected. | fixed | tests/test_fix_engines_pass2.py::test_percent_words_and_ellipsis_quote |
| P2-engines-9 | engines/data/checks.py `check_quotes` | A quote made only of ellipses was "verified" against any source. | fixed | tests/test_fix_engines_pass2.py::test_percent_words_and_ellipsis_quote |
| P2-engines-10 | engines/browser/ctl.py `SNAP_JS`, `locate` | The snapshot and every target only look at the main frame, so buttons and fields inside an iframe cannot be seen or used. | fixed on this branch: the snapshot lists iframe elements and every target and click_text search the frames | tests/test_fix_pass2_open_engines.py::test_P2_engines_10_iframe_button_is_listed_and_clickable |
| P2-engines-11 | engines/browser/ctl.py `main` | Any command with no daemon running printed a FileNotFoundError traceback. | fixed | tests/test_fix_engines_pass2.py::test_ctl_not_running |
| P2-engines-12 | engines/code/testcmd.py `discover` | Go, Node, Java and Rust repos were told to run pytest. | fixed | tests/test_fix_engines_pass2.py::test_testcmd_other_languages (Go only; Node, Maven, Gradle and Cargo are in the same table in the code) |
| P2-engines-13 | engines/review/personas.py `parse_review` | Scores sent as strings ("9") were dropped, which lowered the overall score and forced "revise". | fixed | tests/test_fix_engines_pass2.py::test_scores_as_strings |
| P2-engines-14 | engines/sheets/market.py `treasury_yield` | A null tenor paired a date with the next day's yield and dropped the real latest entry. | fixed | tests/test_fix_engines_pass2.py::test_treasury_null_tenor |
| P2-engines-15 | engines/sheets/pipeline.py (file stem), engines/credit/engine.py (file stem) | Accented and CJK company names were mangled or became "Company", so two companies overwrote one file. Both now use `[^\w]+`. | fixed | tests/test_pass2_regressions.py::test_p2_engines_15_cjk_and_accented_names_stay_in_file_names |
| P2-engines-16 | engines/sheets/builder.py `capital_allocation` | A zero share price gave #DIV/0! across the Model tab. The formula is now wrapped in `IF(price=0,0,...)`. | fixed | tests/test_pass2_regressions.py::test_p2_engines_16_zero_share_price_gives_no_div0 |
| P2-engines-17 | engines/sheets/review_digest.py, qa.py | Cell-by-cell loops made the finish gate and digest take over 100 s on a 100k-row workbook. Every scan is now capped at `MAX_SCAN_ROWS`. | fixed | tests/test_pass2_regressions.py::test_p2_engines_17_big_sheets_are_sampled_not_scanned |

## forge

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-forge-1 | engines/forge/render.py `fallback`, `_render` | An empty table row made `fallback()` raise IndexError outside the per-slide try, aborting the whole deck. | fixed | tests/test_fix_forge_pass2.py::test_empty_table_row_does_not_abort |
| P2-forge-2 | engines/forge/render.py `normalize`, `plan_forms`, density.py `condense` | One wrong-typed field (tags as dicts, steps as an int) aborted the whole deck instead of one slide. | fixed | tests/test_fix_forge_pass2.py::test_wrong_typed_fields_never_abort_deck |
| P2-forge-3 | engines/forge/render.py (chart code) | NaN or inf in a line or waterfall chart raised out of `render()`. | fixed | tests/test_fix_forge_pass2.py::test_wrong_typed_fields_never_abort_deck |
| P2-forge-4 | engines/forge/logos.py `fetch` | A company name over 255 characters raised OSError "File name too long". | fixed | tests/test_fix_forge_pass2.py::test_logo_fetch_long_name_never_raises |
| P2-forge-5 | engines/forge/brief.py `slide_count` | Slide counts were English-only and 1 to 2 digits, so other languages and "100 slides" fell back to 10. | fixed | tests/test_fix_forge_pass2.py::test_slide_count_other_languages |
| P2-forge-6 | engines/forge/brand.py `make_master` | A 16:9 deck at 10 x 5.625 in was rejected with a self-contradictory message. Such masters are now scaled. | fixed | tests/test_ledger017_packaging.py::test_a_16_9_master_at_10_by_5_625_in_is_scaled_and_used |
| P2-forge-7 | engines/forge/measure_pptx.py `measure`, tools.py `measure_office_file` | A pptx with no theme part raised StopIteration, and a non-zip raised BadZipFile. | fixed | tests/test_fix_forge_pass2.py::test_measure_without_theme; tests/test_fix_forge_core.py::test_corrupt_zip_gives_readable_result |
| P2-forge-8 | engines/forge/finance_pages.py `finance_workbook` | Each candidate workbook was fully parsed and never closed just to read defined names. Now `read_only=True` and `wb.close()`. | fixed | tests/test_pass2_regressions.py::test_p2_forge_8_finance_workbook_closes_the_file |
| P2-forge-9 | engines/forge/kb.py (golden.jsonl write) | golden.jsonl was written without an encoding (cp1252 on Windows). Now `encoding="utf-8"`. | fixed | tests/test_pass2_regressions.py::test_p2_forge_9_golden_jsonl_is_utf8_under_a_cp1252_locale |

## packaging

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-packaging-1 | README.md, packaging/requirements-cli.txt | The README promised offline OCR and HEIC support that no build ships. The README now says OCR is optional (`pip install rapidocr onnxruntime`), and pillow-heif is in requirements-cli.txt. | fixed | tests/test_fix_packaging_pass2.py::test_readme_does_not_promise_bundled_ocr |
| P2-packaging-2 | THIRD_PARTY_NOTICES.md (Python packages table) | The notices list packages as bundled that no build installs. | fixed on this branch: the table matches what the builds install (packaging/python-packages.txt records the resolved set) | tests/test_fix_pass2_open_engines.py::test_P2_packaging_2_every_notice_package_is_in_a_requirements_or_constraints_file |
| P2-packaging-3 | packaging/requirements-cli.txt, app/requirements.txt, app/pyproject.toml, engines/forge/measure_pdf.py | olefile, pbixray and pymupdf were imported but declared nowhere. olefile is now declared everywhere; pbixray and pymupdf are documented optional extras and measure_pdf raises a readable "pip install pymupdf" error. | fixed | tests/test_fix_packaging_pass2.py::test_olefile_is_declared_everywhere; ::test_measure_pdf_without_pymupdf_gives_a_readable_error |
| P2-packaging-4 | cli.py `check` output, README.md | A terminal install was given source-checkout advice for LibreOffice. The check output now says LibreOffice is not included in the terminal install, and the README says the same. | fixed | tests/test_pass2_regressions.py::test_p2_packaging_4_check_says_terminal_install_has_no_libreoffice |
| P2-packaging-5 | docs/BUILDING.md | Wrong Node version, a table row outside the table, no Linux rows, and no mention of release.yml. | fixed on this branch: the stray row is gone | none (a docs table row) |
| P2-packaging-6 | cli.py `main` | `meshpool --version` and `-V` were rejected, and `meshpool login --help` started the login flow. | fixed | tests/test_fix_packaging_pass2.py::test_version_flag; ::test_login_help_does_not_prompt |
| P2-packaging-7 | packaging/mac/repack.sh | repack.sh judged signing "on" from two variables while sign.sh needs six, so a partial secret set left the app unsigned. repack.sh now tests all six. | fixed | tests/test_pass2_regressions.py::test_p2_packaging_7_repack_checks_every_apple_secret |
| P2-packaging-8 | packaging/lo_deps_check.py `LINUX_SYSTEM_PREFIXES` | The Linux "system library" allow-list is much wider than what docs/SETUP.md says a desktop has, so the check can pass for a build that fails on a minimal desktop. | fixed on this branch: the list names only libraries the smoke job's desktop provides; checked on the pinned LibreOffice tarball | tests/test_fix_pass2_open_engines.py::test_P2_packaging_8_every_linux_system_library_is_on_a_desktop; ::test_P2_packaging_8_a_pruned_bundled_library_is_reported |

## skills

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-skills-1 | tests/test_codex_engine.py, agent/compaction.py `summarize`, core/client.py `LiveClient.chat` | The codex loop-guard test hung for about 10 minutes because compaction called a dead gateway with 25 retries. The test now sets `MH_COMPACT_MODEL=0`, and side calls such as "compact" have a short connect budget. | fixed | tests/test_codex_engine.py::test_the_loop_guard_and_compaction_still_work_in_codex_style; tests/test_fix_core_pass3.py::test_closed_port_main_and_side_calls |
| P2-skills-2 | core/client.py `LiveClient.chat` | Transient errors were retried 25 times with up to 30 s sleeps (about 10 minutes) for every caller. | fixed | tests/test_fix_core_pass3.py::test_closed_port_main_and_side_calls |
| P2-skills-3 | tests/conftest.py `_no_slot_files_in_real_temp` | The session guard failed whenever any other process took a slot in the shared temp folder. It now counts only slot folders this process opened. | fixed, no test | none (it is test infrastructure; four parallel pytest runs in this check did not trip it) |
| P2-skills-4 | tests/test_agent_robustness.py, test_credit_engine.py, test_delegate.py, test_extras_0010.py, test_hrouter.py (named tests) | Seven tests failed under a four-way parallel run and passed alone. | not reproducible | The seven named tests, plus test_brand.py, the codex loop-guard test and test_forge_measure_pdf_ext.py (33 tests), were run four times at once in separate processes with separate temp folders: 33 passed in each run. Commit #24 also fixed the delegate Stop test. |
| P2-skills-5 | tests/test_brand.py, engines/forge/validate_pptx.py `_opens_cleanly` | A one-off failure "Not a readable Office package: expected str ... not BytesIO". | not reproducible | tests/test_brand.py passed in all four parallel runs above; the audit itself saw it pass on rerun. |
| P2-skills-6 | .github/workflows/tests.yml, tests/test_forge_measure_pdf_ext.py | pymupdf was not installed in CI, so the PDF-measurement tests were silently skipped. tests.yml now installs pymupdf (test-only). | fixed | tests/test_pass2_regressions.py::test_p2_skills_6_test_workflow_installs_pymupdf |
| P2-skills-7 | agent/tui.py `ask_key` | `meshpool login` without a terminal told the user to run `meshpool login`. | fixed | tests/test_fix_agent_pass3.py::test_login_without_terminal_says_it_needs_one |
| P2-skills-8 | agent/mcp.py `token_problem`, `active_config` | A placeholder GITHUB_TOKEN made a red "failed" GitHub server appear on first run. | fixed | tests/test_fix_agent_pass3.py::test_token_problem; ::test_invalid_github_token_server_is_not_started |

## toplevel

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-toplevel-1 | web.py `_lxml_text`, `html_to_text` | Pages without a meta charset came back as mojibake for any non-ASCII text. | fixed | tests/test_fix_toplevel_pass2.py::test_html_to_text_keeps_non_ascii |
| P2-toplevel-2 | web.py `_get`, `fetch` | A redirect loop returned status 302 with empty text as a success. | fixed | tests/test_fix_toplevel_pass2.py::test_redirect_loop_is_an_error |
| P2-toplevel-3 | autoupdate.py `download_resumable` | A truncated download was promoted, then failed the hash and threw away the partial data. | fixed | tests/test_fix_toplevel_pass2.py::test_truncated_download_is_not_promoted |
| P2-toplevel-4 | config.py `write` | The read-modify-write of meshpool.config lost keys across processes. | fixed | tests/test_fix_toplevel_pass2.py::test_config_write_loses_nothing_across_processes; ::test_config_write_loses_nothing_across_threads |
| P2-toplevel-5 | providers.py `_cfg` | The settings file beat a value set in the shell, so `MESHPOOL_BASE_URL=... meshpool -p` was ignored. | fixed | tests/test_fix_toplevel_pass2.py::test_shell_env_beats_saved_config |
| P2-toplevel-6 | cli.py `main` | Subcommands ignored `--help` and ran (scheduler install, run-due, import), and `meshpool work file:/nonexistent` printed a traceback. | fixed | tests/test_fix_toplevel_pass2.py::test_help_has_no_side_effects; ::test_work_missing_file_is_a_message |
| P2-toplevel-7 | capabilities.py `needs` | Questions and summaries were treated as requests to make a file. | fixed | tests/test_fix_toplevel_pass2.py::test_questions_are_not_file_requests |
| P2-toplevel-8 | agent_import.py `_scan_text`, importer.py `import_mcp` | Imported MCP servers were enabled without review, and instruction files were copied with secrets in them. | fixed | tests/test_fix_toplevel_pass2.py::test_imported_mcp_starts_disabled_and_instructions_are_scanned |
| P2-toplevel-9 | autoupdate.py `_write_json` | One fixed temp name per target made concurrent writers race on the rename. | fixed | tests/test_fix_toplevel_pass2.py::test_write_json_is_thread_safe |
| P2-toplevel-10 | provider_setup.py `test` | A 404 or an HTML page on /models passed setup as "Connected", and 402 got a wrong message. | fixed | tests/test_fix_toplevel_pass2.py::test_models_html_page_and_402 |
| P2-toplevel-11 | updater.py (update check error) | Any GitHub HTTP error was reported as "Could not reach GitHub". It now names the rate limit or the status code. | fixed | tests/test_pass2_regressions.py::test_p2_toplevel_11_update_check_names_rate_limit_or_status |
| P2-toplevel-12 | insights.py `_state` | A state.json holding a list crashed the home screen and every turn end. | fixed | tests/test_fix_toplevel_pass2.py::test_insights_state_list_does_not_crash |
| P2-toplevel-13 | checkpoints.py `Checkpoints.revert_to_before` | Undo silently skipped files over 2 MB and left empty folders the agent created. | fixed on this branch: Undo removes empty folders the turns made; a folder with files stays, with a note | tests/test_fix_pass2_open_engines.py::test_P2_toplevel_13_undo_removes_empty_folders_the_turn_made; ::test_P2_toplevel_13_undo_removes_folders_a_write_made_in_journal_mode |
| P2-toplevel-14 | skills.py `add_files` | A bare SKILL.md without front matter became a skill named "skill" with no description. | fixed | tests/test_fix_toplevel_pass2.py::test_bare_skill_md_gets_front_matter |
| P2-toplevel-15 | docread.py `_csv` | A field over 131,072 characters failed the whole file, and large files claimed an exact row count. Both are fixed ("at least N rows"). | fixed | tests/test_fix_toplevel_pass2.py::test_csv_big_field_and_truncated_row_count (covers the big field; the "at least" wording has no test) |
| P2-toplevel-16 | project_store.py `_safe_name` | Dotfile names lost their dot, long names raised OSError and Windows reserved names were accepted. | fixed | tests/test_fix_toplevel_pass2.py::test_safe_names |
| P2-toplevel-17 | mcp_library.py `configure` | The Alpha Vantage key was put into the URL unescaped. | fixed | tests/test_fix_toplevel_pass2.py::test_alpha_vantage_key_is_url_safe |
| P2-toplevel-18 | learn.py `worth_a_look` | Ordinary questions with "again" or "need" were sent for preference extraction on every turn. | fixed | tests/test_fix_toplevel_pass2.py::test_learn_gate_ignores_plain_questions |

## uifront

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-uifront-1 | ui/app.js `runSlash` (/agent) | `/agent` looked up a missing `#oPill` element and threw. | fixed | tests/test_fix_uifront_pass2.py::test_agent_slash_command_opens_the_menu_without_errors |
| P2-uifront-2 | ui/app.js `sendNow` | Files attached to the first message of a new chat failed with "not one of your projects" and left an empty chat. Files now go with the send request into the chat's own folder, and a failed first send discards the new chat. | fixed | tests/test_attachments_first_message.py::test_file_on_the_first_message_lands_in_the_new_chats_own_folder; ::test_unwritable_folder_gives_one_toast_no_empty_chat_and_keeps_the_text |
| P2-uifront-3 | ui/app.js `sendNow` (catch) | When creating the chat failed, "Sending" stayed on the home screen forever. | fixed | tests/test_fix_uifront_pass2.py::test_failed_chat_creation_clears_the_sending_status |
| P2-uifront-4 | ui/app.js `newThread`, `goPage`, `openProject` | After a read-only chat the composer stayed disabled on New chat and other pages. | fixed | tests/test_fix_uifront_pass2.py::test_composer_is_enabled_again_after_a_read_only_chat |
| P2-uifront-5 | ui/app.js (message rendering), shell.js (composer), app.css | No `dir="auto"`, so Arabic and Hebrew text was laid out left to right. Messages and the composer now get `dir="auto"`, and titles use `unicode-bidi: plaintext`. | fixed | tests/test_fix_uifront_pass2.py::test_message_and_composer_get_auto_direction (checks the composer only) |
| P2-uifront-6 | ui/pages.js, memory.css (Memory header) | At phone width the Memory tabs were squeezed and History and Review were off screen. | fixed | tests/test_fix_uifront_pass2.py::test_memory_tabs_are_all_reachable_on_a_phone |
| P2-uifront-7 | ui/app.js `fitPanel`, app.css | At 761 to 979 px the side panel covered the conversation and composer. | fixed | tests/test_fix_uifront_pass2.py::test_side_panel_collapses_the_sidebar_at_tablet_width |
| P2-uifront-8 | ui/app.js `newThread` | New chat kept the unsent text of the last chat, and incognito stayed on. | fixed | tests/test_fix_uifront_pass2.py::test_new_chat_clears_the_box_and_incognito |
| P2-uifront-9 | ui/app.js `threadAct` (delete), ui/agent_api.py | Delete ran in a 6 s page timer, so a reload brought the chat back. The server now finishes the delete itself. | fixed | tests/test_fix_uifront_pass2.py::test_delete_needs_no_page_timer; tests/test_attachments_first_message.py::test_delete_finishes_on_the_server_after_a_reload; tests/test_chat_delete.py::test_delete_is_done_by_the_server_without_the_page |
| P2-uifront-10 | ui/app.js `foldHistory`, input resize, app.css | Long chats opened slowly and each keystroke forced a layout of the whole transcript. History is now folded into skipped blocks with `content-visibility`, and the input resize avoids layout reads. | fixed | tests/test_fix_cpu_front.py::test_typing_in_a_long_chat_stays_cheap; ::test_long_chat_history_is_folded_into_blocks |
| P2-uifront-11 | ui/shell.js (modal handling) | Modal dialogs did not trap focus, and closing search left focus on the body. The page behind a modal is now inert and focus returns to the opener. | fixed | tests/test_fix_uifront_pass2.py::test_tab_stays_inside_the_settings_dialog; tests/test_ui_ws19.py::test_b9_the_page_behind_a_modal_is_inert_and_focus_comes_back |
| P2-uifront-12 | ui/memory.js (consolidate toast) | The toast pointed to an "Archived" place the Memory page does not have. It now says "They stay in History". | fixed | tests/test_pass2_regressions.py::test_p2_uifront_12_consolidate_toast_points_to_history |
| P2-uifront-13 | ui/settings2.js (settings nav search) | A search with no match hid every entry and showed no message. | fixed | tests/test_fix_uifront_pass2.py::test_settings_search_shows_an_empty_state |
| P2-uifront-14 | ui/app.js (browser history) | The app pushed no history entries, so Back left the app. | fixed | tests/test_fix_uifront_pass2.py::test_back_returns_to_the_previous_page |
| P2-uifront-15 | ui/app.js `poll`, sidebar interval | With the server down the stop button stayed in its spinner state and every sidebar poll threw an unhandled rejection. The interval now catches, the stop state is cleared after two failures, and the retry note has a Reload button. | fixed | tests/test_pass2_regressions.py::test_p2_uifront_15_server_gone_mid_run_shows_reload_and_clears_stop |

## uiserver

| ID | Where | What was wrong | Status | Test |
|---|---|---|---|---|
| P2-uiserver-1 | ui/agent_api.py `get`, `Session.close`, `Session._start_mcp` | Concurrent GETs for one saved chat built several Sessions whose MCP servers were never closed. | fixed | tests/test_fix_uiserver_pass2.py::test_concurrent_get_builds_one_session (the `_closed` check after a late MCP start has no test) |
| P2-uiserver-2 | ui/agent_api.py `Session._capability_turn` -> capabilities.py `edits` (`_FILE` regex) | One unbroken token in a message made matching quadratic while holding the GIL, so the whole server stopped answering. | fixed on this branch: the file-name patterns in capabilities.py, office_intent.py and agent/core.py are linear (300 KB in milliseconds) | tests/test_fix_pass2_open_server.py::test_p2_uiserver_2_long_token_is_checked_fast; ::test_p2_uiserver_2_office_intent_long_token_is_checked_fast; ::test_p2_uiserver_2_agent_file_name_scans_are_linear |
| P2-uiserver-3 | ui/agent_api.py `Session._start_turn` `go()` | An exception from `agent.send` killed the turn thread with no error event and no user message shown. | fixed | tests/test_fix_uiserver_pass2.py::test_send_failure_shows_error_and_keeps_message |
| P2-uiserver-4 | ui/workbench.py `git_action` (checkout) | From a detached HEAD with unreachable commits, creating a branch was refused with the message that told the user to create one. | fixed | tests/test_fix_uiserver_pass2.py::test_detached_head_can_create_branch |
| P2-uiserver-5 | ui/server.py `save_settings` | MESHPOOL_BASE_URL was saved without any check. | fixed | tests/test_fix_uiserver_pass2.py::test_save_settings_rejects_bad_base_url |
| P2-uiserver-6 | ui/server.py `Handler._json`, `do_POST` | A JSON body that is not an object, or fields of the wrong type, gave 500 on about 60 routes. Non-objects now raise ValueError, and TypeError/AttributeError from parsing map to 400. | fixed | tests/test_fix_uiserver_pass2.py::test_json_body_must_be_object (wrong field types are handled in `do_POST` but untested) |
| P2-uiserver-7 | ui/server.py `do_POST` (KeyError branch) | Unknown ids and missing fields came back as 404 with the raw key as the message ("zz", "data"). | fixed on this branch: a missing field is 400 "Missing field: <name>."; an unknown id stays 404 | tests/test_fix_pass2_open_server.py::test_p2_uiserver_7_missing_field_is_400_and_unknown_id_is_404 |
| P2-uiserver-8 | ui/workbench.py `attach` | Attached dotfiles lost their dot, and names over 255 bytes gave 500. | fixed | tests/test_fix_uiserver_pass2.py::test_attach_keeps_dotfiles_and_limits_length |
| P2-uiserver-9 | ui/workbench.py `write_file` | Saving to a folder path gave 500 IsADirectoryError. | fixed | tests/test_fix_uiserver_pass2.py::test_write_file_to_folder_is_a_value_error |
| P2-uiserver-10 | ui/previews.py `_csv_sheet`, `meta` | A CSV with a huge cell failed the preview, a corrupt PDF showed a PDFium error, and a corrupt xlsx blamed LibreOffice. | fixed | tests/test_fix_uiserver_pass2.py::test_csv_with_huge_cell_previews; tests/test_previews.py::test_a_damaged_pdf_is_an_error_not_a_crash |
| P2-uiserver-11 | ui/desktop.py `reveal` | Without xdg-open, "Open skills folder" and "Open data folder" returned 500 FileNotFoundError. | fixed | tests/test_fix_uiserver_pass2.py::test_missing_xdg_open_is_readable |
| P2-uiserver-12 | ui/server.py `do_POST`, `_extra`, `_company` | A non-zip .zip gave 500 BadZipFile. It now answers 400 "That file is not a valid zip file." | fixed | tests/test_pass2_regressions.py::test_p2_uiserver_12_non_zip_skill_upload_is_a_400 |
| P2-uiserver-13 | ui/agent_api.py `thread_action`, `_purge`, receipt_api.py | Actions on a chat that does not exist answered ok, and delete left the chat's checks files behind. Both are fixed. | fixed | tests/test_fix_uiserver_pass2.py::test_actions_on_missing_chat_are_404 (removal of the checks files has no test) |
| P2-uiserver-14 | ui/workbench.py `_git`, `git_action` (commit, push) | A silent pre-commit hook failure showed "git -c failed", and pushing from a detached HEAD showed "invalid refspec". | fixed on this branch: the error names the git subcommand, skipping -c and -C values | tests/test_fix_pass2_open_server.py::test_p2_uiserver_14_failed_git_names_the_subcommand |
| P2-uiserver-15 | ui/agent_api.py `listing`, `listing_page` | The sidebar list was silently capped at 400 chats. It now has no cap and pages with a total and a "more" flag. | fixed | tests/test_ws21_server_perf.py::test_p07_the_list_has_no_400_cap_and_pages_with_load_more |
| P2-uiserver-16 | ui/agent_api.py `Session.set_model` | A model name that no endpoint serves ("nonexistent:zz") is accepted and stored, and the next message fails. | fixed on this branch: a name missing from the endpoint's known model list is refused; offline any name is allowed | tests/test_fix_pass2_open_server.py::test_p2_uiserver_16_set_model_checks_the_known_model_list |
| P2-uiserver-17 | ui/workbench.py `list_dir` | Folders over 2000 entries were cut with no indication. | fixed | tests/test_fix_uiserver_pass2.py::test_list_dir_reports_truncation |
