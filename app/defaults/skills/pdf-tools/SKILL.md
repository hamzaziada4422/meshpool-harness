---
name: pdf-tools
description: Merge, split, rotate, extract text from, fill in or compress PDF files. Use when the user has PDFs to combine, cut, read, fill or shrink.
---

# Pdf tools

1. Use Python (all of these ship inside Meshpool; never pip install them): pypdf for merge, split, rotate, form fields and compression (`page.compress_content_streams()`, `writer.compress_identical_objects()`, and `page.images` with `replace(img, quality=60)` to shrink pictures); pypdfium2 for rendering pages to images; pdfminer.six for text with positions; reportlab for stamping or overlaying text.
2. Never change the original. Write the result next to it with a clear new name.
3. After the change, open the result with view_output and check the page count and that nothing is cut off.
4. Forms: list the fields first (pypdf PdfReader.get_fields()), fill them with PdfWriter.update_page_form_field_values(page, values, auto_regenerate=False) and set NeedAppearances, then render the page with view_output to confirm the values show. Checkboxes use the field's own on value. An XFA or scanned form cannot be filled this way: say so and offer to overlay text at measured positions instead.
5. Scanned pages with no text: read_document tries to read them with OCR when this install has it and says so when it does not. If it says OCR is not included, render the page with pypdfium2 and look at it with view_output (you can read the picture yourself), and tell the user the text was read from the picture, not extracted.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
