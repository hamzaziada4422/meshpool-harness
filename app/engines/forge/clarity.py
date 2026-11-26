"""Clarity pass: a second, smaller model reads every line the reader will see and rewrites only the ones that are not
clear on first read. It never changes the facts: a rewrite is kept only when every number, footnote marker and the
bold markup survive and it is no longer than the line it replaces.

Model chain (first that answers wins): FORGE_CLARITY_MODEL, then the main writing model with the same rubric
(core/models.py reviewer_models: never a built-in name; models the endpoint is remembered to lack are left out; none
left: the pass is skipped with a note and nothing is called). One batched call per deck (or per repaired set of slides).
"""
from __future__ import annotations

import json
import os
import re
import uuid

TEXT_KEYS = {"title", "title_right", "subtitle", "text", "lead", "label", "action", "detail", "intro"}
LIST_KEYS = {"bullets", "milestones"}            # lists whose plain strings are reader-facing lines
SKIP_KEYS = {"source", "prov", "notes", "table", "chart", "id", "node", "archetype", "icon", "tags", "exhibit_unit",
             "footnotes", "firm", "date", "columns"}

RUBRIC = """You are the senior editor at a top strategy firm. Each line below will appear on a board slide. Make every
line clear to a smart reader who was not in the room, on first read:
- plain English, a concrete subject and verb; say who does what;
- no jargon or buzzwords (leverage, unlock, go-forward, robust, best-in-class, synergise, value creation as filler);
- no ambiguity: no dangling "this"/"it", no unexplained abbreviations (EPS, EV, GW, $B, % are fine);
- sound like a person wrote it: no hype, no filler openers, no stacked dashes or semicolons;
- keep EVERY number, unit, name and claim exactly as written; never add or drop a fact;
- keep **bold** markup around the same key phrase and keep ^1-style footnote markers;
- same length or shorter; a title stays one sentence with a verb.
Most lines are already fine: change only a line a reader would stumble on (expect to change about one in five),
and keep the author's voice and emphasis. Return JSON {"edits": [{"id": "<id>", "issue": "<= 8 words",
"text": "<rewrite>"}]} with only the lines you changed."""

# The user's own prose (a Word document they asked for): spelling and obvious typos only, never rewording (Priya,
# round 3: the tool result said wording was tidied and "do not mention it", and the model told her it was told to hide
# something).
TYPO_RUBRIC = """You are a proofreader. Each line below is from a document someone wrote. Fix only spelling mistakes and
obvious typos: a misspelt word, a doubled word ("the the"), a missing or swapped letter. Never change the wording, the
tone, the order, the punctuation style, names, numbers or the meaning, and never rephrase. Most lines need nothing.
Return JSON {"edits": [{"id": "<id>", "issue": "spelling", "text": "<the line with only the typos fixed>"}]} with only
the lines you changed."""

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"\S+")


def typo_fixes(old: str, new: str) -> list[tuple[str, str]] | None:
    """The word-for-word fixes from old to new, [("recieve", "receive")], when every change is a typo fix: each
    changed word is close to the one it replaces, or a doubled word was dropped. None when the line was reworded."""
    import difflib
    a, b = _WORD.findall(old), _WORD.findall(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op == "delete" and i2 - i1 == 1 and i1 > 0 and a[i1].lower().strip(".,;:!?") == a[i1 - 1].lower().strip(".,;:!?"):
            out.append((f"{a[i1 - 1]} {a[i1]}", a[i1 - 1]))
            continue
        if op != "replace" or i2 - i1 != j2 - j1:
            return None
        for x, y in zip(a[i1:i2], b[j1:j2]):
            if difflib.SequenceMatcher(a=x.lower(), b=y.lower()).ratio() < 0.75 or _numbers(x) != _numbers(y):
                return None
            out.append((x, y))
    return out


def _numbers(t: str) -> list[str]:
    return sorted(n.replace(",", "") for n in _NUM.findall(t))


def _markers(t: str) -> list[str]:
    return sorted(re.findall(r"\^\w+", t))


def _walk(obj, path, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP_KEYS:
                continue
            p = path + [k]
            if k in TEXT_KEYS and isinstance(v, str) and v.strip():
                out.append((p, v))
            elif k in LIST_KEYS and isinstance(v, list):
                for i, x in enumerate(v):
                    if isinstance(x, str) and x.strip():
                        out.append((p + [i], x))
                    else:
                        _walk(x, p + [i], out)
            elif isinstance(v, (dict, list)):
                _walk(v, p, out)
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            _walk(x, path + [i], out)


def lines(slides: list[dict], only: set[int] | None = None) -> list[tuple[str, list, str]]:
    """Reader-facing lines as (id, path, text). Data (tables, charts), sources and notes are not touched."""
    out = []
    for si, s in enumerate(slides):
        if only is not None and si not in only:
            continue
        found: list = []
        _walk(s, [], found)
        if s.get("archetype") in ("cover", "section_divider", "agenda", "appendix_divider", "end"):
            found = [(p, t) for p, t in found if p != ["title"]]     # a cover title is a name, not a sentence
        for k, (p, t) in enumerate(found):
            out.append((f"s{si + 1}.{k}", [si] + p, t))
    return out


def acceptable(old: str, new: str) -> str | None:
    """None when the rewrite is safe to use; otherwise why it was rejected."""
    if not new or not new.strip():
        return "empty"
    if _numbers(old) != _numbers(new):
        return "numbers changed"
    if _markers(old) != _markers(new):
        return "footnote markers changed"
    if ("**" in old) != ("**" in new):
        return "bold markup changed"
    if len(new.split()) > max(len(old.split()) * 1.1, len(old.split()) + 2):
        return "longer than the original"
    return None


def _set(slides, path, value):
    obj = slides
    for k in path[:-1]:
        obj = obj[k]
    obj[path[-1]] = value


def review(slides: list[dict], client, only: set[int] | None = None, main_model: str | None = None,
           task_id: str = "clarity", typos: bool = False) -> dict:
    """Edit `slides` in place. Returns a report: model used, lines checked, changed, rejected (with reasons)."""
    items = lines(slides, only)
    if not items:
        return {"model": None, "checked": 0, "changed": 0, "edits": [], "rejected": []}
    from meshpool_harness.core.models import reviewer_models
    chain = reviewer_models(os.environ.get("FORGE_CLARITY_MODEL"), main_model)
    if not chain:
        return {"model": None, "checked": len(items), "changed": 0, "edits": [], "rejected": [], "status": "not_run",
                "errors": ["skipped: no reviewer model on this endpoint"]}
    errors: list[str] = []

    def one(batch):
        payload = "\n".join(f"{i}: {t}" for i, _, t in batch)
        for m in chain:
            try:
                r = client.chat(model=m, messages=[{"role": "system", "content": TYPO_RUBRIC if typos else RUBRIC},
                                                   {"role": "user", "content": payload + f"\n<run id=\"{uuid.uuid4().hex[:8]}\"/>"}],
                                max_tokens=6000, temperature=0.2, task_id=task_id, role="verifier",
                                extra={"response_format": {"type": "json_object"}})
                txt = r["choices"][0]["message"].get("content") or "{}"
                data = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                if isinstance(data.get("edits"), list):
                    return m, data["edits"]
            except Exception as e:  # model missing on the gateway, timeout, bad JSON: try the next one
                errors.append(f"{m}: {type(e).__name__}")
        return None, []
    from concurrent.futures import ThreadPoolExecutor
    batches = [items[k:k + 30] for k in range(0, len(items), 30)]      # parallel batches: one deck in ~20 s
    with ThreadPoolExecutor(min(6, len(batches))) as ex:
        results = list(ex.map(one, batches))
    models = [m for m, _ in results if m]
    if not models:
        return {"model": None, "checked": len(items), "changed": 0, "edits": [], "rejected": [],
                "status": "not_run", "errors": errors}
    used = max(set(models), key=models.count)
    by_id = {i: (p, t) for i, p, t in items}
    edits, rejected = [], []
    for e in [x for _, es in results for x in es]:
        if not isinstance(e, dict) or e.get("id") not in by_id:
            continue
        path, old = by_id[e["id"]]
        new = str(e.get("text") or "").strip()
        if new == old.strip():
            continue
        why = acceptable(old, new)
        fixes = typo_fixes(old, new) if typos and not why else None
        if typos and not why and not fixes:
            why = "reworded, not a typo fix"
        if why:
            rejected.append({"id": e["id"], "old": old, "new": new, "why": why})
            continue
        _set(slides, path, new)
        edits.append({"id": e["id"], "issue": e.get("issue", ""), "old": old, "new": new,
                      **({"fixes": fixes} if typos else {})})
    return {"model": used, "checked": len(items), "changed": len(edits), "edits": edits, "rejected": rejected,
            "errors": errors}


def review_markdown(md: str, client, main_model: str | None = None, task_id: str = "clarity-doc",
                    typos: bool = False) -> tuple[str, dict]:
    """The same read for a Markdown document (memos, reports): prose lines and bullets; headings, tables, code and
    links are left alone (a heading is a label: the slide rubric turned "Northwind: valuation and options" into
    "Northwind considers valuation and options."). typos: fix spelling and obvious typos only, never reword (the
    user's own prose; each edit lists its word fixes). Returns (new markdown, report)."""
    out = md.split("\n")
    slots, in_code = [], False
    for k, line in enumerate(out):
        st = line.strip()
        if st.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not st or st.startswith(("|", "<", "![", "---", "#")) or re.match(r"^\[[^\]]+\]\(", st):
            continue
        m = re.match(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)(.*)$", line)
        if m and len(m.group(2).split()) >= 4:
            slots.append((k, m.group(1), m.group(2)))
    doc = [{"text": t} for _, _, t in slots]
    rep = review([{"bullets": doc}], client, main_model=main_model, task_id=task_id, typos=typos)
    for (k, prefix, _), d in zip(slots, doc):
        out[k] = prefix + d["text"]
    return "\n".join(out), rep
