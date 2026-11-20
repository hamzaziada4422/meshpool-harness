"""Style packs: seven looks clustered from 132 reference decks (dominant accent hue, light or dark background,
serif or sans titles, ink density), each authored as our own 12-slot palette plus a font pair Forge can measure.

    python -m engines.forge.build brief.md out --style teal-modern
    from engines.forge import styles
    render(slides, path, **styles.render_kwargs("teal-modern"))

No firm names, logos or masters: a pack is only colours and fonts. Slot roles are those of template.PALETTES (dk1 text,
dk2 darkest brand tone, lt2 panel grey, accent1 emphasis, accent2 the one highlight, accent3 focus, accent4 secondary
series, accent5 rules, accent6 muted text). The values start from each family's measured median colours and are
adjusted until brand.check_roles passes (emphasis 3:1, secondary series 3:1 so white labels read on it, brand dark 4.5:1 for white text, highlight light enough for dark
text). "evidence" counts the reference decks in the family; the 12 decks with dark slide backgrounds are rendered here
as a dark brand tone on white slides, because Forge slides are white.
"""
from __future__ import annotations

from .brand import check_roles
from .template import FONT_PAIRS


def _p(dk1, dk2, lt2, a1, a2, a3, a4, a5, a6):
    return {"dk1": dk1, "lt1": "FFFFFF", "dk2": dk2, "lt2": lt2, "accent1": a1, "accent2": a2, "accent3": a3,
            "accent4": a4, "accent5": a5, "accent6": a6, "hlink": a1, "folHlink": a6}


STYLES = {
    "navy-corporate": {
        "label": "Navy corporate",
        "description": "Deep navy with a mid-blue emphasis and a soft lilac highlight on white, in a clean sans.",
        "palette": _p("0B1B33", "0E2F63", "EDF1F7", "17478A", "CDBBE3", "4A86C8", "6182AF", "C5CCD8", "5A6578"),
        "fonts": "segoe",
        "evidence": {"decks": 44, "kinds": {"financial_services": 25, "consulting": 19}, "background": "light",
                     "median_accent": "17478A"}},
    "teal-modern": {
        "label": "Teal modern",
        "description": "Clear teal with a sand highlight and a deep teal dark tone, in a modern sans.",
        "palette": _p("10242A", "0F4C57", "EEF5F6", "1B7F8E", "E8DCC6", "35ABB9", "4F9CA8", "C7D5D8", "55666A"),
        "fonts": "segoe",
        "evidence": {"decks": 29, "kinds": {"consulting": 21, "financial_services": 8}, "background": "light",
                     "median_accent": "35ABB9"}},
    "deep-green": {
        "label": "Deep green",
        "description": "Forest green with a pale blue-grey highlight on white, in a clean sans.",
        "palette": _p("112019", "0E4A2C", "EEF5F0", "17824A", "C9E0E4", "35B070", "4FA075", "C9D6CD", "56685E"),
        "fonts": "segoe",
        "evidence": {"decks": 17, "kinds": {"consulting": 16, "financial_services": 1}, "background": "light",
                     "median_accent": "1AA057"}},
    "charcoal-orange": {
        "label": "Charcoal and orange",
        "description": "Charcoal text and bands with a burnt-orange emphasis and a pale amber highlight.",
        "palette": _p("1C1C1E", "2E2E33", "F2F0EE", "C25E00", "FFDDA6", "E38116", "7A7A82", "D3D0CB", "63615C"),
        "fonts": "segoe",
        "evidence": {"decks": 11, "kinds": {"consulting": 6, "financial_services": 5}, "background": "light",
                     "median_accent": "E38116"}},
    "burgundy-classic": {
        "label": "Burgundy classic",
        "description": "Burgundy and plum on white with a warm gold highlight, in a soft humanist sans.",
        "palette": _p("1F1416", "5C1230", "F6EFF0", "A81C40", "F5D77A", "CA5763", "C3627B", "DACDD0", "6A5C60"),
        "fonts": "segoe",
        "evidence": {"decks": 11, "kinds": {"consulting": 6, "financial_services": 5}, "background": "light",
                     "median_accent": "A81C40"}},
    "dark-slate": {
        "label": "Dark slate",
        "description": "Dark slate bands and periwinkle emphasis with a dusty-rose highlight, in a modern sans.",
        "palette": _p("15161F", "1D2333", "EEEFF4", "4A5BA8", "E8CFCB", "7380BC", "707DBA", "CBCDD8", "5B5F70"),
        "fonts": "segoe",
        "evidence": {"decks": 12, "kinds": {"consulting": 6, "financial_services": 6}, "background": "dark",
                     "median_accent": "7380BC"}},
    "monochrome": {
        "label": "Monochrome",
        "description": "Near-black and grey with a single steel-blue accent, in a clean sans.",
        "palette": _p("111111", "1F2933", "F3F4F6", "35567F", "D5DEEA", "7C9CC4", "6A82A0", "D3D7DC", "5F6872"),
        # 7 of the 8 decks set titles in a serif; the "serif" pair is held back until the argument slide's right-hand
        # title (render.py role_subtitle_Right, set in the heading face) stops counting as body text in P19.
        "fonts": "segoe",
        "evidence": {"decks": 8, "kinds": {"financial_services": 8}, "background": "light", "median_accent": None,
                     "serif_titles": 7}},
}


def names() -> list[str]:
    return list(STYLES)


def get(name: str) -> dict:
    key = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    if key not in STYLES:
        raise KeyError(f"Unknown style {name!r}. Styles: {', '.join(names())}.")
    return STYLES[key]


def theme(name: str) -> dict:
    """The dict render(theme=...) takes: palette slots as hex plus the font pair."""
    s = get(name)
    return {"palette": "navy", **s["palette"], "fonts": dict(FONT_PAIRS[s["fonts"]])}


def render_kwargs(name: str) -> dict:
    return {"theme": theme(name)}


def listing() -> list[dict]:
    """For Settings > Company: name, label, description, swatch colours, fonts, deck count."""
    return [{"name": n, "label": s["label"], "description": s["description"], "palette": s["palette"],
             "fonts": FONT_PAIRS[s["fonts"]], "decks": s["evidence"]["decks"], "problems": check_roles(s["palette"])}
            for n, s in STYLES.items()]
