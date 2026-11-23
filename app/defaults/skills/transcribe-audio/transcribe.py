"""Transcribe an audio or video file on this computer with the speech model that ships in Meshpool.

  python3 transcribe.py FILE [--out transcript.txt] [--language en] [--no-vad]

Works offline: faster-whisper loads the model from MESHPOOL_WHISPER_MODEL (set by the app), and PyAV (inside the
package) decodes the audio, so a video file needs no ffmpeg. Prints the transcript with a time stamp at the start of
every minute (or at a long pause), plus the detected language, and writes the same text to --out when given.
Exit codes: 0 done, 2 bad arguments or file, 3 speech to text is not available here (the message says why).
"""
import argparse
import os
import sys
import time


def stamp(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--out", help="also write the transcript to this file")
    ap.add_argument("--language", help="two-letter code such as en, fr, ar; default: detect it")
    ap.add_argument("--no-vad", action="store_true", help="do not skip silence (use if speech is cut off)")
    a = ap.parse_args()
    if not os.path.isfile(a.file):
        print(f"No such file: {a.file}", file=sys.stderr)
        return 2
    model_dir = os.environ.get("MESHPOOL_WHISPER_MODEL", "")
    if not (model_dir and os.path.isfile(os.path.join(model_dir, "model.bin"))):
        print("Speech to text is not available here: the speech model is not set up (MESHPOOL_WHISPER_MODEL). "
              "The Meshpool desktop app includes it. Ask the user for a text transcript instead.", file=sys.stderr)
        return 3
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        print(f"Speech to text is not available here: faster-whisper cannot be loaded ({e}). "
              "Ask the user for a text transcript instead.", file=sys.stderr)
        return 3
    t0 = time.time()
    model = WhisperModel(model_dir, device="cpu", compute_type="int8")
    try:
        segments, info = model.transcribe(a.file, language=a.language, vad_filter=not a.no_vad, beam_size=5)
        lines, last_mark = [], -60.0
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            if s.start - last_mark >= 60 or not lines:
                lines.append(f"\n[{stamp(s.start)}]")
                last_mark = s.start
            lines.append(text)
            print(f"  ... {stamp(s.end)} of {stamp(info.duration)}", file=sys.stderr, flush=True)
    except Exception as e:  # noqa: BLE001  a file with no audio track, or a format PyAV cannot read
        print(f"Could not read audio from {a.file}: {e}", file=sys.stderr)
        return 2
    body = " ".join(lines).replace(" \n", "\n").strip()
    head = (f"Language: {info.language} ({info.language_probability:.0%}). Length {stamp(info.duration)}. "
            f"Transcribed in {time.time() - t0:.0f} s on this computer.\n")
    if not body:
        head += "No speech was found in this file.\n"
    out = head + "\n" + body + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {a.out}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
