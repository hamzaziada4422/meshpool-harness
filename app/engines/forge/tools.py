"""Forge tools for Meshpool Work (Cowork): exposed through the meshpool-office MCP server.

check_deck(path, slide_count?, data_files?, request?) -> P0-P19 report (+ P22 against the data) with repair messages (P0: opens in PowerPoint without repair)
check_workbook(path)             -> X0-X13 report after a headless recalculation (X0: opens in Excel without repair)
check_document(path)             -> does a .docx/.pptx/.xlsx open in Office without a repair prompt
measure_office_file(path)        -> anatomy of a .pptx, .xlsx or .pdf (how it is built), with evidence IDs
slide_layouts(message)           -> which archetypes prove a slide's message, with the reference decks' numbers
Each returns (text, is_error). The text is short enough for a model to act on. The tools only read: they never
write next to the user's files, so the agent can run them without asking.
"""
from __future__ import annotations

import json
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)


def _sidecar(path: Path, name: str):
    p = path.with_name(f"{path.stem}.{name}.json")
    if not p.exists():
        # a job folder (built by forge) keeps its sidecars under the plain name; a user's own deck never borrows
        # an unrelated job's file from the same folder
        if not (path.parent / "storyline.json").exists():
            return None
        p = path.parent / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def _p22(path: str, data_files, request) -> list[dict]:
    """P22: the charts of the deck against the data files the request asks a pivot of ([] when none are asked)."""
    if not data_files or not (request or "").strip():
        return []
    from . import data_pages
    _, _, expect = data_pages.pages(request, [data_files] if isinstance(data_files, str) else data_files)
    return data_pages.check_data(path, expect)


def check_deck(path: str, slide_count: int | None = None, render: bool = True, data_files=None,
               request: str | None = None) -> tuple[str, bool]:
    from .validate_pptx import report_text, validate
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    brief = {"slide_count": int(slide_count)} if slide_count else _sidecar(p, "brief")
    res = validate(str(p), brief=brief, provenance=_sidecar(p, "provenance"), storyline=_sidecar(p, "storyline"),
                   render=render)
    p22 = _p22(str(p), data_files, request)
    res["failures"] += p22
    if p22:
        res["rules"]["P22"] = "fail"
    return report_text(res, 30) + _scope_note(res), False


def _scope_note(res: dict) -> str:
    """Tell the agent which failures a rebuild can fix. Without it, renderer-scope failures (layout, type sizes,
    typefaces) sent agents into rebuild loops they could never win."""
    fs = [f for f in res.get("failures", []) if f.get("status") == "fail"]
    if not fs:
        return ""
    if any(f.get("fix_scope") != "content" for f in fs):
        return ("\nFailures marked /renderer or /template come from the engine's own layout; a rebuild with a new "
                "request does not fix them. Do not rebuild for them; mention them to the user if they matter. "
                "Rebuild at most twice for /content failures, then deliver the best version and list what remains.")
    return "\nRebuild at most twice for these, then deliver the best version and list what remains."


def check_workbook(path: str) -> tuple[str, bool]:
    from .validate_xlsx import report_text, validate
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    res = validate(str(p), recompute=_sidecar(p, "recompute"))
    return report_text(res, 30) + _scope_note(res), False


def check_document(path: str) -> tuple[str, bool]:
    from .ooxml_check import check
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    r = check(str(p))
    if r["ok"]:
        if r["schema_validation"] == "run":
            return (f"{p.name}: schema-valid, no problems found in {r['parts_validated']} parts; Office should open it "
                    "without repair."), False
        return (f"{p.name}: the package checks passed (relationships, content types, well-formed XML). Schema "
                "validation did not run (schemas not installed), so Office may still offer to repair it."), False
    lines = [f"{p.name}: {len(r['problems'])} problem(s) that would make Office offer to repair the file:"]
    lines += [f"- {x['part']} ({x['check']}): {x['detail']}" for x in r["problems"][:20]]
    return "\n".join(lines), False


def measure_office_file(path: str, pages: str | None = None) -> tuple[str, bool]:
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    ext = p.suffix.lower()
    if ext in (".pptx", ".potx"):
        from .measure_pptx import measure, public
        try:
            summary = public(measure(str(p)))["summary"]
        except zipfile.BadZipFile:
            return f"{p.name} is not a valid PowerPoint file (the zip container is unreadable).", True
    elif ext in (".xlsx", ".xlsm"):
        from .validate_xlsx import validate
        summary = validate(str(p))["stats"]
    elif ext == ".pdf":
        from .measure_pdf import measure
        summary = measure(str(p), pages)["summary"]
    else:
        return "measure_office_file reads .pptx, .potx, .xlsx, .xlsm or .pdf.", True
    return json.dumps(summary, indent=1, default=str)[:8000], False


def qa_summary(files: list[str], data_files=None, request: str | None = None) -> str:
    """One line per produced deck/workbook, for appending to an engine job's report."""
    lines = []
    for f in files:
        try:
            if f.endswith(".pptx"):
                from .validate_pptx import validate
                r = validate(f, render=False)
                if _p22(f, data_files, request):
                    r["rules"]["P22"] = "fail"
            elif f.endswith(".xlsx"):
                from .validate_xlsx import validate
                r = validate(f)
            elif f.endswith(".docx"):
                from .ooxml_check import check
                d = check(f)
                lines.append(f"- {Path(f).name}: " + ("opens without repair" if d["ok"] else
                             f"{len(d['problems'])} problem(s) Office would repair (run check_document)"))
                continue
            else:
                continue
            bad = [k for k, v in r["rules"].items() if v == "fail"]
            unverified = ""
            if f.endswith(".xlsx"):
                from meshpool_harness.config import find_soffice
                if not find_soffice():
                    unverified = "; values not recalculated: LibreOffice is not installed here, so formula results (errors, checks that do not tie) are unverified. Open the file in Excel to check"
            lines.append(f"- {Path(f).name}: forge rules failing: {', '.join(bad) or 'none'} "
                         f"(run check_deck/check_workbook for the repair list){unverified}")
        except Exception as e:  # QA must never break a finished job
            lines.append(f"- {Path(f).name}: forge QA could not run ({type(e).__name__})")
    return "\n".join(lines)


# What the title says -> the archetypes that prove it (the Zelazny comparison and the diagram triggers in build.py).
LAYOUT_RULES = [
    (r"\brisk|likelihood|probability|exposure|threat", "risk_matrix", "risks ranked by likelihood and impact"),
    (r"priorit|value (?:vs|versus|and) effort|ease|quick win|impact (?:vs|and) effort", "matrix_3x3",
     "items placed on two criteria, three levels each"),
    (r"roadmap|workstream|gantt|\bplan\b|phase|by quarter|\bmonths?\b|milestone", "gantt",
     "a plan over time by workstream"),
    (r"driver|decompos|break(?:s)? (?:down|into)|tree|sub-?question|why ", "issue_tree",
     "a metric broken into drivers, or a question into sub-questions"),
    (r"reports? to|\borg\b|organi[sz]ation|span of control|layers? of management|roles?\b", "org_chart",
     "who reports to whom"),
    (r"cycle|loop|flywheel|reinforc|virtuous|vicious", "cycle", "a loop that feeds itself"),
    (r"\bhub\b|platform|ecosystem|at the cent(?:re|er)|around (?:the|a|one)", "hub_spoke",
     "one centre with the parts that depend on it"),
    (r"funnel|conversion|drop[- ]?off|pipeline|leads? to", "funnel", "drop-off between stages"),
    (r"pyramid|foundation|hierarchy of|build(?:s)? on", "pyramid", "layers that build on each other"),
    (r"grew|fell|rose|declin|trend|over time|since \d{4}|cagr|a year\b|per year", "column_trend",
     "change over time"),
    (r"largest|smallest|rank|leads?\b|lags?\b|vs\.? peers|peer|benchmark|than", "bar_takeaway",
     "a ranking or comparison"),
    (r"bridge|walk|from \$?[\d.]+.* to \$?[\d.]+|because of", "waterfall_bridge", "how one total became another"),
    (r"\bmix\b|share of|composition|split|made up of", "stacked_composition", "parts of a whole"),
    (r"option|choose|recommend|criteria|alternative", "grid", "options against criteria (a grid with a harvey part)"),
    (r"step|process|how (?:it|we|the)|sequence|stages?\b", "grid", "a sequence (a grid with a process part)"),
    (r"issue|challenge|problem|barrier|pain point", "challenge_matrix", "several issues, each with its evidence"),
]


def suggest_layouts(message: str, limit: int = 3) -> list[dict]:
    """Archetypes for a slide's message, best first: [{"archetype", "why"}]. Falls back to the exhibit argument."""
    import re
    out = []
    for pat, arch, why in LAYOUT_RULES:
        if re.search(pat, message or "", re.I) and all(o["archetype"] != arch or o["why"] != why for o in out):
            out.append({"archetype": arch, "why": why})
    if not out:
        out.append({"archetype": "exhibit_argument", "why": "one exhibit and the argument beside it"})
    return out[:limit]


def slide_layouts(message: str, kind: str = "all", show_references: bool | None = None) -> tuple[str, bool]:
    """Read-only: which layouts prove this message, and what strong slides of that kind measure (title words, body
    words, empty space, type sizes) in the reference decks. Pictures of library slides are listed only when the
    knowledge base is on this machine and the user turned on "show references" (MH_SHOW_REFERENCES=1)."""
    import os
    from . import kb
    if not (message or "").strip():
        return "Give the slide's message (its action title or what it must prove).", True
    kind = kind if kind in ("all", "consulting", "financial_services") else "all"
    show = os.environ.get("MH_SHOW_REFERENCES") == "1" if show_references is None else bool(show_references)
    lines = [f"Layouts for: {message.strip()[:160]}"]
    for i, sug in enumerate(suggest_layouts(message), 1):
        pr = kb.priors(sug["archetype"], kind)
        ref = pr["reference"]
        def m(k, d=0):
            v = (ref.get(k) or {}).get("median")
            return f"{v:.{d}f}" if isinstance(v, (int, float)) else "?"
        lines.append(f"{i}. {sug['archetype']}: {sug['why']}.")
        lines.append(f"   Reference pages ({kind}): title {m('title_words')} words, body {m('body_words')} words, "
                     f"largest empty block {m('empty_share', 2)} of the body, {m('n_font_sizes')} type sizes.")
        lib = pr.get("library")
        if lib:
            lines.append(f"   Library: {lib['items']} items of this kind, {lib['golden']} in the golden set"
                         + (f"; common forms: {', '.join(lib['subcategories'][:5])}" if lib.get("subcategories") else ""))
        if show and kb.available():
            hits = kb.search(category=(pr["categories"] or [None])[0], golden=True, limit=3)
            for h in hits:
                t = kb.thumbnail(h["id"])
                if t:
                    lines.append(f"   Reference picture (private, do not put in a deliverable): {t}")
    try:                                                   # the layout vault (engines/forge/vault.py)
        from . import vault
        hits = vault.search(message, limit=4)
    except Exception:                                      # noqa: BLE001 - a broken vault never breaks the tool
        hits = []
    if hits:
        lines.append("Layout notes:")
        lines += [f"   [{h['vault']}] {h['name']}: {h['use_when'][:110]}" for h in hits]
    try:                                                   # built-in layouts (shipped specs, every user has them)
        from . import layouts as LY
        built = LY.search(message, limit=3)
    except Exception:                                      # noqa: BLE001
        built = []
    if built:
        lines.append('Built-in layouts (archetype "layout", "layout": id, "slots": {number: text}):')
        for b in built:
            spec = LY.get(b["id"]) or {}
            sl_ = spec.get("slots") or []
            slots = "; ".join(f"{k}: {role} ({holds[:40]}, <= {mw} words)" for k, (role, holds, mw) in
                              enumerate(sl_[:8], 1)) + (f"; ... {len(sl_)} slots in all" if len(sl_) > 8 else "")
            lines.append(f"   {b['id']}: {b['name']}. Use when: {b['use_when'][:90]}. Slots: {slots}")
    lines.append("Pick the one whose structure IS the message; write the slide in that archetype's fields.")
    return "\n".join(lines), False


S = {"type": "string"}
TOOLS = {
    "check_deck": (check_deck, "Check a .pptx against Meshpool's deck rules (opens without repair, native charts, text "
                   "fit, density, action titles, sourced numbers, alt text). Returns each failure as a fix "
                   "instruction. `data_files` and `request` also check the charts against the data.",
                   {"path": S, "slide_count": {"type": "integer"}, "data_files": {"type": "array", "items": S},
                    "request": S}, ["path"]),
    "check_workbook": (check_workbook, "Check an .xlsx after recalculating it against Meshpool's financial-model rules "
                       "(opens without repair, no hardcodes or plugs, no errors or circular references, consistent "
                       "formulas, checks sheet).", {"path": S}, ["path"]),
    "check_document": (check_document, "Check that a .docx, .pptx or .xlsx opens in Office without a repair prompt; "
                       "returns each problem with its part.", {"path": S}, ["path"]),
    "measure_office_file": (measure_office_file, "Measure how a .pptx, .xlsx or .pdf is built (masters, theme, fonts, "
                            "grid, charts, density; formulas vs constants) to reverse-engineer a reference file.",
                            {"path": S, "pages": S}, ["path"]),
    "slide_layouts": (slide_layouts, "Which slide layouts prove a message, with what strong consulting slides of that "
                      "kind measure. Give the slide's action title; `kind`: all, consulting or financial_services.",
                      {"message": S, "kind": S}, ["message"]),
}
