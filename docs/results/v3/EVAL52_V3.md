## V3 evaluation (52 tasks, 7 harnesses)

52 tasks run by every harness (Meshpool V3, Meshpool V2, Meshpool V1, Codex, OpenCode, Pi, Hermes), DeepSeek V4.1 Flash, one run each, graded by hidden tests or official answers. Tokens are from the shared metering proxy. Runs left out of timing (over 3600s, e.g. across a sleep): 0.

| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | Input tok / task | Output tok / task | Calls / task |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool V3 | 47/52 | 90% (79%–96%) | 0 | $0.0107 | $0.0118 | 50.6 | 117.0 | 351.0 | 462,836 | 11,352 | 21.0 |
| Meshpool V2 | 47/52 | 90% (79%–96%) | 0 | $0.0054 | $0.0060 | 26.2 | 65.3 | 99.4 | 192,997 | 3,959 | 16.0 |
| Meshpool V1 | 47/52 | 90% (79%–96%) | 0 | $0.0053 | $0.0058 | 33.5 | 62.9 | 132.9 | 203,527 | 4,522 | 15.5 |
| Codex | 50/52 | 96% (87%–99%) | 0 | $0.0038 | $0.0040 | 22.2 | 36.8 | 66.5 | 116,283 | 2,236 | 8.2 |
| OpenCode | 48/52 | 92% (82%–97%) | 0 | $0.0047 | $0.0051 | 24.6 | 38.3 | 40.4 | 141,186 | 1,496 | 7.1 |
| Pi | 48/52 | 92% (82%–97%) | 0 | $0.0052 | $0.0057 | 23.1 | 59.2 | 73.2 | 163,010 | 2,667 | 8.3 |
| Hermes | 49/52 | 94% (84%–98%) | 1 | $0.0049 | $0.0052 | 46.6 | 70.7 | 98.3 | 154,427 | 2,448 | 8.6 |

Meshpool V3 against each harness on the same tasks (quality: McNemar exact; cost and time: paired bootstrap 95% interval of the ratio, over tasks both solved):

| vs | Solved only by Meshpool V3 | Solved only by them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Meshpool V2 | 4 | 4 | tie | costlier 2.54× (1.38–4.17) | slower 2.29× (1.48–3.50) |
| Meshpool V1 | 3 | 3 | tie | costlier 2.36× (1.32–4.39) | slower 1.78× (1.22–2.83) |
| Codex | 2 | 5 | tie (not significant, p=0.45) | costlier 3.15× (1.70–5.07) | slower 3.54× (2.44–5.08) |
| OpenCode | 3 | 4 | tie (not significant, p=1.00) | costlier 2.50× (1.33–3.99) | slower 4.07× (2.76–5.64) |
| Pi | 3 | 4 | tie (not significant, p=1.00) | costlier 2.20× (1.11–3.51) | slower 2.80× (1.72–4.59) |
| Hermes | 2 | 4 | tie (not significant, p=0.69) | costlier 2.26× (1.22–3.64) | slower 1.49× (1.00–2.16) |

Solved by category:

| Category | Tasks | Meshpool V3 | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|---|
| algorithmic | 8 | 7 | 7 | 8 | 8 | 8 | 7 | 7 |
| ambiguous | 5 | 4 | 5 | 5 | 5 | 4 | 5 | 5 |
| data | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| debugging | 8 | 8 | 8 | 8 | 8 | 7 | 7 | 8 |
| multi-file | 8 | 5 | 7 | 5 | 7 | 8 | 6 | 6 |
| performance | 5 | 5 | 3 | 4 | 4 | 4 | 5 | 5 |
| refactoring | 6 | 6 | 6 | 5 | 6 | 6 | 6 | 6 |
| test-engineering | 6 | 6 | 5 | 6 | 6 | 5 | 6 | 6 |

Cost per attempted task by category ($):

| Category | Meshpool V3 | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|
| algorithmic | 0.0099 | 0.0067 | 0.0066 | 0.0055 | 0.0053 | 0.0061 | 0.0056 |
| ambiguous | 0.0021 | 0.0015 | 0.0015 | 0.0023 | 0.0036 | 0.0035 | 0.0034 |
| data | 0.0016 | 0.0016 | 0.0014 | 0.0026 | 0.0041 | 0.0042 | 0.0035 |
| debugging | 0.0033 | 0.0019 | 0.0022 | 0.0030 | 0.0040 | 0.0038 | 0.0040 |
| multi-file | 0.0056 | 0.0046 | 0.0063 | 0.0035 | 0.0047 | 0.0048 | 0.0040 |
| performance | 0.0034 | 0.0121 | 0.0046 | 0.0052 | 0.0049 | 0.0087 | 0.0071 |
| refactoring | 0.0268 | 0.0024 | 0.0029 | 0.0031 | 0.0042 | 0.0044 | 0.0045 |
| test-engineering | 0.0348 | 0.0137 | 0.0159 | 0.0050 | 0.0067 | 0.0071 | 0.0078 |

Unsolved tasks per harness:

- Meshpool V3: alg-line-diff, amb-invoice-cents, mf-job-hooks, mf-js-recipe-units, mf-rate-limit
- Meshpool V2: alg-merge-accounts, mf-job-hooks, perf-dag-paths, perf-regex-redos, te-bankers-round
- Meshpool V1: mf-config-layers, mf-job-hooks, mf-js-recipe-units, perf-config-reload, rf-sync-async-client
- Codex: mf-config-layers, perf-regex-redos
- OpenCode: amb-invoice-cents, dbg-stale-cache, perf-config-reload, te-token-bucket
- Pi: alg-expr, dbg-pagination-sync, mf-job-hooks, mf-rename-module
- Hermes: alg-meeting-slot, mf-config-layers, mf-rate-limit
