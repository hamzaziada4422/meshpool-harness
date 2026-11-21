# The slide knowledge base

A private library that Forge can use to study what strong slides look like. It holds:

- 132 reference decks from consulting and financial services firms (PDFs, and picture-slide copies).
- 7,252 slide items cut from free template packs: full slides, partial slide blocks and shape clusters.
- A thumbnail per item, a SQLite index, and style profiles of the reference decks.

It is never part of this repo. Forge works the same without it.

## Licensing

The reference decks are copyrighted works of their publishers, kept for private study only. The template packs come
under their vendors' terms. Nothing from the knowledge base is committed, shipped in the installer or shown to a
user, except when the user turns on "show references" on their own machine:

- not the decks, the items or the thumbnails;
- not text, shapes or images cut from them;
- not labels that name a deck.

The repo keeps only code, tests, rules and numbers measured from it (distributions such as median, p10 and p90).
Tests in `app/tests/test_kb_hygiene.py` fail if a PPTX over 1 MB, an image under `app/engines/forge/data/`, or a deck
or image in the test fixtures is committed.

Items cut from the user's own uploaded files (group `examples_uploaded`) are left out of every result, number and
golden set unless a call asks for them.

## Get it

It lives in the private repo `hamzaziada4422/Slide-templates`, folder `kb/`, as zips split into parts under 90 MB.

```bash
git clone --depth 1 https://github.com/hamzaziada4422/Slide-templates ~/Slide-templates
```

```bash
cd ~/Slide-templates/kb && ./reassemble.sh
```

```bash
mkdir -p ~/meshpool_kb && for z in slide_engine_kb slide_engine_originals_pdfs slide_engine_templates_original; do unzip -q ~/Slide-templates/kb/$z.zip -d ~/meshpool_kb/; done
```

```bash
export MESHPOOL_KB=~/meshpool_kb/slide_engine_kb
```

`reassemble.sh` joins the parts and checks each zip against `SHA256SUMS.txt`. Put the export line in `.env.kb` at the
repo root (gitignored) to keep it. Code reads only the `MESHPOOL_KB` environment variable.

## What degrades when it is missing

Nothing in a normal build. `engines.forge.kb.available()` is false, every lookup returns an empty result, and the log
says once that the knowledge base is off. The reference numbers in `app/engines/forge/data/reference_stats.json` ship
with the engine and need no knowledge base. Tests that need it are marked `needs_kb` and skip.

## What Forge adds to it

`app/engines/forge/kb.py` reads the original index and writes its own tables to
`$MESHPOOL_KB/library/labels.sqlite`; the original items table is never changed.

| Table | What |
|---|---|
| `scores` | a deterministic quality score per item, 0 to 1: tier, text to shape ratio, placeholder text, shapes off the slide, overlapping text boxes, size, colour and font counts, gradients, and whether it rendered |
| `labels` | a vision label per item: category, subcategory, one line description, quality 1 to 5, usable as is, style tags |
| `features` | an image fingerprint per thumbnail (difference hash, colour histogram, ink layout grid) for `similar` and near-duplicate removal |
| `golden` | the golden set: labelled quality 4 or 5 and usable, near duplicates removed |
| `ingested` | items added with `ingest` |

It also writes `library/golden.jsonl` and `reports/library_audit.md`.

## Commands

From `app/`, with `MESHPOOL_KB` set:

```bash
python -m engines.forge.kb search "process flow 5 steps" --limit 5
```

```bash
python -m engines.forge.kb similar ITEM_ID -k 5
```

```bash
python -m engines.forge.kb priors process_steps
```

```bash
python -m engines.forge.kb score
```

```bash
python -m engines.forge.kb labels labels.jsonl
```

```bash
python -m engines.forge.kb golden
```

```bash
python -m engines.forge.kb audit
```

The Python API is `available()`, `search(category, scope, text, tier, limit)`, `similar(item_id, k)`,
`thumbnail(item_id)` and `priors(archetype)`.

## Add new templates

```bash
python -m engines.forge.kb ingest ~/new-templates
```

For every `.pptx` or `.potx` in the folder, ingest:

1. Cuts one item per slide, plus one per large top-level group (3 or more shapes, 6% or more of the slide).
2. Drops duplicates. The key is a hash of the shape tree's structure (geometry, nesting and fills, without ids,
   names, positions or text) plus the size.
3. Makes a thumbnail. LibreOffice converts in chunks; a chunk that hangs is killed with its process group, and its
   files are retried one at a time with a short timeout.
4. Gives each item a heuristic category and a quality score.

Items land in `library/ingested/`, `thumbs/` and the `ingested` table. Vision labels can be added afterwards with the
`labels` command. Running ingest again on the same folder adds nothing.

## The layout vault and library components

- `python -m engines.forge.vault private` writes one linked note per usable library item into
  `$MESHPOOL_KB/vault/`, with its name, when to use it, its structure, its text slots and what each holds, and the
  Forge layout that draws it. The public vault of Forge's own layouts is in `docs/deck-engine/vault/`. The
  `slide_layouts` tool searches both.
- A slide with `"archetype": "library", "item": "<id>", "slots": {...}` rebuilds that item as native shapes in the
  deck's theme at render time (`engines/forge/component.py`): pictures, charts, effects and gradients are dropped,
  colours map to theme slots, template text is cleared and the writer's text goes into the slots. No item id, name
  or label is written into the deck. Without the knowledge base the slide's `fallback` is drawn instead.

## Library numbers (2026-11-20)

The vision pass labelled 2,991 items: the `consulting_grade` and `business` tiers plus all full slides, without basic
shapes of 3 nodes or fewer. Two thirds of them (2,082) scored quality 1 or 2: decorative, clip-art or
placeholder-only. 215 scored 4 or 5, and 214 of those are usable as is. They form the golden set:

| Category | Golden items |
|---|---|
| Charts | 44 |
| KPI metrics | 26 |
| Process flows | 26 |
| Matrices | 20 |
| Issue trees | 13 |
| Tables | 13 |
| Everything else | 72 |

Most golden items come from the `consulting_grade` tier (130 of 360) and the `business` tier (70 of 3,730).
