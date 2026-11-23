"""Router: deterministic rules first; one tiny classification call only when rules are unsure."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..providers import configured_model
from .rules import decide

ENGINES = ["research", "credit", "code", "data", "sheets", "slides", "general"]


@dataclass
class Route:
    engines: list
    confidence: float
    reason: str
    used_model_call: bool


def _classify(request: str, files: list[str], client, model: str | None = None) -> tuple[list[str], float, str]:
    prompt = ("Pick the engine chain for this request. Engines: research (collect and cite outside sources: web, SEC "
              "filings; a sourced report), credit (a lender's cash flow / DSCR / credit memo on a borrower), code (repo/terminal work), data (analysis of data or "
              "text), sheets (build Excel workbooks), slides (build decks), general (anything else). Chains are "
              "ordered, e.g. [\"data\",\"slides\"]. Reply with JSON only: {\"engines\": [...], \"confidence\": 0-1}.\n"
              f"Request: {request}\nAttached files: {', '.join(Path(f).name for f in files) or 'none'}")
    from ..core.client import LiveClient
    kw = {"attempts": 2} if isinstance(client, LiveClient) else {}      # a hint is optional: do not retry for minutes
    resp = client.chat(model=model or configured_model(), messages=[{"role": "user", "content": prompt}], max_tokens=60,
                       temperature=0.0, task_id="router", role="router",
                       extra={"response_format": {"type": "json_object"}}, **kw)
    txt = resp["choices"][0]["message"].get("content") or "{}"
    try:
        d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        eng = [e for e in d.get("engines", []) if e in ENGINES] or ["general"]
        return eng, float(d.get("confidence", 0.5)), "model classification"
    except (ValueError, TypeError):
        return ["general"], 0.3, "model classification unparseable"


def route(request: str, files: list[str] | None = None, output_format: str | None = None, workdir: str | None = None,
          client=None, engine: str | None = None, model: str | None = None, task_id: str = "") -> Route:
    """Pick the engine chain. Nothing is written: the old routing.csv log sat inside the app folder (read-only in the
    signed Mac app), was never read and never got its outcome column filled."""
    files = files or []
    if engine:
        return Route(engine.split(","), 1.0, "override by flag", False)
    eng, conf, why = decide(request, files, output_format, workdir)
    used = False
    if conf < 0.6 and client is not None:
        used = True
        import threading
        box: dict = {}

        def _work():
            try:
                box["r"] = _classify(request, files, client, model)
            except Exception as e:  # noqa: BLE001
                box["e"] = e

        limit = float(os.environ.get("MH_ROUTER_HINT_S", "20"))
        th = threading.Thread(target=_work, daemon=True)
        th.start()
        th.join(timeout=limit)         # the hint is optional: bound its wall-clock, not just its retries
        if th.is_alive():
            return Route(eng, conf, why + f"; routing hint timed out after {limit:g}s", used)
        if "e" in box:                 # the rules already picked an engine
            e = box["e"]
            return Route(eng, conf, why + f"; routing hint failed ({type(e).__name__}: {str(e)[:80]})", used)
        eng2, conf2, why2 = box["r"]
        if conf2 >= 0.5:
            eng, conf, why = eng2, conf2, why2 + f" (rules said {eng} @ {conf})"
        elif conf < 0.5:
            eng, why = ["general"], why + "; low confidence -> general"
    return Route(eng, conf, why, used)
