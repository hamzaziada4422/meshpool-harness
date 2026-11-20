# Forge knowledge base: decisions

Choices made where the plan left room, with the reason. Newest last.

1. **Branches.** Git cannot hold both `forge-kb` and `forge-kb/...`, so the stacked branches are `forge-kb` (audit and
   baseline), then `forge-kb-2-kb`, `forge-kb-3-...` and so on, each based on the one before. All are based on `main`.
2. **Where the knowledge base lives.** Cloned to `/home/user/slide-templates`, rebuilt and unzipped to
   `~/meshpool_kb/`; `MESHPOOL_KB=~/meshpool_kb/slide_engine_kb`, written to the gitignored `.env.kb`. All three
   checksums passed; `components.db` holds 8,523 items; 20 random library files open in python-pptx.
3. **Knowledge base files are untrusted data.** Its prototype scripts (`engine/*.py`) are read for ideas, never run.
   Python that reads it runs with `-I` from scripts outside the folder. Its own `docs/CLAUDE_CODE_PROMPT.md` is
   ignored; the user's prompt is the spec.
4. **The user's uploads stay out.** `library/examples_uploaded` (1,271 items cut from the user's own files) is left out
   of every committed number, label, doc and golden set.
5. **Evaluation without a model.** See `FORGE_KB_BLOCKERS.md`. The 12 briefs' slide JSON was written once, before
   any engine change, and is frozen. Dev split 01, 02, 03, 04, 06, 07, 09, 11; test split 05, 08, 10, 12. The test
   JSON is never edited after this commit.
6. **New archetypes cannot be in the baseline JSON**, because they do not exist yet. They are judged by: renders short,
   normal and long content; passes P0 to P20; looks right on the page. Not as a polish gain.
7. **LibreOffice in a source checkout.** Tests and the eval need `MH_ALLOW_SYSTEM_SOFFICE=1`; without it the render
   rules report a missing LibreOffice.
8. **pymupdf** (AGPL) is used only by the dev tool `measure_pdf.py`, as before this work. It is not bundled.
9. **Reference page labels by sampling.** 6 content pages per deck (pages with at least 25 words, not the first or
   last page), 777 pages from 130 decks, 33 contact sheets of 24, labelled by two vision subagents with a fixed list
   of layouts. Labels stay in the scratchpad; only counts are committed.
10. **Archetype ranking** weighs what the dev briefs needed and could not express first, then reference page and
    library frequency (`docs/results/2026-11-20-forge-kb-baseline.md`).
11. **Vision relabel scope.** 2,991 items: the `consulting_grade` and `business` tiers and all full slides, minus basic
    shapes with 3 nodes or fewer (deterministic prefilter). Five vision subagents labelled 125 contact sheets of 24.
    Every (sheet, cell) has exactly one label, checked after merging. Two agents shared a helper script mid-run;
    rows were moved back by their owners, and two batches rebuilt their `usable` flags from notes, so `usable` is
    slightly less reliable than `quality` on sheets 77 to 124. Labels for quality 5 were spot-checked on a contact
    sheet.
12. **Golden set size.** The plan asked for 400 to 600 items of quality 4 or 5. The library has 215 at that grade, 214
    usable, so the golden set is 214. Quality 3 items were not added to reach the count.
13. **Near duplicates without open_clip.** Image fingerprints are a 64-bit difference hash, a 4x4x4 colour histogram
    and an 8x6 ink grid (numpy and PIL only, no new dependency, no model download). Two items are near duplicates
    when the hash differs by at most 6 bits and the fingerprints' cosine is at least 0.97.
14. **Search.** Plain words are matched against the category, the vision label, the title and the item text; an item
    must match at least half the words, more matches first, then golden, quality and score. The original FTS5 index
    is contentless and stays unused by `search`.
15. **Ingest** writes into `labels.sqlite` (table `ingested`), not into the original `components.db`, so the original
    index is never modified. One-slide files are made by dropping the other slides with python-pptx, which saves only
    the parts still reachable. The prototype scripts were read for ideas, not copied.
16. **Zero images (user request, 2026-11-20).** Icons and SVG logos are native vector shapes; P9 fails any picture
    (`pptx.images.allow_pictures: false`). Raster logos are left out unless a deck allows pictures. Decided pending
    the user's answer on logos.
17. **Band frame as an option, not the default.** The default McKinsey-style grammar is unchanged; the band frame
    (`theme: {"frame": "band"}`) follows the user's BCG benchmark with our own colours and fonts.
18. **Library components are local only.** They need MESHPOOL_KB and never put an item id, name or label into a deck.
    Whether other users get them depends on licences (asked).
19. **One pull request for steps B to G and the new look.** The changes interlock in render.py and parts.py; splitting
    them after the fact would leave intermediate states untested. The PR lists each change with its tests.
