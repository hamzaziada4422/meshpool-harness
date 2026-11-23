"""Shared helpers for honest graders: scratch copies, real test counting, LibreOffice recalculation.

Rules every grader built on this follows:
  * never touch the workspace under test (work in `scratch_copy`),
  * count real test results across ALL result lines,
  * never raise (see `safe`).
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


# --------------------------------------------------------------------------- results

def result(ok: bool, detail: str, passed=None, total=None) -> dict:
    return {"ok": bool(ok), "detail": detail, "passed": passed, "total": total}


def safe(fn):
    """Decorator: a grader that raises returns ok False with the error as detail."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 - graders must never raise
            return result(False, f"grader error: {type(e).__name__}: {e}")
    return wrapper


# --------------------------------------------------------------------------- workspace safety

@contextlib.contextmanager
def scratch_copy(workdir):
    """Yield a temp copy of workdir (symlinks preserved); deleted afterwards."""
    tmp = Path(tempfile.mkdtemp(prefix="gradekit-"))
    try:
        dst = tmp / "w"
        shutil.copytree(workdir, dst, symlinks=True)
        yield dst
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tree_digest(path) -> str:
    """sha256 over relative paths + bytes of every file (and symlink targets) under path."""
    root = Path(path)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            h.update(b"L:" + rel.encode() + b"\0" + os.readlink(p).encode() + b"\0")
        elif p.is_file():
            h.update(b"F:" + rel.encode() + b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
        else:
            h.update(b"D:" + rel.encode() + b"\0")
    return h.hexdigest()


def run(cmd, cwd=None, timeout=300, env=None):
    """Run a command; return (returncode, combined stdout+stderr). rc -9 on timeout, -1 if not found."""
    e = dict(os.environ)
    e.update(env or {})
    try:
        p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as ex:
        out = ex.stdout if isinstance(ex.stdout, str) else (ex.stdout or b"").decode("utf-8", "replace")
        return -9, (out or "") + f"\n[timeout after {timeout}s]"
    except FileNotFoundError as ex:
        return -1, f"[not found: {ex}]"


# --------------------------------------------------------------------------- test counting

_ZERO = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
_PYTEST_ITEM = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?|rerun)\b")
_PYTEST_LINE = re.compile(r"^[=\s-]*((?:\d+\s+\w+(?:,\s*)?)+)\s+in\s+[\d.]+\s*s(?:\s*\([\d:]+\))?\s*[=\s-]*$")
_CARGO = re.compile(r"test result:\s*(?:ok|FAILED)\.\s*(\d+) passed;\s*(\d+) failed;\s*(\d+) ignored")
_NODE = re.compile(r"^\s*#\s*(pass|fail|skipped|cancelled)\s+(\d+)\s*$", re.M)
_UT_RAN = re.compile(r"^Ran (\d+) tests?\b", re.M)
_UT_FAIL = re.compile(r"^FAILED \(([^)]*)\)", re.M)
_UT_OK = re.compile(r"^OK(?: \(([^)]*)\))?\s*$", re.M)


def count_tests(output: str) -> dict:
    """Sum EVERY result line in output: pytest, cargo, node --test, unittest. total = passed+failed+errors+skipped."""
    c = dict(_ZERO)
    for line in output.splitlines():
        m = _PYTEST_LINE.match(line.strip())
        if m:
            for n, kind in _PYTEST_ITEM.findall(m.group(1)):
                n = int(n)
                if kind == "passed":
                    c["passed"] += n
                elif kind == "failed":
                    c["failed"] += n
                elif kind.startswith("error"):
                    c["errors"] += n
                elif kind == "skipped":
                    c["skipped"] += n
            continue
        m = _CARGO.search(line)
        if m:
            c["passed"] += int(m.group(1))
            c["failed"] += int(m.group(2))
            c["skipped"] += int(m.group(3))
    for kind, n in _NODE.findall(output):
        key = {"pass": "passed", "fail": "failed", "skipped": "skipped", "cancelled": "errors"}[kind]
        c[key] += int(n)
    # unittest: each "Ran N tests" block is followed by OK / FAILED (...)
    for m in _UT_RAN.finditer(output):
        ran = int(m.group(1))
        tail = output[m.end():]
        nxt = _UT_RAN.search(tail)
        tail = tail[:nxt.start()] if nxt else tail
        bad = skipped = fails = errs = 0
        f = _UT_FAIL.search(tail)
        if f:
            for k, v in re.findall(r"(\w+)=(\d+)", f.group(1)):
                if k == "failures":
                    fails += int(v)
                elif k == "errors":
                    errs += int(v)
                elif k == "skipped":
                    skipped += int(v)
        else:
            o = _UT_OK.search(tail)
            if o and o.group(1):
                for k, v in re.findall(r"(\w+)=(\d+)", o.group(1)):
                    if k == "skipped":
                        skipped += int(v)
        c["failed"] += fails
        c["errors"] += errs
        c["skipped"] += skipped
        c["passed"] += max(ran - fails - errs - skipped, 0)
    c["total"] = c["passed"] + c["failed"] + c["errors"] + c["skipped"]
    return c


# --------------------------------------------------------------------------- numbers in text

def numbers_in(text: str) -> list[float]:
    """All numbers in text; '1,234.56', '$1234.56', '1234' are all understood."""
    out = []
    for tok in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", text):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def text_has_amount(text: str, value: float, tol: float = 0.006) -> bool:
    """True when text states value (to the cent), with or without thousands commas."""
    return any(abs(n - value) <= tol for n in numbers_in(text))


# --------------------------------------------------------------------------- LibreOffice

_RECALC_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


def recalc_xlsx(path, outdir=None, timeout: int = 180) -> Path:
    """Recalculate a workbook with headless LibreOffice and return the converted copy.

    The copy goes into a temp dir OUTSIDE the workspace (or `outdir`), with a private profile so
    parallel calls do not collide. Cells with formulas get fresh cached values (recalc mode: always).
    """
    src = Path(path).resolve()
    work = Path(tempfile.mkdtemp(prefix="recalc-"))
    prof = work / "profile"
    (prof / "user").mkdir(parents=True)
    (prof / "user" / "registrymodifications.xcu").write_text(_RECALC_XCU)
    out = Path(outdir) if outdir else work / "out"
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--norestore",
           "--convert-to", "xlsx", "--outdir", str(out), str(src)]
    rc, o = run(cmd, cwd=str(work), timeout=timeout, env={"HOME": str(work)})
    res = out / (src.stem + ".xlsx")
    if not res.exists():
        raise RuntimeError(f"recalc failed (rc={rc}): {o[-300:]}")
    shutil.rmtree(prof, ignore_errors=True)
    if outdir:
        shutil.rmtree(work, ignore_errors=True)
    return res
