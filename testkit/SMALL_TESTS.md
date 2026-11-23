# Small cost tests: is Meshpool cheaper than Codex?

Answer it in minutes and cents, without the 52-task eval. Code is in `testkit/loop/` (`small.py`, `small_meter.py`,
`small_codex.py`, `small_offline.py`, `small_fake.py`); tests in `app/tests/test_small_bench.py`.

## How to run

```
python testkit/loop/small.py                       # quick: 4 tasks, meshpool vs codex, about 1-3 min, about $0.01-0.05
python testkit/loop/small.py --set cost8           # the 8 tasks of the cost report (7 eval tasks + chat-explain)
python testkit/loop/small.py --set cost20 --repeat 2 --json out.json   # enough pairs for an interval
python testkit/loop/small.py --tasks 6 --arms meshpool,codex,v2 --budget 0.40
python testkit/loop/small.py --offline             # no network: prompt/tool-schema/context overhead per call, with budgets
```

Needs Python 3.10+ and the Codex CLI (`npm i -g @openai/codex`, or `CODEX_BIN=/path/to/codex`; an npx cache copy is found).
Same tasks, graders and price table as the full benchmark (0.15 / 0.003 / 0.60 USD per 1M tokens in / cached / out).
Concurrency 4 (`--parallel`), 300 s per task run (`--timeout`), hard budget in list-price USD (`--budget`, default
quick 0.25, cost8 0.60, cost20 1.50): when it is spent, the meter refuses further requests and those runs are dropped.

Your own key, model or gateway (any OpenAI-compatible endpoint that also serves `POST /responses` for the Codex arm):

```
export MESHPOOL_BASE_URL=https://my-gateway.example/v1  MESHPOOL_API_KEY=sk-...
python testkit/loop/small.py --model my-model-name --price-in 2.5 --price-cached 1.25 --price-out 10
# Azure OpenAI: the v1 endpoint, the deployment name as the model, key sent as `api-key` automatically
export MESHPOOL_BASE_URL=https://MYRES.openai.azure.com/openai/v1  MESHPOOL_API_KEY=<azure key>
python testkit/loop/small.py --model my-deployment --price-in ... --price-out ...   # classic URLs: add --api-version 2025-04-01-preview
```
With no variables set it uses `https://llm.meshpool.ai/v1` and `deepseek-v4.1-flash` (a proxy-injected credential works).
Set the `--price-*` flags to the model you test, or the dollar numbers are those of DeepSeek V4.1 Flash.

## How to read the output

- One table row per task and arm: solved, tokens in / cached / out, model calls, $ per task, seconds; the Meshpool row
  also shows its `$` ratio to Codex on the same task. Then a per-arm summary: solved, $/task, **$/solved**, calls/task.
- Calls and tokens are counted by a metering proxy on every request, streamed or not (Codex's own usage event is one
  record per turn, which is why an earlier run showed an implausible 1.0 calls/task for Codex).
- `VERDICT: CHEAPER|NOT CHEAPER (ratio x.xx, n tasks, solved a vs b; ...)`. The ratio is Meshpool $ per solved task over
  Codex $ per solved task, so a failed task costs money and earns nothing. Below 1.0 is cheaper.
- Noise: with fewer than 8 task pairs there is no interval and the line says `not significant`: treat it as a smoke
  test of the direction. With 8 or more it prints a paired bootstrap 95% interval; if it spans 1.0 it says `not
  significant`. One run per task is one draw from a noisy process: use `--repeat 2` or `--set cost20` before claiming a win.
- Spend is list price; the Meshpool gateway bills about 2x. `--json` keeps every row, the ratios and the verdict.
- `--offline` prints, per lane (code, work, chat, data) and for Codex's prompt: system and tool-schema tokens (the
  fixed cost of every call), the whole request at call 1 and 6 and the growth per call, over a scripted 6-step task, and
  fails (exit 1) when a lane is over its budget in `testkit/loop/small_budgets.json` (measured + 10%). Re-measure after an
  intended prompt change with `python testkit/loop/small_offline.py --write-budgets`. Tokens are chars / 3.7.
