"""Grader validation: pristine fixture fails, reference solution passes, workspace is never modified."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from testkit import gradekit as gk  # noqa: E402
from testkit.bench3 import make_fixtures as mf  # noqa: E402
from testkit.bench3 import tasks as T  # noqa: E402
from testkit.bench3.tests.reference import office as ref  # noqa: E402

REF = Path(__file__).resolve().parent / "reference"
IDS = [t.id for t in T.TASKS]
OFFICE = [t.id for t in T.TASKS if t.kind == "office"]


def make(tid, tmp_path):
    w = tmp_path / "work"
    w.mkdir()
    T.get(tid)[0].setup(w)
    return w


def grade(tid, w, reply=""):
    """Grade and assert the workspace digest is unchanged and no build/cache junk appeared."""
    before = gk.tree_digest(w)
    files_before = {p.relative_to(w) for p in w.rglob("*")}
    r = T.get(tid)[0].grade(w, reply)
    assert gk.tree_digest(w) == before, "grader modified the workspace"
    new = {p.relative_to(w) for p in w.rglob("*")} - files_before
    assert not new, f"grader left files behind: {new}"
    assert set(r) == {"ok", "detail", "passed", "total"} and isinstance(r["ok"], bool)
    return r


def solve(tid, w):
    """Apply the reference solution; return the reply text."""
    if tid in ref.SOLVERS:
        return ref.SOLVERS[tid](w) or ""
    kind, name = tid.split("_", 1)
    src = REF / tid
    if kind == "py":
        shutil.copy(src / f"{name}.py", w / f"{name}.py")
    else:
        shutil.copy(src / "lib.rs", w / "src" / "lib.rs")
    return ""


# ----------------------------------------------------------------------------- interface

def test_task_list_and_get():
    assert IDS == ["xlsx_edit", "docx_tracked", "invoices_fx", "docx_mailmerge", "xlsx_budget", "pptx_chart",
                   "pdf_report", "py_zipper", "py_sgf_parsing", "py_forth", "rs_react", "rs_macros"]
    assert [t.kind for t in T.TASKS] == ["office"] * 7 + ["code"] * 5
    assert [t.id for t in T.get(None)] == IDS and [t.id for t in T.get("")] == IDS
    assert [t.id for t in T.get("py_forth, xlsx_edit")] == ["py_forth", "xlsx_edit"]
    with pytest.raises(KeyError):
        T.get("nope")
    assert all(t.prompt and t.timeout > 0 for t in T.TASKS)


@pytest.mark.parametrize("tid", IDS)
def test_setup_fills_empty_folder_without_reference_solutions(tid, tmp_path):
    w = make(tid, tmp_path)
    files = {p.relative_to(w).as_posix() for p in w.rglob("*") if p.is_file()}
    assert files
    assert not any(".meta" in f or f.endswith("example.py") or f.endswith("example.rs") for f in files)
    if tid.startswith("py_"):
        assert "README.md" in files


def test_fixtures_are_reproducible(tmp_path):
    mf.build(tmp_path / "again")
    assert gk.tree_digest(tmp_path / "again") == gk.tree_digest(mf.OFFICE)


# ----------------------------------------------------------------------------- pristine fails, reference passes

@pytest.mark.parametrize("tid", IDS)
def test_pristine_fails(tid, tmp_path):
    w = make(tid, tmp_path)
    r = grade(tid, w)
    assert r["ok"] is False, r


@pytest.mark.parametrize("tid", IDS)
def test_reference_passes(tid, tmp_path):
    w = make(tid, tmp_path)
    reply = solve(tid, w)
    r = grade(tid, w, reply)
    assert r["ok"] is True, r
    if r["total"] is not None:
        assert r["passed"] == r["total"]


def test_expected_counts_are_real():
    assert T._rust_expected("rs_react") == 22   # 20 integration tests + 2 doc-tests
    assert T._rust_expected("rs_macros") == 18  # 8 integration tests + 10 doc-tests
    assert T._py_expected("py_forth", "forth_test.py") > 20


@pytest.mark.parametrize("tid,exp", [("rs_react", 22), ("rs_macros", 18)])
def test_rust_counts_all_result_lines(tid, exp, tmp_path):
    w = make(tid, tmp_path)
    solve(tid, w)
    r = grade(tid, w)
    assert (r["passed"], r["total"]) == (exp, exp)


# ----------------------------------------------------------------------------- code graders: honesty

def test_py_edited_tests_do_not_help_and_are_reported(tmp_path):
    w = make("py_zipper", tmp_path)
    (w / "zipper_test.py").write_text("def test_nothing():\n    assert True\n")
    r = grade("py_zipper", w)
    assert r["ok"] is False and "changed the test file" in r["detail"]


def test_py_changed_tests_noted_but_not_fatal_when_solved(tmp_path):
    w = make("py_zipper", tmp_path)
    solve("py_zipper", w)
    with open(w / "zipper_test.py", "a") as f:
        f.write("\n# agent comment\n")
    r = grade("py_zipper", w)
    assert r["ok"] is True and "changed the test file" in r["detail"]


def test_py_partial_solution_not_ok(tmp_path):
    w = make("py_forth", tmp_path)
    src = (REF / "py_forth" / "forth.py").read_text()
    (w / "forth.py").write_text(src + "\n_orig = evaluate\n\ndef evaluate(d):\n    raise StackUnderflowError('x')\n")
    r = grade("py_forth", w)
    assert r["ok"] is False and r["passed"] < r["total"]


def test_py_stray_conftest_is_ignored(tmp_path):
    w = make("py_forth", tmp_path)
    (w / "conftest.py").write_text("import pytest\ndef pytest_collection_modifyitems(items):\n    items.clear()\n")
    r = grade("py_forth", w)
    assert r["ok"] is False  # stub fails; the conftest cannot hide the tests


def test_rust_tests_restored_and_reported(tmp_path):
    w = make("rs_macros", tmp_path)
    (w / "tests" / "macros.rs").write_text("#[test]\nfn fake() { assert!(true); }\n")
    r = grade("rs_macros", w)
    assert r["ok"] is False and "agent changed tests/" in r["detail"]


def test_rust_missing_doc_tests_not_ok(tmp_path):
    w = make("rs_macros", tmp_path)
    solve("rs_macros", w)
    lib = (w / "src" / "lib.rs").read_text()
    (w / "src" / "lib.rs").write_text(lib[:lib.index("/// This module contains")])  # drop the doc-test module
    r = grade("rs_macros", w)
    assert r["ok"] is False  # tests/macros.rs needs the module: compile error, nothing passes


def test_rust_compile_error_is_a_clean_fail(tmp_path):
    w = make("rs_react", tmp_path)
    (w / "src" / "lib.rs").write_text("this is not rust")
    r = grade("rs_react", w)
    assert r["ok"] is False and r["passed"] == 0


# ----------------------------------------------------------------------------- X-08: pdf chart is required

def test_pdf_report_requires_chart(tmp_path):
    results = {}
    for kind in ("none", "vector", "raster"):
        d = tmp_path / kind
        d.mkdir()
        w = make("pdf_report", d)
        ref.solve_pdf_report(w, kind)
        results[kind] = grade("pdf_report", w)
    assert results["none"]["ok"] is False and "chart" in results["none"]["detail"]
    assert results["vector"]["ok"] is True, results["vector"]
    assert results["raster"]["ok"] is True, results["raster"]
    # the table text alone was right in all three
    assert "region revenue total" not in results["none"]["detail"]


def test_pdf_chart_evidence_details(tmp_path):
    out = {}
    for kind in ("none", "vector", "raster"):
        d = tmp_path / kind
        d.mkdir()
        w = make("pdf_report", d)
        ref.solve_pdf_report(w, kind)
        out[kind] = gk.pdf_chart_evidence(w / "report.pdf")
    assert not out["none"]["chart"] and out["none"]["bars"] == 0  # zebra rows, header shading, grid do not count
    assert out["vector"]["bars"] >= 3 and not out["vector"]["raster"]
    assert out["raster"]["raster"] and out["raster"]["bars"] == 0


def test_pdf_matplotlib_vector_chart_counts(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    by = mf.sales_by_region()
    fig, ax = plt.subplots()
    ax.bar(list(by), list(by.values()))
    fig.savefig(tmp_path / "m.pdf")
    plt.close(fig)
    assert gk.pdf_chart_evidence(tmp_path / "m.pdf")["bars"] >= 3


def test_pdf_tiny_or_blank_image_is_not_a_chart(tmp_path):
    from PIL import Image as PI
    from reportlab.pdfgen.canvas import Canvas
    PI.new("RGB", (40, 40), "red").save(tmp_path / "small.png")
    PI.new("RGB", (300, 200), "white").save(tmp_path / "blank.png")
    c = Canvas(str(tmp_path / "a.pdf"))
    c.drawImage(str(tmp_path / "small.png"), 50, 600, 40, 40)
    c.save()
    c = Canvas(str(tmp_path / "b.pdf"))
    c.drawImage(str(tmp_path / "blank.png"), 50, 500, 300, 200)
    c.save()
    assert not gk.pdf_chart_evidence(tmp_path / "a.pdf")["chart"]
    assert not gk.pdf_chart_evidence(tmp_path / "b.pdf")["chart"]


def test_pdf_wrong_totals_or_pages_fail(tmp_path):
    w = make("pdf_report", tmp_path)
    ref.solve_pdf_report(w, "vector")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(str(w / "report.pdf"), pagesize=A4)
    c.drawString(50, 800, "North South East West: revenue about 10.7k 10.0k 11.5k 13.3k")
    c.showPage(); c.showPage(); c.showPage()
    c.save()
    r = grade("pdf_report", w)
    assert r["ok"] is False and "pages" in r["detail"]


# ----------------------------------------------------------------------------- docx_tracked

def test_docx_without_tracked_changes_fails(tmp_path):
    w = make("docx_tracked", tmp_path)
    ref.solve_docx_untracked(w)
    r = grade("docx_tracked", w)
    assert r["ok"] is False
    assert "w:ins" in r["detail"] and "reject-all" in r["detail"]


def test_docx_collateral_edit_fails(tmp_path):
    w = make("docx_tracked", tmp_path)
    ref.solve_docx_tracked(w)
    ref.rewrite_docx(w / "contract.docx", lambda root: ref.track_replace(root, "twelve months", "24 months"))
    r = grade("docx_tracked", w)
    assert r["ok"] is False and "exactly the requested text" in r["detail"]


def test_docx_lost_italic_fails(tmp_path):
    w = make("docx_tracked", tmp_path)
    ref.solve_docx_tracked(w)

    def strip_italic(root):
        for i in list(root.iter(ref.NS + "i")):
            i.getparent().remove(i)
    ref.rewrite_docx(w / "contract.docx", strip_italic)
    r = grade("docx_tracked", w)
    assert r["ok"] is False and "italic" in r["detail"]


# ----------------------------------------------------------------------------- other negative cases

def test_xlsx_edit_hardcoded_values_and_missing_raise_fail(tmp_path):
    import openpyxl
    w = make("xlsx_edit", tmp_path)
    ref.solve_xlsx_edit(w)
    wb = openpyxl.load_workbook(w / "inventory.xlsx")
    ws = wb["Stock"]
    for r in range(2, ws.max_row):
        ws.cell(r, 6).value = ws.cell(r, 4).value * ws.cell(r, 5).value  # numbers, not formulas
    wb.save(w / "inventory.xlsx")
    r = grade("xlsx_edit", w)
    assert r["ok"] is False and "formulas" in r["detail"]

    (tmp_path / "b").mkdir()
    w2 = make("xlsx_edit", tmp_path / "b")
    ref.solve_xlsx_edit(w2)
    wb = openpyxl.load_workbook(w2 / "inventory.xlsx")
    ws = wb["Stock"]
    ws["A1"].font = openpyxl.styles.Font(bold=False)
    for r_ in range(2, ws.max_row):
        if ws.cell(r_, 3).value == "Cables":
            ws.cell(r_, 5).value = ws.cell(r_, 5).value / 1.1  # raise never happened
    wb.save(w2 / "inventory.xlsx")
    r = grade("xlsx_edit", w2)
    assert r["ok"] is False and "Cables" in r["detail"] and "header" in r["detail"]


def test_invoices_wrong_reply_fails(tmp_path):
    w = make("invoices_fx", tmp_path)
    solve("invoices_fx", w)
    r = grade("invoices_fx", w, "The grand total is $4,000.00")
    assert r["ok"] is False and "reply" in r["detail"]
    for fmt in ("4308.96", "4,308.96", "$4,308.96 USD", "USD 4308.96."):
        assert grade("invoices_fx", w, f"total {fmt}")["ok"] is True, fmt


def test_budget_hardcoded_numbers_fail_live_check(tmp_path):
    import openpyxl
    w = make("xlsx_budget", tmp_path)
    ref.solve_xlsx_budget(w)
    rp = gk.recalc_xlsx(w / "budget.xlsx")
    vals = openpyxl.load_workbook(rp, data_only=True)
    out = openpyxl.load_workbook(w / "budget.xlsx")
    for r in out["Budget"].iter_rows():
        for c in r:
            if isinstance(c.value, str) and c.value.startswith("="):
                c.value = vals["Budget"][c.coordinate].value
    out.save(w / "budget.xlsx")
    r = grade("xlsx_budget", w)
    assert r["ok"] is False and "live" in r["detail"]


def test_mailmerge_lost_formatting_fails(tmp_path):
    import docx
    w = make("docx_mailmerge", tmp_path)
    (w / "letters").mkdir()
    for name, company, amount, due in mf.CUSTOMERS:
        d = docx.Document()
        d.add_paragraph("Payment reminder")
        d.add_paragraph(f"Dear {name}, {company}: balance {amount} due {due}.")
        d.save(str(w / "letters" / f"{name}.docx"))
    r = grade("docx_mailmerge", w)
    assert r["ok"] is False and "Heading 1 lost" in r["detail"] and "bold" in r["detail"]


def test_mailmerge_wrong_count_and_leftover_placeholder_fail(tmp_path):
    w = make("docx_mailmerge", tmp_path)
    ref.solve_docx_mailmerge(w)
    next((w / "letters").glob("*.docx")).unlink()
    assert grade("docx_mailmerge", w)["ok"] is False


def test_pptx_picture_instead_of_chart_fails(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    w = make("pptx_chart", tmp_path)
    ref.solve_pptx_chart(w)
    prs = Presentation(str(w / "deck.pptx"))
    for sl in prs.slides:
        for s in list(sl.shapes):
            if getattr(s, "has_chart", False) and s.has_chart:
                s._element.getparent().remove(s._element)
    prs.save(str(w / "deck.pptx"))
    r = grade("pptx_chart", w)
    assert r["ok"] is False and "native chart" in r["detail"]


def test_pptx_wrong_values_and_offslide_fail(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    w = make("pptx_chart", tmp_path)
    ref.solve_pptx_chart(w)
    prs = Presentation(str(w / "deck.pptx"))
    prs.slides[0].shapes.add_textbox(Inches(12), Inches(7), Inches(3), Inches(2))
    prs.save(str(w / "deck.pptx"))
    r = grade("pptx_chart", w)
    assert r["ok"] is False and "outside" in r["detail"]


def test_pptx_regions_as_categories_accepted(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    w = make("pptx_chart", tmp_path)
    prs = Presentation()
    for i in range(4):
        prs.slides.add_slide(prs.slide_layouts[6])
    cd = CategoryChartData()
    cd.categories = mf.REGIONS
    for qi, q in enumerate(mf.QUARTERS):
        cd.add_series(q, [mf.QUARTERLY[r][qi] for r in mf.REGIONS])
    prs.slides[1].shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(1), Inches(1), Inches(6), Inches(4), cd)
    prs.save(str(w / "deck.pptx"))
    assert grade("pptx_chart", w)["ok"] is True


# ----------------------------------------------------------------------------- X-08 regressions: frames are not charts

def _table_pdf(path, border=False, big_fill=False):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import getSampleStyleSheet

    def deco(c, doc):
        if border:
            p = c.beginPath()
            p.moveTo(50, 50); p.lineTo(550, 50); p.lineTo(550, 800); p.lineTo(50, 800); p.lineTo(50, 50)
            c.drawPath(p, stroke=1, fill=0)
        if big_fill:
            c.setFillGray(0.95)
            c.rect(40, 40, 520, 770, stroke=0, fill=1)
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Sales report", getSampleStyleSheet()["Title"]), ref._sales_table(mf.sales_by_region())],
        onFirstPage=deco)


def test_stroked_border_box_is_not_a_chart(tmp_path):
    _table_pdf(tmp_path / "a.pdf", border=True)
    assert not gk.pdf_chart_evidence(tmp_path / "a.pdf")["chart"]
    _table_pdf(tmp_path / "b.pdf", border=True, big_fill=True)
    assert not gk.pdf_chart_evidence(tmp_path / "b.pdf")["chart"]


def test_border_plus_matplotlib_line_chart_passes(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pypdf import PdfReader, PdfWriter, Transformation
    _table_pdf(tmp_path / "t.pdf", border=True)
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3, 4], [10, 25, 15, 30])
    fig.savefig(tmp_path / "l.pdf")
    plt.close(fig)
    wr = PdfWriter(clone_from=str(tmp_path / "t.pdf"))
    wr.pages[0].merge_page(PdfReader(str(tmp_path / "l.pdf")).pages[0])
    wr.write(str(tmp_path / "m.pdf"))
    assert gk.pdf_chart_evidence(tmp_path / "m.pdf")["chart"]
    assert gk.pdf_chart_evidence(tmp_path / "l.pdf")["polylines"] >= 1


def test_reportlab_line_chart_passes(tmp_path):
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    from reportlab.pdfgen.canvas import Canvas
    d = Drawing(400, 200)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 30, 30, 330, 140
    lp.data = [[(1, 10), (2, 25), (3, 15), (4, 30)]]
    d.add(lp)
    c = Canvas(str(tmp_path / "r.pdf"))
    renderPDF.draw(d, c, 50, 500)
    c.save()
    assert gk.pdf_chart_evidence(tmp_path / "r.pdf")["chart"]
