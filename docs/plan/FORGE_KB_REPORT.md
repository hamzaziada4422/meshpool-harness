# Forge knowledge base: final report

## What changed

- **Knowledge base, private and optional.**
  - `engines/forge/kb.py` covers search, similar items, thumbnails, priors, a quality score, vision labels, the golden set (214 items) and an audit.
  - `kb_ingest.py` adds new template packs.
  - It reads `MESHPOOL_KB` only, and Forge works the same without it.
- **Measurements.** 130 reference decks (3,655 pages) are measured as numbers only, in `reference_stats.json` and `reference-measurements.md`.
- **Calibration.** P11 now counts imperative and present-tense verbs. No numeric threshold changed, because the data does not clearly dispute any.
- **Nine diagram archetypes**, drawn natively as grid parts and as archetypes: risk matrix, 3x3, Gantt, issue tree, org chart, cycle, hub and spoke, pyramid, funnel.
- **Seven style packs**, clustered from the reference decks, with `--style` and Settings > Company.
- **Band frame.** An optional BCG-style look: dark title band, two-tone titles, tracker number, phrases in the accent colour.
- **Zero images.**
  - Icons and SVG logos are native vector shapes in theme colours.
  - P9 fails any picture.
  - Raster logos are left out unless a deck allows pictures.
- **Layout vault.**
  - Public notes for Forge's 22 layouts are in the repo.
  - Private notes for 321 usable library layouts sit in the knowledge base, written by 8 Haiku agents.
  - The `slide_layouts` tool searches both, and the deck skills use it.
- **Library components (local only).** A golden library slide is rebuilt as native, editable shapes in the deck's theme, with the writer's text in its slots, and checked by every rule.
- **Reference-guided review**, behind a flag and off.
- **Engine fixes** found by looking:
  - the minimal look's off-grid parts;
  - chevrons in tall regions;
  - table words breaking mid-word.

## Measured gains and costs

Renderer-only A/B on frozen content (`docs/results/2026-11-20-forge-kb.md`):
- Dev failures went from 92 to 80.
- Test failures went from 45 to 41.

Every deck opens cleanly and has no overflow, no placeholders and no pictures. No change added a model call.

## Rejected or not run

- **D's A/B:** not run, because there was no model budget. D ships off.
- **P14 exemption for context numbers:** not changed, because the data does not support it.
- **Threshold changes:** none, because the data does not clearly dispute any.
- **Golden set of 400 to 600:** not reachable. The library has 214 items of quality 4 or 5.

## Remaining weaknesses

- Live builds, repair rounds, tokens and dollars, and the polish score are unmeasured: both model keys are blocked (`FORGE_KB_BLOCKERS.md`).
- Thin timelines and words-only grids leave empty space. That is content the repair loop must fill, plus one renderer gap (item 7 of the worst slides).
- Library items whose text sits in group children cannot have those slots filled.
- Card padding trips P7.

## Next three improvements

1. With a model key: run the 12 briefs live on the baseline and on this branch, and run the D A/B.
2. A storyline-first planner. Pick a framework (situation, complication, resolution; issue tree; options), storyboard titles by slide count and depth, and choose each slide's layout from the vault before writing.
3. Fill the remaining gaps:
   - the argument layout fills to the foot;
   - P7 accepts content inside a card's padding;
   - slot labels for group children.
