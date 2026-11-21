"""Best of N: build a deck N times and keep the one a vision model grades best, on the same rubric the blind reviews
use (clarity, navigation, polish, human feel, readability, density, overall). The writer is not deterministic: the
same brief scored 5.6 to 7.4 across builds in blind reviews (2026-11-21), so choosing among a few builds lifts the
floor of what the user receives. Off by default (cost: N builds and one grading call each): MH_BEST_OF=N or
`python -m engines.forge.build ... --best-of N`.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

RUBRIC = """You are a senior consulting partner. Grade this slide deck 1-10 on: clarity (each slide's message obvious in 5
seconds), navigation (you can tell where you are in the story), polish (alignment, spacing, type hierarchy, no dead
zones), human_feel (looks made by an experienced consultant), readability, density (enough relevant evidence per slide
without clutter), overall. Be strict. Return ONLY JSON {"clarity": n, "navigation": n, "polish": n, "human_feel": n,
"readability": n, "density": n, "overall": n, "worst": "the single biggest weakness, one line"}."""
KEYS = ("clarity", "navigation", "polish", "human_feel", "readability", "density", "overall")


def judge(pptx: str, client, model: str | None = None, task_id: str = "select") -> dict:
    """{"mean": float, scores..., "worst"} for one deck (mean 0.0 when grading fails)."""
    from engines.forge.review import CHAIN, image_part, render_pngs
    pages = render_pngs(pptx, width_px=1100)
    content = [{"type": "text", "text": "The deck, one image per slide in order:"}] + [image_part(p) for _, p in pages]
    for m in [model] + CHAIN if model else CHAIN:
        if not m:
            continue
        try:
            r = client.chat(model=m, messages=[{"role": "system", "content": RUBRIC},
                                               {"role": "user", "content": content}],
                            max_tokens=600, temperature=0.0, task_id=task_id, role="verifier")
            txt = r["choices"][0]["message"].get("content") or ""
            d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            sc = {k: float(d[k]) for k in KEYS if isinstance(d.get(k), (int, float))}
            if len(sc) >= 5:
                return dict(sc, mean=round(sum(sc.values()) / len(sc), 2), worst=str(d.get("worst") or "")[:200],
                            model=m)
        except Exception:  # noqa: BLE001 - the next model in the chain
            continue
    return {"mean": 0.0, "worst": "not graded"}


def best_of(brief: str, out_dir: str, n: int, build_fn, client=None, **kw) -> dict:
    """Build n candidates into out_dir/.candidates/k, grade each, copy the best to out_dir. Returns the winning build's
    result with "select": {"scores": [...], "chosen": k}."""
    out = Path(out_dir)
    cand_root = out / ".candidates"                          # hidden: the user's folder shows the chosen deck
    cand_root.mkdir(parents=True, exist_ok=True)
    if client is None:
        from meshpool_harness.core.client import LiveClient
        client = LiveClient("forge_select")
    results, scores = [], []
    for k in range(max(1, n)):
        d = cand_root / str(k + 1)
        r = build_fn(brief, str(d), client=client, task_id=f"forge-cand{k + 1}", **kw)
        results.append(r)
        sc = judge(r["pptx"], client, task_id=f"select-{k + 1}") if r.get("pptx") and Path(r["pptx"]).exists() \
            else {"mean": 0.0, "worst": "no deck"}
        scores.append(sc)
    best = max(range(len(results)), key=lambda i: scores[i].get("mean", 0.0))
    src = cand_root / str(best + 1)
    for f in src.iterdir():                                  # the winner's files are the build's files
        if f.is_file():
            shutil.copy2(f, out / f.name)
    win = dict(results[best])
    win["pptx"] = str(out / Path(results[best]["pptx"]).name)
    win["select"] = {"scores": scores, "chosen": best + 1}
    (out / "select.json").write_text(json.dumps(win["select"], indent=1))
    return win


def wanted() -> int:
    try:
        return max(1, int(os.environ.get("MH_BEST_OF") or 1))
    except ValueError:
        return 1

