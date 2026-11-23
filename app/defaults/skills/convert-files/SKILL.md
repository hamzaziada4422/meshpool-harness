---
name: convert-files
description: Convert files between formats: Word, PDF, Excel, CSV, Markdown, HTML, images, audio and video. Use when the user asks to convert or export something.
---

# Convert files

Everything below ships inside Meshpool and is already on PATH or importable. Do not pip install, apt install, brew install or download anything.

1. Office to PDF or between Office formats: LibreOffice headless (`soffice --convert-to`). Documents to and from Markdown, HTML, Word (docx), EPUB, RST, plain text: `pandoc` (for example `pandoc in.docx -o out.md`, `pandoc in.md -o out.docx`). PDF as a target from Markdown or HTML: write a docx or HTML with pandoc, then LibreOffice to PDF. Data: pandas (CSV, Excel, Parquet, JSON). Images: PIL, with `from pillow_heif import register_heif_opener; register_heif_opener()` first for iPhone .heic photos. Audio and video: `ffmpeg` (for example `ffmpeg -i in.mov -vn out.mp3`, `ffmpeg -i in.mkv -c:v libx264 out.mp4`); it has no ffprobe, so read a file's streams with `ffmpeg -i file`.
2. If `ffmpeg` is not found on an Apple silicon Mac, that Mac runs macOS 11 and the app's ffmpeg needs macOS 12: say that plainly (Meshpool's Settings, System, shows it). Images, documents and speech to text still work. For anything else missing, say which tool it is and offer the closest alternative that works now.
3. Keep the original. Open the result with view_output when it is visual and check it.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output (or ui_audit for web pages) before you say it is done. Reply with the file path and two sentences on what it is.
