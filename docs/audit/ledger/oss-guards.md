# oss-guards ledger

Branch `audit/oss-guards`. Regression tests for bug classes reported in other open-source coding-agent harnesses.
All 15 are absent from Meshpool; no code fix was needed. Tests: `app/tests/test_oss_guards.py` (26 tests; T below).

| ID | status | guard | fix | files | test |
|---|---|---|---|---|---|
| OSS-01 | not reproducible | config.py `write`: thread lock + cross-process file lock, re-read then merge key, tmp + `replace_file` | none | app/meshpool_harness/config.py | T::test_oss01_concurrent_settings_writes_lose_nothing, T::test_oss01_concurrent_writes_across_processes |
| OSS-03 | not reproducible | prompts go as JSON-RPC over the stdin pipe; spawn argv holds no prompt | none | harnesses/acp.py, harnesses/pi_rpc.py | T::test_oss03_prompt_goes_over_stdio_not_argv |
| OSS-04 | not reproducible | bash/probe spawns use `stdin=DEVNULL`; engine spawns use their own pipes | none | core/tools.py, harnesses/__init__.py | T::test_oss04_bash_tool_never_inherits_stdin, T::test_oss04_every_spawn_sets_stdin_explicitly |
| OSS-06 | not reproducible | compaction keeps system message, original task, recent tail, rule-built note (plan, files, commands) | none | agent/compaction.py | T::test_oss06_compaction_keeps_system_task_todo_and_files |
| OSS-07 | not reproducible | rule notes come from the log with no model call; a dead or empty summary call falls back to them; short history untouched | none | agent/compaction.py | T::test_oss07_failed_or_interrupted_summary_call_still_keeps_facts_and_history |
| OSS-08 | not reproducible (N/A: sessions are JSON files) | tmp + `os.replace` save; resume skips corrupt files | none | agent/core.py | T::test_oss08_crash_mid_save_or_migration_leaves_sessions_loadable |
| OSS-10 | not reproducible | core loop nudges twice then stops with a reason; app agent emits an "empty reply" error | none | core/loop.py, agent/core.py | T::test_oss10_toolUse_stop_with_zero_tool_calls_is_not_silent, T::test_oss10_app_agent_reports_an_empty_toolUse_reply_as_an_error |
| OSS-13 | not reproducible | fuzzy levels (trailing whitespace, tabs, indent), CRLF preserved, closest-region hint | none | core/tools.py | T::test_oss13_whitespace_and_crlf_edits_apply, T::test_oss13_failed_match_points_at_the_closest_text |
| OSS-16 | not reproducible | `_path` resolves symlinks before the root check; grep `inside_only` skips escaping links | none | core/tools.py | T::test_oss16_write_and_edit_through_symlink_are_refused, T::test_oss16_search_skips_symlinks_that_leave_the_project |
| OSS-19 | not reproducible | stale-edit hash check on `edit` (CRLF/BOM-insensitive) | none | core/tools.py | T::test_oss19_edit_after_the_user_changed_the_file_is_refused, T::test_oss19_line_ending_only_difference_is_not_a_conflict |
| OSS-20 | not reproducible | secrets in a 0600 settings file and atomic JSON, no keyring import | none | config.py, fsio.py, agent/mcp.py | T::test_oss20_no_os_keyring_and_large_secrets_round_trip_in_a_private_file |
| OSS-25 | not reproducible | `Agent._repair` fills every missing tool result, idempotent | none | agent/core.py | T::test_oss25_every_parallel_tool_call_gets_a_result_after_a_stop |
| OSS-27 | not reproducible | `find_bash`: bundled bash, then Git Bash, rejects the System32 stub; packaged app never uses PATH (Windows simulated) | none | config.py | T::test_oss27_windows_bash_prefers_bundled_or_git_and_rejects_the_wsl_stub |
| OSS-31 | not reproducible | `validate` JSON and `__truncated__` errors; `_sanitize_calls` keeps requests valid; loop continues | none | core/tools.py, core/loop.py | T::test_oss31_invalid_and_truncated_tool_args_are_reported_not_fatal, T::test_oss31_loop_keeps_going_after_merged_or_truncated_calls |
| OSS-32 | not reproducible | `prepare` stages `.update-*` beside the app; running files untouched until `apply_staged` (win-app simulated) | none | updater.py | T::test_oss32_update_is_staged_beside_the_app_and_the_running_files_are_untouched |
