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
