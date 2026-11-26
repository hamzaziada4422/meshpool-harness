"""Brand items the request spells out: hex colours, font names, the footer text, "confidential", stamps.

Daniel, 0.0.19: the brief asked for footer "Ridgeway Advisory | Confidential", navy #14304F, amber #E8A33D and Georgia
headings. The first pass had a page-number-only footer (none on slide 1), the default bright blue accent, Segoe UI and
a non-navy theme; only a follow-up turn fixed it. Now:

    spec = parse(request)                  # {"colors": {"primary": "14304F", "accent": "E8A33D"}, "fonts": {...},
                                           #  "footer": "Ridgeway Advisory | Confidential", ...}
    theme, brand = look(spec)              # forge theme overrides and render brand settings, applied before building
    problems = check(pptx, spec)           # the QA loop verifies them on the built file (rule P28)
    enforce(pptx, spec)                    # and fixes the file in the same job when one did not hold

Only what the request states is used; nothing is guessed from colour words alone.
"""
from __future__ import annotations

import re
import zipfile

HEX = re.compile(r"#([0-9A-Fa-f]{6})\b")
FONT_NAMES = ["Segoe UI", "Arial", "Georgia", "Calibri", "Cambria", "Helvetica Neue", "Helvetica", "Times New Roman",
              "Verdana", "Tahoma", "Garamond", "Century Gothic", "Franklin Gothic Book", "Gill Sans MT", "Gill Sans",
              "Aptos Display", "Aptos", "Trebuchet MS", "Palatino Linotype", "Palatino", "Book Antiqua", "Corbel",
              "Candara", "Constantia", "Inter", "Lato", "Roboto", "Open Sans", "Montserrat", "Source Sans Pro",
              "Merriweather", "Playfair Display", "Baskerville", "Futura", "Avenir", "Frutiger"]
_ROLE = [("accent", r"\baccents?\b|\bhighlights?\b|\bsecondary\b|\bemphasis\b|\bcallouts?\b"),
         ("background", r"\bbackgrounds?\b"),
         ("text", r"\b(?:body )?text colou?r\b|\bfont colou?r\b"),
         ("primary", r"\bprimary\b|\bmain colou?r\b|\bbrand colou?r\b|\bbase colou?r\b|\bdominant\b")]
_STAMPS = re.compile(r"(?i)^\s*(?:preliminary|draft|for discussion(?: only)?|indicative|work in progress|wip|"
                     r"(?:strictly )?confidential|not for distribution|internal|illustrative only)\s*$")


def _role(t: str) -> str | None:
    for name, rx in _ROLE:
        if re.search(rx, t or "", re.I):
            return name
    return None


def _colors(text: str) -> dict:
    out: dict = {}
    loose = []
    for m in HEX.finditer(text):
        h = m.group(1).upper()
        cut = r"[,.;\n]|\bwith\b|\band\b|\bplus\b|#[0-9A-Fa-f]{6}"
        after = re.split(cut, text[m.end():m.end() + 50], flags=re.I)[0]
        before = re.split(cut.replace(",.;", ",.;:"), text[max(0, m.start() - 70):m.start()], flags=re.I)[-1]
        head = " ".join(re.sub(r"^\W+", "", after).split()[:4])    # "#E8A33D as the accent", "#E8A33D accent"
        role = _role(head) or _role(before)
        if role and role not in out:
            out[role] = h
        elif not role:
            loose.append(h)
    for h in loose:                                      # unnamed colours: the first is the primary, then the accent
        slot = next((r for r in ("primary", "accent") if r not in out), None)
        if slot:
            out[slot] = h
    return out


def _fonts(text: str) -> dict:
    names = "|".join(re.escape(f) for f in sorted(FONT_NAMES, key=len, reverse=True))
    out: dict = {}
    pats = [
        (r"\b(" + names + r")\b(?:\s+bold)?\s+(?:for\s+(?:all\s+|the\s+)?)?(headings?|titles?|headlines?)", "major"),
        (r"\b(headings?|titles?|headlines?)\s*(?:in|:|=|set in|use|using|should be|must be|are|in the)?\s*(?:the\s+)?(" +
         names + r")\b", "major"),
        (r"\b(" + names + r")\s+(?:for\s+(?:all\s+|the\s+)?)?(body(?: text)?|text|paragraphs?)\b", "minor"),
        (r"\b(body(?: text)?|paragraphs?)\s*(?:in|:|=|set in|use|using|should be|must be|is)?\s*(?:the\s+)?(" + names +
         r")\b", "minor"),
        (r"\b(" + names + r")\s+(fonts?|typefaces?|throughout|everywhere)\b", "both"),
        (r"\b(fonts?|typefaces?)\s*(?:is|:|=|in|of|should be|must be|use|using)?\s*(?:the\s+)?(" + names + r")\b", "both"),
    ]
    canon = {f.lower(): f for f in FONT_NAMES}
    for rx, role in pats:
        for m in re.finditer(rx, text, re.I):
            g = [x for x in m.groups() if x and x.lower() in canon]
            if not g:
                continue
            face = canon[g[0].lower()]
            for k in (("major", "minor") if role == "both" else (role,)):
                out.setdefault(k, face)
    return out


def _footer(text: str) -> str | None:
    q = r"['\"\u201c\u2018]([^'\"\u201d\u2019\n]{2,90})['\"\u201d\u2019]"
    m = re.search(r"(?i)\bfooters?\b[^'\"\u201c\u2018\n]{0,70}?" + q, text) or \
        re.search(q + r"(?i:\s+(?:in|on|as|at)\s+(?:the\s+|every\s+|each\s+|all\s+)?(?:slides?'?\s+)?footers?)", text)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()


def parse(text: str) -> dict:
    """What the request asks for, explicitly. Empty dict when it names none of it."""
    t = str(text or "")
    out: dict = {}
    c = _colors(t)
    if c:
        out["colors"] = c
    f = _fonts(t)
    if f:
        out["fonts"] = f
    foot = _footer(t)
    conf = bool(re.search(r"(?i)(?<!not )\bconfidential\b", t))
    if not foot and conf:
        foot = "Confidential"
    if foot:
        out["footer"] = foot
    if conf:
        out["confidential"] = True
    if re.search(r"(?i)\b(?:slide|page) numbers?\b", t):
        out["numbers"] = True
    return out


def allowed_stamps(text: str) -> set[str]:
    """Status stamps ("Preliminary", "Draft") the request itself uses: only those may mark a slide."""
    t = str(text or "").lower()
    words = ["preliminary", "draft", "for discussion", "indicative", "work in progress", "confidential",
             "not for distribution", "internal", "illustrative"]
    return {w for w in words if re.search(r"\b" + re.escape(w) + r"\b", t)}


def is_stamp(tag: str) -> bool:
    return bool(_STAMPS.match(str(tag or "")))


def stamp_ok(tag: str, allowed: set[str]) -> bool:
    return not is_stamp(tag) or any(a in str(tag).lower() for a in allowed)


# ------------------------------------------------------------------ the look the request asks for
def look(spec: dict) -> tuple[dict, dict]:
    """(theme overrides for template.theme_spec, brand settings for render) from a parsed request."""
    from .brand import contrast, mix
    theme: dict = {}
    c = (spec or {}).get("colors") or {}
    p, a = c.get("primary"), c.get("accent")
    if p:
        theme["dk2"] = p                                  # the brand dark: cover field, bands, callouts
        if contrast(p, "FFFFFF") >= 7:                    # dark enough for titles and text
            theme["dk1"] = p
        theme["hlink"] = p
        theme["lt2"] = mix(p, "FFFFFF", 0.93)             # panels: a whisper of the brand colour
        theme["accent4"] = mix(p, "FFFFFF", 0.45)         # a second series
    if a:
        theme["accent1"] = a                              # emphasis: numbers, the highlighted series, rules
        theme["accent3"] = a                              # the focus item in a chart
        theme["accent2"] = mix(a, "FFFFFF", 0.72)         # the pale highlight row (dark text on it)
    if c.get("text"):
        theme["dk1"] = c["text"]
    if c.get("background"):
        theme["lt1"] = c["background"]
    f = (spec or {}).get("fonts") or {}
    if f:
        from .template import FONTS
        theme["fonts"] = {"major": f.get("major") or FONTS["major"], "minor": f.get("minor") or FONTS["minor"]}
    brand: dict = {}
    if (spec or {}).get("footer"):
        brand = {"footer": spec["footer"], "footer_every": True}
    return theme, brand


def merge(look_: dict, spec: dict) -> dict:
    """forge build arguments (theme, brand, template) with the request's own brand items on top. A company master is
    dropped when the request names colours or fonts, as a named palette in the request already does."""
    if not spec:
        return look_
    theme, brand = look(spec)
    out = dict(look_ or {})
    if theme:
        base = out.get("theme")
        base = base if isinstance(base, dict) else ({"palette": base} if base else {})
        fonts = theme.get("fonts")
        if fonts and isinstance(base.get("fonts"), dict):   # a profile's body font stays when only headings are named
            fonts = {"major": (spec.get("fonts") or {}).get("major") or base["fonts"].get("major") or fonts["major"],
                     "minor": (spec.get("fonts") or {}).get("minor") or base["fonts"].get("minor") or fonts["minor"]}
        out["theme"] = {**base, **theme, **({"fonts": fonts} if fonts else {})}
        out.pop("template", None)
    out["brand"] = {**(out.get("brand") or {}), **brand, "asked": spec}
    return out


# ------------------------------------------------------------------ verify and fix the built file
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _theme(path: str) -> tuple[dict, dict]:
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if re.match(r"ppt/theme/theme\d+\.xml$", n)), None)
        x = z.read(name).decode("utf-8", "replace") if name else ""
    cols = {}
    for slot in ("dk1", "dk2", "lt1", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6"):
        m = re.search(rf"<a:{slot}>\s*<a:(?:srgbClr val|sysClr[^>]*lastClr)=\"([0-9A-Fa-f]{{6}})\"", x)
        if m:
            cols[slot] = m.group(1).upper()
    fonts = {}
    for kind in ("major", "minor"):
        m = re.search(rf"<a:{kind}Font>\s*<a:latin typeface=\"([^\"]*)\"", x)
        if m:
            fonts[kind] = m.group(1)
    return cols, fonts


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def check(path: str, spec: dict) -> list[dict]:
    """Failures (rule P28) for every brand item of the request the file does not carry."""
    from .common import failure as F
    if not spec:
        return []
    out = []
    try:
        from pptx import Presentation
        prs = Presentation(path)
        cols, fonts = _theme(path)
    except Exception as e:  # noqa: BLE001
        return [F("P28", "brand", f"The brand the request asks for could not be checked ({type(e).__name__}).",
                  status="not_run", severity="info", fix_scope="renderer")]
    c = spec.get("colors") or {}
    want = [("dk2", c.get("primary"), "the primary colour"), ("accent1", c.get("accent"), "the accent colour")]
    for slot, hexv, what in want:
        if hexv and cols.get(slot) != hexv.upper():
            out.append(F("P28", "brand_color", f"The theme does not use {what} #{hexv.upper()} the request asks for "
                         f"(it has #{cols.get(slot, 'none')}).", fix_scope="renderer"))
    for kind, face in (spec.get("fonts") or {}).items():
        if face and _norm(fonts.get(kind)) != _norm(face):
            out.append(F("P28", "brand_font", f"{'Headings' if kind == 'major' else 'Body text'} are not in {face} as "
                         f"the request asks (the theme has {fonts.get(kind) or 'none'}).", fix_scope="renderer"))
    foot = spec.get("footer")
    if foot:
        miss = []
        for i, sl in enumerate(prs.slides, 1):
            txt = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
            if _norm(foot) not in _norm(txt):
                miss.append(i)
        if miss:
            out.append(F("P28", "brand_footer", f"The footer '{foot}' the request asks for is missing on slide"
                         f"{'s' if len(miss) > 1 else ''} {', '.join(map(str, miss))}.", slide=miss[0],
                         fix_scope="renderer"))
    return out


def enforce(path: str, spec: dict) -> list[str]:
    """Fix the file in place: theme colours and fonts, and the footer text on every slide that lacks it. Returns what
    was changed, in plain words."""
    from lxml import etree
    from pptx import Presentation
    from pptx.enum.dml import MSO_THEME_COLOR
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt
    done = []
    theme, _ = look(spec)
    prs = Presentation(path)
    if theme:
        for m in prs.slide_masters:
            part = next((r.target_part for r in m.part.rels.values() if r.reltype.endswith("/theme")), None)
            if part is None:
                continue
            x = etree.fromstring(part.blob)
            cs = x.find(f".//{{{A}}}clrScheme")
            for el in cs if cs is not None else []:
                slot = etree.QName(el).localname
                if slot in theme and isinstance(theme[slot], str):
                    for ch in list(el):
                        el.remove(ch)
                    etree.SubElement(el, f"{{{A}}}srgbClr", val=theme[slot])
            fs = x.find(f".//{{{A}}}fontScheme")
            for kind in ("major", "minor"):
                face = (theme.get("fonts") or {}).get(kind)
                lat = fs.find(f"{{{A}}}{kind}Font/{{{A}}}latin") if fs is not None else None
                if face and lat is not None:
                    lat.set("typeface", face)
            part._blob = etree.tostring(x, xml_declaration=True, encoding="UTF-8", standalone=True)
        done.append("set the theme colours and fonts the request asks for")
    foot = spec.get("footer")
    if foot:
        W, H = prs.slide_width / 914400, prs.slide_height / 914400
        added = []
        for i, sl in enumerate(prs.slides, 1):
            txt = " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)
            if _norm(foot) in _norm(txt):
                continue
            old = next((sh for sh in sl.shapes if sh.has_text_frame and sh.name == "Firm"), None)
            num = old.text_frame.text.strip() if old is not None else ""
            num = num if re.fullmatch(r"\d{1,3}", num) else (str(i) if i > 1 else "")
            if old is not None:
                old._element.getparent().remove(old._element)
            tb = sl.shapes.add_textbox(Inches(W * 0.45), Inches(H - 0.44), Inches(W * 0.55 - 0.6), Inches(0.25))
            tb.name = "Firm"
            tf = tb.text_frame
            tf.margin_left = tf.margin_right = 0
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.RIGHT
            r = p.add_run()
            r.text = f"{foot}      {num}".rstrip()
            r.font.size = Pt(10)
            r.font.color.theme_color = MSO_THEME_COLOR.TEXT_1
            added.append(i)
        if added:
            done.append(f"added the footer '{foot}' to slide{'s' if len(added) > 1 else ''} "
                        f"{', '.join(map(str, added))}")
    if done:
        prs.save(path)
    return done
