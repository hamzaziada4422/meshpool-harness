"""The layout vault: linked Markdown notes (Obsidian style) that let a model find the right slide layout fast.

Two vaults, the same note shape (YAML front matter, a few plain sections, [[wiki links]]):

- Public, in the repo: docs/deck-engine/vault/. One note per Forge archetype and grid part (what message it proves,
  how it is built, its fields, its word budget, related layouts), one map of contents per family, and index.md.
  Written from ARCHETYPES below by `python -m engines.forge.vault public`.
- Private, in the knowledge base: $MESHPOOL_KB/vault/. One note per usable library item (its name, when to use it,
  its structure, its text slots and what goes in each, its framework, the Forge archetype that draws it, similar
  items), one map of contents per category, and index.md. Written from the knowledge base and the layout labels
  (labels.sqlite, table `layouts`) by `python -m engines.forge.vault private`. Never committed.

`search(text)` ranks notes of both vaults for a slide's message; the slide_layouts tool uses it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[3] / "docs" / "deck-engine" / "vault"

# Forge's own layouts: (family, use when the title says..., how it is built, fields, related)
ARCHETYPES = {
    "executive_summary": ("story", "the whole answer, right after the cover",
                          "3-5 numbered findings with one support line each, metrics panel, decision line",
                          "points [{lead, bullets}], metrics [{value, label}], decision", ["closing", "agenda"]),
    "agenda": ("story", "the sections the deck will cover", "3-6 items, the current one highlighted on trackers",
               "items", ["executive_summary", "closing"]),
    "closing": ("story", "what to do next, who and when", "numbered steps with action, detail, owner and timing",
                "steps [{action, detail, milestones, owner, timing}]", ["gantt", "executive_summary"]),
    "bar_takeaway": ("chart", "X is larger or smaller than Y", "one bar chart on two thirds, takeaway and bullets",
                     "chart, takeaway, bullets", ["benchmark", "column_trend", "table_focus"]),
    "column_trend": ("chart", "X grew or fell over time", "column chart with the trend, takeaway and bullets",
                     "chart, takeaway, bullets", ["line_trend", "waterfall_bridge"]),
    "line_trend": ("chart", "X diverged or recovered", "line chart, up to 4 series, takeaway and bullets",
                   "chart, takeaway, bullets", ["column_trend"]),
    "stacked_composition": ("chart", "the mix shifted", "stacked or 100% columns, takeaway and bullets",
                            "chart, takeaway, bullets", ["waterfall_bridge", "column_trend"]),
    "waterfall_bridge": ("chart", "A became B because of these steps", "a native waterfall, takeaway and bullets",
                         "chart (start, steps, end), takeaway, bullets", ["stacked_composition", "issue_tree"]),
    "benchmark": ("chart", "how peers compare on 1-3 metrics", "bars per metric in one order, median line",
                  "benchmark {companies, panels, highlight}", ["bar_takeaway", "table_focus"]),
    "table_focus": ("table", "one row stands out", "native table, one highlighted row, bullets beside a short one",
                    "table {columns, rows, highlight_rows}, takeaway, bullets", ["benchmark", "matrix_3x3"]),
    "challenge_matrix": ("text", "several issues, each with its evidence", "numbered rows with an icon and two lenses",
                         "columns, rows [{icon, label, cells [{lead, bullets}]}]", ["risk_matrix", "issue_tree"]),
    "exhibit_argument": ("chart", "one exhibit and the argument for it", "exhibit left, argument blocks right",
                         "chart, blocks [{text, bullets}], callout", ["bar_takeaway", "grid"]),
    "grid": ("parts", "the point needs two to four views", "parts on a grid: chart, table, stats, text, callout, "
             "process, timeline, matrix2x2, harvey, quote and every diagram", "layout, look, parts [{type, ...}]",
             ["exhibit_argument", "matrix_3x3", "funnel"]),
    "risk_matrix": ("diagram", "these risks matter most", "3x3 heat grid, likelihood across, impact up",
                    "items [{label, x, y}], takeaway, bullets", ["matrix_3x3", "challenge_matrix"]),
    "matrix_3x3": ("diagram", "items fall into nine zones", "3x3 grid on two named criteria, zone names",
                   "items [{label, x, y}], x_axis, y_axis, cells", ["risk_matrix", "grid"]),
    "gantt": ("diagram", "the plan runs in these workstreams", "label column, periods, bars, milestone diamonds",
              "periods, rows [{label, start, end, group, milestones}]", ["closing", "cycle"]),
    "issue_tree": ("diagram", "the metric breaks down into these drivers", "left-to-right tree, up to 3 levels",
                   "root, branches [{label, value, children}]", ["waterfall_bridge", "org_chart"]),
    "org_chart": ("diagram", "this is who owns what", "top-down tree, up to 3 levels",
                  "root {label, role}, children [{label, role, children}]", ["issue_tree", "hub_spoke"]),
    "cycle": ("diagram", "each step feeds the next", "3-6 nodes on a loop with arrows",
              "steps [{title, text}], center", ["hub_spoke", "gantt"]),
    "hub_spoke": ("diagram", "everything depends on one centre", "hub and 3-8 spokes",
                  "hub, spokes [{title, text}]", ["cycle", "org_chart"]),
    "pyramid": ("diagram", "each layer builds on the one below", "3-5 stacked layers, text beside each",
                "levels [{title, text}]", ["funnel", "issue_tree"]),
    "funnel": ("diagram", "most drop out between these stages", "3-6 narrowing bars, values and conversion",
               "stages [{label, value, text}]", ["pyramid", "bar_takeaway"]),
    "cases": ("diagram", "named companies already prove it", "a row per company: its logo, a big figure, one line",
              "rows [{company, value, text}]", ["benchmark", "grid"]),
}
# labels that name a grid part or a bookend rather than an archetype note -> the note that draws it
ALIAS = {"process_steps": "grid", "kpi_tiles": "grid", "quote": "grid", "timeline_roadmap": "gantt",
         "cover": "executive_summary", "section_divider": "agenda"}
FAMILIES = {"story": "Story and bookends", "chart": "Charts", "table": "Tables", "text": "Text matrices",
            "parts": "Grids of parts", "diagram": "Diagrams"}


def _front(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(json.dumps(x) for x in v)}]")
        else:
            lines.append(f"{k}: {json.dumps(v)}")
    return "\n".join(lines + ["---", ""])


def build_public(dest: Path | None = None) -> int:
    """Write the public vault (Forge's own layouts). Returns the number of notes."""
    from .validate_pptx import load_rules
    dest = Path(dest or PUBLIC)
    dest.mkdir(parents=True, exist_ok=True)
    caps = load_rules()["pptx"]["density"]
    bal = (caps.get("modes") or {}).get("balanced", {}).get("by_archetype", {})
    n = 0
    for name, (fam, use, structure, fields, related) in ARCHETYPES.items():
        words = bal.get(name) or caps["by_archetype"].get(name)
        body = [f"# {name}", "", f"Use when the title says: {use}.", "", f"Built as: {structure}.", "",
                f"Fields: {fields}.", ""]
        if words:
            body += [f"Body words (balanced): up to {words}.", ""]
        body += ["Related: " + ", ".join(f"[[{r}]]" for r in related), "", f"Family: [[{fam}]]", ""]
        (dest / f"{name}.md").write_text(_front({"type": "forge-archetype", "name": name, "family": fam,
                                                 "use_when": use, "related": related}) + "\n".join(body))
        n += 1
    for fam, title in FAMILIES.items():
        names = [a for a, v in ARCHETYPES.items() if v[0] == fam]
        (dest / f"{fam}.md").write_text(_front({"type": "map", "family": fam}) + f"# {title}\n\n" +
                                        "\n".join(f"- [[{a}]]: {ARCHETYPES[a][1]}" for a in names) + "\n")
    (dest / "index.md").write_text(
        "# Forge layout vault\n\nOne note per layout Forge draws natively, grouped by family. Pick the layout whose "
        "structure is the slide's message; the slide_layouts tool searches these notes.\n\n" +
        "\n".join(f"- [[{f}]]: {t}" for f, t in FAMILIES.items()) + "\n")
    return n


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "item"


def store_layouts(records: list[dict]) -> int:
    """Keep the layout labels (name, use_when, structure, slots, ...) in labels.sqlite, table `layouts`."""
    from . import kb
    con = kb.connect(write=True)
    if con is None:
        return 0
    try:
        con.execute("CREATE TABLE IF NOT EXISTS lab.layouts(id TEXT PRIMARY KEY, data TEXT)")
        n = 0
        for r in records:
            if r.get("id"):
                con.execute("INSERT OR REPLACE INTO lab.layouts VALUES (?,?)", (r["id"], json.dumps(r)))
                n += 1
        con.commit()
        return n
    finally:
        con.close()


def build_private(similar_k: int = 3) -> int:
    """Write the private vault into $MESHPOOL_KB/vault from the layout labels. Returns the number of item notes."""
    from . import kb
    r = kb.root()
    if r is None:
        return 0
    con = kb.connect(write=True)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS lab.layouts(id TEXT PRIMARY KEY, data TEXT)")
        recs = {row[0]: json.loads(row[1]) for row in con.execute("SELECT id, data FROM lab.layouts")}
    finally:
        con.close()
    out = r / "vault"
    (out / "items").mkdir(parents=True, exist_ok=True)
    names = {}
    by_cat: dict[str, list] = {}
    for iid, rec in recs.items():
        it = kb.item(iid) or {}
        names[iid] = f"{_slug(rec.get('name'))}--{iid[-24:]}"
        by_cat.setdefault(it.get("category") or "other", []).append(iid)
    for iid, rec in recs.items():
        it = kb.item(iid) or {}
        cat = it.get("category") or "other"
        sim = [s for s, _ in kb.similar(iid, k=80) if s in names][:similar_k]   # nearest among the labelled items
        slots = "\n".join(f"- slot {sl.get('k')} ({sl.get('role')}, up to {sl.get('max_words')} words): "
                          f"{sl.get('holds')}" for sl in rec.get("slots") or [])
        arch = rec.get("forge_archetype") or ""
        arch = arch if arch in ARCHETYPES else ALIAS.get(arch, "grid" if arch else "")
        body = [f"# {rec.get('name') or iid}", "", f"Use when: {rec.get('use_when', '')}", "",
                f"Structure: {rec.get('structure', '')}", ""]
        if rec.get("framework"):
            body += [f"Framework: {rec['framework']}", ""]
        body += ["Slots:", slots or "- none", "",
                 f"Drawn natively by: [[{arch}]]" if arch else "", "",
                 "Similar: " + (", ".join(f"[[{names[s]}]]" for s in sim) or "none"), "",
                 f"Category: [[{cat}]]", "", f"Thumbnail: {it.get('thumb') or ''}", ""]
        front = {"type": "library-item", "id": iid, "category": cat, "subcategory": it.get("subcategory") or "",
                 "quality": it.get("quality"), "golden": it.get("golden") is not None, "tier": it.get("tier"),
                 "density": rec.get("density", ""), "framework": rec.get("framework", ""),
                 "forge_archetype": arch, "pairs_with": rec.get("pairs_with") or [],
                 "aspect": round((it.get("w_in") or 0) / (it.get("h_in") or 1), 2)}
        (out / "items" / f"{names[iid]}.md").write_text(_front(front) + "\n".join(body))
    for cat, ids in by_cat.items():
        ids.sort(key=lambda i: (-((kb.item(i) or {}).get("quality") or 0), i))
        (out / f"{cat}.md").write_text(_front({"type": "map", "category": cat}) + f"# {cat.replace('_', ' ')}\n\n" +
                                       "\n".join(f"- [[{names[i]}]]: {recs[i].get('use_when', '')}" for i in ids) +
                                       "\n")
    (out / "index.md").write_text("# Slide library vault (private)\n\nOne note per usable library item, by category. "
                                  "Private study material: never copy a note's text or an item into the repo.\n\n" +
                                  "\n".join(f"- [[{c}]] ({len(i)})" for c, i in sorted(by_cat.items())) + "\n")
    return len(recs)


def _notes(folder: Path) -> list[tuple[Path, str]]:
    return [(p, p.read_text()) for p in sorted(folder.rglob("*.md"))] if folder.is_dir() else []


_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "by", "with", "this", "that"}


def search(text: str, limit: int = 5, private: bool = True) -> list[dict]:
    """Notes ranked for a message: [{"vault": "public"|"private", "note", "name", "use_when", "score"}]. Matches
    words against the note (front matter and body); maps of contents and indexes are skipped."""
    from . import kb
    words = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 2]
    if not words:
        return []
    pools = [("public", PUBLIC)]
    if private and kb.available():
        pools.append(("private", kb.root() / "vault" / "items"))
    out = []
    for vault, folder in pools:
        for p, t in _notes(folder):
            if 'type: "map"' in t or p.name == "index.md":
                continue
            low = t.lower()
            use = re.search(r"(?:Use when(?: the title says)?|When to use): (.*)", t)
            head = re.search(r"^# (.*)", t, re.M)
            u, h = (use.group(1).lower() if use else ""), (head.group(1).lower() if head else "")
            score = sum(3 if re.search(rf"\b{w}", u) else 2 if re.search(rf"\b{w}", h) else
                        1 if re.search(rf"\b{w}", low) else 0 for w in words)
            score = round(score / (1 + 0.15 * len(u.split())) * 4, 1) if score else 0   # long notes match by chance
            if score:
                out.append({"vault": vault, "note": str(p),
                            "name": head.group(1).strip() if head and p.parent.name == "layouts" else p.stem,
                            "use_when": use.group(1).strip() if use else "", "score": score})
    out.sort(key=lambda x: (-x["score"], x["vault"] != "public", x["name"]))
    return out[:limit]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m engines.forge.vault")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("public")
    pv = sub.add_parser("private")
    pv.add_argument("--labels", nargs="*", default=[], help="layout label JSONL files to store first")
    sr = sub.add_parser("search")
    sr.add_argument("text")
    a = ap.parse_args(argv)
    if a.cmd == "public":
        print("notes", build_public())
    elif a.cmd == "private":
        if a.labels:
            print("labels", store_layouts([json.loads(x) for f in a.labels for x in open(f) if x.strip()]))
        print("notes", build_private())
    else:
        for x in search(a.text):
            print(f"{x['score']:>3} {x['vault']:<8} {x['name']:<50} {x['use_when'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
