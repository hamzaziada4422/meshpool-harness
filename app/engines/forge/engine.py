"""Forge deck engine: request (+ input files) -> consulting-grade .pptx in the output folder.

The model writes the storyline and the slide content as JSON; the forge renderer applies the design system
(engines/forge/render.py) and validate_pptx checks the rendered file. Used by `meshpool run --engine forge` and
`--engine slides` (engines/slides/engine.py is a thin entry to this module).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _inputs(workdir: str, files: list[str] | None) -> list[str]:
    """The files given with the request; with none given (the CLI), the input files in the work folder."""
    if files:
        return list(files)
    return [str(f) for f in sorted(Path(workdir).iterdir()) if f.is_file()
            and f.suffix.lower() in (".md", ".txt", ".pdf", ".docx", ".pptx", ".potx", ".xlsx", ".csv")]


def _brief(task: str, workdir: str, files: list[str] | None) -> str:
    import time
    # the model does not know the date: without this a cover reads "March 2026" in November
    parts = [task, f"(Today is {time.strftime('%d %B %Y')}. Date the cover {time.strftime('%B %Y')} unless the brief "
                   "gives another date.)"]
    for f in _inputs(workdir, files):
        p = Path(f)
        try:
            if p.suffix.lower() in (".md", ".txt", ".csv"):
                text = p.read_text(errors="replace")
            else:
                from meshpool_harness import docread
                text = docread.extract(p)
        except Exception as e:  # an unreadable input is reported, not fatal
            text = f"[could not read {p.name}: {type(e).__name__}]"
        parts.append(f"\n--- {p.name} ---\n{text[:20000]}")
    return "\n".join(parts)


def theme_from(task: str):
    """'ocean theme', 'palette: forest', 'sans fonts', 'accent #7C3AED' in the request -> a theme spec (or None)."""
    from engines.forge.template import FONT_PAIRS, PALETTES
    t = task.lower()
    spec = {}
    m = re.search(r"\b(" + "|".join(PALETTES) + r")\b\s*(?:theme|palette|colou?rs?|look)|(?:theme|palette|colou?rs?)\s*[:=]?\s*(" + "|".join(PALETTES) + r")\b", t)
    if m:
        spec["palette"] = m.group(1) or m.group(2)
    f = re.search(r"\b(" + "|".join(FONT_PAIRS) + r")\s*fonts?\b|fonts?\s*[:=]?\s*(" + "|".join(FONT_PAIRS) + r")\b", t)
    if f:
        spec["fonts"] = f.group(1) or f.group(2)
    a = re.search(r"accent\s*(?:colou?r)?\s*[:=]?\s*#?([0-9a-f]{6})\b", t)
    if a:
        spec["accent1"] = a.group(1)
    return spec or None


LIKE_THESE = re.compile(r"(?i)\b(?:same|this|that|their|these|the attached|our)\s+(?:style|look|format|template|"
                        r"branding|design)\b|\b(?:like|match(?:es|ing)?|based on|copy)\s+(?:this|that|these|the "
                        r"attached|the old|our old|the existing|our existing)\s+(?:decks?|presentations?|slides?|"
                        r"templates?)\b|\bas (?:a )?template\b")


def brand_from(task: str, files: list[str] | None = None) -> tuple[dict, dict | None]:
    """render arguments for the company look: decks attached to the request as the style to follow ('same format as
    the attached deck'), else the profile the request names or the active one (Settings > Company); colour words in
    the request ('ocean theme', 'accent #7C3AED') override its colours. ({...}, profile or None)."""
    asked = theme_from(task)
    prof = None
    try:
        from engines.forge import brand as B
        decks = [f for f in files or [] if str(f).lower().endswith((".pptx", ".potx"))]
        if decks and LIKE_THESE.search(task):                    # learned for this deck only, not saved
            import tempfile
            prof = B.clean(B.learn(decks, folder=tempfile.mkdtemp(prefix="forge_look_")))
        else:
            prof = B.for_request(task)
    except Exception as e:                                       # noqa: BLE001 - a broken profile never blocks a deck
        print(f"company look skipped: {type(e).__name__}: {e}", file=sys.stderr)
        prof = None
    if not prof:
        if not asked:                                            # Settings > Company: a style pack for new decks
            try:
                from engines.forge import brand as B, styles as ST
                chosen = B._index().get("style")
                if chosen in ST.names():
                    return {"theme": ST.theme(chosen)}, None
            except Exception:                                    # noqa: BLE001 - a broken index never blocks a deck
                pass
        return {"theme": asked}, None
    look = B.render_kwargs(prof)
    if asked:
        look["theme"] = asked
        look.pop("template", None)
    return look, prof


def icon_set_from(task: str, options: dict | None = None) -> str | None:
    """The deck's icon set: options["icon_set"], or 'icon set: phosphor', 'material icons', 'carbon icon style' in
    the request (engines/icons SETS). None keeps the default (Lucide)."""
    from engines.icons import SETS
    asked = (options or {}).get("icon_set")
    if asked:
        return str(asked).strip().lower() if str(asked).strip().lower() in SETS else None
    names = "|".join(re.escape(s) for s in sorted(SETS, key=len, reverse=True))
    m = re.search(r"\bicon(?:s| set| style)?\s*[:=]\s*(" + names + r")\b|\b(" + names + r")\s+icons?\b", task.lower())
    return (m.group(1) or m.group(2)) if m else None


def density_from(task: str, options: dict | None = None, density: str | None = None) -> str:
    """'for presenting', 'keynote', 'visual' -> present; 'send-ahead', 'pre-read', 'detailed', 'dense' -> read;
    otherwise balanced. An explicit density (argument, options["density"], or 'density: x' in the request) wins."""
    from engines.forge.density import detect
    return detect(task, density or (options or {}).get("density"))


ADD_RE = re.compile(r"^\[add to deck: ([^\]]+)\]\s*", re.S)


def _plain_office(deck: str) -> bool:
    """True for a deck in PowerPoint's stock look: the Office theme and colours, stock fonts, no logo in the master."""
    import zipfile
    try:
        with zipfile.ZipFile(deck) as z:
            theme = z.read("ppt/theme/theme1.xml").decode("utf-8", "replace")
            pics = [n for n in z.namelist() if n.startswith(("ppt/slideMasters/_rels", "ppt/slideLayouts/_rels"))
                    and b"relationships/image" in z.read(n)]
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    fonts = set(re.findall(r'<a:latin typeface="([^"]*)"', theme)[:2])
    stock = {"Calibri", "Calibri Light", "Aptos", "Aptos Display", "Arial", "Times New Roman", ""}
    return bool(re.search(r'<a:clrScheme name="Office', theme)) and fonts <= stock and not pics


_WORDN = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def _forge_look(deck: str) -> dict | None:
    """A deck this engine made carries its look in its theme: the added slides take those colours and fonts as they
    are, and its footer line. Learning the look from the slides instead made the most used colour (navy) the accent,
    and the added chart's bars came out navy, grey and pale amber (Daniel, 0.0.19)."""
    import zipfile
    try:
        with zipfile.ZipFile(deck) as z:
            theme = next((z.read(n).decode("utf-8", "replace") for n in z.namelist()
                          if re.match(r"ppt/theme/theme\d+\.xml$", n)), "")
        if '<a:clrScheme name="Forge"' not in theme:
            return None
        from engines.forge.requested import _theme
        cols, fonts = _theme(deck)
        from pptx import Presentation
        foot = None
        for sl in list(Presentation(deck).slides)[1:]:
            lab = next((sh.text_frame.text for sh in sl.shapes if sh.has_text_frame and sh.name == "Firm"), "")
            lab = re.sub(r"\s*\d{1,3}\s*$", "", lab).strip()
            if lab:
                foot = lab
                break
    except Exception:  # noqa: BLE001 - an unreadable deck: the look is learned the usual way
        return None
    look = {"theme": {"palette": "navy", **cols, **({"fonts": fonts} if len(fonts) == 2 else {})}}
    if foot:
        look["brand"] = {"footer": foot, "footer_every": True}
    return look


def insert_position(task: str) -> int | None:
    """Where the new slides go in the user's deck (1-based place of the first new slide), or None to append:
    "after slide 4" -> 5, "before slide 7" / "as slide 7" / "at slide 7" / "in position 7" -> 7, "at the start" -> 1."""
    t = (task or "").lower()
    if re.search(r"\b(at|to) the (start|beginning|front)\b|\bbefore (the )?first slide\b", t):
        return 1
    words = "|".join(_WORDN)
    m = re.search(r"\b(after|before|as|at|in position|into position)\s+(?:slide|page|position)\s*(?:number\s*)?"
                  r"(\d{1,3}|" + words + r")\b", t) or \
        re.search(r"\b(after|before)\s+the\s+(" + words + r")\s+(?:slide|page)\b", t)
    if not m:
        return None
    n = int(m.group(2)) if m.group(2).isdigit() else _WORDN[m.group(2)]
    return n + 1 if m.group(1) == "after" else max(1, n)


def add_to_deck(task: str, deck: str, *, workdir: str, task_id: str, client=None, files: list[str] | None = None,
                model: str | None = None, density: str | None = None) -> dict:
    """Slides added to a deck the user already has: forge builds them in that deck's look (its master and theme
    when it is 16:9, else its colours and fonts), then they are added to the deck itself, in place (Undo keeps the
    earlier version). A copy "<name> (updated).pptx" is made only when the deck is open in another app or the request
    asks for one ("save as", "keep the original"): see deck_target."""
    from engines.forge.brief import slide_count
    from engines.forge.build import build
    from engines.forge.merge import append
    if model:
        os.environ.setdefault("FORGE_MODEL", model)
    n = min(max(slide_count(task) or 1, 1), 12)
    # A real brand (its own colours, fonts or logo) is followed; PowerPoint's stock Office theme is not a brand, and
    # copying it gave Calibri and stock colours: those decks get the forge design system instead.
    look = {} if _plain_office(deck) else (_forge_look(deck) or brand_from("same style as the attached deck", [deck])[0])
    from engines.forge import requested as RQ               # colours, fonts, footer the request spells out win
    look = RQ.merge(look, RQ.parse(task))
    res = build(_brief(task, workdir, files), workdir, n, client=client, task_id=task_id,
                density=density_from(task, None, density), insert=True, **look)
    src = Path(deck)
    # the job works on a copy in "Meshpool outputs/<job>/": save next to the user's own deck when it is there
    orig = src.parent.parent.parent / src.name
    home = orig.parent if src.parent.parent.name == "Meshpool outputs" and orig.is_file() else src.parent
    out, why = deck_target(home / src.name, task)
    at = insert_position(task)
    part = out.with_name(f".{out.stem}.{os.getpid()}.part{out.suffix}")   # written whole, then moved into place:
    try:                                                  # a failed merge never leaves the user's deck half-written
        if at:                                            # "after slide 4", "before slide 7", "as slide 3"
            from engines.forge.merge import insert
            added = insert(str(src), res["pptx"], str(part), at)
        else:
            added = append(str(src), res["pptx"], str(part))
        os.replace(part, out)
    finally:
        if part.exists():
            part.unlink()
    left = sum(len(v) for v in (res.get("unresolved") or {}).values())
    saved = (f" and saved it as {out.name} (the same deck, changed in place; Undo puts the earlier version back)"
             if why == "in_place" else
             f" and saved it as {out.name}: {src.name} is open in another app (its lock file is next to it), so it "
             "was not changed" if why == "locked" else f" and saved it as {out.name}, as asked; {src.name} is unchanged")
    return {"ok": out.exists(), "artifacts": [str(out)], "evidence": [], "turns": res.get("calls"), "reason": None,
            "answer": (f"Added {added} slide{'s' if added != 1 else ''} to {src.name}"
                       + (f" at slide {at}" if at else "") + saved +
                       ". They follow the deck's look and were checked after rendering"
                       + (f"; {left} items are still flagged by the checks (listed in qa.json)." if left else "."))}


_SAVE_AS = re.compile(r"(?i)\bsave (?:it |this |the deck |the result )?as\b|\bas a (?:new |separate )?(?:copy|file|"
                      r"version)\b|\b(?:new|separate) (?:copy|file|version)\b|\bkeep the original\b|"
                      r"\b(?:don'?t|do not|never) (?:change|overwrite|touch) the original\b|\bleave the original\b")


def _locked(p: Path) -> bool:
    """Open in PowerPoint (~$name, or ~$ and the name less one or two letters) or LibreOffice (.~lock.name#)."""
    n = p.name
    return any((p.parent / x).exists() for x in ("~$" + n, "~$" + n[1:], "~$" + n[2:], f".~lock.{n}#"))


def deck_target(deck: Path, task: str = "") -> tuple[Path, str]:
    """Where slides added to a deck are saved: the deck itself, changed in place (the chat's Undo checkpoint keeps
    the earlier version), unless the deck is open in another app (its lock file is next to it) or the request asks
    for a copy ("save as", "keep the original"). Daniel, round 2: "update the same deck" made "Deck (updated).pptx"
    beside the original and took more prompts to merge the two. Returns (path, "in_place" | "locked" | "save_as")."""
    why = "locked" if _locked(deck) else "save_as" if _SAVE_AS.search(task or "") else "in_place"
    if why == "in_place":
        return deck, why
    out, i = deck.with_name(f"{deck.stem} (updated){deck.suffix}"), 2
    while out.exists():
        out = deck.with_name(f"{deck.stem} (updated {i}){deck.suffix}")
        i += 1
    return out, why


def panel_sentence(panel: dict | None) -> str:
    """The reviewers in the answer: the storyboard rounds and research, each persona's final score and verdict
    ("Senior partner 8.4/10 signs off; MD 7.6/10: revise, top issue: ..."), or plainly why they could not run."""
    if not panel or panel.get("status") == "off":
        return ""
    out = ""
    sb = panel.get("storyboard") or {}
    rounds = sb.get("rounds") or []
    steps = (panel.get("research") or {}).get("steps") or []
    found = sum(len(s.get("new_facts") or []) for s in steps)
    asked = sum(len(s.get("questions") or []) for s in steps)
    if rounds and any(r.get("score") is not None for r in rounds):
        out += (f" Storyboard: {len(rounds)} review round{'s' if len(rounds) != 1 else ''} before any slide was "
                f"written, {'signed off' if sb.get('signed_off') else 'not signed off'}")
        out += (f"; research answered {sum(1 for s in steps for q in s.get('questions') or [] if q.get('status') == 'found')}"
                f" of {asked} reviewer questions with {found} sourced facts." if asked else ".")
    deck = panel.get("deck") or {}
    n = len(deck.get("rounds") or [])
    if panel.get("status") == "not_run":
        why = next((r.get("why_not_run") for r in reversed(deck.get("rounds") or []) if r.get("why_not_run")),
                   None) or "no reviewer answered"
        return out + (f" The senior partner and MD reviews could not run ({why}), so the deck has no review score; "
                      "it passed the automatic checks only.")
    if panel.get("line"):
        out += f" Review panel after {n} round{'s' if n != 1 else ''}: {panel['line']}."
    if panel.get("status") == "revise" and (deck.get("final") or {}).get("open_issues"):
        out += " Their open blocking and major issues are listed in qa.json."
    return out


def design_sentence(dz: dict | None) -> str:
    """Design-first pages in the answer: how many slides were image-designed and rebuilt, how many fell back."""
    if not dz:
        return ""
    per = list((dz.get("slides") or {}).values())
    done = sum(1 for d in per if d.get("path") == "image")
    back = [d for d in per if d.get("path") != "image"]
    if not per:
        return ""
    out = f" {done} of {len(per)} content slides were image-designed and rebuilt as editable shapes (originals in design/)"
    if back:
        out += f"; {len(back)} kept the standard layout ({back[0].get('why') or 'no design'})"
    return out + "."


def solve(task: str, *, workdir: str, task_id: str, client=None, files: list[str] | None = None,
          model: str | None = None, density: str | None = None, options: dict | None = None, **_) -> dict:
    m = ADD_RE.match(task or "")
    if m:                                                       # add_slides: into the user's own deck
        name = m.group(1).strip()
        deck = next((f for f in files or [] if Path(f).name == Path(name).name), None) or str(Path(workdir) / name)
        if not Path(deck).is_file():
            return {"ok": False, "answer": f"The deck {name} was not found.", "artifacts": [], "reason": "error"}
        return add_to_deck(task[m.end():], deck, workdir=workdir, task_id=task_id, client=client,
                           files=[f for f in files or [] if f != deck] + [deck], model=model, density=density)
    from engines.forge.build import build
    from engines.forge.brief import complete, slide_count
    attached = _inputs(workdir, files)                          # the CLI passes no files: the work folder's inputs
    # an open brief (audience, density, length, look) gets the recommended answers (engines/forge/brief.py)
    n = (options or {}).get("slides")                           # an explicit slides field wins over the wording
    task = complete(task, attached, {"slides": n} if n else None)
    if model:
        os.environ.setdefault("FORGE_MODEL", model)
    slides = slide_count(task) or 10
    mode = density_from(task, options, density)
    fixed, extra = [], []
    from engines.forge.finance_pages import finance_workbook, pages_from_workbook
    cands = list(files or []) + [str(p) for p in Path(workdir).glob("*.xls*") if not p.name.startswith("~$")]
    wbk = finance_workbook(cands)
    if wbk and not re.search(r"\b(no|without) (model|valuation) pages\b", task, re.I):
        try:                                                    # banker pages computed from the model's outputs
            fixed, extra = pages_from_workbook(wbk)
        except Exception as e:                                  # noqa: BLE001 - the deck still builds without them
            print(f"model pages skipped: {type(e).__name__}: {e}", file=sys.stderr)
    nfin = len(fixed)
    expect = None
    try:                    # chart pages computed from the user's own csv/xlsx (the pivot the request asks for), P22
        from engines.forge import data_pages
        dpages, dfacts, expect = data_pages.pages(task, [f for f in attached if not (fixed and f == wbk)])
        fixed, extra = fixed + dpages, extra + dfacts
    except Exception as e:  # noqa: BLE001 - the deck still builds without them
        print(f"data pages skipped: {type(e).__name__}: {e}", file=sys.stderr)
    # The computed pages count inside the slides asked for: the model writes the rest (at least 3: cover, argument,
    # next steps). Asked for fewer than that leaves room for, the first pages (football field, DCF, comps) stay.
    if fixed and len(fixed) > slides - 3:
        keep = {id(p) for p in sorted(fixed, key=lambda p: p.get("rank", 99))[:max(2, slides - 3)]}
        fixed = [p for p in fixed if id(p) in keep]               # the most useful pages, still in deck order
    look, prof = brand_from(task, attached)
    from engines.forge import requested as RQ               # hex colours, fonts, footer text the request spells out
    look = RQ.merge(look, RQ.parse(task))                   # go into the build, and its QA loop checks them (P28)
    iset = icon_set_from(task, options)
    if iset:                                                    # one icon set for the whole deck
        th = look.get("theme")
        look["theme"] = {**(th if isinstance(th, dict) else {"palette": th} if th else {}), "icon_set": iset}
    from engines.forge.design import mode_from as design_mode
    from engines.forge import select as SEL
    kw_ = dict(density=mode, fixed_pages=fixed, extra_facts=extra, data_expectations=expect or None, research=(options or {}).get("research"),
               design=design_mode(task, options), **look)
    if SEL.wanted() > 1:                                       # best of N builds, graded (MH_BEST_OF; off by default)
        res = SEL.best_of(_brief(task, workdir, files), workdir, SEL.wanted(), build, client=client,
                          slides_wanted=max(slides, len(fixed) + 3), **kw_)
    else:
        res = build(_brief(task, workdir, files), workdir, max(slides, len(fixed) + 3), client=client,
                    task_id=task_id, **kw_)
    pptx = res["pptx"]
    failing = [k for k, v in res["rules"].items() if v == "fail"]
    left = sum(len(v) for v in (res.get("unresolved") or {}).values())
    try:
        from pptx import Presentation
        made = len(Presentation(pptx).slides)               # what is in the file, not what was planned
    except Exception:  # noqa: BLE001
        made = max(slides, len(fixed) + 3)
    answer = (f"Built {Path(pptx).name}: {made} slides in the forge design system ({mode} density), "
              + (f"{nfin} of them computed from {Path(wbk).name} (football field, comps, DCF, statements), "
                 if nfin else "")
              + (f"{len(expect)} chart page{'s' if len(expect) != 1 else ''} computed from "
                 f"{', '.join(dict.fromkeys(e['file'] for e in expect))}, "
                 if expect else "")
              + (f"in the {prof.get('name') or 'company'} look ("
                 + ("its slide master, " if look.get("template") else "") + "colours, fonts"
                 + (", logo" if (look.get("brand") or {}).get("logo") else "") + "), " if prof else "")
              + "checked after rendering. "
              + (f"{left} items are still flagged by the checks (listed in qa.json); rules failing: "
                 f"{', '.join(failing) or 'none'}." if left or failing else "All checks passed."))
    answer += design_sentence(res.get("design"))
    answer += panel_sentence(res.get("panel"))
    rv = res.get("review") or {}
    if rv.get("score") is not None:                            # the partner review's verdict, said plainly
        answer += (f" Partner review: {rv['score']}/10, "
                   + ("ready to send." if rv.get("verdict") == "ship" else
                      f"slides {', '.join(map(str, rv.get('fix') or []))} still need work"
                      + (f" (most common: {rv['top'][0]['example']})" if rv.get("top") else "") + "."))
    if rv.get("note"):                                         # the review did not run (or only in part): say why
        answer += f" ({rv['note']}.)" if rv.get("score") is not None else f" {rv['note'][0].upper()}{rv['note'][1:]}."
    if res.get("content_failed"):                              # said in the chat; never written into the file
        answer += " Not written: " + "; ".join(res["content_failed"]) + "."
    return {"ok": Path(pptx).exists(), "answer": answer, "artifacts": [pptx], "evidence": [],
            "turns": res.get("calls"), "reason": None}
