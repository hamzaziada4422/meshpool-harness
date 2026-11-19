## InfiAgent-DABench (12 medium/hard questions, official grader)

11 tasks run by every harness (Meshpool V2, Meshpool V1, Codex, OpenCode, Pi, Hermes), DeepSeek V4.1 Flash, one run each, graded by hidden tests or official answers. Tokens are from the shared metering proxy. Runs left out of timing (over 3600s, e.g. across a sleep): 0.

| Harness | Solved | Rate (95% CI) | Errors | $ / attempted | $ / solved | Median s | Mean s | P90 s | Input tok / task | Output tok / task | Calls / task |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Meshpool V2 | 10/11 | 91% (62%–98%) | 0 | $0.0012 | $0.0013 | 7.1 | 9.0 | 16.2 | 13,251 | 390 | 2.3 |
| Meshpool V1 | 10/11 | 91% (62%–98%) | 0 | $0.0012 | $0.0014 | 8.0 | 11.3 | 20.1 | 15,016 | 411 | 2.5 |
| Codex | 10/11 | 91% (62%–98%) | 0 | $0.0028 | $0.0031 | 11.5 | 23.0 | 63.8 | 58,472 | 422 | 3.5 |
| OpenCode | 10/11 | 91% (62%–98%) | 0 | $0.0134 | $0.0147 | 14.7 | 19.3 | 25.1 | 145,615 | 349 | 4.3 |
| Pi | 10/11 | 91% (62%–98%) | 0 | $0.0117 | $0.0129 | 15.6 | 15.4 | 19.7 | 127,740 | 435 | 3.4 |
| Hermes | 10/11 | 91% (62%–98%) | 0 | $0.0046 | $0.0051 | 21.2 | 39.2 | 83.5 | 80,339 | 584 | 5.2 |

Meshpool V2 against each harness on the same tasks (quality: McNemar exact; cost and time: paired bootstrap 95% interval of the ratio, over tasks both solved):

| vs | Solved only by Meshpool V2 | Solved only by them | Quality | Cost ratio | Time ratio |
|---|---|---|---|---|---|
| Meshpool V1 | 0 | 0 | tie | same cost 0.94× (0.83–1.03) | same speed 0.79× (0.46–1.34) |
| Codex | 0 | 0 | tie | cheaper 0.41× (0.38–0.47) | faster 0.38× (0.23–0.72) |
| OpenCode | 0 | 0 | tie | cheaper 0.09× (0.07–0.11) | faster 0.47× (0.32–0.73) |
| Pi | 0 | 0 | tie | cheaper 0.10× (0.09–0.11) | faster 0.61× (0.45–0.81) |
| Hermes | 0 | 0 | tie | cheaper 0.26× (0.22–0.32) | faster 0.26× (0.19–0.44) |

Solved by difficulty:

| Difficulty | Tasks | Meshpool V2 | Meshpool V1 | Codex | OpenCode | Pi | Hermes |
|---|---|---|---|---|---|---|---|
| hard | 5 | 4 | 4 | 4 | 4 | 4 | 4 |
| medium | 6 | 6 | 6 | 6 | 6 | 6 | 6 |

Unsolved tasks per harness:

- Meshpool V2: dab-137
- Meshpool V1: dab-137
- Codex: dab-137
- OpenCode: dab-137
- Pi: dab-137
- Hermes: dab-137
