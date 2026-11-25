# Harness profiles: the setup changes per task and per run

A **profile** is a named, complete harness setup. Which profile a request runs under is configuration, not code, so the
optimizer can choose it per kind of task (and per plan step) and we measure the choice: quality first, then cost,
then speed.

## Concepts

| setting | values | what it replaces |
|---|---|---|
| `plan` | `auto` / `on` / `off` | `MH_PLAN` (`agent/planning.py`) |
| `review` | bool | `MH_REVIEW` (independent review, `code.build/debug/refactor/test` only) |
| `mutation` | bool | `MH_MUTATION` (mutation feedback on the tests) |
| `compact_chars` | int | `MH_COMPACT_CHARS` (context size before old tool output is shortened) |
| `methods` | `null` or a set name | the spec texts: `null` is the built-in text, `"v2"` loads `app/defaults/methods/v2.json` |
| `lean_tools` | bool | `MH_LEAN_TOOLS` (the specs' drop lists) |
| `step_methods` | bool (default false) | new: one method line when the plan moves to a different kind of step |
| `engine` | `meshpool` / `pi` / `hermes` | new: which bundled agent the route asks for |
| `extends` | profile name | start from another profile and change a few keys |

Built-in profiles (`app/meshpool_harness/hrouter/profiles.py`):

- `full`: today's behaviour. plan auto, review on, mutation on, compact 160000, built-in texts, lean tools.
- `lean`: plan off, review off, mutation off, compact 160000, methods `v2`.
- `lean+plan`: `lean` with plan `auto`. `lean+review`: `lean` with review on.

With no `profiles.json` every route runs `full`, so nothing changes until the optimizer writes an assignment.

## Files

`app/defaults/profiles.json` (shipped, the optimizer writes it), then `<data folder>/profiles.json`, then
`MH_PROFILES_FILE`; later wins, per profile and per key:

```json
{
  "default": "full",
  "assign": {"code.debug": "lean", "code.debug.perf.python": "lean+review", "data": "lean+plan"},
  "profiles": {"mine": {"extends": "lean", "plan": "on", "compact_chars": 80000, "step_methods": true}}
}
```

Assignment keys are the keys hrouter already uses (`_keys` in `hrouter/route.py`): `spec`, `spec.variant`,
`spec.language`, `spec.variant.language`. The most specific key with an assignment wins; else `default`. An unknown
profile name falls back to the default, then to `full`.

Methods sets use the `specs.json` format (`{"specs": {"code.build": {"addon": "..."}}}`) and live in
`app/defaults/methods/<name>.json` or `<data folder>/methods/<name>.json`. Text precedence, lowest to highest: built-in
texts, `app/defaults/specs.json`, `<data folder>/specs.json`, the profile's methods set, `MH_SPECS_FILE`.

## Precedence of switches

1. Explicit env flags: `MH_PLAN`, `MH_REVIEW`, `MH_MUTATION`, `MH_COMPACT_CHARS`, `MH_LEAN_TOOLS`, `MH_SPECS_FILE`.
2. The route's profile (resolved once when the task is routed; `route.profile`, `route.settings`, `route.chip`).
3. Built-in `full` defaults.

`MH_HARNESS_PROFILE=<name>` forces one profile for every route (experiments). `MH_PROFILE=<name>` does the same when
the name is a harness profile; `MH_PROFILE` is also the model-profile override (`profiles/*.yaml`), and that lookup now
ignores names it does not know, so one variable can carry either.

## Per plan step

With `step_methods: true`, when the plan's current step changes the agent routes the step text with hrouter (no model
call). If the step belongs to a different sub-harness than the turn and that harness has a method, it adds one line,
`[Meshpool] For this step (Tests): <first sentence of the method>`, at most once per sub-harness per turn. Chat and
general routes are ignored. Measure it as its own profile (`"step_methods": true`).

## Run it: dev, select, write, measure on eval

```bash
# 1. bench profile arms on the DEV suites (arm "profile:<name>" runs routed MeshPool with MH_HARNESS_PROFILE=<name>)
python testkit/loop/bench.py --suites debug,build,hard --repeats 2 --out <scratch> \
    --arms profile:full,profile:lean,profile:lean+plan,profile:lean+review

# 2. choose per group (dev results only); prints the decision table
python testkit/loop/select_profiles.py <scratch> --out app/defaults/profiles.json --md <scratch>/decisions.md

# 3. measure the committed assignment on EVAL against full (routed arm with no forced profile uses profiles.json)
python testkit/loop/bench.py --suites v2_eval --arms profile:full,routed --out <scratch>
```

Selection rule (`select_profiles.py`): for each group with at least `--min-tasks` tasks (default 3), the profile with
the most tasks solved; ties by lowest total cost, then lowest median time. The overall best becomes `default`. A group
gets its own assignment only if its winner solves more than the default on that group, or ties and is at least 15%
cheaper (`--min-saving`); otherwise it inherits the default. Only tasks every profile ran are compared; repeats are
averaged per task. Arms are profile names (`profile:lean` or `lean`); use `--arm-profile ARM=PROFILE` for other names.
Custom profile definitions already in `--out` are kept. Rows of `*eval*` suites are dropped unless `--include-eval`:
select on dev, measure on eval. Bench rows now also carry `route_keys` and `profile`.

## Engines pi and hermes: wiring left

`engine` is carried on the route (`route.engine`, `route.settings["engine"]`, in `route.to_dict()` for the route chip
event) but only `meshpool` is wired in the agent. Left for the app: in `ui/agent_api.py` (session start, where
`harness_router` picks `harness`), prefer `route.engine` over the benchmark-table pick when it is `pi` or `hermes`
and that harness is installed (`harnesses.get`); for the outside agents the route's `addon(route)` rides on the first
message already (`route_addon`), and plan/review/mutation/compact do not apply there. `bench.py` arms for them
(`pi`, `pi-routed`, `hermes`) exist separately; a `profile:<name>` arm runs the meshpool engine only.
