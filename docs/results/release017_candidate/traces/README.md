Failing runs of the 0.0.17 candidate (8e015e03; app code = rc017b c193d8e1). One .md trace and the redacted session JSON per run.

| suite | task | rep | calls | result |
|---|---|---|---|---|
| O7 | docx_mailmerge | 3 | 6 | FAILED: each customer has one letter with name, company, amount, due date, Heading 1 and a bold run (Maria Lindqvist: due date 2026-12-15 missing; Tomas Okafor: |
| O7 | xlsx_budget | 6 | 17 | FAILED: 12 months of profit match (expected ~7291); annual profit matches (expected ~87487); changing the average ticket by +10% moves annual revenue and profit |
| spreadsheetbench | sb-56921 | 2 | 25 | Value difference at cell C15: golden has 294, output has 511 |
| spreadsheetbench | sb-269-43 | 3 | 6 | Value difference at cell I6: golden has None, output has 95704.64 |
| spreadsheetbench | sb-269-43 | 5 | 9 | Value difference at cell I7: golden has None, output has 95704.64 |
| spreadsheetbench | sb-280-17 | 7 | 8 | Value difference at cell A1: golden has 'DATA 3', output has 'DATA 1' |
| spreadsheetbench | sb-374-31 | 7 | 10 | Value difference at cell A1: golden has 'Code', output has None |
| spreadsheetbench | sb-41969 | 11 | 17 | Value difference at cell B6: golden has 1, output has None |
| spreadsheetbench | sb-39515 | 11 | 7 | Value difference at cell O3: golden has 1350, output has 1560 |
| spreadsheetbench | sb-269-43 | 11 | 18 | Value difference at cell I6: golden has None, output has 95704.64 |
| officebench | ob-2-23-0 | 3 | 6 | failed: evaluate_contain |
| officebench | ob-2-23-0 | 6 | 5 | failed: evaluate_contain |
