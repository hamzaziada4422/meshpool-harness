"""Audit ws13 (config and install): M16-01, M16-03, M16-05, C-01, C-02, C-90, C-04, C-08, C-09, C-21, C-23, F-02, F-19."""
import ast
import json
import multiprocessing as mp
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from meshpool_harness import config as C

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
UI = APP / "meshpool_harness" / "ui"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "meshpool.config"
    monkeypatch.setenv("MESHPOOL_CONFIG", str(p))
    return p


# ---- M16-05 (already fixed): config.write holds a cross-process lock -----------------------------------------------
def _writer(path, prefix, n):
    from meshpool_harness import config as C2
    for i in range(n):
        C2.write({f"{prefix}{i}": "x"}, Path(path))


def test_M16_05_config_write_cross_process(tmp_path):
    p = tmp_path / "meshpool.config"
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(p), f"P{k}_", 15)) for k in range(3)]
    [x.start() for x in procs]
    [x.join(120) for x in procs]
    vals = C.read(p)
    assert len(vals) == 45, f"lost {45 - len(vals)} writes"


# ---- C-04 (already fixed): the running-instance probe wants the real health answer -----------------------------------
def _serve(app_name):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"app": app_name}).encode())

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_C_04_running_instance(tmp_path, monkeypatch):
    from meshpool_harness.ui import desktop as D
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    (tmp_path / "runs").mkdir()
    f = tmp_path / "runs" / ".app.json"
    other = _serve("something-else")
    f.write_text(json.dumps({"port": other.server_port, "pid": 1}))
    assert D._running_instance() is None                      # another program on the port is not Meshpool
    other.shutdown()
    mine = _serve("meshpool")
    f.write_text(json.dumps({"port": mine.server_port, "pid": 1}))
    assert D._running_instance() == f"http://127.0.0.1:{mine.server_port}/"
    mine.shutdown()
    assert D._running_instance() is None                      # stale file, nothing listening
    f.write_text("{not json")
    assert D._running_instance() is None


# ---- C-21 (already fixed): install.ps1 -App installs the app --------------------------------------------------------
def test_C_21_install_ps1_app_switch():
    s = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert re.search(r'\$args -contains "-App"', s) and "function Install-App" in s
    body = s[s.index('$args -contains "-App"'):]
    assert "Install-App" in body.split("}", 1)[0] + body[:300]


# ---- C-23 (already fixed): every third-party import in app/ is declared ----------------------------------------------
# Imports that may be missing on purpose: each must only ever be imported lazily (in a function or a try block).
OPTIONAL = {"AppKit", "Quartz", "mss", "webview", "fastembed", "pbixray", "rapidocr", "pymupdf", "defusedxml",
            "certifi", "eval", "ddgs", "pillow_heif", "onnxruntime"}
DIST = {"PIL": "pillow", "docx": "python-docx", "pptx": "python-pptx", "yaml": "pyyaml", "pdfminer": "pdfminer.six",
        "cairosvg": "cairosvg", "lxml": "lxml", "openpyxl": "openpyxl", "pandas": "pandas", "playwright": "playwright",
        "psutil": "psutil", "pypdf": "pypdf", "pypdfium2": "pypdfium2", "reportlab": "reportlab", "tomli": "tomli",
        "xlrd": "xlrd", "olefile": "olefile", "numpy": "pandas"}   # numpy comes with pandas


def _declared():
    names = set()
    for f in (APP / "requirements.txt", ROOT / "packaging" / "requirements-cli.txt"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)", line)
            if m and not line.lstrip().startswith("#"):
                names.add(m.group(1).lower().replace("_", "-"))
    return names


def test_C_23_requirements_cover_imports():
    local = {p.stem for p in (APP / "meshpool_harness").iterdir()} | {p.stem for p in (APP / "engines").iterdir()}
    local |= {"meshpool_harness", "engines", "tests", "meshpool_recover", "sitecustomize"}
    declared, missing, eager = _declared(), set(), set()
    for d in ("meshpool_harness", "engines"):
        for p in (APP / d).rglob("*.py"):
            if p.name == "build_bundles.py":                  # a build-time tool, not shipped code
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            guarded = {id(n) for t in ast.walk(tree) if isinstance(t, (ast.Try, ast.FunctionDef, ast.AsyncFunctionDef))
                       for n in ast.walk(t)}
            for n in ast.walk(tree):
                mods = ([a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else
                        [n.module.split(".")[0]] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else [])
                for m in mods:
                    if m in sys.stdlib_module_names or m in local:
                        continue
                    if m in OPTIONAL:
                        if id(n) not in guarded:
                            eager.add(f"{m} in {p.name}")
                    elif DIST.get(m, m).lower().replace("_", "-") not in declared:
                        missing.add(m)
    assert not missing, f"imported but not in requirements: {sorted(missing)}"
    assert not eager, f"optional packages imported at module level: {sorted(eager)}"


# ---- M16-01: clearing a setting clears the environment --------------------------------------------------------------
def test_M16_01_cleared_value_leaves_environment(cfg, monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    C.write({"BRAVE_SEARCH_API_KEY": "abc"}, cfg)
    C.load_into_env()
    assert os.environ["BRAVE_SEARCH_API_KEY"] == "abc"
    C.write({"BRAVE_SEARCH_API_KEY": ""}, cfg)
    C.load_into_env(override=True)
    assert "BRAVE_SEARCH_API_KEY" not in os.environ
    monkeypatch.setenv("TAVILY_API_KEY", "from-the-shell")          # a value the user's shell set is not ours to clear
    C.write({"TAVILY_API_KEY": ""}, cfg)
    C.load_into_env()
    assert os.environ["TAVILY_API_KEY"] == "from-the-shell"


# ---- M16-03: a favourite is a read-modify-write under the lock --------------------------------------------------------
def test_M16_03_set_favorite_is_atomic(cfg, monkeypatch):
    from meshpool_harness.ui import workbench as W
    monkeypatch.setattr(W, "models", lambda *a, **k: {})
    real = C.read

    def slow_read(*a, **k):                  # widen the window between a reader's read and its write
        v = real(*a, **k)
        import time
        time.sleep(0.05)
        return v
    monkeypatch.setattr(C, "read", slow_read)
    ts = [threading.Thread(target=W.set_favorite, args=(f"m{i}", True)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    monkeypatch.setattr(C, "read", real)
    got = set((real(cfg).get("MH_FAVORITE_MODELS") or "").split(","))
    assert got == {f"m{i}" for i in range(8)}


# ---- C-01: a UTF-16 settings file ------------------------------------------------------------------------------------
@pytest.mark.parametrize("enc", ["utf-16", "utf-16-le", "utf-16-be"])
def test_C_01_utf16_settings(cfg, enc):
    cfg.write_bytes("MESHPOOL_API_KEY=sk-1\r\nMH_MODEL=m1\r\n".encode(enc))
    assert C.read(cfg) == {"MESHPOOL_API_KEY": "sk-1", "MH_MODEL": "m1"}
    assert b"\x00" not in cfg.read_bytes()                            # rewritten as UTF-8
    C.write({"MH_MODEL": "m2"}, cfg)
    assert C.read(cfg) == {"MESHPOOL_API_KEY": "sk-1", "MH_MODEL": "m2"}
    cfg.write_bytes("MH_MODEL=a\n".encode("utf-16"))
    C.write({"X": "1"}, cfg)                                          # a save on a UTF-16 file keeps its keys
    assert C.read(cfg) == {"MH_MODEL": "a", "X": "1"}


# ---- C-02, C-90: no settings or caches in the source tree ----------------------------------------------------------
def test_C_02_home_is_the_user_data_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPOOL_HOME", raising=False)
    monkeypatch.delenv("MESHPOOL_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(C.sys, "platform", "linux")
    assert C.home() == tmp_path / "xdg" / "meshpool"
    monkeypatch.setattr(C.sys, "platform", "darwin")
    assert C.home() == tmp_path / "Library" / "Application Support" / "Meshpool"
    monkeypatch.setattr(C.sys, "platform", "linux")
    h = C.ensure_home()
    assert (h / "meshpool.config").exists() and (h / "mcp.json").exists()
    assert C.APP.parent not in h.parents and h != C.APP.parent
    from meshpool_harness.agent import mcp as M
    assert C.APP.parent not in M._tools_cache_path().parents if hasattr(M, "_tools_cache_path") else True
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "own"))        # the override stays
    assert C.home() == tmp_path / "own"


def test_C_02_source_tree_home_is_migrated(tmp_path, monkeypatch):
    old = tmp_path / "checkout"
    (old / "app").mkdir(parents=True)
    (old / "meshpool.config").write_text("MH_MODEL=kept\n")
    (old / "runs").mkdir()
    (old / "runs" / "r1").write_text("x")
    monkeypatch.setattr(C, "APP", old / "app")
    monkeypatch.delenv("MESHPOOL_HOME", raising=False)
    monkeypatch.delenv("MESHPOOL_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(C.sys, "platform", "linux")
    h = C.ensure_home()
    assert C.read(h / "meshpool.config").get("MH_MODEL") == "kept"
    assert (h / "runs" / "r1").exists()
    assert (old / "meshpool.config").exists()                         # copied, never moved
    assert not (old / "mcp.json").exists()                            # nothing new written into the tree


# ---- C-08: one permission setting, read when a chat is created --------------------------------------------------------
def test_C_08_permission_mode_reaches_new_chats(cfg, monkeypatch):
    monkeypatch.delenv("MH_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("MH_AGENT_PERMISSIONS", "ask")                 # the stale value the process loaded earlier
    C.write({"MH_AGENT_PERMISSIONS": "edits"}, cfg)                   # then the file was edited by hand
    assert C.permission_mode() == "edits"
    C.write({"MH_AGENT_PERMISSIONS": "Full access"}, cfg)
    assert C.permission_mode() == "auto"
    monkeypatch.setenv("MH_PERMISSION_MODE", "accept-edits")          # the CLI's one-off setting
    assert C.permission_mode() == "edits"
    monkeypatch.setenv("MH_PERMISSION_MODE", "plan")                  # plan is not an access level
    assert C.permission_mode() == "auto"
    from meshpool_harness.agent.core import Agent
    monkeypatch.delenv("MH_PERMISSION_MODE")
    C.write({"MH_AGENT_PERMISSIONS": "supervised"}, cfg)
    assert Agent(str(cfg.parent)).mode == "ask"
    C.write({"MH_AGENT_PERMISSIONS": "edits"}, cfg)
    assert Agent(str(cfg.parent)).mode == "edits"


# ---- C-09: the file never replaces what belongs to the process --------------------------------------------------------
def test_C_09_process_variables_are_not_overridden(cfg, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("MESHPOOL_HOME", "/real/home")
    cfg.write_text("PATH=/evil\nMESHPOOL_HOME=/evil\nHOME=/evil\nMH_MODEL=fine\n")
    C.load_into_env(override=True)
    assert os.environ["PATH"].startswith("/usr/bin") and os.environ["MESHPOOL_HOME"] == "/real/home"
    assert os.environ["HOME"] != "/evil" and os.environ["MH_MODEL"] == "fine"
    monkeypatch.delenv("MH_MODEL")


# ---- F-02: a per-chat Full access choice stays per chat -----------------------------------------------------------------
def test_F_02_per_chat_mode_is_not_the_default():
    js = (UI / "app.js").read_text(encoding="utf-8")
    fn = js[js.index("async function setMode("):]
    fn = fn[:fn.index("\n}\n")]
    assert 'store("mode"' not in fn, "choosing an access level for one chat must not be remembered for later chats"
    assert 'store("mode"' not in js and 'store("mode"' not in (UI / "settings2.js").read_text(encoding="utf-8")
    assert re.search(r'draft: \{[^}]*mode: ""', js)                    # a new draft has no mode of its own
    assert 'S.draft.mode = "";' in js                                 # and a new chat resets it


# ---- F-19: one set of names ----------------------------------------------------------------------------------------------
def test_F_19_one_set_of_permission_names():
    for f in ("app.js", "composer.js", "settings2.js"):
        s = (UI / f).read_text(encoding="utf-8")
        for old in ("Supervised", "Auto-accept", '"Manual"'):
            body = re.sub(r"const MODE_ALIAS = .*", "", s)
            assert old not in body.replace('supervised', ''), f"{f}: {old}"
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert '["Ask",' in app and '["Accept edits",' in app and '["Full access",' in app
    for f in (APP / "meshpool_harness" / "config.py", APP / "meshpool_harness" / "agent" / "tui_render.py",
              APP / "meshpool_harness" / "agent" / "onboard.py"):
        s = f.read_text(encoding="utf-8")
        assert "Supervised" not in s.replace('"supervised"', "") and "Auto-accept edits changes" not in s
    from meshpool_harness.agent import tui_render as R
    assert R.MODE_NAMES == {"ask": "ask", "edits": "accept edits", "auto": "full access"}
    for alias in ("Manual", "supervised", "Auto-accept edits", "accept-edits", "full-access", "FULL ACCESS", "edits", "auto"):
        assert C.normalize_mode(alias) in ("ask", "edits", "auto")
    assert [C.normalize_mode(x) for x in ("manual", "Supervised", "auto-accept edits", "accept edits", "full-access")] == \
        ["ask", "ask", "edits", "edits", "auto"]
    import argparse
    from meshpool_harness import cli
    ap = cli.build_parser() if hasattr(cli, "build_parser") else None
    if ap is not None:
        ns = ap.parse_args(["--permission-mode", "full-access"])
        assert ns.permission_mode == "auto"


def test_workflow_runs_python_310_and_311():
    y = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    m = re.search(r"python-version: \[([^\]]*)\]", y)
    assert m and '"3.10"' in m.group(1) and '"3.11"' in m.group(1)
