# bench3: Meshpool vs Codex vs Pi on one model, one meter

One command runs every arm on the same tasks, same model, same metering proxy, side by side per (task, rep).

    python testkit/bench3/run.py --out docs/results/v4/<name> --tasks all --reps 3 --model gpt-6-luna \
        --parallel 4 --budget 30 --mp-src app [--arms a,b] [--upstream URL] [--python PATH] [--rerun]
    python testkit/bench3/report.py docs/results/v4/<name>

Arms: `codex` (Codex CLI), `pi` (Pi CLI), `mp-cli` (`python -m meshpool_harness -p --auto`), `mp-work` / `mp-code` (the
desktop app's own agent through its HTTP API), `mp-on-pi` / `mp-on-codex` (the app driving Pi / Codex). Office tasks
default to codex, pi, mp-work, mp-on-pi, mp-on-codex, mp-cli; code tasks use mp-code instead of mp-work.
`--mp-src` is the `app/` folder of the Meshpool tree to test (run once on a baseline checkout and once on the current tree).
`mp-on-pi` needs the app's vendored Pi, which source checkouts lack: arms.py installs it into
`~/.cache/meshpool-small/bench3` and runs the app from a copy of `--mp-src` with `vendor/pi` linked (the tree under test
is not touched).

Every arm points its base URL at `http://127.0.0.1:<port>/m/<task>/<arm>/<rep>/v1` (the meter in
`testkit/loop/small_meter.py`). Meshpool arms write the URL into a private `meshpool.config` (it overrides the
environment) and export `MESHPOOL_BASE_URL`/`MH_MODEL`, so nested calls (office engines, the grader) land on the same path
and are counted. The meter refuses calls (HTTP 402) once `--budget` USD is spent; a 429 from the gateway stops the run.

Output in `<out>`: `results.jsonl` (one row per run: ok, detail, secs, calls, failed_calls, input, cached, output,
cost), `meter.jsonl` (every model request), `run.json` (versions, model, prices), `logs/`, `work/`, `REPORT.md`,
`summary.json`. Cost = uncached input + cached input + output at `prices.json` (USD per 1M); a model without a price
gets no cost. Wall-clock is the agent run only (for the app arms: send to turn end), not setup or grading.
Resume: finished (task, arm, rep) are skipped.

Known gaps: the meter has no reasoning-token field (reported as null; output already includes reasoning for billing);
it keeps calls in memory (run.py mirrors them to meter.jsonl after each job); a model reply without a usage block counts
as a call with zero tokens.

Tests: `python -m pytest -q -p no:cacheprovider testkit/bench3/tests/test_runner.py`. Smoke without the real tasks:
`--tasks-module testkit/bench3/tests/hello_tasks.py`.
