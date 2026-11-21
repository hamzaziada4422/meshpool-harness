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
  - Icons are native vector shapes in theme colours.
  - Company logos are fetched from the internet (full-colour CC0 logos, else the Simple Icons mark in the brand's
    colour) and drawn as native shape groups in their own colours; P6 does not count them.
  - P9 fails any picture. Raster logos are left out unless a deck allows pictures.
- **Layout vault.**
  - Public notes for Forge's 22 layouts are in the repo.
  - Private notes for 321 usable library layouts sit in the knowledge base, written by 8 Haiku agents.
  - The `slide_layouts` tool searches both, and the deck skills use it.
- **Library components (local only).** A golden library slide is rebuilt as native, editable shapes in the deck's theme, with the writer's text in its slots, and checked by every rule.
- **Built-in layouts.** 91 layouts ship as abstract specs (geometry and slot roles, no template text, colours,
  fonts or names). 50 of them render clean and are offered to the storyline, which picks one per slide; the writer
  fills its slots.
- **Points, not bullet lists.** An argument column beside a chart or a diagram is drawn as designed points: one
  card per point with an icon chosen by its meaning (numbered badges when not every point has one) and a short
  header the writer gives it; four short points make a 2x2 of icon boxes. Text that would not fit as cards stays a
  text column, so nothing is cut.
- **House style.** The rules of two consulting deck skills are built in (`docs/deck-engine/house-style.md`): in
  the writer's prompt, in the renderer (aligned feet, rows that use the body, full-width tables with the so-what
  under them) and in the validator (P20 `unaligned_feet`).
- **Storyline.** The brief's slide count is kept (a two-page memo is two slides), and "no cover" means no cover.
- **Reference-guided review**, behind a flag and off.
- **Engine fixes** found by looking:
  - the minimal look's off-grid parts;
  - chevrons in tall regions;
  - table words breaking mid-word.

## Measured gains and costs

Renderer-only A/B on frozen content (`docs/results/2026-11-20-forge-kb.md`):
- Dev failures went from 92 to 59.
- Test failures went from 45 to 26.

Live builds on the Meshpool gateway (DeepSeek V4.1 Flash) of six short briefs found five engine defects, fixed with
tests. After the fixes, a two-slide investor memo passes every rule and the other reruns end with P13 or P14 only:
numbers the model quotes beside a chart that does not plot them, or derives without a fact. Model spend is not
readable from the session key.

Every deck opens cleanly and has no overflow, no placeholders and no pictures. No change added a model call.

## Rejected or not run

- **D's A/B:** not run, because there was no model budget. D ships off.
- **P14 exemption for context numbers:** not changed, because the data does not support it.
- **Threshold changes:** none, because the data does not clearly dispute any.
- **Golden set of 400 to 600:** not reachable. The library has 214 items of quality 4 or 5.

## Remaining weaknesses

- Number discipline in live builds: P13 and P14 survive four repair rounds on the flash model.
- 41 of the 91 built-in layouts are held back (mostly P20 on wide layouts).
- Dollars per deck and the polish score are unmeasured: the gateway does not expose spend to the key.
- Thin timelines and words-only grids leave empty space. That is content the repair loop must fill, plus one renderer gap (item 7 of the worst slides).
- Library items whose text sits in group children cannot have those slots filled.

## Next three improvements

1. Run the 12 briefs live on the baseline and on this branch, and the D A/B, with a key whose spend is readable.
2. Move numbers the chart does not plot into footnotes in the renderer (P14), instead of asking the writer.
3. Raise the offered layouts past 50: lay out wide layouts with the argument below them, and label group children
   in library items.
