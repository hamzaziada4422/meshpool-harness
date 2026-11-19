# Local CI replica: will `tests` pass, and what does a run cost

Date: 2026-11-19. Nothing was pushed, enabled, triggered or edited on GitHub; no model endpoint was called.
Machine: 4-core Ubuntu container, Python 3.12.3 venv, LibreOffice and Chromium pre-installed. The working tree was being
edited by other agents while this ran (see "Caveats").

## 1. Workflows

| Workflow | Trigger | Runners | Steps | Expected duration |
|---|---|---|---|---|
| `tests` (`tests.yml`) | pull_request (ignoring docs/**, *.md, testkit/loop/**, harness-loop.yml), push to `main`, manual. Concurrency group per ref, cancel-in-progress. | 1 job, `ubuntu-latest`, timeout 30 | checkout, Python 3.12 (+pip cache), apt LibreOffice (calc, impress, writer) + fonts + poppler, pip `packaging/requirements-cli.txt` + playwright + pytest, `playwright install --with-deps chromium`, Selawik font alias, `cd app; python -m pytest -q -p no:cacheprovider tests`. Sets `MH_ALLOW_SYSTEM_SOFFICE=1` (added in the working tree). | about 8.5 min of tests here on 4 cores plus 3-5 min of setup. Estimate 13-17 min on a runner. |
| `Release` (`release.yml`) | release published, manual. One build at a time, no cancel. | `mac` on `macos-15` (timeout 90), `windows` on `windows-latest` (timeout 90), `publish` on `ubuntu-latest` | mac: download python-build-standalone (arm64 + x64), clone Hermes at a pinned commit and patch it, pip install both runtimes, Node, Pi, `build_all.py --only=mac-arm64,mac-x64` (bundles LibreOffice), clang launchers, `check_minos`, sign (skipped without secrets), `repack.sh` dmgs, mount and smoke-test both dmgs. windows: same runtimes for Windows, Go launcher and winres, `build_all.py --only=win`, smoke, Azure signing (skipped without secrets), NSIS installer. publish: `git archive`, SHA256SUMS, draft release, upload, verify, publish. | mac 30-50 min, windows 25-40 min, publish 1-2 min. The two big jobs run in parallel. |
| `harness-loop` (`harness-loop.yml`) | manual, or push to `harness-router` only if the commit message contains `[loop]`/`[probe]`/`[swe]`. No schedule. | `loop` and `swe` jobs on `ubuntu-latest`, timeouts 150 and 300 | pip install, then benchmark runs that call a paid model through `MESHPOOL_API_KEY` | up to 150 / 300 min and real model spend. Not part of CI; leave it disabled. |

Billing multipliers on private repos: Linux 1x, Windows 2x, macOS 10x. Each job is rounded up to whole minutes.

## 2. Reproduction

`docs/plan/ci_local.sh` repeats the Linux job: it checks or installs the same apt packages, installs the CI Python
packages with `uv pip`, sets up the Selawik "Segoe UI" fontconfig alias, exports `MH_ALLOW_SYSTEM_SOFFICE=1` and
`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, then runs `cd app && python -m pytest -q -p no:cacheprovider tests`.
Run it with `docs/plan/ci_local.sh`; extra arguments go to pytest.

## 3. Results

| Run | Result | Time |
|---|---|---|
| 1 (before `MH_ALLOW_SYSTEM_SOFFICE` was added to the workflow) | 1 failed, 1120 passed, 7 skipped | 6 min 40 s of tests |
| 2 (final, current tree, script as committed above) | **0 failed, 1143 passed, 7 skipped** | 8 min 19 s of tests |

The count differs because other agents added tests between the runs (test_bundled_tools, test_updater_auto and others).

### Failures and skips

1. `tests/test_sheets_finance.py::test_engine_refuses_source_reads_and_pushes_a_first_build` (run 1 only) failed with
   `ModuleNotFoundError: No module named 'engines'` from a subprocess that is meant to get PYTHONPATH from
   `core/tools.py::_run`. **Class: (c) flaky or transient, most likely caused by concurrent edits to
   `core/tools.py` / `config.py` by other agents during run 1 (those files are modified in the tree).** It passed in
   isolation three times, in the whole `test_sheets*` group (52 passed) and in the complete run 2. I could not make it
   fail again, so there is no confirmed root cause and no fix. If it appears on GitHub, look at `_run()` in
   `app/meshpool_harness/core/tools.py` (the `packaged()` branch that uses `user_env()` and does not set PYTHONPATH).
   That file is owned by another agent, so nothing was changed.
2. Seven skips in run 2:
   - 2 in `test_ui_new_chat.py` (no Chromium). **Class (a), environment.** The venv got Playwright 1.65.2 which wants
     Chromium 1255, and the pre-installed browser is 1243. CI runs `playwright install` and so gets a matching
     browser. With Playwright 1.63.0 (revision 1243, installed with `--target`, venv untouched) and
     `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, `test_ui_new_chat.py`, `test_browser_ctl.py` and
     `test_browser_flags.py` give 7 passed.
   - 1 `test_responses_wire.py:38` needs an `eval/` folder that is not in the repository. It skips on GitHub too.
   - The other 4 come from `test_routing_v3.py` ("dev suite not present", also skips on GitHub) and other conditional
     skips in `test_acp_fixes.py` / `test_providers.py`. In an isolated run those files had 138 passed and 4 skipped,
     two of them the Chromium ones above. I did not trace every remaining skip individually.
3. In the Chromium re-run, the conftest check that no test leaks `/tmp/meshpool-gateway-slots` files errored, because
   a Meshpool UI (`meshpool_harness ui --port 8799`) started by another agent was holding slot files. **Class (a),
   interference from a shared machine; will not occur on a clean runner.**

No test was skipped, disabled or weakened. No source was changed.

## 4. Release build path (Linux-relevant parts)

- `build_all.py` only builds mac-arm64, mac-x64 and win trees. It has no Linux target and needs `deps/` (python
  standalone runtimes, Hermes checkout, Node, Pi, Go-built `Meshpool.exe`), which are downloaded by the workflow
  and are not in the repository. With `--only=win` and `--only=mac-arm64` it passed `check_winres()`, then stopped with
  `FileNotFoundError: deps/win/Meshpool/Meshpool.exe` and `deps/rt-mac/python`. That is the expected result of missing
  inputs and shows no bug. I did not download the runtimes. The build also does not run on this tree without
  `MH_BUNDLE_LIBREOFFICE=0`, otherwise it would download LibreOffice (hundreds of MB).
- Note: `build_all.py` deletes and recreates `dist-src/`, which is not in `.gitignore`. I removed the empty
  `dist-src/` my run created. Adding `dist-src/` and `deps/` to `.gitignore` is worth doing.
- `packaging/smoke.py --app app --version 0.0.7` with the venv Python: **PASSED** in 4.6 s (imports, Word and PDF,
  server start, mock-model chat). This is the same smoke the release runs against the built apps.
- Not verifiable here: macOS and Windows jobs (`check_minos.py`, `repack.sh`, hdiutil, clang, NSIS, signing).
  `release.yml` changed in the tree (Intel now needs macOS 11 like Apple silicon, so no Intel exemptions; Chromium is
  the one `check_minos` exemption, at 13.0; each built tree also runs `packaging/verify_lo_prune.py` against its pruned
  LibreOffice). Expect the first Mac job to be the one most likely to fail (Chromium and LibreOffice on a real Mac).

## 5. Estimated Actions minutes per run

Linux runner assumption: a private repo's `ubuntu-latest` has 2 vCPU, so the 8.5 min test time here (4 cores, partly
parallel LibreOffice work) is likely 10-14 min there. Totals below are estimates, not measurements.

| Run | Runner | Wall minutes | Multiplier | Billed minutes |
|---|---|---|---|---|
| `tests` | ubuntu | 13-17 | 1x | **13-17** |
| `Release` mac job | macos-15 | 30-50 | 10x | **300-500** |
| `Release` windows job | windows-latest | 25-40 | 2x | **50-80** |
| `Release` publish job | ubuntu | 1-2 | 1x | 1-2 |
| Release total | | | | **about 350-580** |
| `harness-loop` | ubuntu | up to 150 + 300 | 1x | up to 450, plus model cost; never enable |

A run that is cancelled or fails early still bills what ran. One release is roughly 20-35 `tests` runs. On a plan with
2,000 free minutes a month, a single release uses a quarter to a third of the month, and a failed Mac build that is
retried twice can use all of it.

## 6. Recommendation: minimum safe CI to enable at release

1. Enable only `tests`, and change its triggers to: `push` on the release branch (for example `release/**`), plus
   `workflow_dispatch`. Remove the `pull_request` trigger and the push to `main` for now. Keep `concurrency` with
   `cancel-in-progress: true`, `timeout-minutes` at 20-30 and add path filters so that it only starts for
   `app/**`, `packaging/requirements-cli.txt` and `.github/workflows/tests.yml`. That is one Linux job, 13-17 minutes.
2. Do not enable `harness-loop` (paid model calls, up to 7 hours of runner time). Keep it disabled at repository
   level, not just by commit message.
3. Keep `Release` manual (`workflow_dispatch`) until the Linux job has been green; do not leave `release: published`
   active until then, because one publish can cost 350-580 minutes. Before the first real release, run
   `docs/plan/ci_local.sh` and `packaging/smoke.py` locally (both pass now) and read
   "Open decisions" in `docs/plan/BUNDLED_TOOLS.md`. Consider a Windows-only dry run first (about 60 minutes billed) and run the Mac job
   last, once.
4. Add `dist-src/` and `deps/` to `.gitignore`; consider caching the `playwright install --with-deps` browsers and
   the apt LibreOffice packages to save 2-3 minutes a run.

## Caveats

- Other agents edited `app/` (including `core/tools.py`, `config.py`, `harnesses/`, `ui/`) during the work, so run 1 and
  run 2 did not test an identical tree. Re-run `docs/plan/ci_local.sh` on the final commit before enabling anything.
- Run time here is not a runner measurement. Package versions are the newest allowed by `requirements-cli.txt`
  (it has no pins), so GitHub may resolve different versions.
