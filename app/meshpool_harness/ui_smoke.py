"""UI smoke: use a page the way a person would, not just look at it. ui_audit measures how a page looks; this clicks
every control, submits every form with empty and bad input, presses keys, reloads, and says in plain words what did
nothing, what broke and what was lost.

What it finds: buttons that do nothing, errors in the console, forms that accept empty or bad input, forms that give no
sign they were sent, controls only a mouse can reach, state lost on reload in an app that has no saved data, a game
canvas that never changes when keys are pressed, links that go nowhere, and a page that left the site on a click.

smoke(pw, url) takes a started Playwright and returns {"findings": [...], "text": "..."}. It passes when the text says
"No problems found". Findings have rule, sev ("must" or "should") and msg.
"""
from __future__ import annotations

import re
import time

from meshpool_harness import ui_audit as UA

STATE_JS = r"""() => {
  let ls = ""; try { ls = JSON.stringify(localStorage) + JSON.stringify(sessionStorage); } catch (e) {}
  const c = [...document.querySelectorAll("canvas")].map(c => { try { return c.toDataURL().length + ":" + c.toDataURL().slice(-60); } catch (e) { return "x"; } }).join("|");
  let attrs = 0; for (const e of document.body.querySelectorAll("*")) { const k = (e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className) + "|" + (e.getAttribute("aria-pressed") || "") + (e.getAttribute("aria-selected") || "") + (e.getAttribute("aria-expanded") || "") + (e.getAttribute("aria-checked") || "") + (e.hidden ? "h" : "") + (e.getAttribute("style") || ""); for (let i = 0; i < k.length; i++) attrs = (attrs * 31 + k.charCodeAt(i)) | 0; }
  const tbl = [...document.querySelectorAll("table")].map(t => [...t.querySelectorAll("tr")].slice(0, 8).map(r => r.textContent).join("|") + [...t.querySelectorAll("[aria-sort]")].map(h => h.getAttribute("aria-sort")).join()).join("~");
  return attrs + ":" + tbl + ":" + document.body.innerHTML.length + "#" + (document.body.innerText || "").replace(/\s+/g, " ") + "#" + ls + "#" + location.href + "#" + c +
    "#" + [...document.querySelectorAll("input,select,textarea")].map(e => e.type === "checkbox" || e.type === "radio" ? e.checked : e.value).join(",") +
    "#" + [...document.querySelectorAll("dialog,[role=dialog],details")].map(e => e.open ?? !e.hidden).join("");
}"""

CONTROLS_JS = r"""() => {
  const vis = e => { const s = getComputedStyle(e), b = e.getBoundingClientRect(); return s.visibility !== "hidden" && s.display !== "none" && b.width > 0 && b.height > 0; };
  const mark = (e, kind) => { e.setAttribute("data-smoke", kind + "-" + (window.__sm = (window.__sm || 0) + 1)); return e.getAttribute("data-smoke"); };
  const name = e => ((e.innerText || e.value || e.getAttribute("aria-label") || e.title || e.id || e.tagName) + "").trim().replace(/\s+/g, " ").slice(0, 30);
  const btns = [...document.querySelectorAll("button, [role=button], input[type=button], input[type=submit], [role=tab], summary")].filter(e => vis(e) && !e.disabled && e.getAttribute("aria-disabled") !== "true")
    .map(e => ({id: mark(e, "b"), name: name(e), on: e.getAttribute("aria-pressed") === "true" || e.getAttribute("aria-selected") === "true" || !!e.getAttribute("aria-current") || /\b(active|selected|current|is-on)\b/.test(e.getAttribute("class") || ""), submit: !!e.form && e.type === "submit"}));
  const anchors = [...document.querySelectorAll("a[href]")].filter(vis).map(a => ({id: mark(a, "a"), name: name(a), href: a.getAttribute("href")}));
  const forms = [...document.forms].filter(vis).map(f => ({id: mark(f, "f"), fields: [...f.elements].filter(e => e.name || e.id).length}));
  const loose = [...document.querySelectorAll("div,span,li,td,img,svg")].filter(e => vis(e) && (e.hasAttribute("onclick") || getComputedStyle(e).cursor === "pointer") &&
    !e.closest("button,a,[role=button],[role=tab],label,select,summary") && !e.hasAttribute("tabindex") && !e.getAttribute("role"))
    .filter(e => !e.querySelector("button,a,[role=button],input"))
    .slice(0, 6).map(e => name(e) || e.tagName);
  const fields = [...document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), textarea")].filter(vis).length;
  return {btns, anchors, forms, loose, fields, canvas: document.querySelectorAll("canvas").length};
}"""

FILL_JS = r"""() => { for (const e of document.querySelectorAll("input, textarea")) {
  if (e.disabled || e.readOnly || !e.offsetParent || e.value) continue;
  const t = e.type; const v = t === "number" || t === "range" ? "3" : t === "email" ? "smoke@example.com" : t === "tel" ? "5551234567" : t === "url" ? "https://example.com" : ["text", "search", ""].includes(t) || e.tagName === "TEXTAREA" ? "Smoke test" : null;
  if (v === null) continue; e.value = v; e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); } }"""

BAD_VALUE = {"email": "not-an-email", "tel": "abc", "number": "-5", "url": "nope", "date": "", "time": ""}


def _plain(msg: str) -> str:
    return msg.replace("\n", " ")[:200]


def smoke(pw, url: str, launch=None, view=(1280, 800)) -> dict:
    shown = UA._norm(url)
    addr, stop = UA._serve(shown)
    browser = (launch or UA.launcher(pw))()
    out: list[dict] = []
    errs: list[str] = []

    def add(rule, sev, msg):
        if not any(o["rule"] == rule and o["msg"] == msg for o in out):
            out.append({"rule": rule, "sev": sev, "msg": _plain(msg)})

    try:
        ctx = browser.new_context(viewport={"width": view[0], "height": view[1]})
        page = ctx.new_page()
        origin = {"v": ""}
        page.on("pageerror", lambda e: errs.append(f"script error: {str(e)[:140]}"))
        page.on("console", lambda m: m.type == "error" and errs.append(f"console error: {m.text[:140]}"))
        page.on("requestfailed", lambda r: errs.append(f"file failed to load: {r.url[:100]}") if not r.url.endswith("favicon.ico") else None)
        page.on("dialog", lambda d: d.accept("test"))
        did = {"n": 0}                              # a download, a popup or a dialog is an effect too
        page.on("download", lambda d: did.__setitem__("n", did["n"] + 1))
        page.on("popup", lambda d: did.__setitem__("n", did["n"] + 1))
        page.on("dialog", lambda d: did.__setitem__("n", did["n"] + 1))
        page.goto(addr, wait_until="load", timeout=20000)
        page.wait_for_timeout(500)
        origin["v"] = page.url.split("#")[0]
        inv = page.evaluate(CONTROLS_JS)
        n_btn = len(inv["btns"])

        # 1. every button: does it do anything?
        dead, checked = [], 0
        for b in inv["btns"][:30]:
            el = page.query_selector(f'[data-smoke="{b["id"]}"]')
            if not el or not el.is_visible():
                continue
            if b["submit"] or b["on"] or re.match(r"(?i)^\W*(clear|reset|cancel|close|undo|dismiss|back|previous|prev|export|download|print|copy|share)\b", b["name"] or ""):
                continue                    # these legitimately do nothing when there is nothing to clear
            try:
                page.evaluate(FILL_JS)                 # a button like Add needs something to add
                before = page.evaluate(STATE_JS)
                n0 = did["n"]
                el.click(timeout=1500)
                page.wait_for_timeout(250)
                checked += 1
                if page.url.split("#")[0] != origin["v"]:
                    page.goto(addr, wait_until="load")
                    page.wait_for_timeout(300)
                    page.evaluate(CONTROLS_JS)
                    continue
                if page.evaluate(STATE_JS) == before and did["n"] == n0:
                    dead.append(b["name"] or "(no name)")
            except Exception:  # noqa: BLE001  (covered, moved or removed by an earlier click)
                continue
        if dead:
            add("dead-button", "must", f"{len(dead)} of {checked} buttons did nothing visible when clicked: " + ", ".join(f'"{d}"' for d in dead[:6]) +
                ". Wire each one up, or remove it")

        # 2. links that go nowhere
        bad = [a["name"] for a in inv["anchors"] if a["href"].strip() in ("#", "", "javascript:void(0)", "javascript:;")]
        if bad:
            add("dead-link", "must", f"{len(bad)} links point nowhere (href \"#\"): " + ", ".join(f'"{b}"' for b in bad[:6]) + ". Link them to a section or remove them")
        missing = []
        for a in inv["anchors"]:
            h = a["href"].strip()
            if h.startswith("#") and len(h) > 1 and not page.evaluate("id => !!document.getElementById(id) || !!document.getElementsByName(id).length", h[1:]):
                missing.append(a["name"])
        if missing:
            add("dead-link", "must", f"links to a part of the page that does not exist: " + ", ".join(f'"{m}"' for m in missing[:6]))

        # 3. forms: empty and bad input
        page.goto(addr, wait_until="load")
        page.wait_for_timeout(300)
        inv = page.evaluate(CONTROLS_JS)
        for f in inv["forms"][:3]:
            sel = f'[data-smoke="{f["id"]}"]'
            info = page.evaluate("""s => { const f = document.querySelector(s);
              const els = [...f.elements].filter(e => e.name || e.id);
              return {required: els.filter(e => e.required).length, inputs: els.filter(e => !["submit", "button", "hidden"].includes(e.type) && e.tagName !== "BUTTON" && e.tagName !== "FIELDSET").length,
                      noval: f.noValidate, types: els.map(e => e.type)}; }""", sel)
            if info["inputs"] == 0:
                continue
            sub = page.query_selector(f'{sel} button[type=submit], {sel} input[type=submit], {sel} button:not([type])')
            if not sub:
                add("form-no-submit", "must", "a form has no submit button")
                continue
            before = page.evaluate(STATE_JS)
            try:
                sub.click(timeout=1500)
                page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001
                continue
            after = page.evaluate(STATE_JS)
            invalid = page.evaluate("s => document.querySelector(s).querySelectorAll(':invalid').length", sel) if page.query_selector(sel) else 0
            if page.url.split("#")[0] != origin["v"]:
                add("form-accepts-empty", "must", "submitting a form with every field empty went through. Check the fields and say what is wrong")
                page.goto(addr, wait_until="load")
            elif before == after and not invalid:
                add("form-no-feedback", "must", "submitting a form with every field empty shows nothing: no message, no highlighted field")
            elif info["required"] == 0 and re.search(r"thank|success|confirmed|booked|received|submitted|sent", after.split("#")[1], re.I) and not re.search(r"thank|success|confirmed|booked|received|submitted|sent", before.split("#")[1], re.I):
                add("form-accepts-empty", "must", "an empty form is accepted and a success message appears. Require the fields that matter and check them")
            # bad values
            page.goto(addr, wait_until="load")
            page.wait_for_timeout(250)
            inv = page.evaluate(CONTROLS_JS)
            try:
                filled = page.evaluate("""([s, bad]) => { const f = document.querySelector(s); let n = 0;
                  for (const e of f.elements) { if (e.disabled || ["submit","button","hidden","checkbox","radio","file"].includes(e.type)) continue;
                    if (e.tagName === "SELECT") continue;
                    const v = bad[e.type] !== undefined ? bad[e.type] : (e.tagName === "TEXTAREA" || e.type === "text" ? "   " : "x");
                    if (e.type === "date" || e.type === "time") continue;
                    e.value = v; e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); n++; }
                  return n; }""", [sel, BAD_VALUE])
                if filled:
                    b4 = page.evaluate(STATE_JS)
                    sub = page.query_selector(f'{sel} button[type=submit], {sel} input[type=submit], {sel} button:not([type])')
                    sub.click(timeout=1500)
                    page.wait_for_timeout(300)
                    now = page.evaluate(STATE_JS)
                    if re.search(r"thank|success|confirmed|booked|received|submitted|sent|added", now.split("#")[1], re.I) and not re.search(
                            r"thank|success|confirmed|booked|received|submitted|sent|added", b4.split("#")[1], re.I) and not page.evaluate(
                            "s => document.querySelectorAll(s + ' :invalid').length", sel):
                        add("form-accepts-bad", "must", "a form accepted nonsense (blank spaces, \"not-an-email\", letters in a phone field, negative numbers) and said it worked. Validate each field and show what is wrong next to it")
            except Exception:  # noqa: BLE001
                pass
            page.goto(addr, wait_until="load")
            page.wait_for_timeout(250)

        # 4. keyboard: can it be reached without a mouse?
        if inv["loose"]:
            add("mouse-only", "must", "clickable things a keyboard cannot reach (not a button or link): " + ", ".join(f'"{x}"' for x in inv["loose"][:4]) + ". Use <button> or add tabindex and Enter/Space handling")
        seen = set()
        page.evaluate("document.activeElement && document.activeElement.blur()")
        for _ in range(min(60, max(8, n_btn + len(inv["anchors"]) + 8))):
            page.keyboard.press("Tab")
            seen.add(page.evaluate("(()=>{const e=document.activeElement;return e?e.tagName+(e.id||'')+(e.textContent||'').slice(0,12)+(e.name||''):''})()"))
        if n_btn + len(inv["anchors"]) >= 3 and len(seen) < 2:
            add("keyboard-trap", "must", "Tab does not move between the controls on the page")

        # 5. games: keys must change the canvas or the score
        if inv["canvas"]:
            page.goto(addr, wait_until="load")
            page.wait_for_timeout(400)
            for sel in ("button:has-text('Start')", "button:has-text('Play')", "#start", "#startBtn", "button"):
                try:
                    page.click(sel, timeout=600)
                    break
                except Exception:  # noqa: BLE001
                    pass
            page.mouse.click(view[0] // 2, view[1] // 2)
            a = page.evaluate(STATE_JS)
            for key in ("Enter", "Space", "ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "d", "s", "a", "w"):
                page.keyboard.press(key)
                page.wait_for_timeout(120)
            page.wait_for_timeout(700)
            b = page.evaluate(STATE_JS)
            if a == b:
                add("game-inert", "must", "pressing keys and waiting changed nothing on the canvas or the score. The game does not start or does not react")

        # 6. reload: is the data kept? (only for pages with fields or lists the user changes)
        page.goto(addr, wait_until="load")
        page.wait_for_timeout(300)
        inv = page.evaluate(CONTROLS_JS)
        if inv["fields"] and not inv["forms"]:
            ok = page.evaluate("""() => { const e = [...document.querySelectorAll("input[type=text],input:not([type]),textarea")].find(x => x.offsetParent); if (!e) return false;
               e.focus(); return true; }""")
            if ok:
                marker = "Smoke item 7Q"
                before = page.evaluate("document.body.innerText")
                page.keyboard.type(marker)
                page.keyboard.press("Enter")
                page.wait_for_timeout(250)
                if marker in page.evaluate("document.body.innerText"):
                    page.reload(wait_until="load")
                    page.wait_for_timeout(300)
                    if marker not in page.evaluate("document.body.innerText"):
                        add("no-persistence", "should", "an item typed in was gone after reloading the page. Save the data (localStorage for a small app, a file or database for a backend) and load it on start")
                    page.keyboard.press("Tab")

        if errs:
            for e in dict.fromkeys(errs):
                add("console-error", "must", e)
        ctx.close()
    finally:
        browser.close()
        stop()
    must = [o for o in out if o["sev"] == "must"]
    should = [o for o in out if o["sev"] == "should"]
    lines = []
    if must:
        lines.append("MUST FIX:")
        lines += [f"- {o['msg']}" for o in must]
    if should:
        lines.append("SHOULD FIX:")
        lines += [f"- {o['msg']}" for o in should]
    text = "\n".join(lines) if lines else "No problems found by the click-through."
    head = (f"Click-through of {shown}: {n_btn} buttons, {len(inv['anchors'])} links, {len(inv['forms'])} forms"
            + (f", {inv['canvas']} canvas" if inv["canvas"] else "") + ". ")
    return {"findings": out, "text": head + text, "time": time.time()}
