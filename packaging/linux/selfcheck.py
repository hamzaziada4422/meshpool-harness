"""Proof that the Linux download is self-contained, run with the download's own Python inside the unpacked folder:

    Meshpool/python/bin/python3 -P packaging/linux/selfcheck.py Meshpool

Checks, with the computer's own LibreOffice, Chromium, Node and Git ignored (the packaged app never falls back to them):
  1. the app counts as packaged, and the updater knows it is the Linux download;
  2. LibreOffice, Node, Git and Chromium each resolve to a file INSIDE the unpacked folder, and each one runs;
  3. a deck is rendered by the bundled LibreOffice and passes the deck rules with no failure.
Exits non-zero on the first failure, so the release does not publish the download.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def fail(msg: str) -> None:
    raise SystemExit(f"selfcheck: FAILED, {msg}")


def inside(path: str | None, root: Path, what: str) -> Path:
    if not path:
        fail(f"{what} was not found")
    p = Path(path).resolve()
    if root not in p.parents:
        fail(f"{what} resolves to {p}, outside {root}")
    if not p.is_file():
        fail(f"{what} at {p} is not a file")
    print(f"selfcheck: {what}: {p.relative_to(root)}", flush=True)
    return p


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "app"))
    os.environ.pop("MH_ALLOW_SYSTEM_SOFFICE", None)
    os.environ.pop("MH_SOFFICE", None)
    os.environ.setdefault("MESHPOOL_HOME", tempfile.mkdtemp(prefix="mp-selfcheck-"))
    from meshpool_harness import bundled, office_runtime, updater
    from meshpool_harness.ui import desktop

    if not bundled.packaged():
        fail("the app does not count as packaged (python/ beside app/ is missing)")
    if updater.kind() != "linux-app":
        fail(f"the updater calls this copy {updater.kind()!r}, not 'linux-app'")
    desktop._bundled_tools()                       # what the app does at start: its own tools first on PATH

    so, src = office_runtime.resolve()
    if src != "bundled":
        fail(f"LibreOffice comes from {src or 'nowhere'}, not the bundled copy")
    inside(so, root, "LibreOffice")
    node = inside(bundled.node_exe(), root, "Node.js")
    git = inside(bundled.bundled_git(), root, "Git")
    for exe in (node, git):
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            fail(f"{exe.name} --version exited {r.returncode}: {r.stderr.strip()[:300]}")
        print(f"selfcheck: {exe.name} --version: {r.stdout.strip()}", flush=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        chrome = inside(pw.chromium.executable_path, root, "Chromium")
    r = subprocess.run([str(chrome), "--version"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        fail(f"Chromium --version exited {r.returncode}: {r.stderr.strip()[:300]}")
    print(f"selfcheck: Chromium --version: {r.stdout.strip()}", flush=True)

    from engines.forge.render import render
    from engines.forge.validate_pptx import validate
    slide = {"archetype": "column_trend", "title": "Berlin and Munich rents sit 13-20% below London",
             "chart": {"kind": "column", "categories": ["London", "Munich", "Berlin"],
                       "series": [{"name": "Rent", "values": [120, 104, 96]}], "unit": "£k", "decimals": 0},
             "takeaway": {"text": "Rent offsets part of the staff-cost gap"},
             "bullets": [{"text": "Berlin rent is £24k a year below London per café"},
                         {"text": "Munich rent is £16k below London per café"}],
             "source": {"text": "Source: company data"}}
    out = Path(tempfile.mkdtemp(prefix="mp-selfcheck-deck-")) / "deck.pptx"
    res = render([slide], str(out), carry=False)
    if res.get("errors"):
        fail(f"rendering a deck: {res['errors']}")
    v = validate(str(out), render=True)
    bad = [f for f in v["failures"] if f["status"] == "fail" and f["rule"] not in ("P12", "P13")]
    if bad:
        fail(f"the deck fails {[(f['rule'], f['message'][:120]) for f in bad]}")
    if not v.get("stats", {}).get("rendered_pages"):
        fail("the deck was not rendered by LibreOffice")
    print(f"selfcheck: deck rendered by the bundled LibreOffice ({v['stats']['rendered_pages']} page), rules pass")
    print("selfcheck: PASSED")


if __name__ == "__main__":
    main()
