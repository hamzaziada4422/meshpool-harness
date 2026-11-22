---
name: invoice-quote
description: Make an invoice, quote or receipt as Word, PDF or Excel. Use when the user asks for an invoice, a quote, an estimate or a receipt.
---

# Invoice quote

1. Needed: sender, client, items with quantity and price, tax rate, currency, date, due date, payment details. Ask once for what is missing.
2. Build a workbook with make_spreadsheet where totals, tax and the grand total are formulas, or a document with make_document with a table.
3. Number it, date it, and state payment terms. Do the arithmetic with the calculate tool and check it against the file.
4. Open the result with view_output before replying.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
