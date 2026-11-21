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
20. **Logos keep their colours (user request, 2026-11-20).** A logo is fetched, never drawn by hand: the full-colour
    logo from gilbarbara/logos (CC0-1.0) first, else the Simple Icons mark (CC0-1.0, pinned 13.21.0) filled with the
    brand hex from Simple Icons' data. Each colour becomes one native shape in a group marked `logo:<company>`; P6
    skips those shapes. With no network or no mark the company's name is shown. Tests set `MH_FETCH_LOGOS=0`.
21. **Built-in layouts are abstract and gated.** `data/layouts.json` keeps preset names, boxes in 1/200 units, theme
    roles and slot descriptions only. A layout is offered to the model only when it passes P1, P5, P7, P8, P17 and P20
    with filled slots and no cut text (`testkit/forge_layout_gate.py`, 50 of 91). A deck that names a held-back layout
    still renders it.
22. **The brief's count and bookends win over the model's plan.** The narrative floor is 1 slide, not 3, and a cover
    the model plans is dropped when the brief says there is none. A silent brief keeps the default cover.
23. **Points are drawn, not listed (user request, 2026-11-20).** The renderer, not the model, turns an argument
    column of 2-4 points into cards (`render.draw_points`): an icon per point from the meaning named first in its
    text, a header (`label`, 2-4 words, asked of the writer for exhibit_argument), and four one-line points as a 2x2.
    Cards are overlays (`ov_PointCard`) inside the panel; their text is checked like any text (fit, provenance).
    When the points do not fit at 10 pt the column stays text. `render.POINTS["on"] = False` turns it off.
24. **No gaps, splits are the exception (user request, 2026-11-20).** A short table no longer sits beside a shorter
    panel: it runs full width with its so-what under it (`render.SPLIT_TABLES` off). Next steps and risk rows share
    the body on alternate light bands; grid parts side by side share one height; a side column of points ends at
    the exhibit's foot. Where the content is too thin to fill a slide (a roadmap of one-line milestones) the
    renderer does not pad: P20 flags it and the writer is asked for the detail.
25. **Forms are planned per deck, not per slide (user request, 2026-11-20).** The renderer decides each slide's
    composition knowing its neighbours (`render.plan_forms`). The validator measures looks from the file (P15
    `same_look`: rows of text across the page, a chart with a column beside it, a chart above a row of cards), so a
    repeat that the plan cannot avoid goes back to the writer. Pixel similarity was tried and rejected: look-alike
    pairs scored near zero on it.
26. **White top by default (user choice, 2026-11-20).** Content slides keep a white top in the SlideWorks manner: the
    section number (in order of first appearance) large in the accent colour in the top right corner, an accent
    rule under the title, no hairline across the top, and the exhibit + argument panel starts under the title. The
    dark band stays an option (`theme: {"frame": "band"}`). Segoe UI stays the default face.
27. **Never an eyebrow (user rule, 2026-11-20).** No small label over a slide title, ever ("What to do next" above
    the title was tried and rejected). Tested in `test_forge_house_style.py`.
