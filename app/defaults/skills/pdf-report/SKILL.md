---
name: pdf-report
description: Write a short, well-structured report from files, data or research and save it as a PDF (and Word if asked).
---

# PDF report

1. Gather the material: read the files the user named (read_file or read_document handles PDF, Word, PowerPoint, Excel, CSV and Power BI) and search the web only if the user asked for outside sources.
2. Outline first: a one-paragraph summary with the answer, then three to six sections, each with a heading that states its point.
3. Write in plain language: short paragraphs, numbers with units and dates, tables for comparisons. Cite web sources with links.
4. Save it with make_pdf (filename and title from the topic). For a chart, put a ```chart block in the Markdown (JSON with type bar, line or pie, title, labels and values, or series for several), or embed a picture you made with ![caption](chart.png); never draw a chart with text characters. If the user also wants Word, save the same Markdown with make_document.
5. Reply with the file path and the summary paragraph.
