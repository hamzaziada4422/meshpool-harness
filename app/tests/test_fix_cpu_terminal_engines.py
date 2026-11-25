"""CPU use in the terminal and the office engines: no git per key, a status line that runs on change, streamed long
lines measured at their end only, one LibreOffice run per deck or workbook input, one login shell per process, and
built-in MCP servers that a new chat (every `meshpool -p`) does not start until a tool is used."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.agent import tui_render as R  # noqa: E402
from meshpool_harness.agent.tui_input import FakeKeys  # noqa: E402

posix = pytest.mark.skipif(os.name == "nt", reason="uses a shell script as a stand-in program")


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    (path / "a.txt").write_text("a")
    _git("add", "-A", cwd=path)
    _git("commit", "-qm", "i", cwd=path)
    return path


def _terminal(cwd: Path):
    from testkit import cli_v2_shots as S
    t, out, clk = S.make_terminal()
    t.cwd, t.cwd_label, t.branch = str(cwd), "", None          # ask git, as a real session does
    return t, out, clk


# ---------------------------------------------------------------- the prompt: git once, not once per key
def test_typing_at_the_prompt_runs_git_once_not_per_key(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "r")
    calls = []
    real = T._git_branch
    monkeypatch.setattr(T, "_git_branch", lambda cwd: calls.append(cwd) or real(cwd))
    t, out, clk = _terminal(repo)
    keys = []
    for i in range(120):                                   # 120 keys over 60 s of (fake) time: each one redraws
        keys += ["x", (lambda i=i: clk.__setitem__(0, i * 0.5))]
    t.keys_factory = lambda: FakeKeys(*keys, "\r")
    assert t.read_input() == "x" * 120
    assert t._branch() == "main"
    assert len(calls) == 1                                 # before: every 5 s while typing (12 runs here)
    _git("checkout", "-qb", "feature", cwd=repo)           # HEAD changed: asked again, the new branch shows
    assert t._branch() == "feature" and len(calls) == 2
    assert t._branch() == "feature" and len(calls) == 2
    plain = tmp_path / "plain"                             # not a repository: git is never run
    plain.mkdir()
    t.cwd = str(plain)
    assert t._branch() == "" and len(calls) == 2


def test_branch_of_a_linked_worktree_follows_its_own_head(tmp_path):
    repo = _repo(tmp_path / "r")
    _git("worktree", "add", "-q", "-b", "side", str(tmp_path / "wt"), cwd=repo)
    (tmp_path / "wt" / "sub").mkdir()
    gd, common = T._git_dirs(str(tmp_path / "wt" / "sub"))  # found from a subfolder too
    assert Path(common).resolve() == (repo / ".git").resolve() and Path(gd).resolve() != Path(common).resolve()
    t, _, _ = _terminal(tmp_path / "wt")
    assert t._branch() == "side"


# ---------------------------------------------------------------- the status line command
@posix
def test_status_line_runs_when_its_input_changes_not_on_every_key(tmp_path, monkeypatch):
    log = tmp_path / "runs.log"
    script = tmp_path / "sl.sh"
    script.write_text(f"#!/bin/sh\ncat >/dev/null\necho run >> '{log}'\necho my-line\n")
    script.chmod(0o755)
    monkeypatch.setenv("MH_STATUSLINE", str(script))
    t, out, clk = _terminal(tmp_path)
    monkeypatch.setattr(t, "_statusline_ok", lambda: True)

    def runs():
        for _ in range(100):
            if not getattr(t, "_sl_busy", False):
                break
            time.sleep(0.02)
        return len(log.read_text().splitlines()) if log.exists() else 0

    for _ in range(300):                                   # 300 redraws in well under 2 s: one run
        t.footer_line()
    assert runs() == 1
    assert "my-line" in t.footer_line()
    t._sl_at -= 3                                          # 3 s later, same input: not run again
    t.footer_line()
    assert runs() == 1
    t.mode = "edits"                                       # the input changed: run (at most every 2 s)
    t.footer_line()
    assert runs() == 2
    t._sl_at -= T.SL_REFRESH_S + 1                         # unchanged for SL_REFRESH_S: refreshed once
    t.footer_line()
    t.footer_line()
    assert runs() == 3


def test_status_line_folder_check_is_kept_until_the_git_config_changes(tmp_path, monkeypatch):
    from meshpool_harness.core import tools as TT
    repo = _repo(tmp_path / "r")
    seen = []
    real = TT.git_config_safe
    monkeypatch.setattr(TT, "git_config_safe", lambda root: seen.append(root) or real(root))
    t = T.Terminal.__new__(T.Terminal)
    t.cwd = str(repo)
    assert t._statusline_ok() is True
    for _ in range(20):
        assert t._statusline_ok() is True
    assert len(seen) == 1                                  # kept while nothing changed
    time.sleep(0.01)
    _git("config", "core.fsmonitor", "touch pwned", cwd=repo)
    assert t._statusline_ok() is False                     # a config that runs programs is noticed at once
    assert len(seen) == 2


# ---------------------------------------------------------------- streaming a long line
def _old_tail(partial: str, width: int) -> str:
    tail = partial.replace("\t", "    ")
    w = width - 4
    return "  " + (tail if R.vlen(tail) <= w else "…" + tail[-(w - 1):])


def test_a_long_streamed_line_is_measured_at_its_end_only(monkeypatch):
    from testkit import cli_v2_shots as S
    t, out, clk = S.make_terminal()
    t.emit({"type": "model_start"})
    counted = [0]
    real_cw = R.cw
    monkeypatch.setattr(R, "cw", lambda ch: counted.__setitem__(0, counted[0] + 1) or real_cw(ch))
    word = "pool’s “routes” 価格\t"                        # non-ASCII and wide characters: the slow path
    per_token = []
    for i in range(600):                                   # one paragraph, no line break: about 12,000 characters
        before = counted[0]
        t.emit({"type": "text_delta", "text": word})
        per_token.append(counted[0] - before)
        if i in (5, 50, 599):
            assert t._turn_live()[0][0] == R.clip(_old_tail(t._partial, t.width), t.width - 1)   # same line as before
    assert max(per_token[-50:]) <= 2 * max(per_token[:50]) + 50  # flat, not growing with the line (was O(n) per token)
    assert max(per_token) < 10 * t.width
    t.emit({"type": "turn_end"})


def test_vlen_fast_path_matches_per_character_widths():
    for s in ["", "abc", "\x1b[1mbold\x1b[0m text", "".join(chr(c) for c in range(32, 127)), "tab\there"]:
        assert R.vlen(s) == sum(R.cw(c) for c in R.strip(s))
    assert R.vlen("価格") == 4 and R.vlen("é") == 1


# ---------------------------------------------------------------- one LibreOffice run per input
FAKE_SOFFICE = """#!{py}
import os, shutil, sys
a = sys.argv[1:]
open({log!r}, "a").write(" ".join(x for x in a if x.startswith("--")) + "\\n")
to = a[a.index("--convert-to") + 1].split(":")[0]
out = a[a.index("--outdir") + 1]
src = a[-1]
os.makedirs(out, exist_ok=True)
shutil.copyfile(src, os.path.join(out, os.path.splitext(os.path.basename(src))[0] + "." + to))
"""


@pytest.fixture
def fake_soffice(tmp_path, monkeypatch):
    log = tmp_path / "soffice.log"
    exe = tmp_path / "bin" / "soffice"
    exe.parent.mkdir()
    exe.write_text(FAKE_SOFFICE.format(py=sys.executable, log=str(log)))
    exe.chmod(0o755)
    monkeypatch.setenv("MH_SOFFICE", str(exe))
    return lambda: len(log.read_text().splitlines()) if log.exists() else 0


@posix
def test_the_same_deck_is_rendered_once_for_the_validator_and_the_reviewers(tmp_path, fake_soffice, monkeypatch):
    from engines.forge import common as CM
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"deck v1")
    a = CM.soffice_convert(str(deck), "pdf", str(tmp_path / "o1"))
    b = CM.soffice_convert(str(deck), "pdf", str(tmp_path / "o2"))
    assert fake_soffice() == 1                             # was one LibreOffice per call
    assert a != b and a.read_bytes() == b.read_bytes() == b"deck v1"
    b.unlink()                                             # each caller owns its copy
    assert CM.soffice_convert(str(deck), "pdf", str(tmp_path / "o3")).exists()
    deck.write_bytes(b"deck v2")                           # new bytes: converted again
    assert CM.soffice_convert(str(deck), "pdf", str(tmp_path / "o4")).read_bytes() == b"deck v2"
    assert fake_soffice() == 2
    for f in (CM._convert_dir()).iterdir():                # older than the time limit: converted again
        os.utime(f, (time.time() - CM.CONVERT_TTL_S - 5,) * 2)
    CM.soffice_convert(str(deck), "pdf", str(tmp_path / "o5"))
    assert fake_soffice() == 3
    monkeypatch.setenv("MH_CONVERT_CACHE", "0")
    CM.soffice_convert(str(deck), "pdf", str(tmp_path / "o6"))
    assert fake_soffice() == 4


@posix
def test_a_workbook_is_recalculated_once_for_qa_and_the_review_digest(tmp_path, fake_soffice):
    from engines.forge import validate_xlsx as VX
    from engines.sheets import qa as Q
    book = tmp_path / "model.xlsx"
    book.write_bytes(b"workbook bytes")
    p1 = Q.recalc(str(book), str(tmp_path / "r1"))
    p2 = Q.recalc(str(book), str(tmp_path / "r2"))
    assert fake_soffice() == 1 and Path(p1).read_bytes() == Path(p2).read_bytes() == b"workbook bytes"
    VX.recalc(str(book), str(tmp_path / "r3"))             # the forge rules' run uses another profile: its own run
    VX.recalc(str(book), str(tmp_path / "r4"))
    assert fake_soffice() == 2


def test_deck_without_bubble_labels_starts_no_libreoffice_for_the_label_check(tmp_path, monkeypatch):
    pytest.importorskip("pptx")
    from engines.forge import common as CM
    from engines.forge import render as RD
    runs = []
    monkeypatch.setattr(CM, "find_soffice", lambda: "/bin/true")
    monkeypatch.setattr(CM, "soffice_convert", lambda *a, **k: runs.append(a) or (_ for _ in ()).throw(RuntimeError("x")))
    slides = [{"archetype": "cover", "title": "Regional growth plan", "subtitle": "Board review"},
              {"archetype": "chart_takeaway", "title": "Revenue grew 20% as pricing offset lower volume",
               "chart": {"kind": "column", "categories": ["2023", "2024"], "series": [{"name": "R", "values": [1, 2]}]},
               "takeaways": ["Price rose"], "source": "Company accounts"}]
    res = RD.render(slides, str(tmp_path / "d.pptx"))
    assert runs == [] and not any("label check" in e for e in res["errors"])
    assert RD._has_bubble_labels([{"chart": {"kind": "bubble", "points": [{"x": 1, "y": 1, "label": "North"}]}}])
    assert not RD._has_bubble_labels([{"chart": {"kind": "bubble", "points": [{"x": 1, "y": 1}]}}])


# ---------------------------------------------------------------- MCP servers for a new chat
def test_the_login_shell_runs_once_when_servers_start_together(monkeypatch):
    from meshpool_harness.agent import mcp as M
    if os.name == "nt":
        pytest.skip("no login shell on Windows")
    monkeypatch.delenv("MH_LOGIN_ENV", raising=False)
    monkeypatch.delenv("MESHPOOL_RESOLVING_ENV", raising=False)
    monkeypatch.setattr(M, "_LOGIN_ENV", [])
    calls = []

    def slow(cmd, **kw):
        calls.append(cmd)
        time.sleep(0.2)
        mark = cmd[-1].split("'%s' ")[1].split(";")[0]
        return subprocess.CompletedProcess(cmd, 0, f"{mark}A=1\0{mark}".encode(), b"")
    monkeypatch.setattr(M.subprocess, "run", slow)
    got = []
    th = [threading.Thread(target=lambda: got.append(M.login_shell_env())) for _ in range(5)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    assert len(calls) == 1 and all(g == {"A": "1"} for g in got)      # was one login shell per server thread


def test_the_saved_tool_list_is_shared_by_every_chat(tmp_path, monkeypatch):
    from meshpool_harness.agent import mcp as M
    spec = {"command": sys.executable, "args": ["-m", "meshpool_harness.agent.office_mcp_server"]}
    a = M._cache_key("office", {**spec, "env": {"MH_CHAT_ID": "chat-a"}})
    b = M._cache_key("office", {**spec, "env": {"MH_CHAT_ID": "chat-b"}})
    assert a == b                                          # a new chat (every `meshpool -p`) finds the saved list
    assert M._cache_key("office", {**spec, "env": {"MH_CHAT_ID": "a", "OTHER": "1"}}) != a   # real settings still count
    M.save_tools("office", {**spec, "env": {"MH_CHAT_ID": "chat-a"}}, [{"name": "t"}], {})
    assert M.cached_tools("office", {**spec, "env": {"MH_CHAT_ID": "chat-b"}})[0] == [{"name": "t"}]


def test_startup_imports_no_office_or_browser_libraries():
    """`meshpool -p` in scripts: the command, the terminal and the chat code load without pptx, openpyxl, a PDF
    library, Pillow or Playwright (they load when a tool needs them)."""
    code = ("import sys; import meshpool_harness.cli, meshpool_harness.agent.tui, meshpool_harness.ui.agent_api, "
            "meshpool_harness.agent.mcp; heavy = ('pptx', 'openpyxl', 'fitz', 'pypdfium2', 'pypdf', 'PIL', "
            "'playwright', 'pandas', 'numpy', 'docx'); print([m for m in heavy if m in sys.modules])")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       cwd=str(Path(__file__).resolve().parents[1]),
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.strip().splitlines()[-1] == "[]"
