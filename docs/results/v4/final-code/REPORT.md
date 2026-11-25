# Benchmark report

Model `gpt-6-luna`, 2026-11-23T22:33:03+00:00, codex `codex-cli 0.171.0`, pi `0.73.1`, Meshpool source `/tmp/claude-0/final/app` @ `ab17382102`. Every model call of every arm went through one metering proxy; cost = uncached input + cached input + output at the price table in `prices.json` (unknown model: no cost).

## Per task and arm

| task | arm | pass | cost mean (min-max) USD | calls | input | cached | output | reasoning | secs mean (min-max) |
|---|---|---|---|---|---|---|---|---|---|
| py_forth | codex | 1/1 | 0.0063 (0.0063-0.0063) | 9.0 | 145405 | 123831 | 5737 | - | 51.3 (51.3-51.3) |
| py_forth | pi | 1/1 | 0.0025 (0.0025-0.0025) | 7.0 | 33319 | 25475 | 2858 | - | 33.5 (33.5-33.5) |
| py_forth | mp-code | 1/1 | 0.0054 (0.0054-0.0054) | 14.0 | 123547 | 104778 | 4990 | - | 53.2 (53.2-53.2) |
| py_forth | mp-on-pi | 1/1 | 0.0035 (0.0035-0.0035) | 8.0 | 82718 | 66749 | 2505 | - | 28.1 (28.1-28.1) |
| py_forth | mp-on-codex | 1/1 | 0.0049 (0.0049-0.0049) | 10.0 | 143276 | 124670 | 3650 | - | 43.1 (43.1-43.1) |
| py_zipper | codex | 1/1 | 0.0044 (0.0044-0.0044) | 10.0 | 145803 | 129457 | 2982 | - | 32.6 (32.6-32.6) |
| py_zipper | pi | 1/1 | 0.0015 (0.0015-0.0015) | 7.0 | 33012 | 28231 | 1380 | - | 16.2 (16.2-16.2) |
| py_zipper | mp-code | 1/1 | 0.0027 (0.0027-0.0027) | 8.0 | 68262 | 56054 | 1877 | - | 21.1 (21.1-21.1) |
| py_zipper | mp-on-pi | 1/1 | 0.0026 (0.0026-0.0026) | 7.0 | 59998 | 46871 | 1711 | - | 19.1 (19.1-19.1) |
| py_zipper | mp-on-codex | 1/1 | 0.0029 (0.0029-0.0029) | 6.0 | 62762 | 48071 | 1828 | - | 20.1 (20.1-20.1) |
| rs_react | codex | 0/1 | 0.0082 (0.0082-0.0082) | 12.0 | 202977 | 181856 | 8483 | - | 77.7 (77.7-77.7) |
| rs_react | pi | 1/1 | 0.0037 (0.0037-0.0037) | 9.0 | 59885 | 49745 | 4285 | - | 45.0 (45.0-45.0) |
| rs_react | mp-code | 1/1 | 0.0072 (0.0072-0.0072) | 18.0 | 211227 | 186815 | 5687 | - | 60.2 (60.2-60.2) |
| rs_react | mp-on-pi | 1/1 | 0.0062 (0.0062-0.0062) | 12.0 | 190990 | 170397 | 4910 | - | 50.2 (50.2-50.2) |
| rs_react | mp-on-codex | 0/1 | 0.0073 (0.0073-0.0073) | 11.0 | 189977 | 165799 | 6515 | - | 56.2 (56.2-56.2) |

## Meshpool arms relative to Codex and Pi (mean over reps; below 1.00 is better)

| task | arm vs reference | cost ratio | time ratio |
|---|---|---|---|
| py_forth | mp-code/codex | 0.87 | 1.04 |
| py_forth | mp-code/pi | 2.20 | 1.59 |
| py_forth | mp-on-pi/codex | 0.56 | 0.55 |
| py_forth | mp-on-pi/pi | 1.42 | 0.84 |
| py_forth | mp-on-codex/codex | 0.79 | 0.84 |
| py_forth | mp-on-codex/pi | 2.00 | 1.29 |
| py_zipper | mp-code/codex | 0.62 | 0.65 |
| py_zipper | mp-code/pi | 1.88 | 1.30 |
| py_zipper | mp-on-pi/codex | 0.60 | 0.58 |
| py_zipper | mp-on-pi/pi | 1.82 | 1.18 |
| py_zipper | mp-on-codex/codex | 0.65 | 0.62 |
| py_zipper | mp-on-codex/pi | 1.97 | 1.24 |
| rs_react | mp-code/codex | 0.88 | 0.77 |
| rs_react | mp-code/pi | 1.96 | 1.34 |
| rs_react | mp-on-pi/codex | 0.76 | 0.65 |
| rs_react | mp-on-pi/pi | 1.70 | 1.11 |
| rs_react | mp-on-codex/codex | 0.90 | 0.72 |
| rs_react | mp-on-codex/pi | 2.01 | 1.25 |

## Totals per arm over all tasks

| arm | pass | runs | cost sum USD | cost per run | calls | seconds sum | secs per run |
|---|---|---|---|---|---|---|---|
| codex | 2/3 | 3 | 0.0189 | 0.0063 | 31 | 162 | 53.9 |
| pi | 3/3 | 3 | 0.0076 | 0.0025 | 23 | 95 | 31.6 |
| mp-code | 3/3 | 3 | 0.0153 | 0.0051 | 40 | 134 | 44.8 |
| mp-on-pi | 3/3 | 3 | 0.0124 | 0.0041 | 27 | 97 | 32.4 |
| mp-on-codex | 2/3 | 3 | 0.0151 | 0.0050 | 27 | 119 | 39.8 |

## Where Meshpool loses

- py_forth: `mp-code` vs `codex`: time 1.04x
- py_forth: `mp-code` vs `pi`: cost 2.20x, time 1.59x
- py_forth: `mp-on-pi` vs `pi`: cost 1.42x
- py_forth: `mp-on-codex` vs `pi`: cost 2.00x, time 1.29x
- py_zipper: `mp-code` vs `pi`: cost 1.88x, time 1.30x
- py_zipper: `mp-on-pi` vs `pi`: cost 1.82x, time 1.18x
- py_zipper: `mp-on-codex` vs `pi`: cost 1.97x, time 1.24x
- rs_react: `mp-code` vs `pi`: cost 1.96x, time 1.34x
- rs_react: `mp-on-pi` vs `pi`: cost 1.70x, time 1.11x
- rs_react: `mp-on-codex` vs `pi`: cost 2.01x, time 1.25x, pass 0/1 vs 1/1
