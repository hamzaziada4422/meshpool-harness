"""OfficeBench adapter: 4 fixed single/dual-app tasks that need only Word/Excel/PDF/text files.

Tasks (task id - subtask id of zlwang-cs/OfficeBench): 1-15/2, 2-23/0, 1-11/0, 1-19/3. Each workdir is the task's
official `testbed/` (files under ./data/). The evaluation functions in the task config (evaluate_contain,
evaluate_exact_match, evaluate_file_exist) are ports of OfficeBench utils/evaluate.py, run on a scratch copy of the
workdir. Official runs store the agent's final answer in data/answer.txt; here answer.txt is used if the agent
wrote it, otherwise the final reply text stands in for it. Never modifies the workdir, never raises.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from testkit import gradekit as gk  # noqa: E402
from testkit.bench3.tasks import Task  # noqa: E402

DATA = _HERE / "data" / "officebench"


# ----------------------------------------------------------------- readers (apps/*_read_file.py)
def excel_read_file(path):
    import openpyxl
    sheet = openpyxl.load_workbook(path).active
    s = ""
    for row in sheet.iter_rows():
        for cell in row:
            v = "[Empty Cell]" if cell.value is None else cell.value
            s += f"({cell.row}, {cell.column}): {v}\t"
        s += "\n"
    return s


def word_read_file_into_string(path):
    from docx import Document
    return "\n".join(p.text for p in Document(path).paragraphs)


def read_pdf_to_string(path):
    from pypdf import PdfReader
    return "".join((p.extract_text() or "") for p in PdfReader(path).pages)


# ----------------------------------------------------------------- evaluation ports (utils/evaluate.py)
def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _contain_text(content, args):
    content = content.lower()
    for kw in args["keywords"]:
        kw = kw.lower()
        if _is_number(kw):
            content = content.replace(",", "")
        if kw not in content:
            return False
    return True


def evaluate_contain(tb: Path, args, reply=""):
    t = args["doc_type"]
    fp = tb / args["file"]
    if t == "txt" and not fp.is_file() and Path(args["file"]).name == "answer.txt":
        return _contain_text(reply or "", args)          # official: finish_task answer is written to answer.txt
    if not fp.is_file():
        return False
    if t == "xlsx":
        content = excel_read_file(fp)
    elif t in ("txt", "ics"):
        content = fp.read_text(errors="replace")
    elif t in ("doc", "docx"):
        content = word_read_file_into_string(fp)
    elif t == "pdf":
        content = read_pdf_to_string(fp)
    else:
        return False
    return _contain_text(content, args)


def evaluate_file_exist(tb: Path, args, reply=""):
    return (tb / args["file"]).exists()


def evaluate_exact_match(tb: Path, args, reply="", task_id=""):
    import openpyxl
    res = tb / args["result_file"]
    if not res.exists() or args["doc_type"] != "xlsx":
        return False
    exp = DATA / task_id / "reference" / Path(args["expected_file"]).name   # official: ../../../../reference/<name>
    rs = openpyxl.load_workbook(res).active
    es = openpyxl.load_workbook(exp).active
    for row in rs.iter_rows():
        for c in row:
            if c.value != es.cell(row=c.row, column=c.column).value:
                return False
    for row in es.iter_rows():
        for c in row:
            if c.value != rs.cell(row=c.row, column=c.column).value:
                return False
    return True


_FUNCS = {"evaluate_contain": evaluate_contain, "evaluate_file_exist": evaluate_file_exist,
          "evaluate_exact_match": evaluate_exact_match}


def _grade(tb: Path, cfg: dict, reply: str):
    checks = []
    for item in cfg["evaluation"]:
        fn = _FUNCS[item["function"]]
        try:
            if fn is evaluate_exact_match:
                ok = fn(tb, item["args"], reply, cfg["task_id"])
            else:
                ok = fn(tb, item["args"], reply)
        except Exception as e:  # noqa: BLE001
            ok = False
        checks.append((item["function"], ok))
    return checks


def _make(cfg: dict) -> Task:
    tid, sid = cfg["task_id"], cfg["subtask_id"]

    def setup(workdir):
        shutil.copytree(DATA / tid / "testbed", Path(workdir), dirs_exist_ok=True)

    def grade(workdir, reply=""):
        try:
            with gk.scratch_copy(workdir) as w:
                checks = _grade(w, cfg, reply or "")
            p = sum(1 for _, ok in checks if ok)
            ok = p == len(checks)
            return gk.result(ok, "all official checks passed" if ok else
                             "failed: " + ", ".join(f for f, k in checks if not k), p, len(checks))
        except Exception as e:  # noqa: BLE001
            return gk.result(False, f"grader error: {type(e).__name__}: {e}", 0, 1)

    prompt = (f"{cfg['task'].strip()}\n\n"
              "The working files are in the ./data folder of the current directory; create or edit files there "
              "(keep the file names the task gives). If the task asks a question, state the final answer in your "
              "reply and also write it to data/answer.txt.")
    return Task(id=f"ob-{tid}-{sid}", kind="office", prompt=prompt, setup=setup, grade=grade, timeout=900)


TASKS = [_make(c) for c in json.loads((DATA / "tasks.json").read_text())]


def get(ids):
    if isinstance(ids, str):
        ids = [s.strip() for s in ids.split(",") if s.strip()]
    by = {t.id: t for t in TASKS}
    out = []
    for i in ids:
        t = by.get(i) or by.get("ob-" + i)
        if t is None:
            raise KeyError(f"unknown task {i}; have {sorted(by)}")
        out.append(t)
    return out
