"""The real-world suite: 20 tasks in three categories, each with an objective automatic grader.

  swe       6 real GitHub issues from SWE-bench Verified (SymPy), graded with the official FAIL_TO_PASS/PASS_TO_PASS
            tests (testkit/loop/swe_local.py). The repository is a git worktree of $MESHPOOL_SWE_DIR/sympy.
  office    5 office tasks from testkit/bench3/tasks.py (xlsx budget, pdf report, docx tracked changes, pptx chart,
            invoice PDFs -> xlsx), with their strict file-content graders.
  everyday  9 everyday tasks (inputs from make_fixtures.py), graded deterministically.

Grader contract (same as bench3 and testkit/gradekit.py): grade(workdir, reply) -> {"ok", "detail", "passed", "total"};
a grader never writes into workdir (anything that has to run happens in gradekit.scratch_copy or a temp dir) and never
raises.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(ROOT / "testkit" / "loop")):
    if p not in sys.path:
        sys.path.insert(0, p)

from testkit import gradekit as gk  # noqa: E402
from testkit.gradekit import Checks, result, safe  # noqa: E402
from testkit.realworld import make_fixtures as mf  # noqa: E402

FIX = HERE / "fixtures"
SWE_IDS = ["sympy__sympy-24539", "sympy__sympy-22914", "sympy__sympy-23824", "sympy__sympy-23534",
           "sympy__sympy-24213", "sympy__sympy-23262"]           # the six fastest in earlier runs (all arms)
OFFICE_IDS = ["xlsx_budget", "pdf_report", "docx_tracked", "pptx_chart", "invoices_fx"]


def swe_dir() -> str:
    return os.environ.get("MESHPOOL_SWE_DIR") or str(Path.home() / ".cache" / "meshpool-swe")


def task_python() -> str:
    """The Python graders use to run the agent's code (Flask installed); first on PATH for the agents too."""
    return os.environ.get("RW_TASK_PYTHON") or sys.executable


@dataclass
class Task:
    id: str
    category: str                          # swe | office | everyday
    kind: str                              # office | code (bench3 arm defaults)
    prompt: str
    setup: Callable[[Path], None]          # fills workdir (which does not exist yet for swe; exists and is empty else)
    grade: Callable[[Path, str], dict]
    timeout: int = 900
    teardown: Callable[[Path], None] | None = None
    path_prepend: list = field(default_factory=list)


# =========================================================================== swe

def _swe_tasks() -> list[Task]:
    import swe_local
    raw = (ROOT / "testkit" / "loop" / "suites" / "swe_sympy.json").read_text()
    by_id = {t["id"]: t for t in json.loads(raw.replace("${MESHPOOL_SWE_DIR}", swe_dir()))}
    out = []
    for tid in SWE_IDS:
        t = by_id[tid]

        def setup(wd, t=t):
            wd = Path(wd)
            if wd.exists():
                shutil.rmtree(wd)
            swe_local.worktree(t, str(wd))

        @safe
        def grade(wd, reply, t=t):
            try:
                ok, why = swe_local.grade(t, str(wd))
            except subprocess.TimeoutExpired:
                ok, why = False, "tests timed out"
            return result(ok, why)

        def teardown(wd, t=t):
            swe_local.drop_worktree(t, str(wd))

        out.append(Task(tid, "swe", "code", t["prompt"], setup, grade, 900, teardown, [t["path_prepend"]]))
    return out


# =========================================================================== office (bench3)

def _office_tasks() -> list[Task]:
    from testkit.bench3 import tasks as b3
    out = []
    for t in b3.get(",".join(OFFICE_IDS)):
        out.append(Task(t.id, "office", "office", t.prompt, t.setup, t.grade, 900))
    return out


# =========================================================================== everyday graders

def _copy_fix(tid):
    return lambda wd: shutil.copytree(FIX / tid, Path(wd), dirs_exist_ok=True)


def _restore(w: Path, tid: str, rels) -> None:
    """Put the original copies of rels (tests the agent was told not to change) back into the scratch copy."""
    for rel in rels:
        src = FIX / tid / rel
        dst = w / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


@safe
def grade_pdf_facts(wd, reply):
    ck = Checks()
    f = Path(wd) / "summary.md"
    if not f.exists():
        return result(False, "summary.md is missing", 0, 1)
    text = f.read_text(errors="replace")
    bullets = [ln for ln in text.splitlines() if re.match(r"^\s*([-*•]|\d+[.)])\s+\S", ln)]
    ck.add("exactly 5 bullet points", len(bullets) == 5, f"{len(bullets)} bullets")
    body = " ".join(bullets) if bullets else text
    F = mf.PDF_FACTS
    ck.add("CEO named (Marta Lindqvist)", "lindqvist" in body.lower())
    ck.add("FY2025 revenue 48.6m", re.search(r"48[.,]6\s*(m|million|mn|M)?", body) is not None)
    ck.add("FY2025 net income 5.9m", re.search(r"5[.,]9\s*(m|million|mn|M)?", body) is not None)
    ck.add("headcount 1,284", re.search(r"1[,.\s]?284", body) is not None)
    ck.add("new market Brazil", "brazil" in body.lower())
    ceo = [b for b in bullets if re.search(r"\bceo\b|chief executive", b, re.I)]
    ck.add("the CEO bullet names Lindqvist (not the former CEO Berg alone)",
           any("lindqvist" in b.lower() for b in ceo) or (not ceo and "lindqvist" in body.lower()))
    return ck.result()


@safe
def grade_csv_sales(wd, reply):
    ck = Checks()
    f = Path(wd) / "answers.json"
    if not f.exists():
        return result(False, "answers.json is missing", 0, 1)
    try:
        a = json.loads(f.read_text())
    except ValueError as e:
        return result(False, f"answers.json is not JSON: {e}", 0, 1)
    e = mf.sales_expected()

    def num(v):
        try:
            return float(str(v).replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    q1 = num(a.get("q1"))
    # the exact sum is 511253.075 (a half-cent tie), so .07 and .08 are both right; float noise must not decide
    ck.add("q1 West 2025 revenue", q1 is not None and abs(q1 - e["q1_exact"]) <= 0.0051, f"got {a.get('q1')} want {e['q1']}")
    ck.add("q2 top region Q3 2025", str(a.get("q2", "")).strip().lower() == e["q2"].lower(),
           f"got {a.get('q2')} want {e['q2']}")
    q3 = num(a.get("q3"))
    ck.add("q3 East orders over 1000", q3 is not None and q3 == e["q3"], f"got {a.get('q3')} want {e['q3']}")
    return ck.result()


def _pytest(w: Path, args, timeout=300):
    rc, out = gk.run([task_python(), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", *args],
                     cwd=w, timeout=timeout, env={"PYTHONDONTWRITEBYTECODE": "1"})
    return rc, out, gk.count_tests(out)


@safe
def grade_fix_test(wd, reply):
    ck = Checks()
    with gk.scratch_copy(wd) as w:
        _restore(w, "fix_test", ["tests/test_pricing.py", "tests/test_discounts.py"])
        (w / "tests" / "test_hidden_rw.py").write_text(mf.FIX_TEST_HIDDEN)
        rc, out, c = _pytest(w, ["tests"])
    ck.add("all tests pass (original + hidden)", rc == 0 and c["failed"] == 0 and c["errors"] == 0 and c["passed"] >= 8,
           f"{c['passed']} passed, {c['failed']} failed, {c['errors']} errors")
    return ck.result()


@safe
def grade_flask_feature(wd, reply):
    ck = Checks()
    with gk.scratch_copy(wd) as w:
        _restore(w, "flask_feature", ["tests/test_app.py"])
        (w / "tests" / "test_hidden_rw.py").write_text(mf.FLASK_HIDDEN)
        rc, out, c = _pytest(w, ["tests"])
    ck.add("original + hidden tests pass", rc == 0 and c["failed"] == 0 and c["errors"] == 0 and c["passed"] >= 8,
           f"{c['passed']} passed, {c['failed']} failed, {c['errors']} errors; " + out.strip()[-160:].replace("\n", " "))
    return ck.result()


@safe
def grade_bash_rename(wd, reply):
    ck = Checks()
    script = Path(wd) / "rename.sh"
    if not script.exists():
        return result(False, "rename.sh is missing", 0, 1)
    with tempfile.TemporaryDirectory(prefix="rw-rename-") as td:
        s = Path(td) / "rename.sh"
        shutil.copy2(script, s)
        tree = Path(td) / "photos dir"
        (tree / "sub").mkdir(parents=True)
        for n in list(mf.RENAME_TREE) + list(mf.RENAME_SUBDIR):
            (tree / n).write_bytes(n.encode())
        rc, out = gk.run(["bash", str(s), str(tree)], cwd=td, timeout=60, env={"LC_ALL": "C"})
        got = {p.relative_to(tree).as_posix(): p.read_bytes().decode() for p in tree.rglob("*") if p.is_file()}
    want = {v: k for k, v in {**mf.RENAME_TREE, **mf.RENAME_SUBDIR}.items()}
    ck.add("exits 0", rc == 0, f"rc={rc} {out.strip()[:120]}")
    ck.add("prints nothing", not out.strip(), out.strip()[:80])
    wrong = sorted(set(want.items()) ^ set(got.items()))
    ck.add("every file ends with the expected name (content follows its file)", not wrong,
           f"{len(wrong)} mismatches, e.g. {wrong[:2]}")
    return ck.result()


@safe
def grade_md_to_docx(wd, reply):
    import docx
    ck = Checks()
    f = Path(wd) / "proposal.docx"
    if not f.exists():
        return result(False, "proposal.docx is missing", 0, 1)
    d = docx.Document(str(f))
    paras = [p.text for p in d.paragraphs]
    cells = [[c.text.strip() for c in r.cells] for t in d.tables for r in t.rows]
    allt = "\n".join(paras + [" | ".join(r) for r in cells])
    F = mf.DOCX_FIELDS
    ck.add("no {{placeholders}} left", "{{" not in allt and "}}" not in allt)
    for k in ("client_name", "project_name", "start_date", "end_date", "budget", "contact_name", "contact_email"):
        ck.add(f"{k} filled", F[k] in allt, F[k])
    ph = {r[0]: r[1] for r in cells if len(r) >= 2}
    ck.add("phase table rows filled in place", ph.get("1") == F["phase1"] and ph.get("2") == F["phase2"]
           and ph.get("3") == F["phase3"], str(ph)[:160])
    ck.add("heading 'Project Proposal' kept as a title/heading",
           any(p.text.strip() == "Project Proposal" and (p.style.name or "").lower().startswith(("title", "heading"))
               for p in d.paragraphs))
    bold_ok = any(p.text.startswith("Prepared for:") and p.runs and p.runs[0].bold for p in d.paragraphs)
    ck.add("'Prepared for:' label still bold", bold_ok)
    ck.add("internal margin note not copied", "32%" not in allt and "margin" not in allt.lower())
    return ck.result()


@safe
def grade_js_refactor(wd, reply):
    ck = Checks()
    cart = Path(wd) / "lib" / "cart.js"
    tax = Path(wd) / "lib" / "tax.js"
    ck.add("lib/tax.js exists", tax.exists())
    src = cart.read_text(errors="replace") if cart.exists() else ""
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)
    ck.add("no var in lib/cart.js", cart.exists() and not re.search(r"\bvar\b", code))
    ck.add("cart.js requires ./tax", re.search(r"require\(\s*['\"]\./tax(\.js)?['\"]\s*\)", code) is not None)
    ck.add("no tax rates left in cart.js", not re.search(r"0\.2\b|0\.07\b|0\.05\b", code))
    with gk.scratch_copy(wd) as w:
        _restore(w, "js_refactor", ["test/cart.test.js"])
        (w / "test" / "hidden_rw.test.js").write_text(mf.JS_HIDDEN)
        rc, out = gk.run(["node", "--test"], cwd=w, timeout=120)
        c = gk.count_tests(out)
    ck.add("original + hidden node tests pass", rc == 0 and c["failed"] == 0 and c["passed"] >= 5,
           f"{c['passed']} passed, {c['failed']} failed")
    return ck.result()


@safe
def grade_three_files(wd, reply):
    ck = Checks()
    f = Path(wd) / "answer.json"
    if not f.exists():
        return result(False, "answer.json is missing", 0, 1)
    try:
        a = json.loads(f.read_text())
    except ValueError as e:
        return result(False, f"answer.json is not JSON: {e}", 0, 1)
    e = mf.three_expected()
    try:
        cnt = int(a.get("eligible_count"))
    except (TypeError, ValueError):
        cnt = None
    ck.add("eligible_count", cnt == e["eligible_count"], f"got {a.get('eligible_count')} want {e['eligible_count']}")
    try:
        tot = float(str(a.get("total_payout")).replace(",", ""))
    except ValueError:
        tot = None
    ck.add("total_payout", tot is not None and abs(tot - e["total_payout"]) <= 0.01,
           f"got {a.get('total_payout')} want {e['total_payout']}")
    ck.add("top_employee_id", str(a.get("top_employee_id", "")).strip().upper() == e["top_employee_id"],
           f"got {a.get('top_employee_id')} want {e['top_employee_id']}")
    return ck.result()


def _norm_rows(text: str) -> list[tuple]:
    rows = list(csv.reader(io.StringIO(text)))
    return [tuple(c.strip() for c in r) for r in rows if any(c.strip() for c in r)]


@safe
def grade_csv_clean(wd, reply):
    import hashlib
    ck = Checks()
    f = Path(wd) / "clean_customers.csv"
    if not f.exists():
        return result(False, "clean_customers.csv is missing", 0, 1)
    rows = _norm_rows(f.read_text(errors="replace"))
    _, exp = mf.messy_rows()
    ck.add("header id,name,email,phone,signup_date", bool(rows) and [h.lower() for h in rows[0]] ==
           ["id", "name", "email", "phone", "signup_date"], str(rows[:1]))
    body = rows[1:]
    h = lambda rs: hashlib.sha256("\n".join(",".join(r) for r in rs).encode()).hexdigest()[:12]  # noqa: E731
    same = sum(1 for r in body if r in set(exp))
    ck.add("checksum of normalised rows matches (order, values, count)", h(body) == h(exp),
           f"{same}/{len(exp)} expected rows present, {len(body)} rows written")
    return ck.result()


def _ed(tid, prompt, grade, timeout=600):
    return Task(tid, "everyday", "code", prompt, _copy_fix(tid), grade, timeout)


EVERYDAY = [
    _ed("pdf_facts", "Read annual_report_2025.pdf (15 pages) and write summary.md with exactly 5 bullet points, one "
        "fact each, covering: who is the CEO now, FY2025 revenue, FY2025 net income, headcount at year end, and the "
        "new market planned for 2026.", grade_pdf_facts),
    _ed("csv_sales", "Using sales.csv (revenue = units x unit_price x (1 - discount)), answer and save to answers.json "
        "as {\"q1\": ..., \"q2\": ..., \"q3\": ...}: q1 = total revenue of the West region in calendar 2025, rounded "
        "to 2 decimals; q2 = the region with the highest revenue in Q3 2025 (July-September); q3 = how many East "
        "region orders have revenue above 1000.", grade_csv_sales),
    _ed("fix_test", "The test suite in this repo is failing (`python -m pytest -q`). Find and fix the bug in the "
        "library code. Don't change the tests.", grade_fix_test),
    _ed("flask_feature", "Implement the feature described in FEATURE.md in this Flask app. Keep the existing "
        "endpoints and tests working.", grade_flask_feature),
    _ed("bash_rename", "Write rename.sh as specified in SPEC.md. There is a sample folder you can try it on.",
        grade_bash_rename),
    _ed("md_to_docx", "Fill in proposal_template.docx using the details in project_brief.md and save the result as "
        "proposal.docx. Replace every {{placeholder}} and keep the template's layout and formatting.",
        grade_md_to_docx),
    _ed("js_refactor", "Do the refactor described in TASK.md.", grade_js_refactor),
    _ed("three_files", "Using staff.csv, payroll_config.json and leave_policy.md, work out the unused-leave payout and "
        "write answer.json as {\"eligible_count\": <number of eligible employees>, \"total_payout\": <total EUR, 2 "
        "decimals>, \"top_employee_id\": <employee_id with the highest payout; lowest id on a tie>}.",
        grade_three_files),
    _ed("csv_clean", "Clean customers_raw.csv following CLEANING_RULES.md and write clean_customers.csv.",
        grade_csv_clean),
]


def all_tasks() -> list[Task]:
    return _swe_tasks() + _office_tasks() + list(EVERYDAY)


def __getattr__(name):                       # TASKS is built lazily (the swe tasks need the SymPy clone path)
    if name == "TASKS":
        return all_tasks()
    raise AttributeError(name)


def get(ids: str | None) -> list[Task]:
    ts = all_tasks()
    if not ids or ids in ("all",):
        return ts
    cats = {"swe", "office", "everyday"}
    want = [i.strip() for i in ids.split(",") if i.strip()]
    by = {t.id: t for t in ts}
    out = []
    for w in want:
        if w in cats:
            out += [t for t in ts if t.category == w]
        elif w in by:
            out.append(by[w])
        else:
            raise KeyError(f"unknown task {w!r}")
    return out
