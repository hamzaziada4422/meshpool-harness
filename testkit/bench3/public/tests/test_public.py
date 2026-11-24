"""Public-suite adapters: untouched input fails, reference solution passes, grading leaves the workdir unchanged."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

PUB = Path(__file__).resolve().parents[1]
ROOT = PUB.parents[2]
sys.path.insert(0, str(ROOT))
from testkit import gradekit as gk  # noqa: E402


def _load(name):
    s = importlib.util.spec_from_file_location(f"pub_{name}", PUB / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


SB, OB = _load("spreadsheetbench"), _load("officebench")


def _sb_solution(tid):
    def f(wd):
        shutil.copy(SB.DATA / tid / f"1_{tid}_golden.xlsx", wd / SB._out_name(tid))
        return ""
    return f


def _ob_docx(wd):
    from docx import Document
    d = Document(wd / "data/project_report.docx")
    d.add_paragraph((wd / "data/reference.txt").read_text())
    d.save(wd / "data/project_report.docx")
    return ""


def _ob_costs(wd):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["total due", 104.98])
    wb.save(wd / "data/costs.xlsx")
    return ""


def _ob_sorted(wd):
    shutil.copy(OB.DATA / "1-11/reference/sorted_score.xlsx", wd / "data/sorted_score.xlsx")
    return ""


SOLUTIONS = {t.id: _sb_solution(t.id[3:]) for t in SB.TASKS}
SOLUTIONS.update({"ob-1-15-2": _ob_docx, "ob-2-23-0": _ob_costs, "ob-1-11-0": _ob_sorted,
                  "ob-1-19-3": lambda wd: "The effective date is 2024-05-29."})
ALL = SB.TASKS + OB.TASKS


def test_counts():
    assert len(SB.TASKS) == 6 and len(OB.TASKS) == 4
    assert set(SOLUTIONS) == {t.id for t in ALL}


@pytest.mark.parametrize("task", ALL, ids=lambda t: t.id)
def test_task(task, tmp_path):
    wd = tmp_path / "w"
    wd.mkdir()
    task.setup(wd)
    d0 = gk.tree_digest(wd)
    r = task.grade(wd, "")
    assert r["ok"] is False, f"untouched input must fail: {r}"
    assert set(r) >= {"ok", "detail", "passed", "total"}
    assert gk.tree_digest(wd) == d0, "grading modified the workdir"
    reply = SOLUTIONS[task.id](wd)
    d1 = gk.tree_digest(wd)
    r = task.grade(wd, reply)
    assert r["ok"] is True, f"reference solution must pass: {r}"
    assert gk.tree_digest(wd) == d1, "grading modified the workdir"
