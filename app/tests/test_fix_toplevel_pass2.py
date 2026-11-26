"""Second round of top-level fixes."""
import json
import os
import threading
import urllib.parse
from pathlib import Path

import pytest

from meshpool_harness import config as C


def test_html_to_text_keeps_non_ascii():
    pytest.importorskip("lxml")
    from meshpool_harness import web
    t = web.html_to_text("<html><body><article><p>" + "café 日本語 — " * 30 + "</p></article></body></html>")
    assert "café 日本語" in t["text"]


def test_redirect_loop_is_an_error():
    import http.server
    import socketserver

    from meshpool_harness import web

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(web.WebError):
            web._get(f"http://127.0.0.1:{srv.server_address[1]}/loop", timeout=5)
    finally:
        srv.shutdown()


def test_config_write_loses_nothing_across_threads(tmp_path):
    p = tmp_path / "meshpool.config"
    errs = []

    def w(i):
        try:
            for j in range(15):
                C.write({f"K_{i}_{j}": "v"}, p)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(C.read(p)) == 60


def test_config_write_loses_nothing_across_processes(tmp_path):
    import subprocess
    import sys
    p = tmp_path / "meshpool.config"
    code = ("import sys; from pathlib import Path; from meshpool_harness import config as C\n"
            "i=sys.argv[1]\nfor j in range(10): C.write({f'K_{i}_{j}': 'v'}, Path(sys.argv[2]))")
    ps = [subprocess.Popen([sys.executable, "-c", code, str(i), str(p)], cwd=str(Path(C.__file__).parents[1]))
          for i in range(4)]
    assert all(x.wait(60) == 0 for x in ps)
    assert len(C.read(p)) == 40


def test_shell_env_beats_saved_config(tmp_path, monkeypatch):
    from meshpool_harness import providers as P
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    C.ensure_home()
    C.write({"MESHPOOL_BASE_URL": "https://saved.example/v1"})
    monkeypatch.setenv("MESHPOOL_BASE_URL", "http://my-gateway/v1")
    C._INJECTED.discard("MESHPOOL_BASE_URL")
    assert P._cfg("MESHPOOL_BASE_URL") == "http://my-gateway/v1"
    C._INJECTED.add("MESHPOOL_BASE_URL")                       # copied from the file by us: the file is the truth
    try:
        assert P._cfg("MESHPOOL_BASE_URL") == "https://saved.example/v1"
    finally:
        C._INJECTED.discard("MESHPOOL_BASE_URL")


def test_truncated_download_is_not_promoted(tmp_path, monkeypatch):
    import io

    from meshpool_harness import autoupdate as AU

    class R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    calls = []

    def fake(url, start):
        calls.append(start)
        data = b"x" * 100
        return R(data[start:]) if start else R(data[:40])
    monkeypatch.setattr(AU, "_open_range", fake)
    dst = tmp_path / "a.exe"
    with pytest.raises(AU.UpdateError):
        AU.download_resumable("u", dst, 100)
    assert not dst.exists() and (tmp_path / "a.exe.part").stat().st_size == 40


def test_write_json_is_thread_safe(tmp_path):
    from meshpool_harness import autoupdate as AU
    errs = []

    def w():
        try:
            for _ in range(100):
                AU._write_json(tmp_path / "s.json", {"a": 1})
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs


def test_questions_are_not_file_requests():
    from meshpool_harness.capabilities import needs
    assert needs("give me a quick summary of the attached report") == []
    assert needs("how do I make a pivot table in excel?") == []
    assert needs("I need a report on tesla") == ["document"]
    assert needs("make a deck about sales") == ["presentation"]


def test_imported_mcp_starts_disabled_and_instructions_are_scanned(tmp_path, monkeypatch):
    from meshpool_harness import agent_import as AI
    from meshpool_harness import importer as IM
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    C.ensure_home()
    n = IM.import_mcp("x", {"command": "npx", "args": ["y"]})
    from meshpool_harness.agent import mcp as M
    assert M.load_config()[n]["disabled"] is True
    body, dropped = AI._scan_text("Be brief.\nMy token is sk-abcdefghijklmnopqrst\nAlways cite sources")
    assert dropped == 1 and "sk-" not in body and "Be brief." in body
    body, dropped = AI._scan_text("sk-abcdefghijklmnopqrst")
    assert dropped == 1


@pytest.mark.parametrize("cmd", ["import", "login", "setup", "run-due", "mcp", "app", "scheduler"])
def test_help_has_no_side_effects(cmd, capsys, monkeypatch):
    from meshpool_harness import cli
    from meshpool_harness import scheduler_os as SO
    monkeypatch.setattr(SO, "install", lambda *a, **k: pytest.fail("ran"))
    monkeypatch.setattr(SO, "run_due", lambda: pytest.fail("ran"))
    argv = [cmd, "install", "--help"] if cmd == "scheduler" else [cmd, "--help"]
    assert cli.main(argv) == 0
    assert "Usage" in capsys.readouterr().out


def test_work_missing_file_is_a_message(capsys):
    from meshpool_harness import cli
    assert cli.main(["work", "file:/nonexistent/x.md"]) == 1
    assert "Could not fetch" in capsys.readouterr().out


def test_models_html_page_and_402(monkeypatch):
    from meshpool_harness import provider_setup as PS
    for status, data, want in ((200, None, "not like a model API"), (404, None, "not like a model API"),
                               (402, None, "payment")):
        monkeypatch.setattr(PS, "_get", lambda *a, _s=status, _d=data, **k: (_s, _d, ""))
        r = PS.test("http://x.example/v1", "key")
        assert not r.get("ok") and want in r.get("error", ""), (status, r)


def test_insights_state_list_does_not_crash(tmp_path, monkeypatch):
    from meshpool_harness import insights
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    C.ensure_home()
    f = insights.folder()
    f.mkdir(parents=True, exist_ok=True)
    (f / "state.json").write_text("[]", encoding="utf-8")
    assert insights._state()["types"] == {}


def test_big_file_change_is_noted(tmp_path, monkeypatch):
    from meshpool_harness import checkpoints as CP
    monkeypatch.setattr(CP, "MAX_FILE", 100)
    monkeypatch.setattr(CP, "MAX_BIG", 100)
    root = tmp_path / "w"
    root.mkdir()
    (root / "big.dat").write_bytes(b"a" * 500)
    cp = CP.Checkpoints(str(root), tmp_path / "store")
    cp.begin_turn("t")
    (root / "big.dat").write_bytes(b"b" * 600)
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert any("big.dat" in n for n in r.get("notes", []))


def test_bare_skill_md_gets_front_matter(tmp_path, monkeypatch):
    from meshpool_harness import skills as SK
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    C.ensure_home()
    r = SK.add_files([("SKILL.md", b"# Invoice helper\nDo invoices.")])
    assert r[0]["description"] and r[0]["id"] != "skill"


def test_csv_big_field_and_truncated_row_count(tmp_path):
    from meshpool_harness import docread
    p = tmp_path / "a.csv"
    p.write_text("a,b\n" + "x" * 200_000 + ",1\n", encoding="utf-8")
    assert "2 rows" in docread._csv(p)


def test_safe_names():
    from meshpool_harness.project_store import _safe_name
    assert _safe_name(".env") == ".env" and _safe_name("NUL.txt") != "NUL.txt" and len(_safe_name("a" * 300 + ".txt")) <= 120


def test_alpha_vantage_key_is_url_safe(tmp_path, monkeypatch):
    from meshpool_harness import mcp_library as L
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    C.ensure_home()
    L.configure("alpha-vantage", {"ALPHAVANTAGE_API_KEY": "k&y=1"}, enable=False)
    assert C.read()["ALPHAVANTAGE_API_KEY"] == urllib.parse.quote("k&y=1", safe="")


def test_learn_gate_ignores_plain_questions():
    from meshpool_harness import learn
    assert not learn.worth_a_look("What time is it again?")
    assert learn.worth_a_look("From now on keep answers short.")
