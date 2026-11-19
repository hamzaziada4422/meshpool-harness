## Final evaluation (52 tasks): MeshPool 0.0.7 harness vs every harness

52 tasks run by every harness (MeshPool 0.0.7, Meshpool V2, Meshpool V1, Codex, OpenCode, Pi, Hermes), DeepSeek V4.1 Flash, one run each, graded by hidden tests or official answers. Tokens are from the shared metering proxy. Runs left out of timing (over 3600s, e.g. across a sleep): 0.

| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | Input tok / task | Output tok / task | Calls / task |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MeshPool 0.0.7 | 46/52 | 88% (77%–95%) | 0 | $0.0069 | $0.0078 | 23.3 | 83.1 | 214.6 | 174,499 | 7,473 | 13.5 |
| Meshpool V2 | 47/52 | 90% (79%–96%) | 0 | $0.0054 | $0.0060 | 26.2 | 65.3 | 99.4 | 192,997 | 3,959 | 16.0 |
| Meshpool V1 | 47/52 | 90% (79%–96%) | 0 | $0.0053 | $0.0058 | 33.5 | 62.9 | 132.9 | 203,527 | 4,522 | 15.5 |
| Codex | 50/52 | 96% (87%–99%) | 0 | $0.0038 | $0.0040 | 22.2 | 36.8 | 66.5 | 116,283 | 2,236 | 8.2 |
| OpenCode | 48/52 | 92% (82%–97%) | 0 | $0.0047 | $0.0051 | 24.6 | 38.3 | 40.4 | 141,186 | 1,496 | 7.1 |
| Pi | 48/52 | 92% (82%–97%) | 0 | $0.0052 | $0.0057 | 23.1 | 59.2 | 73.2 | 163,010 | 2,667 | 8.3 |
| Hermes | 49/52 | 94% (84%–98%) | 1 | $0.0049 | $0.0052 | 46.6 | 70.7 | 98.3 | 154,427 | 2,448 | 8.6 |

MeshPool 0.0.7 against each harness on the same tasks (quality: McNemar exact; cost and time: paired bootstrap 95% interval of the ratio, over tasks both solved):

| vs | Solved only by MeshPool 0.0.7 | Solved only by them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Meshpool V2 | 3 | 4 | tie (not significant, p=1.00) | same cost 0.89× (0.67–1.16) | same speed 1.18× (0.78–1.62) |
| Meshpool V1 | 3 | 4 | tie (not significant, p=1.00) | same cost 1.48× (0.75–2.66) | same speed 1.24× (0.77–1.90) |
| Codex | 2 | 6 | tie (not significant, p=0.29) | same cost 1.41× (0.77–2.39) | slower 2.02× (1.29–2.95) |
| OpenCode | 4 | 6 | tie (not significant, p=0.75) | same cost 1.06× (0.56–1.85) | slower 2.27× (1.44–3.24) |
| Pi | 3 | 5 | tie (not significant, p=0.73) | same cost 0.97× (0.53–1.71) | slower 1.65× (1.12–2.50) |
| Hermes | 3 | 6 | tie (not significant, p=0.51) | same cost 0.98× (0.53–1.77) | same speed 0.91× (0.62–1.32) |

Solved by category:

| Category | Tasks | MeshPool 0.0.7 | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|---|
| algorithmic | 8 | 7 | 7 | 8 | 8 | 8 | 7 | 7 |
| ambiguous | 5 | 5 | 5 | 5 | 5 | 4 | 5 | 5 |
| data | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| debugging | 8 | 8 | 8 | 8 | 8 | 7 | 7 | 8 |
| multi-file | 8 | 6 | 7 | 5 | 7 | 8 | 6 | 6 |
| performance | 5 | 5 | 3 | 4 | 4 | 4 | 5 | 5 |
| refactoring | 6 | 5 | 6 | 5 | 6 | 6 | 6 | 6 |
| test-engineering | 6 | 4 | 5 | 6 | 6 | 5 | 6 | 6 |

Cost per attempted task by category ($):

| Category | MeshPool 0.0.7 | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|
| algorithmic | 0.0033 | 0.0067 | 0.0066 | 0.0055 | 0.0053 | 0.0061 | 0.0056 |
| ambiguous | 0.0012 | 0.0015 | 0.0015 | 0.0023 | 0.0036 | 0.0035 | 0.0034 |
| data | 0.0016 | 0.0016 | 0.0014 | 0.0026 | 0.0041 | 0.0042 | 0.0035 |
| debugging | 0.0020 | 0.0019 | 0.0022 | 0.0030 | 0.0040 | 0.0038 | 0.0040 |
| multi-file | 0.0043 | 0.0046 | 0.0063 | 0.0035 | 0.0047 | 0.0048 | 0.0040 |
| performance | 0.0029 | 0.0121 | 0.0046 | 0.0052 | 0.0049 | 0.0087 | 0.0071 |
| refactoring | 0.0028 | 0.0024 | 0.0029 | 0.0031 | 0.0042 | 0.0044 | 0.0045 |
| test-engineering | 0.0391 | 0.0137 | 0.0159 | 0.0050 | 0.0067 | 0.0071 | 0.0078 |

Unsolved tasks per harness:

- MeshPool 0.0.7: alg-merge-accounts, mf-i18n-plurals, mf-job-hooks, rf-sync-async-client, te-csv-line, te-semver
- Meshpool V2: alg-merge-accounts, mf-job-hooks, perf-dag-paths, perf-regex-redos, te-bankers-round
- Meshpool V1: mf-config-layers, mf-job-hooks, mf-js-recipe-units, perf-config-reload, rf-sync-async-client
- Codex: mf-config-layers, perf-regex-redos
- OpenCode: amb-invoice-cents, dbg-stale-cache, perf-config-reload, te-token-bucket
- Pi: alg-expr, dbg-pagination-sync, mf-job-hooks, mf-rename-module
- Hermes: alg-meeting-slot, mf-config-layers, mf-rate-limit
