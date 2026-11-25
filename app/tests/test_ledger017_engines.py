"""Ledger 0.0.17 gap rows for the office engines and the deck engine (engines/sheets, engines/credit,
engines/research, engines/data, engines/browser, engines/icons, engines/forge): each test runs the audit's trigger
against the real code and fails if the fix is reverted. No network, no model, no LibreOffice needed (the one
recalculation test is skipped without it)."""
from __future__ import annotations

import ast
import builtins
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import types
import urllib.error
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


# ---------------------------------------------------------------------------------------------------- helpers
@contextlib.contextmanager
def windows_locale(locale_enc: str = "cp1252"):
    """Text files opened without an encoding get `encoding`, as on a Windows machine whose locale is cp1252.
    open(), io.open() and pathlib (which calls io.open, or on 3.10 its accessor) all go through the wrapper."""
    real = io.open

    def fake(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = locale_enc
        return real(file, mode, buffering, encoding, errors, newline, closefd, opener)
    saved = [(builtins, "open", builtins.open), (io, "open", io.open), (io, "text_encoding", io.text_encoding)]
    acc = getattr(pathlib, "_NormalAccessor", None)            # Python 3.10: Path.open goes through the accessor
    if acc is not None and "open" in vars(acc):
        saved.append((acc, "open", vars(acc)["open"]))
        acc.open = staticmethod(fake)
    builtins.open = io.open = fake
    io.text_encoding = lambda enc, stacklevel=2: "locale" if enc is None else enc     # what it says outside UTF-8 mode
    try:
        yield
    finally:
        for obj, k, v in saved:
            setattr(obj, k, v)


def test_windows_locale_helper_really_breaks_an_unmarked_write(tmp_path):
    """The helper itself: without encoding= an Arabic name fails to write, with utf-8 it is kept."""
    with windows_locale():
        with pytest.raises(UnicodeEncodeError):
            (tmp_path / "a.md").write_text("شركة")
        (tmp_path / "b.md").write_text("شركة", encoding="utf-8")
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "شركة"


def _text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with real text (Helvetica), small enough to write by hand. The page content is compressed, so
    the text is not in the file's raw bytes: only a real PDF reader finds it."""
    import zlib
    stream = "BT /F1 12 Tf 20 180 Td 14 TL " + " ".join(
        "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for ln in lines) + " ET"
    body = zlib.compress(stream.encode("latin-1"))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] /Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(body)} /Filter /FlateDecode >>\nstream\n".encode() + body + b"\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


CSV = """Acme Manufacturing (USD millions),FY2024,FY2025
Net sales,100,110
Operating income,15,17
Depreciation,5,6
Interest expense,(2),(2.5)
Income taxes,(3),(3.4)
Net income,10,11.1
Capital expenditures,(4),(5)
Dividends paid,(1),(2)
Cash,8,6
Total current assets,32,37
Total assets,80,87
Line of credit,3,4
Current portion of long-term debt,4,4
Total current liabilities,16,18
"Long-term debt, net of current portion",20,16
Total liabilities,38,36
Total stockholders' equity,42,51
"""


# ---------------------------------------------------------------------------------------------------- PA-forge-07
def _fake_soffice(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in soffice that records its arguments and its pid, then hangs as LibreOffice does on a bad file."""
    log = tmp_path / "soffice.log"
    exe = tmp_path / "soffice"
    exe.write_text(f"#!/bin/sh\necho \"$$ $*\" >> '{log}'\nexec sleep 60\n")
    exe.chmod(0o755)
    return exe, log


@pytest.mark.skipif(os.name == "nt", reason="the stand-in soffice is a POSIX shell script")
def test_kb_thumbnails_kill_a_hung_libreoffice_and_use_a_file_uri_profile(tmp_path, monkeypatch):
    """PA-forge-07: `kb ingest` when LibreOffice hangs on a file. The hung process is killed, the batch moves on, and
    the profile is passed as Path.as_uri() (a valid file URI on every system), not as "file://" + a raw path."""
    pytest.importorskip("pypdfium2")
    from engines.forge import common as FC
    from engines.forge import kb_ingest as K
    exe, log = _fake_soffice(tmp_path)
    monkeypatch.setattr(FC, "find_soffice", lambda: str(exe))
    deck = tmp_path / "one.pptx"
    deck.write_bytes(b"x")
    t0 = time.time()
    assert K.thumbnails([deck], tmp_path / "thumbs", timeout=1, single_timeout=1) == {}
    assert time.time() - t0 < 8
    rows = log.read_text().splitlines()
    assert len(rows) == 2                                   # the chunk, then the file alone with the short timeout
    for row in rows:
        pid, args = row.split(" ", 1)
        prof = next(a for a in args.split() if a.startswith("-env:UserInstallation="))[len("-env:UserInstallation="):]
        assert prof.startswith("file:///") and prof.endswith("/profile")
        assert prof == Path(prof[len("file://"):]).as_uri()
        with pytest.raises(ProcessLookupError):           # the hung process is gone, not left running
            os.kill(int(pid), 0)


def test_kb_thumbnails_on_windows_kill_the_process_tree_with_taskkill(tmp_path, monkeypatch):
    """PA-forge-07 (Windows branch, simulated): no start_new_session (POSIX only), and a hang is ended with
    taskkill /F /T so LibreOffice's child process dies too; os.killpg is never used."""
    pytest.importorskip("pypdfium2")
    from engines.forge import common as FC
    from engines.forge import kb_ingest as K
    monkeypatch.setattr(FC, "find_soffice", lambda: "C:/Program Files/LibreOffice/program/soffice.exe")
    popen_kw, runs, killed = [], [], []

    class P:
        pid = 4321

        def __init__(self, cmd, **kw):
            popen_kw.append(kw)
            self.n = 0

        def wait(self, timeout=None):
            self.n += 1
            if self.n == 1:
                raise subprocess.TimeoutExpired("soffice", timeout)
            return 1

        def kill(self):
            killed.append(self.pid)

    fake_sub = types.SimpleNamespace(Popen=P, run=lambda cmd, **kw: runs.append(cmd), DEVNULL=subprocess.DEVNULL,
                                     TimeoutExpired=subprocess.TimeoutExpired)

    class _OS:
        name = "nt"

        def __getattr__(self, k):
            if k == "killpg":
                raise AssertionError("os.killpg does not exist on Windows")
            return getattr(os, k)
    monkeypatch.setattr(K, "subprocess", fake_sub)
    monkeypatch.setattr(K, "os", _OS())
    deck = tmp_path / "one.pptx"
    deck.write_bytes(b"x")
    assert K.thumbnails([deck], tmp_path / "thumbs", timeout=1, single_timeout=1) == {}
    assert popen_kw and all("start_new_session" not in kw for kw in popen_kw)
    assert runs == [["taskkill", "/F", "/T", "/PID", "4321"]] * 2
    assert killed == [4321, 4321]


# ---------------------------------------------------------------------------------------------------- PA-forge-12
def test_render_without_libreoffice_adds_no_label_check_error(tmp_path, monkeypatch):
    """PA-forge-12: render() on a machine without LibreOffice. The label check is skipped silently: no
    "label check skipped" line in errors. (_relabel is made to fail as it would with no soffice.)"""
    from engines.forge import common as FC
    from engines.forge import render as R

    def no_soffice(*a, **k):
        raise RuntimeError("LibreOffice is not installed")
    monkeypatch.setattr(R, "_relabel", no_soffice)
    monkeypatch.setattr(FC, "find_soffice", lambda: None)
    res = R.render([{"archetype": "cover", "title": "Quarterly review", "subtitle": "Board pack"}],
                   str(tmp_path / "a.pptx"))
    assert res["slides"] == 1
    assert not [e for e in res["errors"] if "label check" in e], res["errors"]
    monkeypatch.setattr(FC, "find_soffice", lambda: "/usr/bin/soffice")        # with soffice the check does run
    res = R.render([{"archetype": "cover", "title": "Quarterly review"}], str(tmp_path / "b.pptx"))
    assert "label check skipped: RuntimeError" in res["errors"]


# ---------------------------------------------------------------------------------------------------- PA-engines-02
def test_formula_returning_empty_string_is_not_formula_no_value(tmp_path, monkeypatch):
    """PA-engines-02: Calc!B4 =IF(A1>10,"big","") with A1=5 reads back as None after recalculation. That is a
    harmless empty text, not formula_no_value; a formula with no value and no "" is still reported."""
    from openpyxl import Workbook
    from engines.sheets import qa
    wb = Workbook()
    ws = wb.active
    ws.title = "Calc"
    ws["A1"] = 5
    for r in (2, 3, 4):
        ws[f"B{r}"] = f'=IF(A1>10,"big","")'
    ws["C1"] = "=A1*2"
    p = tmp_path / "m.xlsx"
    wb.save(p)
    vals = Workbook()
    vs = vals.active
    vs.title = "Calc"
    vs["A1"] = 5                                         # B2:B4 and C1 read back as None, as LibreOffice gives them
    monkeypatch.setattr(qa, "recalc_values", lambda path: vals)
    issues = qa.check(str(p), allow_hardcoded=True)["issues"]
    flagged = {i["cell"] for i in issues if i["check"] == "formula_no_value"}
    assert flagged == {"Calc!C1"}, issues


# ---------------------------------------------------------------------------------------------------- PA-engines-07
def test_recipe_failed_note_reaches_the_answer(monkeypatch):
    """PA-engines-07: a recipe applies but its build fails, so the agent builds the workbook. The note saying so is
    added to the final answer the user sees."""
    from engines.sheets import engine as E
    from engines.sheets import pipeline as PL
    monkeypatch.setattr(E, "dossier_brief", lambda *a, **k: "")
    monkeypatch.setattr(E, "review_enabled", lambda *a, **k: False)
    monkeypatch.setattr(PL, "applies", lambda task: "dcf")

    def broken(*a, **k):
        raise KeyError("wacc")
    monkeypatch.setattr(PL, "build", broken)
    monkeypatch.setattr(E, "run", lambda *a, **k: types.SimpleNamespace(
        ok=True, answer="Built model.xlsx.", artifacts=[], evidence=[], usage={}, turns=3, reason=None))
    monkeypatch.setattr(E, "tidy", lambda ws, arts: arts)
    ws = types.SimpleNamespace()
    out = E._solve("Build a DCF for Acme", "/tmp", ws, None, "m", "t1", {}, 10, None, None, False, None, None)
    assert out["answer"].startswith("Built model.xlsx.")
    assert "The dcf recipe could not build it (KeyError: 'wacc'); the agent built it." in out["answer"]


# ---------------------------------------------------------------------------------------------------- PA-engines-08
_SCAN_DIRS = ("sheets", "data", "research", "credit", "review")


def _unmarked_text_io(root: Path) -> list[str]:
    """Calls in the office engines that read or write text with the locale's encoding: open() in text mode, or
    Path.read_text / write_text / open, with no encoding= argument."""
    bad = []
    for p in sorted(f for d in _SCAN_DIRS for f in (root / d).rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or any(k.arg == "encoding" for k in n.keywords):
                continue
            f, mode = n.func, None
            if isinstance(f, ast.Name) and f.id == "open":
                if len(n.args) >= 2:
                    mode = n.args[1].value if isinstance(n.args[1], ast.Constant) else "?"
                mode = next((k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)),
                            mode)
                if mode == "?" or (mode and "b" in mode):
                    continue
            elif isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
                pass
            else:
                continue
            bad.append(f"{p.relative_to(root)}:{n.lineno}")
    return bad


def test_office_engine_text_files_are_all_utf8():
    """PA-engines-08: every text file the office engines read or write names its encoding (utf-8), so a workbook
    label like 株式会社 does not fail on a Windows machine whose locale is cp1252. This is a source scan over the
    engines the audit lists (sheets, data, research, credit, review): Windows locales cannot be switched here."""
    assert _unmarked_text_io(APP / "engines") == []


def test_review_round_digest_keeps_japanese_text_on_a_cp1252_machine(tmp_path, monkeypatch):
    """PA-engines-08: the workbook review writes digest.txt with utf-8 (engines/sheets/engine.py), so a label like
    株式会社 survives a cp1252 locale."""
    from engines.sheets import engine as E
    import engines.sheets.review_digest as RD
    monkeypatch.setattr(RD, "digest", lambda *a, **k: "株式会社 revenue 100")
    import engines.review.personas as PS
    monkeypatch.setattr(PS, "panel", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model in tests")))
    root = tmp_path / "w"
    root.mkdir()
    wbk = root / "model.xlsx"
    wbk.write_bytes(b"PK")
    rv = E.WorkbookReview.__new__(E.WorkbookReview)
    rv.ws = types.SimpleNamespace(root=root, run_dir=root / ".run")
    rv.rounds, rv.budget, rv.models = [], 1000, []
    with windows_locale():
        try:
            rv.run_round(str(wbk), {})
        except Exception:  # noqa: BLE001 - only the digest file matters here
            pass
    got = (root / ".run" / "review" / "round1" / "digest.txt").read_text(encoding="utf-8")
    assert got == "株式会社 revenue 100"


# ---------------------------------------------------------------------------------------------------- PA-engines-09
class _HTTP:
    def __init__(self, codes):
        self.codes, self.calls = list(codes), 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        code = self.codes.pop(0)
        if code == 0:
            raise urllib.error.URLError("connection reset")
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)


def test_sec_lookup_does_not_retry_a_404(monkeypatch):
    """PA-engines-09: fetch_companyfacts("9999999999") gets 404 from the SEC. A 4xx other than 429 will not change on
    retry: one request, no sleeps."""
    from engines.sheets import spreading as SP
    http, sleeps = _HTTP([404, 404, 404]), []
    monkeypatch.setattr(SP.urllib.request, "urlopen", http)
    monkeypatch.setattr(SP.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="SEC request failed"):
        SP.fetch_companyfacts("9999999999", user_agent="Test analyst@example.com")
    assert http.calls == 1 and sleeps == []


def test_sec_lookup_still_retries_429_and_network_errors(monkeypatch):
    """PA-engines-09: a 429 (rate limit) or a dropped connection is still retried with a back-off."""
    from engines.sheets import spreading as SP
    http, sleeps = _HTTP([429, 0, 403]), []
    monkeypatch.setattr(SP.urllib.request, "urlopen", http)
    monkeypatch.setattr(SP.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError):
        SP.fetch_companyfacts("320193", user_agent="Test analyst@example.com")
    assert http.calls == 3 and len(sleeps) == 2


# ---------------------------------------------------------------------------------------------------- credit engine
@pytest.fixture
def credit_csv(tmp_path):
    p = tmp_path / "acme.csv"
    p.write_text(CSV, encoding="utf-8")
    return p


def test_credit_without_an_amount_refuses_instead_of_approving_zero(tmp_path, credit_csv, monkeypatch):
    """PA-engines-14: solve("need a loan", files=[table.csv]) used $0, 7% and 7 years and said Approve. It now asks
    for the amount and writes nothing."""
    from engines.credit import engine as CE
    monkeypatch.setattr("meshpool_harness.providers.configured_model", lambda m=None: m or "test-model")
    wd = tmp_path / "w"
    out = CE.solve("need a loan", workdir=str(wd), task_id="t", files=[str(credit_csv)], client=None)
    assert out["ok"] is False and out["artifacts"] == []
    assert "State the loan amount" in out["reason"] and "Approve" not in out["reason"]
    assert not list(wd.glob("credit_*"))


def test_credit_defaulted_rate_and_term_are_said_to_be_assumptions(tmp_path, credit_csv, monkeypatch):
    """PA-engines-14: with an amount but no rate or term, the 7% and 7 years are marked as assumptions in the
    answer and in the workbook's source column."""
    from openpyxl import load_workbook
    from engines.credit import engine as CE
    monkeypatch.setattr("meshpool_harness.providers.configured_model", lambda m=None: m or "test-model")
    monkeypatch.setenv("MH_CREDIT_COMMENTARY", "0")
    out = CE.solve("Credit memo for a $10m term loan for Acme", workdir=str(tmp_path / "w"), task_id="t",
                   files=[str(credit_csv)], client=None)
    assert out["ok"] is True, out
    assert "Assumed because the request did not state them: interest rate 7%, 7-year amortisation." in out["answer"]
    xl = next(a for a in out["artifacts"] if a.endswith(".xlsx"))
    rows = {r[0].value: [c.value for c in r] for r in load_workbook(xl)["Inputs"].iter_rows() if r[0].value}
    src = "Assumption: not stated in the request (replace with the term sheet)"
    assert src in rows["Interest rate (% per year)"] and src in rows["Amortisation (years, level payments)"]
    assert src not in rows[next(k for k in rows if str(k).startswith("Loan amount"))]


def _fin_without_da(tmp_path):
    from engines.credit import analysis as A
    p = tmp_path / "noda.csv"
    p.write_text("\n".join(ln for ln in CSV.splitlines() if not ln.startswith("Depreciation")) + "\n", encoding="utf-8")
    return A.from_table(str(p), name="Acme")


def test_credit_ebitda_without_da_is_operating_income_in_python(tmp_path):
    """PA-engines-18: a CSV with no D&A row. The analysis treats D&A as 0 (EBITDA = operating income), as the
    workbook does, instead of EBITDA None, capacity 0 and "Decline: exceeds debt capacity"."""
    from engines.credit import analysis as A
    fin = _fin_without_da(tmp_path)
    assert fin.v("FY2025", "da") is None
    assert A.ebitda(fin, "FY2025") == 17
    r = A.analyse(fin, A.Loan(amount=10, rate=0.08, amort_years=5), A.Policy())
    pf_debt = 4 + 4 + 16 + 10                                    # line of credit, current and long-term debt, the loan
    assert r["history"][-1]["ebitda"] == 17 and r["pro_forma"]["leverage"] == pytest.approx(pf_debt / 17)
    assert r["capacity"]["by_leverage"] is not None


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed to recalculate the workbook")
def test_credit_workbook_and_memo_agree_without_da(tmp_path, monkeypatch):
    """PA-engines-18: the same run's workbook, recalculated, shows the same EBITDA and capacity as the analysis."""
    from meshpool_harness import office_runtime
    monkeypatch.setenv("MH_ALLOW_SYSTEM_SOFFICE", "1")            # a test may recalculate with the system copy
    if not office_runtime.find_soffice():
        pytest.skip("no usable LibreOffice here")
    from openpyxl import load_workbook
    from engines.credit import analysis as A
    from engines.credit import workbook as W
    from engines.sheets import qa
    fin = _fin_without_da(tmp_path)
    loan, pol = A.Loan(amount=10, rate=0.08, amort_years=5), A.Policy()
    r = A.analyse(fin, loan, pol)
    p = tmp_path / "credit.xlsx"
    out = W.build(fin, loan, pol, str(p))
    wb = load_workbook(qa.recalc(str(p), str(tmp_path / "rc")), data_only=True)
    C, K = wb["Cash flow"], wb["Capacity"]
    for i, c in enumerate(out["cols"]):
        assert C[f"{c}{out['cash_flow_rows']['ebitda']}"].value == pytest.approx(r["history"][i]["ebitda"])
    assert K[out["capacity_cells"]["capacity"]].value == pytest.approx(r["capacity"]["max_loan"])


@pytest.mark.parametrize("loan_kw", [{"amount": 0}, {"amount": 10, "floating": False}])
def test_credit_workbook_has_no_rate_stress_row_for_fixed_or_zero_loans(tmp_path, credit_csv, loan_kw):
    """PA-engines-19: Loan(amount=0) or a fixed-rate loan has no rate stress in the analysis, and now none in the
    workbook either; a floating loan has it in both."""
    from openpyxl import load_workbook
    from engines.credit import analysis as A
    from engines.credit import workbook as W
    fin = A.from_table(str(credit_csv), name="Acme")

    def rows(loan):
        p = tmp_path / f"c{time.time_ns()}.xlsx"
        W.build(fin, loan, A.Policy(), str(p))
        k = load_workbook(p)["Capacity"]
        xl = [c.value for c in k["A"] if isinstance(c.value, str) and "Rate stress" in c.value]
        py = [s for s in A.analyse(fin, loan, A.Policy())["stress"] if s["case"].startswith("Rate")]
        return xl, py
    xl, py = rows(A.Loan(rate=0.08, amort_years=5, **loan_kw))
    assert xl == [] and py == []
    xl, py = rows(A.Loan(amount=10, rate=0.08, amort_years=5))
    assert xl == ["Rate stress on the new loan"] and len(py) == 1


# ---------------------------------------------------------------------------------------------------- research
REPORT = "# Answer\n\nPrices rose to €5 per unit, in “quoted text” form [S1].\n"


def test_research_finish_check_reads_the_report_as_utf8(tmp_path, monkeypatch):
    """PA-engines-23: on Windows, a report containing “quoted text” or € is read with utf-8 (not the locale), so
    the check sees the text the agent wrote."""
    from engines.research import engine as RE
    from engines.research import verify as V
    from engines.research.library import Corpus
    c = Corpus(str(tmp_path))
    c.add_text("Price note", "Prices rose to €5 per unit, in “quoted text” form. " * 4, url="https://example.com/a")
    c.save()
    (tmp_path / "report.md").write_text(REPORT, encoding="utf-8")
    seen = []
    monkeypatch.setattr(V, "check", lambda md, corpus: seen.append(md) or [])
    ws = types.SimpleNamespace(root=tmp_path)
    with windows_locale():
        assert RE.finish_check("The answer [S1].", ["report.md"], ws) is None
    assert seen == [REPORT]


def test_research_check_cli_reads_the_report_as_utf8(tmp_path, monkeypatch, capsys):
    """PA-engines-23: `python -m engines.research.check report.md` reads the report with utf-8 too."""
    import runpy
    from engines.research import verify as V
    from engines.research.library import Corpus
    c = Corpus(str(tmp_path))
    c.add_text("Price note", "Prices rose to €5 per unit, in “quoted text” form. " * 4, url="https://example.com/a")
    c.save()
    rep = tmp_path / "report.md"
    rep.write_text(REPORT, encoding="utf-8")
    seen = []
    monkeypatch.setattr(V, "check", lambda md, corpus: seen.append(md) or [])
    monkeypatch.setattr(sys, "argv", ["check", str(rep)])
    sys.modules.pop("engines.research.check", None)
    with windows_locale():
        runpy.run_module("engines.research.check", run_name="__main__")
    assert seen == [REPORT]
    assert capsys.readouterr().out.startswith("PASS")


def test_dossier_md_keeps_an_arabic_company_name(tmp_path, monkeypatch):
    """PA-engines-24: a brief about an EGX company with an Arabic name, on a cp1252 Windows machine. dossier.md and
    dossier.json are written with utf-8, so the dossier is not lost."""
    from engines.forge import research as RS
    from engines.research import dossier as DO
    name = "شركة مصر للألومنيوم"
    monkeypatch.setattr(DO, "identify", lambda *a, **k: {"companies": [{"name": name, "short": name}],
                                                         "decision": "قرار الاستثمار", "questions": []})
    monkeypatch.setattr(RS, "investigate", lambda qs, facts, **k: {
        "facts": [{"id": "F1", "text": f"{name}: الإيرادات 10 مليار جنيه", "question": qs[0]["question"]}],
        "questions": [{"question": qs[0]["question"], "status": "ok", "facts": ["F1"]}]})
    monkeypatch.setenv("MH_RESEARCH_MARKET", "0")
    with windows_locale():
        d = DO.build_dossier(f"Should we invest in {name}?", [], client=None, models=[], out_dir=str(tmp_path / "d"))
    md = (tmp_path / "d" / "dossier.md").read_text(encoding="utf-8")
    assert name in md and "الإيرادات 10 مليار جنيه" in md
    assert json.loads((tmp_path / "d" / "dossier.json").read_text(encoding="utf-8"))["facts"] == d["facts"]


# ---------------------------------------------------------------------------------------------------- PA-engines-26
def test_data_engine_quotes_from_pdf_and_html_sources_are_not_fabricated(tmp_path, monkeypatch):
    """PA-engines-26: an interview .txt mixed with a .pdf transcript and an .html page. Every readable attachment is
    loaded as a quote source (docread), so a verbatim quote from the PDF or the HTML passes the quote check."""
    pytest.importorskip("pypdfium2")
    from engines.data import engine as DE
    from engines.data.checks import check_quotes
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_DOC_CACHE", "0")
    monkeypatch.setattr("meshpool_harness.providers.configured_model", lambda m=None: m or "test-model")
    txt = tmp_path / "p1.txt"
    txt.write_text("P1: The onboarding took far too long for our team.\n", encoding="utf-8")
    pdf = tmp_path / "p2.pdf"
    pdf.write_bytes(_text_pdf(["P2: Support answered every ticket within an hour."]))
    html = tmp_path / "p3.html"
    html.write_text("<html><body><p>P3: Pricing &amp; support were the main reasons we stayed.</p></body></html>",
                    encoding="utf-8")
    got = {}
    monkeypatch.setattr(DE, "make_check", lambda sources: got.update(sources) or (lambda *a: None))
    monkeypatch.setattr(DE, "run", lambda *a, **k: types.SimpleNamespace(ok=True, answer="", artifacts=[],
                                                                       evidence=[], turns=1, reason=None, usage={}))
    DE.solve("Code the interviews", workdir=str(tmp_path / "w"), task_id="t",
             files=[str(txt), str(pdf), str(html)], client=object())
    assert set(got) == {"p1.txt", "p2.pdf", "p3.html"}
    answer = ('Users praised support: "Support answered every ticket within an hour." (P2). Price kept them: '
              '"Pricing & support were the main reasons we stayed" (P3).')
    assert check_quotes(answer, got) == []
    assert check_quotes('"Support was slow and rude to everyone"', got)       # a made-up quote is still caught


# ---------------------------------------------------------------------------------------------------- PA-engines-28
def test_browser_engine_stops_at_once_when_the_browser_cannot_start(tmp_path, monkeypatch):
    """PA-engines-28: no Chromium installed. The engine returns ok False with the start error after zero turns,
    instead of letting the model burn its turns on a browser that is not there."""
    from engines.browser import ctl
    from engines.browser import engine as BE

    def no_browser(argv):
        print("ERROR: Chromium is not installed. Run: meshpool install-browser")
        return 1
    monkeypatch.setattr(ctl, "main", no_browser)
    monkeypatch.setattr(BE, "run", lambda *a, **k: pytest.fail("the model loop must not start"))
    out = BE.solve("Open example.com and read the title", workdir=str(tmp_path), task_id="t", client=object())
    assert out["ok"] is False and out["turns"] == 0
    assert out["reason"] == "ERROR: Chromium is not installed. Run: meshpool install-browser"


# ---------------------------------------------------------------------------------------------------- PA-engines-31
@pytest.mark.skipif(os.name == "nt", reason="POSIX owners and modes")
def test_icon_cache_is_private_to_the_user(tmp_path, monkeypatch):
    """PA-engines-31: the icon cache folder is the user's own, with no group or other access, even when it already
    existed with open permissions."""
    import engines.icons as IC
    cache = tmp_path / "meshpool_icons_x"
    cache.mkdir(mode=0o777)
    os.chmod(cache, 0o777)
    monkeypatch.setattr(IC, "CACHE", cache)
    if "dir" in IC.SETS.get("phosphor", {"dir": 1}):
        pytest.skip("phosphor is not a bundled set here")
    p = IC.path("phosphor:coin")                                   # a bundled set: written to the cache on first use
    assert p is not None and p.is_file()
    assert cache in p.parents                                  # its own folder is kept and used...
    assert os.stat(cache).st_mode & 0o077 == 0                 # ...with no group or other access left
    assert os.stat(p.parent).st_mode & 0o077 == 0


def test_icon_cache_default_name_is_per_user():
    """PA-engines-31: the default cache is not one shared /tmp/meshpool_icons for every user."""
    import engines.icons as IC
    if os.environ.get("MESHPOOL_ICON_CACHE"):
        pytest.skip("a cache folder is set for this run")
    assert IC.CACHE.name == f"meshpool_icons_{IC._user_tag()}" and IC._user_tag()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owners and modes")
def test_icon_cache_planted_by_another_user_is_not_used(tmp_path, monkeypatch):
    """PA-engines-31: shared /tmp on Linux, two users. Another user made the cache folder first and planted an SVG
    in it. That folder is not trusted: the icon comes from a private folder of this user's own."""
    import engines.icons as IC
    cache = tmp_path / "meshpool_icons_1000"
    monkeypatch.setattr(IC, "CACHE", cache)
    good = IC.path("phosphor:coin")
    if good is None or "dir" in IC.SETS.get("phosphor", {}):
        pytest.skip("phosphor is not a bundled set here")
    real = good.read_text(encoding="utf-8")
    shutil.rmtree(cache)
    planted = IC._cache_path("phosphor", good.stem)
    planted.parent.mkdir(parents=True)
    planted.write_text("<svg><script>alert(1)</script></svg>", encoding="utf-8")
    me = os.getuid()
    fake_os = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
    fake_os.getuid = lambda: me + 1                                       # the folder is not "mine"
    monkeypatch.setattr(IC, "os", fake_os)
    got = IC.path("phosphor:coin")
    assert got is not None and cache not in got.parents
    assert got.read_text(encoding="utf-8") == real


def test_forge_caches_are_private_and_skip_a_planted_folder(tmp_path, monkeypatch):
    """PA-engines-31 (rest): the forge's icon, template and brand caches sat at predictable names in the shared temp
    folder, so another user could plant a file there first. They now live in one per-user folder that is used only
    when it is this user's own and not a link; otherwise a private random folder is used."""
    import engines.icons as IC
    import tempfile as _tf
    monkeypatch.setattr(_tf, "tempdir", str(tmp_path))
    monkeypatch.setattr(IC, "_PRIVATE", {})
    mine = IC.forge_cache()
    assert mine.parent == tmp_path and (mine.stat().st_mode & 0o777) == 0o700
    target = tmp_path / "elsewhere"
    target.mkdir()
    mine.rmdir()
    mine.symlink_to(target)                                               # a planted link at the predictable name
    got = IC.forge_cache()
    assert got != mine and target not in got.parents and got != target and (got.stat().st_mode & 0o777) == 0o700
    from engines.forge import render as R, template as TP
    monkeypatch.setattr(R, "ICON_CACHE", None)
    assert got in R._icon_cache().parents
    assert TP.CACHE.parent == Path(".")                                   # a name only: built inside forge_cache()


def test_forge_brand_master_and_template_are_built_in_forge_cache(tmp_path, monkeypatch):
    """PA-engines-31 (brand and template caches): the recoloured brand master (brand.master_template) and the built
    forge template (template._build) are written inside engines.icons.forge_cache(), the private per-user folder,
    and never into the shared temp folder, where another user could plant a file at the predictable name first."""
    pytest.importorskip("pptx")
    import tempfile as _tf
    from pptx import Presentation
    import engines.icons as IC
    from engines.forge import brand as B, template as TP
    shared, mine = tmp_path / "shared tmp", tmp_path / "mine"
    shared.mkdir()
    mine.mkdir(mode=0o700)
    monkeypatch.setattr(_tf, "tempdir", str(shared))                     # the shared temp folder
    monkeypatch.setattr(IC, "forge_cache", lambda: mine)                 # the private per-user cache
    src = tmp_path / "company.pptx"
    Presentation().save(src)
    prof = {"template_mode": "master", "template": str(src),
            "colors": {"accent1": "0B6E4F", "dk2": "123ABC"}, "fonts": {"major": "Georgia", "minor": "Arial"}}
    got = Path(B.master_template(prof))
    assert got.exists() and got.parent == mine and got.name.startswith("forge_brand")
    TP._build.cache_clear()
    try:
        built = Path(TP.default_template({"palette": "forest", "frame": "band", "accent1": "13579B"}))
        assert built.exists() and built.parent == mine and built.name.startswith(TP.CACHE.stem)
    finally:
        TP._build.cache_clear()                                           # later tests build in their own folder
    assert not any(shared.rglob("*.pptx")), sorted(shared.rglob("*"))   # nothing written to the shared folder


@pytest.mark.skipif(os.name == "nt", reason="a POSIX path; Windows URIs are checked by Path.as_uri itself")
def test_kb_thumbnails_profile_uri_is_escaped_for_spaces_and_non_ascii(tmp_path, monkeypatch):
    """PA-forge-07 (profile URI): the LibreOffice profile is passed as Path.as_uri(), so a temp folder with a space
    or a non-ASCII letter (a user name like "José Smith") gives a valid, percent-escaped file URI. "file://" + the
    raw path would put the space and the raw letter into the URI and LibreOffice would not use the profile."""
    pytest.importorskip("pypdfium2")
    import tempfile as _tf
    from urllib.parse import unquote
    from engines.forge import common as FC
    from engines.forge import kb_ingest as K
    odd = tmp_path / "José Smith tmp"
    odd.mkdir()
    monkeypatch.setattr(_tf, "tempdir", str(odd))
    monkeypatch.setattr(FC, "find_soffice", lambda: "/usr/bin/soffice")
    cmds = []

    class P:
        pid = 4321

        def __init__(self, cmd, **kw):
            cmds.append(cmd)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(K, "subprocess", types.SimpleNamespace(Popen=P, DEVNULL=subprocess.DEVNULL,
                                                               TimeoutExpired=subprocess.TimeoutExpired))
    deck = tmp_path / "one.pptx"
    deck.write_bytes(b"x")
    assert K.thumbnails([deck], tmp_path / "thumbs", timeout=1, single_timeout=1) == {}
    assert cmds
    for cmd in cmds:
        prof = next(a for a in cmd if a.startswith("-env:UserInstallation="))[len("-env:UserInstallation="):]
        assert prof.startswith("file:///") and prof.endswith("/profile")
        assert " " not in prof and "é" not in prof                           # escaped, not raw
        assert "Jos%C3%A9%20Smith%20tmp/kbthumb_" in prof
        local = Path(unquote(prof[len("file://"):]))
        assert local.parent.parent == odd and prof == local.as_uri()
