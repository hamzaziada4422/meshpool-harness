"""Renderer-only evaluation of Forge on the frozen forge_kb briefs (app/tests/fixtures/forge_kb_briefs).

The slide JSON of each brief is fixed, so two runs on different engine versions differ only by the renderer, the
rules and the style pack, never by the content model. No model calls, no network.

    cd app && python ../testkit/forge_kb_eval.py OUT_DIR [--split dev|test|all] [--style NAME] [--no-png]

For every brief: render -> validate (P0-P20 with the LibreOffice render) -> ooxml_check -> page PNGs and a contact
sheet. Writes OUT_DIR/summary.json and prints one line per deck. Decks, PNGs and contact sheets stay in OUT_DIR,
outside the repo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
FIX = APP / "tests" / "fixtures" / "forge_kb_briefs"
PLACEHOLDER = re.compile(r"(?i)lorem ipsum|click to (add|edit)|\[(insert|tbd|xx)\b|\bxx+\b|\bTBD\b")


def briefs(split: str) -> list[Path]:
    sp = json.loads((FIX / "split.json").read_text())
    ids = sp["dev"] + sp["test"] if split == "all" else sp[split]
    return [d for i in ids for d in FIX.iterdir() if d.is_dir() and d.name.split("_")[0] == str(i).split("_")[0]]


def pngs(pptx: Path, out: Path) -> list[Path]:
    """The deck as page PNGs (LibreOffice -> PDF -> pypdfium2) and one contact sheet."""
    import pypdfium2 as pdfium
    from PIL import Image
    from engines.forge.common import soffice_convert
    pdf = soffice_convert(str(pptx), "pdf", str(out), 240)
    doc = pdfium.PdfDocument(str(pdf))
    files = []
    for i in range(len(doc)):
        im = doc[i].render(scale=1.5).to_pil()
        f = out / f"page_{i + 1:02d}.png"
        im.save(f)
        files.append(f)
    if files:
        tw, th, cols = 480, 270, 3
        rows = (len(files) + cols - 1) // cols
        sheet = Image.new("RGB", (tw * cols, th * rows), "white")
        for k, f in enumerate(files):
            im = Image.open(f)
            im.thumbnail((tw - 8, th - 8))
            sheet.paste(im, ((k % cols) * tw + 4, (k // cols) * th + 4))
        sheet.save(out / "contact_sheet.png")
    return files


def placeholders(pptx: Path) -> list[str]:
    from pptx import Presentation
    hits = []
    for n, sl in enumerate(Presentation(str(pptx)).slides, 1):
        for sh in sl.shapes:
            if sh.has_text_frame and PLACEHOLDER.search(sh.text_frame.text):
                hits.append(f"slide {n}: {sh.text_frame.text[:60]!r}")
    return hits


def run_one(d: Path, out: Path, style: str | None, png: bool) -> dict:
    from engines.forge.build import bookends
    from engines.forge.ooxml_check import check as ooxml
    from engines.forge.render import render
    from engines.forge.validate_pptx import load_rules, validate
    slides = json.loads((d / "slides.json").read_text())
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    brief = (d / "brief.md").read_text()
    density = meta.get("density", "balanced")
    out.mkdir(parents=True, exist_ok=True)
    pptx = out / "deck.pptx"
    kw = {}
    if style:
        from engines.forge import styles
        kw = styles.render_kwargs(style)
    t0 = time.time()
    r = render(json.loads(json.dumps(slides)), str(pptx), carry=False, density=density, **kw)
    t_render = time.time() - t0
    prov = json.loads(Path(r["provenance"]).read_text())
    rules = load_rules()
    res = validate(str(pptx), rules, brief={"slide_count": len(slides), **bookends(brief)}, provenance=prov,
                   storyline={"nodes": [{"id": s["node"]} for s in slides if s.get("node")]}, render=True,
                   density=density)
    try:
        ox = ooxml(str(pptx))
        ox_ok = bool(ox["ok"]) and ox.get("schema_validation") == "run"
    except Exception as e:                                   # noqa: BLE001 - reported, never fatal
        ox, ox_ok = {"error": str(e)}, None
    st = res["rules"]
    fails = [f for f in res["failures"] if f.get("status", "fail") == "fail"]
    by_rule: dict[str, int] = {}
    for f in fails:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    shares = [x for x in (res["stats"].get("empty_share_by_slide") or []) if isinstance(x, (int, float))]
    overflow = sum(1 for f in fails if f["rule"] in ("P1", "P5") and re.search(r"(?i)overflow|outside|runs off|off the slide", f["message"]))
    files = pngs(pptx, out) if png else []
    rec = {"id": d.name, "slides": len(slides), "render_s": round(t_render, 1),
           "render_errors": r.get("errors", []), "passed": sum(1 for v in st.values() if v == "pass"),
           "failed": sorted(k for k, v in st.items() if v == "fail"),
           "not_run": sorted(k for k, v in st.items() if v == "not_run"), "failures_by_rule": by_rule,
           "p20_empty_share_max": round(max(shares), 3) if shares else None,
           "p20_empty_share_mean": round(sum(shares) / len(shares), 3) if shares else None,
           "overflow_failures": overflow, "placeholders": placeholders(pptx), "ooxml_clean": ox_ok,
           "pages": len(files), "dir": str(out)}
    (out / "qa.json").write_text(json.dumps({"record": rec, "failures": res["failures"], "stats": res["stats"]},
                                            indent=1, default=str))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--style")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    recs = []
    for d in briefs(a.split):
        rec = run_one(d, out / d.name, a.style, not a.no_png)
        recs.append(rec)
        print(f"{rec['id']:<28} pass {rec['passed']:>2}  fail {','.join(rec['failed']) or '-':<24} "
              f"P20 max {rec['p20_empty_share_max']}  overflow {rec['overflow_failures']}  ooxml {rec['ooxml_clean']}",
              flush=True)
    n = len(recs)
    agg = {"decks": n, "split": a.split, "style": a.style,
           "all_rules_pass_first_time": sum(1 for r in recs if not r["failed"]),
           "fail_counts": {}, "p20_max_median": None, "overflow_total": sum(r["overflow_failures"] for r in recs),
           "placeholders_total": sum(len(r["placeholders"]) for r in recs),
           "ooxml_clean": sum(1 for r in recs if r["ooxml_clean"])}
    for r in recs:
        for k, v in r["failures_by_rule"].items():
            agg["fail_counts"][k] = agg["fail_counts"].get(k, 0) + v
    mx = sorted(r["p20_empty_share_max"] for r in recs if r["p20_empty_share_max"] is not None)
    agg["p20_max_median"] = mx[len(mx) // 2] if mx else None
    (out / "summary.json").write_text(json.dumps({"aggregate": agg, "decks": recs}, indent=1))
    print(json.dumps(agg))


if __name__ == "__main__":
    main()
