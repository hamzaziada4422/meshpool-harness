"""Supports Python 3.10+ (B12, H-12): nothing may need newer syntax or stdlib than 3.10 without a fallback."""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
DIRS = [APP / "meshpool_harness", APP / "engines", APP / "tests", ROOT / "testkit", ROOT / "packaging"]
SWEEP = [APP / "meshpool_harness", APP / "engines", ROOT / "packaging", ROOT / "testkit"]


def _pyfiles(dirs):
    for d in dirs:
        if d.is_dir():
            yield from (p for p in sorted(d.rglob("*.py")) if "__pycache__" not in p.parts)


def _find_python(minor: int):
    env = os.environ.get(f"MH_PY3{minor}")
    if env and Path(env).exists():
        return env
    if shutil.which("uv"):
        try:
            r = subprocess.run(["uv", "python", "find", f"3.{minor}"], capture_output=True, text=True, timeout=60)
            out = r.stdout.strip().splitlines()
            if r.returncode == 0 and out and Path(out[-1]).exists():
                return out[-1]
        except (OSError, subprocess.SubprocessError):
            pass
    return shutil.which(f"python3.{minor}")


_COMPILE = ("import sys\nbad = 0\n"
            "for p in sys.argv[1:]:\n"
            "    try:\n        compile(open(p, 'rb').read(), p, 'exec')\n"
            "    except SyntaxError as e:\n        bad += 1; print(f'{p}:{e.lineno}: {e.msg}')\n"
            "sys.exit(1 if bad else 0)\n")


def _compile_with(py: str):
    files = [str(p) for p in _pyfiles(DIRS)]
    r = subprocess.run([py, "-c", _COMPILE, *files], capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def test_every_file_compiles_on_python_310():
    py = _find_python(10)
    if not py:
        if os.environ.get("CI"):
            pytest.fail("no Python 3.10 found (set MH_PY310, or install uv or python3.10): this check must run in CI")
        pytest.skip("no Python 3.10 interpreter found")
    code, out = _compile_with(py)
    assert code == 0, "syntax newer than Python 3.10:\n" + out


def test_every_file_compiles_on_python_311():
    py = _find_python(11)
    if not py:
        pytest.skip("no Python 3.11 interpreter found")
    code, out = _compile_with(py)
    assert code == 0, "syntax newer than Python 3.11:\n" + out


# name -> the Python version that introduced it; flagged unless inside a try with a handler
TYPING_NEW = {"Self": "3.11", "override": "3.12", "LiteralString": "3.11", "Never": "3.11", "assert_never": "3.11",
              "reveal_type": "3.11", "TypeVarTuple": "3.11", "Unpack": "3.11", "NotRequired": "3.11",
              "Required": "3.11"}
ATTRS = {("datetime", "UTC"): "3.11", ("itertools", "batched"): "3.12", ("asyncio", "TaskGroup"): "3.11",
         ("asyncio", "timeout"): "3.11", ("contextlib", "chdir"): "3.11", ("enum", "StrEnum"): "3.11",
         ("hashlib", "file_digest"): "3.11"}
FROM_NAMES = {("datetime", "UTC"): "3.11", ("itertools", "batched"): "3.12", ("contextlib", "chdir"): "3.11",
              ("enum", "StrEnum"): "3.11", ("hashlib", "file_digest"): "3.11", ("asyncio", "TaskGroup"): "3.11",
              ("asyncio", "timeout"): "3.11"}
BARE = {"ExceptionGroup": "3.11", "BaseExceptionGroup": "3.11"}


def _guarded(node, parents):
    n = node
    while n in parents:
        n = parents[n]
        if isinstance(n, ast.Try) and n.handlers:
            return True
    return False


def scan_source(src: str, label: str = "") -> list[str]:
    tree = ast.parse(src)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    hits = []

    def hit(node, what, ver):
        if not _guarded(node, parents):
            hits.append(f"{label}:{node.lineno}: {what} needs Python {ver}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "tomllib":
                    hit(node, "import tomllib without a fallback", "3.11")
        elif isinstance(node, ast.ImportFrom):
            m = node.module or ""
            if m == "tomllib":
                hit(node, "from tomllib without a fallback", "3.11")
            for a in node.names:
                if m == "typing" and a.name in TYPING_NEW:
                    hit(node, f"typing.{a.name} (use typing_extensions)", TYPING_NEW[a.name])
                if (m, a.name) in FROM_NAMES:
                    hit(node, f"{m}.{a.name}", FROM_NAMES[(m, a.name)])
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            ver = ATTRS.get((node.value.id, node.attr))
            if ver:
                hit(node, f"{node.value.id}.{node.attr}", ver)
            if node.value.id == "typing" and node.attr in TYPING_NEW:
                hit(node, f"typing.{node.attr}", TYPING_NEW[node.attr])
        elif isinstance(node, ast.Attribute) and node.attr == "walk" and isinstance(node.value, ast.Call):
            f = node.value.func          # Path(...).walk()
            if isinstance(f, ast.Name) and f.id in ("Path", "PurePath"):
                hit(node, "Path.walk", "3.12")
        elif isinstance(node, ast.Name) and node.id in BARE and isinstance(node.ctx, ast.Load):
            hit(node, node.id, BARE[node.id])
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "rmtree" and any(k.arg == "onexc" for k in node.keywords):
                hit(node, "shutil.rmtree(onexc=)", "3.12")
        elif node.__class__.__name__ == "TryStar":
            hit(node, "except*", "3.11")
    return hits


def test_the_scan_flags_what_it_should():
    bad = ("import tomllib\nfrom datetime import UTC\nfrom typing import Self\nimport itertools\n"
           "itertools.batched\nshutil.rmtree(p, onexc=f)\n")
    assert len(scan_source(bad)) == 5
    ok = "try:\n    import tomllib\nexcept ModuleNotFoundError:\n    import tomli as tomllib\n"
    assert scan_source(ok) == []


def test_no_newer_stdlib_without_a_guard():
    hits = []
    for p in _pyfiles(SWEEP):
        try:
            hits += scan_source(p.read_text(encoding="utf-8"), str(p.relative_to(ROOT)))
        except SyntaxError as e:
            hits.append(f"{p}: {e}")
    assert not hits, "\n".join(hits)


def test_codex_mcp_import_works_without_tomllib(tmp_path, monkeypatch):
    from meshpool_harness import importer as I
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('[mcp_servers.x]\ncommand = "echo"\n', encoding="utf-8")
    monkeypatch.setattr(I, "HOME", tmp_path)
    monkeypatch.setattr(I, "_appdata", lambda: tmp_path / "appdata")
    monkeypatch.setitem(sys.modules, "tomllib", None)          # as on Python 3.10
    for tomli in (sys.modules.get("tomli"), None):             # with tomli (if installed), then with neither
        monkeypatch.setitem(sys.modules, "tomli", tomli)
        found = dict(I.mcp_sources())
        assert "x" in found.get("Codex", {}), found


def test_p21_message_text_is_unchanged(tmp_path):
    from pptx import Presentation
    from engines.forge.common import load_rules
    from engines.forge.validate_pptx import validate
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "We leverage synergies to unlock growth"
    prs.save(tmp_path / "x.pptx")
    fails = validate(str(tmp_path / "x.pptx"), load_rules(), render=False)["failures"]
    msgs = [f["message"] for f in fails if f["check"] == "banned_phrase"]
    assert msgs, fails
    assert msgs[0].startswith("Slide 1, ") and ': "' in msgs[0]
    assert msgs[0].endswith('" reads as machine-written; say it plainly.')
    import re
    assert re.search(r'^Slide 1, .+: ("[a-z-]+")(, "[a-z-]+")* reads as machine-written; say it plainly\.$', msgs[0]), msgs[0]


def test_times_ending_in_z_parse_on_every_supported_python(monkeypatch):
    """datetime.fromisoformat accepts a trailing "Z" only from Python 3.11: a scheduled task's time and a typed memory
    expiry written that way failed on 3.10 (run this file under 3.10 to see it)."""
    from meshpool_harness import assistant
    from meshpool_harness.ui import memory_api
    assert assistant.next_run({"when": "once", "at": "2030-01-01T09:00:00Z"}) == 1893488400.0
    seen = {}
    monkeypatch.setattr(memory_api.MS, "set_expiry", lambda fid, until: seen.setdefault("until", until) and {})
    monkeypatch.setattr(memory_api, "_row", lambda f, used: f, raising=False)
    try:
        memory_api.act({"action": "expiry", "id": "f1", "until": "2030-01-01T00:00:00Z"})
    except Exception:  # noqa: BLE001 - only the parsed value matters here
        pass
    assert seen.get("until") == 1893456000.0
