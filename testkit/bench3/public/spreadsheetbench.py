"""SpreadsheetBench (verified 400) adapter: 6 fixed tasks (3 cell-level, 3 sheet-level).

Grading is a port of RUCKBReasoning/SpreadsheetBench evaluation/evaluation.py (compare_workbooks /
cell_level_compare): both the agent's output workbook and the golden workbook are recalculated with headless
LibreOffice (the official open_spreadsheet.py step), then every cell of each `answer_position` range is compared
with the official value normaliser. Grading works on scratch copies and never touches the workdir; never raises.
"""
from __future__ import annotations

import datetime
import json
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from testkit import gradekit as gk  # noqa: E402
from testkit.bench3.tasks import Task  # noqa: E402

DATA = _HERE / "data" / "spreadsheetbench"


# ----------------------------------------------------------------- official comparison (verbatim logic)
def datetime_to_float(dt):
    return (dt - datetime.datetime(1899, 12, 30)).days + (dt - datetime.datetime(1899, 12, 30)).seconds / 86400.0


def transform_value(v):
    if isinstance(v, (int, float)):
        v = round(float(v), 2)
    elif isinstance(v, datetime.time):
        v = str(v)[:-3]
    elif isinstance(v, datetime.datetime):
        v = round(datetime_to_float(v), 0)
    elif isinstance(v, str):
        try:
            v = round(float(v), 2)
        except ValueError:
            pass
    return v


def compare_cell_value(v1, v2):
    v1 = transform_value(v1)
    v2 = transform_value(v2)
    if (v1 == "" and v2 is None) or (v1 is None and v2 == ""):
        return True
    if (v1 == "" and v2 == "") or (v1 is None and v2 is None):
        return True
    if type(v1) != type(v2):
        return False
    return v1 == v2


def col_num2name(n):
    name = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def col_name2num(name):
    num = 0
    for c in name:
        num = num * 26 + (ord(c) - ord("A") + 1)
    return num


def parse_cell_range(range_str):
    start_cell, end_cell = range_str.split(":")
    sc, sr, ec, er = "", "", "", ""
    for ch in start_cell:
        if ch.isdigit():
            sr += ch
        else:
            sc += ch
    for ch in end_cell:
        if ch.isdigit():
            er += ch
        else:
            ec += ch
    return (col_name2num(sc), int(sr)), (col_name2num(ec), int(er))


def generate_cell_names(range_str):
    if ":" not in range_str:
        return [range_str]
    (sc, sr), (ec, er) = parse_cell_range(range_str)
    cols = [col_num2name(i) for i in range(sc, ec + 1)]
    return [f"{c}{r}" for c in cols for r in range(sr, er + 1)]


def cell_level_compare(wb_gt, wb_proc, sheet_name, cell_range):
    if sheet_name not in wb_proc:
        return False, "worksheet not found"
    ws_gt, ws_proc = wb_gt[sheet_name], wb_proc[sheet_name]
    for name in generate_cell_names(cell_range):
        a, b = ws_gt[name], ws_proc[name]
        if not compare_cell_value(a.value, b.value):
            return False, f"Value difference at cell {a.coordinate}: golden has {a.value!r}, output has {b.value!r}"
    return True, ""


def compare_workbooks(gt_file, proc_file, answer_position):
    import openpyxl
    if not Path(proc_file).exists():
        return False, "File not exist"
    try:
        wb_gt = openpyxl.load_workbook(filename=gt_file, data_only=True)
        wb_proc = openpyxl.load_workbook(filename=proc_file, data_only=True)
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    results, msgs = [], []
    for scr in answer_position.split(","):
        if "!" in scr:
            sheet_name, cell_range = scr.split("!")
            sheet_name = sheet_name.lstrip("'").rstrip("'")
        else:
            sheet_name, cell_range = wb_gt.sheetnames[0], scr
        sheet_name = sheet_name.lstrip("'").rstrip("'")
        cell_range = cell_range.lstrip("'").rstrip("'")
        r, m = cell_level_compare(wb_gt, wb_proc, sheet_name, cell_range)
        results.append(r)
        msgs.append(m)
    return all(results), "; ".join(m for m in msgs if m)


# ----------------------------------------------------------------- task wiring
def _out_name(tid: str) -> str:
    return f"1_{tid}_output.xlsx"


def _in_name(tid: str) -> str:
    return f"1_{tid}_init.xlsx"


def _make(rec: dict) -> Task:
    tid, pos = str(rec["id"]), rec["answer_position"]

    def setup(workdir, tid=tid):
        shutil.copy(DATA / tid / _in_name(tid), Path(workdir) / _in_name(tid))

    def grade(workdir, reply="", tid=tid, pos=pos):
        try:
            out = Path(workdir) / _out_name(tid)
            if not out.exists():
                return gk.result(False, f"output file {_out_name(tid)} not found", 0, 1)
            with gk.scratch_copy(workdir) as w, tempfile.TemporaryDirectory(prefix="sb-rc-") as td:
                td = Path(td)
                (td / "o").mkdir()
                (td / "g").mkdir()
                try:
                    proc = gk.recalc_xlsx(w / _out_name(tid), outdir=td / "o")
                except Exception as e:  # noqa: BLE001
                    return gk.result(False, f"output workbook unreadable by LibreOffice: {e}", 0, 1)
                gold = gk.recalc_xlsx(DATA / tid / f"1_{tid}_golden.xlsx", outdir=td / "g")
                ok, msg = compare_workbooks(gold, proc, pos)
            return gk.result(ok, "answer range matches golden" if ok else msg, int(ok), 1)
        except Exception as e:  # noqa: BLE001
            return gk.result(False, f"grader error: {type(e).__name__}: {e}", 0, 1)

    prompt = (f"{rec['instruction'].strip()}\n\n"
              f"The input workbook is `{_in_name(tid)}` in the current directory. Do not modify it. "
              f"Save the finished workbook as `{_out_name(tid)}` in the current directory (same sheets, with the "
              f"requested result in place; prefer .xlsx and plain cell formulas or values, no macros). "
              f"The result will be checked in range {pos}.")
    return Task(id=f"sb-{tid}", kind="office", prompt=prompt, setup=setup, grade=grade, timeout=900)


TASKS = [_make(r) for r in json.loads((DATA / "tasks.json").read_text())]


def get(ids):
    if isinstance(ids, str):
        ids = [s.strip() for s in ids.split(",") if s.strip()]
    by = {t.id: t for t in TASKS}
    out = []
    for i in ids:
        t = by.get(i) or by.get("sb-" + i)
        if t is None:
            raise KeyError(f"unknown task {i}; have {sorted(by)}")
        out.append(t)
    return out
