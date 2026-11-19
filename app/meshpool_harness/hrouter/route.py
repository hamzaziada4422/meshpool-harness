"""Route a request to a harness spec, a language pack, a lane and a backend agent.

Stage 0, signals (signals.py), decides most requests for free. Stage 1, a single JSON-only call on the cheapest model,
runs only when the signals disagree or say nothing and a client is given. The backend agent (Meshpool Code, Pi,
Codex...) is then picked by harness_router from the benchmark table for the spec's category: quality first, then
cost, then time.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from dataclasses import asdict, dataclass, field

from meshpool_harness import pace as PACE

from . import signals as S
from .packs import PACKS
from .specs import SPECS, get

PRIORITY = ["docs", "data", "browser", "automation", "research", "code", "writing"]   # tie-break between families
CONFIDENT = 0.6


@dataclass
class Route:
    spec: str                         # chat, code.debug, research, ...
    family: str                       # chat, code, research, data, docs, browser, automation, general
    sub: str = ""                     # debug, build, ... for code
    variant: str = ""                 # the kind of debug or build job: syntax, test, exception, regression, perf...
    language: str = ""                # a pack id when one applies
    lane: str = "task"                # chat (answer fast), task, deep
    difficulty: str = "normal"        # easy | normal | hard
    base: str = "work"                # built-in profile that runs it: code | work
    confidence: float = 1.0
    reason: str = ""
    used_model_call: bool = False
    signals: dict = field(default_factory=dict)
    ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        """Short text for the chat's route chip: "Debug · Python"."""
        parts = [get(self.spec).name]
        if self.variant:
            parts.append({"perf": "Performance", "cli": "CLI", "api": "API", "ui": "UI"}.get(
                self.variant, self.variant.capitalize()))
        if self.language:
            parts.append({"typescript": "TypeScript", "javascript": "JavaScript", "csharp": "C#", "cpp": "C++"}.get(
                self.language, self.language.capitalize()))
        return " · ".join(parts)


def _decide(text: str, files: list[str], workdir: str | None, kind: str) -> Route:
    files = files or []
    err = S.pasted_error(text)
    langs = S.languages_in(text, files)
    fams = S.families(text, files)
    repo = S.is_repo(workdir)
    pace = PACE.classify(text, bool(files))
    lane = {"quick": "chat", "deep": "deep"}.get(pace, "task")
    sig = {"error": err, "languages": langs, "families": fams, "repo": repo, "pace": pace}

    fam, why, conf = "", "", 0.5
    if err:
        fam, why, conf = "code", f"a pasted {err} error", 0.95
    elif kind != "code" and not files and S.snippet_request(text):
        fam, why, conf, lane = "chat", "a request for a short snippet", 0.85, "chat"
    elif kind == "code":
        other = {k: v for k, v in fams.items() if k not in ("code", "research")}
        if repo or langs or any(S.EXT_LANG.get(Path(f).suffix) for f in files):
            other.pop("docs", None)        # "the sales report is wrong" in a code project is code, not a document
            other.pop("automation", None)  # "the daily job runs at the wrong time": the code's domain, not a schedule
        top = max(other, key=other.get) if other else ""
        if top and other[top] >= 2 and other[top] > fams.get("code", 0) + 1:
            fam, why, conf = top, f"{top} words in a Code chat", 0.7
        else:
            fam, why, conf = "code", "Code mode", 0.8 if (fams.get("code") or langs or repo) else 0.6
    elif fams:
        ranked = sorted(fams, key=lambda f: (-fams[f], PRIORITY.index(f)))
        fam = ranked[0]
        gap = fams[fam] - (fams[ranked[1]] if len(ranked) > 1 else 0)
        conf = min(0.95, 0.6 + 0.1 * fams[fam] + 0.05 * gap)
        why = f"{fam} words ({fams[fam]:g})"
        if fam == "code" and not (langs or repo or files) and lane == "chat":
            fam, why = "chat", "a quick question about code"
    else:
        fam, why, conf = ("chat", "a short question", 0.75) if lane == "chat" else ("general", "no strong signal", 0.45)

    sub, language, var = "", "", ""
    if fam == "code":
        sub, _ = S.code_sub(text, bool(err))
        if lane == "chat" and sub == "build" and not err:
            sub = "explain"
        best = max(langs, key=langs.get) if langs else ""
        language = err or best or (S.project_language(workdir) or "")
        language = language if language in PACKS else ""
        spec = f"code.{sub}"
        var = S.variant(sub, text)
    elif fam in ("chat", "writing"):
        spec, fam = "chat", "chat"
        if lane == "task" and not files:
            lane = "chat"             # rewriting or drafting text needs no agent loop
    elif fam in SPECS:
        spec = fam
    else:
        spec = "general"
    deep_research = bool(re.search(r"\b(sources|citations?|cite|report|landscape|due diligence|literature|deep dive|"
                                   r"compare|comparison|in[- ]depth|thorough)\b", text or "", re.I))
    if lane == "chat" and (spec == "general" or (spec == "research" and not deep_research)):
        spec = "chat"                 # a quick lookup: the chat spec answers and uses one search when needed
    elif lane == "chat" and spec in ("research", "docs", "data", "automation", "browser"):
        lane = "task"                 # a named deliverable or a sourced study is not a quick answer

    n = len((text or "").split())
    difficulty = ("easy" if lane == "chat" or (n <= 25 and not files) else
                  "hard" if lane == "deep" or n > 250 or len(files) > 3 or len([v for v in fams.values() if v]) > 2
                  else "normal")
    return Route(spec=spec, family=fam if fam in ("chat", "code") or fam in SPECS else "general", sub=sub,
                 variant=var if fam == "code" else "",
                 language=language, lane=lane, difficulty=difficulty, base=get(spec).base, confidence=round(conf, 2),
                 reason=why, signals=sig)


def _ask_model(text: str, files: list[str], client, model: str) -> tuple[str, float] | None:
    ids = sorted(SPECS)
    prompt = ("Classify the request into exactly one harness id. Ids: " + ", ".join(ids) + ". code.* is software work "
              "in a project (debug = something is broken, build = new code, refactor, review, test, terminal = "
              "install/config/devops, explain = questions about code). chat = a question or rewrite answerable "
              "without files. Reply with JSON only: {\"id\": \"...\", \"confidence\": 0-1}.\n"
              f"Request: {text[:1500]}\nAttached: {', '.join(files) or 'none'}")
    try:
        resp = client.chat(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=40,
                           temperature=0.0, task_id="router", role="router",
                           extra={"response_format": {"type": "json_object"}})
        txt = resp["choices"][0]["message"].get("content") or "{}"
        d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        sid = str(d.get("id", ""))
        return (sid, float(d.get("confidence", 0.5))) if sid in SPECS else None
    except Exception:  # noqa: BLE001 - routing must never fail a task
        return None


def route(text: str, files: list[str] | None = None, workdir: str | None = None, kind: str = "cowork",
          client=None, model: str | None = None, force: str | None = None) -> Route:
    """The route for a new task. force: a spec id the user picked (e.g. from /debug or the route chip)."""
    t0 = time.perf_counter()
    files = files or []
    if force and force in SPECS:
        r = _decide(text, files, workdir, kind)
        sp = get(force)
        r.spec, r.base, r.confidence, r.reason = force, sp.base, 1.0, "picked by the user"
        r.family = force.split(".")[0]
        r.sub = force.split(".", 1)[1] if "." in force else ""
    else:
        r = _decide(text, files, workdir, kind)
        if r.confidence < CONFIDENT and client is not None:
            got = _ask_model(text, files, client, model or S.env_fast_model() or "deepseek-v4.1-flash")
            r.used_model_call = True
            if got and got[1] >= 0.5:
                sid, c = got
                if sid != r.spec:
                    r.reason = f"model classification (rules said {r.spec})"
                r.spec, r.confidence, r.base = sid, round(c, 2), get(sid).base
                r.family = sid.split(".")[0]
                r.sub = sid.split(".", 1)[1] if "." in sid else ""
    r.ms = round((time.perf_counter() - t0) * 1000, 2)
    return r


def _keys(r: Route) -> list[str]:
    """Spec keys from general to most specific: code.debug, code.debug.perf, code.debug.python, code.debug.perf.python."""
    ks = [r.spec]
    if r.variant:
        ks.append(f"{r.spec}.{r.variant}")
    if r.language:
        ks.append(f"{r.spec}.{r.language}")
        if r.variant:
            ks.append(f"{r.spec}.{r.variant}.{r.language}")
    return ks


def addon(r: Route) -> str:
    """The prompt text a route adds: the spec's method, the sub-subtask's method, the language pack, and any
    data-driven overrides (most specific wins for "addon"; every "addon_extra" adds up)."""
    from .specs import VARIANT_ADDON, overrides
    sp, ov = get(r.spec), overrides()
    base = sp.addon
    for k in _keys(r):
        if ov.get(k, {}).get("addon") is not None:
            base = ov[k]["addon"]
    parts = [base] if base else []
    if r.variant and VARIANT_ADDON.get(f"{r.spec}.{r.variant}"):
        parts.append(VARIANT_ADDON[f"{r.spec}.{r.variant}"])
    if r.language and r.family == "code":
        parts.append(PACKS[r.language].prompt)
    parts += [ov[k]["addon_extra"] for k in _keys(r) if ov.get(k, {}).get("addon_extra")]
    return ("\n\n[Harness: " + r.label + "]\n" + "\n".join(parts)) if parts else ""


def dropped(r: Route) -> set:
    from .specs import overrides
    drop, ov = set(get(r.spec).drop), overrides()
    for k in _keys(r):
        if "drop" in ov.get(k, {}):
            drop = set(ov[k]["drop"])
    return drop


def tools(r: Route) -> list[str]:
    from .specs import overrides
    t, ov = list(get(r.spec).tools), overrides()
    for k in _keys(r):
        if "tools" in ov.get(k, {}):
            t = list(ov[k]["tools"])
    return t
