# Harness router v2: pick the harness per task

Goal: MeshPool sends each task to the agent harness that is best for that kind of task (its own engine under a
profile, Pi, Hermes, and where the build ships them Codex or OpenCode), so the combination beats every single
harness. Off by default (`MH_HARNESS_ROUTER=1` turns it on) until it has been measured.

```
prompt ──> classify (signals; one model call only when unsure) ──> category
category + model id ──> scorecard ──> harness id [+ profile] ──> run it
```

Code: `app/meshpool_harness/hrouter/classify.py` (classifier), `hrouter/harness_select.py` (scorecard selection),
`app/defaults/harness_scorecard.json` (data), `testkit/loop/harness_scorecard.py` (fit), bench arm `meta`.

## 1. Classifier

Categories (the route keys MeshPool already uses, at harness-choice granularity): `code.debug`, `code.debug.perf`,
`code.build`, `code.build.algorithm`, `code.build.feature` (several files / keep every caller working),
`code.refactor`, `code.test`, `code.explain`, `code.review`, `code.terminal`, `ambiguous-build`, `data`, `docs`,
`research`, `browser`, `automation`, `chat`.

* **Stage 1 (free).** `route._decide` (signals.py) gives a spec, variant and confidence; `classify.category_of` maps it to
  a category. Very short "add X" requests become `ambiguous-build` with confidence 0.5 (signals cannot judge them).
* **Stage 2 (one model call).** Only when confidence < `MH_CLASSIFY_THRESHOLD` (default 0.7) or `MH_CLASSIFY=always`.
  One `client.chat` call on the user's own model (`model` argument, else `MH_MODEL`, through `core.client.LiveClient`, so
  the same endpoint, key and gateway as the agent), `max_tokens=12`, `temperature=0`. The prompt is a card: each category
  with a one-line definition and two short examples, the project signals (`languages: python; files: 12; git repo: yes`),
  the request truncated to 1500 characters, "Answer with the category id only." The answer is validated against the
  category ids (longest id wins, so `code.debug.perf` is not read as `code.debug`); an invalid answer, an exception or no
  client falls back to stage 1. The call's tokens come back as `classifier_tokens` (`prompt_tokens`, `cached_tokens`,
  `completion_tokens`, `model_calls`) to be added to the task's usage. Results are cached in memory per
  (request, project signals, model); a cache hit costs 0 tokens.
* **Stage 0 (offline, optional).** `MH_CLASSIFY_LOCAL=1` and `fastembed` importable: nearest neighbour (cosine) over the
  embedded example requests replaces the model call; no network. Below the threshold it falls back to stage 1. Without
  fastembed the model call is used. `fastembed` is not a dependency.
* The example requests are written from the dev suite and generic requests. Never copy a prompt from
  `testkit/loop/suites/v2_eval.json`; `test_card_has_every_category_and_no_eval_prompts` fails on any 40-character overlap.

Known limit: stage 1 sees most multi-file feature requests as plain `code.build` at 0.8, so they stay there. If the
measurement shows the build/feature split matters, lower stage 1's confidence for variant-less builds or run with
`MH_CLASSIFY=always`.

## 2. Scorecard

`app/defaults/harness_scorecard.json` (a copy in the data folder, then `MH_HARNESS_SCORECARD`, overrides per model and
category). Keys starting `_` are comments.

```json
{"deepseek-v4.1-flash": {"code.debug": [{"harness": "pi-routed", "solved": 9, "tasks": 10,
                                           "cost_per_task": 0.0069, "median_s": 95.0}, ...]},
 "*": {...}}
```

Harness ids are bench arm names: `meshpool`, `meshpool:<profile>`, `pi`, `pi-routed`, `hermes`, `hermes-routed`, `codex`,
`codex-routed`, `opencode`, `opencode-routed` (`-routed` = the MeshPool harness method rides on the first message).

Selection (`harness_select.pick`):

1. Candidates are harnesses available in this build: `harnesses.unsupported()` empty and installed (Pi via `pi_rpc`, others
   via `load_all()`); MeshPool's own engine is always available.
2. A row with fewer than `min_tasks` (3) tasks is not evidence. If a category has none, it inherits its parent
   (`code.debug.perf` -> `code.debug` -> `code`; `ambiguous-build` -> `code.build` -> `code`). With no evidence at any
   level: MeshPool's own engine under the profile `profiles.json` assigns to that route.
3. Highest solve rate wins. Harnesses within `tie` tasks (default 1, i.e. `1 / max(n)` of the rate) of it are tied; the
   lower `cost_per_task`, then lower `median_s`, wins. A model's own entry beats `"*"` (the pool over all models).

`select(prompt, workdir, model)` returns `{category, harness, profile, reason, classifier_tokens, stage, confidence}`.
The shipped scorecard is empty on purpose: nothing is claimed until it is fitted from runs.

## 3. Route fields

With `MH_HARNESS_ROUTER=1`, `hrouter.route()` also sets `Route.harness`, `Route.harness_reason`, `Route.category`
(all `""` when off). When the scorecard picks `meshpool:<profile>` the route's profile follows it (unless a profile is
forced by `MH_HARNESS_PROFILE`). The extra classifier call counts in `Route.used_model_call`.

## 4. Fit and measure

Fit on dev, measure on eval:

```
# 1. run every harness on the DEV suite, same model, metered (the meter makes cost comparable across harnesses)
python testkit/loop/bench.py --arms routed,profile:lean,pi,pi-routed,hermes,codex-routed --suites v2_dev \
       --repeats 3 --meter-port 8899 --out runs/dev
# 2. fit the scorecard (eval rows are refused unless --include-eval)
python testkit/loop/harness_scorecard.py runs/dev --out app/defaults/harness_scorecard.json \
       --md docs/results/harness_scorecard.md
# 3. measure the router on EVAL against its candidates (the meta arm classifies, picks and dispatches per task)
python testkit/loop/bench.py --arms meta,routed,pi-routed,hermes --suites v2_eval --repeats 3 --meter-port 8899 \
       --out runs/eval
```

The `meta` arm (`bench.run_meta`) runs `harness_select.select` in the task's process, then dispatches: MeshPool engine via
`run_task` with the chosen profile forced, `pi`/`pi-routed` via `run_pi_agent`, `codex`/`opencode`/`hermes` and their
`-routed` variants via `run_acp_agent`. The row carries `meta_harness`, `meta_category`, `meta_reason`, `meta_stage`. The
classifier call goes through the same endpoint, so the meter counts it; without a meter its tokens are added to the
row's usage. `harness_scorecard.py` skips `meta` rows (they are the router's output, not evidence for it).

Success criterion: on eval, `meta` solves at least as many tasks as the best single harness at no higher cost per solved
task, or the same at lower cost. Report per category: the table in `harness_scorecard.md` shows where each harness wins.
Do not turn `MH_HARNESS_ROUTER` on by default before that holds.
