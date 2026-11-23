# Benchmark report

Model `deepseek-v4.1-flash`, 2026-11-23T22:04:06+00:00, codex `codex-cli 0.171.0`, pi `0.73.1`, Meshpool source `/tmp/claude-0/final/app` @ `ab17382102`. Every model call of every arm went through one metering proxy; cost = uncached input + cached input + output at the price table in `prices.json` (unknown model: no cost).

## Per task and arm

| task | arm | pass | cost mean (min-max) USD | calls | input | cached | output | reasoning | secs mean (min-max) |
|---|---|---|---|---|---|---|---|---|---|
| docx_mailmerge | codex | 1/1 | 0.0070 (0.0070-0.0070) | 11.0 | 122375 | 109056 | 1443 | - | 17.6 (17.6-17.6) |
| docx_mailmerge | pi | 1/1 | 0.0035 (0.0035-0.0035) | 8.0 | 23891 | 19456 | 1433 | - | 17.4 (17.4-17.4) |
| docx_mailmerge | mp-work | 1/1 | 0.0087 (0.0087-0.0087) | 11.0 | 130460 | 114176 | 1940 | - | 23.2 (23.2-23.2) |
| docx_mailmerge | mp-on-pi | 1/1 | 0.0060 (0.0060-0.0060) | 8.0 | 46156 | 35328 | 1651 | - | 15.1 (15.1-15.1) |
| docx_mailmerge | mp-on-codex | 1/1 | 0.0080 (0.0080-0.0080) | 9.0 | 122681 | 107008 | 1603 | - | 21.1 (21.1-21.1) |
| docx_mailmerge | mp-cli | 1/1 | 0.0052 (0.0052-0.0052) | 9.0 | 100261 | 94464 | 1986 | - | 20.4 (20.4-20.4) |
| docx_tracked | codex | 1/1 | 0.0166 (0.0166-0.0166) | 18.0 | 290445 | 271104 | 6351 | - | 83.0 (83.0-83.0) |
| docx_tracked | pi | 1/1 | 0.0072 (0.0072-0.0072) | 14.0 | 71727 | 65792 | 3644 | - | 31.4 (31.4-31.4) |
| docx_tracked | mp-work | 1/1 | 0.2289 (0.2289-0.2289) | 235.0 | 2937632 | 2668288 | 92719 | - | 723.1 (723.1-723.1) |
| docx_tracked | mp-on-pi | 0/1 | 0.0134 (0.0134-0.0134) | 20.0 | 178382 | 158976 | 4500 | - | 49.3 (49.3-49.3) |
| docx_tracked | mp-on-codex | 0/1 | 0.0135 (0.0135-0.0135) | 20.0 | 310807 | 290048 | 3619 | - | 41.1 (41.1-41.1) |
| docx_tracked | mp-cli | 1/1 | 0.0183 (0.0183-0.0183) | 27.0 | 394125 | 373760 | 6934 | - | 79.5 (79.5-79.5) |
| invoices_fx | codex | 1/1 | 0.0060 (0.0060-0.0060) | 6.0 | 59564 | 48128 | 1420 | - | 16.8 (16.8-16.8) |
| invoices_fx | pi | 1/1 | 0.0032 (0.0032-0.0032) | 6.0 | 15178 | 12544 | 1732 | - | 14.2 (14.2-14.2) |
| invoices_fx | mp-work | 1/1 | 0.0240 (0.0240-0.0240) | 18.0 | 160267 | 111872 | 5506 | - | 44.6 (44.6-44.6) |
| invoices_fx | mp-on-pi | 0/1 | 0.0304 (0.0304-0.0304) | 37.0 | 401109 | 348416 | 8147 | - | 79.3 (79.3-79.3) |
| invoices_fx | mp-on-codex | 1/1 | 0.0203 (0.0203-0.0203) | 18.0 | 178929 | 140544 | 5062 | - | 50.2 (50.2-50.2) |
| invoices_fx | mp-cli | 1/1 | 0.0040 (0.0040-0.0040) | 7.0 | 72528 | 67840 | 1518 | - | 21.5 (21.5-21.5) |
| pdf_report | codex | 1/1 | 0.0105 (0.0105-0.0105) | 11.0 | 144091 | 130304 | 3863 | - | 43.1 (43.1-43.1) |
| pdf_report | pi | 1/1 | 0.0069 (0.0069-0.0069) | 10.0 | 49892 | 44032 | 3535 | - | 39.9 (39.9-39.9) |
| pdf_report | mp-work | 0/1 | 0.3628 (0.3628-0.3628) | 271.0 | 1181489 | 826112 | 181844 | - | 900.1 (900.1-900.1) |
| pdf_report | mp-on-pi | 0/1 | 0.0106 (0.0106-0.0106) | 15.0 | 143357 | 127744 | 3483 | - | 900.9 (900.9-900.9) |
| pdf_report | mp-on-codex | 1/1 | 0.0327 (0.0327-0.0327) | 55.0 | 1050830 | 1014784 | 10659 | - | 153.6 (153.6-153.6) |
| pdf_report | mp-cli | 0/1 | 0.0530 (0.0530-0.0530) | 49.0 | 1240537 | 1159680 | 14144 | - | 155.2 (155.2-155.2) |
| pptx_chart | codex | 1/1 | 0.0164 (0.0164-0.0164) | 14.0 | 214670 | 198144 | 7317 | - | 88.5 (88.5-88.5) |
| pptx_chart | pi | 1/1 | 0.0096 (0.0096-0.0096) | 10.0 | 62092 | 56320 | 5544 | - | 56.3 (56.3-56.3) |
| pptx_chart | mp-work | 1/1 | 0.3868 (0.3868-0.3868) | 274.0 | 1592032 | 1134080 | 172855 | - | 900.9 (900.9-900.9) |
| pptx_chart | mp-on-pi | 1/1 | 0.0887 (0.0887-0.0887) | 68.0 | 882988 | 721152 | 23170 | - | 166.6 (166.6-166.6) |
| pptx_chart | mp-on-codex | 1/1 | 0.0608 (0.0608-0.0608) | 39.0 | 291711 | 193024 | 20402 | - | 98.3 (98.3-98.3) |
| pptx_chart | mp-cli | 0/1 | 0.2722 (0.2722-0.2722) | 202.0 | 1228708 | 896512 | 118653 | - | 900.0 (900.0-900.0) |
| xlsx_budget | codex | 1/1 | 0.0103 (0.0103-0.0103) | 11.0 | 139074 | 125184 | 3691 | - | 28.7 (28.7-28.7) |
| xlsx_budget | pi | 0/1 | 0.0066 (0.0066-0.0066) | 8.0 | 34644 | 30208 | 3708 | - | 44.5 (44.5-44.5) |
| xlsx_budget | mp-work | 1/1 | 0.0477 (0.0477-0.0477) | 49.0 | 662959 | 586240 | 14057 | - | 130.9 (130.9-130.9) |
| xlsx_budget | mp-on-pi | 0/1 | 0.0501 (0.0501-0.0501) | 37.0 | 543089 | 449280 | 12256 | - | 136.4 (136.4-136.4) |
| xlsx_budget | mp-on-codex | 1/1 | 0.0213 (0.0213-0.0213) | 25.0 | 290519 | 258048 | 6735 | - | 70.2 (70.2-70.2) |
| xlsx_budget | mp-cli | 0/1 | 0.0231 (0.0231-0.0231) | 31.0 | 321555 | 290304 | 8227 | - | 65.0 (65.0-65.0) |
| xlsx_edit | codex | 1/1 | 0.0123 (0.0123-0.0123) | 13.0 | 198607 | 178432 | 3369 | - | 39.9 (39.9-39.9) |
| xlsx_edit | pi | 1/1 | 0.0057 (0.0057-0.0057) | 9.0 | 50764 | 42752 | 2068 | - | 38.0 (38.0-38.0) |
| xlsx_edit | mp-work | 1/1 | 0.0193 (0.0193-0.0193) | 16.0 | 301294 | 276480 | 7037 | - | 49.2 (49.2-49.2) |
| xlsx_edit | mp-on-pi | 1/1 | 0.0107 (0.0107-0.0107) | 11.0 | 87517 | 75776 | 4818 | - | 46.2 (46.2-46.2) |
| xlsx_edit | mp-on-codex | 1/1 | 0.0164 (0.0164-0.0164) | 19.0 | 299628 | 279040 | 5869 | - | 66.2 (66.2-66.2) |
| xlsx_edit | mp-cli | 1/1 | 0.0115 (0.0115-0.0115) | 12.0 | 151613 | 135424 | 3989 | - | 35.5 (35.5-35.5) |

## Meshpool arms relative to Codex and Pi (mean over reps; below 1.00 is better)

| task | arm vs reference | cost ratio | time ratio |
|---|---|---|---|
| docx_mailmerge | mp-work/codex | 1.24 | 1.31 |
| docx_mailmerge | mp-work/pi | 2.49 | 1.33 |
| docx_mailmerge | mp-on-pi/codex | 0.85 | 0.85 |
| docx_mailmerge | mp-on-pi/pi | 1.72 | 0.87 |
| docx_mailmerge | mp-on-codex/codex | 1.14 | 1.20 |
| docx_mailmerge | mp-on-codex/pi | 2.29 | 1.21 |
| docx_mailmerge | mp-cli/codex | 0.73 | 1.16 |
| docx_mailmerge | mp-cli/pi | 1.48 | 1.17 |
| docx_tracked | mp-work/codex | 13.83 | 8.71 |
| docx_tracked | mp-work/pi | 31.78 | 23.04 |
| docx_tracked | mp-on-pi/codex | 0.81 | 0.59 |
| docx_tracked | mp-on-pi/pi | 1.86 | 1.57 |
| docx_tracked | mp-on-codex/codex | 0.82 | 0.50 |
| docx_tracked | mp-on-codex/pi | 1.88 | 1.31 |
| docx_tracked | mp-cli/codex | 1.11 | 0.96 |
| docx_tracked | mp-cli/pi | 2.55 | 2.53 |
| invoices_fx | mp-work/codex | 4.02 | 2.66 |
| invoices_fx | mp-work/pi | 7.40 | 3.14 |
| invoices_fx | mp-on-pi/codex | 5.10 | 4.72 |
| invoices_fx | mp-on-pi/pi | 9.40 | 5.57 |
| invoices_fx | mp-on-codex/codex | 3.40 | 2.99 |
| invoices_fx | mp-on-codex/pi | 6.26 | 3.52 |
| invoices_fx | mp-cli/codex | 0.67 | 1.28 |
| invoices_fx | mp-cli/pi | 1.23 | 1.51 |
| pdf_report | mp-work/codex | 34.52 | 20.86 |
| pdf_report | mp-work/pi | 52.65 | 22.54 |
| pdf_report | mp-on-pi/codex | 1.01 | 20.88 |
| pdf_report | mp-on-pi/pi | 1.54 | 22.56 |
| pdf_report | mp-on-codex/codex | 3.11 | 3.56 |
| pdf_report | mp-on-codex/pi | 4.74 | 3.85 |
| pdf_report | mp-cli/codex | 5.04 | 3.60 |
| pdf_report | mp-cli/pi | 7.69 | 3.89 |
| pptx_chart | mp-work/codex | 23.56 | 10.17 |
| pptx_chart | mp-work/pi | 40.31 | 15.99 |
| pptx_chart | mp-on-pi/codex | 5.41 | 1.88 |
| pptx_chart | mp-on-pi/pi | 9.25 | 2.96 |
| pptx_chart | mp-on-codex/codex | 3.70 | 1.11 |
| pptx_chart | mp-on-codex/pi | 6.33 | 1.74 |
| pptx_chart | mp-cli/codex | 16.58 | 10.16 |
| pptx_chart | mp-cli/pi | 28.37 | 15.97 |
| xlsx_budget | mp-work/codex | 4.64 | 4.56 |
| xlsx_budget | mp-work/pi | 7.28 | 2.94 |
| xlsx_budget | mp-on-pi/codex | 4.87 | 4.75 |
| xlsx_budget | mp-on-pi/pi | 7.64 | 3.06 |
| xlsx_budget | mp-on-codex/codex | 2.07 | 2.44 |
| xlsx_budget | mp-on-codex/pi | 3.25 | 1.58 |
| xlsx_budget | mp-cli/codex | 2.25 | 2.26 |
| xlsx_budget | mp-cli/pi | 3.52 | 1.46 |
| xlsx_edit | mp-work/codex | 1.57 | 1.23 |
| xlsx_edit | mp-work/pi | 3.41 | 1.30 |
| xlsx_edit | mp-on-pi/codex | 0.87 | 1.16 |
| xlsx_edit | mp-on-pi/pi | 1.90 | 1.22 |
| xlsx_edit | mp-on-codex/codex | 1.33 | 1.66 |
| xlsx_edit | mp-on-codex/pi | 2.90 | 1.74 |
| xlsx_edit | mp-cli/codex | 0.94 | 0.89 |
| xlsx_edit | mp-cli/pi | 2.03 | 0.93 |

## Totals per arm over all tasks

| arm | pass | runs | cost sum USD | cost per run | calls | seconds sum | secs per run |
|---|---|---|---|---|---|---|---|
| codex | 7/7 | 7 | 0.0790 | 0.0113 | 84 | 318 | 45.4 |
| pi | 6/7 | 7 | 0.0426 | 0.0061 | 65 | 242 | 34.5 |
| mp-work | 6/7 | 7 | 1.0781 | 0.1540 | 874 | 2772 | 396.0 |
| mp-on-pi | 3/7 | 7 | 0.2100 | 0.0300 | 196 | 1394 | 199.1 |
| mp-on-codex | 6/7 | 7 | 0.1729 | 0.0247 | 185 | 501 | 71.5 |
| mp-cli | 4/7 | 7 | 0.3873 | 0.0553 | 337 | 1277 | 182.4 |

## Where Meshpool loses

- docx_mailmerge: `mp-work` vs `codex`: cost 1.24x, time 1.31x
- docx_mailmerge: `mp-work` vs `pi`: cost 2.49x, time 1.33x
- docx_mailmerge: `mp-on-pi` vs `pi`: cost 1.72x
- docx_mailmerge: `mp-on-codex` vs `codex`: cost 1.14x, time 1.20x
- docx_mailmerge: `mp-on-codex` vs `pi`: cost 2.29x, time 1.21x
- docx_mailmerge: `mp-cli` vs `codex`: time 1.16x
- docx_mailmerge: `mp-cli` vs `pi`: cost 1.48x, time 1.17x
- docx_tracked: `mp-work` vs `codex`: cost 13.83x, time 8.71x
- docx_tracked: `mp-work` vs `pi`: cost 31.78x, time 23.04x
- docx_tracked: `mp-on-pi` vs `codex`: pass 0/1 vs 1/1
- docx_tracked: `mp-on-pi` vs `pi`: cost 1.86x, time 1.57x, pass 0/1 vs 1/1
- docx_tracked: `mp-on-codex` vs `codex`: pass 0/1 vs 1/1
- docx_tracked: `mp-on-codex` vs `pi`: cost 1.88x, time 1.31x, pass 0/1 vs 1/1
- docx_tracked: `mp-cli` vs `codex`: cost 1.11x
- docx_tracked: `mp-cli` vs `pi`: cost 2.55x, time 2.53x
- invoices_fx: `mp-work` vs `codex`: cost 4.02x, time 2.66x
- invoices_fx: `mp-work` vs `pi`: cost 7.40x, time 3.14x
- invoices_fx: `mp-on-pi` vs `codex`: cost 5.10x, time 4.72x, pass 0/1 vs 1/1
- invoices_fx: `mp-on-pi` vs `pi`: cost 9.40x, time 5.57x, pass 0/1 vs 1/1
- invoices_fx: `mp-on-codex` vs `codex`: cost 3.40x, time 2.99x
- invoices_fx: `mp-on-codex` vs `pi`: cost 6.26x, time 3.52x
- invoices_fx: `mp-cli` vs `codex`: time 1.28x
- invoices_fx: `mp-cli` vs `pi`: cost 1.23x, time 1.51x
- pdf_report: `mp-work` vs `codex`: cost 34.52x, time 20.86x, pass 0/1 vs 1/1
- pdf_report: `mp-work` vs `pi`: cost 52.65x, time 22.54x, pass 0/1 vs 1/1
- pdf_report: `mp-on-pi` vs `codex`: cost 1.01x, time 20.88x, pass 0/1 vs 1/1
- pdf_report: `mp-on-pi` vs `pi`: cost 1.54x, time 22.56x, pass 0/1 vs 1/1
- pdf_report: `mp-on-codex` vs `codex`: cost 3.11x, time 3.56x
- pdf_report: `mp-on-codex` vs `pi`: cost 4.74x, time 3.85x
- pdf_report: `mp-cli` vs `codex`: cost 5.04x, time 3.60x, pass 0/1 vs 1/1
- pdf_report: `mp-cli` vs `pi`: cost 7.69x, time 3.89x, pass 0/1 vs 1/1
- pptx_chart: `mp-work` vs `codex`: cost 23.56x, time 10.17x
- pptx_chart: `mp-work` vs `pi`: cost 40.31x, time 15.99x
- pptx_chart: `mp-on-pi` vs `codex`: cost 5.41x, time 1.88x
- pptx_chart: `mp-on-pi` vs `pi`: cost 9.25x, time 2.96x
- pptx_chart: `mp-on-codex` vs `codex`: cost 3.70x, time 1.11x
- pptx_chart: `mp-on-codex` vs `pi`: cost 6.33x, time 1.74x
- pptx_chart: `mp-cli` vs `codex`: cost 16.58x, time 10.16x, pass 0/1 vs 1/1
- pptx_chart: `mp-cli` vs `pi`: cost 28.37x, time 15.97x, pass 0/1 vs 1/1
- xlsx_budget: `mp-work` vs `codex`: cost 4.64x, time 4.56x
- xlsx_budget: `mp-work` vs `pi`: cost 7.28x, time 2.94x
- xlsx_budget: `mp-on-pi` vs `codex`: cost 4.87x, time 4.75x, pass 0/1 vs 1/1
- xlsx_budget: `mp-on-pi` vs `pi`: cost 7.64x, time 3.06x
- xlsx_budget: `mp-on-codex` vs `codex`: cost 2.07x, time 2.44x
- xlsx_budget: `mp-on-codex` vs `pi`: cost 3.25x, time 1.58x
- xlsx_budget: `mp-cli` vs `codex`: cost 2.25x, time 2.26x, pass 0/1 vs 1/1
- xlsx_budget: `mp-cli` vs `pi`: cost 3.52x, time 1.46x
- xlsx_edit: `mp-work` vs `codex`: cost 1.57x, time 1.23x
- xlsx_edit: `mp-work` vs `pi`: cost 3.41x, time 1.30x
- xlsx_edit: `mp-on-pi` vs `codex`: time 1.16x
- xlsx_edit: `mp-on-pi` vs `pi`: cost 1.90x, time 1.22x
- xlsx_edit: `mp-on-codex` vs `codex`: cost 1.33x, time 1.66x
- xlsx_edit: `mp-on-codex` vs `pi`: cost 2.90x, time 1.74x
- xlsx_edit: `mp-cli` vs `pi`: cost 2.03x
