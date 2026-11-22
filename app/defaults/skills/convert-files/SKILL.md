---
name: convert-files
description: Convert files between formats: Word, PDF, Excel, CSV, Markdown, HTML, images, audio and video. Use when the user asks to convert or export something.
---

# Convert files

1. Office to PDF or between Office formats: LibreOffice headless (soffice --convert-to). Documents to Markdown or HTML: pandoc if present, else python-docx. Data: pandas. Images: PIL. Audio and video: ffmpeg if present.
2. If a tool is missing, say which one and offer the closest alternative that works now.
3. Keep the original. Open the result with view_output when it is visual and check it.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
