"""Workbook QA: LibreOffice recalculation, error scan, circular refs, hardcoded numbers, structure."""
from __future__ import annotations

import atexit
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from openpyxl import load_workbook

ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "Err:", "#ERR")
CALC_SHEETS = re.compile(r"calc|model|output|summary|dashboard|p&l|pnl|forecast|valuation", re.I)
INPUT_SHEETS = re.compile(r"input|assumption|data|source|raw", re.I)
FINANCE_SHEETS = re.compile(r"comps|precedent|dcf|valuation", re.I)
RECALC_TIMEOUT_S = 180


def recalc(xlsx_path: str, out_dir: str | None = None) -> str:
    """Round-trip through LibreOffice headless so every formula gets a computed cached value. Without out_dir the copy
    goes to a scratch folder the caller removes (recalc_values does it for you); it is also removed at exit."""
    from engines.forge.common import _run_tree, convert_cached, convert_keep, convert_key
    from meshpool_harness.office_runtime import Unavailable, find_soffice
    soffice = find_soffice()
    if not soffice:
        raise Unavailable()                          # says why: not installed, or a system copy Meshpool does not use
    if out_dir:
        out = Path(out_dir)
    else:
        out = Path(tempfile.mkdtemp(prefix="recalc_"))
        atexit.register(shutil.rmtree, str(out), True)      # the converted copy is scratch: do not leave it in /tmp
    out.mkdir(parents=True, exist_ok=True)
    dest = out / (Path(xlsx_path).stem + ".xlsx")             # converted to .xlsx, whatever it came in as (.xlsm)
    # the finish gate and the review digest recalculate the same unchanged file: the same bytes are not run again
    key = convert_key(xlsx_path, "xlsx:Calc MS Excel 2007 XML", soffice, "calc")
    if convert_cached(key, dest):
        return str(dest)
    from meshpool_harness import lo_session                  # the kept LibreOffice: no new process per workbook
    made = lo_session.convert(soffice, xlsx_path, "xlsx:Calc MS Excel 2007 XML", str(out), RECALC_TIMEOUT_S,
                              recalc=True)
    if made is not None and made.exists():
        convert_keep(key, made)
        return str(made)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as profile:
        # its own process group, killed whole on a timeout: soffice.bin outlives a plain kill of soffice
        r = _run_tree([soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--calc",
                       "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out), xlsx_path],
                      RECALC_TIMEOUT_S)
    if r.returncode:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    if dest.exists() and dest.stat().st_mtime >= t0 - 2:     # this run wrote it
        convert_keep(key, dest)
    return str(dest)


def recalc_values(xlsx_path: str):
    """The workbook with values (data_only) after a LibreOffice recalculation. The recalculated copy is removed as
    soon as it is read, so a killed run leaves no scratch folder behind."""
    out = tempfile.mkdtemp(prefix="recalc_")
    try:
        return load_workbook(recalc(xlsx_path, out), data_only=True)
    finally:
        shutil.rmtree(out, True)


MAX_SCAN_ROWS = 20000
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
CHECK_SHEETS = re.compile(r"\bchecks?\b", re.I)          # Checks, Check, Error checks (not Checklist)
FAILING = ("ERROR", "FAIL", "FAILED", "NOT OK", "MISMATCH", "DOES NOT TIE")
_CELLREF = re.compile(r"^(\$?[A-Za-z]{1,3}\$?\d+(:\$?[A-Za-z]{1,3}\$?\d+)?|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}|"
                      r"\$?\d+:\$?\d+)$")
_MAX_REF_CELLS = 20000


def formula_refs(formula: str) -> list[tuple[str | None, str]]:
    """(sheet or None, reference) for every reference in a formula; a defined name comes back as (None, name)."""
    from openpyxl.formula.tokenizer import Token, Tokenizer
    out = []
    for t in Tokenizer(formula).items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            if "!" in t.value:
                sh, _, ref = t.value.rpartition("!")
                if len(sh) >= 2 and sh[0] == sh[-1] == "'":
                    sh = sh[1:-1].replace("''", "'")
                out.append((sh, ref))
            else:
                out.append((None, t.value))
    return out


def _ref_cells(ws, ref: str):
    """The cells a reference covers, without creating any (openpyxl creates a cell on access); None when the extent
    cannot be judged (whole columns or rows, a huge range, a malformed reference)."""
    from openpyxl.utils.cell import range_boundaries
    try:
        c0, r0, c1, r1 = range_boundaries(ref.replace("$", ""))
    except (ValueError, TypeError):
        return None
    store = getattr(ws, "_cells", None)                      # a chartsheet has none
    if store is None or None in (c0, r0, c1, r1) or (c1 - c0 + 1) * (r1 - r0 + 1) > _MAX_REF_CELLS:
        return None
    return [store.get((r, c)) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


def _blank(cell) -> bool:
    return cell is None or cell.value is None or (isinstance(cell.value, str) and not cell.value.strip())


def hollow_checks(wb) -> list[dict]:
    """Check formulas that cannot be checking anything, on every Checks tab of a formula workbook (data_only=False):
    - every cell they reference is empty (=B12-SUM(B10:G10) written on the Checks tab reads its own blank cells and
      always shows OK);
    - every cell they reference is a typed value on the Checks tab itself, so they never look at the figures they are
      meant to check.
    References through defined names are followed. A formula with a reference that cannot be judged (a whole column,
    a name bound to a constant, an unknown sheet) is left alone. Returns [{"cell", "formula", "kind", "detail"}]."""
    names: dict[str, list] = {}
    try:
        items = list(wb.defined_names.items())
    except AttributeError:
        items = [(d.name, d) for d in wb.defined_names.definedName]
    for n, dn in items:
        try:
            names[n.lower()] = list(dn.destinations)
        except Exception:  # noqa: BLE001 - a name bound to a constant or an expression
            names[n.lower()] = []
    out = []
    for ws in wb.worksheets:
        if not CHECK_SHEETS.search(ws.title):
            continue
        local = {}
        try:
            for n, dn in ws.defined_names.items():
                local[n.lower()] = list(dn.destinations)
        except Exception:  # noqa: BLE001
            pass
        for row in ws.iter_rows(max_row=min(ws.max_row, MAX_SCAN_ROWS)):
            for c in row:
                v = c.value
                if not (isinstance(v, str) and v.startswith("=")):
                    continue
                try:
                    refs = formula_refs(v)
                except Exception:  # noqa: BLE001 - a formula the tokenizer cannot read: not judged here
                    continue
                cells, judged = [], bool(refs)
                for sh, ref in refs:
                    if sh is None and not _CELLREF.match(ref):
                        targets = local.get(ref.lower()) or names.get(ref.lower())
                        if not targets:
                            judged = False
                            break
                    else:
                        targets = [(sh or ws.title, ref)]
                    for tsh, tref in targets:
                        got = _ref_cells(wb[tsh], tref) if tsh in wb.sheetnames else None
                        if got is None:
                            judged = False
                            break
                        cells += [(tsh, x) for x in got]
                    if not judged:
                        break
                if not judged or not cells:
                    continue
                where = f"{ws.title}!{c.coordinate}"
                if all(_blank(x) for _, x in cells):
                    out.append({"cell": where, "formula": v, "kind": "check_refs_empty", "detail": (
                        f"{v[:80]} points only at empty cells, so it reads 0 and the check shows OK without checking "
                        "anything. Reference the tab that holds the figures, e.g. ='Budget'!B12-SUM('Budget'!B10:G10) "
                        "(B.add_check(..., sheet='Budget') does this)")})
                elif all(tsh == ws.title for tsh, _ in cells) and not any(
                        isinstance(x.value, str) and x.value.startswith("=") for _, x in cells if x is not None):
                    out.append({"cell": where, "formula": v, "kind": "check_refs_own_sheet", "detail": (
                        f"{v[:80]} only reads typed values on the {ws.title} tab itself, not the figures it is meant "
                        "to check. Reference the tab that holds them, e.g. ='Budget'!B12-SUM('Budget'!B10:G10)")})
    return out


def _row_label(ws, c) -> str:
    """The plain-words label of a check cell: the nearest text to its left that is not a status word."""
    for col in range(c.column - 1, 0, -1):
        lab = ws.cell(row=c.row, column=col).value
        if isinstance(lab, str) and lab.strip() and not lab.startswith("=") and lab.strip().upper() not in FAILING \
                and lab.strip().upper() != "OK":
            return lab.strip()
    return c.coordinate


def _failing(value) -> bool:
    return value is False or (isinstance(value, str) and value.strip().upper() in FAILING)


def check(xlsx_path: str, expect_sheets: list[str] | None = None, allow_hardcoded: bool = False) -> dict:
    """Returns {"issues": [...], "stats": {...}}. issues empty == pass."""
    issues = []
    wb_f = load_workbook(xlsx_path, data_only=False)
    try:
        wb_v = recalc_values(xlsx_path)
    except RuntimeError as e:
        from meshpool_harness.office_runtime import is_unavailable
        if not is_unavailable(e):
            raise
        wb_v = None                                  # no LibreOffice: structure checks only
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        wb_v = None                                  # recalculation failed: structure checks, and say so
        issues.append({"check": "recalc_failed", "cell": "",
                       "detail": f"Could not recalculate the workbook to check its values ({type(e).__name__}); "
                                 "formulas and structure were checked, values were not."})
    n_formula = n_numeric_const = n_ph = 0
    for si, ws in enumerate(wb_f.worksheets):
        wv = None
        if wb_v is not None:                          # LibreOffice truncates long sheet names: fall back to position
            wv = wb_v[ws.title] if ws.title in wb_v.sheetnames else wb_v.worksheets[si]
        is_calc = bool(CALC_SHEETS.search(ws.title)) and not INPUT_SHEETS.search(ws.title)
        is_checks = bool(CHECK_SHEETS.search(ws.title))
        for row in ws.iter_rows(max_row=min(ws.max_row, MAX_SCAN_ROWS)):   # huge data tabs are sampled
            for c in row:
                v = c.value
                if v is None:
                    continue
                cv = wv[c.coordinate].value if wv is not None else None
                if isinstance(v, str) and v.startswith("="):
                    n_formula += 1
                    ph = PLACEHOLDER.findall(v)
                    if ph:                    # a Python template written without formatting: Excel sees {ocf_row}
                        n_ph += 1
                        if n_ph <= 5:
                            issues.append({"check": "unformatted_placeholder", "cell": f"{ws.title}!{c.coordinate}",
                                           "detail": f"{v[:60]} holds the literal {ph[0]}: the formula was written "
                                                     "from a plain string, not an f-string, so Excel shows #N/A. "
                                                     "Write it with f'...' so the row number goes in."})
                        continue
                    if cv is None and wv is not None and '""' not in v:   # ="" results read back as None
                        issues.append({"check": "formula_no_value", "cell": f"{ws.title}!{c.coordinate}",
                                       "detail": v[:80]})
                    if isinstance(cv, str) and cv.startswith(ERRORS):
                        kind = "circular_ref" if "522" in cv or "523" in cv else "formula_error"
                        issues.append({"check": kind, "cell": f"{ws.title}!{c.coordinate}", "detail": f"{cv} <- {v[:80]}"})
                    elif is_checks and _failing(cv):        # read from the recalculated copy, never the cached value
                        issues.append({"check": "check_not_tying", "cell": f"{ws.title}!{c.coordinate}",
                                       "detail": f"'{_row_label(ws, c)}' shows {cv} after recalculation: the figures "
                                                 f"it compares do not tie ({v[:80]})"})
                elif isinstance(v, str) and v.strip().startswith(ERRORS):
                    issues.append({"check": "error_literal", "cell": f"{ws.title}!{c.coordinate}", "detail": v})
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    n_numeric_const += 1
                    # numbers typed into calculation/output sheets are the classic hardcode failure
                    is_label = bool(c.font and c.font.bold) or (c.fill is not None and c.fill.fgColor is not None
                                                                and str(c.fill.fgColor.rgb).endswith("1B2333"))
                    if is_calc and not allow_hardcoded and c.column > 1 and not is_label:
                        issues.append({"check": "hardcoded_number", "cell": f"{ws.title}!{c.coordinate}",
                                       "detail": f"{v} typed into a calculation sheet; reference Inputs instead"})
    issues += [{"check": h["kind"], "cell": h["cell"], "detail": h["detail"]} for h in hollow_checks(wb_f)]
    if n_ph > 5:
        issues.append({"check": "unformatted_placeholder", "cell": "", "detail": f"{n_ph} formulas in all hold "
                       "{name} placeholders; fix the code that writes them (use f-strings), then save again"})
    names = list(wb_f.defined_names.keys()) if hasattr(wb_f.defined_names, "keys") else [d.name for d in wb_f.defined_names.definedName]
    if expect_sheets:
        missing = [s for s in expect_sheets if s not in wb_f.sheetnames]
        if missing:
            issues.append({"check": "missing_sheets", "cell": "", "detail": missing})
    if n_formula == 0:
        issues.append({"check": "no_formulas", "cell": "", "detail": "workbook has no live formulas"})
    checks_status = None
    if wb_v is not None and "Checks" in wb_f.sheetnames:
        try:                                         # the master check cell, read from the recalculated values
            checks_status = read_ref(wb_v, "Checks!F3")
        except Exception:  # noqa: BLE001
            pass
    return {"issues": issues, "stats": {"sheets": wb_f.sheetnames, "formulas": n_formula,
                                        "numeric_constants": n_numeric_const, "named_ranges": names,
                                        "checks_status": checks_status, "recalculated": wb_v is not None}}


def value(xlsx_path: str, ref: str):
    """Computed value of 'Sheet!A1' or a named range after LibreOffice recalc (for gold-answer checks)."""
    try:
        wb = recalc_values(xlsx_path)
    except RuntimeError as e:
        from meshpool_harness.office_runtime import is_unavailable
        if is_unavailable(e):
            return None                              # no LibreOffice: value unknown, not a failure
        raise
    return read_ref(wb, ref)


def read_ref(wb, ref: str):
    """Value of 'Sheet!A1' or a defined name in an already-loaded (recalculated) workbook."""
    if "!" in ref:
        sh, cell = ref.split("!", 1)
        return wb[sh.strip("'")][cell.replace("$", "")].value
    dn = wb.defined_names[ref]
    (sh, cell), = list(dn.destinations)
    return wb[sh][cell.replace("$", "")].value


def _blue(c) -> bool:
    col = c.font.color if c.font is not None else None
    return bool(col is not None and col.type == "rgb" and str(col.rgb).upper().endswith("0000FF"))


def missing_sources(xlsx_path: str) -> list[str]:
    """Valuation workbooks (a Comps, Precedents, DCF or Valuation tab): every typed input (blue number) must carry a
    Source in its table's Source column (the assumption book on Inputs; the peer/deal rows on Comps/Precedents).
    Returns one entry per input row without a source, e.g. "Inputs!C7 (Risk-free rate (%))"."""
    wb = load_workbook(xlsx_path)
    if not any(FINANCE_SHEETS.search(s) for s in wb.sheetnames):
        return []
    out = []
    for ws in wb.worksheets:
        if re.search(r"check|cover", ws.title, re.I):
            continue
        is_inputs = bool(INPUT_SHEETS.search(ws.title))
        headers: dict[int, int | None] = {}                 # header row -> Source column (None: table has none)
        for row in ws.iter_rows():
            for c in row:
                if c.fill is not None and c.fill.fgColor is not None and str(c.fill.fgColor.rgb).endswith("1B2333"):
                    headers.setdefault(c.row, None)
                    if isinstance(c.value, str) and c.value.strip().lower().startswith("source"):
                        headers[c.row] = c.column
        done = set()
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if c.row in done or not (isinstance(v, (int, float)) and not isinstance(v, bool)) or not _blue(c):
                    continue
                label = ws.cell(row=c.row, column=1).value or ""
                above = [h for h in headers if h < c.row]
                sc = headers[max(above)] if above else None
                if sc is None:
                    if is_inputs:
                        out.append(f"{ws.title} row {c.row} ({label}): no Source column; use B.assumption_book")
                        done.add(c.row)
                    continue
                s = ws.cell(row=c.row, column=sc).value
                if s is None or not str(s).strip():
                    out.append(f"{ws.title}!{ws.cell(row=c.row, column=sc).coordinate} ({label})")
                done.add(c.row)
    return out


METHOD_FAMILIES = {   # valuation method -> names or sheet titles that show the workbook used it
    "DCF": (r"^(\w+_)?dcf_per_share$", r"^dcf"),
    "trading comps": (r"^comps_", r"^comps|trading comp"),
    "precedent transactions": (r"^prec_", r"^precedent"),
    "sum of the parts": (r"^sotp_", r"^sotp|sum of the parts"),
    "NAV": (r"^nav_", r"^nav\b|net asset"),
    "dividend discount": (r"^ddm_", r"^ddm|dividend discount"),
    "excess return": (r"^(er|excess_return)_", r"^excess return|residual income"),
    "LBO": (r"^lbo_", r"^lbo"),
}


def _labels(ws, col: int = 1) -> list[str]:
    return [str(c.value).strip() for c in ws.iter_cols(min_col=col, max_col=col).__next__() if c.value is not None]


def model_gaps(xlsx_path: str) -> list[str]:
    """Integrated-model and valuation rules the engine enforces before a finance workbook is accepted:
    - a DCF takes its cash flows from integrated statements (a balance sheet in the workbook);
    - integrated statements carry a balance-sheet-balances check and a cash-tie check on the Checks tab;
    - a DCF or integrated model has base / bull / bear cases behind one switch (names scenario, *_bull, *_bear);
    - more than one valuation method needs the weighting table: weights that sum to 100% (a check), a weighted
      value per share, and a justification on every weighted method.
    Returns one message per gap."""
    wb = load_workbook(xlsx_path)
    names = {k.lower() for k in wb.defined_names.keys()}
    titles = [s.lower() for s in wb.sheetnames]
    out = []
    has_bs = any(lab.lower() == "total liabilities and equity" for ws in wb.worksheets for lab in _labels(ws))
    used = [fam for fam, (nre, tre) in METHOD_FAMILIES.items()
            if any(re.search(nre, n) for n in names) or any(re.search(tre, t) for t in titles)]
    dcf = "DCF" in used
    checks = [lab.lower() for lab in _labels(wb["Checks"])] if "Checks" in wb.sheetnames else []
    if dcf and not has_bs:
        out.append("DCF without integrated statements: build the income statement, balance sheet and cash flow with "
                   "B.three_statement (drivers, working-capital days, PP&E roll-forward, debt, equity) and take free "
                   "cash flow from them with B.dcf_from_statements")
    if has_bs:
        if not any("balance sheet balances" in c for c in checks):
            out.append("No balance-sheet-balances check on the Checks tab (total assets minus total liabilities and "
                       "equity, every year)")
        if not any(re.search(r"cash (ties|equals|flow closing cash)|closing cash equals", c) for c in checks):
            out.append("No cash-tie check on the Checks tab (cash flow closing cash equals balance sheet cash)")
    if (dcf or has_bs) and not ("scenario" in names and any(n.endswith("_bull") for n in names)
                                and any(n.endswith("_bear") for n in names)):
        out.append("No scenarios: put the drivers in B.scenario_book (base / bull / bear with one switch named "
                   "scenario) and show all three with B.scenario_summary")
    if len(used) >= 2:
        if "weighted_value_per_share" not in names:
            out.append(f"{len(used)} valuation methods ({', '.join(used)}) but no weighting: use B.valuation_summary "
                       "to decide which methods fit and weight them, with the weights as editable inputs")
        else:
            if not any("weights" in c and "100%" in c for c in checks):
                out.append("No check that the valuation weights sum to 100%")
            out += _weighting_gaps(wb)
    return out


def _weighting_gaps(wb) -> list[str]:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value == "Weight used (%)":
                    hdr = c.row
                    jc = next((x.column for x in ws[hdr] if isinstance(x.value, str)
                               and x.value.lower().startswith("source / justification")), None)
                    out = []
                    r = hdr + 1
                    while ws.cell(row=r, column=1).value not in (None, "Total"):
                        j = ws.cell(row=r, column=jc).value if jc else None
                        if not (isinstance(j, str) and len(j.strip()) >= 20):
                            out.append(f"Weighted method '{ws.cell(row=r, column=1).value}' has no justification")
                        r += 1
                    return out
    return ["Weighted value per share found but no weighting table (Weight used (%) column)"]
