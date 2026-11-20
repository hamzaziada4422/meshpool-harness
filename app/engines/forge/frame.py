"""The deck frame: a deterministic reading of the brief, made BEFORE any model sees it, so every build starts from the
same skeleton whatever the model and however the brief is worded. The storyline model fills the frame; it does not
invent the deck's shape.

frame_brief(brief, slides) -> {"audience", "occasion", "question", "options", "no_cover", "kind", "skeleton"} and
frame_block(frame) -> the text block that goes in front of the brief in the storyline prompt.

Kinds and their skeletons (the consulting shapes the BCG and McKinsey decks use):
- decision: the answer and the ask, where we stand, why it matters now, the options on the same measures, the
  economics of the recommendation, the plan with owners and dates.
- diagnosis: the answer, the symptom sized, the drivers ranked, the root cause proven, what to do.
- update: the headline, results against plan, what moved them, what is next.
- explainer: the point, the context, the mechanism, the evidence, what it means for the reader.
"""
from __future__ import annotations

import re

KINDS = {
    "decision": ["Executive summary: the answer and the decision asked for",
                 "Where we stand: the situation, sized with the key numbers",
                 "Why it matters now: the complication or the cost of waiting",
                 "The options compared on the same measures",
                 "Why the recommended option wins: its economics and its risks",
                 "The plan: steps with owners, dates and the decision gates"],
    "diagnosis": ["Executive summary: the answer",
                  "The symptom, sized",
                  "The drivers, ranked by impact",
                  "The root cause, proven with evidence",
                  "What to do about it, with owners and dates"],
    "update": ["The headline: results against plan",
               "What moved the results",
               "Risks and issues",
               "What is next, with owners and dates"],
    "explainer": ["The point in one line",
                  "The context the reader needs",
                  "How it works",
                  "The evidence",
                  "What it means for the reader"],
}
PRIORITY = {6: [2, 0, 3, 1], 5: [2, 0, 1], 4: [0, 1]}   # decision: options, situation, economics, urgency
_QUESTION = re.compile(r"(?i)\b(decid\w*|whether|should (?:we|the|it)|recommend\w*|choose)\b|\?")
_DECIDE = re.compile(r"(?i)\b(decid\w*|whether|should (?:we|the|it)|options?\b|choose|choice|approve|recommend\w*|"
                     r"invest|fund|buy|sell|go/no-go)\b")
_DIAG = re.compile(r"(?i)\b(why (?:is|are|did|do|has|have)|root cause|diagnos\w*|what is driving|driven by|decline|"
                   r"falling|dropp\w*|underperform\w*)\b")
_UPDATE = re.compile(r"(?i)\b(update|progress|status|quarterly review|results against|month(?:ly)? review|qbr)\b")
_AUD = re.compile(r"(?i)\bfor (?:the )?((?:board|leadership team|executive committee|investment committee|investors|"
                  r"management|ceo|cfo|steering committee|partners|team)(?: of [A-Z][\w&.\- ]{1,60}?)?)(?=[,.;:]| who| "
                  r"deciding| on | to |$)")
_OPTS = re.compile(r"(?i)\boptions?\s*(?:are|:)\s*(.+?)(?:\.\s|$)")


def _options(brief: str) -> list[str]:
    m = _OPTS.search(brief)
    if not m:
        return []
    parts = re.split(r"\s*(?:;|,\s*or\s+|\bor\b|,)\s*", m.group(1))
    return [p.strip(" .") for p in parts if 2 <= len(p.split()) <= 18][:5]


def kind_of(brief: str) -> str:
    if _DECIDE.search(brief):
        return "decision"
    if _DIAG.search(brief):
        return "diagnosis"
    if _UPDATE.search(brief):
        return "update"
    return "explainer"


def _fit(skel: list[str], n: int) -> list[str]:
    """The skeleton for n content slides: the first and the last beats always; the middle ones merged or repeated
    (the evidence beats split in two when there is room)."""
    if n <= 0:
        return []
    if n >= len(skel):
        mid = skel[1:-1]
        extra = n - len(skel)
        out = [skel[0]]
        for i, b in enumerate(mid):
            out.append(b)
            if extra > 0 and i < len(mid) - 1:            # more slides: the evidence beats get a second slide
                out.append(b + " (continued: a second angle with new evidence)")
                extra -= 1
        while extra > 0:
            out.append("Further evidence for the recommendation (a new angle, not a repeat)")
            extra -= 1
        return out + [skel[-1]]
    if n == 1:
        return [skel[0]]
    mid = skel[1:-1]
    order = PRIORITY.get(len(skel), list(range(len(mid))))   # the beats a short deck keeps, most needed first
    pick = sorted(order[: max(0, n - 2)])
    return [skel[0]] + [mid[i] for i in pick if i < len(mid)] + [skel[-1]]


def frame_brief(brief: str, slides: int) -> dict:
    b = " ".join((brief or "").split())
    no_cover = bool(re.search(r"(?i)\bno (?:cover|title) (?:slide|page)\b|\bwithout a cover\b", b))
    k = kind_of(b)
    aud = _AUD.search(b)
    content = max(1, slides - (0 if no_cover else 1))
    q = next((s.strip() for s in re.split(r"(?<=[.?!])\s+", b)
              if _QUESTION.search(s) and not s.lower().startswith(("facts", "options"))),
             re.split(r"(?<=[.?!])\s+", b)[0] if b else "")
    return {"kind": k, "audience": aud.group(1).strip() if aud else "", "question": q[:300],
            "options": _options(b), "no_cover": no_cover, "slides": slides,
            "skeleton": _fit(KINDS[k], content)}


def frame_block(fr: dict) -> str:
    lines = ["DECK FRAME (fixed before planning; fill it, do not change its shape):",
             f"- Kind: {fr['kind']} deck" + (f" for {fr['audience']}" if fr["audience"] else "") + ".",
             ]
    if fr["question"]:
        lines.append(f"- The question the deck answers: {fr['question']}")
    if fr["options"]:
        lines.append("- Options to compare, each on the same measures: " + "; ".join(fr["options"]) + ".")
    lines.append(f"- {fr['slides']} slides" + (", no cover" if fr["no_cover"] else ", starting with the cover") + ".")
    lines.append("- Slide by slide after the cover (each is one message; its title states the takeaway with its number):")
    lines += [f"  {i}. {beat}" for i, beat in enumerate(fr["skeleton"], 1)]
    lines.append("- Every fact appears once in the deck; later slides add new evidence. The insight is drawn on each "
                 "exhibit (gap arrow, target line, descriptors), the so-what is stated beside it.")
    return "\n".join(lines)
