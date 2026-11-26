"""Cached values for workbooks whose formulas have none. openpyxl writes a formula without its result, so a workbook an
agent made with it shows blanks in Quick Look, on a phone and in mail, and the app's own preview showed the formula
text. LibreOffice (the kept session) calculates the workbook once; the values are then read from that copy.

- ``values(path)`` for the preview: {(sheet, "B2"): value} for the formula cells with no cached value, or None when
  there is nothing to do or it cannot be done here. Cached by the file's mtime, so it runs once per version.
- ``fill(path)`` for a file made in a turn: writes those values into the file itself, as the cached results of the
  same formulas. Only the empty ``<v>`` of each formula cell changes; formulas, styles and the rest are untouched."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

MAX_FORMULAS = 5000
MAX_BYTES = 25 * 1024 * 1024
_ERRORS = ("#VALUE!", "#REF!", "#NAME?", "#DIV/0!", "#N/A", "#NUM!", "#NULL!")
_CACHE: dict = {}                      # (path, mtime_ns, size) -> {(sheet, cell): value} | None
_LOCK = threading.Lock()


def _uncached(path) -> dict | None:
    """{(sheet, cell): formula} of the formula cells with no cached value; {} when none; None when unreadable."""
    try:
        import openpyxl
        wf = openpyxl.load_workbook(path)
        wv = openpyxl.load_workbook(path, data_only=True)
    except Exception:  # noqa: BLE001
        return None
    out = {}
    try:
        for ws in wf.worksheets:
            vs = wv[ws.title]
            for row in ws.iter_rows():
                for c in row:
                    t = getattr(c.value, "text", c.value)
                    if isinstance(t, str) and t.startswith("=") and vs[c.coordinate].value is None:
                        out[(ws.title, c.coordinate)] = t
                        if len(out) > MAX_FORMULAS:
                            return None
    finally:
        wf.close()
        wv.close()
    return out


def _recalculated(path, timeout: float):
    """An openpyxl data_only workbook of a LibreOffice-recalculated copy of path, and its temp folder; or None."""
    try:
        import openpyxl
        from meshpool_harness import lo_session, office_runtime
        exe = office_runtime.find_soffice()
        if not exe:
            return None
        tmp = tempfile.mkdtemp(prefix="mp-recalc-")
        made = lo_session.convert(exe, str(path), "xlsx", tmp, max(1, timeout), recalc=True)
        if made is None:
            import subprocess
            kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
            prof = Path(tmp) / "lo-profile"
            subprocess.run([exe, f"-env:UserInstallation={prof.as_uri()}", "--headless", "--calc", "--convert-to", "xlsx",
                            "--outdir", str(Path(tmp) / "out"), str(path)], capture_output=True, timeout=max(1, timeout), **kw)
            made = Path(tmp) / "out" / (Path(path).stem + ".xlsx")
        if not made or not Path(made).is_file():
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        return openpyxl.load_workbook(made, data_only=True), tmp
    except Exception:  # noqa: BLE001
        return None


def _compute(path, timeout: float) -> dict | None:
    try:
        if Path(path).stat().st_size > MAX_BYTES:
            return None
    except OSError:
        return None
    need = _uncached(path)
    if not need:
        return None
    got = _recalculated(path, timeout)
    if got is None:
        return None
    wb, tmp = got
    try:
        vals = {}
        for (sheet, cell) in need:
            if sheet in wb.sheetnames:
                v = wb[sheet][cell].value
                if v is not None:
                    vals[(sheet, cell)] = v
        return vals or None
    finally:
        wb.close()
        shutil.rmtree(tmp, ignore_errors=True)


def values(path, timeout: float = 25) -> dict | None:
    """Computed values for the formula cells of path that have none stored (see the module doc). Once per file version."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    res = _compute(path, timeout)
    with _LOCK:
        for k in [k for k in _CACHE if k[0] == key[0]]:
            _CACHE.pop(k, None)
        if len(_CACHE) > 16:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = res
    return res


def _cell_xml(ref: str, attrs: str, f: str, v) -> str | None:
    from datetime import date, datetime, time as dtime
    attrs = re.sub(r'\st="[^"]*"', "", attrs)
    t = ""
    if isinstance(v, bool):
        t, text = ' t="b"', "1" if v else "0"
    elif isinstance(v, (int, float)):
        text = repr(float(v)) if isinstance(v, float) else str(v)
    elif isinstance(v, (datetime, date, dtime)):
        from openpyxl.utils.datetime import to_excel
        text = repr(float(to_excel(v)))
    elif isinstance(v, str):
        t, text = (' t="e"' if v.strip() in _ERRORS else ' t="str"'), escape(v)
    else:
        return None
    return f'<c r="{ref}"{attrs}{t}>{f}<v>{text}</v></c>'


_CELL = re.compile(r'<c r="([A-Z]+\d+)"([^>]*)>(<f[^>]*?(?:/>|>.*?</f>))<v\s*(?:/>|></v>)</c>', re.S)


def _sheet_parts(z: zipfile.ZipFile) -> dict:
    """{sheet name: its part name in the zip}."""
    wbx = z.read("xl/workbook.xml").decode("utf-8", "replace")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
    target = {}
    for m in re.finditer(r"<Relationship\b[^>]*>", rels):
        i, t = re.search(r'\bId="([^"]*)"', m.group(0)), re.search(r'\bTarget="([^"]*)"', m.group(0))
        if i and t:
            target[i.group(1)] = t.group(1)
    out = {}
    for m in re.finditer(r"<sheet\b[^>]*>", wbx):
        n, r = re.search(r'\bname="([^"]*)"', m.group(0)), re.search(r'\br:id="([^"]*)"', m.group(0))
        if n and r and r.group(1) in target:
            t = target[r.group(1)].lstrip("/")
            out[_unescape(n.group(1))] = t if t.startswith("xl/") else "xl/" + t
    return out


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def fill(path, timeout: float = 25) -> int:
    """Write the computed results into the formula cells of path that have none. Returns how many cells were filled;
    0 when nothing needed it or it could not be done (the file is then left as it was)."""
    path = Path(path)
    vals = _compute(path, timeout)
    if not vals:
        return 0
    by_sheet: dict = {}
    for (sheet, cell), v in vals.items():
        by_sheet.setdefault(sheet, {})[cell] = v
    n, tmp = 0, path.with_name(path.name + f".meshpool-{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(path) as zin:
            parts = _sheet_parts(zin)
            want = {parts[s]: c for s, c in by_sheet.items() if s in parts}
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for info in zin.infolist():
                    data = zin.read(info.filename)
                    cells = want.get(info.filename)
                    if cells:
                        def sub(m, cells=cells):
                            nonlocal n
                            if m.group(1) not in cells:
                                return m.group(0)
                            new = _cell_xml(m.group(1), m.group(2), m.group(3), cells[m.group(1)])
                            if new is None:
                                return m.group(0)
                            n += 1
                            return new
                        data = _CELL.sub(sub, data.decode("utf-8")).encode("utf-8")
                    zout.writestr(info, data)
        if n:
            os.replace(tmp, path)
        return n
    except Exception:  # noqa: BLE001 - the file stays as it was
        return 0
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
