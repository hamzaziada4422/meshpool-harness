"""Shared pieces for the forge measurers and validators: rule config, failure records, evidence IDs, LibreOffice,
font metrics. Nothing here calls a model."""
from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
EMU_PER_IN = 914400


# ------------------------------------------------------------------ config
def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def load_rules(override: str | dict | None = None) -> dict:
    """rules.yaml, deep-merged with an optional client override (path or dict)."""
    rules = yaml.safe_load((HERE / "rules.yaml").read_text())
    if override:
        over = yaml.safe_load(Path(override).read_text()) if isinstance(override, (str, Path)) else override
        rules = _merge(rules, over or {})
    return rules


# ------------------------------------------------------------------ identity and evidence
def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def evidence_id(file_sha: str, key: str) -> str:
    """Stable ID for one measurement on one file: EV-<file sha8>-<metric>."""
    return f"EV-{file_sha[:8]}-{key}"


def failure(rule: str, check: str, message: str, *, slide=None, shape=None, sheet=None, cell=None, measured=None,
            limit=None, fix_scope: str = "content", severity: str = "error", status: str = "fail") -> dict:
    """One machine-readable failure. `message` is written so any LLM can act on it without other context."""
    f = {"rule": rule, "check": check, "status": status, "severity": severity, "fix_scope": fix_scope,
         "message": message}
    for k, v in (("slide", slide), ("shape", shape), ("sheet", sheet), ("cell", cell), ("measured", measured),
                 ("limit", limit)):
        if v is not None:
            f[k] = v
    return f


def summarize(failures: list[dict], rules_run: list[str]) -> dict:
    """Per-rule status: pass / fail / not_run (a rule that could not be evaluated is never reported as a pass)."""
    by = {r: "pass" for r in rules_run}
    for f in failures:
        r = f["rule"]
        if f["status"] == "fail" and f["severity"] == "error":
            by[r] = "fail"
        elif f["status"] == "not_run" and by.get(r) == "pass":
            by[r] = "not_run"
        elif f["status"] == "fail" and by.get(r) == "pass":
            by[r] = "warn"
    return by


# ------------------------------------------------------------------ LibreOffice
def find_soffice() -> str | None:
    """LibreOffice from the one resolver (meshpool_harness/office_runtime.py): MH_SOFFICE, the copy bundled in the
    app. A system install counts only for developers (MH_ALLOW_SYSTEM_SOFFICE=1)."""
    from meshpool_harness import office_runtime
    return office_runtime.find_soffice()


FONT_SUBSTITUTES = {"Segoe UI": "Selawik", "Segoe UI Semibold": "Selawik"}


def _profile_fonts(profile: Path) -> None:
    """The throwaway profile carries the bundled fonts and a substitution table, so LibreOffice draws Segoe UI with
    Selawik (same metrics) on every computer. Without it a machine lacking Segoe UI falls back to a wider face and
    the render check reports overflows that PowerPoint never shows."""
    try:
        here = Path(__file__).resolve().parent / "fonts"
        fonts = profile / "user" / "fonts"
        fonts.mkdir(parents=True, exist_ok=True)
        for f in here.glob("*.ttf"):
            shutil.copy2(f, fonts / f.name)
        pairs = "".join(
            f'<item oor:path="/org.openoffice.Office.Common/Font/Substitution/FontPairs"><node oor:name="_{i}" '
            f'oor:op="replace"><prop oor:name="Always" oor:op="fuse"><value>true</value></prop>'
            f'<prop oor:name="OnScreenOnly" oor:op="fuse"><value>false</value></prop>'
            f'<prop oor:name="ReplaceFont" oor:op="fuse"><value>{a}</value></prop>'
            f'<prop oor:name="SubstituteFont" oor:op="fuse"><value>{b}</value></prop></node></item>'
            for i, (a, b) in enumerate(FONT_SUBSTITUTES.items()))
        (profile / "user" / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<item oor:path="/org.openoffice.Office.Common/Font/Substitution"><prop oor:name="Replacement" '
            'oor:op="fuse"><value>true</value></prop></item>' + pairs +
            # recalculate every formula on load: by default LibreOffice keeps the values cached in an xlsx, so a
            # workbook whose inputs were edited elsewhere was checked against stale results (0 = always)
            '<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" '
            'oor:op="fuse"><value>0</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" '
            'oor:op="fuse"><value>0</value></prop></item></oor:items>', encoding="utf-8")
    except OSError:                                        # the conversion still runs, with the system's fonts
        pass


_LIVE: set = set()          # LibreOffice runs in flight (P-04: killed with their whole process tree on timeout and at exit)


def _kill_tree(proc) -> None:
    import signal
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def kill_all_soffice() -> None:
    """Stop every LibreOffice run this process started, with everything it started."""
    for pr in list(_LIVE):
        _kill_tree(pr)


import atexit as _atexit
_atexit.register(kill_all_soffice)


def _run_tree(cmd: list, timeout: int):
    """subprocess.run(capture_output, text) that runs the command in its own process group (a job on Windows) and kills
    the whole tree when the timeout passes: soffice starts a second process that plain kill() leaves running."""
    kw: dict = {"start_new_session": True} if os.name != "nt" else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)
    _LIVE.add(pr)
    try:
        out, err = pr.communicate(timeout=timeout)
    except BaseException:
        _kill_tree(pr)
        try:
            pr.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        _LIVE.discard(pr)
    return subprocess.CompletedProcess(cmd, pr.returncode, out, err)


def soffice_convert(src: str, to: str, out_dir: str, timeout: int = 240) -> Path:
    """Headless convert with a throwaway profile, so parallel runs never share a lock."""
    from meshpool_harness import office_runtime
    exe = find_soffice()
    if not exe:
        raise office_runtime.Unavailable()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        _profile_fonts(Path(profile))
        r = _run_tree([exe, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", to,
                       "--outdir", str(out_dir), str(src)], timeout)
    ext = to.split(":")[0]
    out = Path(out_dir) / (Path(src).stem + "." + ext)
    if not out.exists():
        raise RuntimeError(f"LibreOffice could not convert {Path(src).name}: {(r.stderr or r.stdout)[-400:]}")
    return out


# ------------------------------------------------------------------ fonts
# Open fonts bundled in engines/forge/fonts (SIL OFL): Selawik has Segoe UI's metrics, so slides set in Segoe UI are
# measured exactly before they are drawn.
BUNDLED = {"selawik": ("Selawik-Regular.ttf", "Selawik-Bold.ttf")}


@lru_cache(maxsize=64)
def font_file(family: str, bold: bool = False, italic: bool = False, metric_map: tuple = ()) -> tuple[str | None, bool]:
    """Returns (path, exact). exact is False when a metric-compatible substitute or a fallback was used."""
    mapping = dict(metric_map)
    style = ("Bold" if bold else "") + ("Italic" if italic else "")
    # fonts shipped with the engine come first: measuring is then exact on every computer (fc-match is Linux-only)
    for name in (family, mapping.get(family)):
        own = BUNDLED.get((name or "").lower())
        if own and not italic:
            path = Path(__file__).with_name("fonts") / own[1 if bold else 0]
            if path.exists():
                return str(path), name == family
    for name, exact in ((family, True), (mapping.get(family), False)):
        if not name:
            continue
        fc = shutil.which("fc-match")
        if fc:
            try:
                q = f"{name}:style={style or 'Regular'}"
                r = subprocess.run([fc, "-f", "%{family}\t%{file}", q], capture_output=True, text=True, timeout=10)
                fam, _, path = r.stdout.partition("\t")
                if path and name.lower() in fam.lower():
                    if bold and "bold" not in path.lower() and _is_variable(path):
                        path += "|Bold"
                    return path, exact
            except Exception:
                pass
    fallback = "/usr/share/fonts/truetype/crosextra/Carlito-" + ({"": "Regular"}.get(style, style)) + ".ttf"
    if Path(fallback).exists():
        return fallback, False
    return None, False


@lru_cache(maxsize=64)
def _is_variable(path: str) -> bool:
    try:
        from PIL import ImageFont
        return bool(ImageFont.truetype(path, 10).get_variation_names())
    except Exception:
        return False


@lru_cache(maxsize=512)
def _pil_font(path: str, size_pt: float):
    """`path` may carry '|Bold' (or another named instance) for variable fonts such as Gelasio."""
    from PIL import ImageFont
    path, _, inst = path.partition("|")
    f = ImageFont.truetype(path, max(1, int(round(size_pt * 10))))
    if inst:
        try:
            f.set_variation_by_name(inst)
        except Exception:
            pass
    return f


ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
ARABIC_FONT = {False: str(Path(__file__).parent / "fonts" / "NotoSansArabic-Regular.ttf"),
               True: str(Path(__file__).parent / "fonts" / "NotoSansArabic-Bold.ttf")}


def is_rtl(text: str, share: float = 0.3) -> bool:
    """Is this text mostly Arabic script (letters only counted)?"""
    letters = [c for c in text or "" if c.isalpha()]
    return bool(letters) and sum(bool(ARABIC.match(c)) for c in letters) / len(letters) >= share


@lru_cache(maxsize=64)
def _arabic_font(bold: bool, size_pt: float):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(ARABIC_FONT[bold], max(1, int(round(size_pt * 10))), layout_engine=ImageFont.Layout.RAQM)
    except Exception:  # noqa: BLE001 - no raqm: basic layout still gives the advance widths
        return ImageFont.truetype(ARABIC_FONT[bold], max(1, int(round(size_pt * 10))))


def text_width_in(text: str, size_pt: float, path: str | None) -> float:
    if text and ARABIC.search(text):                 # Arabic is measured shaped (joined letters), right to left, with
        bold = bool(path) and "bold" in path.lower()  # the bundled Noto Sans Arabic: Latin fonts have no Arabic glyphs
        f = _arabic_font(bold, size_pt)
        try:
            return f.getlength(text, direction="rtl") / 10 / 72
        except Exception:  # noqa: BLE001
            return f.getlength(text) / 10 / 72
    if not path:
        return len(text) * size_pt * 0.5 / 72          # crude average advance when no font file exists at all
    return _pil_font(path, size_pt).getlength(text) / 10 / 72


def wrap_lines(text: str, width_in: float, size_pt: float, path: str | None, safety: float = 0.95) -> int:
    usable = max(width_in * safety, 0.05)
    n = 0
    for para in text.split("\n"):
        cur = ""
        lines = 1
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if not cur or text_width_in(trial, size_pt, path) <= usable:
                cur = trial
            else:
                lines += 1
                cur = word
        n += lines
    return n


def positive_axis_ids(chart):
    """python-pptx's chart templates use negative axis ids; the schema says unsignedInt. Map them to positive ids."""
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    cs = chart._chartSpace
    seen = {}
    for el in cs.iter(f"{C}axId", f"{C}crossAx"):
        v = el.get("val")
        if v and v.startswith("-"):
            el.set("val", seen.setdefault(v, str(50000 + 1000 * len(seen) + abs(int(v)) % 1000)))


# ------------------------------------------------------------------ PowerPoint templates
_MAIN_TYPES = (b"application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
               b"application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
               b"application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml")


def open_pptx(path):
    """python-pptx Presentation for a .pptx, or a .potx / .pptm / .potm, which python-pptx refuses by the content
    type of the main part: those are opened from an in-memory copy with the type rewritten to a presentation's."""
    import io
    import zipfile
    from pptx import Presentation
    with zipfile.ZipFile(path) as zin:
        ct = zin.read("[Content_Types].xml")
        if not any(t in ct for t in _MAIN_TYPES):
            return Presentation(path)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    for t in _MAIN_TYPES:
                        data = data.replace(t, b"application/vnd.openxmlformats-officedocument.presentationml."
                                               b"presentation.main+xml")
                zout.writestr(item, data)
    buf.seek(0)
    return Presentation(buf)


# ------------------------------------------------------------------ numbers
# One extractor for every number check: the renderer records numbers with it (provenance) and P13 / P14 and the
# pre-render fact check read numbers with it, so what was recorded is exactly what is checked.
NUM = re.compile(r"(?<![\w.])[-+]?\$?\(?\d[\d,]*(?:\.\d+)?\)?\s?(?:%|pp|x|bn|b|m|mm|k|million|billion|thousand)?(?![\w])", re.I)


_RANGE_TAIL = re.compile(r"\s*(?:-|\u2013|to)\s*\$?\d[\d,]*(?:\.\d+)?\s?(%|pp|x|bn|mm|million|billion|thousand)(?![\w])", re.I)


def numbers(text: str, ignore: re.Pattern | None = None) -> list[tuple[str, float, str]]:
    """(raw, value, unit) for every number in text; years and small ordinals filtered by `ignore`."""
    out = []
    for m in NUM.finditer(text):
        raw = m.group(0).strip()
        core = re.sub(r"[^\d.]", "", raw.split()[0] if " " in raw else raw)
        if not core or core == ".":
            continue
        shared = None                                   # a range's first figure takes its unit: "81-83%" is 81% and
        if not re.search(r"(%|pp|x|bn|b|mm|m|k|million|billion|thousand)\s*\)?$", raw, re.I):   # 83%, not a bare 81
            r_ = _RANGE_TAIL.match(text, m.end())
            if r_:
                shared = r_.group(1)
        if ignore is not None and ignore.match(core) and not shared and \
                not re.search(r"%|\$|bn|b$|m$|k$|x$|million|billion|thousand", raw, re.I):
            continue
        try:
            v = float(core)
        except ValueError:
            continue
        if raw.startswith("-") or (raw.startswith("(") and ")" in raw):   # (7.1) is a negative; "(7.1%" is an aside
            v = -v
        unit = (re.search(r"(%|pp|x|bn|b|mm|m|k|million|billion|thousand)\s*$", raw, re.I) or [None])[0]
        unit = unit.lower() if unit else (shared or "")
        out.append((raw, v, unit))
    return out


SCALE = {"": 1, "k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def candidates(v: float, unit: str) -> list[float]:
    """A quoted number may be stated in the chart's own units or scaled: compare against all plausible readings."""
    if unit in ("%", "pp"):
        return [v, v / 100]
    s = SCALE.get(unit, 1)
    return sorted({v, v * s, v * s / 1e3, v * s / 1e6, v * s / 1e9})
