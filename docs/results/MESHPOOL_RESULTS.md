# Meshpool: what is shipped, and how it compares with Codex

**Shipped build:** Meshpool **0.0.16**. This is the latest public download:
https://github.com/hamzaziada4422/meshpool-harness/releases/tag/v0.0.16 (commit `dd4ad47d`, published 2026-11-25 17:28 UTC).

It is the best build measured. A 0.0.17 candidate, made of 0.0.16 plus four office patches, was measured on all three office suites. It came out no better on cost per solved task, so it was not released.

## Results against Codex CLI 0.171

Both agents used the same model (deepseek-v4.1-flash), on the same gateway, at the same time.

- **Cost** is cost per solved task as a multiple of Codex's, with the 95% interval in brackets. Under 1.00x means cheaper.
- **Time** is the median of per-task time ratios. Under 1.00x means faster.

| Suite | Tasks x runs | Solved, Meshpool vs Codex | Cost per solved vs Codex (95% CI) | Time vs Codex |
|---|---|---|---|---|
| SWE-bench Verified, SymPy | 12 x 2 | 22 vs 22 | **0.76x** (0.69-0.84) | 0.93x |
| v2_dev code | 21 x 2 | 39 vs 38 | **0.79x** (0.69-0.90) | 0.99x |
| v2_eval + build, debug, deep, hard | 78 x 2 | 144 vs 145 | **0.71x** (0.65-0.78) | 1.03x |
| Work: DABench + data tasks | 27 x 2 | 45 vs 44 | **0.51x** (0.44-0.58) | 0.81x |
| Office-7: decks, documents, workbooks | 7 x 6 | 42 vs 41 | **0.94x** (0.72-1.18) | 0.76x |
| SpreadsheetBench, public sample | 6 x 6 | 31 vs 35 | **0.90x** (0.74-1.05) | 1.07x |
| OfficeBench, public sample | 4 x 6 | 21 vs 24 | **0.83x** (0.60-1.06) | 0.87x |

### Where Meshpool wins
- **Cost:** it is cheaper than Codex on every suite.
- **Code:** 21-29% cheaper, solving within one task of Codex. On two of the three code suites it solves as many or more.
- **Work and data tasks:** 49% cheaper, with one more task solved.
- **Office-7:** solves one more task than Codex, at 6% lower cost.

### Where it still loses
- **SpreadsheetBench:** 31 vs 35 solved.
- **OfficeBench:** 21 vs 24 solved.
- **Uncertainty:** the office suites are small, so their intervals are wide. The SpreadsheetBench and OfficeBench cost intervals reach past 1.00x.

## What 0.0.16 contains (cost and quality work)
- **A coding agent built on Codex's own design.** Code chats send the same request Codex CLI 0.171 sends: one command tool, plus Codex's prompt. Meshpool adds its permissions, sandbox, loop guards and compaction around it.
- **A direct office route.** Existing workbooks, documents and decks are edited in place with their own libraries, keeping formulas and styles. Before the answer goes back, these checks run:
  - the agent's own failed assertions;
  - slide count;
  - the total row;
  - template formatting;
  - shapes off the slide;
  - an output identical to the input;
  - years replaced by `TODAY()`;
  - formulas the turn wrote, recalculated with LibreOffice to catch Excel errors, or a "fix" that gives the same wrong result.
- **Security and reliability fixes from the audit:**
  - permission-rule bypasses closed;
  - a launch key now protects the app page;
  - a sub-agent usage race fixed;
  - file reads outside the project fixed.

## The 0.0.17 candidate (measured, not shipped)

| Suite | 0.0.17 candidate | 0.0.16 (shipped) |
|---|---|---|
| Office-7 | 40/42, 1.03x (0.76-1.26) | 42/42, 0.94x |
| SpreadsheetBench (12 runs) | 64/72, 1.08x (0.87-1.31) | 31/36, 0.90x |
| OfficeBench | 22/24, 0.73x (0.59-0.85) | 21/24, 0.83x |

- **What it added:** a firmer second note when a formula fix still gives the wrong result; extracted values written as values; a check that catches a wrong cell handed back unchanged; and a fix for a false alarm from the failed-check note.
- **Result:** cost was worse on two of the three suites. The failures could not be traced to any patch, so the difference is mostly run-to-run noise, but nothing showed an improvement.
- **Where it is kept:** the patches are on branch `claude/amazing-hopper-j9708m` under `docs/patches/` (0008-0011), with raw rows in `docs/results/release017_candidate/`.

## Verification of the shipped build
- **CI:** the test workflow and the Release workflow both passed on release commit `ee728ce5`.
- **Linux download:** the checksum matches `SHA256SUMS.txt`, and the release smoke test passes (imports, Word/PDF, ffmpeg/pandoc/speech/notebooks, server and chat).
- **Code chat:** the shipped CLI against the real gateway ran a file and answered correctly, for $0.0022.
- **Office edit:** the shipped CLI in Work mode added a live `=SUM` total row to an xlsx, for $0.0028.
- **Not tested:** the Mac and Windows downloads were not opened. The Mac `.dmg` is the Intel app, which runs under Rosetta on Apple silicon, because GitHub's Apple-silicon build machines were unavailable.

## Open items, not shipped
- **PR #21** fixes the test scripts and the smoke test's stand-in model, and adds a LibreOffice download fallback for release builds. It is verified locally and held until the next release, because merging it starts CI.
- **Office quality gap** on SpreadsheetBench and OfficeBench. Closing it needs either more benchmark runs, so that small fixes can be measured, or a stronger model for office edits. Escalation patch 0004 exists, switched off.

## Spend
The model benchmark budget was **$10.22 billed of the $11.50 approved**.

Raw data for every row above is in `docs/results/release016/` and `docs/results/release017_candidate/` on `claude/amazing-hopper-j9708m`.
