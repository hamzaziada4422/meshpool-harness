# House style

The rules every Forge deck follows, taken from two consulting deck skills (a BCG-style design system and a
McKinsey-style clarity method) and built into the engine. A rule lives in one of three places: the writer's prompt
(the model is told), the renderer (the layout makes it true), or a validator (the deck fails until it holds).

## Storyline

| Rule | Where |
|---|---|
| Answer first: the governing thought on slide 2, then 2-4 sections, each one claim its slides prove | storyline prompt (`build.VISUALS`, `STORY`) |
| The titles, read in order, tell the whole story | prompt (`build.CONSULTING`); P12 checks titles do not repeat |
| One message per slide; a title that needs "and" is two slides | prompt |
| The last slide is the decision and next steps, each with an owner and a date | prompt; closing archetype; fill check |
| The brief's slide count is the count; "no cover" means no cover | build (`narrative_n`, `no_cover`); P16 |

## Titles

| Rule | Where |
|---|---|
| A full-sentence takeaway, not a topic label | prompt; P11 (a verb) |
| 15 words or fewer, two lines at most, no full stop, sentence case, no em dashes | prompt; P11 (15 words, two lines) |
| The number in the title when the point is a number | prompt |

## Visuals

| Rule | Where |
|---|---|
| Every slide has an exhibit; a slide of bullets is a failed slide | prompt; a words-only grid becomes an argument with a visual per block (`parts.words_only_to_argument`) |
| The visual follows the message: ranking -> bar, trend -> column or line, sequence -> process, plan -> roadmap, parallel items -> equal cards, results -> big numbers | storyline prompt (`VISUALS`); `story_variety` |
| Never the same visual on two slides in a row, and never the same look | `story_variety`; the renderer plans forms across the deck (`render.plan_forms`: rows next to a table become icon columns or a numbered process, chart runs alternate a side column and a row of cards, chart grids mirror); validator P15 `same_look` |
| A big number is shown once | `build.variety_checks` |
| One emphasised element per exhibit, the rest muted; values labelled directly | prompt; chart renderer (`highlight_categories`) |
| Points are drawn as cards with an icon by meaning and a 2-4 word header; four short points make a 2x2 | renderer (`render.draw_points`) |
| Zero pictures: icons and logos are native shapes | renderer; P9 |

## Composition and spacing

| Rule | Where |
|---|---|
| One exhibit across the page with its so-what under it is the norm; an argument beside it only when it has 3-4 points | prompt; table slides run full width (`render.SPLIT_TABLES` off) |
| Blocks side by side share their top and bottom edges | renderer (grid parts share one height; side columns end at the exhibit's foot); validator P20 `unaligned_feet` |
| No block of white in the body | renderer (rows of next steps and risk tables spread to the foot on alternate light bands; timelines with room get a card per milestone); validator P20 `dead_space` and `thin` |
| One grid, one margin, one footer on every content slide | template and renderer; P7, P8 |
| A white top: the section number in the accent colour top right, an accent rule under the title, Segoe UI | renderer `_header` (the dark band only with `frame: band`) |
| Never an eyebrow: no small label over a title | renderer `_header`; `test_never_an_eyebrow_over_the_title` |
| Tables: words left, numbers right, the recommended row in accent text between accent rules, no fill; a cell that only says the sources are silent reads n/a | renderer `_table`, `_cell` |
| Charts: one accent bar, the context in light grey, slim bars | renderer `_chart` |
| Columns of points share bands: leads, then detail from one line, then owner and timing on one bottom line | `compose._columns` |
| Body text 14 pt (balanced density), 12 pt at least in cards and 13 pt in the executive summary; detail moves to the notes before type shrinks | `density.SCALES`, `render.draw_points`; P5 |
| Rows and cards that stand for things carry an icon by meaning (or numbers for the whole slide) | `compose.meaning_icon`; executive summary, cards, risk columns |
| Every data slide has a source line; every number traces to a fact | renderer; P13 |

When the content is too thin to fill a slide (a roadmap of four one-line milestones), the renderer does not pad it:
the slide fails P20 and the writer is asked for the missing substance (`build.fill_checks`).
