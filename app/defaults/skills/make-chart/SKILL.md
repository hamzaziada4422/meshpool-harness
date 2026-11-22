---
name: make-chart
description: Make a chart or graph from data and check how it looks. Use when the user asks for a chart, a graph, a plot or a visual of numbers.
---

# Make chart

1. Read the data. State the one takeaway the chart should show and use it as the title.
2. Pick the simplest form: line for time, bars for comparing, a table when there are fewer than four numbers. No 3D, no pie with more than four slices.
3. Charts drawn as SVG in a page keep a margin of at least 48 px on the right and 32 px on every other side inside the drawing, so the last label is never cut off; ui_audit reports it when it is.
3. Draw it with matplotlib (PNG, 1600 px wide, labelled axes, units, at most five colours, readable text) or as a native chart in make_spreadsheet or make_deck when the user wants it in Excel or slides.
4. Open the PNG with view_output. Fix overlapping labels or clipped text and look again.
5. Reply with the path and the takeaway.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
