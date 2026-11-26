"""Speaker notes: what the presenter reads aloud, and nothing else.

Every writer of notes text goes through this module (Daniel, 0.0.19: the notes held "[QA] Note moved off the slide",
"Detail for the presenter (moved off the slide):", a raw JSON bullets blob with "prov F9", fact ids such as "C1" and
"F9", and "The storyline title said tripled; it is corrected here").

- qa(s, msg): a remark for the checks (what the renderer cut, shortened or could not draw). It goes to the build's
  errors and qa.json, never into the file.
- moved(s, text): words that did not fit on the slide. They join the notes as plain presenter prose, with no label.
- clean(text): the last step before notes are written into a .pptx. It drops check markers, labels, JSON, fact and
  calc ids, provenance and remarks about the brief, the storyline or corrections.
"""
from __future__ import annotations

import json
import re

QA_KEY = "_qa"


def qa(s: dict | None, msg: str) -> None:
    """Record a check remark on the slide (never shown in its notes)."""
    if isinstance(s, dict) and str(msg or "").strip():
        s.setdefault(QA_KEY, []).append(str(msg).strip())


def qa_of(s) -> list[str]:
    return list(s.get(QA_KEY) or []) if isinstance(s, dict) else []


def _sentence(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip(" -;|")
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    return t if t[-1] in ".!?" else t + "."


def moved(s: dict | None, text) -> None:
    """Words moved off the slide, added to the notes as plain sentences."""
    if not isinstance(s, dict):
        return
    items = text if isinstance(text, (list, tuple)) else [text]
    lines = [x for x in (_sentence(prose(i)) for i in items) if x]
    if lines:
        s["notes"] = (str(s.get("notes") or "") + "\n" + "\n".join(lines)).strip()


_SKIP_KEYS = {"prov", "title_prov", "id", "node", "kind", "type", "unit", "decimals", "icon", "style", "look",
              "layout", "highlight", "highlight_categories", "highlight_rows", "italic_rows", "delta", "target", "calcs",
              "size", "color", "align", "_gap", "_section", "archetype", "status", "rank"}


def prose(v) -> str:
    """Any slide content (a string, a bullet, a stats item, a chart, a table row) as plain words."""
    if v is None:
        return ""
    if isinstance(v, str):
        return re.sub(r"\*\*|\^\w+", "", v).strip()
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        return "; ".join(x for x in (prose(i) for i in v) if x)
    if isinstance(v, dict):
        if isinstance(v.get("categories"), list) and isinstance(v.get("series"), list):   # a chart: its values
            unit = str(v.get("unit") or "").strip()
            out = []
            for se in v["series"]:
                vals = se.get("values") if isinstance(se, dict) else None
                if not isinstance(vals, list):
                    continue
                pairs = [f"{c} {prose(x)}{unit if unit in ('%', 'x') else ''}" for c, x in zip(v["categories"], vals)
                         if x is not None]
                if pairs:
                    out.append((f"{prose(se.get('name'))}: " if se.get("name") else "") + ", ".join(pairs))
            return "; ".join(out)
        if "value" in v and ("label" in v or "text" in v):          # a big number with its label
            return f"{prose(v.get('value'))} {prose(v.get('label') or v.get('text'))}".strip()
        parts = []
        for k in ("heading", "lead", "action", "label", "text", "detail", "claim", "owner", "timing"):
            if v.get(k):
                parts.append(prose(v[k]))
        for k, x in v.items():
            if k in _SKIP_KEYS or k in ("heading", "lead", "action", "label", "text", "detail", "claim", "owner",
                                        "timing"):
                continue
            if k == "chart" or isinstance(x, (dict, list)):
                t = prose(x)
                if t:
                    parts.append(t)
        parts = [p for p in parts if p]
        if not parts:
            return ""
        return parts[0] + (": " + "; ".join(parts[1:]) if len(parts) > 1 else "")
    return str(v)


# ------------------------------------------------------------------ the last step before a note is written
_MARKER_LINE = re.compile(r"(?im)^\s*\[(?:QA|REVIEW|CHECK|FIX|TODO|NOTE TO SELF)\b[^\]]*\].*$")
_LABEL = re.compile(r"(?im)^\s*(?:detail for the presenter[^:\n]*:|say aloud\s*:|presenter notes?\s*:|"
                    r"speaker notes?\s*:|moved off the slide\s*:|footnote\s*:|milestones not shown\s*:|"
                    r"did not fit[^:\n]*:|note moved off the slide\s*:)\s*")
_BULLET = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
_ID = r"(?:[FCWO]\d{1,3}|S\d{1,3}[CO]\d{1,3}|EV-[0-9a-f]{6,}(?:-\w+)?)"
_PAREN_IDS = re.compile(r"\s*[(\[]\s*(?:(?:see|from|per|source|sources|prov|fact|facts|calc)\s*:?\s*)?" + _ID +
                        r"(?:\s*(?:,|;|/|&|and)\s*" + _ID + r")*\s*[)\]]")
_PROV = re.compile(r"(?i)\s*\bprov(?:enance)?\b\s*:?\s*(?:\[\s*)?" + _ID + r"(?:\s*,\s*" + _ID + r")*(?:\s*\])?")
_BARE_ID = re.compile(r"\b" + _ID + r"\b")
_GAP = re.compile(r"\[GAP[^\]]*\]", re.I)
_META = re.compile(r"(?i)\bstory\s?line\b|\bstoryboard\b|\bfact (?:list|ids?)\b|\bin the facts\b|\bthe facts (?:do|did)\b|"
                   r"\bslide brief\b|\bthe brief\b|\b(?:is|was|are|were|has been|have been) corrected\b|"
                   r"\bcorrected here\b|\b(?:title|headline|wording) said\b|\bwording\b|\bmoved off the (?:slide|page)\b|"
                   r"\bdid not fit\b|\bleft out of the (?:slide|text|page)\b|\bthe renderer\b|\bthe (?:engine|model)\b|"
                   r"\bjson\b|\bprovenance\b|\bschema\b|\bplaceholder\b|\bbefore circulating\b|\[QA\]|\[REVIEW\]|"
                   r"\bnot (?:a )?stated (?:fact|in the)\b|\bthe sources? (?:do|does|did) not\b|\bno source\b|"
                   r"\bcite[sd]? (?:by|as) (?:id|fact)\b")


def _drop_json(t: str) -> str:
    """Balanced {...} or [...] spans that hold JSON (quotes and colons) come out."""
    out, i = [], 0
    while i < len(t):
        ch = t[i]
        if ch in "{[":
            close = "}" if ch == "{" else "]"
            depth, j = 0, i
            while j < len(t):
                if t[j] in "{[":
                    depth += 1
                elif t[j] in "}]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            span = t[i:j + 1]
            if j < len(t) and t[j] == close and (ch == "{" or re.search(r"[\"']\s*:|\"\s*,|^\[\s*[\"{]", span)):
                i = j + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def clean(text, punctuate: bool = True) -> str:
    """Notes as presenter-facing prose only. Never raises: on any error the text goes out with only its markers cut.
    punctuate=False (notes a person dictated through an edit): sentences are kept as written, not capitalised or
    closed with a full stop."""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = prose(text) if not isinstance(text, (dict, list)) else prose(text)
        except Exception:  # noqa: BLE001
            text = str(text)
    try:
        t = _MARKER_LINE.sub("", text)
        t = _LABEL.sub("", t)
        t = _BULLET.sub("", t)
        t = _drop_json(t)
        t = _PROV.sub("", t)
        t = _PAREN_IDS.sub("", t)
        out_lines = []
        for line in t.split("\n"):
            keep = []
            for sent in _SPLIT.split(line.strip()):
                st = sent.strip()
                if not st or _META.search(st) or _GAP.search(st) or _BARE_ID.search(st):
                    continue
                st = re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s{2,}", " ", st))
                st = re.sub(r"[,;:]+(?=[.!?]$)", "", st).strip(" ,;")
                if st.endswith(":") and len(st.split()) <= 4:     # a label left with nothing under it
                    continue
                st = st.rstrip(":,; ")
                if re.search(r"[A-Za-z]", st):
                    if not punctuate:
                        keep.append(st)
                        continue
                    st = st[0].upper() + st[1:]
                    keep.append(st if st[-1] in ".!?\"')" else st + ".")
            if keep:
                out_lines.append(" ".join(keep))
        return "\n".join(out_lines).strip()
    except Exception:  # noqa: BLE001 - a note is never lost to the cleaner
        return re.sub(r"(?im)^\s*\[QA\].*$", "", str(text)).strip()


WORDS = 120          # speaker notes run about 40 to 120 words a slide unless the request asks for more (Daniel, round
# 2: slide 6's notes ran to 253 words, more than a presenter reads aloud for one page)


def limit_for(request: str) -> int:
    """The most words a slide's notes keep: WORDS, or what the request asks for ("detailed speaker notes", "notes of
    about 200 words", "full script")."""
    t = str(request or "")
    m = re.search(r"(?i)\b(\d{2,4})(?:\s*(?:-|to)\s*(\d{2,4}))?[- ]words?\b[^.\n]{0,40}\bnotes\b|\bnotes\b[^.\n]{0,40}?"
                  r"\b(\d{2,4})(?:\s*(?:-|to)\s*(\d{2,4}))?[- ]words?\b", t)
    if m:
        nums = [int(x) for x in m.groups() if x]
        return max(40, max(nums))
    if re.search(r"(?i)\b(?:detailed|full|long|longer|extensive|verbatim|word[- ]for[- ]word)\s+(?:speaker\s+|presenter\s+)?"
                 r"(?:notes|script)\b|\bfull script\b", t):
        return 300
    return WORDS


def trim(text: str, words: int | None = WORDS) -> str:
    """Whole sentences, in order, up to `words` words (the first sentence always stays)."""
    if not words or len(str(text or "").split()) <= words:
        return text
    out, n = [], 0
    for line in str(text).split("\n"):
        keep = []
        for sent in _SPLIT.split(line.strip()):
            w = len(sent.split())
            if out or keep:
                if n + w > words:
                    break
            keep.append(sent)
            n += w
        if keep:
            out.append(" ".join(keep))
        if n >= words or len(keep) < len(_SPLIT.split(line.strip())):
            break
    return "\n".join(out).strip()


def write(slide, text, words: int | None = WORDS) -> None:
    """Set a python-pptx slide's speaker notes to the cleaned text, whole sentences up to `words` words (nothing is
    written when nothing is left)."""
    t = trim(clean(text), words)
    if t or slide.has_notes_slide:
        slide.notes_slide.notes_text_frame.text = t


def json_free(obj) -> str:
    """For callers that used json.dumps to move content into the notes: plain words instead."""
    try:
        return prose(obj)
    except Exception:  # noqa: BLE001
        return re.sub(r"[{}\[\]\"]", "", json.dumps(obj, ensure_ascii=False, default=str))
