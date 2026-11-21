"""Writing styles: how Meshpool words its answers, chosen per chat.

Four presets ship with the app (Normal adds nothing). The user adds their own styles: a name and an instruction, or a
pasted writing sample that one cheap model call turns into an instruction. Custom styles and the default style live in
styles.json in the Meshpool data folder. A chat keeps its own choice in its meta.json (key "style"); a chat that never
chose follows the default.

The chosen style rides with every user message as a hidden note, after the separator the chat never shows (see
ui/agent_api.py Session._body), so it works the same for the built-in agents, Pi, Hermes and every ACP agent.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import config as C

NORMAL = "normal"
SEP = "\n\n​"                                  # notes start with this: the chat shows nothing after it
MAX_NAME, MAX_INSTRUCTION, MAX_SAMPLE, MAX_CUSTOM = 40, 2000, 6000, 40

PRESETS: list[dict] = [
    {"id": "normal", "name": "Normal", "preset": True, "instruction": "",
     "desc": "No extra instruction. Meshpool answers the way it usually does."},
    {"id": "concise", "name": "Concise", "preset": True,
     "desc": "Shorter, direct answers.",
     "instruction": "Keep replies short and direct. Lead with the answer or the result, then add only the detail that "
                    "changes what the reader does next. Leave out preamble, restating the question and closing "
                    "summaries, and use a list only when there are several parallel items."},
    {"id": "explanatory", "name": "Explanatory", "preset": True,
     "desc": "Teaches: explains the reasoning and the background.",
     "instruction": "Explain as you go, like a patient teacher. Say why something works, not only what to do, and give "
                    "the background a newcomer would need. Define terms the first time they appear, use a short "
                    "concrete example where it helps, and build from simple to detailed."},
    {"id": "formal", "name": "Formal", "preset": True,
     "desc": "Professional and polished.",
     "instruction": "Write in a professional, polished register. Use complete sentences and precise vocabulary, with "
                    "no slang, contractions, jokes or exclamation marks. Organise longer answers under clear "
                    "headings or numbered points and keep a measured, courteous tone."},
]
_PRESET_IDS = {p["id"] for p in PRESETS}
_LOCK = threading.RLock()


def _file() -> Path:
    return C.home() / "styles.json"


def _read() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)                            # writing samples can be personal: only the user reads it
    except OSError:
        pass
    C.replace_file(tmp, f)                              # never a half-written file


def _clean_custom(rows) -> list[dict]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not str(r.get("id") or "").strip():
            continue
        out.append({"id": str(r["id"]), "name": " ".join(str(r.get("name") or "").split())[:MAX_NAME] or "Untitled",
                    "instruction": str(r.get("instruction") or "").strip()[:MAX_INSTRUCTION],
                    "preset": False, "created": float(r.get("created") or 0)})
    return out


def custom_styles() -> list[dict]:
    return _clean_custom(_read().get("custom"))


def all_styles() -> list[dict]:
    return [dict(p) for p in PRESETS] + custom_styles()


def get(style_id: str | None) -> dict | None:
    sid = str(style_id or "")
    return next((s for s in all_styles() if s["id"] == sid), None)


def default_id() -> str:
    d = str(_read().get("default") or NORMAL)
    return d if get(d) else NORMAL                      # a deleted style never stays the default


def listing() -> dict:
    return {"styles": all_styles(), "default": default_id(), "max_custom": MAX_CUSTOM}


def _check(name: str, instruction: str) -> tuple[str, str]:
    name = " ".join(str(name or "").split())
    instruction = str(instruction or "").strip()
    if not name:
        raise ValueError("Give the style a name.")
    if len(name) > MAX_NAME:
        raise ValueError(f"Keep the name under {MAX_NAME} characters.")
    if not instruction:
        raise ValueError("Write the instruction: how should Meshpool write in this style?")
    if len(instruction) > MAX_INSTRUCTION:
        raise ValueError(f"Keep the instruction under {MAX_INSTRUCTION} characters.")
    return name, instruction


def _name_taken(name: str, rows: list[dict], skip: str = "") -> bool:
    n = name.casefold()
    return any(s["name"].casefold() == n and s["id"] != skip for s in [*PRESETS, *rows])


def create(name: str, instruction: str) -> dict:
    name, instruction = _check(name, instruction)
    with _LOCK:
        d = _read()
        rows = _clean_custom(d.get("custom"))
        if len(rows) >= MAX_CUSTOM:
            raise ValueError(f"You can keep up to {MAX_CUSTOM} custom styles. Delete one first.")
        if _name_taken(name, rows):
            raise ValueError(f"A style named {name} already exists.")
        row = {"id": "c_" + uuid.uuid4().hex[:10], "name": name, "instruction": instruction, "preset": False,
               "created": round(time.time(), 3)}
        d["custom"] = [*rows, row]
        _write(d)
    return row


def update(style_id: str, name: str, instruction: str) -> dict:
    if style_id in _PRESET_IDS:
        raise ValueError("The built-in styles can't be changed. Create your own style to adjust one.")
    name, instruction = _check(name, instruction)
    with _LOCK:
        d = _read()
        rows = _clean_custom(d.get("custom"))
        row = next((r for r in rows if r["id"] == style_id), None)
        if not row:
            raise ValueError("That style does not exist any more.")
        if _name_taken(name, rows, skip=style_id):
            raise ValueError(f"A style named {name} already exists.")
        row["name"], row["instruction"] = name, instruction
        d["custom"] = rows
        _write(d)
    return row


def delete(style_id: str) -> None:
    if style_id in _PRESET_IDS:
        raise ValueError("The built-in styles can't be deleted.")
    with _LOCK:
        d = _read()
        rows = _clean_custom(d.get("custom"))
        if not any(r["id"] == style_id for r in rows):
            raise ValueError("That style does not exist any more.")
        d["custom"] = [r for r in rows if r["id"] != style_id]
        if d.get("default") == style_id:
            d["default"] = NORMAL
        _write(d)


def set_default(style_id: str) -> str:
    with _LOCK:
        if not get(style_id):
            raise ValueError("That style does not exist any more.")
        d = _read()
        d["default"] = style_id
        _write(d)
    return style_id


def resolve(chat_style: str | None) -> dict:
    """The style a chat uses: its own choice, else the default. A style that was deleted since reads as Normal."""
    sid = str(chat_style or "") or default_id()
    return get(sid) or get(NORMAL)


def note(chat_style: str | None) -> str:
    """The hidden note added to a user message ("" for Normal). Starts with the separator, so the chat never shows it."""
    s = resolve(chat_style)
    ins = (s.get("instruction") or "").strip()
    if not ins:
        return ""
    return (f"{SEP}[Meshpool] Writing style for your replies: {s['name']}. {ins} This only changes how you write; "
            "it never overrides what the user asked for or any other instruction.")


# ---------- a writing sample becomes an instruction: one cheap model call --------------------------------------------
SAMPLE_PROMPT = (
    "Below is a writing sample. Describe how it is written, as instructions to an assistant that must write replies "
    "in the same style. Cover tone, sentence length and rhythm, vocabulary, structure and formatting habits. Write 3 to "
    "5 plain sentences that begin with a verb, such as \"Write...\" or \"Use...\". Describe the style only: do not quote "
    "the sample, do not repeat its subject matter and do not add commentary. Reply with the instructions and nothing "
    "else.\n\n<sample>\n{sample}\n</sample>")


def _ask(sample: str, client=None, model: str | None = None) -> str:
    """The instruction text from one model call (max_tokens 300), or "" on no answer within MH_STYLE_TIMEOUT seconds
    (default 12) or any error. The shared client retries for minutes on outages and the Settings page waits for this,
    so the call runs on a daemon thread and is abandoned at the deadline (the pattern of hrouter/classify.ask_model)."""
    box: list = []

    def once():
        try:
            c = client
            if c is None:
                from meshpool_harness.core.client import LiveClient
                c = LiveClient("styles")                # LiveClient picks SimpleLedger when installed on its own
            m = model or C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
            resp = c.chat(model=m, messages=[{"role": "user", "content": SAMPLE_PROMPT.format(sample=sample)}],
                          max_tokens=300, temperature=0.3, task_id="style_sample", role="router")
            box.append(((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        except Exception:  # noqa: BLE001 - a missing key or a down service must never break the editor
            box.append("")
    th = threading.Thread(target=once, daemon=True)
    th.start()
    try:
        th.join(float(os.environ.get("MH_STYLE_TIMEOUT", "12")))
    except ValueError:
        th.join(12.0)
    return re.sub(r"\s+", " ", box[0]).strip() if box else ""


def from_sample(sample: str, client=None, model: str | None = None) -> dict:
    """{instruction, generated, message}. generated=False means the pasted text itself is the instruction (no key,
    the call failed or came back empty); message says so in words the page can show."""
    sample = str(sample or "").strip()
    if not sample:
        raise ValueError("Paste a piece of your writing first.")
    sample = sample[:MAX_SAMPLE]
    have_key = client is not None
    if not have_key:
        try:
            C.load_into_env(override=True)
            from meshpool_harness import providers as PV
            have_key = bool(PV.key())
        except Exception:  # noqa: BLE001
            have_key = False
    if not have_key:
        return {"instruction": sample[:MAX_INSTRUCTION], "generated": False,
                "message": "No API key is set, so your text is kept as the instructions. Edit it, or add a key in "
                           "Settings and try again."}
    out = _ask(sample, client, model)
    if not out:
        return {"instruction": sample[:MAX_INSTRUCTION], "generated": False,
                "message": "Meshpool could not turn the sample into instructions just now, so your text is kept as "
                           "the instructions. Edit it, or try again."}
    return {"instruction": out[:MAX_INSTRUCTION], "generated": True,
            "message": "Made from your sample. Read it and change anything that is off."}
