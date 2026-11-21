# Reference deck measurements

These numbers calibrate the Forge deck engine against public consulting and financial services decks. Only numbers are kept in the repo. The distributions are in `app/engines/forge/data/reference_stats.json`. No deck titles, file names or page text are stored.

## What was measured

We measured 132 PDF decks. 2 decks had no page with both a title and enough text (one has only two pages), so 130 decks are in the statistics: 73 consulting and 57 financial services. No deck timed out.

The decks hold 4965 content pages. To bound the time we took at most 60 content pages per deck, evenly spaced. That gives 3655 measured pages. Large decks are cut down more than small ones, so a deck with 60 pages counts the same as a deck with 600.

## Method

The tool is `app/engines/forge/measure_pdf.py` with the `--extended` flag. It reads text spans, vector drawings and images with pymupdf.

- A content page has a title and at least 15 words, or a raster exhibit that covers more than 30% of the page.
- The title is the largest text in the top 22% of the page, at least 14 pt. Title size is scaled to a 13.333 in wide slide, because page sizes vary.
- Title lines are the distinct baselines in the title. Body words are words larger than 9.5 pt that are not in the title.
- Margins are the extents of all text, visible drawings and images. Full page backgrounds are ignored. A full bleed band or image gives a margin of zero, so the text margins are also given.
- The empty block is the largest empty rectangle in the band from 20% to 90% of the page height. It is measured on a rendered bitmap at 40 dpi or less. Near white pixels count as empty. Light grey panels count as empty too, as in the P20 rule. White space inside a plot also counts as empty.
- Colours are text, fill and stroke colours at 4 bits per channel, without near white and near black. Greys count.
- The chart flag is a heuristic. It needs at least 4 filled rectangles with a common baseline and varying length, or a long stroked polyline, and few words inside the region.
- The table flag is a heuristic. It needs at least 3 parallel horizontal rules in the body, or a text grid of at least 3 columns and 4 rows.

## Caveats

- Many exhibits are raster images. Text inside them is invisible to the tool, so word counts, source line shares and the verb share are lower bounds on those pages. The raster share is given so you can judge this.
- Raster charts and tables are not detected. Pie and donut charts are missed. The chart and table shares are lower bounds.
- The verb share uses a word list and only covers English decks.
- A deck can use more than one page size. Inch values are not comparable across pages without the fractions, which are in the JSON file.
- Pages are pooled. A few large firms with many decks weigh more than small firms. The per deck columns for shares show the spread between decks.
- Each cell below is the median, with the 10th and 90th percentile in brackets.

## All, consulting and financial services

| Metric | All | consulting | financial services |
|---|---|---|---|
| Title size, pt on a 13.333 in slide | 28 (18.2 to 40) | 25.1 (18 to 44.1) | 28 (20.1 to 38) |
| Title lines | 1 (1 to 2) | 2 (1 to 2) | 1 (1 to 2) |
| Title words | 6 (2 to 18) | 8 (2 to 24) | 5 (2 to 12) |
| Body words | 84 (15 to 250) | 97 (14 to 266) | 77 (15 to 240) |
| Largest empty block, share of body | 0.1 (0.04 to 0.27) | 0.09 (0 to 0.24) | 0.11 (0.05 to 0.3) |
| Font sizes per page | 6 (3 to 9) | 5 (3 to 8) | 6 (3 to 9) |
| Colours per page | 5 (1 to 10) | 4 (1 to 10) | 5 (2 to 11) |

### Margins

| Metric | All | consulting | financial services |
|---|---|---|---|
| Left margin, in | 0.39 (0 to 0.76) | 0.4 (0 to 1.03) | 0.39 (0 to 0.52) |
| Right margin, in | 0.34 (0 to 0.77) | 0.24 (0 to 1.1) | 0.39 (0 to 0.53) |
| Top margin, in | 0.3 (0 to 0.54) | 0.3 (0 to 0.7) | 0.3 (0 to 0.53) |
| Bottom margin, in | 0.19 (0 to 0.38) | 0.24 (0 to 0.57) | 0.18 (0 to 0.34) |
| Left text margin, in | 0.5 (0.27 to 0.85) | 0.55 (0.36 to 1.09) | 0.48 (0.27 to 0.63) |
| Right text margin, in | 0.5 (0.22 to 1.14) | 0.57 (0.24 to 1.89) | 0.47 (0.21 to 0.76) |
| Top text margin, in | 0.42 (0.2 to 0.76) | 0.47 (0.14 to 1.04) | 0.4 (0.2 to 0.54) |
| Bottom text margin, in | 0.25 (0.07 to 0.63) | 0.3 (0.08 to 0.83) | 0.22 (0.07 to 0.36) |
| Left margin, share of width | 0.029 (0 to 0.055) | 0.027 (0 to 0.081) | 0.03 (0 to 0.046) |
| Top margin, share of height | 0.04 (0 to 0.071) | 0.035 (0 to 0.087) | 0.043 (0 to 0.071) |

### Shares of pages

The bracket for shares is the 10th to 90th percentile of the per deck share, in percent.

| Metric | All | consulting | financial services |
|---|---|---|---|
| Pages with a vector chart | 20% (0 to 45) | 19% (0 to 47) | 20% (0 to 38) |
| Pages with a table | 30% (2 to 54) | 26% (0 to 50) | 34% (14 to 56) |
| Pages with a source line | 23% (0 to 80) | 34% (0 to 88) | 16% (0 to 45) |
| Titles with a verb, English decks | 51% (15 to 85) | 59% (12 to 92) | 45% (20 to 74) |
| Pages led by a raster exhibit | 19% (0 to 58) | 27% (0 to 72) | 13% (0 to 38) |

## Main firms

Firms with at least 5 decks. Other firms are in the JSON file.

| Metric | BCG | Citi | Goldman Sachs | LEK | Allianz | Elixirr | Roland Berger | Simon-Kucher | Accenture | Bain | EY | IBM IBV |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Title size, pt on a 13.333 in slide | 25.6 (16.6 to 48.4) | 28 (28 to 32) | 22 (19 to 29.3) | 18 (18 to 18) | 38 (26 to 46.6) | 24.6 (20 to 40.9) | 33.2 (28.6 to 72.8) | 18.2 (18.1 to 29.1) | 26.7 (17.5 to 32) | 24 (22 to 28.2) | 32 (20.5 to 88) | 20.7 (19.8 to 38.7) |
| Title lines | 2 (1 to 3) | 1 (1 to 2) | 1 (1 to 2) | 2 (1 to 3) | 1 (1 to 1) | 1 (1 to 2) | 2 (1 to 2) | 2 (1 to 3) | 1 (1 to 2) | 1 (1 to 2) | 1 (1 to 2) | 2 (1 to 3) |
| Title words | 10 (1 to 20) | 6 (1 to 13) | 5 (2 to 10) | 22 (7 to 30) | 5 (2 to 8) | 4 (1 to 19) | 12 (2 to 19) | 11 (1 to 27) | 7 (2 to 11) | 11 (2 to 20) | 5 (1 to 15) | 5 (2 to 12) |
| Body words | 102 (35 to 202) | 108 (40 to 394) | 46 (0 to 110) | 141 (36 to 273) | 86 (23 to 276) | 174 (7 to 333) | 95 (9 to 185) | 92 (30 to 206) | 59 (0 to 196) | 146 (26 to 353) | 95 (29 to 610) | 81 (0 to 236) |
| Largest empty block, share of body | 0.09 (0.01 to 0.17) | 0.1 (0.06 to 0.25) | 0.07 (0 to 0.18) | 0.07 (0.04 to 0.17) | 0.12 (0.09 to 0.35) | 0.12 (0 to 0.3) | 0.1 (0.05 to 0.16) | 0.09 (0.03 to 0.18) | 0.11 (0 to 0.24) | 0.09 (0.05 to 0.35) | 0.01 (0 to 0.07) | 0.19 (0 to 0.39) |
| Font sizes per page | 6 (3 to 8) | 6 (3 to 10) | 5 (3 to 8) | 6 (4 to 7) | 6 (4 to 9) | 6 (4 to 9) | 6 (4 to 8) | 6 (4 to 8) | 3 (2 to 12) | 6 (3 to 8) | 5 (3 to 8) | 5 (3 to 6) |
| Colours per page | 5 (2 to 8) | 3 (1 to 6) | 6 (2 to 14) | 7 (3 to 11) | 6 (3 to 12) | 5 (2 to 10) | 3 (1 to 5) | 7 (4 to 12) | 2 (1 to 7) | 5 (1 to 10) | 4 (2 to 12) | 2 (0 to 6) |

### Margins by firm

| Metric | BCG | Citi | Goldman Sachs | LEK | Allianz | Elixirr | Roland Berger | Simon-Kucher | Accenture | Bain | EY | IBM IBV |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Left margin, in | 0.27 (0 to 0.83) | 0.31 (0.01 to 0.35) | 0.46 (0 to 0.63) | 0 (0 to 0) | 0.51 (0 to 0.52) | 1.04 (0 to 1.16) | 0.78 (0 to 0.81) | 0 (0 to 0.7) | 0.42 (0 to 0.78) | 0.64 (0.5 to 1) | 0.53 (0 to 0.66) | 0 (0 to 0.62) |
| Right margin, in | 0.1 (0 to 1) | 0.31 (0.25 to 0.33) | 0.46 (0 to 0.63) | 0 (0 to 0) | 0.52 (0.34 to 0.53) | 0.21 (0 to 1.11) | 0.43 (0.24 to 0.56) | 0 (0 to 0.44) | 0.42 (0 to 0.52) | 0.12 (0.12 to 1) | 0.24 (0 to 0.66) | 0.58 (0 to 0.63) |
| Top margin, in | 0 (0 to 0.8) | 0.31 (0.01 to 0.35) | 0.26 (0.14 to 0.28) | 0.47 (0.22 to 0.77) | 0.51 (0 to 0.53) | 0.52 (0.23 to 0.9) | 0.24 (0 to 0.53) | 0 (0 to 0.52) | 0.44 (0 to 0.5) | 0.14 (0 to 0.62) | 0.26 (0 to 0.36) | 0.11 (0 to 0.61) |
| Bottom margin, in | 0.25 (0 to 0.79) | 0.07 (0 to 0.09) | 0.17 (0 to 0.95) | 0.32 (0.14 to 0.34) | 0.34 (0.18 to 0.38) | 0.19 (0 to 0.99) | 0.01 (0 to 0.71) | 0 (0 to 0.14) | 0.07 (0 to 0.29) | 0.15 (0.15 to 0.34) | 0.33 (0 to 0.37) | 0.12 (0 to 0.34) |

### Shares of pages by firm

| Metric | BCG | Citi | Goldman Sachs | LEK | Allianz | Elixirr | Roland Berger | Simon-Kucher | Accenture | Bain | EY | IBM IBV |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Pages with a vector chart | 29% (12 to 52) | 27% (10 to 46) | 11% (0 to 35) | 28% (8 to 42) | 20% (12 to 27) | 11% (0 to 15) | 16% (4 to 28) | 47% (42 to 50) | 8% (0 to 65) | 26% (0 to 36) | 11% (2 to 35) | 4% (0 to 18) |
| Pages with a table | 20% (6 to 36) | 27% (12 to 40) | 25% (14 to 36) | 38% (21 to 54) | 26% (10 to 40) | 39% (8 to 51) | 31% (7 to 41) | 36% (21 to 45) | 29% (4 to 80) | 26% (0 to 34) | 17% (7 to 31) | 12% (2 to 26) |
| Pages with a source line | 76% (37 to 96) | 18% (0 to 25) | 1% (0 to 2) | 74% (60 to 85) | 3% (2 to 5) | 2% (0 to 4) | 62% (0 to 93) | 39% (1 to 68) | 6% (0 to 9) | 48% (6 to 53) | 7% (0 to 16) | 2% (0 to 17) |
| Titles with a verb, English decks | 66% (47 to 82) | 61% (41 to 73) | 58% (50 to 74) | 80% (71 to 96) | 33% (23 to 42) | 38% (20 to 57) | 68% (24 to 94) | 72% (54 to 89) | 49% (0 to 67) | 61% (0 to 75) | 36% (15 to 73) | 46% (31 to 56) |
| Pages led by a raster exhibit | 18% (0 to 52) | 6% (0 to 20) | 8% (0 to 35) | 12% (0 to 21) | 3% (0 to 7) | 9% (0 to 20) | 8% (0 to 18) | 45% (25 to 63) | 51% (36 to 100) | 7% (0 to 8) | 69% (3 to 91) | 29% (18 to 36) |

## Counts

- all: 130 decks, 3655 pages
- consulting: 73 decks, 1530 pages
- financial services: 57 decks, 2125 pages
- BCG: 8 decks, 143 pages
- Citi: 8 decks, 175 pages
- Goldman Sachs: 8 decks, 166 pages
- LEK: 8 decks, 220 pages
- Allianz: 7 decks, 414 pages
- Elixirr: 6 decks, 103 pages
- Roland Berger: 6 decks, 99 pages
- Simon-Kucher: 6 decks, 121 pages
- Accenture: 5 decks, 51 pages
- Bain: 5 decks, 113 pages
- EY: 5 decks, 132 pages
- IBM IBV: 5 decks, 125 pages
