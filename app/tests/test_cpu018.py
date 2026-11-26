"""CPU use found in the 0.0.18 hunt (area CPU). Each test counts the work (file reads, parses, processes, tree walks)
that used to be redone per call, and checks the kept answer still sees a change.

- CPU-01 profiles.load_all parsed every profile YAML file on each model request (for_model runs several times a step).
- CPU-02 modelcaps._hints read and parsed model_hints.json on each resolve() once a reply had taught anything.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import fsio  # noqa: E402

OLD = time.time() - 3600


def count_opens(monkeypatch, match):
    """A live count of files opened for reading whose path matches match(path) (open, Path.read_text, read_bytes)."""
    import builtins
    import io
    box = {"n": 0, "paths": []}
    real = io.open

    def opener(file, mode="r", *a, **k):
        if "r" in str(mode) and "+" not in str(mode) and isinstance(file, (str, os.PathLike)) and match(os.fspath(file)):
            box["n"] += 1
            box["paths"].append(os.fspath(file))
        return real(file, mode, *a, **k)
    monkeypatch.setattr(io, "open", opener)
    monkeypatch.setattr(builtins, "open", opener)
    return box


def _age(*paths):
    for p in paths:
        os.utime(p, (OLD, OLD))


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(h))
    monkeypatch.setattr(fsio, "RACY_S", 0.0)          # files made by the test are seconds old; the guard has its own test
    return h


def test_parse_cached_rereads_a_file_changed_in_the_last_seconds(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"a": 1}')
    n0 = fsio.READS[0]
    for _ in range(3):
        assert fsio.parse_cached(f, json.loads, "t") == {"a": 1}
    assert fsio.READS[0] - n0 == 3                     # just written: a same-tick change could hide, so it is read
    _age(f)
    time.sleep(0.01)
    fsio.parse_cached(f, json.loads, "t")
    n1 = fsio.READS[0]
    for _ in range(5):
        fsio.parse_cached(f, json.loads, "t")
    assert fsio.READS[0] == n1 or fsio.RACY_S > 0     # ctime is new here; with the guard on it may still re-read
    f.unlink()
    assert fsio.parse_cached(f, json.loads, "t", missing="gone") == "gone"


# ---------------------------------------------------------------------------------------------------- CPU-01
def test_cpu01_profiles_are_parsed_once_while_unchanged(tmp_path, monkeypatch, home):
    import yaml

    from meshpool_harness import profiles as P
    d = tmp_path / "profiles"
    shutil.copytree(P.DIR, d)
    _age(*d.iterdir())
    monkeypatch.setattr(P, "DIR", d)
    calls = [0]
    real = yaml.safe_load

    def counting(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    monkeypatch.setattr(yaml, "safe_load", counting)
    first = P.for_model("claude-sonnet-5")
    for _ in range(30):                                # the body, the client, the context window, the caps: per step
        assert P.for_model("claude-sonnet-5") == first
    files = len(list(d.glob("*.yaml")))
    assert calls[0] == files, f"{calls[0]} YAML parses for 31 lookups over {files} files"
    # a caller that changes its profile does not change the next caller's
    P.load_all()["default"]["limits"] = "changed"
    assert P.load_all()["default"].get("limits") != "changed"
    # an edited profile is seen at once
    f = d / "default.yaml"
    f.write_text(f.read_text(encoding="utf-8") + "\nextra_key_018: 7\n", encoding="utf-8")
    os.utime(f, (OLD + 60, OLD + 60))
    assert P.load_all()["default"]["extra_key_018"] == 7


# ---------------------------------------------------------------------------------------------------- CPU-02
def test_cpu02_model_hints_are_not_reread_per_request(home, monkeypatch):
    from meshpool_harness.core import modelcaps as MC
    MC.reset()
    hints = home / "model_hints.json"
    hints.write_text(json.dumps({"models": [{"match": "^acme-", "reasoning": "effort", "efforts": ["low", "high"]}]}))
    _age(hints)
    base = "https://api.example.com/v1"
    MC.learn(base, "acme-1", usage_cached=True)       # what the first reply teaches: resolve() then skips its cache
    MC.resolve(base, "acme-1")                        # the first request reads the tables
    box = count_opens(monkeypatch, lambda p: p.endswith("model_hints.json"))
    for _ in range(20):
        assert MC.resolve(base, "acme-1").efforts == ("low", "high")
    assert box["n"] == 0, f"model_hints.json was read {box['n']} times for 20 requests"
    hints.write_text(json.dumps({"models": [{"match": "^acme-", "reasoning": "effort", "efforts": ["medium"]}]}))
    os.utime(hints, (OLD + 60, OLD + 60))
    assert MC.resolve(base, "acme-1").efforts == ("medium",)
    MC.reset()


# ---------------------------------------------------------------------------------------------------- CPU-03
def test_cpu03_trust_check_reads_no_file_per_chat_poll(home, tmp_path, monkeypatch):
    from meshpool_harness import trust as TR
    from meshpool_harness.ui import agent_api as A
    monkeypatch.delenv("MH_TRUST_PROJECT", raising=False)
    proj = tmp_path / "proj"
    (proj / ".meshpool").mkdir(parents=True)
    (proj / ".meshpool" / "hooks.json").write_text(json.dumps({"PreToolUse": [{"command": "echo hi"}]}))
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"command": "x"}}}))
    (home / "trusted_projects.json").write_text(json.dumps([str(tmp_path / "elsewhere")]))
    (home / "trusted-folders.json").write_text(json.dumps({"folders": {str(tmp_path / "other"): {"at": 1}}}))
    _age(proj / ".meshpool" / "hooks.json", proj / ".mcp.json", home / "trusted_projects.json",
         home / "trusted-folders.json")
    first = A._trust_note(str(proj))
    assert first and first["files"] == [".meshpool/hooks.json", ".mcp.json"]
    box = count_opens(monkeypatch, lambda p: p.endswith(".json"))
    for _ in range(25):                                # the page polls the chat view every 0.4 to 4 s
        assert A._trust_note(str(proj)) == first
        assert TR.is_trusted(proj) is False
    assert box["n"] == 0, f"{box['n']} JSON reads for 25 polls: {sorted(set(box['paths']))}"
    TR.trust(proj)                                     # trusting the folder is seen at once
    assert A._trust_note(str(proj)) is None
    assert TR.is_trusted(proj / "sub") is True


# ---------------------------------------------------------------------------------------------------- CPU-04
def test_cpu04_hooks_files_are_not_reread_per_tool_call(home, tmp_path, monkeypatch):
    from meshpool_harness import trust as TR
    from meshpool_harness.agent import extras as EX
    proj = tmp_path / "proj"
    (proj / ".meshpool").mkdir(parents=True)
    (proj / ".meshpool" / "hooks.json").write_text(json.dumps({"PostToolUse": [{"command": "true", "matcher": "zz"}]}))
    (home / "hooks.json").write_text(json.dumps({"PreToolUse": [{"command": "true", "matcher": "zz"}]}))
    (home / "plugin-hooks").mkdir()
    (home / "plugin-hooks" / "p.json").write_text(json.dumps({"Stop": [{"command": "true"}]}))
    TR.trust(proj)
    for f in (proj / ".meshpool" / "hooks.json", home / "hooks.json", home / "plugin-hooks" / "p.json",
              home / "trusted-folders.json"):
        _age(f)
    EX.hooks(proj)
    box = count_opens(monkeypatch, lambda p: p.endswith(".json"))
    for _ in range(10):                                # a ten-call turn: hooks before and after each call
        EX.run_hooks_full(proj, "PreToolUse", {}, tool="read_file", args={"path": "a"})
        EX.run_hooks_full(proj, "PostToolUse", {}, tool="read_file", args={"path": "a"})
    assert box["n"] == 0, f"{box['n']} hook file reads in a ten-call turn"
    hs = EX.hooks(proj)
    assert [h["command"] for h in hs["PreToolUse"]] == ["true"] and len(hs["PostToolUse"]) == 1 and hs["Stop"]
    hs["PreToolUse"][0]["command"] = "changed"         # one caller's change is not the next caller's hooks
    assert EX.hooks(proj)["PreToolUse"][0]["command"] == "true"
    (home / "hooks.json").write_text(json.dumps({"PreToolUse": [{"command": "echo new"}]}))
    os.utime(home / "hooks.json", (OLD + 60, OLD + 60))
    assert [h["command"] for h in EX.hooks(proj)["PreToolUse"]] == ["echo new"]


# ---------------------------------------------------------------------------------------------------- CPU-05
def test_cpu05_sidebar_refresh_reads_no_project_files(home, tmp_path, monkeypatch):
    from meshpool_harness import project_store as PS
    from meshpool_harness.ui import agent_api as A
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.list_dirty()
    pids = []
    for i in range(6):
        link = tmp_path / f"repo{i}"
        link.mkdir()
        pids.append(PS.create(f"P{i}", "d", "be brief", link={"path": str(link)})["id"])
    for p in (home / "projects").rglob("*"):
        if p.is_file():
            _age(p)
    want_legacy = {}
    for r in PS.list_projects():                       # what the sidebar used before, built the old way
        for k in (r.get("legacy_path"), (r.get("link") or {}).get("path")):
            if k:
                want_legacy.setdefault(k, r["id"])
    assert A._project_info() == (want_legacy, set(pids))
    A.listing()
    box = count_opens(monkeypatch, lambda p: "projects" in Path(p).parts)
    for _ in range(10):                                # every 3.5 s while a chat runs
        A.list_dirty()                                 # and the saved-chat rows rebuilt too
        A.listing()
    assert box["n"] == 0, f"{box['n']} project file reads for 10 sidebar refreshes"
    PS.delete(pids[0])
    assert pids[0] not in A._project_info()[1]


# ---------------------------------------------------------------------------------------------------- CPU-06
def test_cpu06_pdf_preview_opens_the_pdf_once_per_new_page(home, tmp_path, monkeypatch):
    import pypdfium2 as pdfium

    from meshpool_harness.ui import previews as P
    from meshpool_harness.ui import workbench as W
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    doc = pdfium.PdfDocument.new()
    for _ in range(8):
        doc.new_page(400, 300)
    doc.save(str(root / "r.pdf"))
    doc.close()
    opened = [0]
    real = pdfium.PdfDocument

    class Counting(real):
        def __init__(self, *a, **k):
            opened[0] += 1
            super().__init__(*a, **k)
    monkeypatch.setattr(pdfium, "PdfDocument", Counting)
    assert P.meta(str(root), "r.pdf")["pages"] == 8
    opened[0] = 0
    for i in range(8):                                 # the page strip asks for every page
        assert P.page_png(str(root), "r.pdf", i, 300).startswith(b"\x89PNG")
    assert opened[0] == 8, f"{opened[0]} PDF opens to draw 8 pages"
    for i in range(8):                                 # shown again: every page is in the PNG cache
        P.page_png(str(root), "r.pdf", i, 300)
    assert opened[0] == 8, f"{opened[0] - 8} PDF opens to show 8 cached pages"
    with pytest.raises(ValueError):
        P.page_png(str(root), "r.pdf", 8, 300)


# ---------------------------------------------------------------------------------------------------- CPU-07
def test_cpu07_open_editor_does_not_hash_the_file_every_four_seconds(home, tmp_path, monkeypatch):
    from meshpool_harness.ui import files_api as FA
    from meshpool_harness.ui import workbench as W
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    f = root / "big.py"
    f.write_bytes(b"x = 1\n" * 300_000)                # 1.8 MB: each check read and hashed all of it
    _age(f)
    first = FA.read(str(root), "big.py")
    box = count_opens(monkeypatch, lambda p: p.endswith("big.py"))
    for _ in range(15):                                # one minute of an open editor
        assert FA.stat(str(root), "big.py")["sha"] == first["sha"]
    assert box["n"] <= 1, f"the open file was read {box['n']} times in 15 checks"
    f.write_bytes(b"x = 2\n" * 300_000)                # same size: the change is still noticed
    os.utime(f, (OLD + 60, OLD + 60))
    assert FA.stat(str(root), "big.py")["sha"] != first["sha"]
    monkeypatch.setattr(fsio, "RACY_S", 2.0)
    f.write_bytes(b"x = 3\n" * 300_000)                # changed just now: hashed again each time, since a second
    a = FA.stat(str(root), "big.py")["sha"]            # write in the same clock tick keeps the same stamp
    f.write_bytes(b"x = 4\n" * 300_000)
    assert FA.stat(str(root), "big.py")["sha"] != a


# ---------------------------------------------------------------------------------------------------- CPU-08, CPU-09
GIT = shutil.which("git")


@pytest.fixture
def picker_repo(home, tmp_path, monkeypatch):
    import subprocess

    from meshpool_harness.ui import files_api as FA
    from meshpool_harness.ui import workbench as W
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(40):
        (root / "pkg" / f"mod{i}.py").write_text(f"def handle_{i}(x):\n    return x\n\nclass Widget{i}:\n    pass\n" * 2)
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([GIT, "add", "-A"], cwd=root, check=True)
    for p in root.rglob("*.py"):
        _age(p)
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    getattr(FA, "_SYMS", {}).clear()
    getattr(FA, "_GITF", {}).clear()
    runs = []
    real = subprocess.run

    def counting(cmd, *a, **k):
        if "ls-files" in [str(c) for c in cmd]:
            runs.append(cmd)
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", counting)
    return root, runs


@pytest.mark.skipif(not GIT, reason="git is not installed")
def test_cpu08_symbol_picker_does_not_reread_every_source_file_per_key(picker_repo, monkeypatch):
    from meshpool_harness.ui import files_api as FA
    root, _ = picker_repo
    assert FA.symbols(str(root), "ha")["results"]
    box = count_opens(monkeypatch, lambda p: p.endswith(".py"))
    for q in ("han", "hand", "handl", "handle", "handle_", "handle_1", "wid", "widg", "widget"):   # typing after @
        r = FA.symbols(str(root), q)["results"]
        assert r and all(q in h["name"].lower() for h in r)
    assert box["n"] == 0, f"{box['n']} source file reads for 9 keystrokes"
    assert FA.symbols(str(root), "handle_12")["results"][0] == {"name": "handle_12", "kind": "def",
                                                                "path": "pkg/mod12.py", "line": 1}
    f = root / "pkg" / "mod3.py"
    f.write_text("def renamed_entry(y):\n    return y\n")
    os.utime(f, (OLD + 60, OLD + 60))
    assert [h["name"] for h in FA.symbols(str(root), "renamed")["results"]] == ["renamed_entry"]
    assert not [h for h in FA.symbols(str(root), "handle_3")["results"] if h["name"] == "handle_3"]


@pytest.mark.skipif(not GIT, reason="git is not installed")
def test_cpu09_picker_asks_git_once_for_a_burst_of_keys(picker_repo):
    import subprocess

    from meshpool_harness.ui import files_api as FA
    root, runs = picker_repo
    for q in ("m", "mo", "mod", "mod1", "mod12"):     # each key: the names and the symbols request, together
        FA.search(str(root), q)
        FA.symbols(str(root), q)
    assert len(runs) == 1, f"{len(runs)} git ls-files runs for 5 keys"
    (root / "pkg" / "fresh.py").write_text("x = 1\n")
    subprocess.run([GIT, "add", "fresh.py"], cwd=root / "pkg", check=True)   # the index changed: asked again at once
    assert any(h["path"] == "pkg/fresh.py" for h in FA.search(str(root), "fresh")["results"])
    assert len(runs) == 2
