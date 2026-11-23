# Benchmark report

Model `deepseek-v4.1-flash`, 2026-11-23T20:44:40+00:00, codex `codex-cli 0.171.0`, pi `0.73.1`, Meshpool source `/tmp/claude-0/base/app` @ `370427e372`. Every model call of every arm went through one metering proxy; cost = uncached input + cached input + output at the price table in `prices.json` (unknown model: no cost).

## Per task and arm

| task | arm | pass | cost mean (min-max) USD | calls | input | cached | output | reasoning | secs mean (min-max) |
|---|---|---|---|---|---|---|---|---|---|
| docx_mailmerge | codex | 1/1 | 0.0061 (0.0061-0.0061) | 7.0 | 72475 | 60416 | 1310 | - | 12.8 (12.8-12.8) |
| docx_mailmerge | pi | 1/1 | 0.0036 (0.0036-0.0036) | 7.0 | 20775 | 16896 | 1660 | - | 24.5 (24.5-24.5) |
| docx_mailmerge | mp-work | 1/1 | 0.0185 (0.0185-0.0185) | 23.0 | 309462 | 280576 | 5427 | - | 49.2 (49.2-49.2) |
| docx_mailmerge | mp-on-pi | 1/1 | 0.0145 (0.0145-0.0145) | 9.0 | 256260 | 227328 | 2607 | - | 20.1 (20.1-20.1) |
| docx_mailmerge | mp-on-codex | 1/1 | 0.0127 (0.0127-0.0127) | 8.0 | 140350 | 110336 | 1551 | - | 16.1 (16.1-16.1) |
| docx_mailmerge | mp-cli | 1/1 | 0.0198 (0.0198-0.0198) | 26.0 | 351468 | 321280 | 5863 | - | 54.0 (54.0-54.0) |
| docx_tracked | codex | 1/1 | 0.0105 (0.0105-0.0105) | 14.0 | 174766 | 160000 | 3449 | - | 45.5 (45.5-45.5) |
| docx_tracked | pi | 1/1 | 0.0074 (0.0074-0.0074) | 13.0 | 68128 | 59648 | 3204 | - | 37.8 (37.8-37.8) |
| docx_tracked | mp-work | 1/1 | 0.0206 (0.0206-0.0206) | 27.0 | 279558 | 241920 | 4982 | - | 53.2 (53.2-53.2) |
| docx_tracked | mp-on-pi | 1/1 | 0.0111 (0.0111-0.0111) | 6.0 | 159979 | 132608 | 932 | - | 20.1 (20.1-20.1) |
| docx_tracked | mp-on-codex | 1/1 | 0.0150 (0.0150-0.0150) | 13.0 | 257073 | 232192 | 4017 | - | 37.1 (37.1-37.1) |
| docx_tracked | mp-cli | 1/1 | 0.0154 (0.0154-0.0154) | 25.0 | 258351 | 236800 | 5120 | - | 59.5 (59.5-59.5) |
| invoices_fx | codex | 1/1 | 0.0062 (0.0062-0.0062) | 7.0 | 70022 | 58112 | 1449 | - | 15.9 (15.9-15.9) |
| invoices_fx | pi | 1/1 | 0.0032 (0.0032-0.0032) | 6.0 | 20458 | 16640 | 1375 | - | 17.0 (17.0-17.0) |
| invoices_fx | mp-work | 1/1 | 0.0347 (0.0347-0.0347) | 41.0 | 465764 | 413184 | 11053 | - | 126.3 (126.3-126.3) |
| invoices_fx | mp-on-pi | 1/1 | 0.0318 (0.0318-0.0318) | 33.0 | 479481 | 419840 | 7100 | - | 103.3 (103.3-103.3) |
| invoices_fx | mp-on-codex | 0/1 | 0.0338 (0.0338-0.0338) | 36.0 | 469068 | 410368 | 8849 | - | 102.3 (102.3-102.3) |
| invoices_fx | mp-cli | 1/1 | 0.0575 (0.0575-0.0575) | 73.0 | 1483390 | 1419264 | 20432 | - | 192.1 (192.1-192.1) |
| pdf_report | codex | 1/1 | 0.0087 (0.0087-0.0087) | 11.0 | 140587 | 130560 | 3429 | - | 35.6 (35.6-35.6) |
| pdf_report | pi | 1/1 | 0.0055 (0.0055-0.0055) | 10.0 | 44496 | 40448 | 2927 | - | 39.8 (39.8-39.8) |
| pdf_report | mp-work | 0/1 | 0.1493 (0.1493-0.1493) | 130.0 | 900410 | 715776 | 63353 | - | 489.2 (489.2-489.2) |
| pdf_report | mp-on-pi | 0/1 | 0.0553 (0.0553-0.0553) | 23.0 | 704098 | 570624 | 5674 | - | 85.3 (85.3-85.3) |
| pdf_report | mp-on-codex | 1/1 | 0.0180 (0.0180-0.0180) | 11.0 | 223060 | 192512 | 5026 | - | 42.1 (42.1-42.1) |
| pdf_report | mp-cli | 0/1 | 0.1651 (0.1651-0.1651) | 117.0 | 918438 | 730112 | 74314 | - | 505.7 (505.7-505.7) |
| pptx_chart | codex | 1/1 | 0.0229 (0.0229-0.0229) | 25.0 | 464096 | 445184 | 10424 | - | 73.0 (73.0-73.0) |
| pptx_chart | pi | 1/1 | 0.0124 (0.0124-0.0124) | 12.0 | 82818 | 73728 | 6768 | - | 52.5 (52.5-52.5) |
| pptx_chart | mp-work | 1/1 | 0.0783 (0.0783-0.0783) | 47.0 | 417608 | 309504 | 30707 | - | 213.5 (213.5-213.5) |
| pptx_chart | mp-on-pi | 0/1 | 0.0980 (0.0980-0.0980) | 40.0 | 267313 | 129792 | 39212 | - | 197.5 (197.5-197.5) |
| pptx_chart | mp-on-codex | 0/1 | 0.0936 (0.0936-0.0936) | 44.0 | 299160 | 179712 | 40172 | - | 208.5 (208.5-208.5) |
| pptx_chart | mp-cli | 0/1 | 0.0349 (0.0349-0.0349) | 21.0 | 138882 | 75520 | 10201 | - | 91.9 (91.9-91.9) |
| xlsx_budget | codex | 1/1 | 0.0088 (0.0088-0.0088) | 10.0 | 113534 | 101632 | 3187 | - | 24.7 (24.7-24.7) |
| xlsx_budget | pi | 1/1 | 0.0052 (0.0052-0.0052) | 8.0 | 29086 | 25088 | 2809 | - | 29.6 (29.6-29.6) |
| xlsx_budget | mp-work | 0/1 | 0.0347 (0.0347-0.0347) | 42.0 | 488615 | 436480 | 11039 | - | 109.3 (109.3-109.3) |
| xlsx_budget | mp-on-pi | 0/1 | 0.0409 (0.0409-0.0409) | 33.0 | 649935 | 575488 | 9462 | - | 122.3 (122.3-122.3) |
| xlsx_budget | mp-on-codex | 1/1 | 0.0368 (0.0368-0.0368) | 57.0 | 795954 | 744192 | 11239 | - | 144.4 (144.4-144.4) |
| xlsx_budget | mp-cli | 0/1 | 0.0717 (0.0717-0.0717) | 72.0 | 1234338 | 1151232 | 27821 | - | 261.6 (261.6-261.6) |
| xlsx_edit | codex | 1/1 | 0.0082 (0.0082-0.0082) | 10.0 | 115889 | 101632 | 2131 | - | 27.1 (27.1-27.1) |
| xlsx_edit | pi | 1/1 | 0.0082 (0.0082-0.0082) | 12.0 | 73456 | 66304 | 4080 | - | 44.9 (44.9-44.9) |
| xlsx_edit | mp-work | 1/1 | 0.0578 (0.0578-0.0578) | 68.0 | 1215187 | 1123072 | 15150 | - | 162.8 (162.8-162.8) |
| xlsx_edit | mp-on-pi | 1/1 | 0.0264 (0.0264-0.0264) | 21.0 | 785889 | 744448 | 5951 | - | 53.2 (53.2-53.2) |
| xlsx_edit | mp-on-codex | 1/1 | 0.0223 (0.0223-0.0223) | 20.0 | 516300 | 482304 | 5976 | - | 43.1 (43.1-43.1) |
| xlsx_edit | mp-cli | 1/1 | 0.0257 (0.0257-0.0257) | 26.0 | 446034 | 406272 | 7499 | - | 75.0 (75.0-75.0) |

## Meshpool arms relative to Codex and Pi (mean over reps; below 1.00 is better)

| task | arm vs reference | cost ratio | time ratio |
|---|---|---|---|
| docx_mailmerge | mp-work/codex | 3.04 | 3.86 |
| docx_mailmerge | mp-work/pi | 5.18 | 2.01 |
| docx_mailmerge | mp-on-pi/codex | 2.37 | 1.57 |
| docx_mailmerge | mp-on-pi/pi | 4.04 | 0.82 |
| docx_mailmerge | mp-on-codex/codex | 2.08 | 1.26 |
| docx_mailmerge | mp-on-codex/pi | 3.54 | 0.66 |
| docx_mailmerge | mp-cli/codex | 3.25 | 4.24 |
| docx_mailmerge | mp-cli/pi | 5.53 | 2.21 |
| docx_tracked | mp-work/codex | 1.96 | 1.17 |
| docx_tracked | mp-work/pi | 2.77 | 1.41 |
| docx_tracked | mp-on-pi/codex | 1.06 | 0.44 |
| docx_tracked | mp-on-pi/pi | 1.50 | 0.53 |
| docx_tracked | mp-on-codex/codex | 1.44 | 0.82 |
| docx_tracked | mp-on-codex/pi | 2.03 | 0.98 |
| docx_tracked | mp-cli/codex | 1.47 | 1.31 |
| docx_tracked | mp-cli/pi | 2.08 | 1.57 |
| invoices_fx | mp-work/codex | 5.57 | 7.93 |
| invoices_fx | mp-work/pi | 10.89 | 7.41 |
| invoices_fx | mp-on-pi/codex | 5.11 | 6.48 |
| invoices_fx | mp-on-pi/pi | 9.99 | 6.06 |
| invoices_fx | mp-on-codex/codex | 5.42 | 6.42 |
| invoices_fx | mp-on-codex/pi | 10.60 | 6.00 |
| invoices_fx | mp-cli/codex | 9.23 | 12.05 |
| invoices_fx | mp-cli/pi | 18.05 | 11.28 |
| pdf_report | mp-work/codex | 17.16 | 13.74 |
| pdf_report | mp-work/pi | 27.31 | 12.29 |
| pdf_report | mp-on-pi/codex | 6.36 | 2.40 |
| pdf_report | mp-on-pi/pi | 10.12 | 2.14 |
| pdf_report | mp-on-codex/codex | 2.07 | 1.18 |
| pdf_report | mp-on-codex/pi | 3.29 | 1.06 |
| pdf_report | mp-cli/codex | 18.98 | 14.21 |
| pdf_report | mp-cli/pi | 30.20 | 12.70 |
| pptx_chart | mp-work/codex | 3.41 | 2.92 |
| pptx_chart | mp-work/pi | 6.30 | 4.07 |
| pptx_chart | mp-on-pi/codex | 4.27 | 2.71 |
| pptx_chart | mp-on-pi/pi | 7.89 | 3.76 |
| pptx_chart | mp-on-codex/codex | 4.08 | 2.86 |
| pptx_chart | mp-on-codex/pi | 7.54 | 3.97 |
| pptx_chart | mp-cli/codex | 1.52 | 1.26 |
| pptx_chart | mp-cli/pi | 2.81 | 1.75 |
| xlsx_budget | mp-work/codex | 3.94 | 4.43 |
| xlsx_budget | mp-work/pi | 6.67 | 3.70 |
| xlsx_budget | mp-on-pi/codex | 4.64 | 4.96 |
| xlsx_budget | mp-on-pi/pi | 7.87 | 4.14 |
| xlsx_budget | mp-on-codex/codex | 4.18 | 5.85 |
| xlsx_budget | mp-on-codex/pi | 7.09 | 4.88 |
| xlsx_budget | mp-cli/codex | 8.15 | 10.61 |
| xlsx_budget | mp-cli/pi | 13.82 | 8.85 |
| xlsx_edit | mp-work/codex | 7.06 | 6.01 |
| xlsx_edit | mp-work/pi | 7.06 | 3.63 |
| xlsx_edit | mp-on-pi/codex | 3.23 | 1.97 |
| xlsx_edit | mp-on-pi/pi | 3.23 | 1.19 |
| xlsx_edit | mp-on-codex/codex | 2.72 | 1.59 |
| xlsx_edit | mp-on-codex/pi | 2.72 | 0.96 |
| xlsx_edit | mp-cli/codex | 3.14 | 2.77 |
| xlsx_edit | mp-cli/pi | 3.14 | 1.67 |

## Totals per arm over all tasks

| arm | pass | runs | cost sum USD | cost per run | calls | seconds sum | secs per run |
|---|---|---|---|---|---|---|---|
| codex | 7/7 | 7 | 0.0714 | 0.0102 | 84 | 235 | 33.5 |
| pi | 7/7 | 7 | 0.0455 | 0.0065 | 68 | 246 | 35.2 |
| mp-work | 5/7 | 7 | 0.3938 | 0.0563 | 378 | 1204 | 171.9 |
| mp-on-pi | 4/7 | 7 | 0.2781 | 0.0397 | 165 | 602 | 86.0 |
| mp-on-codex | 5/7 | 7 | 0.2322 | 0.0332 | 189 | 594 | 84.8 |
| mp-cli | 4/7 | 7 | 0.3901 | 0.0557 | 360 | 1240 | 177.1 |

## Where Meshpool loses

- docx_mailmerge: `mp-work` vs `codex`: cost 3.04x, time 3.86x
- docx_mailmerge: `mp-work` vs `pi`: cost 5.18x, time 2.01x
- docx_mailmerge: `mp-on-pi` vs `codex`: cost 2.37x, time 1.57x
- docx_mailmerge: `mp-on-pi` vs `pi`: cost 4.04x
- docx_mailmerge: `mp-on-codex` vs `codex`: cost 2.08x, time 1.26x
- docx_mailmerge: `mp-on-codex` vs `pi`: cost 3.54x
- docx_mailmerge: `mp-cli` vs `codex`: cost 3.25x, time 4.24x
- docx_mailmerge: `mp-cli` vs `pi`: cost 5.53x, time 2.21x
- docx_tracked: `mp-work` vs `codex`: cost 1.96x, time 1.17x
- docx_tracked: `mp-work` vs `pi`: cost 2.77x, time 1.41x
- docx_tracked: `mp-on-pi` vs `codex`: cost 1.06x
- docx_tracked: `mp-on-pi` vs `pi`: cost 1.50x
- docx_tracked: `mp-on-codex` vs `codex`: cost 1.44x
- docx_tracked: `mp-on-codex` vs `pi`: cost 2.03x
- docx_tracked: `mp-cli` vs `codex`: cost 1.47x, time 1.31x
- docx_tracked: `mp-cli` vs `pi`: cost 2.08x, time 1.57x
- invoices_fx: `mp-work` vs `codex`: cost 5.57x, time 7.93x
- invoices_fx: `mp-work` vs `pi`: cost 10.89x, time 7.41x
- invoices_fx: `mp-on-pi` vs `codex`: cost 5.11x, time 6.48x
- invoices_fx: `mp-on-pi` vs `pi`: cost 9.99x, time 6.06x
- invoices_fx: `mp-on-codex` vs `codex`: cost 5.42x, time 6.42x, pass 0/1 vs 1/1
- invoices_fx: `mp-on-codex` vs `pi`: cost 10.60x, time 6.00x, pass 0/1 vs 1/1
- invoices_fx: `mp-cli` vs `codex`: cost 9.23x, time 12.05x
- invoices_fx: `mp-cli` vs `pi`: cost 18.05x, time 11.28x
- pdf_report: `mp-work` vs `codex`: cost 17.16x, time 13.74x, pass 0/1 vs 1/1
- pdf_report: `mp-work` vs `pi`: cost 27.31x, time 12.29x, pass 0/1 vs 1/1
- pdf_report: `mp-on-pi` vs `codex`: cost 6.36x, time 2.40x, pass 0/1 vs 1/1
- pdf_report: `mp-on-pi` vs `pi`: cost 10.12x, time 2.14x, pass 0/1 vs 1/1
- pdf_report: `mp-on-codex` vs `codex`: cost 2.07x, time 1.18x
- pdf_report: `mp-on-codex` vs `pi`: cost 3.29x, time 1.06x
- pdf_report: `mp-cli` vs `codex`: cost 18.98x, time 14.21x, pass 0/1 vs 1/1
- pdf_report: `mp-cli` vs `pi`: cost 30.20x, time 12.70x, pass 0/1 vs 1/1
- pptx_chart: `mp-work` vs `codex`: cost 3.41x, time 2.92x
- pptx_chart: `mp-work` vs `pi`: cost 6.30x, time 4.07x
- pptx_chart: `mp-on-pi` vs `codex`: cost 4.27x, time 2.71x, pass 0/1 vs 1/1
- pptx_chart: `mp-on-pi` vs `pi`: cost 7.89x, time 3.76x, pass 0/1 vs 1/1
- pptx_chart: `mp-on-codex` vs `codex`: cost 4.08x, time 2.86x, pass 0/1 vs 1/1
- pptx_chart: `mp-on-codex` vs `pi`: cost 7.54x, time 3.97x, pass 0/1 vs 1/1
- pptx_chart: `mp-cli` vs `codex`: cost 1.52x, time 1.26x, pass 0/1 vs 1/1
- pptx_chart: `mp-cli` vs `pi`: cost 2.81x, time 1.75x, pass 0/1 vs 1/1
- xlsx_budget: `mp-work` vs `codex`: cost 3.94x, time 4.43x, pass 0/1 vs 1/1
- xlsx_budget: `mp-work` vs `pi`: cost 6.67x, time 3.70x, pass 0/1 vs 1/1
- xlsx_budget: `mp-on-pi` vs `codex`: cost 4.64x, time 4.96x, pass 0/1 vs 1/1
- xlsx_budget: `mp-on-pi` vs `pi`: cost 7.87x, time 4.14x, pass 0/1 vs 1/1
- xlsx_budget: `mp-on-codex` vs `codex`: cost 4.18x, time 5.85x
- xlsx_budget: `mp-on-codex` vs `pi`: cost 7.09x, time 4.88x
- xlsx_budget: `mp-cli` vs `codex`: cost 8.15x, time 10.61x, pass 0/1 vs 1/1
- xlsx_budget: `mp-cli` vs `pi`: cost 13.82x, time 8.85x, pass 0/1 vs 1/1
- xlsx_edit: `mp-work` vs `codex`: cost 7.06x, time 6.01x
- xlsx_edit: `mp-work` vs `pi`: cost 7.06x, time 3.63x
- xlsx_edit: `mp-on-pi` vs `codex`: cost 3.23x, time 1.97x
- xlsx_edit: `mp-on-pi` vs `pi`: cost 3.23x, time 1.19x
- xlsx_edit: `mp-on-codex` vs `codex`: cost 2.72x, time 1.59x
- xlsx_edit: `mp-on-codex` vs `pi`: cost 2.72x
- xlsx_edit: `mp-cli` vs `codex`: cost 3.14x, time 2.77x
- xlsx_edit: `mp-cli` vs `pi`: cost 3.14x, time 1.67x

## Notes on this run

- Code: base commit 370427e (0.0.12 + the audit branch). Model deepseek-v4.1-flash for every arm, one run per arm
  (iteration run; the final table uses gpt-6-luna with 3 runs). Cost from testkit/bench3/prices.json.
- The meter's $1.00 cap was reached during pptx_chart: the mp-on-pi pptx_chart failure includes refused calls (402),
  so that one fail is not a quality result. pdf_report was run separately with its own cap.
