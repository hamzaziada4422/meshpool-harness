---
name: cite-sources
description: Find and format citations and a bibliography (APA, MLA, Chicago, BibTeX) from DOIs, titles or links. Use when someone asks to cite, reference or build a bibliography.
---

# Cite sources

1. For each item find the metadata from the source itself: for a DOI fetch https://api.crossref.org/works/<doi> with fetch_url; for a title search Crossref with query.bibliographic; for a web page read title, author and date from the page.
2. Never guess an author, year or page number. Mark a missing field as missing and say so.
3. Produce the style asked for, and a .bib file when they use LaTeX or Zotero (Zotero imports .bib and .ris).
4. Open each link once to confirm it exists. List any that failed.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output before you say it is done. Reply with the file path and two sentences on what it is.
