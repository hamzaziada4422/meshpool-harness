"""meshpool-research: multi-source research with passage-level citations. The model collects sources with the research
library (web, SEC EDGAR, provided files), synthesises a report, and a deterministic verifier gates the finish: every
number and quote must trace to a cited passage, sources must be independent and authoritative enough."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402

SYSTEM = f"""You are a senior research associate (investment banking / strategy consulting). You answer the question
with a sourced report a managing director would forward without edits. Every number and quote traces to a passage
you collected; the engine checks this automatically and rejects the report otherwise.

Work only in the current folder. The research library below is complete and documented here.

Method:
1. Frame. Restate the decision the research serves. Break the question into 3-6 sub-questions that are mutually
   exclusive and together answer it (an issue tree). Write a one-line hypothesis for each.
2. Start from what others already wrote. c.find_reports("<company or industry>") searches for equity research,
   initiating-coverage and industry reports, investor presentations and annual reports (filetype:pdf, "report.pdf").
   Read them for the structure of the problem: which drivers matter, how the market and revenue grow, how costs
   behave, the bull and bear debates, and the figures they rely on. Treat their conclusions as hypotheses: do not
   copy their framing or numbers without checking them.
3. Collect, primary first. For companies: SEC filings (c.edgar_search / c.add_edgar: 10-K, 10-Q, 8-K earnings
   releases, proxies, S-1), investor-relations pages and earnings releases; then regulators and statistics agencies;
   then reputable press and research; trade press last. Run 6-12 varied queries per round (names, synonyms, metrics,
   years, competitors, "site:" for key domains). Read the files you were given with c.add_file.
4. Read and extract. Use c.find(sub_question) for the best passages and c.numbers(metric) to see every figure for a
   metric across sources. Note period, unit, scope (company vs segment, GAAP vs adjusted, calendar vs fiscal year).
5. Verify and triangulate. c.corroborate("S4.p2") checks every figure in a passage against independent sources.
   Every key figure comes from a primary or reputable source (tier 1-2) or two independent sources; the engine
   rejects an Answer whose numbers rest on one tier-3 source (syndicated copies are one source).
   When sources disagree, give the range, say which you rely on and why (primacy, recency, definition).
   For market sizes, build top-down and bottom-up estimates and report the gap.
6. Find what matters. Build the driver tree: market size and growth (volume x price; who buys, why, how much),
   the company's revenue build (segments x units x price, customers x ARPU, capacity x utilisation x price), the
   cost structure (fixed vs variable, unit costs, operating leverage, capex intensity, working capital) and the
   cash conversion. Say which 2-3 drivers explain most of the value or of the change, with evidence; separate
   what is important from what is noise.
7. Second round. Look at what is missing or weak (c.overview() shows tiers and dates) and search again for it.
8. Write report.md (Pyramid principle):
   - Title, then "Answer": the governing thought in 2-4 sentences, then 3-5 key findings, each a full-sentence
     takeaway with numbers.
   - "What drives it": the driver tree and the 2-3 drivers that matter most (market, revenue, costs, cash).
   - One section per sub-question. Each paragraph opens with its takeaway sentence. Tables for comparisons.
   - "Conflicts and open questions": disagreements between sources, figures you could not verify, what to ask
     management or an expert next.
   - Cite at the end of every sentence, bullet or table row that states a fact: [S3.p12] or [S3.p12; S7.p2].
     Write each number as the cited passage states it (rounding is fine: $215.9 billion -> $216bn). A number you
     computed is written with its arithmetic, e.g. "up 64% [calc: 215.9/131.6-1] [S1.p365; S2.p40]", and its inputs
     must be in the cited passages. Quote only verbatim text. Do not state numbers from memory.
   - Dates: say the period each figure covers (FY2026, Q3 2026, as of 2026-11-12).
9. Finish: run R.finalize("report.md", c) (adds the Sources list, saves sources.json, writes report.docx), then
   python -m engines.research.check report.md and fix every problem until it prints PASS. Your final answer is the
   Answer section (governing thought and key findings with citations); list report.md, report.docx and
   sources.json in artifacts and cite the check command as evidence.

Research library (from engines.research import library as R; if python cannot find it, start the script with:
import sys; sys.path.insert(0, r"{ROOT}")). Keep all collection in one script you rerun; the corpus persists in
sources.json between runs, so later runs add to it.
  c = R.Corpus(".")                                     # loads sources.json if present
  c.search_and_read(["query one", "query two", ...], per_query=6, recency="", site="")   # web; recency day|week|
      month|year; returns the new Source objects
  hits = c.edgar_search('"data center revenue"', forms="10-K,10-Q,8-K", start="2025-01-01", ciks="")
  c.add_edgar(hits[0])                                  # a filing or exhibit, tier 1, dated with its filing date
  c.add_url(url); c.add_file("cim.pdf"); c.add_text(title, text, url=None, publisher=None, date=None)
  c.find_reports("Nvidia", extra=["Nvidia data center TAM report pdf"])     # existing research to start from
  c.corroborate("S4.p2"); c.corroborate("$170 billion", "Microsoft capex fiscal 2026")   # independent checks
  print(c.overview())                                   # id, tier, kind, date, passages, duplicates
  for p in c.find("question", k=8, per_source=2, min_tier=3): print(p.cite, p.source.label, p.page, p.text)
  for n in c.numbers("data center revenue", k=20): print(n)    # value, unit, passage id, context
  c.facts(["S1.p365"])                                  # deck facts with source, url, page
  c.assumption_source("S1.p365")                        # Source / Evidence text for a model's assumption book
  c.save(); R.finalize("report.md", c)
Document grid (the same questions across many documents: CIM screening, peer filings, diligence trackers):
  from engines.research import grid as G
  g = G.grid(c, {{"Apple FY26 10-K": ["S1"], "Microsoft FY26 10-K": ["S2"]}}, ["FY2026 revenue", "Capex guidance"])
  G.to_excel(g, c, "grid.xlsx")    # every cell answered only from its row's passages, cited, numbers checked;
                                   # unanswerable cells say "Not found in the documents" (never guessed)
Tiers: 1 filings, regulators, statistics agencies, issuer releases and IR, provided documents; 2 wire services and
financial press, rating agencies, academic and major research institutions; 3 other; 4 forums and user content.
Rely on tiers 1-2 for numbers; never let a number rest only on tier 4."""


def finish_check(answer, artifacts, ws):
    from engines.research.library import Corpus
    from engines.research.verify import check, report_text
    md = [a for a in artifacts or [] if isinstance(a, str) and a.endswith(".md")] or (["report.md"] if (ws.root / "report.md").exists() else [])
    if not md:
        return "ENGINE CHECK: write report.md, run R.finalize and list it in artifacts."
    p = md[0] if os.path.isabs(md[0]) else str(ws.root / md[0])
    if not os.path.exists(p):
        return f"ENGINE CHECK: artifact not found: {md[0]}"
    corpus = Corpus(str(Path(p).parent))
    if corpus.load_error:
        return (f"ENGINE CHECK: sources.json could not be read: {corpus.load_error}. Collect the sources again "
                "(c.search_and_read / c.add_*), then R.finalize.")
    if not corpus.sources:
        return "ENGINE CHECK: no sources.json next to the report; collect sources with the research library first."
    probs = check(Path(p).read_text(encoding="utf-8"), corpus)
    from engines.research.verify import citations_in
    if not citations_in(answer or ""):
        probs.insert(0, {"check": "R0 answer", "where": "final answer",
                         "detail": "the final answer must carry citations like the report's Answer section"})
    if probs:
        return "ENGINE CHECK: research report rejected:\n" + report_text(probs)
    return None


def answer_section(md: str, limit: int = 1500) -> str:
    """The report's "Answer" section (the text under a heading containing "Answer", up to the next heading); the
    first ~1500 characters when there is no such heading."""
    import re
    lines = md.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s{0,3}#{1,6}\s+.*answer", ln, re.I):
            body = []
            for nxt in lines[i + 1:]:
                if re.match(r"^\s{0,3}#{1,6}\s", nxt):
                    break
                body.append(nxt)
            text = "\n".join(body).strip()
            if text:
                return text
    return md.strip()[:limit]


def salvage(r, ws) -> dict:
    """The run ended without an accepted finish (cap, abort, tool-less replies). Do not throw a written report away:
    run the same gate finish uses on report.md. Passing -> ok with the files; failing -> not ok, but with the files
    and the failures; no report -> not ok with an explanation. The answer is never empty."""
    rep = ws.root / "report.md"
    if not rep.exists():
        return {"ok": False, "artifacts": [], "evidence": [],
                "answer": (f"Stopped ({r.reason}) before report.md was written; nothing to deliver. Run again with a "
                           "larger turn budget (MH_RESEARCH_MAX_TURNS) or a narrower question."),
                "reason": f"{r.reason}; no report.md written"}
    arts = [str(ws.root / n) for n in ("report.md", "report.docx", "sources.json") if (ws.root / n).exists()]
    msg = "ENGINE CHECK: report.md is empty"
    try:
        text = rep.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        text = ""
        msg = f"ENGINE CHECK: report.md could not be read: {e}"
    answer = answer_section(text)
    if text:
        try:
            msg = finish_check(answer, ["report.md"], ws)
        except Exception as e:                      # noqa: BLE001 - a crashing checker must not lose the file either
            msg = f"ENGINE CHECK: the checker crashed: {type(e).__name__}: {e}"
    failures = [ln.strip() for ln in (msg or "").splitlines() if ln.strip() and not ln.startswith("ENGINE CHECK")] \
        or ([msg] if msg else [])
    if not msg:
        return {"ok": True, "artifacts": arts, "evidence": ["engine finish check on report.md: pass"],
                "answer": (f"{answer}\n\n(The run stopped ({r.reason}) before finish; the saved report.md passes "
                           "the engine's checks.)"),
                "reason": f"{r.reason}; delivered report.md, checks pass"}
    lines = [f"The run stopped ({r.reason}) before finish, and the saved report.md does not pass the engine's "
             f"checks ({len(failures)} failing):"]
    lines += [f"- {f}" for f in failures[:15]] + ([f"- and {len(failures) - 15} more"] if len(failures) > 15 else [])
    return {"ok": False, "artifacts": arts, "evidence": [f"engine finish check on report.md: {len(failures)} failing"],
            "answer": "\n".join(lines),
            "reason": f"{r.reason}; saved report.md fails {len(failures)} checks"}


def solve(task: str, *, workdir: str, task_id: str, files: list[str] | None = None, max_turns: int | None = None,
          model: str = "deepseek-v4.1-flash", client=None, phase: str = "phase_2_build") -> dict:
    ws = Workspace(workdir, os.path.join(workdir, ".run"))
    if files:
        task += ("\n\nProvided documents (add each with c.add_file; they are primary sources): "
                 + ", ".join(Path(f).name for f in files))
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=SYSTEM,
            caps=Caps(max_turns=max_turns or int(os.environ.get("MH_RESEARCH_MAX_TURNS", "40"))), temperature=0.0,
            finish_check=finish_check)
    out = {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
           "reason": r.reason, "usage": r.usage}
    if not r.ok:
        out.update(salvage(r, ws))
    return out
