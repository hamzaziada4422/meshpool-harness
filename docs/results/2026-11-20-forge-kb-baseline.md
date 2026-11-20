# Forge knowledge base: baseline, 2026-11-20

Forge on `main` (6ed9da3) before any knowledge base change. Renderer-only: the slide JSON is fixed, so this measures
the renderer, the rules and the validators, not a content model.

## How it was run

- No model calls. Both model keys were unusable on this date (`docs/plan/FORGE_KB_BLOCKERS.md`), so live builds,
  repair rounds, tokens and dollars per deck, and the reviewer's polish score were not run.
- Content: 12 synthetic briefs in `app/tests/fixtures/forge_kb_briefs/`, written once, by hand, in the role of the
  content model, before any engine change, and frozen. Dev split 01, 02, 03, 04, 06, 07, 09, 11; test split 05, 08,
  10, 12. Only dev is used for tuning. This is hand-authored content, not the production model's.
- Each deck is rendered once (`carry=False`, density balanced), validated with the LibreOffice render (P0 to P20),
  checked with `ooxml_check` against the ECMA-376 schemas, and converted to page PNGs.
- Command (from `app/`, with `MH_ALLOW_SYSTEM_SOFFICE=1`):
  `python ../testkit/forge_kb_eval.py OUT --split all`.
- First pass only: in a live build the failures below go back to the content model for up to 4 repair rounds. Here
  they do not, so every count is a first-pass count.

## Tests

`cd app && MH_ALLOW_SYSTEM_SOFFICE=1 python -m pytest -q -p no:cacheprovider tests`: 1443 passed, 2 failed, 23
skipped. The 2 failures are `test_browser_modes` tests that need the Python Playwright package, which this container
lacks; they do not touch Forge. Without `MH_ALLOW_SYSTEM_SOFFICE=1` a source checkout does not use the system
LibreOffice, and 21 render tests fail for that reason alone.

## Per deck

| Deck | Slides | Rules passed | Rules failed | P20 worst slide | P20 mean | Overflow | Placeholder text | OOXML clean | Render s |
|---|---|---|---|---|---|---|---|---|---|
| 01_market_entry | 10 | 17 | P13, P14, P20, P7 | 0.64 | 0.21 | 0 | 0 | yes | 1.9 |
| 02_due_diligence | 10 | 17 | P11, P13, P14, P7 | 0.17 | 0.11 | 0 | 0 | yes | 1.9 |
| 03_cost_reduction | 9 | 18 | P13, P14, P20 | 0.23 | 0.14 | 0 | 0 | yes | 1.8 |
| 04_post_merger_integration | 9 | 17 | P13, P14, P20, P7 | 0.69 | 0.23 | 0 | 0 | yes | 1.5 |
| 06_bank_strategy_refresh | 10 | 17 | P11, P13, P20, P7 | 0.64 | 0.21 | 0 | 0 | yes | 1.9 |
| 07_pricing_review | 9 | 16 | P11, P13, P14, P20, P7 | 0.37 | 0.16 | 0 | 0 | yes | 1.8 |
| 09_digital_roadmap | 9 | 16 | P10, P11, P13, P14, P20 | 0.29 | 0.15 | 0 | 0 | yes | 1.7 |
| 11_product_launch | 10 | 17 | P13, P14, P20, P7 | 0.69 | 0.21 | 0 | 0 | yes | 1.9 |
| 05_investor_update (test) | 9 | 17 | P11, P13, P14, P20 | 0.38 | 0.18 | 0 | 0 | yes | 1.6 |
| 08_org_redesign (test) | 9 | 18 | P13, P20, P7 | 0.26 | 0.17 | 0 | 0 | yes | 2.0 |
| 10_esg_assessment (test) | 9 | 17 | P13, P14, P20, P7 | 0.33 | 0.16 | 0 | 0 | yes | 1.7 |
| 12_board_pack (test) | 9 | 17 | P11, P13, P14, P20 | 0.29 | 0.19 | 0 | 0 | yes | 1.7 |

"Rules passed" counts P0 to P21 statuses of `pass`; none was `not_run`.

## Totals

| Split | Slides | Failures | P20 | P13 | P14 | P7 | P11 | P10 | Content slides over 20% empty | P20 mean |
|---|---|---|---|---|---|---|---|---|---|---|
| dev | 76 | 92 | 32 | 37 | 10 | 6 | 6 | 1 | 22 of 68 | 0.178 |
| test | 36 | 45 | 19 | 16 | 4 | 4 | 2 | 0 | 12 of 32 | 0.173 |

Decks passing every rule first time: 0 of 12. Overflow: 0. Leftover placeholder text: 0. OOXML clean: 12 of 12.
Every chart and table is native (P2, P3 pass on all decks).

Failure checks, all decks: P20 dead space 34, P13 unsourced number 29, P13 no source line 24, P20 thin 17, P14
text and chart disagree 14, P7 off grid 10, P11 title without a verb 8, P10 too many bullets 1.

## What the failures say

- P13 no source line (24): the executive summary and the closing slide of every deck carry numbers but no source
  line. Content, and repaired in a live build.
- P13 unsourced number (29): mostly numbers inside grid parts (stats, process, timeline) and closing steps, where
  the fixture gave a slide-level source but no per-number provenance. Content.
- P20 dead space and thin (51): the largest group. Timelines with 4 milestones, process parts and short grids leave
  half the body empty (worst slides 0.64 to 0.69). Partly content (thin slides), partly renderer: a process part
  draws small chevrons at the top of a tall region and leaves the rest empty.
- P7 off grid (10): grid part headings and callouts end at 12.58 in, off the 24-column grid. A renderer defect, the
  same on every deck that uses the parts layout.

## Reference measurements

`docs/deck-engine/reference-measurements.md` and `app/engines/forge/data/reference_stats.json`: 130 of 132 reference
decks (73 consulting, 57 financial services), 3655 content pages, numbers only.

## Thresholds the 132 reference decks dispute

Nothing was changed here. Shares are of the 3474 reference pages with a measured body band. The references include
long reports and investor decks, not only consulting exhibit pages, and raster exhibits make text counts lower
bounds.

| Rule (key) | Current value | What the data shows | Read |
|---|---|---|---|
| P20 `whitespace.max_empty_share` | 0.20 | median 0.10 (p10 0.04, p90 0.27). 16.7% of pages exceed 0.20 (consulting 14%, financial services 18%); 11.5% exceed 0.25; 8.6% exceed 0.30. The grammar's own figure (median 0.06, p90 0.12) came from four McKinsey pages; this set has no McKinsey deck | The cap sits near the reference p83. Strict but defensible for a quality floor. Candidate: none for balanced and read; present's 0.30 matches the reference p91 |
| P11 `titles.max_words` | 15 | median 6, p90 18; consulting median 8, p90 24. 14% of all pages and 30% of consulting pages exceed 15 words. Only 4.2% of titles run past 2 lines | Consulting action titles run longer than 15 words while staying within 2 lines. The 2-line cap is supported; the 15-word cap is tight for read decks |
| P11 `titles.require_verb` | true | 51% of English titles carry a verb (consulting 59%, financial services 45%) | The references often use topic titles. The rule stands by design (engine-spec: where a reference breaks a rule, the rule stands) |
| P13 `provenance.require_source_line` | true | 23% of pages show a source line (consulting 34%, financial services 16%); lower bound | Same: the rule stands by design |
| P10 `density.max_body_words` (read default) | 80 | median 84, p10 15, p90 250; 50% of pages exceed 80, 34% exceed 110, 20% exceed 160 | The default sits at the median; the per-archetype caps (100 to 260) cover the dense tail. The old note "median 114, p90 185" (16 decks) is replaced by median 84, p90 250 (130 decks) |
| Title size (grammar, 25 pt; `text_fit.min_title_pt` 20) | 25 pt, floor 20 | scaled to a 13.33 in slide: median 28 (consulting 25, financial services 28), p10 18 | 25 pt matches consulting; the floor is above the reference p10. No change |
| Margins (grammar: content from 0.6 in) | 0.6 in | text left margin median 0.50 in (p10 0.27, p90 0.85), right 0.50 in | Forge sits within p10 to p90. No change |
| Font sizes per page (`fonts.max_sizes_per_role*`) | per role, 3 to 8 | 6 distinct sizes per page (p10 3, p90 9) | Not comparable one to one (per page against per role); no dispute |
| Colours (theme slots) | 8 slots | 5 colours per page (p10 1, p90 10) | Consistent |

## Archetype gaps

Sources: (1) 777 sampled reference pages (6 per deck, 130 decks) labelled by layout from contact sheets (a vision
pass; primary and up to two secondary labels); (2) library item counts in the `consulting_grade` and `business` tiers;
(3) diagrams the dev briefs wanted but could not express (`wanted_diagrams` in each dev `meta.json`).

| Layout | Reference pages (any label) | Decks | Library (consulting + business) | Dev briefs wanting it | Forge today |
|---|---|---|---|---|---|
| Icon grid | 105 (13.5%) | 63 | 27 (icon sets) | 0 | icons only inside the challenge matrix |
| Process flow | 74 (9.5%) | 47 | 270 | 0 | `process` part (weak at full height, see above) |
| Map | 19 (2.4%) | 18 | 13 | 0 | none |
| Hub and spoke | 14 (1.8%) | 12 | in matrix_framework | 0 | none |
| Team page | 12 (1.5%) | 12 | 161 | 0 | none (needs photos) |
| Cycle | 10 (1.3%) | 10 | 31 | 0 | none |
| Gantt or swimlane roadmap | 5 timeline pages | 5 | 227 (timeline_roadmap) | 4 | `gantt_plan` is in the spec table but has no renderer |
| 3x3 matrix, heat or risk matrix | 6 (0.8%) | 6 | 138 (matrix_framework) | 4 (risk matrix 3, risk heat map 1) | 2x2 only; heat tables only |
| Venn | 5 (0.6%) | 5 | 9 | 0 | none |
| Issue or driver tree | 1 | 1 | 96 (hierarchy_org) | 2 | none |
| Org chart | 2 | 2 | 96 (hierarchy_org) | 1 | none |
| Pyramid or funnel | 3 | 3 | 37 | 1 | none |

Ranking for section 4B, weighing the briefs' demand first (what a deck needs and cannot say), then reference and
library frequency:

1. Risk matrix and 3x3 matrix (one renderer: a native heat grid with items placed in cells).
2. Gantt and swimlane roadmap (the spec already promises `gantt_plan`).
3. Issue or driver tree, and org chart (one tree layout renderer).
4. Cycle and hub and spoke (one radial layout renderer).
5. Pyramid and funnel; then venn.

Icon grids are the most common layout in the references that Forge lacks as a part; they come after these because
no dev brief asked for one, and Forge's icon set rules (P9, at most 4 per slide) would need a change first.
