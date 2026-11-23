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


# --------------------------------------------------------------------------- checklist

class Checks:
    """Collect named pass/fail checks; ok only when every check passed."""

    def __init__(self):
        self.items: list[tuple[str, bool]] = []

    def add(self, name: str, cond, note: str = "") -> bool:
        self.items.append((name + (f" ({note})" if note and not cond else ""), bool(cond)))
        return bool(cond)

    def result(self) -> dict:
        bad = [n for n, ok in self.items if not ok]
        good = len(self.items) - len(bad)
        detail = ("FAILED: " + "; ".join(bad) + f" [{good}/{len(self.items)} checks passed]") if bad \
            else f"all {len(self.items)} checks passed"
        return result(not bad, detail, good, len(self.items))


# --------------------------------------------------------------------------- PDF chart evidence

_FILL = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
_STROKE = {b"S", b"s", b"B", b"B*", b"b", b"b*"}


def _mat_mul(a, b):
    """Row-vector convention: result = a x b (apply a first, then b)."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _walk_content(reader, stream, resources, ctm, out, depth=0):
    from pypdf.generic import ContentStream
    if depth > 4:
        return
    cs = ContentStream(stream, reader)
    stack, pts, curves, diag = [], [], 0, 0

    def T(x, y):
        return (x * ctm[0] + y * ctm[2] + ctm[4], x * ctm[1] + y * ctm[3] + ctm[5])

    for operands, op in cs.operations:
        try:
            if op == b"q":
                stack.append(ctm)
            elif op == b"Q":
                ctm = stack.pop() if stack else ctm
            elif op == b"cm":
                ctm = _mat_mul(tuple(float(v) for v in operands), ctm)
            elif op == b"re":
                x, y, w, h = (float(v) for v in operands)
                pts += [T(x, y), T(x + w, y), T(x + w, y + h), T(x, y + h)]
            elif op == b"m":
                pts.append(T(float(operands[0]), float(operands[1])))
            elif op == b"l":
                q = T(float(operands[0]), float(operands[1]))
                if pts and abs(q[0] - pts[-1][0]) > 0.5 and abs(q[1] - pts[-1][1]) > 0.5:
                    diag += 1  # only non-axis-aligned segments can be part of a line chart
                pts.append(q)
            elif op in (b"c", b"v", b"y"):
                v = [float(o) for o in operands]
                pts += [T(v[i], v[i + 1]) for i in range(0, len(v), 2)]
                curves += 1
            elif op in _FILL or op in _STROKE:
                if pts:
                    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                    box = (min(xs), min(ys), max(xs), max(ys))
                    if op in _FILL:
                        out["fills"].append(box + (curves,))
                    if op in _STROKE and diag >= 3 and (box[2] - box[0]) > 5 and (box[3] - box[1]) > 5:
                        out["polylines"] += 1
                pts, curves, diag = [], 0, 0
            elif op == b"n":
                pts, curves, diag = [], 0, 0
            elif op == b"Do" and resources is not None:
                xo = resources.get("/XObject")
                xo = xo.get_object() if xo is not None else {}
                obj = xo.get(operands[0])
                obj = obj.get_object() if obj is not None else None
                if obj is None:
                    continue
                sub = obj.get("/Subtype")
                if sub == "/Image":
                    out["images"].append((int(obj.get("/Width", 0)), int(obj.get("/Height", 0))))
                elif sub == "/Form":
                    m = obj.get("/Matrix")
                    fm = tuple(float(v) for v in m) if m else (1, 0, 0, 1, 0, 0)
                    res = obj.get("/Resources")
                    res = res.get_object() if res is not None else resources
                    _walk_content(reader, obj, res, _mat_mul(fm, ctm), out, depth + 1)
            elif op == b"INLINE IMAGE":
                st = operands.get("settings", {}) if isinstance(operands, dict) else {}
                w = int(st.get("/W", st.get("/Width", 0)))
                h = int(st.get("/H", st.get("/Height", 0)))
                out["images"].append((w, h))
        except Exception:  # noqa: BLE001 - a malformed operator must not break grading
            continue


def _clusters(items, key, tol=0.6):
    items = sorted(items, key=key)
    groups, cur = [], []
    for it in items:
        if cur and key(it) - key(cur[-1]) > tol:
            groups.append(cur)
            cur = []
        cur.append(it)
    if cur:
        groups.append(cur)
    return groups


def pdf_chart_evidence(path, min_img=(150, 100)) -> dict:
    """Look for a chart in a PDF: a decent raster image, or vector bars / filled curves / a polyline.

    Table rules and zebra/header shading do not count: bars are >=3 filled shapes on one baseline
    whose extents differ (a bar chart); table cells share a baseline but not differing extents.
    """
    from pypdf import PdfReader
    rd = PdfReader(str(path))
    out = {"images": [], "fills": [], "polylines": 0, "pages": len(rd.pages)}
    blank_only = True
    for page in rd.pages:
        res = page.get("/Resources")
        res = res.get_object() if res is not None else None
        contents = page.get_contents()
        if contents is not None:
            _walk_content(rd, contents, res, (1, 0, 0, 1, 0, 0), out)
        try:
            for im in page.images:
                pil = im.image
                if pil.size[0] >= min_img[0] and pil.size[1] >= min_img[1]:
                    cols = pil.convert("RGB").getcolors(maxcolors=4096)
                    if cols is None or len(cols) >= 3:
                        blank_only = False
        except Exception:  # noqa: BLE001
            blank_only = None  # could not decode: judge by size alone
    big = [i for i in out["images"] if i[0] >= min_img[0] and i[1] >= min_img[1]]
    raster = bool(big) and blank_only is not True
    fills = [f for f in out["fills"] if (f[2] - f[0]) >= 1.0 and (f[3] - f[1]) >= 1.0]
    bars = 0
    for lo, hi, ext in ((1, 3, lambda f: f[3] - f[1]),    # vertical bars share y0, differ in height
                        (0, 2, lambda f: f[2] - f[0])):   # horizontal bars share x0, differ in width
        for g in _clusters(fills, key=lambda f, lo=lo: f[lo]):
            if len(g) >= 3 and len({round(ext(f)) for f in g}) >= 3:
                bars = max(bars, len(g))
    curved = sum(1 for f in fills if f[4] > 0)
    out.update(raster=raster, bars=bars, curved=curved,
               chart=bool(raster or bars >= 3 or curved >= 3 or out["polylines"] >= 1))
    return out
