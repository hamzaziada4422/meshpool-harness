"""meshpool-credit: lender cash flow and credit analysis as a fixed pipeline, not an agent loop.

At most two short model calls, so it runs on a small local model (7-8B) as well as a large cloud one:
  1. fill the loan request fields the regex parser could not find (JSON, temperature 0);
  2. optional commentary (borrower, risks, mitigants) from the computed facts; every number it writes is checked
     against those facts and sentences that fail are dropped. MH_CREDIT_COMMENTARY=0 skips it; a large model
     (MH_CREDIT_DEPTH=full) gets a longer brief.
Everything else (spreading, cash flow measures, capacity, covenants, stress, workbook, memo) is code.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engines.credit import analysis as A  # noqa: E402

AMOUNT = re.compile(r"(?P<cur>[$\u20ac\u00a3]|\b(?:usd|eur|gbp)\s?)?\s?"
                    r"(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
                    r"(?P<sc>k|m|mm|mn|million|bn|b|billion)?\b", re.I)
LOAN_WORDS = re.compile(r"(?i)\b(?:loan|facility|term loan|borrow\w*|financing|credit line|line of credit|revolver|"
                        r"request\w*|refinanc\w*|lend\w*|advance|mortgage|capex line)\b")
FIN_WORDS = re.compile(r"(?i)\b(?:revenue|revenues|sales|ebitda|income|assets|profit|turnover|cash|equity|debt)\b")
NOT_TICKERS = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "SOFR", "LIBOR", "EURIBOR", "LLC", "LP", "LLP",
               "LTD", "INC", "PLC", "CORP", "CO", "SA", "AG", "NV", "BV", "GMBH", "TTM", "LTM", "EBITDA", "DSCR",
               "FCCR", "TNW", "LTV", "ABL", "RCF", "TL", "USA", "US", "UK", "EU", "CEO", "CFO", "FY", "YOY"}
SCALE = {"k": 1e-3, "m": 1, "mm": 1, "mn": 1, "million": 1, "bn": 1e3, "b": 1e3, "billion": 1e3}


def _amount(text: str):
    """The loan amount in millions: the figure sitting next to loan/facility words, never revenue-type figures."""
    best, best_score, n_cand = None, None, 0
    for m in AMOUNT.finditer(text):
        sc = (m.group("sc") or "").lower()
        if not m.group("cur") and sc not in ("m", "mm", "mn", "million", "bn", "b", "billion"):
            continue                                       # a bare number is not an amount ("7 years", "2024")
        n_cand += 1
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 30]
        score = 0
        if LOAN_WORDS.search(before) or LOAN_WORDS.search(after):
            score += 2
        tail = re.sub(r"^\s*(?:(?:of|in|from|annual|net|total|operating|gross|annual)\s+)+", "", after, flags=re.I)
        if FIN_WORDS.match(tail):
            score -= 3                                     # "$50m of revenue", "$5m in sales"
        if best_score is None or score > best_score:
            best, best_score = m, score
    if best is None or (best_score <= 0 and n_cand > 1):
        return None                                        # ambiguous: do not guess which figure is the loan
    v, sc = float(best.group("n").replace(",", "")), (best.group("sc") or "").lower()
    return v * SCALE.get(sc, 1e-6)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
# a percentage is the interest rate only when rate words sit next to it ("7% interest", "at 6.5%", "rate of 8%"),
# never when it is a loan-to-value, an advance rate or a share ("80% LTV", "100% of debt")
RATE_AFTER = re.compile(r"\s*(?:per\s+(?:year|annum)\s+|p\.?a\.?\s+|annual\s+|all[- ]in\s+)?"
                        r"(?:interest|rate\b|coupon|fixed|floating|sofr|apr\b|per\s+annum|p\.?a\.?(?:\s|$|[.,;]))")
RATE_BEFORE = re.compile(r"(?:interest(?:\s+rate)?|rate|coupon|pricing|priced|apr|\bat)\s*(?:of|at|:|=|is)?\s*(?:a\s+)?"
                         r"(?:fixed\s+|floating\s+)?$")
NOT_RATE = re.compile(r"\s*(?:ltv|loan[- ]to[- ]value|ltc|loan[- ]to[- ]cost|of\b|advance|equity|down|owner|"
                      r"stake|ownership|margin|growth|share)")
YEARS = r"(\d+(?:\.\d+)?)[- ]?(?:year|yr)s?\b"


def _rate(t: str) -> float | None:
    for m in PCT.finditer(t):
        after, before = t[m.end():m.end() + 30], t[max(0, m.start() - 30):m.start()]
        if NOT_RATE.match(after):
            continue
        if RATE_AFTER.match(after) or RATE_BEFORE.search(before):
            return float(m.group(1)) / 100
    return None


def parse_request(text: str) -> dict:
    """Loan fields from plain words. Amounts are returned in $m."""
    out: dict = {}
    t = text.lower()
    amt = _amount(text)
    if amt is not None:
        out["amount"] = amt
    r = _rate(t)
    if r is not None:
        out["rate"] = r
    # amortisation is stated on its own ("20-year amortization", "amortizing over 15 years"); a term is the maturity
    am = re.search(YEARS + r"\s*(?:amorti[sz]ation|amorti[sz]ing|amort\b)", t) or \
        re.search(r"amorti[sz](?:ation|ed|ing)\s*(?:schedule\s*)?(?:of|over|period of|:)?\s*" + YEARS, t)
    term = re.search(YEARS + r"\s*(?:term|maturity|tenor|loan term)\b", t) or \
        re.search(r"(?:term|maturity|tenor)\s*(?:of|:)?\s*" + YEARS, t)
    if am:
        out["amort_years"] = float(am.group(1))
        if term:
            out["term_years"] = float(term.group(1))        # maturity before the end of amortisation: a balloon
    elif term:
        out["amort_years"] = float(term.group(1))           # no separate amortisation: fully amortising over the term
    else:
        a = re.search(YEARS, t)
        if a:
            out["amort_years"] = float(a.group(1))
    if re.search(r"\brefinanc", t):
        out["refinance_existing"] = True
    if re.search(r"\bfixed[- ]rate\b|\bfixed[- ]interest\b", t):
        out["floating"] = False
    tk = re.search(r"(?:\b[Tt]icker|\bNYSE|\bNASDAQ|\bNasdaq)\s*[:=]?\s*\(?([A-Z]{1,5})\)?\b", text)
    if not tk:                                      # "Acme Corp (ACME)": but not currencies, rates or entity suffixes
        for pm in re.finditer(r"\(([A-Z]{1,5})\)", text):
            if pm.group(1) not in NOT_TICKERS:
                tk = pm
                break
    if tk:
        out["ticker"] = tk.group(1)
    p = re.search(r"\bto (fund|finance|acquire|buy|build|refinance|expand)\b([^.,;(\n]{0,60})", t)   # one line:
    # the CLI appends "\n\nInput files ..." to the request, which ended up in the memo's purpose
    if p:
        out["purpose"] = (p.group(1) + p.group(2)).strip()
    return out


ASK = ("Extract the loan request as JSON with keys: borrower (company name), ticker (or null), amount_musd (number, "
       "in millions of US dollars, or null), rate (decimal, e.g. 0.07, or null), amort_years (number or null), "
       "refinance_existing (true/false), purpose (short text or null). Reply with JSON only.\nRequest: ")


def _llm_json(client, model: str, prompt: str, max_tokens: int = 200) -> dict:
    resp = client.chat(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens,
                       temperature=0.0, task_id="credit", role="worker",
                       extra={"response_format": {"type": "json_object"}})
    txt = resp["choices"][0]["message"].get("content") or "{}"
    try:
        return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except ValueError:
        return {}


def commentary(client, model: str, res: dict, borrower_text: str = "") -> dict:
    from engines.credit.memo import check_commentary, facts
    fs = facts(res)
    full = os.environ.get("MH_CREDIT_DEPTH", "short") == "full"
    n = "3-5" if full else "2-3"
    prompt = (f"You are a bank credit analyst. Using ONLY these facts, write JSON with keys business (one or two "
              f"sentences on what the borrower does and how it earns cash), risks (list of {n} short sentences) and "
              f"mitigants (list of {n} short sentences). Use no number that is not in the facts.\nFacts:\n- "
              + "\n- ".join(fs) + (f"\nAbout the borrower: {borrower_text[:1500]}" if borrower_text else ""))
    d = _llm_json(client, model, prompt, 700 if full else 350)
    out = {}
    if isinstance(d.get("business"), str):
        out["business"] = check_commentary(d["business"], fs)
    for k in ("risks", "mitigants"):
        if isinstance(d.get(k), list):
            out[k] = [y for y in (check_commentary(str(x), fs) for x in d[k][:6]) if y]
    return out


def solve(task: str, *, workdir: str, task_id: str, files: list[str] | None = None, client=None,
          model: str = "deepseek-v4.1-flash", **_) -> dict:
    t0 = time.time()
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    req = parse_request(task)
    if client is not None and not ({"amount", "rate", "amort_years"} <= req.keys()):
        try:
            d = _llm_json(client, model, ASK + task[:3000])
            for k_in, k_out in (("amount_musd", "amount"), ("rate", "rate"), ("amort_years", "amort_years"),
                                ("ticker", "ticker"), ("purpose", "purpose"), ("borrower", "borrower")):
                if d.get(k_in) not in (None, "") and k_out not in req:
                    req[k_out] = d[k_in]
            if d.get("refinance_existing") is True:
                req["refinance_existing"] = True
        except Exception:                                      # noqa: BLE001 - the regex result stands
            pass
    tables = [f for f in files or [] if Path(f).suffix.lower() in (".xlsx", ".xlsm", ".csv")]
    if tables:
        fin = A.from_table(tables[0], name=req.get("borrower"))
    elif req.get("ticker"):
        from engines.sheets import spreading as S
        try:
            fin = A.from_spread(S.spread(S.fetch_companyfacts(S.cik_for_ticker(req["ticker"]),
                                                              cache_dir=str(wd / ".edgar")), 3))
        except KeyError as e:
            return {"ok": False, "answer": "", "artifacts": [], "turns": 0, "usage": {},
                    "reason": f"{e.args[0] if e.args else e}. Attach the borrower's financial statements (xlsx or "
                              "csv) or give the public company's exact ticker."}
    else:
        return {"ok": False, "answer": "", "artifacts": [], "turns": 0, "usage": {},
                "reason": "Attach the borrower's financial statements (xlsx or csv: line items down, years across) "
                          "or give a public company's ticker, e.g. 'ticker: ACME'."}
    try:
        amount = float(req.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return {"ok": False, "answer": "", "artifacts": [], "turns": 0, "usage": {},
                "reason": "State the loan amount, e.g. 'a $10m term loan'; I will not assume one."}
    defaulted = tuple(k for k in ("rate", "amort_years") if not req.get(k))
    loan = A.Loan(amount=amount, defaulted=defaulted, rate=float(req.get("rate") or 0.07),
                  amort_years=float(req.get("amort_years") or 7), term_years=req.get("term_years"),
                  floating=req.get("floating", True),
                  refinance_existing=bool(req.get("refinance_existing")), purpose=req.get("purpose") or "")
    pol = A.Policy()
    if A.ebitda(fin, fin.years[-1]) is None:
        return {"ok": False, "answer": "", "artifacts": [], "turns": 0, "usage": {},
                "reason": f"The statements for {fin.years[-1]} have no operating income (or pretax income) row, so "
                          "EBITDA cannot be computed. Add one of them and run again."}
    res = A.analyse(fin, loan, pol)
    from engines.credit import memo, workbook
    stem = re.sub(r"[^A-Za-z0-9]+", "_", fin.name).strip("_")[:40] or "borrower"
    xl = str(wd / f"credit_{stem}.xlsx")
    workbook.build(fin, loan, pol, xl, date=time.strftime("%Y-%m-%d"))
    com = {}
    if client is not None and os.environ.get("MH_CREDIT_COMMENTARY", "1") == "1":
        try:
            com = commentary(client, model, res)
        except Exception:                                      # noqa: BLE001 - the memo stands without it
            com = {}
    md = str(wd / f"credit_memo_{stem}.md")
    memo.write(res, fin, md, com)
    (wd / f"credit_{stem}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    rec, why = memo.recommendation(res)
    pf, cap = res["pro_forma"], res["capacity"]
    answer = (f"{rec}: {why} Pro forma DSCR {memo._x(pf['dscr'])} (minimum {pol.min_dscr:.2f}x), funded debt / "
              f"EBITDA {memo._x(pf['leverage'])} (maximum {pol.max_leverage:.2f}x), debt capacity "
              f"{memo.money(cap['max_loan'], fin.unit)} vs {memo.money(cap['requested'], fin.unit)} requested.")
    notes = []
    if defaulted:
        names = {"rate": "interest rate 7%", "amort_years": "7-year amortisation"}
        notes.append("Assumed because the request did not state them: " + ", ".join(names[k] for k in defaulted) + ".")
    if fin.v(fin.years[-1], "da") is None:
        notes.append("No depreciation and amortisation row: EBITDA equals operating income.")
    if notes:
        answer += " " + " ".join(notes)
    arts = [xl, md, str(Path(md).with_suffix(".docx")), str(wd / f"credit_{stem}.json")]
    return {"ok": True, "answer": answer, "artifacts": [a for a in arts if os.path.exists(a)],
            "evidence": [f"computed in code in {time.time() - t0:.1f}s"], "turns": 1, "reason": "", "usage": {}}
