# Handoff: MeshPool harness router, V2 sprint (continue from here)

You are picking up the MeshPool harness-router work. Read this whole file first, then
`docs/plan/STATUS.md`, `docs/plan/OSS_RESEARCH.md`, `docs/plan/SWE_FEASIBILITY.md` and `docs/LEADERBOARD.md` in the
repo.

## Where everything is

- **Repo:** `~/Downloads/meshpool-harness`, branch `harness-router`, pushed to `hamzaziada4422/meshpool-harness`
  (PR #1, not merged). Commit everything there. GitHub Actions are **disabled**: do not enable them until the final
  green run and release (see "Release").
- **Frozen V1 MeshPool** (the "before"): commit `b52c31c`, checked out as a git worktree at `~/.cache/meshpool-v1`.
  Benchmark arm `v1` runs it.
- **Latest results:** `docs/results/v2/V2_REPORT.md`, `docs/BENCHMARKS_V2.md` (verdict: promising but unproven).
- **Benchmark code:** `testkit/loop/`
  - `bench.py`: runner, arms, metering, statistics, cap guard.
  - `meter.py`: a metering proxy every harness's model calls go through.
  - `swe_local.py`: SWE-bench Verified SymPy tasks without Docker.
  - `dabench.py`: InfiAgent-DABench with its official grader.
  - `optimize.py`: Harness²-style optimizer.
  - `v2/`: the V2 suite (task definitions with reference solutions, `build.py` validator, `report_v2.py`, `why.py`).
  - `suites/`: generated suites.
- **Results:** V1-era raw results in `docs/results/v1-raw/`; reports in `docs/results/`. V2 runs in `runs-v2/`
  (gitignored raw data; copy the reports you keep into `docs/results/v2/`).
- **Keys:** `~/.meshpool-bench.env` (MESHPOOL_API_KEY and MESHPOOL_BASE_URL; never print it). The benchmark loads it
  automatically.

## Hard constraints (from the owner)

- **DeepSeek V4.1 Flash only** (`MH_MODEL=deepseek-v4.1-flash`). No GPT, Claude or Kimi.
- **$25 cap**, enforced by `bench.py` from the key's billed spend (ledger: `~/.meshpool-bench-ledger.json`). Check it
  with `python testkit/loop/bench.py --cap-status`. **$10.02 spent, $14.98 left** after the V2 runs (19 Nov 2026).
- No GitHub Actions or cloud CI for benchmarks. Do not increase repetitions (1 for V2 and DABench, 2 for SWE SymPy).
  Do not weaken tasks or graders. Do not tune on `v2_eval` tasks (use `v2_dev`). Report regressions and ties honestly.
- **Machine safety:**
  - Keep total parallel benchmark processes at 12 or fewer: 20 crashed the Mac once and wiped `/tmp`.
  - Write results under the repo (`runs-v2/`), never `/tmp`.
  - Free disk was 47 GB after the reboot. `~/.npm` grows by gigabytes from `npx` agents; clear it between runs with
    `npm cache clean --force`.
  - `bench.py` keeps the Mac awake with `caffeinate` during runs.

## How to run things

```sh
cd ~/Downloads/meshpool-harness
export MH_MODEL=deepseek-v4.1-flash
P=~/.venvs/meshpool/bin/python            # Python 3.12 venv with every dependency

# Full V2 evaluation (52 tasks x 6 harnesses, about $2-3, about 60-90 min at --parallel 8)
$P testkit/loop/bench.py --suites v2_eval --arms routed,v1,pi,codex,opencode,hermes --out runs-v2/v2eval52 \
  --parallel 8 --budget 4 --timeout 600
python3 testkit/loop/v2/report_v2.py runs-v2/v2eval52 --suite-file testkit/loop/suites/v2_eval.json --title "V2 evaluation (52)"

# DABench (24 prepared; regenerate with dabench.py --n N)
$P testkit/loop/bench.py --suites dabench --arms routed,v1,pi,codex,opencode,hermes --out runs-v2/dabench24 \
  --parallel 3 --budget 1.5 --meter-port 18991

# Rebuild or extend the V2 suite (every task must fail as given and pass with its reference)
python3 testkit/loop/v2/build.py ; python3 testkit/loop/v2/why.py <task-id>

# App unit tests (must be green before any release)
cd app && PATH="$HOME/.venvs/meshpool/bin:$PATH" $P -m pytest -q -p no:cacheprovider tests
```

The 47 local test failures are all environmental (LibreOffice is missing because its install failed; browser tests;
a case-insensitive disk). CI installs those dependencies.

## What V2 changed in the harness (all in `app/meshpool_harness/`)

- **Routing:** `hrouter/` (signals, specs, route, packs, outcomes). Sub-subtask variants for debug and build;
  data-driven overrides in `app/defaults/specs.json`; issue reports route to debug; per-model measured routing
  (`harness_router.measured`, written by `testkit/loop/routing_fit.py`).
- **Zero-token pre-steps:**
  - reproduce the named failing command;
  - error context shows the failing lines and counts them as read;
  - data preview;
  - repo map;
  - a syntax and bug check on every edit.
- **Agent behaviour** (`agent/core.py`):
  - verify-before-done: one nudge when the last check after a change failed;
  - never fetch the upstream fix;
  - targeted tests;
  - lean tool sets for focused fixes;
  - no narration between tool calls;
  - parallel read-only tools;
  - per-tool traces in `run_task(...)["trace"]`;
  - a review receipt;
  - refused-field adaptation;
  - a quota stop;
  - a masked-key message.
- **Memory v2** (`assistant.py`): relevant recall, project notes, fenced and scrubbed.

## Evidence-backed next steps (in order)

1. **Cut self-testing churn on build-from-spec tasks.** Traces from the V2 eval: on algorithmic tasks MeshPool made
   71 `bash` calls where Codex made a handful; v2-alg-meeting-slot took 32 calls against Codex's 5. Change the build
   harness so the agent writes one test script with all its checks and reruns that, instead of one-off probes. Tune
   on `v2_dev`, measure on the eval suite.
2. **Edit failures:** 8 of 12 `edit` calls failed on those tasks. Make the edit tool's error show the nearest
   matching lines (a fuzzy hint), and allow `write_file` for files under about 80 lines. Measure failed-edit rate and
   calls.
3. **Run the full 52-task V2 evaluation** (command above) and DABench 24; write the final report.
4. **Wire the remaining public benchmarks:**
   - **SpreadsheetBench Verified** (HF `KAKA22/SpreadsheetBench`, 400 tasks; grader `evaluation/evaluation.py`; needs
     LibreOffice for recalculation: retry `brew install --cask libreoffice`).
   - **OfficeBench** (`zlwang-cs/OfficeBench`, Apache-2.0, 300 file-based subtasks). Its grader `utils/evaluate.py`
     uses its own `apps/` readers: copy each task's `testbed/` as the workspace and run `evaluate()` on the result.
     The research is done; only the adapter is missing.
5. **Office and document harnesses** (Word, Excel, PowerPoint): add sub-harness specs (`docs.word`, `docs.sheets`,
   `docs.slides`) and measure them on SpreadsheetBench and OfficeBench.
6. **Use more open-source tools** (`docs/plan/OSS_RESEARCH.md`):
   - SolidLSP (MIT) symbol tools for build and refactor;
   - ast-grep codemods for refactor;
   - semantic-router plus fastembed for routing stage 1;
   - trafilatura for fetch_url.
   Add each behind a flag and keep it only if the loop shows it helps.
7. **SWE-bench Verified at scale** needs approval and Docker (not feasible on this Mac); see
   `docs/plan/SWE_FEASIBILITY.md`.

## Release (only when everything is green)

1. Install LibreOffice locally (or rely on CI). Get `pytest tests` green.
2. Enable the `tests` workflow (`gh workflow enable tests -R hamzaziada4422/meshpool-harness`) and confirm green.
3. Bump `__version__` (now 0.0.7) and run `python packaging/build_all.py --winres`.
4. Enable `Release` and run it from `harness-router` with
   `gh workflow run release.yml --ref harness-router -R hamzaziada4422/meshpool-harness`. The Mac job is pinned to
   `macos-15`, because `macos-latest` had no Apple-silicon capacity and `macos-14` is retired. It builds Mac Apple
   silicon, Mac Intel and Windows and publishes the release. The default model stays DeepSeek V4.1 Flash.
5. Disable the workflows again afterwards.
