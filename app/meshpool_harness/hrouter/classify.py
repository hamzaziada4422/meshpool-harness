"""Task classifier for harness selection: which kind of work is this request?

The categories are the route keys MeshPool already uses, at the level that matters when choosing a harness (the
scorecard in defaults/harness_scorecard.json is keyed by them).

  Stage 1  the zero-token signals (route._decide) give a category and a confidence. Most requests end here.
  Stage 2  only when confidence < threshold (default 0.7) or MH_CLASSIFY=always: ONE call to the user's configured
           model (max_tokens 12, temperature 0) with a compact card: each category's definition and a few examples,
           the request and short project signals. The model answers with one category id; anything else, or any
           error, falls back to stage 1.
  Stage 0  optional, offline: with MH_CLASSIFY_LOCAL=1 and `fastembed` importable, nearest neighbour over the embedded
           example requests replaces the model call (no network). fastembed is not a dependency; without it the
           model call is used.

Results are cached per (request, project signals, model). The classifier's tokens are returned so callers can add
them to the task's usage. The examples below are written from the dev suite and generic requests; never copy a
prompt from the held-out eval suite here (tests/test_harness_router.py checks).
"""
from __future__ import annotations

import hashlib
import os
import re
import threading

THRESHOLD = 0.7
MAX_REQUEST_CHARS = 1500

# id -> (one-line definition, [short example requests]). Order is the order of the card.
CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "code.debug": (
        "Something that exists is broken or wrong: a failing test, a crash, a wrong result, a regression.",
        ["Customers sometimes get another customer's price after the first call; find the root cause and fix it.",
         "pytest shows KeyError: 'user_id' in handlers.py, please fix it."]),
    "code.debug.perf": (
        "Correct but too slow or too big: make existing code faster or lighter without changing results.",
        ["The nightly report job times out on 60k rows; make it fast and keep the output identical.",
         "This lookup is quadratic; bring it down so it handles a million items."]),
    "code.build": (
        "Write new code or add a small, clearly specified capability in one place.",
        ["Add a --json flag to the export command.",
         "Write a helper that parses ISO durations like PT1H30M."]),
    "code.build.algorithm": (
        "Implement a function or data structure from precise rules: ordering, ties, edge cases, complexity.",
        ["Implement a leaderboard with competition ranking, ties share a rank, and O(log n) updates.",
         "Merge intervals where open and closed endpoints decide whether touching ranges join."]),
    "code.build.feature": (
        "A behaviour change that spans several files or must keep every existing caller working.",
        ["Users must be soft-deleted; every existing read behaves as if they are gone, add restore and include_deleted.",
         "Add layered settings: defaults, a file, environment variables and the command line, with clear precedence."]),
    "code.refactor": (
        "Restructure existing code without changing behaviour: split, rename, deduplicate, migrate.",
        ["This 50-line function nobody dares to touch: split it into small functions, same behaviour.",
         "Three copy-pasted CSV parsers drift apart; unify them."]),
    "code.test": (
        "Write or extend tests for existing code, or raise coverage.",
        ["Write thorough tests for slugify, including accents and empty input.",
         "Add unit tests for the TTL cache expiry rules."]),
    "code.explain": (
        "Questions about how existing code works, why it is designed that way, or how to use it.",
        ["What does this middleware do and in which order do the hooks run?",
         "Walk me through how the cache invalidation works in this repo."]),
    "code.review": (
        "Review a diff, pull request or file and report problems; do not necessarily change it.",
        ["Review my PR for race conditions and missing error handling.",
         "Look over this module and list security issues."]),
    "code.terminal": (
        "Environment and tooling work: install, configure, deploy, CI, docker, shell, git operations.",
        ["Set up a GitHub Actions workflow that runs the tests on every push.",
         "The docker build fails with permission denied; get it building."]),
    "ambiguous-build": (
        "A very short or underspecified request to add something; the right behaviour must be inferred from the code.",
        ["Add GBP to the money formatter.",
         "Let me set a priority on tasks."]),
    "data": (
        "Answer a question from data files (csv, json, logs, spreadsheets) by computing over them.",
        ["From orders.csv: what is total revenue in USD, using the rates file?",
         "How many distinct users were active per day in events.jsonl?"]),
    "docs": (
        "Produce or edit a document, deck, spreadsheet or report file.",
        ["Turn these notes into a two-page project brief as a docx.",
         "Build a quarterly summary slide deck from this spreadsheet."]),
    "research": (
        "Find and synthesise information from the web or many sources, with citations.",
        ["Compare the three leading vector databases and cite sources.",
         "Give me a landscape report on open-source LLM inference servers."]),
    "browser": (
        "Operate a website or web app: click, fill forms, navigate, extract from pages.",
        ["Open the pricing page and fill in the signup form with the test account.",
         "Log in to the dashboard and download last month's invoice."]),
    "automation": (
        "Set up a recurring or triggered job, schedule or workflow outside the code.",
        ["Every Monday at 9 send me a summary of open pull requests.",
         "Remind me to renew the domain a week before it expires."]),
    "chat": (
        "A question, rewrite or short snippet answerable from knowledge, no files or project needed.",
        ["What is the difference between a mutex and a semaphore?",
         "Rewrite this paragraph to sound friendlier."]),
}
IDS = list(CATEGORIES)

_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()


# ---------- stage 1 -----------------------------------------------------------------------------------------------
def _short_vague(text: str) -> bool:
    return len((text or "").split()) <= 14 and "\n" not in (text or "") and "`" not in (text or "")


def category_of(route, text: str = "") -> tuple[str, float]:
    """The category (and confidence) a stage-1 Route stands for."""
    spec, var, conf = route.spec, route.variant, float(route.confidence)
    if spec == "code.debug":
        return ("code.debug.perf" if var == "perf" else "code.debug"), conf
    if spec == "code.build":
        if var == "algorithm":
            return "code.build.algorithm", conf
        if var == "feature":
            return "code.build.feature", conf
        if _short_vague(text):
            return "ambiguous-build", min(conf, 0.5)       # the kind of request signals cannot judge
        return "code.build", conf
    if spec in CATEGORIES:
        return spec, conf
    return "chat", min(conf, 0.5)                          # "general": no strong signal


def stage1(text: str, files=None, workdir: str | None = None, kind: str = "code"):
    """(category, confidence, Route) from the free signals."""
    from .route import _decide
    r = _decide(text, list(files or []), workdir, kind)
    cat, conf = category_of(r, text)
    return cat, conf, r


# ---------- the card ----------------------------------------------------------------------------------------------
def project_signals(workdir: str | None, route=None) -> str:
    """Short project facts for the card: languages, file count, repo or not."""
    langs, n, repo = [], 0, False
    try:
        from . import signals as S
        repo = bool(S.is_repo(workdir))
        lang = S.project_language(workdir)
        if lang:
            langs.append(lang)
    except Exception:  # noqa: BLE001
        pass
    if route is not None:
        langs += [k for k in ((route.signals or {}).get("languages") or {}) if k not in langs]
    if workdir and os.path.isdir(workdir):
        try:
            for _, dirs, files in os.walk(workdir):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv")]
                n += len(files)
                if n >= 500:
                    break
        except OSError:
            pass
    return (f"languages: {', '.join(langs[:3]) or 'none detected'}; files: {n}{'+' if n >= 500 else ''}; "
            f"git repo: {'yes' if repo else 'no'}")


def card(text: str, signals: str = "") -> str:
    """The prompt: category definitions with examples, project signals, the request."""
    lines = ["Classify the request into exactly one category id. Categories:"]
    for cid, (desc, ex) in CATEGORIES.items():
        lines.append(f"- {cid}: {desc} e.g. " + " | ".join(f'"{e}"' for e in ex))
    lines.append(f"Project: {signals or 'unknown'}")
    lines.append("Request: " + (text or "")[:MAX_REQUEST_CHARS])
    lines.append("Answer with the category id only.")
    return "\n".join(lines)


def parse(answer: str) -> str:
    """The category id in a model answer, or "" when there is none (longest id wins: code.debug.perf over code.debug)."""
    low = (answer or "").strip().lower()
    for cid in sorted(IDS, key=len, reverse=True):
        if re.search(r"(?<![\w.-])" + re.escape(cid) + r"(?![\w-]|\.\w)", low):
            return cid
    return ""


# ---------- stage 0: local embeddings -----------------------------------------------------------------------------
_EMB: dict = {}


def _embedder():
    """embed(list[str]) -> list[vector], or None when fastembed or its model is missing. Patched in tests."""
    if "fn" not in _EMB:
        try:
            from fastembed import TextEmbedding  # type: ignore
            m = TextEmbedding(os.environ.get("MH_CLASSIFY_LOCAL_MODEL", "BAAI/bge-small-en-v1.5"))
            _EMB["fn"] = lambda xs: [list(map(float, v)) for v in m.embed(xs)]
        except Exception:  # noqa: BLE001 - not installed, no model files, offline
            _EMB["fn"] = None
    return _EMB["fn"]


def local_classify(text: str) -> tuple[str, float] | None:
    """(category, cosine similarity) of the nearest example request, None when no embedder is available."""
    emb = _embedder()
    if emb is None:
        return None
    try:
        if "bank" not in _EMB:
            labels = [(cid, e) for cid, (_, ex) in CATEGORIES.items() for e in ex]
            _EMB["bank"] = (labels, emb([e for _, e in labels]))
        labels, vecs = _EMB["bank"]
        q = emb([(text or "")[:MAX_REQUEST_CHARS]])[0]

        def cos(a, b):
            na, nb = sum(x * x for x in a) ** 0.5, sum(x * x for x in b) ** 0.5
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
        best = max(range(len(labels)), key=lambda i: cos(q, vecs[i]))
        return labels[best][0], round(cos(q, vecs[best]), 3)
    except Exception:  # noqa: BLE001
        return None


# ---------- stage 2: one model call -------------------------------------------------------------------------------
def ask_model(text: str, signals: str, client, model: str) -> tuple[str, dict] | None:
    """(category, usage) from one call, or None on any error or an answer that is not a category."""
    try:
        resp = client.chat(model=model, messages=[{"role": "user", "content": card(text, signals)}], max_tokens=12,
                           temperature=0.0, task_id="hclassify", role="router")
        ans = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        u = resp.get("usage") or {}
        usage = {"prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
                 "cached_tokens": int((u.get("prompt_tokens_details") or {}).get("cached_tokens")
                                      or u.get("prompt_cache_hit_tokens") or 0),
                 "completion_tokens": int(u.get("completion_tokens", 0) or 0), "model_calls": 1}
        cid = parse(ans)
        return (cid, usage) if cid else None
    except Exception:  # noqa: BLE001 - classification must never fail a task
        return None


def threshold() -> float:
    try:
        return float(os.environ.get("MH_CLASSIFY_THRESHOLD", THRESHOLD))
    except ValueError:
        return THRESHOLD


def _default_client():
    from meshpool_harness.core.client import LiveClient
    return LiveClient("harness_classify")


def classify(text: str, files=None, workdir: str | None = None, kind: str = "code", client=None,
             model: str | None = None, route=None) -> dict:
    """{category, confidence, stage ("signals"|"local"|"model"), route, usage, cached}. route: a stage-1 Route
    already computed (saves a second pass)."""
    if route is not None:
        cat, conf = category_of(route, text)
    else:
        cat, conf, route = stage1(text, files, workdir, kind)
    out = {"category": cat, "confidence": round(conf, 2), "stage": "signals", "route": route, "usage": {},
           "cached": False}
    if conf >= threshold() and os.environ.get("MH_CLASSIFY", "").lower() != "always":
        return out
    local = os.environ.get("MH_CLASSIFY_LOCAL") == "1"
    sig = project_signals(workdir, route)
    model = model or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    key = hashlib.sha1("\0".join([text or "", sig, model, "local" if local else ""]).encode()).hexdigest()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit:
        return {**out, **hit, "usage": {}, "cached": True}
    got = None
    if local:
        loc = local_classify(text)
        if loc is not None:                          # offline classifier ran: never fall through to the network
            if loc[1] >= threshold():
                got = {"category": loc[0], "confidence": loc[1], "stage": "local"}
            return {**out, **got, "usage": {}} if got else out
    try:
        c = client or _default_client()
    except Exception:  # noqa: BLE001
        c = None
    if c is not None:
        r = ask_model(text, sig, c, model)
        if r:
            got = {"category": r[0], "confidence": 0.9, "stage": "model", "usage": r[1]}
    if got is None:
        return out                                   # fell back to stage 1
    with _LOCK:
        _CACHE[key] = {k: v for k, v in got.items() if k != "usage"}
    return {**out, **got}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
