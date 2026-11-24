# Office re-run on 8dc9f501 (compaction-loop fix)

deepseek-v4.1-flash, one run per arm. results.jsonl has two rows for xlsx_edit/mp-work: the first (0 metered calls,
no chat session found) is unreliable; the second (re-run) is the one used. Meshpool on Codex and Meshpool on Pi rows
show secs = 900 because the app kept reporting the chat as working after the turn ended (a bug reported to its
owner); real turn times come from each chat's task_receipt:

| arm | passed | $ per task | calls (7 tasks) | s per task (receipts) |
|---|---|---|---|---|
| mp-cli | 6/7 | 0.0355 | 346 | 108 |
| mp-on-codex | 4/7 | 0.0301 | 262 | 103 |
| mp-on-pi | 5/7 | 0.0491 | 253 | 139 |
| mp-work | 6/7 | 0.0933 | 612 | 194 |
