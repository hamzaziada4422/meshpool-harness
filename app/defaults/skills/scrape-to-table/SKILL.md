---
name: scrape-to-table
description: Collect information from websites or pages into a spreadsheet. Use when the user wants prices, listings, contacts or any list gathered from the web.
---

# Scrape to table

1. Say the columns you will collect. Use web_search and fetch_url for public pages; use the browser tools only for pages that need a sign-in or clicks.
2. Respect robots.txt and rate limits, collect only public information, and no personal contact details unless the user owns the source.
3. Write a CSV or workbook with the source link in every row. Mark values you could not find as empty, never guessed.
4. Report how many rows, how many sources, and anything that failed.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
