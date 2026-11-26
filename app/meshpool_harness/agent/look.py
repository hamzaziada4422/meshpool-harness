"""Look at what was made before saying it is done (release 0.0.18).

When a turn made or changed something with a visible result, meshpool renders the real result, measures what is wrong,
and gives the model one review message: the user's request, the screenshots (when the model takes images) and the
measured findings as text. The model compares the result with the request and fixes what it finds. Then meshpool looks
again. At most MAX_ROUNDS review messages per turn; the turn ends with a "Checked: ..." line.

What it looks at:
- A web page the turn wrote or changed (HTML, CSS, JS/TS, JSX/TSX, Vue, Svelte, a static site, a built page): the
  static file through file://, or the dev server the turn already started with bash_start. It never starts a server
  of its own. A framework app with no running dev server cannot be rendered; the result says so.
- A document, deck or sheet (through the kept LibreOffice session, view.render), a PDF, an image or chart, unless the
  turn already looked at it or an engine's own check ran on it after the last write.

Nothing is looked at for a plain text answer, a code change with no page, or a text file.

Cost: one headless Chromium per look, closed right after (never a resident one), no polling. MH_LOOK_REVIEW=0, or the
Settings switch "Look at web pages it makes", turns it off. It needs no approval: rendering a local file is read-only.
"""
from __future__ import annotations

import base64
import os
import re
import threading
import time
from pathlib import Path

MAX_ROUNDS = 2
DESKTOP = (1280, 800)
PHONE = (390, 844)
SHOT_MAX_H = 2400            # a screenshot shows the page down to this height: a long page is not one huge image
MAX_IMAGES = 4

WEB_EXT = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue",
           ".svelte", ".astro"}
APP_EXT = {".jsx", ".tsx", ".vue", ".svelte", ".astro"}      # needs a build or a dev server to be seen
STYLE_EXT = {".css", ".scss", ".sass", ".less"}
DOC_EXT = {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods", ".pdf"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
_NOT_UI = re.compile(r"(^|[\\/])(tests?|__tests__|spec|node_modules|\.meshpool|\.run)([\\/]|$)|"
                     r"(\.|_)(test|spec)\.[a-z]+$|^test_|(^|[\\/])[\w.-]*\.config\.[cm]?[jt]s$|"
                     r"(^|[\\/])(gulpfile|gruntfile|webpack|rollup|eslint|babel|jest|karma)[\w.-]*$", re.I)
_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{2,5})?(?:/[^\s'\"<>)\x1b]*)?", re.I)
_BUILD = re.compile(r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?build\b|\bvite\s+build\b|\bnext\s+build\b|\bastro\s+build\b|"
                    r"\bng\s+build\b|\bparcel\s+build\b|\beleventy\b|\bhugo\b|\bjekyll\s+build\b", re.I)
# a look after the last write already happened this turn: the engines' checks that draw the pages, and the viewing tools.
# check_document, check_workbook, make_document and make_workbook only test the file's structure or values and never
# draw a page, so a Word file or sheet they handled is still looked at once.
_LOOKED = re.compile(r"(^|__)(check_deck|view_output|read_document|measure_office_file|"
                     r"make_deck|make_report|build_deck|build_report|build_model)$")
_WROTE = re.compile(r"^(write_file|write|edit|edit_file|apply_patch|bash|shell|exec_command|run_python|python)$")


# ---------- the switch --------------------------------------------------------------------------------------------------
def enabled() -> bool:
    v = os.environ.get("MH_LOOK_REVIEW")
    if v is None or v == "":
        try:
            from meshpool_harness import config as C
            v = C.read().get("MH_LOOK_REVIEW")
        except Exception:  # noqa: BLE001
            v = None
    return str(v if v not in (None, "") else "1").strip().lower() not in ("0", "off", "false", "no")


# ---------- the instructions the model reads -----------------------------------------------------------------------------
# The "does it look finished" points draw on general design practice and on the unslop skill by Damir Sokolovsky
# (https://heydamir.design/unslop, CC BY 4.0): quiet chrome, one accent used with purpose, even spacing.
RUBRIC_WEB = (
    "Check it against the request, in this order:\n"
    "1. Does it do what was asked? Is every part the request names there?\n"
    "2. Do the main actions the request implies work: buttons, links, forms, navigation, and states such as empty, "
    "error and loading? Does each action give visible feedback?\n"
    "3. Can it be used with the keyboard alone, with the focus visible?\n"
    "4. Is the layout right at both widths: nothing scrolls sideways, overlaps or is cut off?\n"
    "5. Are there errors in the console, or files that failed to load?\n"
    "6. Is all text readable: contrast at least 4.5:1 (3:1 for large text)?\n"
    "7. Does it look finished: no placeholder text, no empty sections, even spacing, one accent colour used with "
    "purpose, nothing that competes with the content?")
RUBRIC_DOC = (
    "Check it against the request, in this order:\n"
    "1. Does it do what was asked? Is every part the request names there, with the right content and numbers?\n"
    "2. Is anything cut off, overlapping, off the page or slide, or unreadable?\n"
    "3. Does it look finished: no placeholder text, no empty pages or slides, consistent fonts and spacing?")
TAIL = ("List what is wrong or missing in a few short lines, then fix it in the files. If nothing is wrong, say so in "
        "one line. Do not start servers or open other programs for this: meshpool looks again after your fix. End "
        "with your answer to the user as before: what you made and where it is.")
AGAIN = ("[meshpool] meshpool looked again after your fix. These problems are still there. Fix them, or say in one "
         "line why they are right as they are.")


def _blind(agent) -> bool:
    """True when this chat's model cannot take images (the same knowledge the chat uses for image attachments)."""
    if os.environ.get("MH_VISION", "1") == "0":
        return True
    try:
        if agent._model_is_blind():
            return True
        from meshpool_harness import providers as PV
        from meshpool_harness.core import modelcaps as MC
        return bool(MC.no_vision(PV.key_base(), PV.split(agent.current_model())[1]))
    except Exception:  # noqa: BLE001
        return False


def request_of(agent, g: dict) -> str:
    from meshpool_harness.core.review import clean_request
    t = getattr(agent, "_asked_text", "") or g.get("req") or ""
    if isinstance(t, list):
        t = " ".join(str(p.get("text", "")) for p in t if isinstance(p, dict))
    return clean_request(str(t))[:4000]


# ---------- what to look at ----------------------------------------------------------------------------------------------
def _changed(agent, g: dict) -> list[Path]:
    from meshpool_harness.agent import grader as GR
    root = Path(agent.root)
    rels = GR.files_changed(root, g.get("t0", time.time()), getattr(agent.ws, "turn_files", ()), limit=200)
    return [p if p.is_absolute() else root / p for p in map(Path, rels)]


def _servers(agent, since: float) -> list[dict]:
    """Dev servers this chat started with bash_start that are still running, newest first, with the address each one
    printed. Read from the output already kept; nothing is started or polled."""
    out = []
    for bid, e in list((getattr(agent.ws, "__dict__", {}).get("_bg") or {}).items()):
        try:
            if e["p"].poll() is not None:
                continue
            with e["lock"]:
                text = "".join(e["text"])
        except Exception:  # noqa: BLE001
            continue
        m = _URL.findall(re.sub(r"\x1b\[[0-9;]*m", "", text))
        if m:
            url = m[-1].rstrip(".,;")
            url = re.sub(r"://0\.0\.0\.0", "://127.0.0.1", url)
            out.append({"id": bid, "url": url, "new": e.get("t0", 0) >= since - 1, "t0": e.get("t0", 0)})
    return sorted(out, key=lambda s: -s["t0"])


def _page_for(asset: Path, root: Path) -> Path | None:
    """The HTML page that uses a changed stylesheet or script: one beside it or above it (up to the project folder)
    that names it, else an index.html there."""
    d = asset.parent
    seen = 0
    while True:
        pages = sorted(d.glob("*.htm*"))
        for p in pages:
            try:
                if asset.name in p.read_text(encoding="utf-8", errors="replace")[:200000]:
                    return p
            except OSError:
                pass
        if (d / "index.html").is_file():
            return d / "index.html"
        seen += 1
        if d == root or root not in d.parents or seen > 6:
            return None
        d = d.parent


def web_target(agent, g: dict, changed: list[Path] | None = None) -> dict | None:
    """{"url", "label", "files"} to render; {"none": why, "files"} when the turn changed a page that cannot be rendered;
    None when the turn made no web UI."""
    root = Path(agent.root)
    changed = _changed(agent, g) if changed is None else changed
    web = [p for p in changed if p.suffix.lower() in WEB_EXT and not _NOT_UI.search(str(p.relative_to(root))
                                                                                   if root in p.parents else p.name)]
    t0 = g.get("t0", 0)
    servers = _servers(agent, t0)
    started = [s for s in servers if s["new"]]
    if started or (servers and web):
        s = (started or servers)[0]
        return {"url": s["url"], "label": f"the page at {s['url']} (the dev server this chat started)",
                "files": web, "served": True}
    htmls = [p for p in web if p.suffix.lower() in (".html", ".htm")]
    if not htmls:
        cmds = agent.ws.commands[g.get("cmd0", 0):]
        if any(c.get("code") == 0 and _BUILD.search(str(c.get("cmd", ""))) for c in cmds):
            for d in ("dist", "build", "out", "public", "_site"):
                p = root / d / "index.html"
                try:
                    if p.is_file() and p.stat().st_mtime >= t0 - 1:
                        htmls = [p]
                        break
                except OSError:
                    pass
    if not htmls:
        for a in web:
            if a.suffix.lower() in APP_EXT:
                continue
            p = _page_for(a, root)
            if p is not None and (a.suffix.lower() in STYLE_EXT or _static_page(p)):
                htmls = [p]
                break
    if htmls:
        main = next((p for p in htmls if p.name.lower() == "index.html"), None) or max(
            htmls, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        if _static_page(main):
            rel = os.path.relpath(main, root) if root in main.parents else str(main)
            return {"url": main.resolve().as_uri(), "label": rel, "files": web, "page": str(main)}
    ui = [p for p in web if p.suffix.lower() in APP_EXT | STYLE_EXT | {".html", ".htm"}]
    if ui:
        return {"none": "it is an app that needs its dev server or a build, and none was running this turn",
                "files": web}
    return None


def _static_page(p: Path) -> bool:
    """A page that shows something when opened from disk: not a bundler's shell (a module script under src/ that
    only a dev server can serve)."""
    try:
        t = p.read_text(encoding="utf-8", errors="replace")[:200000]
    except OSError:
        return False
    return not re.search(r"<script[^>]+type=[\"']module[\"'][^>]+src=[\"']/?src/[^\"']+\.(?:[jt]sx|ts|vue|svelte)",
                         t, re.I)


def doc_target(agent, g: dict, changed: list[Path] | None = None) -> dict | None:
    """A document, deck, sheet, PDF, image or chart the turn made, unless the turn already looked at it (view_output,
    read_document) or an engine made and checked it after the last write."""
    changed = _changed(agent, g) if changed is None else changed
    files = [p for p in changed if p.suffix.lower() in DOC_EXT | IMG_EXT and p.is_file()
             and not p.name.startswith("~$") and not _NOT_UI.search(p.name)]
    if not files:
        return None
    steps = list(getattr(agent, "trace", []))[g.get("trace0", 0):]
    last_look = last_write = -1
    for i, t in enumerate(steps):
        n = str(t.get("tool", "")).replace("-", "_")
        if _LOOKED.search(n) and t.get("ok", True):
            last_look = i
        elif _WROTE.match(n):
            last_write = i
    if last_look > last_write:
        return None                          # the engine's own check or the agent's own look came after the last write
    main = max(files, key=lambda p: (p.suffix.lower() in DOC_EXT, p.stat().st_mtime))
    return {"file": str(main), "label": os.path.relpath(main, agent.root) if Path(agent.root) in main.parents
            else main.name, "files": files}


# ---------- rendering a web page -----------------------------------------------------------------------------------------
EXTRA_JS = r"""
() => {
  const out = [], vw = innerWidth;
  const sel = el => { if (!el || !el.tagName) return ""; if (el.id) return "#" + el.id; let s = el.tagName.toLowerCase();
    const c = (el.getAttribute("class") || "").trim().split(/\s+/)[0]; if (c) s += "." + c;
    const t = (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 28); return s + (t ? ` "${t}"` : ""); };
  const vis = e => { const s = getComputedStyle(e), b = e.getBoundingClientRect(); return s.visibility !== "hidden" && s.display !== "none" && b.width > 0 && b.height > 0 && +s.opacity > 0.05; };
  const own = e => [...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim().length > 1);
  const all = [...document.querySelectorAll("body *")].filter(vis);
  const body = document.body ? document.body.innerText || "" : "";
  const ph = body.match(/\[(?:x|xx+|\.\.\.|tbd|todo|placeholder|image|photo|logo|link)\]|\{\{\s*\w+\s*\}\}|\bundefined\b|\bNaN\b|\[object Object\]/i);
  if (ph) out.push({rule: "placeholder", msg: `placeholder or unfilled text on the page: "${ph[0]}"`, sel: ""});
  // text drawn on top of other text, or on a button or field
  const texts = all.filter(e => own(e) || ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "IMG"].includes(e.tagName)).slice(0, 350);
  // one box per line of an inline element: the bounding box of text that wraps covers lines it is not on
  const rects = texts.map(e => [...e.getClientRects()].filter(r => r.width > 1 && r.height > 1));
  const hit = (r, s) => { const w = Math.min(r.right, s.right) - Math.max(r.left, s.left), h = Math.min(r.bottom, s.bottom) - Math.max(r.top, s.top);
    return w > 4 && h > 4 && w * h > 0.25 * Math.min(r.width * r.height, s.width * s.height); };
  let n = 0;
  for (let i = 0; i < texts.length && n < 5; i++) for (let j = i + 1; j < texts.length && n < 5; j++) {
    const a = texts[i], b = texts[j];
    if (a.contains(b) || b.contains(a)) continue;
    if (a.tagName === "IMG" && b.tagName === "IMG") continue;
    if ((a.tagName === "IMG") !== (b.tagName === "IMG")) continue;      // text on a picture is often on purpose
    if (rects[i].some(r => rects[j].some(s => hit(r, s)))) {
      n++; out.push({rule: "overlap", msg: `overlaps ${sel(b)}`, sel: sel(a)});
    }
  }
  // text cut off at the bottom or past the left edge
  n = 0;
  for (const e of all) {
    if (n >= 4) break;
    const s = getComputedStyle(e);
    if (own(e) && /(hidden|clip)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4 && s.webkitLineClamp === "none" && e.clientHeight > 0) { n++; out.push({rule: "text-cut-off", msg: "its text is cut off at the bottom", sel: sel(e)}); }
    else if (own(e) && e.getBoundingClientRect().left < -2 && s.position !== "fixed") { n++; out.push({rule: "past-the-edge", msg: "starts past the left edge of the screen", sel: sel(e)}); }
  }
  // lists, tables and grids that are empty where content is expected
  n = 0;
  for (const e of document.querySelectorAll("ul, ol, tbody, table, [class*=list], [class*=grid], [class*=cards], [class*=items], [class*=results], [class*=gallery], [class*=products], [id*=list], [id*=grid], [id*=results]")) {
    if (n >= 3 || !document.body.contains(e)) break;
    const s = getComputedStyle(e); if (s.display === "none") continue;
    if (["UL", "OL"].includes(e.tagName) ? e.querySelectorAll("li").length === 0
        : ["TBODY", "TABLE"].includes(e.tagName) ? e.querySelectorAll("td").length === 0
        : (e.children.length === 0 && !(e.textContent || "").trim() && e.getBoundingClientRect().width > 50)) {
      n++; out.push({rule: "empty", msg: "is empty where content is expected", sel: sel(e)});
    }
  }
  return out;
}
"""
KEEP = {"left-over-text", "placeholder-link", "chart-text-cut-off", "sideways-scroll", "past-the-edge", "text-cut-off",
        "contrast", "broken-image", "viewport", "empty-page", "placeholder", "overlap", "empty", "console", "load"}


def _interact(target: str, request: str, out_dir: str, browser) -> dict:
    """The interaction check (review_interact.exercise), when that module is installed: it clicks and types through
    the page's main actions in the same browser. Without it, no interaction checks."""
    import importlib
    try:
        RI = importlib.import_module("meshpool_harness.review_interact")
    except ImportError:
        return {"steps": [], "findings": [], "shots": [], "console_errors": [], "ms": 0}
    try:
        r = RI.exercise(target, request, out_dir, browser=browser, widths=(DESKTOP[0], PHONE[0]))
        return r if isinstance(r, dict) else {}
    except Exception as e:  # noqa: BLE001 - a broken interaction check never breaks the review
        return {"steps": [], "findings": [], "shots": [], "console_errors": [], "ms": 0,
                "error": f"{type(e).__name__}: {str(e)[:120]}"}


def _launch(pw):
    """The headless Chromium meshpool already uses (ui_audit, previews): Playwright's own, else the newest one a
    Playwright install left on this computer. Never the user's own browser."""
    try:
        return pw.chromium.launch(headless=True)
    except Exception:  # noqa: BLE001
        from meshpool_harness import browser_use as BU
        path = os.environ.get("MH_TEST_CHROMIUM") or BU.find_playwright_chromium()
        if not path:
            raise
        return pw.chromium.launch(headless=True, executable_path=path)


def _web_once(url: str, out_dir: Path, tag: str, request: str, launch) -> dict:
    from playwright.sync_api import sync_playwright
    from meshpool_harness import ui_audit as UA
    out_dir.mkdir(parents=True, exist_ok=True)
    merged: dict = {}
    shots: list = []
    res = {"findings": [], "shots": [], "steps": [], "views": []}
    with sync_playwright() as pw:
        browser = (launch or _launch)(pw)
        try:
            for view, (w, h) in (("desktop", DESKTOP), ("phone", PHONE)):
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1,
                                          has_touch=view == "phone", is_mobile=view == "phone")
                try:
                    page = ctx.new_page()
                    probs: list = []
                    page.on("pageerror", lambda e, p=probs: p.append(f"script error: {str(e)[:160]}"))
                    page.on("console", lambda m, p=probs: m.type == "error" and p.append(f"console error: {m.text[:160]}"))
                    page.on("requestfailed", lambda r, p=probs: "favicon" not in r.url and p.append(
                        f"failed to load: {r.url[:120]}"))
                    page.on("response", lambda r, p=probs: r.status >= 400 and "favicon" not in r.url and p.append(
                        f"HTTP {r.status}: {r.url[:120]}"))
                    try:
                        page.goto(url, wait_until="load", timeout=15000)
                        page.wait_for_timeout(300)
                    except Exception as e:  # noqa: BLE001
                        merged.setdefault(("load", "", "x"), {"rule": "load", "sel": "", "views": [view],
                                                              "msg": f"the page did not open: {str(e)[:140]}"})
                        continue
                    found = []
                    try:
                        found += [f for f in page.evaluate(UA.AUDIT_JS)["findings"] if f["sev"] == "must"]
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        found += page.evaluate(EXTRA_JS)
                    except Exception:  # noqa: BLE001
                        pass
                    for p_ in dict.fromkeys(probs):
                        merged.setdefault(("console", "", p_), {"rule": "console", "msg": p_, "sel": "",
                                                                "views": []})["views"].append(view)
                    for f in found:
                        if f.get("rule") in KEEP:
                            merged.setdefault((f["rule"], f.get("sel", ""), f["msg"]), dict(f, views=[]))["views"].append(view)
                    try:
                        # the content's own height: a phone page widened by a wide element reports a tall empty canvas
                        full = page.evaluate("() => Math.ceil(Math.max(document.body.getBoundingClientRect().bottom, "
                                             "...[...document.body.querySelectorAll('*')].slice(0, 3000).map(e => "
                                             "e.getBoundingClientRect().bottom)) + scrollY)")
                        f = out_dir / f"{tag}-{view}.jpg"
                        page.screenshot(path=str(f), full_page=True, type="jpeg", quality=70,
                                        clip={"x": 0, "y": 0, "width": w, "height": max(h, min(int(full), SHOT_MAX_H))})
                        shots.append((f"{view} {w}x{h}", f))
                    except Exception:  # noqa: BLE001
                        pass
                    res["views"].append(view)
                finally:
                    ctx.close()
            target = url
            if url.startswith("file://"):
                from urllib.parse import unquote, urlparse
                target = unquote(urlparse(url).path)
            it = _interact(target, request, str(out_dir), browser)
        finally:
            browser.close()
    for e in it.get("console_errors") or []:
        merged.setdefault(("console", "", str(e)), {"rule": "console", "msg": str(e)[:200], "sel": "", "views": ["use"]})
    res["findings"] = sorted(merged.values(), key=lambda f: (f["rule"] != "console", f["rule"]))
    res["interact"] = [str(x)[:240] for x in it.get("findings") or []]
    res["steps"] = it.get("steps") or []
    bad = [s.get("shot") for s in res["steps"] if s.get("shot") and not s.get("ok", True)]
    extra = list(dict.fromkeys(bad + [s for s in (it.get("shots") or []) if s]))[:2]
    res["shots"] = shots + [("using it", Path(s)) for s in extra if Path(s).is_file()]
    return res


def render_web(url: str, out_dir, tag: str = "r1", request: str = "", launch=None, timeout: float = 90) -> dict:
    """Render once at desktop and phone width in a fresh headless Chromium, closed before returning. Runs on a thread
    of its own: Playwright's sync API cannot run inside a thread that has an event loop."""
    box: dict = {}

    def run():
        try:
            box["r"] = _web_once(url, Path(out_dir), tag, request, launch)
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=run, daemon=True, name="meshpool-look")
    t = time.time()
    th.start()
    th.join(timeout)
    if "r" in box:
        box["r"]["secs"] = round(time.time() - t, 2)
        return box["r"]
    why = box.get("e") or "it took too long"
    return {"error": f"could not open a browser to look at it: {str(why)[:200]}", "findings": [], "shots": []}


def render_doc(path: str, out_dir, tag: str = "r1") -> dict:
    """Pages of a document, deck, sheet, PDF or image as pictures (view.render, through the kept LibreOffice for
    Office files), with what can be measured from their text."""
    from meshpool_harness import view as V
    t = time.time()
    try:
        out = Path(out_dir) / tag
        if V.kind(path) == "web":                              # an SVG chart: drawn by the same headless browser
            return render_web(Path(path).resolve().as_uri(), out_dir, tag)
        r = V.render(path, pages="1-2", out_dir=out)
    except Exception as e:  # noqa: BLE001
        return {"error": f"could not draw {Path(path).name}: {str(e)[:200]}", "findings": [], "shots": []}
    shots = list(r.get("shots") or [])
    findings, texts = [], []
    for label, f in shots:
        txt = V.texts.get(f, "")
        texts.append(f"[{label}]\n{txt[:1500]}" if txt else f"[{label}] (no text)")
        m = re.search(r"lorem ipsum|\bTODO\b|\bTBD\b|\[(?:x|insert[^\]]*|your [^\]]*)\]|click to add (?:title|text)|"
                      r"#REF!|#VALUE!|#DIV/0!|#NAME\?", txt, re.I)
        if m:
            findings.append({"rule": "placeholder", "msg": f"{label}: placeholder or error text \"{m.group(0)}\"",
                             "sel": "", "views": []})
        if V.kind(path) in ("office", "pdf") and not txt.strip():
            findings.append({"rule": "empty", "msg": f"{label} has no text", "sel": "", "views": []})
    return {"findings": findings, "shots": shots[:2], "notes": r.get("notes") or [], "text": "\n".join(texts)[:3000],
            "secs": round(time.time() - t, 2)}


# ---------- the review message ------------------------------------------------------------------------------------------
def finding_lines(res: dict) -> list[str]:
    out = []
    for f in res.get("findings") or []:
        views = f.get("views") or []
        where = "" if not views or set(views) >= {"desktop", "phone"} else f" (at {', '.join(views)} width)"
        out.append(f"[{f['rule']}] {f['sel'] + ': ' if f.get('sel') else ''}{f['msg']}{where}")
    out += [f"[use] {x}" for x in res.get("interact") or []]
    return out


def _image(path) -> dict | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    mime = "image/jpeg" if str(path).lower().endswith((".jpg", ".jpeg")) else "image/png"
    return {"mime": mime, "data": base64.b64encode(data).decode()}


def message(kind: str, target: dict, res: dict, request: str, blind: bool) -> dict:
    """The review message: the request, what was measured, the screenshots when the model takes images."""
    from meshpool_harness.agent.core import _image_part
    if kind == "web":
        what = (f"{target['label']} in a headless browser at desktop width ({DESKTOP[0]}x{DESKTOP[1]}) and phone width "
                f"({PHONE[0]}x{PHONE[1]})")
        rubric = RUBRIC_WEB
    else:
        what = f"{target['label']} as pictures of its first pages"
        rubric = RUBRIC_DOC
    lines = finding_lines(res)
    steps = [s for s in res.get("steps") or [] if isinstance(s, dict)]
    text = [f"[meshpool] Before you call this done, look at what you made. meshpool opened {what}.",
            "", "The user's request:", request or "(not available)", ""]
    files = [os.path.basename(str(f)) for f in target.get("files") or []][:12]
    if files:
        text += ["Files made or changed this turn: " + ", ".join(files), ""]
    text.append("What meshpool measured:" if lines else "What meshpool measured: no problems found by the checks.")
    text += ["- " + ln for ln in lines[:30]]
    if steps:
        text += ["", "What happened when meshpool used it:"]
        text += [f"- {'ok' if s.get('ok', True) else 'FAILED'}: {str(s.get('action', ''))[:120]}"
                 + (f" ({str(s.get('note'))[:160]})" if s.get("note") else "") for s in steps[:12]]
    if kind != "web" and res.get("text"):
        text += ["", "The text on those pages:", res["text"]]
    shots = res.get("shots") or []
    if blind or not shots:
        text += ["", "This model cannot take images, so you get the measurements only." if blind and shots
                 else "No picture could be taken."]
    else:
        text += ["", "Pictures attached: " + ", ".join(lbl for lbl, _ in shots[:MAX_IMAGES]) + "."]
    text += ["", rubric, "", TAIL]
    body = "\n".join(text)
    if blind or not shots:
        return {"role": "user", "content": body}
    parts = [{"type": "text", "text": body}]
    for _, f in shots[:MAX_IMAGES]:
        im = _image(f)
        if im:
            parts.append(_image_part(im))
    return {"role": "user", "content": parts}


def _short(f: dict) -> str:
    return {"sideways-scroll": "the page scrolled sideways", "past-the-edge": "content past the screen edge",
            "console": "a console error", "load": "the page did not open", "contrast": "low contrast text",
            "overlap": "overlapping elements", "text-cut-off": "cut-off text", "broken-image": "a broken image",
            "placeholder": "placeholder text", "left-over-text": "placeholder text", "empty": "an empty section",
            "empty-page": "an empty page", "viewport": "no phone viewport tag",
            "placeholder-link": "a placeholder link"}.get(f["rule"], f["rule"])


def summary_line(kind: str, st: dict) -> str:
    """The closing line, e.g. "Checked: looked at the page at desktop and phone width; fixed the page scrolled
    sideways and a console error"."""
    first, last = st.get("first") or {}, st.get("last") or {}
    if last.get("error"):
        return f"Checked: {last['error']}."
    where = "looked at the page at desktop and phone width" if kind == "web" else \
        f"looked at {st.get('label') or 'the file'}"
    key = lambda f: (f["rule"], f.get("sel", ""), f["msg"])  # noqa: E731
    now = {key(f) for f in last.get("findings") or []}
    fixed = list(dict.fromkeys(_short(f) for f in first.get("findings") or [] if key(f) not in now))
    left = list(dict.fromkeys(_short(f) for f in last.get("findings") or []))
    bits = []
    if fixed:
        bits.append("fixed " + _join(fixed))
    elif st.get("rounds") and st.get("changed"):
        bits.append("updated it after the review")
    if left:
        bits.append("still open: " + _join(left))
    if not bits:
        bits.append("no problems found")
    return f"Checked: {where}; " + "; ".join(bits) + "."


def _join(xs: list) -> str:
    xs = xs[:4]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


# ---------- the step in the agent's loop ---------------------------------------------------------------------------------
def _sig(files) -> tuple:
    out = []
    for f in files or ():
        try:
            st = os.stat(f)
            out.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(f), 0, 0))
    return tuple(sorted(out))


def out_dir(agent) -> Path:
    from meshpool_harness import config as C
    return C.home() / "look" / re.sub(r"[^\w.-]", "_", str(getattr(agent, "id", "chat")))[:80]


def gate(agent, g: dict, final: str, *, render_web_fn=None, render_doc_fn=None) -> tuple[bool, str]:
    """At the final answer: look at what the turn made. Returns (True, final) when a review message was added and the
    model should go on, else (False, final with the "Checked:" line). Never raises."""
    try:
        if (not enabled() or getattr(agent, "plan", False) or getattr(agent, "_is_grader", False)
                or getattr(agent, "_delegate_depth", 0) or not (final or "").strip()):
            return False, final
        st = g.setdefault("look", {"rounds": 0})
        if st.get("done"):
            return False, final
        if "kind" not in st:
            changed = _changed(agent, g)
            t = web_target(agent, g, changed)
            st["kind"], st["target"] = ("web", t) if t else ("doc", doc_target(agent, g, changed))
            if st["target"] is None:
                st["done"] = True
                return False, final
        kind, target = st["kind"], st["target"]
        if target.get("none"):
            st["done"] = True
            line = f"Checked: could not look at the page: {target['none']}."
            return False, _close(agent, final, line)
        watch = [target["page"]] if target.get("page") else []
        watch += [str(f) for f in target.get("files") or []] + ([target["file"]] if target.get("file") else [])
        sig = _sig(watch)
        if st["rounds"] and sig == st.get("sig"):        # nothing changed since the last look: the same result stands
            st["done"] = True
            return False, _close(agent, final, summary_line(kind, st))
        if st["rounds"]:
            st["changed"] = True
        st["sig"] = sig
        tag = f"t{getattr(agent, '_turn_no', 0)}-r{st['rounds'] + 1}"
        agent.emit({"type": "status", "look": True, "text": "Looking at the result" + (" again" if st["rounds"] else "")
                    + " before finishing..."})
        req = request_of(agent, g)
        if kind == "web":
            res = (render_web_fn or render_web)(target["url"], out_dir(agent), tag, req)
        else:
            st["label"] = target["label"]
            res = (render_doc_fn or render_doc)(target["file"], out_dir(agent), tag)
        agent._check_stop()
        st.setdefault("first", res)
        st["last"] = res
        st.setdefault("shots", []).append([str(f) for _, f in res.get("shots") or []])
        try:
            agent._trace_note("look", f"{kind} round {st['rounds'] + 1}: {len(res.get('findings') or [])} findings")
        except Exception:  # noqa: BLE001
            pass
        if res.get("error") and not st["rounds"]:
            st["done"] = True
            return False, _close(agent, final, f"Checked: {res['error']}.")
        found = finding_lines(res)
        if st["rounds"] < MAX_ROUNDS and (st["rounds"] == 0 or found) and not res.get("error"):
            msg = message(kind, target, res, req, _blind(agent))
            if st["rounds"]:                               # a later round: lead with what is still wrong
                head = AGAIN + "\n\n"
                if isinstance(msg["content"], str):
                    msg["content"] = head + msg["content"]
                else:
                    msg["content"][0]["text"] = head + msg["content"][0]["text"]
            st["rounds"] += 1
            if isinstance(msg["content"], list):            # only this look's pictures stay in the conversation
                from meshpool_harness.agent.core import _drop_images
                _drop_images(agent.messages, keep=0)
            agent.messages.append(msg)
            return True, final
        st["done"] = True
        return False, _close(agent, final, summary_line(kind, st))
    except Exception as e:  # noqa: BLE001
        try:
            from meshpool_harness.agent.core import Interrupted
            from meshpool_harness.core import usage
            if isinstance(e, (Interrupted, KeyboardInterrupt, usage.BudgetExceeded)):
                raise
        except ImportError:
            pass
        return False, final


def _close(agent, final: str, line: str) -> str:
    """The "Checked:" line goes under the final reply (and into the chat history and the turn's receipt)."""
    agent.last_look = {"turn": getattr(agent, "_turn_no", 0), "line": line}
    agent.emit({"type": "assistant", "text": line, "streamed": False, "has_tools": False, "append": True})   # joins the reply
    if agent.messages and agent.messages[-1].get("role") == "assistant" and isinstance(
            agent.messages[-1].get("content"), str):
        agent.messages[-1]["content"] += "\n\n" + line
    return (final.rstrip() + "\n\n" + line) if final else line
