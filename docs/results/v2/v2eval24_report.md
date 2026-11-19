## MeshPool Advanced Problem-Solving Benchmark V2 (24-task stratified subset, 3 per category)

24 tasks run by every harness (Meshpool V2, Meshpool V1, Codex, OpenCode, Pi, Hermes), DeepSeek V4.1 Flash, one run each, graded by hidden tests or official answers. Tokens are from the shared metering proxy. Runs left out of timing (over 3600s, e.g. across a sleep): 0.

| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | Input tok / task | Output tok / task | Calls / task |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool V2 | 19/24 | 79% (60%–91%) | 0 | $0.0035 | $0.0044 | 42.1 | 80.1 | 186.7 | 105,087 | 2,630 | 13.4 |
| Meshpool V1 | 21/24 | 88% (69%–96%) | 0 | $0.0048 | $0.0054 | 52.4 | 72.1 | 169.2 | 124,752 | 4,034 | 14.0 |
| Codex | 24/24 | 100% (86%–100%) | 0 | $0.0039 | $0.0039 | 24.1 | 41.7 | 72.5 | 113,523 | 1,541 | 6.3 |
| OpenCode | 22/24 | 92% (74%–98%) | 0 | $0.0134 | $0.0147 | 26.9 | 66.2 | 172.1 | 286,702 | 1,410 | 7.3 |
| Pi | 23/24 | 96% (80%–99%) | 0 | $0.0111 | $0.0116 | 32.3 | 61.5 | 71.2 | 256,023 | 1,924 | 6.6 |
| Hermes | 22/24 | 92% (74%–98%) | 0 | $0.0070 | $0.0077 | 36.8 | 78.6 | 77.6 | 139,599 | 2,095 | 7.8 |

Meshpool V2 against each harness on the same tasks (quality: McNemar exact; cost and time: paired bootstrap 95% interval of the ratio, over tasks both solved):

| vs | Solved only by Meshpool V2 | Solved only by them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Meshpool V1 | 3 | 5 | tie (not significant, p=0.73) | cheaper 0.59× (0.42–0.91) | same speed 0.89× (0.58–1.34) |
| Codex | 0 | 5 | tie (not significant, p=0.06) | cheaper 0.75× (0.59–0.94) | same speed 1.23× (0.59–2.52) |
| OpenCode | 1 | 4 | tie (not significant, p=0.38) | cheaper 0.24× (0.17–0.33) | same speed 0.81× (0.45–2.05) |
| Pi | 1 | 5 | tie (not significant, p=0.22) | cheaper 0.25× (0.19–0.33) | same speed 1.19× (0.80–1.66) |
| Hermes | 2 | 5 | tie (not significant, p=0.45) | cheaper 0.43× (0.29–0.62) | same speed 0.85× (0.62–1.36) |

Solved by category:

| Category | Tasks | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|
| algorithmic | 3 | 2 | 3 | 3 | 2 | 3 | 2 |
| ambiguous | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| data | 3 | 2 | 2 | 3 | 3 | 3 | 2 |
| debugging | 3 | 2 | 3 | 3 | 3 | 3 | 3 |
| multi-file | 3 | 2 | 2 | 3 | 2 | 3 | 3 |
| performance | 3 | 3 | 3 | 3 | 3 | 2 | 3 |
| refactoring | 3 | 3 | 2 | 3 | 3 | 3 | 3 |
| test-engineering | 3 | 2 | 3 | 3 | 3 | 3 | 3 |

Cost per attempted task by category ($):

| Category | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|
| algorithmic | 0.0054 | 0.0063 | 0.0038 | 0.0194 | 0.0149 | 0.0103 |
| ambiguous | 0.0015 | 0.0015 | 0.0037 | 0.0073 | 0.0067 | 0.0036 |
| data | 0.0013 | 0.0018 | 0.0039 | 0.0180 | 0.0144 | 0.0059 |
| debugging | 0.0025 | 0.0029 | 0.0033 | 0.0119 | 0.0088 | 0.0062 |
| multi-file | 0.0051 | 0.0057 | 0.0035 | 0.0186 | 0.0118 | 0.0065 |
| performance | 0.0030 | 0.0047 | 0.0050 | 0.0155 | 0.0078 | 0.0131 |
| refactoring | 0.0025 | 0.0031 | 0.0036 | 0.0077 | 0.0148 | 0.0041 |
| test-engineering | 0.0062 | 0.0121 | 0.0045 | 0.0090 | 0.0094 | 0.0067 |

Unsolved tasks per harness:

- Meshpool V2: alg-topo-cycle, data-messy-join, dbg-tz-daily, mf-atomic-transfer, te-money-split
- Meshpool V1: data-mixed-timestamps, mf-config-layers, rf-break-cycle
- Codex: none
- OpenCode: alg-topo-cycle, mf-config-layers
- Pi: perf-regex-redos
- Hermes: alg-k-hop-path, data-mixed-timestamps
