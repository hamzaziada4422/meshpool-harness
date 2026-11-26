"""Meshpool Auto: route a new task to the harness that does that kind of work best.

The routing table is defaults/routing.json (a copy in the Meshpool data folder overrides it). Each category has
keywords and, per harness, a benchmark quality score (0-100) and, where measured, the cost and time per solved task.

Auto never trades away quality: it takes the best quality among the harnesses that are installed and can run on the
chosen provider, treats every harness within `quality_band` points of it as equally good (one task in twenty is 5
points, inside run-to-run noise), and among those picks the cheapest, then the fastest. No model call is made, so
routing is instant, free and repeatable.
"""
from __future__ import annotations

import json
import os
import re

from meshpool_harness import config as C

DEFAULT_BAND = 5.0


def table() -> dict:
    for p in (C.home() / "routing.json", C.APP / "defaults" / "routing.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d.get("categories"), dict):
                return d
        except (OSError, ValueError):
            continue
    return {"categories": {"general": {"keywords": [], "scores": {"meshpool-code": 1}}}}


def classify(text: str, t: dict | None = None) -> tuple[str, list[str]]:
    """The category whose keywords match best. A phrase of several words counts once per word, so "pull request"
    outweighs a lone generic word; a tie goes to the category whose first match comes earliest in the text."""
    t = t or table()
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    best, best_key, hits_best = "general", (0, 0), []
    for cat, c in t["categories"].items():
        hits, weight, first = [], 0, len(low)
        for k in c.get("keywords") or []:
            k = k.strip().lower()
            m = k and re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", low)
            if m:
                hits.append(k)
                weight += len(k.split())
                first = min(first, m.start())
        key = (weight, -first)
        if weight and key > best_key:
            best, best_key, hits_best = cat, key, hits
    return best, hits_best


def _ready(kind: str) -> dict:
    """Harnesses Auto may pick: installed, set up on a provider, and not a subscription the user kept for itself."""
    from meshpool_harness import harnesses as HR
    # A harness on a ChatGPT sign-in spends the user's own plan. Auto uses that plan freely when ChatGPT is the
    # provider the user chose, and otherwise only after the user allowed it.
    subs_ok = (C.read().get("MH_AUTO_SUBSCRIPTIONS") or os.environ.get("MH_AUTO_SUBSCRIPTIONS", "0")) == "1"
    chosen = HR.provider()
    try:                                            # agents whose process could not start lately (hrouter/health.py)
        from meshpool_harness.hrouter import health
        down = health.broken()
    except Exception:  # noqa: BLE001
        down = {}
    # A chat's mode decides its agents: a Code chat is never routed to Work or Hermes, nor a Work chat to a code agent.
    return {h["id"]: h for h in HR.load_all()
            if h.get("available") and not h.get("error") and h.get("type") != "auto" and h.get("ready", True)
            and (kind not in ("code", "cowork") or h.get("mode", "any") in (kind, "any")) and h["id"] not in down
            and (not h.get("plan") or h["plan"] == chosen or subs_ok)}


def office_engine(text: str, kind: str, ready: dict) -> str:
    """meshpool's own agent for a request whose main input or output is an office file (office_intent.office_work):
    meshpool Work in a Work chat, meshpool Code in a Code chat (it has the same office tools; a Code chat is never
    moved to Work, S-04). "" for any other request, or when that agent cannot run."""
    try:
        from meshpool_harness.office_intent import office_work
        if not office_work(text):
            return ""
    except Exception:  # noqa: BLE001
        return ""
    own = "meshpool-code" if kind == "code" else "meshpool-work"
    return own if own in ready else ""


def _money(v: float) -> str:
    return f"${v:.4f}".rstrip("0").rstrip(".") if v < 0.1 else f"${v:.2f}"


def measured(t: dict, cat: str, model: str | None) -> dict:
    """The loop's measurements for this model and category (testkit/loop/routing_fit.py): harness -> {score,
    cost_usd, seconds, n}, only entries with enough tasks to trust."""
    if not model:
        return {}
    from meshpool_harness import providers as PV
    bare = PV.split(model)[1]
    m = ((t.get("measured") or {}).get(bare) or {}).get(cat) or {}
    need = int(t.get("measured_min_n", 10))
    return {h: v for h, v in m.items() if isinstance(v, dict) and v.get("n", 0) >= need}


def route(text: str, kind: str = "cowork", category: str | None = None, model: str | None = None) -> dict:
    """{harness, name, category, reason, score, ranking} for a task. category: already decided (hrouter's spec).
    model: the chat's model; where the loop measured harnesses on it, those numbers replace the hand-set ones."""
    from meshpool_harness import harnesses as HR
    t = table()
    cat, hits = classify(text, t)
    if category and category in t["categories"]:
        cat = category
    c = dict(t["categories"].get(cat) or t["categories"].get("general") or {})
    meas = measured(t, cat, model or os.environ.get("MH_MODEL") or C.read().get("MH_MODEL"))
    if meas:                                    # measured on this model: those numbers win, per harness
        c["scores"] = {**(c.get("scores") or {}), **{h: v["score"] for h, v in meas.items()}}
        c["cost_usd"] = {**(c.get("cost_usd") or {}), **{h: v["cost_usd"] for h, v in meas.items() if "cost_usd" in v}}
        c["seconds"] = {**(c.get("seconds") or {}), **{h: v["seconds"] for h, v in meas.items() if "seconds" in v}}
    quality = {h: float(s) for h, s in (c.get("scores") or {}).items()}
    for hid, bonus in ((t.get("mode_bonus") or {}).get(kind) or {}).items():
        if hid in quality:                      # a harness with no score in a category is never picked for it
            quality[hid] += bonus
    cost, secs = c.get("cost_usd") or {}, c.get("seconds") or {}
    ready = _ready(kind)
    own = office_engine(text, kind, ready)
    if own:
        # Priya, round 3: "make rooms-summary.xlsx" on Auto went to Codex twice (a 6-minute hang, then a raw command
        # as the answer); meshpool Work did it in 27 s. Office work goes to meshpool's own agent, whatever else is
        # installed; an outside agent still runs it when the user picks one.
        rest = [h for h in sorted(quality, key=lambda h: -quality[h]) if h in ready and h != own]
        return {"harness": own, "name": ready[own]["name"], "category": cat, "score": quality.get(own),
                "reason": "Office files (Word, Excel, PowerPoint, PDF) go to meshpool's own agent, which makes and "
                          "checks them.",
                "ranking": [{"harness": h, "name": ready[h]["name"], "score": quality.get(h)}
                            for h in [own, *rest][:5]]}
    scored = {h: q for h, q in quality.items() if h in ready}
    if not scored:
        hid = HR.default_for(kind if kind in HR.MODES else "cowork")
        _nm = ready.get(hid, {}).get("name") or next((d.get("name") for d in HR.load_all() if d.get("id") == hid), hid)
        return {"harness": hid, "name": _nm, "category": cat,
                "reason": "No ranked agent is ready, so the mode's default is used.", "ranking": []}
    best = max(scored.values())
    band = float(t.get("quality_band", DEFAULT_BAND))
    inf = float("inf")
    order = lambda h: (cost.get(h, inf), secs.get(h, inf), -scored[h])   # noqa: E731
    pool = sorted((h for h, q in scored.items() if q >= best - band), key=order)
    hid = pool[0]
    why = f"a {cat} task" + (f" ({', '.join(hits[:3])})" if hits else "") + (
        f", measured on this model ({sum(v['n'] for v in meas.values())} runs)" if meas else "")
    if len(pool) == 1 or hid not in cost:
        reason = f"Best benchmark quality for {why}."
    else:
        top = max(pool, key=lambda h: scored[h])
        rest = [h for h in pool if h != hid and h in cost]
        reason = (f"Top benchmark quality for {why}" if scored[hid] >= scored[top] else
                  f"Benchmark quality within {band:g} points of the best for {why}")
        reason += f", at the lowest cost ({_money(cost[hid])} per solved task"
        if rest:
            reason += f" vs {_money(min(cost[h] for h in rest))}"
        reason += ")."
    ranking = sorted(scored, key=lambda h: (-(scored[h] >= best - band), order(h)))
    return {"harness": hid, "name": ready[hid]["name"], "category": cat, "score": scored[hid], "reason": reason,
            "ranking": [{"harness": h, "name": ready[h]["name"], "score": scored[h],
                         **({"cost_usd": cost[h]} if h in cost else {}), **({"seconds": secs[h]} if h in secs else {})}
                        for h in ranking[:5]]}
