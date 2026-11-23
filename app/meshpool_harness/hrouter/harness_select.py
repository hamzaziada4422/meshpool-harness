"""Harness selection: classify the task, then pick the harness the scorecard says solves that kind of task best.

    select(prompt, workdir, model) -> {category, harness, profile, reason, classifier_tokens, stage}

The scorecard (defaults/harness_scorecard.json, fitted by testkit/loop/harness_scorecard.py) holds, per model id (with
"*" as the fallback) and category, one row per harness: solved, tasks, cost_per_task, median_s. Selection:

  1. Only harnesses available in this build (not harnesses.unsupported(), installed) are candidates.
  2. A category with fewer than min_tasks (3) tasks of evidence for a harness ignores that harness and inherits the
     parent category (code.debug.perf -> code.debug -> code); with no evidence anywhere the default is Meshpool's own
     engine with the profile assigned to that route (hrouter/profiles.py).
  3. Highest solve rate wins; harnesses within `tie` tasks of it (default 1) are tied and the cheaper, then faster
     one is taken.

Harness ids are the bench arm names: "meshpool" or "meshpool:<profile>", "pi", "pi-routed", "codex", "codex-routed",
"opencode", "opencode-routed", "hermes", "hermes-routed". "-routed" means the Meshpool harness method rides on the
first message.
"""
from __future__ import annotations

import dataclasses
import json
import os
import hashlib
import uuid
import time
from pathlib import Path

MIN_TASKS = 3
TIE = 1.0
DEFAULT = "meshpool"
ACP = ("codex", "opencode", "hermes")

# category -> (route spec, variant): the key a category has in the profile assignments
SPEC_OF = {"code.debug.perf": ("code.debug", "perf"), "code.build.algorithm": ("code.build", "algorithm"),
           "code.build.feature": ("code.build", "feature"), "ambiguous-build": ("code.build", "")}


def parents(category: str) -> list[str]:
    """code.debug.perf -> [code.debug.perf, code.debug, code]; ambiguous-build -> [ambiguous-build, code.build, code]."""
    cat = "code.build" if category == "ambiguous-build" else category
    parts = cat.split(".")
    chain = [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
    return ([category] + chain) if category == "ambiguous-build" else chain


def base_id(harness: str) -> str:
    """meshpool:lean -> meshpool, pi-routed -> pi."""
    h = harness.split(":", 1)[0]
    return h[:-7] if h.endswith("-routed") else h


# ---------- the scorecard -----------------------------------------------------------------------------------------
def scorecard_paths() -> list[Path]:
    from meshpool_harness import config as C
    ps = [C.APP / "defaults" / "harness_scorecard.json", C.home() / "harness_scorecard.json"]
    if os.environ.get("MH_HARNESS_SCORECARD"):
        ps.append(Path(os.environ["MH_HARNESS_SCORECARD"]))
    return ps


def load_scorecard() -> dict:
    """{model: {category: [rows]}} merged from the shipped file, the data folder and MH_HARNESS_SCORECARD (later
    wins per model and category). Keys starting with "_" are comments."""
    out: dict = {}
    for p in scorecard_paths():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for model, cats in (d.items() if isinstance(d, dict) else []):
            if model.startswith("_") or not isinstance(cats, dict):
                continue
            out.setdefault(model, {}).update({c: rows for c, rows in cats.items() if isinstance(rows, list)})
    return out


def bare_model(model: str | None) -> str:
    if not model:
        return ""
    try:
        from meshpool_harness import providers as PV
        return PV.split(model)[1]
    except Exception:  # noqa: BLE001
        return model


def available() -> set[str]:
    """Base harness ids that can run in this build."""
    from meshpool_harness import harnesses as HR
    ok = {DEFAULT}
    try:
        for h in HR.load_all():
            hid = h.get("id", "")
            if h.get("available") and not h.get("error") and not HR.unsupported(hid):
                ok.add({"meshpool-code": DEFAULT}.get(hid, hid))
    except Exception:  # noqa: BLE001
        pass
    try:
        from meshpool_harness.harnesses import pi_rpc
        if pi_rpc.available():
            ok.add("pi")
    except Exception:  # noqa: BLE001
        pass
    return {h for h in ok if h == DEFAULT or not HR.unsupported(h)}


def pick(card: dict, model: str, category: str, avail: set[str] | None = None, tie: float = TIE,
         min_tasks: int = MIN_TASKS) -> dict | None:
    """The best scorecard row for a category, or None when there is no usable evidence.
    Returns {"harness", "level", "row", "reason"}; level is the category whose evidence was used."""
    avail = avail if avail is not None else available()
    cats = card.get(bare_model(model)) or card.get(model or "") or card.get("*") or {}
    if not cats and "*" in card:
        cats = card["*"]
    for level in parents(category):
        rows = [r for r in cats.get(level, []) if isinstance(r, dict) and base_id(str(r.get("harness", ""))) in avail
                and int(r.get("tasks", 0) or 0) >= min_tasks]
        if not rows:
            continue
        rate = lambda r: r["solved"] / r["tasks"]   # noqa: E731
        best = max(rows, key=rate)
        inf = float("inf")
        tied = [r for r in rows if (rate(best) - rate(r)) <= tie / max(best["tasks"], r["tasks"]) + 1e-9]
        tied.sort(key=lambda r: (r.get("cost_per_task", inf) if r.get("cost_per_task") is not None else inf,
                                 r.get("median_s", inf) if r.get("median_s") is not None else inf, -rate(r)))
        win = tied[0]
        why = (f"{win['harness']} solved {win['solved']}/{win['tasks']} {level} tasks"
               + (f" (best was {best['harness']} {best['solved']}/{best['tasks']}; tied within {tie:g} task, "
                  f"cheaper/faster)" if win is not best else " (best solve rate)")
               + ("" if level == category else f"; inherited from {level}, too little evidence for {category}"))
        return {"harness": str(win["harness"]), "level": level, "row": win, "reason": why}
    return None


# ---------- the router --------------------------------------------------------------------------------------------
def default_profile(category: str, route=None) -> str:
    """The profile Meshpool's own engine runs this category under (profiles.json assignment, else the default)."""
    from . import profiles as PROF
    from .route import Route
    spec, var = SPEC_OF.get(category, (category, ""))
    base = route if route is not None else Route(spec=spec, family=spec.split(".")[0])
    r = dataclasses.replace(base, spec=spec, variant=var or (base.variant if base.spec == spec else ""))
    return PROF.resolve(r)[0]


def select(prompt: str, workdir: str | None = None, model: str | None = None, *, files=None, kind: str = "code",
           client=None, route=None, card: dict | None = None, avail: set[str] | None = None,
           tie: float | None = None, min_tasks: int | None = None) -> dict:
    """The harness for a task. {category, harness, profile, reason, classifier_tokens, stage, confidence}."""
    from . import classify as CL
    t0 = time.time()
    cl = CL.classify(prompt, files=files, workdir=workdir, kind=kind, client=client, model=model, route=route)
    cat = cl["category"]
    card = card if card is not None else load_scorecard()
    got = pick(card, model or os.environ.get("MH_MODEL") or "", cat, avail,
               tie=TIE if tie is None else tie, min_tasks=MIN_TASKS if min_tasks is None else min_tasks)
    if got:
        harness, why = got["harness"], got["reason"]
    else:
        harness, why = DEFAULT, "no scorecard evidence for this category: Meshpool's own harness"
    profile = ""
    if base_id(harness) == DEFAULT:
        profile = harness.split(":", 1)[1] if ":" in harness else default_profile(cat, cl.get("route"))
        harness = f"{DEFAULT}:{profile}" if profile != "full" else DEFAULT
    out = {"category": cat, "harness": harness, "profile": profile,
           "reason": f"{cat} ({cl['stage']}, {cl['confidence']:.2f}): {why}",
           "classifier_tokens": dict(cl.get("usage") or {}), "stage": cl["stage"], "confidence": cl["confidence"],
           "decision_id": uuid.uuid4().hex[:12], "route_ms": round((time.time() - t0) * 1000)}
    log_decision(out, prompt, model)
    return out


# ---------- observability: every routing decision and its outcome, on this computer only ------------------------------
def _log_path():
    from meshpool_harness import config as C
    return C.home() / "routing-log.jsonl"


def _append(row: dict) -> None:
    """One JSON line in <data folder>/routing-log.jsonl. Never the request text (only its hash and length), never a
    key; MH_ROUTING_LOG=0 turns it off. A log failure never affects routing."""
    if os.environ.get("MH_ROUTING_LOG", "1") == "0":
        return
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def log_decision(d: dict, prompt: str, model: str | None) -> None:
    _append({"type": "decision", "id": d["decision_id"], "t": time.time(), "model": model or os.environ.get("MH_MODEL", ""),
             "prompt_sha": hashlib.sha256((prompt or "").encode()).hexdigest()[:16], "prompt_len": len(prompt or ""),
             **{k: d[k] for k in ("category", "harness", "profile", "stage", "confidence", "route_ms",
                                  "classifier_tokens")}})


def log_outcome(decision_id: str, *, ok: bool | None, secs: float, usage: dict | None = None,
                error: str | None = None) -> None:
    """The result of the run a decision chose: finished or failed, time, tokens. With the decision, this is what a
    scorecard can later be refitted from (opt-in)."""
    if decision_id:
        _append({"type": "outcome", "id": decision_id, "t": time.time(), "ok": ok, "secs": round(secs, 1),
                 "usage": {k: (usage or {}).get(k, 0) for k in ("prompt_tokens", "cached_tokens", "completion_tokens",
                                                               "model_calls")},
                 "error": (error or "")[:200] or None})


def enabled() -> bool:
    return os.environ.get("MH_HARNESS_ROUTER") == "1"
