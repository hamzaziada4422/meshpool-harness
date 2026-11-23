---
name: transcribe-audio
description: Turn a meeting recording, podcast, voice note or video into a transcript and a summary. Use when the user gives an audio or video file.
---

# Transcribe audio

1. Speech to text ships inside Meshpool and runs on this computer: the audio never leaves it, and nothing is downloaded or installed. Stay in the project folder and call the script by its full path, which is the Folder line above plus `transcribe.py`: `python3 "<Folder>/transcribe.py" "<audio or video file>" --out "Meshpool outputs/<name> transcript.txt"` (`python` if there is no `python3`). Do not cd into the skill's folder: the file names you pass would then point at the wrong place. It takes audio (mp3, m4a, wav, flac, ogg) and video (mp4, mov, mkv, webm) directly, detects the language, and prints the transcript with a time stamp every minute. Add `--language xx` (en, fr, ar ...) when the language is known and the guess is wrong.
2. Speed is about a quarter of the recording's length on an ordinary laptop, and a foreground command is cut off after 15 minutes. So for anything longer than about 20 minutes, start it with bash_start, say how long it will take, check on it with bash_output, and read the `--out` file when it ends. Shorter files can run in the foreground.
3. Check the text: names and numbers the model heard wrongly, and places it marked as silence. Mark passages that were unclear instead of guessing.
4. Then write the summary: decisions, action items with owners, open questions.
5. If the script says speech to text is not available (exit code 3), this is a terminal install without the speech model: say so plainly, and ask the user for a text transcript. Do not pretend to have listened, and do not try to install or download a model.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output before you say it is done. Reply with the file path and two sentences on what it is.
