# bench3 request profile

No model calls: every arm runs against a local fake upstream that records each request body.

    /root/.venvs/mp/bin/python testkit/bench3/profile/run_profile.py            # all six arms on xlsx_edit, model deepseek-v4.1-flash
    /root/.venvs/mp/bin/python testkit/bench3/profile/run_profile.py --arms mp-work --calls 13 --out /tmp/cap13   # more turns: grader, notes, growth
    /root/.venvs/mp/bin/python testkit/bench3/profile/run_pi152.py                 # vanilla Pi 1.5.2 (the one the app ships)
    python3 testkit/bench3/profile/analyze.py && python3 testkit/bench3/profile/components.py > components.md

`capture.py` is the fake upstream (chat completions and Responses API, streamed or not; scripted reply: `--calls N` `ls` tool calls, then "done"; it reports usage as bytes/4 because Meshpool shrinks its tool schemas when the reported usage is smaller than what it sent). Options: `--mp-src PATH` (default `/tmp/claude-0/base/app`), `--delay S` (hold replies back so MCP servers finish starting). Findings: `FINDINGS.md`. Raw requests: `captures/<arm>.jsonl`; texts: `dumps/`.
