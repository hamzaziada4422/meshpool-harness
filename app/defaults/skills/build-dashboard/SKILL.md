---
name: build-dashboard
description: Build a dashboard of numbers as a web page with charts and tables. Use when the user asks for a dashboard, a report page, KPIs or an internal tool.
---

# Build dashboard

1. Read the build-ui skill first. Lead with the three to five numbers that matter, then charts, then a table.
2. One HTML file with the data embedded as JSON from the user's real file. Charts as inline SVG drawn by small JavaScript; no external libraries or network calls so it works offline.
3. Every chart has a title that states the takeaway, labelled axes and a table fallback. Numbers are right-aligned and formatted.
4. Run ui_audit until it reports no problems, then view_output the screenshots.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
