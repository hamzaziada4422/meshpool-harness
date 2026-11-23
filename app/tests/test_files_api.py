"""The in-app file browser and editor (ui/files_api.py, /api/fs/*): list, read, save, conflicts, path safety, and that
no click path asks the operating system to open anything."""
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from meshpool_harness.ui import files_api as FA
from meshpool_harness.ui import server as S
from meshpool_harness.ui import workbench as W


@pytest.fixture
def proj(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    monkeypatch.setenv("MH_RUNS_DIR", str(home / "runs"))
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    (root / "src" / "a.py").write_text("print('hi')\n")
    (root / "README.md").write_text("# Title\n")
    (root / "src" / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    return root


@pytest.fixture
def http(proj):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(method, route, body=None, **q):
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in q.items())
        req = urllib.request.Request(f"http://127.0.0.1:{port}{route}" + (f"?{qs}" if qs else ""), method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Meshpool-Token": S.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    yield call
    httpd.shutdown()


def test_list_is_lazy_and_hides_heavy_folders(proj, http):
    st, r = http("GET", "/api/fs/list", root=str(proj), path="")
    names = [e["name"] for e in r["entries"]]
    assert st == 200 and "src" in names and "README.md" in names
    assert ".git" not in names and "node_modules" not in names
    assert r["entries"][0]["dir"] is True                    # folders first
    st, r = http("GET", "/api/fs/list", root=str(proj), path="src")
    assert {e["path"] for e in r["entries"]} == {"src/a.py", "src/crlf.txt"}


def test_search_finds_files_by_name(proj, http):
    (proj / "node_modules" / "a.py").write_text("x")
    st, r = http("GET", "/api/fs/search", root=str(proj), q="a.py")
    assert [x["path"] for x in r["results"]] == ["src/a.py"]


def test_read_text_and_kinds(proj, http):
    st, r = http("GET", "/api/fs/read", root=str(proj), path="src/a.py")
    assert st == 200 and r["kind"] == "text" and r["text"] == "print('hi')\n" and len(r["sha"]) == 64
    (proj / "b.bin").write_bytes(b"\0\1\2")
    assert http("GET", "/api/fs/read", root=str(proj), path="b.bin")[1]["kind"] == "binary"
    (proj / "d.pdf").write_bytes(b"%PDF-1.4")
    assert http("GET", "/api/fs/read", root=str(proj), path="d.pdf")[1]["kind"] == "preview"
    st, r = http("GET", "/api/fs/read", root=str(proj), path="src/crlf.txt")
    assert r["text"] == "one\ntwo\n" and r["eol"] == "crlf"


def test_save_writes_keeps_line_endings_and_a_backup(proj, http):
    st, r = http("GET", "/api/fs/read", root=str(proj), path="src/a.py")
    st, s = http("POST", "/api/fs/save", {"root": str(proj), "path": "src/a.py", "text": "print('bye')\n", "base_sha": r["sha"]})
    assert st == 200 and s["ok"] and s["backup"].endswith("a.py.bak")
    assert (proj / "src" / "a.py").read_text() == "print('bye')\n"
    bak = list((FA.C.runs_dir() / "user-edits").rglob("*.bak"))
    assert len(bak) == 1 and bak[0].read_text() == "print('hi')\n"
    st, r = http("GET", "/api/fs/read", root=str(proj), path="src/crlf.txt")
    http("POST", "/api/fs/save", {"root": str(proj), "path": "src/crlf.txt", "text": "one\ntwo\nthree\n", "base_sha": r["sha"]})
    assert (proj / "src" / "crlf.txt").read_bytes() == b"one\r\ntwo\r\nthree\r\n"
    assert not [p for p in (proj / "src").iterdir() if p.name.endswith(".tmp")]


def test_save_refuses_when_the_file_changed_on_disk(proj, http):
    st, r = http("GET", "/api/fs/read", root=str(proj), path="src/a.py")
    (proj / "src" / "a.py").write_text("changed elsewhere\n")             # the agent, or another tool
    st, s = http("POST", "/api/fs/save", {"root": str(proj), "path": "src/a.py", "text": "mine\n", "base_sha": r["sha"]})
    assert st == 409 and s["conflict"] is True and s["current"]["sha"]
    assert (proj / "src" / "a.py").read_text() == "changed elsewhere\n"    # nothing was written
    st, s = http("POST", "/api/fs/save", {"root": str(proj), "path": "src/a.py", "text": "mine\n", "base_sha": r["sha"], "overwrite": True})
    assert st == 200 and (proj / "src" / "a.py").read_text() == "mine\n"


def test_stat_reports_changes(proj, http):
    st, r = http("GET", "/api/fs/read", root=str(proj), path="README.md")
    assert http("GET", "/api/fs/stat", root=str(proj), path="README.md")[1]["sha"] == r["sha"]
    (proj / "README.md").write_text("new")
    assert http("GET", "/api/fs/stat", root=str(proj), path="README.md")[1]["sha"] != r["sha"]


@pytest.mark.parametrize("bad", ["../outside.txt", "src/../../outside.txt", "/etc/passwd", "..", "src/\0x"])
def test_traversal_is_refused(proj, http, bad, tmp_path):
    (tmp_path / "outside.txt").write_text("secret")
    for route in ("/api/fs/read", "/api/fs/list", "/api/fs/stat"):
        st, r = http("GET", route, root=str(proj), path=bad)
        assert st == 400, (route, bad, st, r)
    st, r = http("POST", "/api/fs/save", {"root": str(proj), "path": bad, "text": "x"})
    assert st == 400
    assert (tmp_path / "outside.txt").read_text() == "secret"


def test_root_must_be_a_project(proj, http, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "f.txt").write_text("x")
    for route, kw in (("/api/fs/list", {"path": ""}), ("/api/fs/read", {"path": "f.txt"})):
        st, r = http("GET", route, root=str(other), **kw)
        assert st == 400 and "not one of your projects" in r["error"]
    assert http("POST", "/api/fs/save", {"root": str(other), "path": "f.txt", "text": "y"})[0] == 400


def test_symlinks_out_of_the_project_are_refused(proj, http, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    try:
        os.symlink(outside, proj / "linkdir")
        os.symlink(outside / "secret.txt", proj / "linkfile.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    st, r = http("GET", "/api/fs/list", root=str(proj), path="")
    assert "linkdir" not in [e["name"] for e in r["entries"]] and "linkfile.txt" not in [e["name"] for e in r["entries"]]
    assert http("GET", "/api/fs/list", root=str(proj), path="linkdir")[0] == 400
    assert http("GET", "/api/fs/read", root=str(proj), path="linkdir/secret.txt")[0] == 400
    assert http("GET", "/api/fs/read", root=str(proj), path="linkfile.txt")[0] == 400
    assert http("POST", "/api/fs/save", {"root": str(proj), "path": "linkfile.txt", "text": "pwn"})[0] == 400
    assert http("GET", "/api/fs/search", root=str(proj), q="secret")[1]["results"] == []
    assert (outside / "secret.txt").read_text() == "secret"


def test_save_never_writes_through_a_link_that_stays_inside(proj, http):
    try:
        os.symlink(proj / "README.md", proj / "alias.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    st, r = http("POST", "/api/fs/save", {"root": str(proj), "path": "alias.md", "text": "x"})
    assert st == 400 and "link" in r["error"]
    assert (proj / "README.md").read_text() == "# Title\n"


def test_meshpool_home_is_browsable_but_keys_stay_private(proj, http):
    home = FA.C.home()
    (home / "skills").mkdir(exist_ok=True)
    (home / "skills" / "x.md").write_text("skill")
    (home / "meshpool.config").write_text("MESHPOOL_API_KEY=sk-secret")
    st, r = http("GET", "/api/fs/home")
    assert st == 200 and r["skills"] == "skills"
    st, r = http("GET", "/api/fs/list", root=r["home"], path="")
    names = [e["name"] for e in r["entries"]]
    assert "skills" in names and "meshpool.config" not in names
    assert http("GET", "/api/fs/read", root=str(home), path="meshpool.config")[0] == 400
    assert http("GET", "/api/fs/read", root=str(home), path="skills/x.md")[1]["text"] == "skill"


def test_reveal_is_explicit_validated_and_never_called_by_other_routes(proj, http, monkeypatch):
    from meshpool_harness.ui import desktop as D
    shown = []
    monkeypatch.setattr(D, "show_in_folder", lambda p: shown.append(p))
    monkeypatch.setattr(D, "reveal", lambda p: shown.append(("OPENED", p)))
    http("GET", "/api/fs/read", root=str(proj), path="src/a.py")
    http("GET", "/api/fs/list", root=str(proj), path="")
    http("POST", "/api/fs/save", {"root": str(proj), "path": "src/a.py", "text": "x\n"})
    assert shown == []                                                  # reading, listing and saving never ask the system
    assert http("POST", "/api/fs/reveal", {"root": str(proj), "path": "../x"})[0] == 400
    assert shown == []
    assert http("POST", "/api/fs/reveal", {"root": str(proj), "path": "src/a.py"})[0] == 200
    assert shown == [(proj / "src" / "a.py").resolve()]


def test_the_old_open_routes_are_gone(http, proj):
    for route in ("/api/open-path", "/api/open-file", "/api/open-data"):
        assert http("POST", route, {"path": str(proj), "root": str(proj)})[0] == 404, route


def test_open_in_editor_no_longer_starts_a_program(proj, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("a program was started"))
    r = W.open_editor(str(proj))
    assert r["ok"] and r["open"]["root"] == str(proj.resolve()) and "Meshpool editor" in r["message"]


def test_the_terminal_app_never_launches_an_editor_or_a_viewer():
    from pathlib import Path
    import meshpool_harness.agent.tui as T
    src = Path(T.__file__).read_text(encoding="utf-8")
    for word in ("EDITOR", "VISUAL", "xdg-open", "startfile", "webbrowser", "\"open\"", "'open'", "osascript"):
        assert word not in src, word


def test_no_page_script_posts_to_a_removed_open_route():
    from pathlib import Path
    ui = Path(S.__file__).parent
    for js in ui.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        for route in ("/api/open-path", "/api/open-file", "/api/open-data", "/api/open-editor", "/api/company/open"):
            assert route not in text, (js.name, route)
