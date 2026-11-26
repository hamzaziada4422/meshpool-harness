"""Pass 2 open rows: iframes in the browser snapshot (P2-engines-10), empty folders a turn made and Undo
(P2-toplevel-13), the Python packages in THIRD_PARTY_NOTICES.md (P2-packaging-2) and the Linux system-library list of
the LibreOffice dependency check (P2-packaging-8)."""
import importlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

from meshpool_harness.checkpoints import Checkpoints

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))


# ---------- P2-engines-10: a button inside an iframe is listed and clickable ------------------------------------------
FRAMED = """<html><head><title>Framed</title></head><body><h1>Outer page</h1>
<iframe title="Payment" srcdoc="<button onclick=&quot;document.getElementById('out').innerText='Paid '+(++window.n||(window.n=1))&quot;>Pay now</button><p id='out'></p>"></iframe>
</body></html>"""


def _serve_dir(d) -> str:
    import functools
    import http.server
    import threading
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def test_P2_engines_10_iframe_button_is_listed_and_clickable(monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.delenv("MH_BROWSER_PORT", raising=False)
    monkeypatch.setenv("MESHPOOL_HOME", tempfile.mkdtemp())
    if os.environ.get("MH_TEST_CHROMIUM"):                  # a Chromium of another Playwright version, for local runs
        monkeypatch.setenv("MH_CHROMIUM_PATH", os.environ["MH_TEST_CHROMIUM"])
    from engines.browser import ctl
    importlib.reload(ctl)
    d = tempfile.mkdtemp()
    Path(d, "f.html").write_text(FRAMED)
    assert ctl.main(["start"]) == 0
    frame_out = "document.querySelector('iframe').contentDocument.getElementById('out').innerText"
    try:
        snap = ctl.call("goto", f"{_serve_dir(d)}/f.html")
        line = next((e for e in snap["elements"] if 'button "Pay now"' in e), None)
        assert line, snap["elements"]                       # the frame's button has a number like the page's own
        assert not any("contents not listed" in e for e in snap["elements"])
        res = ctl.call("click", re.match(r"\[(\d+)\]", line).group(1))
        assert "error" not in res, res
        assert ctl.call("_eval", frame_out)["value"] == "Paid 1"
        res = ctl.call("batch", json.dumps([{"do": "click", "target": {"text": "Pay now"}}]))
        assert res["batch"]["ok"], res["batch"]             # {"text": ...} reaches into the frame
        assert ctl.call("_eval", frame_out)["value"] == "Paid 2"
        res = ctl.call("batch", json.dumps([{"do": "click", "target": {"role": "button", "name": "Pay now"}}]))
        assert res["batch"]["ok"], res["batch"]
        assert "error" not in ctl.call("click_text", "Pay now")
        assert ctl.call("_eval", frame_out)["value"] == "Paid 4"
    finally:
        ctl.call("stop")


# ---------- P2-toplevel-13: Undo removes the empty folders a turn made --------------------------------------------------
def test_P2_toplevel_13_undo_removes_empty_folders_the_turn_made(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text("v1\n")
    (root / "old_empty").mkdir()                            # the user's own empty folder, there before the turn
    cp = Checkpoints(str(root), tmp_path / "store")
    cp.begin_turn("agent makes folders")
    (root / "made").mkdir()                                 # empty
    (root / "deep" / "er").mkdir(parents=True)              # empty, nested
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("x\n")             # a folder with an agent file in it
    (root / "shared").mkdir()                               # the user puts a file in it after the turn
    cp.end_turn()
    (root / "shared" / "mine.txt").write_text("mine\n")
    r = cp.revert_to_before(1)
    assert not (root / "made").exists() and not (root / "deep").exists() and not (root / "pkg").exists()
    assert (root / "old_empty").is_dir()
    assert (root / "shared" / "mine.txt").read_text() == "mine\n"
    assert any(n.startswith("shared/") for n in r["notes"]), r
    assert r["deleted"] == ["pkg/mod.py"]


def test_P2_toplevel_13_undo_removes_folders_a_write_made_in_journal_mode(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "src").mkdir()
    cp = Checkpoints(str(root), tmp_path / "store", mode="journal", shared=tmp_path / "shared")
    cp.begin_turn("agent writes into new folders")
    with cp.tool("write_file", {"path": "src/new/deeper/a.py"}):
        (root / "src" / "new" / "deeper").mkdir(parents=True)
        (root / "src" / "new" / "deeper" / "a.py").write_text("a\n")
    with cp.tool("bash", {"command": "mkdir -p empty/inner"}):
        (root / "empty" / "inner").mkdir(parents=True)
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert r["deleted"] == ["src/new/deeper/a.py"]
    assert not (root / "src" / "new").exists() and not (root / "empty").exists()
    assert (root / "src").is_dir()                          # it was there before the turn


# ---------- P2-packaging-2: the Python packages table matches what the builds install ----------------------------------
def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _notice_packages() -> dict:
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    section = text.split("## Python packages", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("| Package"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for name in re.sub(r"\(.*?\)", "", cells[0]).split(","):   # "nbformat, nbclient, ..." rows name several
            out.setdefault(_norm(name.strip()), []).append(cells[1])
    return out


def _requirement_names() -> set:
    files = [*(ROOT / "packaging").glob("requirements*.txt"), ROOT / "packaging" / "mac" / "constraints.txt",
             ROOT / "packaging" / "python-packages.txt"]
    names = set()
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)", line.split("#", 1)[0])
            if m:
                names.add(_norm(m.group(1)))
    return names


def test_P2_packaging_2_every_notice_package_is_in_a_requirements_or_constraints_file():
    table, known = _notice_packages(), _requirement_names()
    assert sorted(set(table) - known) == []
    for gone in ("pbixray", "rapidocr", "opencv-python-headless", "omegaconf", "pyclipper", "shapely"):
        assert gone not in table, gone                      # no build installs these
    assert table["onnxruntime"] == ["1.19.2"]               # 1.31.1 was never installed (no macOS 11 build)
    assert "nemo-relay" in table                            # a Hermes dependency, so it stays
    record = {_norm(m.group(1)) for line in (ROOT / "packaging" / "python-packages.txt").read_text().splitlines()
              if (m := re.match(r"([A-Za-z0-9][A-Za-z0-9_.\-]*)", line))}
    assert sorted(record - set(table)) == []                # and every installed package is in the table


# ---------- P2-packaging-8: the Linux system libraries are the ones a desktop has --------------------------------------
def _lo_deps():
    sys.path.insert(0, str(ROOT / "packaging"))
    import lo_deps_check
    return importlib.reload(lo_deps_check)


def _smoke_packages() -> set:
    """The Debian packages the release workflow's bare Ubuntu 22.04 smoke test installs: what a desktop has."""
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    m = re.search(r"apt-get install -y -q --no-install-recommends ca-certificates (.*?)>/dev/null", text, re.DOTALL)
    assert m, "the smoke job's package list moved"
    return set(m.group(1).replace("\\", " ").split()) | {"ca-certificates"}


# library prefix -> the package that provides it: the smoke job's list, or the base system every Linux has
BASE = {"libc6", "libgcc-s1", "libstdc++6", "zlib1g"}
PROVIDER = {
    "libc.so": "libc6", "libm.so": "libc6", "libdl.so": "libc6", "libpthread.so": "libc6", "librt.so": "libc6",
    "libresolv.so": "libc6", "libutil.so": "libc6", "ld-linux": "libc6", "libgcc_s": "libgcc-s1",
    "libstdc++": "libstdc++6", "libz.so": "zlib1g",
    "libX11.so": "libx11-6", "libX11-xcb.so": "libx11-xcb1", "libxcb.so": "libxcb1", "libXext.so": "libxext6",
    "libXrender.so": "libxrender1", "libXinerama.so": "libxinerama1", "libXrandr.so": "libxrandr2",
    "libXcomposite.so": "libxcomposite1", "libXdamage.so": "libxdamage1", "libXfixes.so": "libxfixes3",
    "libxkbcommon.so": "libxkbcommon0", "libSM.so": "libsm6", "libICE.so": "libice6",
    "libfontconfig.so": "fontconfig", "libfreetype.so": "libfreetype6",
    "libnss3.so": "libnss3", "libnssutil3.so": "libnss3", "libsmime3.so": "libnss3", "libssl3.so": "libnss3",
    "libnspr4.so": "libnspr4", "libplc4.so": "libnspr4", "libplds4.so": "libnspr4",
    "libdbus-1.so": "libdbus-1-3", "libglib-2.0.so": "libglib2.0-0", "libgobject-2.0.so": "libglib2.0-0",
    "libgio-2.0.so": "libglib2.0-0", "libgmodule-2.0.so": "libglib2.0-0", "libcups.so": "libcups2",
    "libavahi-client.so": "libavahi-client3", "libavahi-common.so": "libavahi-client3", "libcairo.so": "libcairo2",
    "libpango-1.0.so": "libpango-1.0-0", "libatk-1.0.so": "libatk1.0-0", "libatk-bridge-2.0.so": "libatk-bridge2.0-0",
    "libdrm.so": "libdrm2", "libgbm.so": "libgbm1", "libasound.so": "libasound2",
    "libgssapi_krb5.so": "libcurl4",                        # pulled in by libcurl4; the bundled libcurl.so.4 needs it
}


def test_P2_packaging_8_every_linux_system_library_is_on_a_desktop():
    LD = _lo_deps()
    smoke = _smoke_packages()
    for prefix in LD.LINUX_SYSTEM_PREFIXES:
        assert prefix in PROVIDER, f"{prefix} is not in the documented desktop set"
        assert PROVIDER[prefix] in smoke | BASE, f"{prefix}: {PROVIDER[prefix]} is not installed by the smoke job"
    # what the bundled LibreOffice carries itself must never pass as a system library
    for bundled in ("libcurl.so.4", "libicuuc.so.78", "libxml2.so.16", "libxslt.so.1", "libssl.so.3", "libcrypto.so.3",
                    "libpng16.so.16", "libjpeg.so.8", "libharfbuzz.so.0", "liblcms2.so.2", "libGL.so.1"):
        assert not bundled.startswith(LD.LINUX_SYSTEM_PREFIXES), bundled
    setup = (ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8")
    for group in ("X11", "fontconfig", "NSS", "GLib", "Cairo", "Pango", "ALSA", "CUPS", "Avahi"):
        assert group in setup


def test_P2_packaging_8_a_pruned_bundled_library_is_reported(tmp_path, monkeypatch):
    LD = _lo_deps()
    prog = tmp_path / "program"
    prog.mkdir()
    needs = {"libmergedlo.so": ["libc.so.6", "libX11.so.6", "libcurl.so.4", "libnss3.so", "libicuuc.so.78"],
             "libicuuc.so.78": ["libstdc++.so.6"],
             "liblibreofficekitgtk.so": ["libgtk-3.so.0", "libgdk-3.so.0"]}   # never loaded by soffice
    for name in needs:
        (prog / name).write_bytes(b"\x7fELF" + b"\0" * 60)
    monkeypatch.setattr(LD, "elf_needed", lambda p: needs.get(p.name, []))
    assert sorted(LD.check(tmp_path, "linux")) == ["libcurl.so.4"]     # bundled, pruned by mistake: not a system one
