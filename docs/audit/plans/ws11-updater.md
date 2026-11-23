# ws11-updater: plan

Scope: `app/meshpool_harness/autoupdate.py`, `updater.py`, the update endpoints in `ui/server.py`, the restart path in
`cli.py` / `ui/desktop.py`, the run gate in `ui/agent_api.py`, the update panel in `ui/app.js` / `ui/shell.js`, and a
new start-up recovery hook (`app/sitecustomize.py` + `app/meshpool_recover.py`). Line numbers are 0.0.12.

## Does it still reproduce in 0.0.12?

All 21 items reproduce by reading the 0.0.12 code (evidence in the root-cause column). None was fixed between 0.0.9
and 0.0.12; the update code is unchanged in the relevant parts.

| ID | Root cause (0.0.12) |
|----|---------------------|
| M10-01 | `on_quit()` (autoupdate.py:648) launches the helper whenever `_ready()`; nothing records that the last helper failed. `ready.json` survives (`_load_ready`, :660) and the helper reopens the app on failure (Mac `open "$OLD"`, Windows `Start-Process $exe`), so every quit re-runs it. |
| M10-02 | `tick()` (:528) keeps `ready` when the check says nothing is newer; `_load_ready()` restores it from disk; `on_quit()` never asks whether the release is still published. |
| M10-03 | `tick()` (:525) does `_set(state="error")` on any failed check, whatever the pipeline state; `pipeline()` error paths do the same. |
| M10-04 | `launch_helper()` (:596/:611) returns "will be downloaded again" but leaves state `ready`; `tick()` (:532) returns early on `ready` + same tag. |
| M10-05 | `_code_update()` (:512) raises "will be downloaded when it is checked" on `need_installer`, but the next tick takes the same code-only branch (:471) again. |
| M10-06 | A checksum mismatch sets `error`; the loop retries every `RETRY` (15 min, :689) with no backoff and re-downloads the whole installer each time. |
| M10-07 | The Mac stage beside the app (`bundle.parent/.Meshpool-update-<v>`, :264) is only removed by the helper on success; the leftover sweep (:477) only looks in the data folder. |
| M10-08 | `tick()` returns `status()` (:522-538), and `status()` calls `_last_result()` (:100), which marks `result.json` seen. |
| M10-09 | `_checked()` (:171) accepts the file against the re-read sums but `_pipeline()` records `sums[a["name"]]` (:490/:493), the old hash, which the Windows helper then compares. |
| M10-10 | `save_settings()` (:80) only writes the file; nothing starts a download until the next 6-hourly tick. |
| M11-01 | `updater.install()` swaps the code folders inside the running process and returns "Restart to use it"; `check()` compares with the in-memory `MH.__version__` (:89), so the same release is offered and installed again; any module imported later loads the new files. |
| M11-02 | `/api/update/install` and `/api/update/apply` (server.py:620/:629) call `_running()` once before seconds of download and verify; nothing stops a turn or a scheduled run from starting afterwards. |
| M11-03 | `restart()` spawns the new copy with `MESHPOOL_RESTART_WAIT=1`; `cli.main` (cli.py:189) sleeps a fixed 3 s; `desktop.run` then finds the old instance on `/healthz` and exits. `close_all` has no time limit. |
| M11-04 | `_pick()` offers a prerelease to a prerelease copy, but `_pipeline()` (:457) refuses every prerelease, also when the user clicked Download. |
| M11-05 | `install()` runs `pip install` (:364) before `_swap_all()`; a failed swap returns "Nothing was changed" (:383). |
| M11-06 | `_swap_all()` renames `dst -> .dst.old` then `staged -> dst`; a kill between them leaves no package, and nothing that runs at start-up (the package itself is gone) can repair it. |
| M11-07 | `install()` has no lock and always stages into the shared `APP/.<name>.new` (:373); two requests interleave `rmtree`/`copytree`/`rename`. |
| M12-01 | `renderUpdBar`/`updatePanel` read `S.upd` (the page's own check, made once at load); the background check only changes `S.ust.state` to `available`, which neither function renders. |
| M12-02 | `install()` never sets the polled status; `updStatus` re-renders `updatePanel(S.upd)` every 60 s and draws an enabled Update now. |
| M12-03 | `restartApp()` reloads on the first `/healthz` 200 after 2.5 s; `/healthz` returns no process identity (server.py:327). |
| F-30 | `renderUpdBar` shows "... is available / Update" for any `u.newer`, including `kind == "dev"`, whose panel has no button. |

## Design (root-cause fixes)

### 1. One persisted state machine (autoupdate.py) — M10-01..M10-10, M11-04, M12-01/02 back end

State lives in `<home>/updates/state.json`, written only through `fsio.update_json` (file lock + atomic write); `_ST`
stays as the in-memory mirror (the TUI reads it). `ready.json` is migrated once and removed. States:

`idle, checking, available, downloading, verifying, staging, ready, installing, failed, withdrawn, uptodate`

Every change goes through `_go(to, **fields)`, which checks a transition table and persists. Rules:

- A failed **check** never changes the state: it sets `check_error` / `checked_at` only (M10-03). Pipeline failures go
  to `failed` with `fail = {tag, count, next_try, sums_fp, error}`; `next_try = now + min(RETRY * 2**(count-1), 24 h)`;
  the pipeline for that tag does not run before `next_try` unless the user asks or the release's SHA256SUMS changed
  (M10-06). The background loop sleeps until the earlier of the next check and `next_try`.
- `_checked()` returns the hash it accepted, and that is what is recorded (M10-09).
- Every successful check records the offerable tags (`v["tags"]`, from updater). A `ready`/`failed`/`available` tag that
  is no longer offered (deleted, drafted, turned prerelease, lost its SHA256SUMS) goes to `withdrawn`: files and stage
  are deleted (M10-02). A newer tag supersedes `ready`: the old files and stage are deleted first (M10-07).
- `ready` is re-validated (`_intact`: files and stage exist, tag newer than this copy) at load, at every tick, and in
  `begin_apply`/`on_quit`; when it is not intact the entry is dropped and the pipeline runs again (M10-04).
- Installing: `begin_apply`/`on_quit` move `ready -> installing` (persisted with `attempt`) before the helper starts.
  At the next start `_load()` resolves `installing`: this copy's version >= tag means success (clean up, `idle`);
  otherwise back to `ready` with `install_failures += 1` and the helper's message as `error`. `on_quit()` never
  installs automatically when `install_failures > 0`, when the ready release is a manual prerelease, or when the
  release has not been confirmed as still published within 24 h and a 5 s re-check cannot confirm it (M10-01, M10-02).
  The Restart button still works (an explicit retry); it re-checks the release first and refuses a withdrawn one.
- `status(consume=False)` has no side effects; only the page's status endpoint passes `consume=True` (M10-08).
  It also returns `check` (the latest check: latest, tag, newer, kind, url, current, checked_at) and `check_error`
  (M12-01), `install_failures`, `fail`, `manual`.
- `save_settings()` starts a background tick when `auto_download` turns on (M10-10).
- `pipeline(v, download, manual)`: a prerelease is refused only for automatic downloads (M11-04); a manually
  downloaded prerelease is ready with `manual=True` (installs on Restart only).
- Windows code-only release: `_code_update` calls `updater.prepare(v, auto=True)`; `need_installer` falls through to
  the installer download in the same run (M10-05); a staged code update is `ready` with `mode="code"` and is applied
  at quit or Restart (never in the background while the app runs, see 2).
- Stage sweep at load and after every drop: `bundle.parent/.Meshpool-update-*` and `<home>/updates/<tag>/` that the
  current `ready` does not use are removed. The Mac helper also removes `$STAGE` on its codesign-failure and
  rolled-back paths (M10-07).
- `mark_installing(tag, latest, mode)` / `install_finished(ok, error)` let the in-place path report `installing` to the
  page (M12-02).

### 2. In-place code update (updater.py) — M11-01, M11-05, M11-06, M11-07

- `install()` is split into `prepare(v, auto)` (download, verify, extract, version check, decide packages; stage into a
  **unique** `APP/.update-<tag>-<random>/`, finished by a `.complete` marker) and `apply_staged(st)` (swap). Nothing
  is swapped while the app keeps running: the server applies and restarts in one step; the background Windows path
  applies at quit (M11-01). `check()` also compares with the version **on disk** (`installed_version()`), returns
  `pending_restart`, and `install()` answers "restart" instead of reinstalling (M11-01).
- `install_lock()`: `fsio.file_lock(<home>/updates/install, timeout=0)`; a second installer (thread or process) gets
  `UpdateBusy` -> "An update is already being installed." (M11-07).
- `apply_staged`: write `APP/.update-journal.json` (atomic, fsynced) -> swap every folder (backups kept) -> for the
  command-line copy, `pip install` the new packages **after** the swap; if pip fails, put the old folders back and
  re-install the old package list, and say exactly what state the copy is in (M11-05) -> copy the top-level files ->
  remove the journal -> delete backups.
- Start-up recovery (M11-06): `app/meshpool_recover.py` (stdlib only, outside the swapped folders) restores the old
  folders from the journal; `app/sitecustomize.py` calls it at every interpreter start with `PYTHONPATH=app` (the
  Windows launcher, `meshpool.cmd`, the CLI shim, `restart()`), then chains to any other `sitecustomize` on sys.path.
  Both files ship with the app (build_all, install.sh/ps1) and with every code update.
- `restart()`: passes its PID in `MESHPOOL_RESTART_WAIT`; the quit hooks get at most 60 s, then the process exits.
  `wait_for_exit(pid, timeout)` (POSIX `kill 0`, Windows `OpenProcess`/`WaitForSingleObject`).

### 3. Restart and the run gate (cli.py, desktop.py, server.py, agent_api.py) — M11-02, M11-03, M12-03 server side

- `agent_api`: `GATE` lock + `QUIESCING`; `quiesce()` atomically checks that nothing runs and closes the gate; a turn
  (`_start_turn`, `grade_now`) claims `working` under the same lock and is refused while the gate is closed; the
  scheduler does not claim due tasks while it is closed. `resume()` reopens it when the update does not go ahead.
- Server: `/api/update/install` = lock -> `installing` -> `prepare` -> `quiesce` -> `apply_staged` -> restart;
  `/api/update/apply` = lock -> `quiesce` -> `begin_apply` -> quit/restart; every failure reopens the gate and reports.
- `/healthz` returns `pid` and a per-process `boot` id. `cli.main` waits for the old PID to exit (up to 120 s) instead
  of sleeping 3 s; `desktop.run` started by a restart waits up to 90 s for an old instance to stop answering before
  it treats it as "already running".

### 4. Page (app.js, shell.js) — M12-01, M12-02, M12-03, F-30, M11-04 UI

- `updStatus` adopts `S.ust.check` when it is newer than `S.upd`; bar and panel render `available`, `installing`
  (no button, fast poll), `failed` (error, next try, Try again), `withdrawn`, and a ready update whose install failed.
- `restartApp`/`applyUpdate`: read `/healthz` boot before, reload only when `/healthz` answers with a different boot.
- `kind == "dev"`: no sidebar "Update"; the About panel says to run `git pull` and offers a Copy button.

## Tests (each must fail on the old code)

| Item | Test |
|------|------|
| M10-01 | `tests/test_update_state.py::test_failed_helper_is_not_rerun_on_quit` |
| M10-02 | `::test_withdrawn_ready_release_is_dropped_and_not_installed`, `::test_on_quit_rechecks_unconfirmed_release` |
| M10-03 | `::test_failed_check_keeps_ready_and_downloading` |
| M10-04 | `::test_missing_installer_is_downloaded_again` |
| M10-05 | `::test_code_update_needing_installer_downloads_installer` |
| M10-06 | `::test_checksum_failure_backs_off` |
| M10-07 | `::test_superseded_or_failed_mac_stage_is_removed` |
| M10-08 | `::test_tick_does_not_consume_result` |
| M10-09 | `::test_reread_sums_hash_is_recorded` |
| M10-10 | `::test_enabling_auto_download_starts_download` |
| M11-01 | `tests/test_update_inplace.py::test_update_now_does_not_swap_running_code`, `::test_disk_version_prevents_reinstall` |
| M11-02 | `tests/test_update_gate.py::test_turn_started_during_update_is_refused`, `::test_scheduler_waits_while_quiescing` |
| M11-03 | `tests/test_update_gate.py::test_restarted_copy_waits_for_old_pid` |
| M11-04 | `tests/test_update_state.py::test_manual_prerelease_download_runs` |
| M11-05 | `tests/test_update_inplace.py::test_packages_installed_after_swap_and_rolled_back` |
| M11-06 | `tests/test_update_inplace.py::test_kill_between_renames_is_recovered_at_start` (real subprocess killed mid-swap) |
| M11-07 | `tests/test_update_inplace.py::test_two_concurrent_installs` (two threads and two processes) |
| M12-01 | `tests/test_ui_update_panel.py::test_background_release_shows_without_reload` |
| M12-02 | `tests/test_ui_update_panel.py::test_installing_hides_update_now` |
| M12-03 | `tests/test_ui_update_panel.py::test_restart_waits_for_new_boot` |
| F-30 | `tests/test_ui_update_panel.py::test_git_checkout_has_no_update_bar` |
| all transitions | `tests/test_update_state.py::test_transition_table` (every allowed edge persists; every other edge refused) |

Execution: four Sonnet subagents in separate worktrees (A: autoupdate.py; B: updater.py + recovery files;
D: app.js/shell.js), then C (server/cli/desktop/agent_api) on top of A and B. Opus reviews every diff, reverts each fix
to prove its test fails, and attacks the fixes (two processes, kill mid-swap, Mac/Windows paths simulated).
