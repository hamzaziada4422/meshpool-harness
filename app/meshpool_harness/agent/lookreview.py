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
from urllib.parse import urlparse

MAX_ACTIONS = 4
MAX_PROBLEMS = 10
MAX_REVIEW_IMAGES = 6
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
- Answer with one JSON object and nothing else."""

PLAN_ASK = ("""Reply with this JSON object:
{"persona": "<the one line from step 1>", "actions": [ ... ]}
actions: up to %d things you want to see tried, that the pictures and the log do not show yet. Each one is
{"do": "click" | "fill" | "submit" | "open_menu" | "press" | "look_at", "target": "<the button, link, field or section, in its visible words>", "text": "<what to type, for fill only>", "submit": true | false, "width": "desktop" | "phone"}
Examples: click the "Order now" button; fill the email field with "x" and submit it; open the menu at phone width. "submit" on its own fills the form with sample values and sends it. Use [] when you have seen enough. meshpool tries each one on a fresh copy of the page, in the same browser, and tells you what happened. You get this one chance.""" % MAX_ACTIONS)

VERDICT_ASK = """Reply with this JSON object:
{"persona": "%s", "can_do_the_job": true | false, "problems": [{"what": "<short>", "where": "<where>", "severity": "blocker" | "major" | "minor"}], "pass": true | false}
Use [] for problems when there are none."""

DOC_NOTE = ("This is a file, not a page: you see its first pages as pictures and the text on them. You cannot click "
            "anything, so judge what a person reading or using the file would see.")

_BAD_PERSONA = re.compile(r"\b(?:who|that)\s+(?:asked|requested|wrote|commissioned|ordered)\b|"
                          r"^(?:you|me|i|myself|the requester|the requestor|the author|the owner|the user|a user|"
                          r"the person who)\b", re.I)
_SELF_USE = re.compile(r"\b(?:for me|for myself|i(?:'ll| will| am going to| want to| need to) (?:use|read|run|open)|"
                       r"my own use|just for me|i will be the)\b", re.I)


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
    """The first JSON object in a reply (a code fence or a sentence around it is fine). None when there is none."""
    t = str(reply or "")
    t = re.sub(r"```(?:json)?", "", t)
    i = t.find("{")
    while i != -1:
        depth, in_s, esc = 0, False, False
        for j in range(i, len(t)):
            c = t[j]
            if in_s:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_s = False
            elif c == '"':
                in_s = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        d = json.loads(t[i:j + 1])
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
    probs = []
    for p in d.get("problems") or []:
        if isinstance(p, str) and p.strip():
            p = {"what": p}
        if isinstance(p, dict) and str(p.get("what") or "").strip():
            probs.append({"what": re.sub(r"\s+", " ", str(p["what"])).strip()[:240],
                          "where": re.sub(r"\s+", " ", str(p.get("where") or "")).strip()[:120],
                          "severity": _sev(p.get("severity"))})
    probs.sort(key=lambda p: SEVERITIES.index(p["severity"]))
    probs = probs[:MAX_PROBLEMS]
    can = d.get("can_do_the_job")
    can = (not any(p["severity"] == "blocker" for p in probs)) if can is None else bool(can)
    if any(p["severity"] == "blocker" for p in probs):
        can = False
    ok = bool(d.get("pass")) and can and not any(p["severity"] in ("blocker", "major") for p in probs)
    return {"persona": persona, "can_do_the_job": can, "problems": probs, "pass": ok}


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
    return out


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


def _local(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme in ("file", "data", "blob", "about") or u.hostname in ("127.0.0.1", "localhost", "::1")


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


def run_actions(browser, url: str, actions: list, out_dir, tag: str, deadline_s: float = 30.0) -> list:
    """Try each requested action on a fresh page in `browser`. Returns [{"action", "ok", "note", "shot"}]. Only local
    pages are opened; every request to another host is blocked. Never raises."""
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
            ctx.route("**/*", lambda r: r.continue_() if _local(r.request.url) else r.abort())
            page = ctx.new_page()
            errs: list = []
            page.on("pageerror", lambda e, p=errs: p.append(f"script error: {str(e)[:120]}"))
            page.on("console", lambda m, p=errs: m.type == "error" and p.append(f"console error: {m.text[:120]}"))
            page.on("dialog", lambda d: (errs.append(f"the page showed an alert: {d.message[:100]}"), d.dismiss()))
            page.goto(url, wait_until="load", timeout=12000)
            page.wait_for_timeout(200)
            before = page.evaluate(_SNAP_JS)
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
                el = _find_click(page, a["target"]) if a["target"] else None
                if el is not None:
                    el.scroll_into_view_if_needed(timeout=2000)
                elif a["target"]:
                    ok, note_pre = False, f"could not find '{a['target']}' on the page"
            page.wait_for_timeout(450)
            after = page.evaluate(_SNAP_JS)
            changed, note = _change_note(before, after)
            if errs:
                note = (note + "; " if note else "") + "; ".join(dict.fromkeys(errs))[:200]
            if ok and not changed and do in ("click", "submit", "open_menu") and not errs:
                ok, note = False, "nothing visibly changed"
            shot = None
            try:
                f = Path(out_dir) / f"{tag}-act{n}.jpg"
                page.screenshot(path=str(f), type="jpeg", quality=60)
                shot = str(f)
            except Exception:  # noqa: BLE001
                pass
            out.append({"action": text, "ok": ok, "note": (note_pre + (note or "")).strip("; ")[:420], "shot": shot})
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
    run in it. look._web_once fills it. It closes itself after IDLE_S whatever happens, so no browser is left behind."""
    IDLE_S = 150.0

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.res: dict | None = None
        self.alive = False
        self.thread: threading.Thread | None = None

    def serve(self, browser, url: str, out_dir, tag: str) -> None:
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
                reply.put(run_actions(browser, url, actions, out_dir, tag))
        finally:
            self.alive = False

    def run(self, actions: list, timeout: float = 60.0) -> list | None:
        if not self.alive:
            return None
        reply: queue.Queue = queue.Queue()
        self.q.put((actions, reply))
        try:
            return reply.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self, wait: float = 15.0) -> None:
        self.q.put(None)
        if self.thread is not None:
            self.thread.join(wait)


# ---------- the messages the reviewer gets --------------------------------------------------------------------------------
def _files_line(target: dict) -> list:
    files = [os.path.basename(str(f)) for f in target.get("files") or []][:12]
    return ["Files made or changed this turn: " + ", ".join(files), ""] if files else []


def _evidence(kind: str, target: dict, res: dict, request: str, blind: bool, finding_lines: list) -> list:
    steps = [s for s in res.get("steps") or [] if isinstance(s, dict)]
    what = (f"{target['label']}, opened in a headless browser at desktop width and at phone width" if kind == "web"
            else f"{target['label']}, drawn as pictures of its first pages")
    t = ["The request that led to this being made:", request or "(not available)", "", f"What was made: {what}.", ""]
    t += _files_line(target)
    t.append("What meshpool measured:" if finding_lines else "What meshpool measured: no problems found by the checks.")
    t += ["- " + ln for ln in finding_lines[:30]]
    if steps:
        t += ["", "What happened when meshpool clicked and typed through it (not you, a scripted check):"]
        t += [f"- {'ok' if s.get('ok', True) else 'FAILED'}: {str(s.get('action', ''))[:120]}"
              + (f" ({str(s.get('note'))[:160]})" if s.get("note") else "") for s in steps[:12]]
    if kind != "web":
        t += ["", DOC_NOTE]
        if res.get("text"):
            t += ["", "The text on those pages:", res["text"]]
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
        text += ["", "What happened when meshpool tried the actions you asked for:"]
        text += [f"- {'ok' if a.get('ok') else 'DID NOT WORK'}: {a['action']}" + (f" -> {a['note']}" if a.get("note") else "")
                 for a in acts]
        shots += [(f"after: {a['action'][:40]}", a["shot"]) for a in acts if a.get("shot")]
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
    t0 = time.time()
    b = {k: turn.get(k, 0) for k in ("in", "out", "calls", "cached")}
    try:
        return agent._aux_complete(SYSTEM, user_content(text, shots), turn, max_tokens=900, source="look")
    finally:
        stats["secs"] = round(stats.get("secs", 0) + time.time() - t0, 2)
        for k in ("in", "out", "calls", "cached"):
            stats[k] = stats.get(k, 0) + max(0, int(turn.get(k, 0)) - int(b[k]))


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
            d = parse_json(_call(agent, turn, text, shots, stats))
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
        d = parse_json(_call(agent, turn, text, shots, stats))
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
    if hold is None:
        return []
    return hold.run(actions) or []


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


def _phrase(p: dict) -> str:
    t = re.sub(r"\s+", " ", p["what"]).strip().rstrip(".")
    t = t[:1].lower() + t[1:] if t[:2] != t[:2].upper() else t
    return t[:90]


def _join(xs: list) -> str:
    from meshpool_harness.agent import look as L
    return L._join(xs)


def summary_line(kind: str, st: dict, extra: list | None = None) -> str:
    """The closing line from the reviewer's first and last verdicts: "Checked: a customer on her phone ordering a
    birthday cake tried it; fixed the order form error and low contrast; still open: no delivery date picker."."""
    evals = st.get("evals") or []
    persona = st.get("persona") or persona_fallback(kind)
    verb = "tried it" if kind == "web" else "looked it over"
    first, last = evals[0], evals[-1]
    fixed = [p for p in first["problems"] if not any(_same(p, q) for q in last["problems"])] if len(evals) > 1 else []
    left = [] if last["pass"] and not last["problems"] else last["problems"]
    measured = [x for x in (extra or []) if not any(_words(x) & _words(_phrase(p)) for p in left)]
    bits = []
    if fixed:
        bits.append("fixed " + _join(list(dict.fromkeys(_phrase(p) for p in fixed))))
    if left or measured:
        bits.append(("minor things left: " if last["pass"] and not measured else "still open: ")
                    + _join(list(dict.fromkeys([_phrase(p) for p in left] + measured))))
    if not bits:
        bits.append("no problems found")
    return f"Checked: {persona} {verb}; " + "; ".join(bits) + "."
