---
name: pivot-table
description: Summarise data into a pivot table or cross-tab, in Excel or as a page. Use when the user asks for a pivot table, a summary by group, or totals by month and category.
---

# Pivot table

1. Read the data and name the rows, the columns and the value (sum, count, average) in one line before building.
2. Compute it with pandas (pivot_table with margins=True for totals). Check the grand total against the raw data.
3. In Excel: build the workbook with make_spreadsheet with the raw data on one sheet and the summary on another using live SUMIFS or COUNTIFS formulas that point at the raw sheet, so it updates when the data changes. State plainly that it is a formula summary, not a refreshable pivot object.
4. For a page, use the build-dashboard skill. For a chart of it, use make-chart.
5. Open the result with view_output before replying.

Rules: Write in plain sentences, no em dashes. Reply with the file path and the three biggest numbers.
