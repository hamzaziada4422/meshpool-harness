---
name: pdf-tools
description: Merge, split, rotate, extract text from, fill in or compress PDF files. Use when the user has PDFs to combine, cut, read, fill or shrink.
---

# Pdf tools

1. Use Python: pypdf for merge, split, rotate and form fields; pypdfium2 for rendering pages; Ghostscript or PIL re-saving for compression if installed.
2. Never change the original. Write the result next to it with a clear new name.
3. After the change, open the result with view_output and check the page count and that nothing is cut off.
4. Forms: list the fields first (pypdf PdfReader.get_fields()), fill them with PdfWriter.update_page_form_field_values(page, values, auto_regenerate=False) and set NeedAppearances, then render the page with view_output to confirm the values show. Checkboxes use the field's own on value. An XFA or scanned form cannot be filled this way: say so and offer to overlay text at measured positions instead.
5. Scanned pages with no text: run OCR through the read_document tool and say so.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
