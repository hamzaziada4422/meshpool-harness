"""The 12 benchmark tasks (7 office + 5 code) with strict graders.

Every grader takes (workdir, reply) and returns {"ok", "detail", "passed", "total"}. Graders never modify
workdir (all work happens in scratch copies / temp dirs) and never raise.
Office quality is graded by file content only; code tasks by real test counts (gradekit.count_tests).
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import math
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from testkit import gradekit as gk  # noqa: E402
from testkit.gradekit import Checks, result, safe  # noqa: E402
from testkit.bench3 import make_fixtures as mf  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PYTHON = "/root/.venvs/mp/bin/python"


@dataclass
class Task:
    id: str
    kind: str                              # "office" | "code"
    prompt: str                            # what the user types, same text for every agent
    setup: Callable[[Path], None]          # fills an EMPTY existing folder with the starting files
    grade: Callable[[Path, str], dict]     # (workdir, reply) -> {"ok", "detail", "passed", "total"}
    timeout: int = 900


def _copy_fixture(src: Path, dst: Path):
    if not src.is_dir():
        raise FileNotFoundError(f"fixture missing: {src} (run testkit/bench3/make_fixtures.py)")
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)


def _setup_for(kind: str, tid: str):
    return lambda workdir: _copy_fixture(FIX / kind / tid, Path(workdir))


def _rel_close(a, b, rel=0.01, absol=0.0):
    return a is not None and abs(a - b) <= max(abs(b) * rel, absol)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _recalc_values(path: Path):
    """Recalculated workbook (data_only) loaded from a temp dir outside the workspace."""
    import openpyxl
    with tempfile.TemporaryDirectory(prefix="bench3-rc-") as td:
        rp = gk.recalc_xlsx(path, outdir=td)
        return openpyxl.load_workbook(rp, data_only=True)


def _headers(ws):
    return {str(c.value).strip().lower(): c.column for c in ws[1] if c.value is not None}


def _rgb(fill):
    c = getattr(fill, "fgColor", None)
    v = getattr(c, "rgb", None)
    return v[-6:].upper() if isinstance(v, str) else None


# =========================================================================== 1. xlsx_edit

@safe
def grade_xlsx_edit(workdir, reply):
    import openpyxl
    ck = Checks()
    f = Path(workdir) / "inventory.xlsx"
    if not f.exists():
        return result(False, "inventory.xlsx is missing (must be saved over the same file)", 0, 1)
    orig = openpyxl.load_workbook(FIX / "office" / "xlsx_edit" / "inventory.xlsx")
    wb = openpyxl.load_workbook(f)
    ck.add("sheets Stock and Notes exist", {"Stock", "Notes"} <= set(wb.sheetnames), str(wb.sheetnames))
    if not ck.items[-1][1]:
        return ck.result()
    ws, ows = wb["Stock"], orig["Stock"]
    # Notes unchanged
    nw, no = wb["Notes"], orig["Notes"]
    nvals = {(c.row, c.column): c.value for r in nw.iter_rows() for c in r if c.value is not None}
    ovals = {(c.row, c.column): c.value for r in no.iter_rows() for c in r if c.value is not None}
    ck.add("Notes sheet unchanged", nvals == ovals)
    # header formatting preserved
    hdr_ok = all(ws.cell(1, c).value == ows.cell(1, c).value and ws.cell(1, c).font.b
                 and _rgb(ws.cell(1, c).fill) == _rgb(ows.cell(1, c).fill) for c in range(1, 6))
    ck.add("header text, bold and fill preserved", hdr_ok)
    ck.add("frozen header preserved", ws.freeze_panes == ows.freeze_panes, str(ws.freeze_panes))
    widths_ok = all(abs((ws.column_dimensions[L].width or 0) - (ows.column_dimensions[L].width or 0)) <= 2 for L in "ABCDE")
    ck.add("column widths preserved", widths_ok)
    # rows by sku
    h = _headers(ws)
    vcol = next((c for k, c in h.items() if "value" in k), None)
    ck.add("a Value column exists in the header", vcol is not None, str(list(h)))
    skus = {str(ws.cell(r, 1).value): r for r in range(2, ws.max_row + 1) if str(ws.cell(r, 1).value).startswith("SKU-")}
    exp = {row[0]: row for row in mf.inventory_rows()}
    ck.add("all 30 SKU rows present", set(skus) == set(exp), f"{len(skus)} found")
    if vcol is None or set(skus) != set(exp):
        return ck.result()
    nf0 = ows.cell(2, 5).number_format
    ck.add("unit_cost currency format preserved",
           all(ws.cell(r, 5).number_format == nf0 or "$" in ws.cell(r, 5).number_format for r in skus.values()),
           ws.cell(2, 5).number_format)
    ck.add("Value cells are formulas",
           all(isinstance(ws.cell(r, vcol).value, str) and ws.cell(r, vcol).value.startswith("=") for r in skus.values()))
    trow = next((r for r in range(2, ws.max_row + 1) if r not in skus.values()
                 and any(isinstance(ws.cell(r, c).value, str) and "total" in ws.cell(r, c).value.lower()
                         for c in range(1, vcol))), None)
    ck.add("a total row exists", trow is not None)
    ck.add("total is a formula", trow is not None and isinstance(ws.cell(trow, vcol).value, str)
           and ws.cell(trow, vcol).value.startswith("="))
    # recalculated values
    rv = _recalc_values(f)["Stock"]
    rh = _headers(rv)
    rvcol = next((c for k, c in rh.items() if "value" in k), vcol)
    rrows = {str(rv.cell(r, 1).value): r for r in range(2, rv.max_row + 1) if str(rv.cell(r, 1).value).startswith("SKU-")}
    bad_cost = bad_val = bad_other = 0
    total = 0.0
    for sku, (_, item, cat, qty, cost) in exp.items():
        r = rrows.get(sku)
        if r is None:
            bad_other += 1
            continue
        want = round(cost * 1.1, 2) if cat == "Cables" else cost
        got_cost, got_qty, got_val = _num(rv.cell(r, 5).value), _num(rv.cell(r, 4).value), _num(rv.cell(r, rvcol).value)
        if rv.cell(r, 2).value != item or rv.cell(r, 3).value != cat or got_qty != qty:
            bad_other += 1
        if got_cost is None or abs(got_cost - want) > 0.01:
            bad_cost += 1
        if got_val is None or got_cost is None or abs(got_val - qty * got_cost) > 0.01 or abs(got_val - qty * want) > 0.05:
            bad_val += 1
        total += qty * want
    ck.add("item/category/qty unchanged", bad_other == 0, f"{bad_other} rows differ")
    ck.add("Cables +10%, all other unit costs unchanged", bad_cost == 0, f"{bad_cost} rows wrong")
    ck.add("every Value = qty x unit_cost after recalculation", bad_val == 0, f"{bad_val} rows wrong")
    tv = _num(rv.cell(trow, rvcol).value) if trow else None
    ck.add("total equals the sum of Value", tv is not None and abs(tv - total) <= 0.05, f"{tv} vs {total:.2f}")
    return ck.result()


# =========================================================================== 2. docx_tracked

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_root(path):
    from lxml import etree
    with zipfile.ZipFile(path) as z:
        return etree.fromstring(z.read("word/document.xml"))


def _unwrap(el):
    parent = el.getparent()
    i = parent.index(el)
    for ch in list(el):
        parent.insert(i, ch)
        i += 1
    parent.remove(el)


def tracked_view(root, accept: bool):
    """Return a copy of a document.xml root with all tracked changes accepted or rejected."""
    root = copy.deepcopy(root)
    drop, keep = (_W + "del", _W + "ins") if accept else (_W + "ins", _W + "del")
    drop_m, keep_m = (_W + "moveFrom", _W + "moveTo") if accept else (_W + "moveTo", _W + "moveFrom")
    for el in list(root.iter(drop, drop_m)):
        if el.getparent() is not None:
            el.getparent().remove(el)
    for el in list(root.iter(keep, keep_m)):
        par = el.getparent()
        if par is None:
            continue
        if par.tag == _W + "rPr":
            par.remove(el)
        else:
            _unwrap(el)
    for t in root.iter(_W + "delText"):
        t.tag = _W + "t"
    return root


def doc_paragraph_texts(root):
    return ["".join(t.text or "" for t in p.iter(_W + "t")) for p in root.iter(_W + "p")]


def _run_italic(r):
    rpr = r.find(_W + "rPr")
    i = rpr.find(_W + "i") if rpr is not None else None
    return i is not None and i.get(_W + "val", "true") not in ("0", "false", "off")


@safe
def grade_docx_tracked(workdir, reply):
    ck = Checks()
    f = Path(workdir) / "contract.docx"
    if not f.exists():
        return result(False, "contract.docx is missing", 0, 1)
    root = _docx_root(f)
    orig = doc_paragraph_texts(_docx_root(FIX / "office" / "docx_tracked" / "contract.docx"))
    ins = [e for e in root.iter(_W + "ins") if e.getparent().tag != _W + "rPr"]
    dels = [e for e in root.iter(_W + "del") if e.getparent().tag != _W + "rPr"]
    ck.add("has tracked insertions (w:ins)", bool(ins))
    ck.add("has tracked deletions (w:del)", bool(dels))
    rej = tracked_view(root, accept=False)
    ck.add("reject-all gives back the original text exactly", doc_paragraph_texts(rej) == orig)
    acc = tracked_view(root, accept=True)
    want = [p.replace("Acme Corp", "Globex Inc").replace("4,000", "4,500") for p in orig]
    got = doc_paragraph_texts(acc)
    ck.add("accept-all gives exactly the requested text", got == want,
           next((f"{a!r} != {b!r}" for a, b in zip(got, want) if a != b), f"{len(got)} vs {len(want)} paragraphs"))
    full = "\n".join(got)
    ck.add("accept-all: no Acme Corp, >=3 Globex Inc, 4,500 and no 4,000",
           "Acme Corp" not in full and full.count("Globex Inc") >= 3 and "4,500" in full and "4,000" not in full)
    title = next((p for p in acc.iter(_W + "p") if "".join(t.text or "" for t in p.iter(_W + "t")) == "Services Agreement"), None)
    st = title.find(f"{_W}pPr/{_W}pStyle") if title is not None else None
    ck.add("Title paragraph style survives", st is not None and st.get(_W + "val") == "Title")
    fee = [r for r in acc.iter(_W + "r") if "4,500" in "".join(t.text or "" for t in r.iter(_W + "t"))]
    ck.add("fee text is still italic", bool(fee) and all(_run_italic(r) for r in fee))
    return ck.result()


# =========================================================================== 3. invoices_fx

@safe
def grade_invoices_fx(workdir, reply):
    ck = Checks()
    f = Path(workdir) / "invoices.xlsx"
    if not f.exists():
        return result(False, "invoices.xlsx is missing", 0, 1)
    wb = _recalc_values(f)
    rows = []  # (sheet, [values])
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            if any(v is not None for v in r):
                rows.append(list(r))
    lines = mf.invoice_lines()
    ids = [i[0] for i in mf.INVOICES]
    id_rows = [r for r in rows if any(isinstance(v, str) and any(i in v for i in ids) for v in r)]
    ck.add("one row per invoice line (no more than the 8 lines plus per-invoice subtotals)",
           len(lines) <= len(id_rows) <= len(lines) + len(ids), f"{len(id_rows)} rows mention invoice ids")
    used = set()
    missing = []
    for inv, cust, cur, item, qty, price, amt, usd in lines:
        hit = None
        for k, r in enumerate(id_rows):
            if k in used:
                continue
            strs = " | ".join(str(v).lower() for v in r if isinstance(v, str))
            nums = [v for v in r if _num(v) is not None]
            if inv.lower() in strs and item.lower() in strs and cust.lower() in strs and cur.lower() in strs \
                    and all(any(abs(n - w) <= 0.011 for n in nums) for w in (qty, price, amt, usd)):
                hit = k
                break
        if hit is None:
            missing.append(f"{inv} {item}")
        else:
            used.add(hit)
    ck.add("every line has invoice, customer, currency, item, qty, unit price, amount and USD amount",
           not missing, "missing: " + ", ".join(missing))
    gt = mf.invoices_grand_total_usd()
    tot_rows = [r for r in rows if any(isinstance(v, str) and "total" in v.lower() for v in r)]
    ck.add(f"a total row holds the USD grand total {gt:.2f}",
           any(_num(v) is not None and abs(v - gt) <= 0.011 for r in tot_rows for v in r))
    ck.add("the reply states the grand total", gk.text_has_amount(reply or "", gt), f"expected {gt:.2f}")
    return ck.result()


# =========================================================================== 4. docx_mailmerge

def _date_forms(iso: str):
    d = dt.date.fromisoformat(iso)
    forms = {iso, d.strftime("%d %B %Y"), d.strftime("%B %d, %Y"), d.strftime("%d/%m/%Y"), d.strftime("%m/%d/%Y"),
             d.strftime("%d.%m.%Y"), d.strftime("%b %d, %Y"), d.strftime("%d %b %Y"), d.strftime("%B %d %Y"),
             d.strftime("%A, %B %d, %Y")}
    forms |= {re.sub(r"(?<!\d)0(\d)", r"\1", x) for x in set(forms)}
    return {x.lower() for x in forms}


def _docx_text(doc):
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                parts += [p.text for p in c.paragraphs]
    return "\n".join(parts)


@safe
def grade_docx_mailmerge(workdir, reply):
    import docx
    ck = Checks()
    folder = Path(workdir) / "letters"
    files = sorted(p for p in folder.rglob("*.docx") if not p.name.startswith("~$")) if folder.is_dir() else []
    ck.add("letters/ holds exactly 5 .docx files", len(files) == 5, f"{len(files)} found")
    if len(files) != 5:
        return ck.result()
    docs = [(p, docx.Document(str(p))) for p in files]
    texts = [_docx_text(d) for _, d in docs]
    ck.add("no placeholders left ({{ or }})", all("{{" not in t and "}}" not in t for t in texts))
    mapped, issues = set(), []
    for name, company, amount, due in mf.CUSTOMERS:
        idx = [i for i, t in enumerate(texts) if name in t and company in t]
        if len(idx) != 1 or idx[0] in mapped:
            issues.append(f"{name}: {len(idx)} letters")
            continue
        i = idx[0]
        mapped.add(i)
        t = texts[i]
        if not gk.text_has_amount(t, float(amount)):
            issues.append(f"{name}: amount {amount} missing")
        if not any(form in t.lower() for form in _date_forms(due)):
            issues.append(f"{name}: due date {due} missing")
        doc = docs[i][1]
        if not any(p.style.name == "Heading 1" and "payment reminder" in p.text.lower() for p in doc.paragraphs):
            issues.append(f"{name}: Heading 1 lost")
        if not any(r.bold and gk.text_has_amount(r.text, float(amount)) for p in doc.paragraphs for r in p.runs):
            issues.append(f"{name}: bold amount run lost")
    ck.add("each customer has one letter with name, company, amount, due date, Heading 1 and a bold run",
           not issues, "; ".join(issues))
    return ck.result()


# =========================================================================== 5. xlsx_budget

def _find(wb, name):
    return next((wb[n] for n in wb.sheetnames if n.strip().lower() == name.lower()), None)


def _cells_near(ws, target, rel=0.01):
    return sum(1 for r in ws.iter_rows() for c in r if _num(c.value) is not None and _rel_close(c.value, target, rel, 0.5))


@safe
def grade_xlsx_budget(workdir, reply):
    import openpyxl
    ck = Checks()
    f = Path(workdir) / "budget.xlsx"
    if not f.exists():
        return result(False, "budget.xlsx is missing", 0, 1)
    wb = openpyxl.load_workbook(f)
    a, b = _find(wb, "Assumptions"), _find(wb, "Budget")
    ck.add("sheets Assumptions and Budget exist", a is not None and b is not None, str(wb.sheetnames))
    if a is None or b is None:
        return ck.result()
    refs = sum(1 for r in b.iter_rows() for c in r
               if isinstance(c.value, str) and c.value.startswith("=") and re.search(r"assumptions'?!", c.value, re.I))
    ck.add("Budget formulas reference the Assumptions sheet (>=5 cells)", refs >= 5, f"{refs} cells")
    e = mf.cafe_expected()
    bv = _find(_recalc_values(f), "Budget")
    ck.add("12 months of revenue match", _cells_near(bv, e["rev_m"]) >= 12, f"expected ~{e['rev_m']:.0f}")
    ck.add("12 months of profit match", _cells_near(bv, e["profit_m"]) >= 12, f"expected ~{e['profit_m']:.0f}")
    ck.add("annual revenue matches", _cells_near(bv, e["rev_y"]) >= 1, f"expected ~{e['rev_y']:.0f}")
    ck.add("annual profit matches", _cells_near(bv, e["profit_y"]) >= 1, f"expected ~{e['profit_y']:.0f}")
    # live test: raise the average ticket by 10% in a scratch copy and recalc
    live = False
    with gk.scratch_copy(workdir) as w:
        wb2 = openpyxl.load_workbook(w / "budget.xlsx")
        a2 = _find(wb2, "Assumptions")
        done = False
        for row in a2.iter_rows():
            if done:
                break
            for c in row:
                if isinstance(c.value, str) and "ticket" in c.value.lower():
                    nxt = next((x for x in row[c.column:] if _num(x.value) is not None), None)
                    if nxt is not None:
                        nxt.value = nxt.value * 1.1
                        done = True
                        break
        if done:
            wb2.save(w / "budget_live.xlsx")
            bl = _find(_recalc_values(w / "budget_live.xlsx"), "Budget")
            e2 = mf.cafe_expected(1.1)
            live = _cells_near(bl, e2["rev_y"]) >= 1 and _cells_near(bl, e2["profit_y"]) >= 1
    ck.add("changing the average ticket by +10% moves annual revenue and profit (live formulas)", live)
    return ck.result()


# =========================================================================== 6. pptx_chart

def _iter_shapes(shapes):
    for s in shapes:
        yield s
        if getattr(s, "shape_type", None) == 6:  # group
            yield from _iter_shapes(s.shapes)


def _label_ok(cat, label):
    return str(cat).strip().lower().startswith(label.lower())


def _chart_matches(chart):
    """True if a chart plots QUARTERLY (either orientation) within 0.5%."""
    for plot in chart.plots:
        cats = [str(c) for c in plot.categories]
        series = {s.name: list(s.values) for s in plot.series}
        for cat_labels, ser_labels, getv in (
                (mf.QUARTERS, mf.REGIONS, lambda ser, i: mf.QUARTERLY[ser][i]),
                (mf.REGIONS, mf.QUARTERS, lambda ser, i: mf.QUARTERLY[mf.REGIONS[i]][mf.QUARTERS.index(ser)])):
            if len(cats) != len(cat_labels) or not all(_label_ok(c, l) for c, l in zip(cats, cat_labels)):
                continue
            ok = True
            for lab in ser_labels:
                vals = next((v for n, v in series.items() if _label_ok(n or "", lab)), None)
                if vals is None or len(vals) != len(cats) or not all(
                        v is not None and _rel_close(v, getv(lab, i), 0.005) for i, v in enumerate(vals)):
                    ok = False
                    break
            if ok:
                return True
    return False


@safe
def grade_pptx_chart(workdir, reply):
    from pptx import Presentation
    ck = Checks()
    f = Path(workdir) / "deck.pptx"
    if not f.exists():
        return result(False, "deck.pptx is missing", 0, 1)
    prs = Presentation(str(f))
    n = len(prs.slides)
    ck.add("4 to 6 slides", 4 <= n <= 6, f"{n} slides")
    W, H = prs.slide_width, prs.slide_height
    outside = 0
    charts = []
    for sl in prs.slides:
        for s in _iter_shapes(sl.shapes):
            if None not in (s.left, s.top, s.width, s.height):
                if s.left < 0 or s.top < 0 or s.left + s.width > W or s.top + s.height > H:
                    outside += 1
            if getattr(s, "has_chart", False) and s.has_chart:
                charts.append(s.chart)
    ck.add("no shape outside the slide", outside == 0, f"{outside} shapes")
    ck.add("at least one native chart", bool(charts))
    ck.add("a chart plots revenue per quarter for each region with the CSV values",
           any(_chart_matches(c) for c in charts))
    return ck.result()


# =========================================================================== 7. pdf_report

def _has_int(text, x):
    for n in {int(math.floor(x)), int(round(x))}:
        for form in (f"{n:,}", str(n)):
            if re.search(r"(?<![\d,.])" + re.escape(form) + r"(?!\d)", text):
                return True
    return False


@safe
def grade_pdf_report(workdir, reply):
    from pypdf import PdfReader
    ck = Checks()
    f = Path(workdir) / "report.pdf"
    if not f.exists():
        return result(False, "report.pdf is missing", 0, 1)
    rd = PdfReader(str(f))
    ck.add("1 or 2 pages", 1 <= len(rd.pages) <= 2, f"{len(rd.pages)} pages")
    text = "\n".join((p.extract_text() or "") for p in rd.pages)
    low = text.lower()
    tot = mf.sales_by_region()
    ck.add("every region name appears", all(r.lower() in low for r in tot))
    ck.add("every region revenue total appears (integer part)", all(_has_int(text, v) for v in tot.values()),
           ", ".join(f"{k}={v:,.2f}" for k, v in tot.items()))
    ev = gk.pdf_chart_evidence(f)  # REQUIRED chart: raster image or vector bars/curves/polyline
    ck.add("contains a chart (raster image >=150x100 or vector bars/curves/line)", ev["chart"],
           f"images={ev['images']} bars={ev['bars']} curved={ev['curved']} polylines={ev['polylines']}")
    return ck.result()


# =========================================================================== code tasks: python

def _sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _py_exe():
    return os.environ.get("BENCH_PYTHON", PYTHON)


@lru_cache(maxsize=None)
def _py_expected(tid: str, test_file: str) -> int:
    """Number of tests in the pristine test file, from `pytest --collect-only -q`."""
    with gk.scratch_copy(FIX / "code" / tid) as w:
        rc, out = gk.run([_py_exe(), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", test_file],
                         cwd=w, timeout=120, env={"PYTHONDONTWRITEBYTECODE": "1"})
    n = sum(1 for l in out.splitlines() if "::" in l and not l.startswith(("=", " ")))
    if n == 0:
        raise RuntimeError(f"could not collect pristine tests: {out[-200:]}")
    return n


def _make_py_grader(tid, module, test_file):
    @safe
    def grade(workdir, reply):
        fx = FIX / "code" / tid
        expected = _py_expected(tid, test_file)
        with gk.scratch_copy(workdir) as w:
            if not (w / module).exists():
                return result(False, f"{module} is missing", 0, expected)
            changed = _sha(w / test_file) != _sha(fx / test_file)
            shutil.copy2(fx / test_file, w / test_file)  # restore pristine tests, in the scratch copy only
            for stray in ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"):
                if not (fx / stray).exists() and (w / stray).exists():
                    (w / stray).unlink()
            rc, out = gk.run([_py_exe(), "-m", "pytest", "-q", "-p", "no:cacheprovider", test_file], cwd=w,
                             timeout=180, env={"PYTHONDONTWRITEBYTECODE": "1"})
        c = gk.count_tests(out)
        ok = c["failed"] + c["errors"] == 0 and c["passed"] == expected
        note = " [agent changed the test file; pristine copy was used]" if changed else ""
        return result(ok, f"{c['passed']}/{expected} passed, {c['failed']} failed, {c['errors']} errors{note}",
                      c["passed"], expected)
    return grade


# =========================================================================== code tasks: rust

def _rust_expected(tid: str) -> int:
    """#[test] attributes in tests/*.rs plus doc-test code fences in src/*.rs of the pristine fixture."""
    fx = FIX / "code" / tid
    n = sum(len(re.findall(r"^\s*#\[test\]", p.read_text(), re.M)) for p in (fx / "tests").glob("*.rs"))
    fences = sum(len(re.findall(r"^\s*//[/!]\s*```", p.read_text(), re.M)) for p in (fx / "src").glob("*.rs"))
    return n + fences // 2


def _make_rs_grader(tid):
    @safe
    def grade(workdir, reply):
        fx = FIX / "code" / tid
        expected = _rust_expected(tid)
        # fresh target dir per call (outside the workspace): a reused one can serve a stale binary
        # because cargo's freshness check trusts file mtimes that a copy preserves
        own_tgt = tempfile.mkdtemp(prefix="bench3-target-", dir=os.environ.get("BENCH_CARGO_TARGET") or None)
        tgt = own_tgt
        try:
            with gk.scratch_copy(workdir) as w:
                if not (w / "src" / "lib.rs").exists():
                    return result(False, "src/lib.rs is missing", 0, expected)
                changed = any(_sha(w / "tests" / p.name) != _sha(p) for p in (fx / "tests").glob("*.rs"))
                shutil.rmtree(w / "tests", ignore_errors=True)
                shutil.copytree(fx / "tests", w / "tests")
                for p in (fx / "src").glob("*.rs"):  # doc-test files other than lib.rs are tests too
                    if p.name != "lib.rs":
                        shutil.copy2(p, w / "src" / p.name)
                rc, out = gk.run(["cargo", "test", "--color", "never", "--", "--include-ignored"], cwd=w, timeout=600,
                                 env={"CARGO_NET_OFFLINE": "true", "CARGO_TARGET_DIR": tgt,
                                      "CARGO_TERM_COLOR": "never"})
        finally:
            shutil.rmtree(own_tgt, ignore_errors=True)
        c = gk.count_tests(out)
        ok = c["failed"] == 0 and c["errors"] == 0 and c["passed"] == expected
        note = " [agent changed tests/; pristine copy was used]" if changed else ""
        tail = "" if ok or c["total"] else f" | {out.strip().splitlines()[-1][:120] if out.strip() else ''}"
        return result(ok, f"{c['passed']}/{expected} passed across all result lines, {c['failed']} failed{note}{tail}",
                      c["passed"], expected)
    return grade


# =========================================================================== registry

def _office(tid, prompt, grade, timeout=900):
    return Task(tid, "office", prompt, _setup_for("office", tid), grade, timeout)


def _code(tid, prompt, grade, timeout=900):
    return Task(tid, "code", prompt, _setup_for("code", tid), grade, timeout)


def _py_prompt(module, test_file):
    return (f"Implement {module}.py so that the tests in {test_file} pass. Do not change the tests. "
            "The task is described in README.md.")


_RS_PROMPT = ("Implement src/lib.rs so that all the tests in tests/ pass, including the ones marked #[ignore]. "
              "Do not change the tests. The task is described in README.md.")

TASKS: list[Task] = [
    _office("xlsx_edit",
            "In inventory.xlsx, add a Value column (qty times unit cost) as live formulas. Raise the unit cost of "
            "every item in the Cables category by 10%. Add a total row for the Value column. Keep the formatting "
            "that's already there, and save over the same file.", grade_xlsx_edit),
    _office("docx_tracked",
            "In contract.docx, change the client from Acme Corp to Globex Inc everywhere, and the fee from 4,000 to "
            "4,500 USD. Make these changes as tracked changes so I can accept or reject them in Word. Save over the "
            "same file.", grade_docx_tracked),
    _office("invoices_fx",
            "Read the three invoice PDFs and make invoices.xlsx with one row per invoice line: invoice, customer, "
            "currency, item, qty, unit price, amount, and amount in USD (use the rates in fx_rates.csv). Add a total "
            "row in USD at the bottom. Then tell me the grand total in USD.", grade_invoices_fx),
    _office("docx_mailmerge",
            "Use letter_template.docx and customers.csv to make one letter per customer, in a folder called letters "
            "(one .docx each). Fill in every placeholder and keep the template's formatting.", grade_docx_mailmerge),
    _office("xlsx_budget",
            "Using assumptions.csv for our small cafe, make budget.xlsx: a 12-month budget with an Assumptions sheet "
            "and a Budget sheet. The Budget sheet should use formulas that point at the assumptions (revenue, food "
            "cost, staff, rent, other, and profit for each month, plus a total column), so when I change an "
            "assumption the budget updates.", grade_xlsx_budget),
    _office("pptx_chart",
            "Make deck.pptx, 4 to 6 slides presenting quarterly.csv to leadership. Include a real PowerPoint chart "
            "(one I can edit, not a picture) of revenue by quarter for each region.", grade_pptx_chart),
    _office("pdf_report",
            "Make report.pdf, a one or two page sales report from sales.csv, with a table of revenue by region and "
            "a chart of revenue by region.", grade_pdf_report),
    _code("py_zipper", _py_prompt("zipper", "zipper_test.py"), _make_py_grader("py_zipper", "zipper.py", "zipper_test.py")),
    _code("py_sgf_parsing", _py_prompt("sgf_parsing", "sgf_parsing_test.py"),
          _make_py_grader("py_sgf_parsing", "sgf_parsing.py", "sgf_parsing_test.py")),
    _code("py_forth", _py_prompt("forth", "forth_test.py"), _make_py_grader("py_forth", "forth.py", "forth_test.py")),
    _code("rs_react", _RS_PROMPT, _make_rs_grader("rs_react"), 1200),
    _code("rs_macros", _RS_PROMPT, _make_rs_grader("rs_macros"), 1200),
]


def get(ids: str | None) -> list[Task]:
    """Comma list of task ids, or every task when ids is None/empty."""
    if not ids or not ids.strip():
        return list(TASKS)
    by_id = {t.id: t for t in TASKS}
    want = [i.strip() for i in ids.split(",") if i.strip()]
    unknown = [i for i in want if i not in by_id]
    if unknown:
        raise KeyError(f"unknown task id(s) {unknown}; known: {list(by_id)}")
    return [by_id[i] for i in want]
