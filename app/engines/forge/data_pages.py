"""Chart pages computed from the user's own data files, and the check that a deck carries them (rule P22).

A request like "revenue by quarter for each region" over quarterly.csv used to reach the writing model as loose text
rows; the model drew one chart with one series and nothing compared it with the file. Here the pivot is computed in
code (deterministic, no model call), becomes a fixed chart page like the finance pages (finance_pages.py), and
check_data reads every native chart of the finished deck and fails when none holds the pivot's categories, series
and values.

    pages, facts, expectations = pages(request, [csv, xlsx, ...])
    failures = check_data(pptx_path, expectations)           # [] when one chart holds each expected pivot

ask() reads the request (measure, category axis, series dimension), pivot() computes it, tables() reads the files.
Everything here is best effort and never raises: a file or request this cannot read gives no page, not an error.
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

from .common import failure as F

PERIODS = ("day", "week", "month", "quarter", "year")
SERIES_MARK = r"for each|for every|in each|in every|each|per"          # "for each region": the series dimension
AXIS_MARK = r"by|over the|over|across the|across"                        # "by quarter": the category axis
MARK = rf"(?P<mark>{SERIES_MARK}|{AXIS_MARK}|and)"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MAX_SERIES, MAX_CATEGORIES = 8, 24                                      # a wider pivot is not a readable chart


def _sing(w: str) -> str:
    w = w.lower().strip()
    return w[:-3] + "y" if w.endswith("ies") else w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def _label(col) -> str:
    return re.sub(r"[_\s]+", " ", str(col)).strip().lower()


def _namepat(col) -> str:
    """The column's name as it appears in a request: any spacing, singular or plural."""
    return r"[_\s]+".join(re.escape(_sing(w)) for w in _label(col).split()) + r"(?:s|es)?"


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip())
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ reading files
def _frame(path: Path):
    import pandas as pd
    if path.suffix.lower() == ".csv":
        for enc in ("utf-8-sig", "latin-1"):
            try:
                df = pd.read_csv(path, sep=None, engine="python", encoding=enc)
                break
            except Exception:                                  # noqa: BLE001 - the next encoding, then nothing
                df = None
        if df is None:
            return None
    else:
        raw = pd.read_excel(path, sheet_name=0, header=None)
        raw = raw.dropna(how="all").dropna(axis=1, how="all")
        hdr = next((i for i in range(min(len(raw), 15))
                    if sum(isinstance(v, str) for v in raw.iloc[i]) >= max(2, int(0.6 * raw.shape[1]))), None)
        if hdr is None:
            return None
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr]]
        df = df.reset_index(drop=True).infer_objects()
    df = df.dropna(how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df if len(df) >= 2 and df.shape[1] >= 2 else None


def tables(paths) -> list[tuple[str, object]]:
    """[(file name, DataFrame)] for the .csv and simple .xlsx inputs (the first sheet with a header row)."""
    out = []
    for p in paths or []:
        p = Path(str(p))
        try:
            if p.suffix.lower() in (".csv", ".xlsx", ".xlsm") and p.is_file() and not p.name.startswith("~$"):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    df = _frame(p)
                if df is not None:
                    out.append((p.name, df))
        except Exception:                                      # noqa: BLE001 - an unreadable file gives no page
            continue
    return out


# ------------------------------------------------------------------------------------------------ the request
def _date_col(df) -> str | None:
    """The column that holds dates (a datetime column, or text that parses as dates), for 'by month' on a date."""
    import pandas as pd
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            return c
        if not pd.api.types.is_numeric_dtype(s) and re.search(r"date|day|time|period|when", _label(c)):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ok = pd.to_datetime(s, errors="coerce")
            if ok.notna().mean() >= 0.9:
                return c
    return None


def _usable_dim(df, col) -> bool:
    import pandas as pd
    s = df[col]
    if pd.api.types.is_bool_dtype(s):
        return False
    if pd.api.types.is_numeric_dtype(s):                       # a numeric column is an axis only when it is one:
        return bool(re.fullmatch(r"year|quarter|month|week|day|period", _label(col))) and s.nunique() <= 31
    return s.nunique() >= 2


def _mentions(text: str, df) -> list[dict]:
    """Every dimension the request names, in order: {pos, dim, mark} (mark: series | axis). dim is ("col", name) or
    ("period", word, date column): 'month' on a table with a date column and no month column."""
    dc = _date_col(df)
    found = []
    cands = [(("col", c), _namepat(c)) for c in df.columns if _usable_dim(df, c)]
    have = {_sing(_label(c)) for c in df.columns}
    if dc is not None:
        cands += [(("period", w, dc), re.escape(w) + r"(?:s)?") for w in PERIODS if w not in have]
    for dim, pat in cands:
        for m in re.finditer(rf"\b{MARK}\s+(?:the\s+|all\s+|its\s+|their\s+)?(?:{pat})\b", text):
            found.append({"pos": m.start(), "dim": dim, "mark": m.group("mark")})
    found.sort(key=lambda x: x["pos"])
    out, seen = [], set()
    for f in found:
        if f["dim"] in seen:
            continue
        seen.add(f["dim"])
        mark = f["mark"]
        if mark == "and":                                       # "by region and quarter": the by-group's second
            prev = next((o for o in reversed(out) if f["pos"] - o["pos"] < 60), None)
            mark = prev["mark"] if prev else "by"
        f["mark"] = "series" if re.fullmatch(SERIES_MARK, mark) else "axis"
        out.append(f)
    return out


def _timey(dim, df) -> bool:
    import pandas as pd
    if dim[0] == "period":
        return True
    return bool(re.search(r"year|quarter|month|week|day|date|period|time", _label(dim[1]))) or \
        pd.api.types.is_datetime64_any_dtype(df[dim[1]])


def _measure(text: str, df, dims: set) -> dict | None:
    import pandas as pd
    cands = []
    for c in df.columns:
        if ("col", c) in dims or not pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c]):
            continue
        m = re.search(rf"\b{_namepat(c)}\b", text)
        if m:
            cands.append((m.start(), {"measure": _label(c), "column": c}))
    names = {_label(c) for c in df.columns}
    cols = {_sing(_label(c)).replace(" ", "_"): c for c in df.columns}
    price = next((cols[k] for k in ("unit_price", "price", "price_per_unit", "unit_cost") if k in cols
                  and k != "unit_cost"), None)
    units = next((cols[k] for k in ("unit", "quantity", "qty") if k in cols), None)
    m = re.search(r"\b(revenue|sales)\b", text)
    if m and m.group(1) not in names and price is not None and units is not None and units != price:
        cands.append((m.start(), {"measure": m.group(1), "derive": [units, price]}))
    return min(cands, key=lambda x: x[0])[1] if cands else None


def ask(request: str, df) -> dict | None:
    """What the request asks of this table: {"measure", "category", "series" or None, "agg"}, or None when it asks
    nothing this can compute. category and series are ("col", name) or ("period", word, date column)."""
    try:
        text = (request or "").lower()
        mentions = _mentions(text, df)
        if not mentions:
            return None
        meas = _measure(text, df, {m["dim"] for m in mentions})
        if not meas:
            return None
        used = set(meas.get("derive") or [meas.get("column")])
        mentions = [m for m in mentions if m["dim"][0] == "period" or m["dim"][1] not in used]
        if not mentions:
            return None
        cat = ser = None
        if len(mentions) == 1:
            cat = mentions[0]["dim"]
        else:
            a, b = mentions[0], mentions[1]
            axis = [m for m in (a, b) if m["mark"] == "axis"]
            series = [m for m in (a, b) if m["mark"] == "series"]
            if len(axis) == 1:                                   # "by quarter for each region", "per region by month"
                cat, ser = axis[0]["dim"], series[0]["dim"]
            else:                                                # both marked alike: time on the axis, else in order
                ta, tb = _timey(a["dim"], df), _timey(b["dim"], df)
                first, second = (b, a) if (tb and not ta) else (a, b)
                cat, ser = first["dim"], second["dim"]
        agg = "mean" if re.search(r"\b(average|avg|mean)\b", text) else "sum"
        spec = dict(meas, category=cat, series=ser, agg=agg)
        spec["measure"] = ("average " if agg == "mean" else "") + spec["measure"]
        return spec
    except Exception:                                          # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------ the pivot
def _period_key(ts, word: str):
    """(sort key, label) of a timestamp at the period the request names."""
    if word == "year":
        return ts.year, str(ts.year)
    if word == "quarter":
        return (ts.year, (ts.month - 1) // 3), f"Q{(ts.month - 1) // 3 + 1} {ts.year}"
    if word == "month":
        return (ts.year, ts.month), f"{ts.strftime('%b')} {ts.year}"
    if word == "week":
        iso = ts.isocalendar()
        return (iso[0], iso[1]), f"{iso[0]}-W{iso[1]:02d}"
    return ts.toordinal(), ts.strftime("%Y-%m-%d")


def _natural(labels: list[str]):
    """A sort key for labels with a natural order (Q1..Q4, Jan..Dec, 2024, 'Q1 2025'), else None."""
    keys = []
    for s in labels:
        t = str(s).strip()
        if m := re.fullmatch(r"(?i)q([1-4])(?:\s*['-]?\s*((?:19|20)?\d\d))?", t):
            keys.append((int(m.group(2) or 0), int(m.group(1))))
        elif m := re.fullmatch(r"(?i)((?:19|20)\d\d)\s*[- ]?q([1-4])", t):
            keys.append((int(m.group(1)), int(m.group(2))))
        elif m := re.fullmatch(r"(?i)([a-z]{3})[a-z]*(?:\s+((?:19|20)\d\d))?", t):
            if m.group(1).lower() not in MONTHS:
                return None
            keys.append((int(m.group(2) or 0), MONTHS.index(m.group(1).lower())))
        elif (n := _num(t)) is not None:
            keys.append((n, 0))
        else:
            return None
    return keys


def _axis(df, dim):
    """(labels per row, ordered labels, time-like) for a dimension."""
    import pandas as pd
    if dim[0] == "period":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ts = pd.to_datetime(df[dim[2]], errors="coerce")
        pairs = [None if pd.isna(t) else _period_key(t, dim[1]) for t in ts]
        order = sorted({p for p in pairs if p})
        return [p[1] if p else None for p in pairs], [p[1] for p in order], True
    col = df[dim[1]]
    if pd.api.types.is_datetime64_any_dtype(col):
        pairs = [None if pd.isna(t) else _period_key(t, "day") for t in col]
        order = sorted({p for p in pairs if p})
        return [p[1] if p else None for p in pairs], [p[1] for p in order], True
    labels = [None if pd.isna(v) else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v)) for v in col]
    first = list(dict.fromkeys(l for l in labels if l is not None))
    keys = _natural(first)
    if keys is not None:
        return labels, [l for _, l in sorted(zip(keys, first), key=lambda x: x[0])], True
    return labels, first, _timey(dim, df)


def _values(df, spec):
    import pandas as pd
    if spec.get("derive"):
        u, p = (pd.to_numeric(df[c].map(_num), errors="coerce") for c in spec["derive"])
        return u * p
    return pd.to_numeric(df[spec["column"]].map(_num), errors="coerce")


def pivot(df, spec: dict) -> dict:
    """{"categories": [...], "series": [{"name", "values"}]}: the measure summed (or averaged) per category and
    series, in the file's order (chronological for periods, natural for Q1..Q4)."""
    val = _values(df, spec)
    cats_of, cat_order, _ = _axis(df, spec["category"])
    if spec.get("series"):
        ser_of, ser_order, _ = _axis(df, spec["series"])
        ser_order = ser_order if _axis(df, spec["series"])[2] else list(dict.fromkeys(s for s in ser_of if s))
    else:
        ser_of, ser_order = [None] * len(df), [None]
    cell: dict = {}
    for c, s, v in zip(cats_of, ser_of, val):
        if c is not None and v == v:
            cell.setdefault((c, s), []).append(float(v))
    red = (lambda xs: sum(xs) / len(xs)) if spec.get("agg") == "mean" else sum
    name = spec["measure"][:1].upper() + spec["measure"][1:]
    out = []
    for s in ser_order:
        vals = []
        for c in cat_order:
            v = round(red(cell[(c, s)]), 2) if (c, s) in cell else 0
            vals.append(int(v) if float(v).is_integer() else v)
        out.append({"name": s if s is not None else name, "values": vals})
    return {"categories": list(cat_order), "series": out}


# ------------------------------------------------------------------------------------------------ the page
def _n(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.1f}" if abs(v) >= 100 else f"{v:,.2f}"


def _dim_word(dim) -> str:
    return _sing(dim[1]) if dim[0] == "period" else _label(dim[1])


def _words(s: str) -> int:
    return len(s.split())


def _compose(spec, pv, fid_total, new_fact) -> tuple[str, list[str], list[str]]:
    """(title, bullet texts, fact ids): every number in them is a fact of the page."""
    cats, ser = pv["categories"], pv["series"]
    meas = spec["measure"]
    cw, sw = _dim_word(spec["category"]), _dim_word(spec["series"]) if spec.get("series") else None
    ordered_c = _axis_ordered(spec["category"])
    tot = {s["name"]: sum(s["values"]) for s in ser}
    c0, cn = cats[0], cats[-1]

    def grow(vals):
        return None if vals[0] <= 0 else (vals[-1] - vals[0]) / vals[0] * 100

    bullets, ids, parts = [], [], []
    if len(ser) >= 2:
        top = max(tot, key=tot.get)
        low = min(tot, key=tot.get)
        every = all(max(ser, key=lambda s: s["values"][i])["name"] == top for i in range(len(cats)))
        lead = f"{top} leads every {cw}" if every else f"{top} has the highest {meas}, {_n(tot[top])}"
        parts.append(lead)
        ids += [fid_total[top], fid_total[low]]
        bullets += [f"{top} has the highest total {meas} at {_n(tot[top])}",
                    f"{low} has the lowest at {_n(tot[low])}"]
        if ordered_c and len(cats) >= 2:
            gs = {s["name"]: grow(s["values"]) for s in ser}
            gs = {k: v for k, v in gs.items() if v is not None}
            if gs and max(gs.values()) > 0:
                f_ = max(gs, key=gs.get)
                v = next(s["values"] for s in ser if s["name"] == f_)
                p = round(gs[f_])
                fid = new_fact(f"{f_} {meas} rose {p}% from {c0} ({_n(v[0])}) to {cn} ({_n(v[-1])})")
                parts.append(f"{f_} grew fastest, up {p}% from {c0} to {cn}")
                bullets.append(f"{f_} grew fastest: {_n(v[0])} in {c0} to {_n(v[-1])} in {cn}, up {p}%")
                ids.append(fid)
        elif sw and _axis_ordered(spec["series"]):               # the series are the periods: the total's change
            vals = [sum(s["values"][i] for s in ser) for i in range(len(cats))]
            tops = {c: max(ser, key=lambda s: s["values"][i])["name"] for i, c in enumerate(cats)}
            lead_c = max(cats, key=lambda c: sum(s["values"][cats.index(c)] for s in ser))
            ctot = {c: sum(s["values"][i] for s in ser) for i, c in enumerate(cats)}
            ids = [new_fact(f"{lead_c} total {meas}: {_n(ctot[lead_c])}")]
            parts = [f"{lead_c} leads {meas} at {_n(ctot[lead_c])}" if True else ""]
            bullets = [f"{lead_c} has the highest total {meas} at {_n(ctot[lead_c])}"]
            lo_c = min(cats, key=lambda c: ctot[c])
            ids.append(new_fact(f"{lo_c} total {meas}: {_n(ctot[lo_c])}"))
            bullets.append(f"{lo_c} has the lowest at {_n(ctot[lo_c])}")
            by = [sum(s["values"][i] for i in range(len(cats))) for s in ser]
            g = grow(by)
            if g is not None and abs(g) >= 1:
                p = round(abs(g))
                ids.append(new_fact(f"Total {meas} {'rose' if g > 0 else 'fell'} {p}% from {ser[0]['name']} "
                                    f"({_n(by[0])}) to {ser[-1]['name']} ({_n(by[-1])})"))
                parts.append(f"total {'rose' if g > 0 else 'fell'} {p}% from {ser[0]['name']} to {ser[-1]['name']}")
                bullets.append(f"Total {meas} {'rose' if g > 0 else 'fell'} {p}% from {ser[0]['name']} to "
                               f"{ser[-1]['name']}")
            del tops, vals
    else:
        vals = ser[0]["values"]
        top_i = max(range(len(cats)), key=lambda i: vals[i])
        low_i = min(range(len(cats)), key=lambda i: vals[i])
        total = sum(vals)
        g = grow(vals) if ordered_c and len(cats) >= 2 else None
        if g is not None and abs(g) >= 1:
            p = round(abs(g))
            ids.append(new_fact(f"{meas.capitalize()} {'rose' if g > 0 else 'fell'} {p}% from {c0} ({_n(vals[0])}) "
                                f"to {cn} ({_n(vals[-1])})"))
            parts.append(f"{meas.capitalize()} {'rose' if g > 0 else 'fell'} {p}% from {c0} to {cn}")
            bullets.append(f"{meas.capitalize()} {'rose' if g > 0 else 'fell'} from {_n(vals[0])} in {c0} to "
                           f"{_n(vals[-1])} in {cn}")
        else:
            share = round(vals[top_i] / total * 100) if total else 0
            ids.append(new_fact(f"{cats[top_i]} {meas}: {_n(vals[top_i])}, {share}% of the total of {_n(total)}"))
            parts.append(f"{cats[top_i]} leads {meas} at {_n(vals[top_i])}, {share}% of the total")
            bullets.append(f"{cats[top_i]} has the highest {meas}, {_n(vals[top_i])} ({share}% of {_n(total)})")
        ids.append(new_fact(f"{cats[top_i]} {meas} peak: {_n(vals[top_i])}; {cats[low_i]} low: {_n(vals[low_i])}"))
        bullets.append(f"The peak is {cats[top_i]} at {_n(vals[top_i])}; the low is {cats[low_i]} at {_n(vals[low_i])}")
    cand = "; ".join(parts)
    while _words(cand) > 15 and "; " in cand:
        cand = cand.rsplit("; ", 1)[0]
    if _words(cand) > 15:                                        # drop the "from X to Y" tail rather than the verb
        cand = re.sub(r" from .* to .*$", "", cand)
    return cand[:1].upper() + cand[1:], bullets[:3], list(dict.fromkeys(ids))


def _axis_ordered(dim) -> bool:
    return dim[0] == "period" or bool(re.search(r"year|quarter|month|week|day|date|period|time", _label(dim[1])))


def pages(request: str, paths) -> tuple[list[dict], list[dict], list[dict]]:
    """(pages, facts, expectations): one computed chart page per table the request asks a pivot of. Facts are D1..;
    expectations are what check_data finds again in the finished deck."""
    pgs, facts, expect = [], [], []
    for name, df in tables(paths):
        try:
            spec = ask(request, df)
            if not spec:
                continue
            pv = pivot(df, spec)
            cats, ser = pv["categories"], pv["series"]
            if not cats or len(cats) > MAX_CATEGORIES or not 1 <= len(ser) <= MAX_SERIES or \
                    all(v == 0 for s in ser for v in s["values"]):
                continue
            if len(cats) < 2 and len(ser) < 2:
                continue

            def new_fact(text, _f=facts, _n_=name):
                fid = f"D{len(_f) + 1}"
                _f.append({"id": fid, "text": text, "source": _n_})
                return fid
            ftot = {s["name"]: new_fact(f"{s['name']} total {spec['measure']} across {cats[0]} to {cats[-1]}: "
                                        f"{_n(sum(s['values']))}") for s in ser}
            cell_ids = []
            if len(cats) * len(ser) <= 100:
                cell_ids = [new_fact(f"{s['name']} {spec['measure']} in {c}: {_n(v)}")
                            for s in ser for c, v in zip(cats, s["values"])]
            title, bullets, ids = _compose(spec, pv, ftot, new_fact)
            allv = [v for s in ser for v in s["values"]]
            dec = 0 if all(float(v).is_integer() for v in allv) else (1 if all(abs(v * 10 - round(v * 10)) < 1e-6
                                                                              for v in allv) else 2)
            line = len(cats) > 6
            cw = _dim_word(spec["category"])
            what = f"{spec['measure'].capitalize()} by {cw}" + (f" for each {_dim_word(spec['series'])}"
                                                                if spec.get("series") else "")
            prov = ids[:4] or list(ftot.values())[:1]
            page = {"archetype": "line_trend" if line else "bar_takeaway", "fixed": True, "node": f"D{len(pgs) + 1}",
                    "section": "The data", "rank": 0, "title": title, "title_prov": prov,
                    "chart": {"kind": "line" if line else "column", "categories": cats, "unit": "", "decimals": dec,
                              "series": ser},
                    "takeaway": {"text": what},
                    "bullets": [{"text": b, "prov": [i]} for b, i in zip(bullets, ids)],
                    "source": {"text": f"{name} (summed per {cw}" + (f" and {_dim_word(spec['series'])}"
                                                                    if spec.get("series") else "") + ")",
                               "prov": prov}}
            pgs.append(page)
            expect.append({"file": name, "measure": spec["measure"], "categories": cats, "what": what,
                           "series": {s["name"]: s["values"] for s in ser}})
            del cell_ids
        except Exception:                                      # noqa: BLE001 - this table gives no page
            continue
    return pgs, facts, expect


# ------------------------------------------------------------------------------------------------ the check (P22)
def _norm(v) -> str:
    s = str(v).strip().lower()
    return re.sub(r"\.0$", "", re.sub(r"\s+", " ", s)) if re.fullmatch(r"-?\d+\.0", s) or True else s


def _same(want, got) -> bool:
    w, g = _norm(want), _norm(got)
    return w == g or bool(re.search(r"(?<![\w.])" + re.escape(w) + r"(?![\w.])", g))


def _charts(prs):
    """[(slide number, categories, [(series name, values)])] for every native chart, groups included."""
    out = []

    def walk(shapes, n):
        for sh in shapes:
            try:
                if sh.shape_type == 6:                          # a group
                    walk(sh.shapes, n)
                    continue
                if not getattr(sh, "has_chart", False) or not sh.has_chart:
                    continue
                ch = sh.chart
                cats, ser = [], []
                for pl in ch.plots:
                    try:
                        cs = [str(getattr(c, "label", c)) for c in pl.categories]
                    except Exception:                          # noqa: BLE001 - xy plots have no categories
                        cs = []
                    cats = cats or cs
                    ser += [(str(s.name), list(s.values)) for s in pl.series]
                out.append((n, cats, ser))
            except Exception:                                  # noqa: BLE001 - an unreadable chart is skipped
                continue
    for i, sl in enumerate(prs.slides, 1):
        walk(sl.shapes, i)
    return out


def _dec(v) -> int:
    t = f"{float(v):.6f}".rstrip("0").split(".")[1]
    return min(len(t), 6)


def _values_fit(pairs) -> tuple[float, list]:
    """The scale (1, 1e3, 1e6, 1e-3) at which the chart's numbers best fit the source's, and the cells that still
    differ: within 0.6% or the rounding of the decimals shown."""
    best = (1.0, pairs)
    for s in (1.0, 1e3, 1e6, 1e-3):
        bad = []
        for e, g, d in pairs:
            if g is None:
                bad.append((e, g, d))
                continue
            tol = max(0.006 * abs(e), (0.5 * 10 ** -d + 1e-9) * s)
            if abs(g * s - e) > tol:
                bad.append((e, g, d))
        if len(bad) < len(best[1]) or s == 1.0:
            best = (s, bad) if len(bad) < len(best[1]) or s == 1.0 else best
        if not bad:
            return s, []
    return best


def _compare(exp: dict, cats: list, ser: list) -> dict:
    """How well one chart holds an expectation, in either orientation: {"score", "orient", "missing_series",
    "missing_categories", "bad"} (bad: cells whose values differ, only when everything is present)."""
    ecats, eser = list(exp["categories"]), exp["series"]
    best = None
    for orient in ("straight", "transposed"):
        # straight: chart categories are the expected categories; transposed: they are the expected series
        want_c = ecats if orient == "straight" else list(eser)
        want_s = list(eser) if orient == "straight" else ecats
        got_c = {i: c for i, c in enumerate(cats)}
        cmap = {w: next((i for i, c in got_c.items() if _same(w, c)), None) for w in want_c}
        single = len(want_s) == 1 and orient == "straight"
        smap = {}
        for w in want_s:
            hit = next((k for k, (nm, _) in enumerate(ser) if _same(w, nm)), None)
            smap[w] = hit
        if single and smap[want_s[0]] is None and len(ser) >= 1:     # one series: its name is the chart's choice
            vals0 = list(eser.values())[0]
            hit = next((k for k, (_, v) in enumerate(ser) if len(v) == len(vals0) and _values_fit(
                [(e, _f(g), 0) for e, g in zip(vals0, v)])[1] == []), None)
            smap[want_s[0]] = hit
        miss_c = [w for w, i in cmap.items() if i is None]
        miss_s = [w for w, i in smap.items() if i is None]
        res = {"score": (len(want_c) - len(miss_c)) + (len(want_s) - len(miss_s)), "orient": orient,
               "missing_series": miss_s if orient == "straight" else miss_c,
               "missing_categories": miss_c if orient == "straight" else miss_s, "bad": []}
        if not miss_c and not miss_s:
            pairs = []
            for w_s in want_s:
                _, vals = ser[smap[w_s]]
                dec = max([_dec(v) for v in vals if v is not None] or [0])
                for w_c in want_c:
                    ec, es = (w_c, w_s) if orient == "straight" else (w_s, w_c)
                    e = eser[es][ecats.index(ec)] if orient == "straight" else eser[w_c][ecats.index(w_s)]
                    i = cmap[w_c]
                    pairs.append((e, _f(vals[i]) if i < len(vals) else None, dec, es if orient == "straight" else w_c,
                                  ec if orient == "straight" else w_s))
            sc, bad = _values_fit([(e, g, d) for e, g, d, _, _ in pairs])
            res["bad"] = [(a, b) for (e, g, d, a, b) in pairs if (e, g, d) in bad][:5]
            res["bad_vals"] = [(e, g) for (e, g, d) in bad][:5]
            res["score"] += 1 if not bad else 0.5
        if best is None or res["score"] > best["score"]:
            best = res
    return best


def _f(v):
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def _names(xs, limit=8) -> str:
    xs = [str(x) for x in xs]
    return ", ".join(xs[:limit]) + (f" and {len(xs) - limit} more" if len(xs) > limit else "")


def check_data(pptx_path: str, expectations: list[dict] | None) -> list[dict]:
    """P22: [failure] for every expectation no single native chart of the deck holds (all its categories and series
    with the source's values, in either orientation, rounded to the decimals shown, in a uniform scale)."""
    if not expectations:
        return []
    try:
        from pptx import Presentation
        charts = _charts(Presentation(str(pptx_path)))
    except Exception as e:                                     # noqa: BLE001 - an unreadable deck is P0's to report
        return [F("P22", "chart-content", f"the deck's charts could not be read to compare them with the data "
                  f"({type(e).__name__})", fix_scope="renderer", status="not_run", severity="warning")][:0]
    out = []
    for exp in expectations:
        what = exp.get("what") or f"{exp.get('measure', 'the values')} ({len(exp['categories'])} categories, " \
                                  f"{len(exp['series'])} series)"
        scored = [(_compare(exp, cats, ser), n, cats, ser) for n, cats, ser in charts]
        if any(not r["missing_series"] and not r["missing_categories"] and not r["bad"] for r, *_ in scored):
            continue
        if not scored:
            out.append(F("P22", "chart-content", f"no chart holds {what} from {exp['file']}: the deck has no native "
                         f"chart; add a column or line chart with categories {_names(exp['categories'])} and series "
                         f"{_names(exp['series'])} from {exp['file']}", fix_scope="content"))
            continue
        r, n, cats, ser = max(scored, key=lambda x: x[0]["score"])
        have = f"has categories {_names(cats) or 'none'} and series " + (_names([f"'{s}'" for s, _ in ser]) or "none")
        if r["missing_series"] or r["missing_categories"]:
            gaps = ([f"missing series {_names(r['missing_series'])}"] if r["missing_series"] else []) + \
                   ([f"missing categories {_names(r['missing_categories'])}"] if r["missing_categories"] else [])
            msg = (f"no chart holds {what} from {exp['file']}: the closest chart (slide {n}) {have}; "
                   + "; ".join(gaps) + ". Plot every series and category with the values of the file")
        else:
            wrong = "; ".join(f"{a} in {b} shows {_n(g) if g is not None else 'nothing'} against {_n(e)}"
                              for (a, b), (e, g) in zip(r["bad"], r["bad_vals"]))
            msg = (f"no chart holds {what} from {exp['file']}: the closest chart (slide {n}) has the right "
                   f"categories and series but its values differ from the file: {wrong}")
        out.append(F("P22", "chart-content", msg, slide=n, fix_scope="content"))
    return out
