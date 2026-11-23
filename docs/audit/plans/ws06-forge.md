# ws06-forge plan: deck engine reviewers, models, content checks

Starting point: branch `claude/sleepy-faraday-tkjcox` (0.0.12 + ledger + `fsio.py`). Every item was checked against
0.0.12 before planning. Reproduction harness: `testkit/single_deploy_proxy.py` puts a single-deployment endpoint in
front of the real gateway (only the configured model exists, every other id gets 404 DeploymentNotFound, and
`temperature` gets 400 "Unsupported parameter").

## Baseline on 0.0.12 (chart deck from quarterly.csv, configured model deepseek-v4.1-flash)

- Proxy rejecting `temperature`: the build fails at once ("storyline came back empty ... HTTP 400 Unsupported
  parameter: 'temperature'"). Worse than the audited 0.0.9 run.
- Proxy accepting `temperature`: 130 calls, 70 of them 404 (gpt-6.1-terra 23, gpt-6-luna 16, gpt-5.6-luna 16,
  kimi-k3 8, claude-sonnet-5.5 7). Most came from the clarity pass (64 calls) and the figure-consistency check
  (21 calls): every batch walks the fixed chain again and is billed for it.
- With the reviewers reachable (direct to the gateway) the research dossier ran web research for a deck about the
  user's own CSV and pulled unrelated web figures into the facts.
- The deepseek run happened to chart every region and quarter; the audited gpt-6-luna run did not. Nothing in the
  engine guarantees it and none of the deck rules checks it.

## S-03: reviewers call undeployed fixed models and send temperature

Still reproduces (above). Root cause: fixed model lists in `engines/forge/review.py` (`CHAIN`),
`engines/forge/clarity.py` (`CHAIN`), `engines/forge/critics.py` (text/deck models built on `review.CHAIN`),
`engines/forge/design.py` (vision models from `review.chain`), `engines/forge/select.py` (`review.CHAIN`),
`engines/sheets/engine.py` (`REVIEW_CHAIN`), `meshpool_harness/importer.py` (`MH_CONTEXT_MODEL` default
gpt-6-luna); and `core/client.py` `LiveClient.chat` never remembers a 404 model and retries a 400 with the same
parameters. `classify_error` does not recognise "DeploymentNotFound" as a missing model.

Fix (root cause, one place):
- New `meshpool_harness/core/models.py`: `reviewer_models(*explicit)` = explicit models and env overrides, then the
  configured model (`MH_MODEL`), never a built-in model name, minus models remembered as missing on this endpoint.
  `vision_reviewer_models()` additionally drops models remembered as unable to read images.
- Per-endpoint capability memory (`MESHPOOL_HOME/cache/endpoint_models.json`, written with `fsio.update_json`, so
  engine subprocesses and later runs share it; entries expire after 24 h so a newly deployed model is tried again):
  `mark_missing / is_missing`, `mark_unsupported / unsupported_params`, `mark_no_vision`.
- `LiveClient.chat`: a model remembered as missing raises at once (no network call, nothing billed); a 404 / model
  missing marks it; a 400 naming an unsupported parameter marks it, drops it and retries once at once; remembered
  unsupported parameters are never sent again; a 400 about images on a request with images marks the model as
  text-only. `classify_error` treats DeploymentNotFound as `model_missing`.
- All reviewer call sites above use the helper; when the list is empty the review is skipped with a note
  ("skipped: no reviewer model on this endpoint ...") and nothing is called.
- Calls per deck: the fallback chain was most of the cost (70 of 130 calls). With one configured model each pass
  is one call per batch. Passes that stay: clarity (one batched call per deck, plus one per repair round on the
  repaired slides only), figure consistency (one call), partner/MD panel (off by default in rules.yaml).

Tests (`app/tests/test_reviewer_models.py`): a local single-deployment HTTP server (only `m-main`; 400 on
`temperature`); run the clarity pass, the partner review chain, critics models and the sheets review chain through
a real `LiveClient`; assert zero requests for any other model, at most one 400 per (model, parameter) across two
clients (memory persists), a remembered 404 model is never requested again, `reviewer_models()` never contains
built-in names, and an empty reviewer list gives a clean "skipped" note.

## X-01: chart deck from quarterly.csv lacks the requested data

Root cause: the engine leaves the data to the writing model (CSV rows become loose facts) and no check compares a
chart with the source data or the request; the research dossier also adds web figures to a deck about the user's
own file.

Fix:
- New `engines/forge/data_pages.py`: reads CSV/XLSX inputs (pandas), reads the request for the measure ("revenue"),
  the category axis ("by quarter") and the series ("for each region"), with simple derivations (revenue = units x
  unit price when there is no revenue column; month / quarter from a date column), computes the pivot and returns
  (a) a computed chart page (`fixed: True`, like the finance pages) with every category and every series, and its
  facts, (b) the expectation for the content check.
- `engine.solve` adds the page (inside the slide count) and passes the expectations to the build.
- Content check `check_data(pptx, expectations)`: some chart must hold every expected category and series with the
  source values (either orientation, rounding and K/M scaling tolerated); a failure is a rule failure (P22 "chart
  data matches the source data the request asks for") that names what is missing; `tools.check_deck` /
  `qa_summary` report it too.
- Dossier: no web research when the brief is about attached data files and names no company.

Tests (`app/tests/test_forge_data_pages.py`): the pivot for the audit's request on a quarterly.csv fixture (4 regions
x 4 quarters); the computed page has 4 series x 4 categories with the CSV values; `check_data` fails on the audit's
deck (one chart, one series "Q4 revenue") and passes on a correct one; an offline engine build (stub client) carries
the page; the dossier skips research for a data-only brief.

## X-04: one request produced extra decks and engine runs

0.0.12 already blocks hand-built decks (`agent/core.py` refuses python-pptx decks in Work). Still reproducible: the
MCP `make_deck` returns "still working" after 100 s and a second identical call starts a second engine process; a
finished build whose report says "flagged" invites a rebuild; nothing ties builds to the request they serve.

Fix (`agent/office_jobs.py`, `agent/office_mcp_server.py`, `agent/core.py`): jobs carry a scope (the chat); a second
call with the same engine, request and inputs in the same scope reuses the running job (waits on it) or returns the
finished build's report ("already built") instead of starting the engine; a rebuild of a similar request past the
rebuild limit in the same chat is refused with the reason; the report tells the agent to deliver a flagged build
rather than rebuild it.

Tests (`app/tests/test_office_jobs_scope.py`): duplicate while running -> one process; duplicate after finishing ->
no new process, report says already built; a different request still builds.

## M27-03: the rebuild counter is global to the app process

Root cause: `office_jobs.new_job` counts every job of the engine in `JOBS` (process wide). Fix: count per scope (the
chat id: `Agent.id` for office_task, `MH_CHAT_ID` for the MCP server, which `agent/mcp.py` now passes to the office
server too). Test: three decks in chat A, then chat B's first deck reports build 1 and no "stop rebuilding".

## M33-01: forge logo cache written in place and trusted when non-empty

Root cause: `engines/forge/logos.py` writes the SVG with `write_bytes` and serves any non-empty cached file; the
download is also cut at 200 KB without noticing. Fix: download to memory, refuse a body that hit the size cap,
validate that the SVG parses with an `<svg>` root, write with `fsio.atomic_write_bytes`; a cached file that does
not parse is deleted and fetched again. Same for `simple-icons.json`. Tests: truncated cache file is replaced;
a killed writer leaves no partial file; an oversized download is refused.

## B12 / H-12: Python 3.10 and 3.11

Root cause: `engines/forge/validate_pptx.py` P21 message uses a backslash inside an f-string expression (PEP 701,
3.12 only); `importer.py`, `agent_import.py` and `packaging/hermes_requirements.py` import `tomllib` (3.11+), so on
3.10 Codex MCP servers are silently skipped. Sweep: `vermin --target=3.10` over the whole app, packaging and testkit,
plus the full test suite run under Python 3.10.

Fix: rewrite the message without the nested quotes; `tomllib` falls back to `tomli` (added as a dependency for
Python < 3.11 in pyproject and requirements-cli); fix anything else the 3.10 suite run turns up. Test
(`app/tests/test_py310_compat.py`): compiles every .py of the app, testkit and packaging with a Python 3.10
interpreter (`uv python find 3.10`), skips locally when none is found but fails in CI (`CI` set); an AST scan for
3.11+/3.12+ standard-library names without a fallback; Codex config import works when `tomllib` is missing.
