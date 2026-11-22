---
name: transcribe-audio
description: Turn a meeting recording, podcast, voice note or video into a transcript and a summary. Use when the user gives an audio or video file.
---

# Transcribe audio

1. Check what is available: python3 -c "import faster_whisper" (pip install faster-whisper if missing, and say it downloads a speech model once). ffmpeg is needed for video: pip install imageio-ffmpeg gives a copy that needs no system install.
2. Transcribe on this computer; the audio never leaves it. Use the small model first, base for long files if it is slow.
3. Write the transcript with timestamps every minute, then a summary: decisions, action items with owners, open questions. Mark places the audio was unclear.
4. If the speech model cannot be installed (no network, no space), say so plainly and ask the user for a text transcript. Do not pretend to have listened.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output before you say it is done. Reply with the file path and two sentences on what it is.
