"""What a message needs a harness to be able to make, and which harnesses can make it.

A chat asking for a PowerPoint must get one, whichever agent the chat runs on: the chat checks every message here (not
only the first), gives the agent the tools and a plain note that it can do it, and hands the chat over, with its
history, to an agent that can when the current one cannot. Only when no agent Meshpool has can do it does the chat say
so (see ui/agent_api.Session._capability_turn).

needs() is plain rules, no model call: a file type named outright (PowerPoint, ppt, xlsx, Word document) counts on its
own; a general word (deck, slides, report, spreadsheet, PDF) counts with a request to make something ("make", "need",
"turn this into"). A false positive costs little: the agent gets office tools it does not use.
"""
from __future__ import annotations

import re

# capability -> (what the user calls it, how Meshpool's office tools make it)
CAPS: dict[str, tuple[str, str]] = {
    "presentation": ("PowerPoint presentations", "make_deck (or office_task where you have it)"),
    "document": ("Word documents", "make_document"),
    "spreadsheet": ("Excel workbooks", "make_spreadsheet (or office_task where you have it)"),
    "pdf": ("PDF files", "make_pdf"),
}
OFFICE = frozenset(CAPS)

# A file type named outright: enough on its own.
_STRONG = {
    "presentation": r"power ?points?|pptx?s?|keynotes?|google slides|slide ?decks?|pitch ?decks?|slideshows?",
    "document": r"word (?:docs?|documents?|files?)|docx?s?|google docs?",
    "spreadsheet": r"excel(?: (?:files?|workbooks?|sheets?|models?))?|xlsx?s?|google sheets?",
    "pdf": r"pdfs?",
}
# A general word: counts when the message asks for something to be made.
_WEAK = {
    "presentation": r"decks?|slides?|presentations?",
    "document": r"documents?|docs?|memos?|reports?|proposals?|one[- ]pagers?|briefs?|letters?|white ?papers?",
    "spreadsheet": r"spreadsheets?|workbooks?|financial models?|trackers?",
    "pdf": r"",
}
_MAKE = re.compile(r"\b(make|makes|making|create|creating|build|building|generate|produce|prepare|put together|draft|"
                   r"write|design|turn (?:this|it|that|these|them) into|convert|export|save (?:it )?as|need|want|"
                   r"give me|send me|can you do|could you do|help me (?:make|build|create|with))\b", re.I)
_READ_ONLY = re.compile(r"\b(read|open|summari[sz]e|what(?:'s| is) in|explain|review|look at|check) (?:this|the|my|"
                        r"attached|that)\b", re.I)


def _rx(words: str) -> re.Pattern | None:
    return re.compile(r"(?<![a-z0-9])(?:" + words + r")(?![a-z0-9])", re.I) if words else None


_STRONG_RX = {k: _rx(v) for k, v in _STRONG.items()}
_WEAK_RX = {k: _rx(v) for k, v in _WEAK.items()}
SEP = "\n\n​"                       # notes only the model sees follow this (pace.SEP)


def needs(text: str) -> list[str]:
    """The capabilities a message asks for, in CAPS order. Empty for a question about a file the user already has
    ("summarize this pdf") unless it also asks to make one."""
    t = str(text or "").split(SEP)[0]
    if not t.strip():
        return []
    make = bool(_MAKE.search(t))
    if not make and _READ_ONLY.search(t):
        return []
    out = []
    for cap in CAPS:
        strong, weak = _STRONG_RX[cap], _WEAK_RX[cap]
        if (strong and strong.search(t) and (make or not _READ_ONLY.search(t))) or (make and weak and weak.search(t)):
            out.append(cap)
    return out


def of_harness(h: dict) -> frozenset:
    """What a harness can make. Meshpool's own agents and every outside agent that gets the meshpool-office server
    (Pi, Hermes and the ACP agents, unless their manifest says office: false) make all four."""
    if not isinstance(h, dict):
        return frozenset()
    if h.get("type") == "auto":
        return OFFICE
    if h.get("id") in ("meshpool-work", "meshpool-code"):
        return OFFICE
    if h.get("type") in ("pi", "acp"):
        return OFFICE if h.get("office", True) else frozenset()
    return frozenset()


def labels(caps) -> str:
    names = [CAPS[c][0] for c in caps if c in CAPS]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def note(caps, harness: dict | None = None) -> str:
    """The hidden note that goes with a message asking for a file: the agent can make it, with which tools, and must
    not answer that it cannot."""
    caps = [c for c in caps if c in CAPS]
    if not caps:
        return ""
    how = "; ".join(f"{CAPS[c][0]}: {CAPS[c][1]}" for c in caps)
    return (SEP + f"[Meshpool] This message asks for {labels(caps)}. You can make "
            + ("these files" if len(caps) > 1 else "this file")
            + f" with the Meshpool office tools ({how}). Make it, save it in the project folder, check it, and tell "
            "the user where it is. Do not answer that you cannot create it, and do not hand over only text or code "
            "to paste instead of the file.")


def pick(caps, current: str, ranked: list[str], ready: dict) -> str | None:
    """The harness to run a message that needs `caps`: the current one when it can; else the first in `ranked`
    (Auto's order for the task) that is ready and can; else any ready one that can; else None (no agent can)."""
    need = set(caps)
    if not need:
        return current
    if need <= of_harness(ready.get(current) or {}):
        return current
    for hid in [*ranked, *ready]:
        h = ready.get(hid)
        if h and h.get("type") != "auto" and need <= of_harness(h):
            return hid
    return None
