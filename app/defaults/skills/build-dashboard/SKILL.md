---
name: build-dashboard
description: Build a dashboard of numbers as a web page with charts and tables. Use when the user asks for a dashboard, a report page, KPIs or an internal tool.
---

# Build dashboard

1. Read the build-ui skill first. Profile the user's file with a script before designing: row count, columns, types, blanks, duplicates, date formats, odd spellings ("web" and "Web"), money written as "$1,200" or "n/a", outliers. Clean in the script, count what you dropped or fixed, and show that count on the page ("12 rows had no amount and are left out of revenue"). Never silently drop or invent data.
2. Lead with the three to five numbers that matter, then charts, then a table. The headline states the takeaway in words, from the data, not a generic title.
3. One HTML file with the cleaned data embedded as JSON. Charts as inline SVG drawn by small JavaScript; no external libraries or network calls so it works offline. Keep a margin of at least 48 px on the right and 32 px elsewhere inside each SVG chart so the last axis label and value are never cut off.
4. Interactions a reader expects: filter by the main categories and by date range, a reset button, sortable table columns, search, rows per page or a pager, and a CSV download of the filtered rows. Filters change the numbers, the charts and the table together. A filter with no matches shows an empty state, not a blank chart. Hover or focus on a chart mark shows its exact value.
5. Every chart has a title, labelled axes with units, a legend only when needed, colours that pass contrast and no more than five. Numbers are right-aligned and formatted with separators and currency. Dates are in one format. Phone: tables scroll inside their own box, charts shrink without cutting labels.
6. Check the page numbers against the file with a script (total, a group total, the row count). Then run `ui_audit` and `ui_smoke` until clean and `view_output` the screenshots.
7. Reply with the file path, the three main findings from the data, and what you cleaned or left out.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done.
