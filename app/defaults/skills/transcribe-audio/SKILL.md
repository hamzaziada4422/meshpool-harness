---
name: transcribe-audio
description: Turn a meeting recording, podcast, voice note or video into a transcript and a summary. Use when the user gives an audio or video file.
---

# Transcribe audio

1. Speech to text ships inside Meshpool and runs on this computer: the audio never leaves it, and nothing is downloaded or installed. Run `python3 transcribe.py "<file>" --out "<transcript path>.txt"` from this skill's folder (the Folder line above; use `python` if there is no `python3`). It takes audio (mp3, m4a, wav, flac, ogg) and video (mp4, mov, mkv, webm) directly, detects the language, and prints the transcript with a time stamp every minute. Add `--language xx` (en, fr, ar ...) when the language is known and the guess is wrong.
2. A long file takes a while (about a quarter of its length on an ordinary laptop). Say so before you start, and run it with a long timeout or in the background and read the output when it ends.
3. Check the text: names and numbers the model heard wrongly, and places it marked as silence. Mark passages that were unclear instead of guessing.
4. Then write the summary: decisions, action items with owners, open questions.
5. If the script says speech to text is not available (exit code 3), this is a terminal install without the speech model: say so plainly, and ask the user for a text transcript. Do not pretend to have listened, and do not try to install or download a model.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output before you say it is done. Reply with the file path and two sentences on what it is.
