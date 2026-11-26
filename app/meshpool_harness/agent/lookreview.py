"""The independent reviewer for the look step (agent/look.py).

When a turn made a page or a file, meshpool renders it and measures it. Before this module, the builder then reviewed
its own work in its own conversation. Now a separate model call does it, with a fresh context: none of the builder's
messages, system prompt or reasoning. It gets the user's request, the pictures (when the model takes images), the
measured findings, the log of what meshpool clicked and typed, and the list of files made.

The reviewer first names who would really use the thing (a customer on her phone, a finance director before a board
meeting), then reviews as that person. For a web page it may also ask meshpool to try up to MAX_ACTIONS specific
actions. They run in the same headless browser that drew the pictures, and the reviewer sees what happened before its
verdict. Documents, decks and sheets get a reader persona and the rendered pages, with no actions.

The model, endpoint, client and gate are the chat's own (Agent._aux_complete), so routing does not change. Each call
is counted in the turn's usage. A call that fails or answers with something unreadable returns None, and look.py goes
back to sending the measured findings to the builder.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from pathlib import Path

MAX_ACTIONS = 4
MAX_PROBLEMS = 10
MAX_REVIEW_IMAGES = 4          # pictures in one reviewer call
MAX_REVIEW_CALLS = 4           # reviewer calls in one turn, retries included
SEVERITIES = ("blocker", "major", "minor")
_SEV_ALIAS = {"critical": "blocker", "block": "blocker", "blocking": "blocker", "high": "major", "medium": "major",
              "moderate": "major", "low": "minor", "nit": "minor", "trivial": "minor"}

# ---------- the instructions the reviewer reads --------------------------------------------------------------------------
SYSTEM = """You are a reviewer. You did not build what you are about to look at, and you do not know how it was built. You see only what is given below: the request, what the thing looks like, what was measured, and what happened when it was used.

Step 1. Decide who would really use this thing. Write one line that names that person and what they are doing, for example "a customer on her phone ordering a birthday cake from the bakery", "a finance director reading this memo before a board meeting" or "a new hire using this dashboard on day one". Take that person from the request. They are the end user of the thing. They are never the person who wrote the request, unless the request says that person is the one who will use it.

Step 2. Become that person. Do not judge as a designer or a developer. Ask: can I do what I came here to do? What confuses me? What is broken? What did the request promise that I cannot find? What looks unfinished?

Rules:
- Judge only from what you are given. If you cannot tell something, say so in a problem. Do not invent things you did not see.
- The request's own choices win. If it says something is a placeholder or does nothing yet, that is not a problem.
- Write each problem as a short phrase that names what is wrong, in the words of the person, such as "no way to pick a delivery date" or "the order form shows an error after a valid email". Say where it is (a section, a button, a page).
- severity: "blocker" means I cannot do what I came to do. "major" means I can, but it is confusing, broken or wrong. "minor" means it is rough or unfinished but does not stop me.
- "pass" is true only when you can do the job and there is no blocker or major problem.
- Judge only what the request asked for in this turn. A problem is something that stops the request from being met. If you notice something else, such as wording or design in a file the request did not ask to change, it is not a problem. Put it in "also_noticed" as one short line, or leave that empty.
- For a request to change something (add a column, extend a chart, fix a typo), a problem is only something about what the request changed or asked for, or real breakage: an error value, a file that does not open, data lost, content running off the page. Colours, fills, print setup, column widths, fonts, alignment and other style or layout the request did not touch are never problems: put one of them in "also_noticed".
- If you cannot see pictures, do not describe how it looks ("###", "no bars", "blank chart", "overlapping"). Say only what the measurements and the text show.
- When the request is to convert, export, read, summarize or answer a question about an existing file, the file's own content is not yours to judge. Judge only whether the result is what was asked: it opens, it is complete, and it matches the source. Never list the source's wording, titles, numbers or design as a problem.
- When nothing needs changing, "problems" is [] and "pass" is true. Do not list complaints next to a pass.
- Answer with one JSON object and nothing else."""

PLAN_ASK = ("""Reply with this JSON object:
{"persona": "<the one line from step 1>", "actions": [ ... ]}
actions: up to %d things you want to see tried, that the pictures and the log do not show yet. Each one is
{"do": "click" | "fill" | "submit" | "open_menu" | "press" | "look_at", "target": "<the button, link, field or section, in its visible words>", "text": "<what to type, for fill only>", "submit": true | false, "width": "desktop" | "phone"}
Examples: click the "Order now" button; fill the email field with "x" and submit it; open the menu at phone width. "submit" on its own fills the form with sample values and sends it. Use [] when you have seen enough. meshpool tries each one on a fresh copy of the page, in the same browser, and tells you what happened. You get this one chance.""" % MAX_ACTIONS)

VERDICT_ASK = """Reply with this JSON object:
{"persona": "%s", "can_do_the_job": true | false, "problems": [{"what": "<short>", "where": "<where>", "severity": "blocker" | "major" | "minor", "in_request": true | false}], "pass": true | false, "also_noticed": "<one short line about something outside the request, or empty>"}
Use [] for problems when there are none. "problems" holds only what the request asked for and did not get. Anything else goes in "also_noticed", never in "problems"."""

DOC_NOTE = ("This is a file, not a page: you see its first pages as pictures and the text on them. You cannot click "
            "anything, so judge what a person reading or using the file would see.")

_BAD_PERSONA = re.compile(r"\b(?:who|that)\s+(?:asked|requested|wrote|commissioned|ordered)\b|"
                          r"^(?:you|me|i|myself|the requester|the requestor|the author|the owner|the user|a user|"
                          r"the person who)\b", re.I)
_SELF_USE = re.compile(r"\b(?:for me|for myself|i(?:'ll| will| am going to| want to| need to) (?:use|read|run|open)|"
                       r"my own use|just for me|i will be the)\b", re.I)


# ---------- what this turn asked for -------------------------------------------------------------------------------------
_EDIT_VERBS = re.compile(r"\b(?:fix|change|update|edit|rewrite|reword|rephrase|add|remove|delete|replace|correct|improve|"
                         r"redesign|restyle|translate|shorten|expand|insert|rename|tidy|clean(?:\s|-)?up|polish|"
                         r"make\s+it|make\s+them|adjust|resize|reorder|sort|merge|split|build|create|write|draw|design|"
                         r"generate|draft|compose)\b", re.I)
_CONVERT = re.compile(r"\b(?:convert|export|save\s+(?:it\s+|this\s+|that\s+|them\s+)?as|turn\s+(?:it|this|that|them)\s+into|"
                      r"print\s+(?:it\s+|this\s+)?to\s+pdf|(?:make|give|get|produce|need|want)\s+(?:me\s+)?(?:a|an|the|my)\s+"
                      r"(?:\w+\s+){0,2}(?:pdf|png|jpe?g|image|picture|csv)\b|"
                      r"(?:a|the)\s+(?:pdf|png|jpe?g|image|picture)\s+(?:of|from|version\s+of)|"
                      r"\bto\s+(?:a\s+)?(?:pdf|png|jpe?g|csv)\b|\bas\s+(?:a|an)\s+(?:pdf|png|jpe?g|image)\b)", re.I)
_SOURCE = re.compile(r"[\w\-. ()]+\.(?:docx?|pptx?|xlsx?|odt|ods|odp|rtf|html?|md|txt|csv|svg|png|jpe?g)\b|"
                     r"\b(?:of|from|my|this|that|the|our|your)\s+(?:\w+\s+){0,2}(?:deck|slides?|presentation|document|"
                     r"doc|spreadsheet|workbook|sheet|file|page)\b", re.I)
_READ = re.compile(r"^\W*(?:please\s+)?(?:what|which|who|when|where|why|how|is|are|does|do|did|can\s+you\s+tell|tell\s+me|"
                   r"summari[sz]e|read|explain|show\s+me|list|count|find|look\s+up|check\s+(?:if|whether)|compare)\b", re.I)


def request_scope(request: str) -> str:
    """What this turn asked for: "convert" (turn an existing file into a PDF, an image or another format, with no content
    change asked), "read" (read, summarize or answer a question) or "change" (make or change something). Only a request
    that names no edit decides "convert" or "read": when in doubt it is "change", the review that was always done."""
    t = re.sub(r"\s+", " ", str(request or "")).strip()
    if not t:
        return "change"
    if _CONVERT.search(t) and _SOURCE.search(t) and not _EDIT_VERBS.search(_CONVERT.sub(" ", t)):
        return "convert"                              # an existing file, named or pointed at, and nothing to change in it
    if _READ.search(t) and not _EDIT_VERBS.search(t):
        return "read"
    return "change"


SCOPE_NOTE = {
    "convert": ("This turn only asked to convert or export an existing file. Judge only whether the result is a faithful, "
                "complete copy that opens. The source file's own wording, titles, numbers and design are not in scope: "
                "never list them as problems."),
    "read": ("This turn only asked to read something or answer a question. Nothing was to be edited. The source file's own "
             "content is not in scope: never list it as a problem."),
}


def persona_fallback(kind: str) -> str:
    return "a first-time visitor to this page" if kind == "web" else "someone opening this file for the first time"


def clean_persona(p: str, request: str, kind: str) -> tuple[str, bool]:
    """(persona, replaced). The persona is the end user, never the person who asked. A reviewer that named the requester
    gets a neutral end user in its place."""
    p = re.sub(r"\s+", " ", str(p or "")).strip().strip('"').rstrip(".")[:140]
    if not p:
        return "", False
    if _BAD_PERSONA.search(p) and not _SELF_USE.search(request or ""):
        return persona_fallback(kind), True
    return p, False


# ---------- reading the reply --------------------------------------------------------------------------------------------
def parse_json(reply: str) -> dict | None:
    """The first JSON object in a reply (a code fence or a sentence around it is fine). A closing bracket that matches
    nothing open (a model's typo such as "}]]") is left out. None when there is no object."""
    t = re.sub(r"```(?:json)?", "", str(reply or ""))
    pairs = {"}": "{", "]": "["}
    i = t.find("{")
    while i != -1:
        stack: list = []
        out: list = []
        in_s = esc = False
        for c in t[i:]:
            if in_s:
                out.append(c)
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_s = False
                continue
            if c == '"':
                in_s = True
            elif c in "{[":
                stack.append(c)
            elif c in pairs:
                if not stack or stack[-1] != pairs[c]:
                    continue                                   # a closer that matches nothing: left out
                stack.pop()
            out.append(c)
            if not stack:
                try:
                    d = json.loads("".join(out))
                    if isinstance(d, dict):
                        return d
                except ValueError:
                    pass
                break
        i = t.find("{", i + 1)
    return None


def _sev(s) -> str:
    s = str(s or "").strip().lower()
    return s if s in SEVERITIES else _SEV_ALIAS.get(s, "major")


def normalize_verdict(d: dict | None, request: str, kind: str, persona_given: str = "") -> dict | None:
    """{"persona", "can_do_the_job", "problems", "pass"} from the reviewer's JSON, or None when it is not one."""
    if not isinstance(d, dict) or not ("pass" in d or "problems" in d or "can_do_the_job" in d):
        return None
    persona = persona_given or clean_persona(d.get("persona"), request, kind)[0]
    if not persona:
        return None
    probs, outside = [], []
    for p in d.get("problems") or []:
        if isinstance(p, str) and p.strip():
            p = {"what": p}
        if isinstance(p, dict):
            p = dict(p, what=p.get("what") or p.get("problem") or p.get("issue") or p.get("description") or "")
        if isinstance(p, dict) and str(p.get("what") or "").strip():
            q = {"what": re.sub(r"\s+", " ", str(p["what"])).strip()[:240],
                 "where": re.sub(r"\s+", " ", str(p.get("where") or "")).strip()[:120],
                 "severity": _sev(p.get("severity"))}
            if p.get("in_request") is False or p.get("outside_request") is True:
                outside.append(q["what"])                  # the reviewer itself said it is outside the request
            else:
                probs.append(q)
    probs.sort(key=lambda p: SEVERITIES.index(p["severity"]))
    probs = probs[:MAX_PROBLEMS]
    can = d.get("can_do_the_job")
    can = (not any(p["severity"] == "blocker" for p in probs)) if can is None else bool(can)
    if any(p["severity"] == "blocker" for p in probs):
        can = False
    ok = bool(d.get("pass")) and can and not any(p["severity"] in ("blocker", "major") for p in probs)
    also = _also_line(d.get("also_noticed") or d.get("also")) or _also_line(outside)
    return settle({"persona": persona, "can_do_the_job": can, "problems": probs, "pass": ok, "also": also})


def settle(v: dict) -> dict:
    """A verdict that needs no change carries no fix list. No problems is a pass. A pass keeps no problems: the first of
    them becomes the one line "also" offers to the user, and none of them goes to the builder."""
    if not v["problems"]:
        return dict(v, **{"pass": True, "can_do_the_job": True})
    if v["pass"]:
        seen = next((p for p in v["problems"] if not unsure(p)), None)      # "could not confirm" is not something noticed
        return dict(v, problems=[], also=v.get("also") or (_also_line(seen["what"]) if seen else ""))
    return v


def _also_line(x) -> str:
    """One short line about something outside the request, or "". A list or a non-string is read tolerantly."""
    if isinstance(x, (list, tuple)):
        x = next((i for i in x if isinstance(i, str) and i.strip()), "")
    if not isinstance(x, str):
        return ""
    t = re.sub(r"\s+", " ", x).strip().strip('"').rstrip(".")
    if _OWN_CHECK.search(t):                       # meshpool's own check failing to find something is not about the user's file
        return ""
    return "" if re.fullmatch(r"(?:none|nothing|n/?a|no|-+)?", t, re.I) else t[:160]


_OWN_CHECK = re.compile(r"\bscripted\b|\bby label\b|\bselector\b|\b(?:check|checker|script|test|step)\b.{0,60}\b(?:could not|couldn'?t|can'?t|cannot|did not|didn'?t|unable to|failed to)\b.{0,30}\b(?:find|locate|match|click|read|see)\b", re.I)


_BREAKAGE = re.compile(r"\b(?:errors?|exceptions?|corrupt\w*|cannot open|can't open|won't open|does not open|doesn't open|unopenable|"
                       r"data loss|lost data|missing data|deleted|wiped|overwritten|off the (?:page|slide|edge|screen)|"
                       r"outside the (?:page|slide)|crash\w*|broken|fails?|failed)\b|#(?:REF|DIV|VALUE|NAME|N/A)", re.I)
_STYLE = re.compile(r"\b(?:colou?rs?|fills?|shading|print(?:ing)?|landscape|portrait|page setup|paper|margins?|column widths?|"
                    r"widen|fonts?|typeface|alignment|align(?:ed)?|spacing|padding|gridlines?|borders?|gradient|bold|italic|"
                    r"formatting)\b", re.I)
_VISUAL = re.compile(r"###|\bno bars?\b|\bblank (?:chart|page|slide|area)\b|\bempty (?:chart|plot)\b|"
                     r"\bbars? (?:are|is) (?:missing|absent)\b|\b(?:chart|page|slide) (?:is|looks|appears) (?:empty|blank)\b|"
                     r"\b(?:looks?|appears?|seems?) (?:blank|empty|cut|truncated|squashed|overlapping)\b|\boverlap\w*\b|"
                     r"\btruncated\b|\bnot visible\b|\bcut off\b", re.I)
_HEDGE = re.compile(r"\b(?:looks?|appears?|seems?|may|might|possibly|perhaps|probably|unclear|not sure|could be|"
                    r"hard to tell)\b", re.I)


def hedged(p: dict) -> bool:
    """A problem the reviewer only guessed at ("the typo looks swapped"): not evidence that anything is wrong."""
    return bool(_HEDGE.search(str(p.get("what") or "")))


def _confirmed(p: dict, lines: list) -> bool:
    txt = f"{p.get('what', '')} {p.get('where', '')}"
    w = _words(txt)
    return any(("###" in txt and "###" in ln) or (w and len(w & _words(ln)) / len(w) >= 0.4) for ln in lines or [])


def scope_filter(v: dict, request: str, blind: bool, finding_lines: list) -> dict:
    """The verdict held to what the builder should hear. A reviewer that saw no pictures makes no visual claim unless the
    measurements show it. For a request to change something, style and layout the request did not touch (fills, print
    setup, column widths) are not problems: the first becomes the one "also noticed" offer. Real breakage always stays."""
    scope = request_scope(request)
    keep, moved = [], []
    for p in v["problems"]:
        txt = f"{p['what']} {p['where']}"
        if blind and _VISUAL.search(txt) and not _confirmed(p, finding_lines):
            continue
        if scope == "change" and _STYLE.search(txt) and not _BREAKAGE.search(txt) and p["severity"] != "blocker":
            moved.append(p["what"])
            continue
        keep.append(p)
    if len(keep) == len(v["problems"]):
        return v
    out = dict(v, problems=keep, also=v.get("also") or _also_line(moved))
    out["pass"] = out["pass"] or not any(p["severity"] in ("blocker", "major") for p in keep)
    out["can_do_the_job"] = out["can_do_the_job"] or not any(p["severity"] == "blocker" for p in keep)
    return settle(out)


def apply_choices_to_verdict(v: dict, request: str) -> dict:
    """Drop problems about a control the request itself calls a placeholder (look.apply_choices). Without problems left,
    the review passes."""
    from meshpool_harness.agent import look as L
    if not L.inert_clauses(request):
        return v
    keep, _ = L.apply_choices([f"{p['what']} {p['where']}".replace('"', "'") for p in v["problems"]], [], request)
    left = [p for p in v["problems"] if f"{p['what']} {p['where']}".replace('"', "'") in keep]
    if len(left) == len(v["problems"]):
        return v
    out = dict(v, problems=left)
    out["pass"] = out["pass"] or not any(p["severity"] in ("blocker", "major") for p in left)
    out["can_do_the_job"] = out["can_do_the_job"] or not any(p["severity"] == "blocker" for p in left)
    return settle(out)


def plan_actions(raw) -> list:
    """Up to MAX_ACTIONS well-formed action requests from the reviewer's list. A plain sentence is read too."""
    out = []
    for a in (raw if isinstance(raw, list) else []):
        if isinstance(a, str):
            a = parse_action_text(a)
        if not isinstance(a, dict):
            continue
        do = str(a.get("do") or a.get("action") or "").strip().lower().replace(" ", "_")
        if do in ("tap", "press_button", "open", "select"):
            do = "click"
        if do in ("type", "enter", "input"):
            do = "fill"
        if do in ("menu", "open_the_menu", "hamburger"):
            do = "open_menu"
        if do in ("scroll", "scroll_to", "view", "show"):
            do = "look_at"
        if do not in ("click", "fill", "submit", "open_menu", "press", "look_at"):
            continue
        w = str(a.get("width") or "").lower()
        out.append({"do": do, "target": str(a.get("target") or "")[:80], "text": str(a.get("text") or "")[:120],
                    "submit": bool(a.get("submit")), "width": "phone" if w.startswith(("p", "m", "3")) else "desktop"})
        if len(out) >= MAX_ACTIONS:
            break
    return out


def parse_action_text(s: str) -> dict | None:
    t = str(s or "").strip()
    q = re.findall(r"""['"“‘]([^'"”’]{1,80})['"”’]""", t)
    low = t.lower()
    w = "phone" if re.search(r"phone|mobile|narrow|390", low) else "desktop"
    if re.search(r"open the (?:menu|nav)|hamburger|menu at", low):
        return {"do": "open_menu", "target": q[0] if q else "", "width": w}
    if low.startswith(("fill", "type", "enter")):
        m = re.search(r"\bwith\s+['\"“‘]?([^'\"”’]+?)['\"”’]?(?:\s+and\s+submit.*)?$", t, re.I)
        field = re.search(r"(?:fill|type|enter)(?: in)?(?: the)? (.+?) (?:field|input|box)", t, re.I)
        return {"do": "fill", "target": (field.group(1) if field else (q[0] if q else "")),
                "text": (q[-1] if len(q) > 1 else (m.group(1) if m else "")), "submit": "submit" in low, "width": w}
    if low.startswith("submit"):
        return {"do": "submit", "target": q[0] if q else "", "width": w}
    if low.startswith(("press", "hit")):
        return {"do": "press", "target": q[0] if q else t.split()[-1], "width": w}
    if low.startswith(("click", "tap", "press the")):
        return {"do": "click", "target": q[0] if q else t.split(" ", 1)[-1], "width": w}
    if low.startswith(("look", "scroll", "show")):
        return {"do": "look_at", "target": q[0] if q else t.split(" ", 1)[-1], "width": w}
    return None


def action_text(a: dict) -> str:
    do, t, tx = a.get("do"), a.get("target") or "", a.get("text") or ""
    w = " at phone width" if a.get("width") == "phone" else ""
    if do == "click":
        return f"click '{t}'{w}"
    if do == "fill":
        return f"fill '{t}' with '{tx}'" + (" and submit" if a.get("submit") else "") + w
    if do == "submit":
        return f"fill in and submit '{t or 'the form'}'{w}"
    if do == "open_menu":
        return f"open the menu{w}"
    if do == "press":
        return f"press {t or 'Tab'}{w}"
    return f"look at '{t}'{w}"


# ---------- trying the actions in the browser that drew the pictures ----------------------------------------------------
_SAMPLE = {"email": "sam@example.com", "tel": "5551234567", "number": "2", "url": "https://example.com",
           "date": "2030-06-15", "time": "10:30", "password": "Sample-pass-1", "search": "test", "text": "Sample"}

_SNAP_JS = r"""() => {
  const t = (document.body ? document.body.innerText : "").replace(/[ \t]+/g, " ");
  const lines = t.split("\n").map(s => s.trim()).filter(Boolean).slice(0, 400);
  const open = document.querySelectorAll("dialog[open], [role=dialog], [aria-modal=true], [aria-expanded=true], details[open]").length;
  return {lines, url: location.href, y: Math.round(scrollY), open, invalid: document.querySelectorAll(":invalid").length,
          shown: [...document.querySelectorAll("[role=alert], [role=status], .error, .success, .toast, [class*=error], [class*=success], [class*=thank]")]
             .filter(e => e.getClientRects().length && (e.innerText || "").trim()).map(e => e.innerText.trim().replace(/\s+/g, " ").slice(0, 120)).slice(0, 4)};
}"""


def _rx(text: str):
    return re.compile(re.escape(text.strip()), re.I) if text and text.strip() else None


def _find_click(page, target: str):
    """A visible control or text that the reviewer named, the most button-like first."""
    rx = _rx(target)
    if rx is None:
        return None
    for role in ("button", "link", "tab", "menuitem"):
        loc = page.get_by_role(role, name=rx)
        for i in range(min(loc.count(), 4)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    for sel in (f"input[type=submit][value*='{target.strip()[:40]}' i]", f"[aria-label*='{target.strip()[:40]}' i]"):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:  # noqa: BLE001
            pass
    loc = page.get_by_text(rx)
    for i in range(min(loc.count(), 4)):
        if loc.nth(i).is_visible():
            return loc.nth(i)
    return None


def _find_field(page, target: str):
    rx = _rx(target)
    t = (target or "").strip().lower()
    if rx is None:
        return None
    for fn in (lambda: page.get_by_label(rx), lambda: page.get_by_placeholder(rx)):
        try:
            loc = fn()
            for i in range(min(loc.count(), 3)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:  # noqa: BLE001
            pass
    word = re.sub(r"\s*(field|input|box|form)s?\b", "", t).strip()
    for sel in ([f"input[type={word}]"] if word in ("email", "tel", "date", "number", "password", "search", "url", "time")
                else []) + ([f"[name*='{word[:30]}' i], [id*='{word[:30]}' i]"] if word else []) + (
            ["textarea"] if word in ("message", "comment", "comments", "note", "notes") else []):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:  # noqa: BLE001
            pass
    return None


def _fill_form(page, scope) -> int:
    """Sample values into every visible empty field of a form (or the page). Returns how many were filled."""
    n = 0
    els = scope.locator("input, textarea, select")
    for i in range(min(els.count(), 30)):
        e = els.nth(i)
        try:
            if not e.is_visible():
                continue
            tag = e.evaluate("e => e.tagName.toLowerCase()")
            typ = (e.get_attribute("type") or "text").lower()
            if typ in ("hidden", "submit", "button", "reset", "image", "file"):
                continue
            if tag == "select":
                opts = e.evaluate("e => [...e.options].filter(o => o.value && !o.disabled).map(o => o.value)")
                if opts:
                    e.select_option(opts[1] if len(opts) > 1 else opts[0])
                    n += 1
            elif typ in ("checkbox", "radio"):
                e.check()
                n += 1
            elif not (e.input_value() or "").strip():
                e.fill("Hello, this is a test message." if tag == "textarea" else _SAMPLE.get(typ, "Sample"))
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def _submit(page, scope) -> bool:
    try:
        btn = scope.locator("button[type=submit], input[type=submit], button:not([type])").first
        if btn.count() and btn.is_visible():
            btn.click(timeout=2500)
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:  # noqa: BLE001
        return False


def _open_menu(page, target: str) -> str | None:
    if target:
        el = _find_click(page, target)
        if el is not None:
            el.click(timeout=2500)
            return target
    for sel in ("[aria-label*=menu i]", "button[class*=menu i]", "button[class*=burger i]", "[class*=hamburger]",
                "[class*=burger]", "button[aria-controls]", "button[aria-expanded]", "summary", "header button", "nav button"):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 4)):
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=2500)
                    return sel.split("[")[0] or sel
        except Exception:  # noqa: BLE001
            continue
    return None


def _change_note(a: dict, b: dict) -> tuple[bool, str]:
    """(did anything change, a short note of what a person would notice)."""
    before = set(a["lines"])
    new = [ln for ln in b["lines"] if ln not in before][:5]
    gone = [ln for ln in a["lines"] if ln not in set(b["lines"])][:2]
    bits = []
    if new:
        bits.append("new on the page: " + " | ".join(x[:90] for x in new))
    if gone and not new:
        bits.append("gone from the page: " + " | ".join(x[:60] for x in gone))
    if b["shown"] and b["shown"] != a["shown"]:
        bits.append("message shown: " + " | ".join(b["shown"]))
    if b["url"] != a["url"]:
        bits.append("the address changed to " + b["url"][-80:])
    if b["y"] != a["y"]:
        bits.append("the page scrolled")
    if b["open"] != a["open"]:
        bits.append("something opened or closed")
    if b["invalid"] != a["invalid"]:
        bits.append(f"{b['invalid']} field(s) now marked invalid by the browser")
    return bool(bits), "; ".join(bits)


def run_actions(browser, url: str, actions: list, out_dir, tag: str, deadline_s: float = 30.0, allowed=None) -> list:
    """Try each requested action on a fresh page in `browser`. Returns [{"action", "ok", "note", "shot"}]. Only the
    page's own origin (and `allowed`: the other servers this chat started) can be reached; every other request, file:
    URLs included, is blocked. Never raises."""
    from meshpool_harness.agent import look as L
    from meshpool_harness.review_interact import guard
    allowed = set(allowed or ()) | L._origins(url)
    snap = (lambda pg: L.eval_bounded(pg, _SNAP_JS))
    out: list = []
    t_end = time.time() + deadline_s
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for n, a in enumerate(actions[:MAX_ACTIONS], 1):
        text = action_text(a)
        if time.time() > t_end:
            out.append({"action": text, "ok": False, "note": "not tried: out of time", "shot": None})
            continue
        w, h = (390, 844) if a.get("width") == "phone" else (1280, 800)
        ctx = None
        try:
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1,
                                      has_touch=a.get("width") == "phone", is_mobile=a.get("width") == "phone")
            guard(ctx, allowed)
            ctx.set_default_timeout(5000)
            page = ctx.new_page()
            errs: list = []
            page.on("pageerror", lambda e, p=errs: p.append(f"script error: {str(e)[:120]}"))
            page.on("console", lambda m, p=errs: m.type == "error" and p.append(f"console error: {m.text[:120]}"))
            page.on("dialog", lambda d: (errs.append(f"the page showed an alert: {d.message[:100]}"), d.dismiss()))
            page.goto(url, wait_until="load", timeout=12000)
            page.wait_for_timeout(200)
            before = snap(page)
            note_pre, ok = "", True
            do = a["do"]
            if do == "click":
                el = _find_click(page, a["target"])
                if el is None:
                    ok, note_pre = False, f"could not find '{a['target']}' on the page"
                else:
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=3000)
            elif do == "fill":
                el = _find_field(page, a["target"])
                if el is None:
                    ok, note_pre = False, f"could not find a field called '{a['target']}'"
                else:
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.fill(a["text"], timeout=3000)
                    if a.get("submit"):
                        _submit(page, el.locator("xpath=ancestor::form[1]"))
            elif do == "submit":
                el = _find_click(page, a["target"]) if a["target"] else None
                form = el.locator("xpath=ancestor-or-self::form[1]") if el is not None else page.locator("form").first
                scope = form if form.count() else page
                filled = _fill_form(page, scope)
                note_pre = f"filled {filled} field(s) with sample values; " if filled else ""
                if not _submit(page, scope):
                    ok, note_pre = False, note_pre + "found no way to send it"
            elif do == "open_menu":
                got = _open_menu(page, a["target"])
                if got is None:
                    ok, note_pre = False, "found no menu button" + ("" if a.get("width") == "phone" else " at desktop width")
            elif do == "press":
                page.keyboard.press(a["target"] or "Tab")
            else:                                                       # look_at
                el = None
                if a["target"]:
                    try:                                          # a section is named by its heading, not by a link to it
                        hd = page.get_by_role("heading", name=_rx(a["target"]))
                        el = hd.first if hd.count() and hd.first.is_visible() else None
                    except Exception:  # noqa: BLE001
                        el = None
                    el = el or _find_click(page, a["target"])
                if el is not None:
                    el.scroll_into_view_if_needed(timeout=2000)
                    try:                                       # what a person would read there, for a reviewer with no picture
                        seen = el.evaluate("e => ((e.closest('section,article,form,main,aside,footer') || e).innerText || '')"
                                           ".replace(/\\s+/g, ' ').trim()")
                        note_pre = ("it shows: " + seen[:260] + "; ") if seen else ""
                    except Exception:  # noqa: BLE001
                        pass
                elif a["target"]:
                    ok, note_pre = False, f"could not find '{a['target']}' on the page"
            page.wait_for_timeout(450)
            after = snap(page)
            changed, note = _change_note(before, after)
            if errs:
                note = (note + "; " if note else "") + "; ".join(dict.fromkeys(errs))[:200]
            if ok and not changed and do in ("click", "submit", "open_menu") and not errs:
                ok, note = False, "nothing visibly changed"
            shot = None
            try:
                f = Path(out_dir) / f"{tag}-act{n}.jpg"
                page.screenshot(path=str(f), type="jpeg", quality=60, timeout=5000)
                shot = str(f)
            except Exception:  # noqa: BLE001
                pass
            out.append({"action": text, "ok": ok, "note": (note_pre + (note or "")).strip("; ")[:420], "shot": shot})
        except L.Frozen:
            out.append({"action": text, "ok": False, "note": "could not do it: the page froze", "shot": None})
            t_end = 0                                       # a frozen page answers nothing more: the rest is not tried
        except Exception as e:  # noqa: BLE001 - one action failing never stops the others
            out.append({"action": text, "ok": False, "note": f"could not do it: {type(e).__name__}: {str(e)[:100]}",
                        "shot": None})
        finally:
            try:
                if ctx is not None:
                    ctx.close()
            except Exception:  # noqa: BLE001
                pass
    return out


# ---------- the browser kept open between the render and the actions ------------------------------------------------------
class Hold:
    """The headless Chromium that drew the pictures, kept open on its own thread until close() so the reviewer's actions
    run in it. look.render_web fills it and attaches the worker that owns the browser. It closes itself after IDLE_S
    whatever happens, a request that does not come back in time or a Stop kills the browser by pid, and close() kills
    it when it does not end on its own, so no browser is left behind."""
    IDLE_S = 150.0

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.res: dict | None = None
        self.alive = False
        self.worker = None
        self.stop = None
        self._end = None

    @property
    def thread(self):
        return getattr(self.worker, "thread", None)

    def attach(self, worker, end, stop=None) -> None:
        """look.render_web: the worker that owns the browser, and end(why) that ends it (killed when why is set)."""
        self.worker, self._end, self.stop = worker, end, stop

    def detach(self) -> None:
        self._end = None

    def serve(self, browser, url: str, out_dir, tag: str, allowed=None) -> None:
        """Called on the browser's thread once the pictures are taken: answer action requests until closed."""
        self.alive = True
        end = time.time() + self.IDLE_S
        try:
            while True:
                try:
                    cmd = self.q.get(timeout=max(0.1, end - time.time()))
                except queue.Empty:
                    break
                if cmd is None:
                    break
                actions, reply = cmd
                reply.put(run_actions(browser, url, actions, out_dir, tag, allowed=allowed))
        finally:
            self.alive = False

    def _stopped(self) -> bool:
        try:
            return bool(self.stop and self.stop())
        except Exception:  # noqa: BLE001
            return False

    def run(self, actions: list, timeout: float = 60.0) -> list | None:
        """The results of the actions, or None when the browser is gone, the time ran out or Stop was pressed (the
        browser is then killed)."""
        if not self.alive:
            return None
        reply: queue.Queue = queue.Queue()
        self.q.put((actions, reply))
        t_end = time.time() + timeout
        while True:
            try:
                return reply.get(timeout=0.2)
            except queue.Empty:
                pass
            if self._stopped() or time.time() > t_end or not self.alive:
                self.kill()
                return None

    def kill(self) -> None:
        end, self._end = self._end, None
        if end is not None:
            end("killed")
        elif self.worker is not None:
            self.worker.kill()

    def close(self, wait: float = 15.0) -> None:
        self.q.put(None)
        end, self._end = self._end, None
        if self.worker is not None:
            self.worker.join(min(wait, 1.0) if self._stopped() else wait)
        if end is not None:
            end("killed" if self.worker is not None and self.worker.alive() else None)


# ---------- the messages the reviewer gets --------------------------------------------------------------------------------
def _files_line(target: dict) -> list:
    files = [os.path.basename(str(f)) for f in target.get("files") or []][:12]
    return ["Files made or changed this turn: " + ", ".join(files), ""] if files else []


def _evidence(kind: str, target: dict, res: dict, request: str, blind: bool, finding_lines: list) -> list:
    from meshpool_harness.agent import look as L
    steps = [s for s in res.get("steps") or [] if isinstance(s, dict)]
    what = (f"{target['label']}, opened in a headless browser at desktop width and at phone width" if kind == "web"
            else f"{target['label']}, drawn as pictures of {L.shown_pages(res)}")
    t = ["The request that led to this being made:", request or "(not available)", "", f"What was made: {what}.", ""]
    t += _files_line(target)
    if SCOPE_NOTE.get(request_scope(request)):
        t += [SCOPE_NOTE[request_scope(request)], ""]
    data = ["What meshpool measured:" if finding_lines else "What meshpool measured: no problems found by the checks."]
    data += ["- " + ln for ln in finding_lines[:30]]
    if steps:
        data += ["", "What happened when meshpool clicked and typed through it (not you, a scripted check):"]
        data += [f"- {'ok' if s.get('ok', True) else 'FAILED'}: {str(s.get('action', ''))[:120]}"
                 + (f" ({str(s.get('note'))[:160]})" if s.get("note") else "") for s in steps[:12]]
    if kind != "web" and res.get("text"):
        data += ["", "The text on those pages:", res["text"]]
    if res.get("blocked"):
        data += ["", L.blocked_line(res["blocked"])]
    t += L.fence(data, "the page and the checks")
    if kind != "web":
        t += ["", DOC_NOTE]
    return t


def _image_note(shots: list, blind: bool) -> list:
    if blind:
        return ["", "You cannot see pictures here, so judge from the measurements and the log."]
    if not shots:
        return ["", "No picture could be taken."]
    return ["", "Pictures attached, in this order: " + ", ".join(lbl for lbl, _ in shots) + "."]


def build_user(mode: str, kind: str, target: dict, res: dict, request: str, blind: bool, finding_lines: list,
               persona: str = "", acts: list | None = None) -> tuple[str, list]:
    """(text, [(label, path)]) for one reviewer call. mode: "plan" (persona and actions) or "verdict"."""
    text = _evidence(kind, target, res, request, blind, finding_lines)
    shots = [(lbl, f) for lbl, f in (res.get("shots") or [])][:4]
    if acts:
        from meshpool_harness.agent import look as L
        text += [""] + L.fence(["What happened when meshpool tried the actions you asked for:"] + [
            f"- {'ok' if a.get('ok') else 'DID NOT WORK'}: {a['action']}" + (f" -> {a['note']}" if a.get("note") else "")
            for a in acts], "the page")
        shots = shots[:2] + [(f"after: {a['action'][:40]}", a["shot"]) for a in acts if a.get("shot")]
    shots = shots[:MAX_REVIEW_IMAGES]
    text += _image_note([] if blind else shots, blind)
    text.append("")
    if mode == "plan":
        text.append(PLAN_ASK)
    else:
        if persona:
            text.append(f"You are: {persona}. Review it as that person.")
        else:
            text.append("First name the person who would use this, as in step 1, and put it in \"persona\".")
        text.append(VERDICT_ASK % (persona or "<the one line from step 1>"))
    return "\n".join(text), ([] if blind else shots)


def user_content(text: str, shots: list):
    """A plain string, or text with image parts for a model that takes images."""
    if not shots:
        return text
    from meshpool_harness.agent import look as L
    from meshpool_harness.agent.core import _image_part
    parts = [{"type": "text", "text": text}]
    for _, f in shots:
        im = L._image(f)
        if im:
            parts.append(_image_part(im))
    return parts if len(parts) > 1 else text


# ---------- running the review ---------------------------------------------------------------------------------------------
def _call(agent, turn: dict, text: str, shots: list, stats: dict) -> str:
    """One reviewer call: a fresh context (the reviewer's instructions and this one message), the chat's own model and
    endpoint. Adds its calls, tokens and seconds to stats."""
    if stats.get("tried", 0) >= MAX_REVIEW_CALLS:
        raise RuntimeError(f"the reviewer's limit of {MAX_REVIEW_CALLS} calls for this turn was reached")
    stats["tried"] = stats.get("tried", 0) + 1
    t0 = time.time()
    b = {k: turn.get(k, 0) for k in ("in", "out", "calls", "cached")}
    try:
        return agent._aux_complete(SYSTEM, user_content(text, shots), turn, max_tokens=900, source="look")
    finally:
        stats["secs"] = round(stats.get("secs", 0) + time.time() - t0, 2)
        for k in ("in", "out", "calls", "cached"):
            stats[k] = stats.get(k, 0) + max(0, int(turn.get(k, 0)) - int(b[k]))


RETRY_ASK = "That was not valid JSON. Reply with only the JSON object, nothing else."


def _json_call(agent, turn: dict, text: str, shots: list, stats: dict, good, st: dict) -> dict | None:
    """One reviewer call, read tolerantly. When the reply is not usable, one retry in a fresh context that shows the
    reviewer its own reply and asks for only the JSON object. Both calls are counted. None when the retry fails too."""
    reply = _call(agent, turn, text, shots, stats)
    d = parse_json(reply)
    if good(d):
        return d
    st["retries"] = st.get("retries", 0) + 1
    again = _call(agent, turn, text + "\n\nYour reply was:\n" + str(reply)[:4000] + "\n\n" + RETRY_ASK, [], stats)
    d = parse_json(again)
    return d if good(d) else None


def review(agent, turn: dict, kind: str, target: dict, res: dict, request: str, st: dict, *, blind: bool,
           finding_lines: list, hold: Hold | None = None, out_dir=None, tag: str = "r1", actions_fn=None,
           announce=None) -> dict | None:
    """The reviewer's verdict for this look, or None when it could not be had (the caller then sends the measured
    findings to the builder). The first look of a web page: one call for the persona and the actions it wants tried,
    the actions run, then one call for the verdict. Later looks and files: one call, with the persona kept."""
    stats = st.setdefault("reviewer", {"calls": 0, "in": 0, "out": 0, "cached": 0, "secs": 0.0})
    persona = st.get("persona", "")
    acts: list = []
    try:
        if kind == "web" and not persona:
            text, shots = build_user("plan", kind, target, res, request, blind, finding_lines)
            d = _json_call(agent, turn, text, shots, stats, lambda d: isinstance(d, dict) and bool(clean_persona(d.get("persona"), request, kind)[0]), st)
            persona, replaced = clean_persona((d or {}).get("persona"), request, kind)
            if not persona:
                st["why_not"] = "the reviewer did not name who would use it"
                return None
            st["persona"], st["persona_replaced"] = persona, replaced
            if announce:
                announce(persona)
            wanted = plan_actions((d or {}).get("actions"))
            st["actions"] = wanted
            if wanted:
                acts = (actions_fn or default_actions)(hold, target, wanted, out_dir, tag) or []
                st["action_results"] = acts
        text, shots = build_user("verdict", kind, target, res, request, blind, finding_lines, persona, acts)
        d = _json_call(agent, turn, text, shots, stats, lambda d: normalize_verdict(d, request, kind, persona) is not None, st)
        v = normalize_verdict(d, request, kind, persona)
        if v is None:
            st["why_not"] = "its answer could not be read"
            return None
        if not persona:                                                 # a file: the persona came with the verdict
            persona, replaced = clean_persona(v["persona"], request, kind)
            persona = persona or persona_fallback(kind)
            v["persona"] = st["persona"] = persona
            st["persona_replaced"] = replaced
            if announce:
                announce(persona)
        v = apply_choices_to_verdict(v, request)
        v = scope_filter(v, request, blind, finding_lines)
        v["actions"] = acts
        return v
    except Exception as e:  # noqa: BLE001 - a reviewer that fails never breaks the turn (Stop and the budget pass through)
        from meshpool_harness.agent.core import Interrupted
        from meshpool_harness.core import usage
        if isinstance(e, (Interrupted, KeyboardInterrupt, usage.BudgetExceeded)):
            raise
        st["why_not"] = f"the reviewer call failed: {str(e)[:100]}"
        return None


def default_actions(hold, target: dict, actions: list, out_dir, tag: str) -> list:
    """Run the actions in the browser that drew the pictures (the Hold). No hold: no browser is opened for them."""
    got = hold.run(actions) if hold is not None else None
    if got is None:                                                    # the browser was gone: say so, do not pretend
        return [{"action": action_text(a), "ok": False, "note": "not tried: the browser was not available", "shot": None}
                for a in actions]
    return got


# ---------- what the builder reads and the line the user reads --------------------------------------------------------------
def verdict_lines(v: dict) -> list:
    out = [f"- [{p['severity']}] {p['what']}" + (f" (where: {p['where']})" if p.get("where") else "") for p in v["problems"]]
    return out or ["- nothing wrong that the reviewer could see"]


def action_lines(v: dict) -> list:
    return [f"- {'ok' if a.get('ok') else 'DID NOT WORK'}: {a['action']}" + (f" -> {a['note']}" if a.get("note") else "")
            for a in v.get("actions") or []]


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(s).lower()) if len(w) > 2}


def _same(a: dict, b: dict) -> bool:
    x, y = _words(a["what"] + " " + a["where"]), _words(b["what"] + " " + b["where"])
    return bool(x and y) and len(x & y) / len(x | y) >= 0.4


_STOPWORDS = {"a", "an", "the", "of", "and", "or", "to", "with", "for", "in", "on", "at", "by", "from", "as", "is", "are",
              "it", "its", "that", "this", "but", "so", "when", "while", "than", "then", "if", "into", "onto", "after",
              "before", "not", "no", "be", "was", "were", "shows", "show", "says"}
# a problem the reviewer could not check is not evidence that anything is wrong
_UNSURE = re.compile(r"^(?:i\s+)?(?:can(?:'|no)?t|cannot|could\s?n[o']t|unable to|not able to)\s+"
                     r"(?:confirm|verify|tell|check|be sure|see whether|see if|determine)\b|"
                     r"^(?:not sure|unclear|it is unclear|it's unclear|unknown)\b", re.I)


def unsure(p: dict) -> bool:
    """True for a problem that only says the reviewer could not check something ("can't confirm the page says where
    the open house is"): not evidence that it is still wrong."""
    return bool(_UNSURE.search(re.sub(r"\s+", " ", str(p.get("what") or "")).strip()))


def _phrase(p: dict, limit: int = 80) -> str:
    """The problem as a short clean phrase for the Checked line: one clause, never "could not find" or an "instead of"
    tail, never ending in the middle of a word or on a small word."""
    t = re.sub(r"\s+", " ", str(p["what"])).strip().rstrip(".")
    t = t.split(";")[0].strip()
    t = re.split(r",? so (?:i|she|he|they|a|the|keyboard|it|you|we)\b|, which\b|, because\b|\s+instead of\b|"
                 r"\s+when (?:looking|checking|i|she|he|they)\b|\s+-\s+|:\s", t, 1, flags=re.I)[0].rstrip(" ,;")   # the thing, not the why
    t = re.sub(r"\s*\([^()]*\)", "", t)                                   # ('not-an-email'), (where: ...)
    t = re.sub(r"^(?:i\s+)?(?:could\s?n[o']t|can(?:no|')?t|unable to|did\s?n[o']t)\s+(?:find|see|locate)\s+"
               r"(?:the\s+|any\s+|an?\s+)?", "missing ", t, flags=re.I)
    m = re.match(r"^(?:the\s+)?(.+?)\s+(?:could\s?n[o']t|can(?:no|')?t)\s+be\s+(?:found|seen|located)\b", t, re.I)
    if m:
        t = "missing " + m.group(1)
    t = re.sub(r"\s+(?:on the page|anywhere)?\s*(?:at|on)\s+(?:the\s+)?(?:(?:phone|desktop|mobile|narrow|wide)\s+"
               r"(?:width|screen size|size)|\d+\s*px(?:\s+width)?)$", "", t, flags=re.I)
    t = re.sub(r"\s+(?:on the page|anywhere on the page)$", "", t, flags=re.I).strip(" ,;:")
    t = t[:1].lower() + t[1:] if t[:2] != t[:2].upper() else t
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0]                                    # never in the middle of a word
    words = t.split(" ")
    while len(words) > 1 and words[-1].lower().strip(",;:") in _STOPWORDS:
        words.pop()                                                     # never on "an", "the", "of"
    t = " ".join(words).rstrip(" ,;:")
    for o, c in (("(", ")"), ("'", "'"), ('"', '"')):
        if (t.count(o) - t.count(c) if o != c else t.count(o) % 2) > 0:
            t = t[:t.rfind(o)].rstrip(" ,;:")                           # an open quote or bracket: cut before it
    return t


def _join(xs: list) -> str:
    from meshpool_harness.agent import look as L
    return L._join(xs)



# ---------- the line the user reads: a fixed template over structured data -----------------------------------------------
# "Checked as <short persona>: fixed <N> things: <item> and <item>. Nothing left open." Every part is built from the
# verdicts' own fields. A part that would be malformed (an empty persona, an item that ends on a small word) is left
# out, never printed as a fragment.
_DANGLING = _STOPWORDS | {"it's", "there", "has", "have", "had", "can", "could", "would", "should", "does", "do",
                          "did", "will", "only", "just", "very", "also", "about", "over", "under", "per", "via", "like",
                          "such", "some", "any", "each", "every", "both", "e.g", "i.e", "etc", "my", "your", "our",
                          "their", "her", "his", "she", "he", "they", "we", "i", "you", "nor", "yet", "because",
                          "which", "who", "whose", "where", "what", "how", "up", "out", "off", "between", "through",
                          "across", "against", "without", "within"}
_NOT_VERBS = {"morning", "evening", "during", "something", "anything", "nothing", "everything", "ceiling", "wedding",
              "building", "meeting", "booking", "clothing", "pricing", "shopping", "housing", "training"}
PERSONA_WORDS = 8
_NOT_VERBS |= {"thing", "king", "ring", "spring", "string", "wing", "bring"}


def _gerund(w: str) -> bool:
    return bool(re.fullmatch(r"[a-z]{2,}ing", w.lower())) and w.lower() not in _NOT_VERBS
ITEM_CHARS = 60


def _last_word(t: str) -> str:
    return re.sub(r"[^\w']+$", "", t.rsplit(" ", 1)[-1]).lower()


def _fine(t: str) -> bool:
    """True when t is a whole phrase: not empty, no doubled spaces, no orphan punctuation, no open quote or bracket, and
    not ending on an article, a preposition, a conjunction or a helper word."""
    if not t or len(t) < 3 or t != t.strip() or "  " in t or "…" in t or ".." in t:
        return False
    if re.match(r"^[\W_]", t) and not re.match(r"^['\"(]\w", t):
        return False                                           # starts on punctuation
    if re.search(r"[,;:\-(\[/\\]$", t) or re.search(r"\s[,;:.)]|[,;:]{2,}|\(\s|\s\)", t):
        return False                                           # an orphan comma, colon, dash or bracket
    for o, c in (("(", ")"), ("[", "]")):
        if t.count(o) != t.count(c):
            return False
    if t.count('"') % 2:
        return False
    if len(re.findall(r"(?<!\w)'|'(?!\w)", t)) % 2:
        return False                                           # an open single quote (an apostrophe inside a word is fine)
    if t.lower().rstrip(".!?").endswith(("so it's", "so it is", "e.g", "i.e", "etc")):
        return False
    return _last_word(t) not in _DANGLING


def short_persona(p: str, words: int = PERSONA_WORDS) -> str:
    """The persona in at most `words` words, cut at a word and never on a small word or a clause opener ("ordering a").
    "" when nothing whole is left: the line then says "Checked:" alone."""
    t = re.sub(r"\s*\([^()]*\)", "", re.sub(r"\s+", " ", str(p or ""))).strip(" ,;:.\"'")
    t = re.split(r"[;:]\s|,\s|\s[-–]\s", t, 1)[0]                # the first clause
    if t.split(" ")[0] in ("A", "An", "The", "Someone", "Anyone", "One", "Her", "His", "Their"):
        t = t[:1].lower() + t[1:]
    ws = t.split(" ") if t else []
    cut = len(ws) > words
    if cut:                                                    # who they are, not what they are doing: cut before "reading ..."
        g = next((i for i in range(2, len(ws)) if _gerund(ws[i])), 0)
        if g and g <= words:
            ws = ws[:g]
    ws = ws[:words]
    popped = False
    while ws and (_last_word(ws[-1]) in _DANGLING
                  or ((cut or popped) and len(ws) > 3 and _gerund(_last_word(ws[-1])))):
        ws.pop()
        popped = True                                           # never on "a", "the", "of", or a verb that opens a clause
    t = " ".join(ws).strip(" ,;:")
    return t if _fine(t) else ""


_QUOTED = re.compile(r"""(?:(?<![\w'])'[^'\n]{1,160}'(?!\w))|(?:"[^"\n]{1,160}")|(?:‘[^’\n]{1,160}’)|(?:“[^”\n]{1,160}”)""")
_SPLIT = re.compile(r";\s|:\s|,\s|\s[-–—]\s|\s\(|\s(?:so|because|which|but)\s",
                    re.I)
_DROP_QUOTE = re.compile(r"(?<!\bsays )(?<!\breads )(?<!\bshows )(?<!\bwith )(?<!\bas )(?<!\bis )(?<!\bare )"
                         r"(?:%s)(?=\s+(?:is|are|was|were|appears?|looks?|reads?|repeats?|has|does)\b)" % _QUOTED.pattern)


_HARD_STOP = re.compile(r";\s|:\s|\s[-\u2013\u2014]\s")
_EXTRA_CUT = re.compile(r"\s+(?:on|at|in) the (?:page|screen|phone|desktop|mobile)\b|\s+at (?:phone|desktop|mobile|narrow|\d+px)\b|\swhen\s",
                        re.I)
_MISSING = re.compile(r"^(?:i\s+)?(?:could\s?n[o']t|can(?:no|')?t|unable to|did\s?n[o']t)\s+(?:find|see|locate)\s+"
                      r"(?:the\s+|any\s+|an?\s+)?", re.I)


def _item(p: dict, limit: int = ITEM_CHARS) -> str:
    """One problem as its own whole phrase of at most `limit` characters: the reviewer's own words, cut only at a break
    (a comma, a colon, "so", "because", "on the page"...) that is not inside a quote, as long as the part before it reads
    whole. The longest such part wins. "" when none fits: a phrase is never cut mid-way."""
    w = _MISSING.sub("missing ", re.sub(r"\s+", " ", str(p.get("what") or "")).strip().rstrip("."))
    for cand in (w, _DROP_QUOTE.sub("", w)):
        cand = re.sub(r"\s+", " ", cand).strip(" ,;:")
        spans = [m.span() for m in _QUOTED.finditer(cand)]
        cuts = {m.start() for rx in (_SPLIT, _EXTRA_CUT) for m in rx.finditer(cand)
                if not any(a < m.start() < b for a, b in spans)} | {len(cand)}
        hard = [m.start() for m in _HARD_STOP.finditer(cand) if not any(a < m.start() < b for a, b in spans)]
        for c in sorted((x for x in cuts if not hard or x <= hard[0]), reverse=True):   # what follows a colon only explains
            t = cand[:c].strip(" ,;:")
            if t[:1].isupper() and not t[:2].isupper():
                t = t[:1].lower() + t[1:]
            if 8 <= len(t) <= limit and _fine(t):
                return t
    return ""


def _items(ps: list, n: int = 2, limit: int = ITEM_CHARS) -> list:
    out: list = []
    for p in ps:
        t = _item(p, limit) if isinstance(p, dict) else (str(p) if _fine(str(p)) and len(str(p)) <= limit else "")
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
        if len(out) >= n:
            break
    return out


def _things(n: int) -> str:
    return f"{n} thing" + ("" if n == 1 else "s")


# The reviewer says it could not see part of what it was asked to judge (Sara: "the pages shown only cover section 1
# ... I can't see section 3's tracked deletions"): its "pass" then covers less than the request.
_BLIND = re.compile(r"\b(?:can(?:'|’|no)?t|cannot|could ?not|couldn(?:'|’)t|unable to|did ?not|didn(?:'|’)t"
                    r"|was not able to|wasn(?:'|’)t able to)\s+(?:see|view|check|verify|confirm|find|judge|tell)\b"
                    r"|\bonly (?:covers?|shows?|includes?)\b|\bnot (?:shown|visible|included|in the pictures?|on the pages? "
                    r"shown)\b|\b(?:pages?|slides?|pictures?) shown (?:only|do not|don't)\b", re.I)


def blind_spot(text: str) -> bool:
    return bool(_BLIND.search(str(text or "")))


def _also_offer(st: dict) -> str:
    """"I also noticed <one thing outside the request>, want me to fix it?" for the user, or "". It is never sent to the
    builder."""
    evals = st.get("evals") or []
    if not evals:
        return ""
    known = [p for v in evals for p in v["problems"]]
    for v in reversed(evals):
        t = _also_line(v.get("also"))
        if not t or blind_spot(t):
            continue                                            # the reviewer's own view, not a thing to fix
        w = _words(t)
        if w and any(len(w & _words(p["what"] + " " + p["where"])) / len(w) >= 0.4 for p in known):
            continue                                            # the builder was told about it: not news
        t = t[:1].lower() + t[1:] if t[:2] != t[:2].upper() else t
        if len(t) > 110:
            t = t[:110].rsplit(" ", 1)[0]
        t = t.strip(" ,;:")
        if _fine(t):
            return f"I also noticed {t}, want me to fix it?"
    return ""


def summary_line(kind: str, st: dict, extra: list | None = None, note: str = "") -> str:
    """The closing line, from the verdicts' fields and a fixed template:
        "Checked as a customer on her phone ordering a birthday cake: fixed 2 things: the order form error and low
        contrast. Still open: no delivery date picker."
    The persona is at most 8 words and each item one whole phrase of at most 60 characters. Something is "still open" only
    when the last look shows it (a "could not confirm" is not evidence), or `extra`, the last measurement, has it. When the
    builder changed the page after the only verdict and no later verdict could be had, it says "changed ... after the
    check". What the reviewer noticed outside the request follows as one offer on its own line."""
    evals = st.get("evals") or []
    persona = short_persona(st.get("persona") or persona_fallback(kind))
    first, last = evals[0], evals[-1]
    known = [p for p in first["problems"] if not unsure(p)]
    seen_last = [p for p in last["problems"] if not unsure(p)]
    solid = [p for p in seen_last if not hedged(p)]            # what the last verdict states, not guesses
    shaky = not last.get("pass") and len(solid) != len(last["problems"])    # not a pass, and it held a guess or "could not confirm"
    changed = False
    if len(evals) > 1:
        fixed = [p for p in known if not any(_same(p, q) for q in seen_last)]
        left = solid
    elif st.get("changed"):                       # fixed after this verdict, then no reviewer saw it again
        fixed, left, changed = known, [], True
    else:
        fixed, left = [], solid
    whole = lambda t: len(t.split()) >= 3                       # "section 3" says nothing: not printed
    left_items = [t for t in _items(left, 2) if whole(t)] if left else []
    measured = [x for x in (extra or []) if not any(_words(x) & _words(q) for q in left_items)]
    open_items = left_items or [t for t in _items(measured, 1) if whole(t)]
    if left and not left_items:
        shaky = True                                            # open, but nothing worth saying about it
    n_open = len(open_items)
    head = f"Checked as {persona}" if persona else "Checked"
    listed = _items(fixed, 2)
    tail = (": " + "; ".join(listed)) if listed else ""
    if changed:
        body = f"changed {_things(len(fixed))} after the check, not looked at again{tail}" if fixed else "found nothing to fix"
    elif fixed:
        body = f"fixed {_things(len(fixed))}{tail}"
    elif n_open:
        body = "fixed nothing"
    elif shaky or blind_spot(last.get("also")):
        body = ""                                               # unsure, contradictory, or it could not see it all
    else:
        body = "found nothing to fix"
        pg = (st.get("last") or {}).get("pages") or {} if kind != "web" else {}
        if pg.get("shown") and pg.get("total") and len(pg["shown"]) < pg["total"]:
            from meshpool_harness.agent import look as L
            body += " on " + L.pages_phrase(pg["shown"], pg["total"])    # no more than it looked at
    if not body:
        parts = [f"Checked it as {persona}." if persona else "Checked it."]
    else:
        parts = [f"{head}: {body}."]
    if n_open:
        parts.append("Still open: " + "; ".join(open_items) + ".")
    elif fixed and not changed and not shaky:
        parts.append("Nothing left open.")
    if note:
        parts.append(note)
    line = " ".join(parts)
    offer = _also_offer(st)
    return line + ("\n\n" + offer if offer else "")
