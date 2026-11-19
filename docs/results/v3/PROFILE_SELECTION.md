# Profile selection (24 tasks run by every profile)

Default: **lean-stop**. Rule per group: most solved, then lowest total cost, then lowest median time; a group gets its own profile only if it solves more than the default or ties and costs 15%+ less. Groups with fewer than 3 tasks are not listed.

Cell: solved / total cost USD / median seconds.

| group | n | full-noplan | full-noplan-stop | lean-stop | chosen | why |
|---|---|---|---|---|---|---|
| code.build | 9 | 7 / 0.0830 / 82.7 | 7 / 0.0738 / 77.3 | 8 / 0.0680 / 95.1 | lean-stop (default) | default is the best here |
| code.build.algorithm | 3 | 1 / 0.0388 / 164.3 | 2 / 0.0316 / 166.3 | 2 / 0.0512 / 259.7 | full-noplan-stop (own) | ties on solved (2) and costs 38% less |
| code.build.algorithm.python | 3 | 1 / 0.0388 / 164.3 | 2 / 0.0316 / 166.3 | 2 / 0.0512 / 259.7 | full-noplan-stop (own) | ties on solved (2) and costs 38% less |
| code.build.python | 9 | 7 / 0.0830 / 82.7 | 7 / 0.0738 / 77.3 | 8 / 0.0680 / 95.1 | lean-stop (default) | default is the best here |
| code.debug | 5 | 5 / 0.0291 / 65.5 | 5 / 0.0264 / 55.8 | 5 / 0.0138 / 42.7 | lean-stop (default) | default is the best here |
| code.debug.python | 4 | 4 / 0.0218 / 172.35 | 4 / 0.0209 / 154.05 | 4 / 0.0094 / 31.1 | lean-stop (default) | default is the best here |
| code.refactor | 3 | 2 / 0.0209 / 44.3 | 2 / 0.0215 / 52.7 | 2 / 0.0126 / 51.4 | lean-stop (default) | default is the best here |
| code.refactor.python | 3 | 2 / 0.0209 / 44.3 | 2 / 0.0215 / 52.7 | 2 / 0.0126 / 51.4 | lean-stop (default) | default is the best here |
| code.test | 3 | 3 / 0.1164 / 206.1 | 2 / 0.1078 / 212.2 | 2 / 0.1196 / 340.1 | full-noplan (own) | solves 3 vs 2 for the default |
| code.test.python | 3 | 3 / 0.1164 / 206.1 | 2 / 0.1078 / 212.2 | 2 / 0.1196 / 340.1 | full-noplan (own) | solves 3 vs 2 for the default |
| data | 3 | 2 / 0.0109 / 28.3 | 2 / 0.0223 / 76.8 | 1 / 0.0077 / 25.5 | full-noplan (own) | solves 2 vs 1 for the default |
