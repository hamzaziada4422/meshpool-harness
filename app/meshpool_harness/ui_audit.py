"""UI audit: open a page the way a customer would (a wide screen and a phone, light and dark), measure what is wrong
and say it in plain words. This is how Meshpool checks a user interface it built, because a model that cannot see
pictures can still read measurements.

What it finds: the page scrolling sideways, things past the screen edge, text with too little contrast, controls too
small to hit or with no name, broken or unlabelled images, a missing viewport tag or language, headings out of order,
text cut off, errors in the console and files that failed to load, and an empty page. It also prints a short outline of
the page (where the header, main areas and footer sit) so the layout can be judged without a screenshot.

audit(pw, url) takes a started Playwright (the browser tool owns one) and returns {"findings": [...], "outline": [...],
"shots": [...], "text": "..."}; "text" is what the agent reads. It passes when the text says "No problems found".
"""
from __future__ import annotations

import time
from pathlib import Path

VIEWS = {"desktop": (1440, 900), "tablet": (820, 1180), "phone": (390, 844)}

AUDIT_JS = r"""
() => {
  const out = [], vw = innerWidth;
  const sel = el => {
    if (!el || !el.tagName) return "";
    if (el.id) return "#" + el.id;
    let s = el.tagName.toLowerCase();
    const c = (el.getAttribute("class") || "").trim().split(/\s+/)[0];
    if (c) s += "." + c;
    const t = (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 28);
    return s + (t ? ` "${t}"` : "");
  };
  const add = (rule, sev, msg, el) => out.push({rule, sev, msg, sel: sel(el)});
  const vis = e => { const s = getComputedStyle(e), b = e.getBoundingClientRect(); return s.visibility !== "hidden" && s.display !== "none" && b.width > 0 && b.height > 0 && +s.opacity > 0.05; };
  const clipped = e => { for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const s = getComputedStyle(a); if (/(hidden|auto|scroll|clip)/.test(s.overflowX) && a.getBoundingClientRect().right <= vw + 1) return true; } return false; };
  const all = [...document.querySelectorAll("body *")].filter(vis);

  // notes to the owner or filler left on the page
  const body = document.body.innerText || "";
  const left = body.match(/lorem ipsum|PHONE_NUMBER_?\d*|EMAIL_?\w*_\d+|\bTODO\b|\bTBD\b|your (name|company|email|text) here|I (do not|don't) know (these|this)|placeholder|\bXXX+\b|\[(?:insert|your)[^\]]*\]/i);
  if (left) add("left-over-text", "must", `the page still holds filler or a note to its owner: "${left[0]}". Visitors must never see it. Remove it, or leave that section out and tell the owner in your reply`, document.body);

  const badLink = [...document.querySelectorAll("a[href]")].find(a => /PHONE_NUMBER|EMAIL_?\w*_\d|YOUR_|REPLACE|example\.(com|org)|lorem|TODO|\bXXX/i.test(decodeURIComponent(a.getAttribute("href"))) || /^(tel:|https:\/\/wa\.me\/)\D*$/i.test(a.getAttribute("href")));
  if (badLink) add("placeholder-link", "must", `a link points at a placeholder: ${badLink.getAttribute("href").slice(0, 70)}. Use the real value, or leave that link out and ask for it in your reply`, badLink);

  // layout
  const over = document.scrollingElement.scrollWidth - vw;
  if (over > 1) add("sideways-scroll", "must", `the page scrolls sideways by ${over}px`, document.body);
  let n = 0;
  for (const e of all) { const r = e.getBoundingClientRect(); if (r.right > vw + 2 && !clipped(e) && n++ < 5) add("past-the-edge", "must", `reaches ${Math.round(r.right)}px on a ${vw}px screen`, e); }
  n = 0;
  for (const e of all) { const s = getComputedStyle(e); if (/(hidden|clip)/.test(s.overflowX) && e.scrollWidth > e.clientWidth + 3 && s.textOverflow !== "ellipsis" && e.children.length === 0 && (e.textContent || "").trim() && n++ < 4) add("text-cut-off", "should", "its text is cut off with no ellipsis", e); }

  // contrast
  const parse = c => { const m = String(c).match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const over2 = (f, b) => ({r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1});
  const lum = c => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(c.r) + .7152 * f(c.g) + .0722 * f(c.b); };
  const bgOf = el => { let layers = []; for (let a = el; a; a = a.parentElement) { const s = getComputedStyle(a); if (s.backgroundImage !== "none") return null; const c = parse(s.backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; } } let base = {r: 255, g: 255, b: 255, a: 1}; for (const c of layers.reverse()) base = over2(c, base); return base; };
  const bad = []; let seen = 0;
  for (const e of all) {
    if (seen > 500) break;
    const own = [...e.childNodes].some(x => x.nodeType === 3 && x.textContent.trim().length > 1);
    if (!own) continue; seen++;
    const s = getComputedStyle(e), fg = parse(s.color), bg = bgOf(e); if (!fg || !bg) continue;
    const f = over2({...fg, a: fg.a * (+s.opacity || 1)}, bg), l1 = lum(f), l2 = lum(bg);
    const ratio = (Math.max(l1, l2) + .05) / (Math.min(l1, l2) + .05), px = parseFloat(s.fontSize), big = px >= 24 || (px >= 18.66 && +s.fontWeight >= 700);
    if (ratio < (big ? 3 : 4.5)) bad.push({e, ratio, need: big ? 3 : 4.5});
  }
  bad.sort((a, b) => a.ratio - b.ratio);
  for (const b of bad.slice(0, 6)) add("contrast", "must", `contrast ${b.ratio.toFixed(1)}:1, needs ${b.need}:1`, b.e);
  if (bad.length > 6) add("contrast", "must", `${bad.length - 6} more text elements have too little contrast`, document.body);

  // controls
  const phone = vw <= 480;
  n = 0;
  for (const e of document.querySelectorAll("button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=tab], [onclick]")) {
    if (!vis(e) || e.disabled) continue;
    if (["checkbox", "radio"].includes(e.type) && e.closest("label")) continue;
    if (e.tagName === "A" && getComputedStyle(e).display === "inline") continue;       // a link inside a sentence
    const r = e.getBoundingClientRect();
    if ((r.width < 24 || r.height < 24) && n++ < 6) add("tap-target", "must", `${Math.round(r.width)}x${Math.round(r.height)}px: too small to hit (24px at least)`, e);
    else if (phone && (r.height < 40 || r.width < 40) && n++ < 6 && e.tagName !== "A") add("tap-target-phone", "should", `${Math.round(r.width)}x${Math.round(r.height)}px on a phone (about 44px is comfortable)`, e);
  }
  n = 0;
  for (const e of document.querySelectorAll("button, [role=button], a[href], input:not([type=hidden]), select, textarea")) {
    if (!vis(e)) continue;
    const named = (e.textContent || "").trim() || e.getAttribute("aria-label") || e.getAttribute("title") || e.getAttribute("aria-labelledby") || e.getAttribute("placeholder") || (e.labels && e.labels.length) || e.closest("label") || e.querySelector("img[alt]:not([alt=''])") || e.querySelector("svg title");
    if (!named && n++ < 5) add("no-name", "must", "a control with no text or label (add aria-label)", e);
  }
  if (phone) { n = 0; for (const e of document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea")) if (vis(e) && parseFloat(getComputedStyle(e).fontSize) < 16 && n++ < 3) add("input-zoom", "should", "text under 16px: phones zoom the page when it is focused", e); }

  // images
  n = 0;
  for (const e of document.querySelectorAll("img")) {
    if (!vis(e)) continue;
    if (e.complete && e.naturalWidth === 0 && n++ < 5) add("broken-image", "must", "the image did not load", e);
    else if (!e.hasAttribute("alt") && n++ < 5) add("image-alt", "should", "an image with no alt text", e);
  }

  // document
  if (!document.querySelector('meta[name="viewport"]')) add("viewport", "must", "no <meta name=\"viewport\">: phones show a tiny desktop page", document.documentElement);
  if (!document.documentElement.getAttribute("lang")) add("lang", "should", "<html> has no lang attribute", document.documentElement);
  if (!document.title.trim()) add("title", "should", "the page has no <title>", document.documentElement);
  const hs = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].filter(vis);
  const h1 = hs.filter(h => h.tagName === "H1").length;
  if (hs.length && h1 !== 1) add("headings", "should", `${h1} <h1> headings (one is right)`, hs[0]);
  let last = 0; for (const h of hs) { const l = +h.tagName[1]; if (last && l > last + 1) { add("headings", "should", `jumps from h${last} to h${l}`, h); break; } last = l; }
  const bodyPx = parseFloat(getComputedStyle(document.body).fontSize);
  if (bodyPx < 14) add("body-text", "should", `body text is ${bodyPx}px (16px reads best)`, document.body);
  if ((document.body.innerText || "").trim().length < 20 && !document.querySelector("canvas,svg,video")) add("empty-page", "must", "the page shows almost no text", document.body);

  // outline: where the big parts sit
  const outline = [...document.querySelectorAll("header, nav, main, section, article, aside, footer, form")].filter(vis).slice(0, 14).map(e => {
    const r = e.getBoundingClientRect(); return `${e.tagName.toLowerCase()}${e.id ? "#" + e.id : ""} at ${Math.round(r.left)},${Math.round(r.top + scrollY)} size ${Math.round(r.width)}x${Math.round(r.height)} "${(e.innerText || "").trim().replace(/\s+/g, " ").slice(0, 36)}"`; });
  return {findings: out, outline, height: document.scrollingElement.scrollHeight};
}
"""


def launcher(pw):
    """Opens a headless Chromium: Playwright's own, else any Chromium already on this computer."""
    def launch():
        try:
            return pw.chromium.launch(headless=True)
        except Exception:  # noqa: BLE001
            from meshpool_harness import browser_use as BU
            path = BU.find_playwright_chromium()
            if not path:
                raise
            return pw.chromium.launch(headless=True, executable_path=path)
    return launch


def _norm(path_or_url: str) -> str:
    u = (path_or_url or "").strip()
    if u.startswith(("http://", "https://", "file://", "data:")):
        return u
    p = Path(u).expanduser()
    if not p.is_absolute():
        base = Path(__import__("os").environ.get("MESHPOOL_PROJECT") or Path.cwd())
        p = base / p
    return p.resolve().as_uri()


def _serve(url: str):
    """A page on disk is opened through a throwaway local server rooted at its folder, so root-relative files
    (/style.css) and fetch() calls work as they will on a real site. Returns (address, stop)."""
    if not url.startswith("file://"):
        return url, lambda: None
    import functools
    import http.server
    import threading
    from urllib.parse import unquote, urlparse
    path = Path(unquote(urlparse(url).path))
    if path.drive if hasattr(path, "drive") else False:
        pass
    root = path.parent if path.is_file() else path

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D102
            pass

        def do_GET(self):  # noqa: N802, D102  a page with no icon is not an error
            if self.path.startswith("/favicon.ico") and not (root / "favicon.ico").exists():
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    name = path.name if path.is_file() else ""
    return f"http://127.0.0.1:{srv.server_address[1]}/{name}", srv.shutdown


def audit(pw, url: str, views=("desktop", "tablet", "phone"), schemes=("light", "dark"), shots_dir: Path | None = None,
          launch=None) -> dict:
    """Run the audit. `launch` (optional) opens the browser (so the caller can pick its Chromium); default is Playwright's."""
    shown = _norm(url)
    url, stop = _serve(shown)
    browser = (launch or launcher(pw))()
    merged: dict = {}
    outline, shots, runs = [], [], []
    try:
        for scheme in schemes:
            for view in views:
                w, h = VIEWS[view]
                ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1,
                                          has_touch=view == "phone", is_mobile=view == "phone")
                page = ctx.new_page()
                problems: list[str] = []
                page.on("pageerror", lambda e: problems.append(f"script error: {str(e)[:160]}"))
                page.on("console", lambda m: m.type == "error" and problems.append(f"console error: {m.text[:160]}"))
                page.on("requestfailed", lambda r: problems.append(f"failed to load: {r.url[:120]}"))
                page.on("response", lambda r: r.status >= 400 and problems.append(f"HTTP {r.status}: {r.url[:120]}"))
                try:
                    page.goto(url, wait_until="load", timeout=20000)
                    page.wait_for_timeout(500)
                    res = page.evaluate(AUDIT_JS)
                except Exception as e:  # noqa: BLE001
                    ctx.close()
                    merged[("load", "page", str(e)[:100])] = {"rule": "load", "sev": "must", "msg": f"could not open the page: {str(e)[:140]}",
                                                              "sel": url, "views": [f"{view}, {scheme}"]}
                    continue
                for p_ in dict.fromkeys(problems):
                    merged.setdefault(("console", "", p_), {"rule": "console", "sev": "must", "msg": p_, "sel": "", "views": []})["views"].append(f"{view}, {scheme}")
                for f in res["findings"]:
                    merged.setdefault((f["rule"], f["sel"], f["msg"]), dict(f, views=[]))["views"].append(f"{view}, {scheme}")
                if (view, scheme) == ("desktop", schemes[0]) or not outline:
                    outline = [f"{view}: {o}" for o in res["outline"]]
                if shots_dir:
                    try:
                        shots_dir.mkdir(parents=True, exist_ok=True)
                        f = shots_dir / f"ui-{view}-{scheme}.png"
                        page.screenshot(path=str(f), full_page=True)
                        shots.append(str(f))
                    except Exception:  # noqa: BLE001
                        pass
                runs.append(f"{view}, {scheme}")
                ctx.close()
    finally:
        browser.close()
        stop()
    url = shown
    findings = sorted(merged.values(), key=lambda f: (f["sev"] != "must", f["rule"]))
    return {"url": url, "findings": findings, "outline": outline, "shots": shots, "runs": runs,
            "text": report(url, findings, outline, shots, runs)}


def report(url: str, findings: list, outline: list, shots: list, runs: list) -> str:
    must = [f for f in findings if f["sev"] == "must"]
    should = [f for f in findings if f["sev"] != "must"]
    lines = [f"UI audit of {url}, checked at {', '.join(runs) or 'nothing'}."]
    if not findings:
        lines.append("No problems found.")
    else:
        lines.append(f"{len(must)} to fix, {len(should)} worth fixing.")

        def row(i, f):
            where = "" if len(f["views"]) >= max(1, len(runs)) else f"  [{'; '.join(f['views'])}]"
            return f"{i}. [{f['rule']}] {f['sel'] + ': ' if f['sel'] else ''}{f['msg']}{where}"
        n = 0
        for title, group in (("MUST FIX", must), ("SHOULD FIX", should)):
            if group:
                lines.append(title)
                for f in group[:25]:
                    n += 1
                    lines.append(row(n, f))
                if len(group) > 25:
                    lines.append(f"... and {len(group) - 25} more")
    if outline:
        lines.append("Layout:")
        lines += ["  " + o for o in outline[:12]]
    if shots:
        lines.append("Screenshots: " + ", ".join(shots))
    if findings:
        lines.append("Fix what is listed, then run ui_audit again. Do not call the page finished until the report says there are no problems.")
    return "\n".join(lines)
