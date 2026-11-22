"""First run from the user's own files, and "While you were away".

    GET  /api/firstrun/scan?folder=      3 starters tied to real files: only names, types, sizes and dates are read
    POST /api/firstrun/samples           copy 3 small bundled samples into "Meshpool samples" and return a starter
    GET  /api/away                       finished runs and cards since the user last looked
    POST /api/away/seen                  the user has looked
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs

from meshpool_harness import config as C

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "Library", "AppData"}
SAMPLES = ("Q3_sales.xlsx", "meeting-notes.docx", "customer-feedback.csv")
SAMPLE_PROMPT = "Summarize Q3_sales.xlsx in one slide: the headline, the regions behind target, and one next step."


def _allowed(folder: str) -> Path | None:
    """Only folders the user already gave Meshpool, or its own project folder."""
    try:
        p = Path(folder).expanduser().resolve()
    except (OSError, ValueError):
        return None
    if not p.is_dir():
        return None
    try:
        from meshpool_harness.ui import workbench as W
        from meshpool_harness.ui.desktop import default_project
        roots = [Path(r["path"]).resolve() for r in W.projects() if r.get("path")] + [default_project().resolve()]
        if any(p == r or r in p.parents for r in roots):
            return p
    except Exception:  # noqa: BLE001
        return p
    return None


def starters_for(files: list[dict]) -> list[dict]:
    """Up to 3 organising starters from file names and types (never contents), most recent files first."""
    files = sorted(files, key=lambda f: -f["mtime"])
    pick: list[dict] = []
    used_kind: set = set()
    rules = [("sheet", lambda n: f"Summarize {n} in one slide"),
             ("doc", lambda n: f"Turn {n} into an action plan"),
             ("deck", lambda n: f"Turn {n} into a one-page memo"),
             ("pdf", lambda n: f"Summarize {n} in five bullet points"),
             ("text", lambda n: f"Turn {n} into an action plan")]
    for kind, make in rules:
        f = next((x for x in files if x["kind"] == kind), None)
        if f and kind not in used_kind and len(pick) < 3:
            used_kind.add(kind)
            pick.append({"label": make(f["name"]).split(" in ")[0][:48], "prompt": make(f["name"]) + ".", "file": f["path"],
                         "name": f["name"]})
    return pick[:3]


KINDS = {".xlsx": "sheet", ".xls": "sheet", ".csv": "sheet", ".docx": "doc", ".doc": "doc", ".pptx": "deck",
         ".pdf": "pdf", ".md": "text", ".txt": "text"}


def scan(folder: str, limit: int = 300) -> dict:
    p = _allowed(folder)
    if p is None:
        return {"files": 0, "starters": []}
    files: list[dict] = []
    depth0 = len(p.parts)
    for f in p.rglob("*"):
        if len(files) >= limit or len(f.parts) - depth0 > 3:
            break
        if any(part in SKIP_DIRS or part.startswith(".") for part in f.relative_to(p).parts):
            continue
        kind = KINDS.get(f.suffix.lower())
        if kind and f.is_file():
            try:
                st = f.stat()
            except OSError:
                continue
            files.append({"name": f.name, "path": str(f), "kind": kind, "size": st.st_size, "mtime": st.st_mtime})
    return {"files": len(files), "starters": starters_for(files), "folder": str(p)}


def samples() -> dict:
    from meshpool_harness.ui.desktop import default_project
    dst = default_project() / "Meshpool samples"
    dst.mkdir(parents=True, exist_ok=True)
    src = C.APP / "defaults" / "samples"
    for n in SAMPLES:
        if (src / n).is_file() and not (dst / n).exists():
            shutil.copy2(src / n, dst / n)
    try:
        from meshpool_harness.ui import workbench as W
        W.add_project(str(dst), "Meshpool samples")
    except Exception:  # noqa: BLE001
        pass
    return {"folder": str(dst), "files": [n for n in SAMPLES if (dst / n).exists()], "prompt": SAMPLE_PROMPT}


def handle(method: str, u, body: bytes = b""):
    import json
    q = parse_qs(u.query)
    if u.path == "/api/firstrun/scan" and method == "GET":
        return 200, scan((q.get("folder") or [""])[0])
    if u.path == "/api/firstrun/samples" and method == "POST":
        return 200, samples()
    if u.path == "/api/away" and method == "GET":
        from meshpool_harness import away
        return 200, away.report()
    if u.path == "/api/away/seen" and method == "POST":
        from meshpool_harness import away
        away.mark_seen()
        return 200, {"ok": True}
    return 404, {"error": "not found"}
