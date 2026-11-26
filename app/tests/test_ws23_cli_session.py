"""ws23 chunk A: the terminal is a front end of the app's chat engine (ui/agent_api.Session through meshpool_harness.chats).

Scripted model, temp folders, no network. Every test here fails on the old terminal (its own Agent, no transcript, no
meta, no checks, tracebacks from subcommands, silent -p denials)."""
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from testkit import cli_v2_shots as S  # noqa: E402

from meshpool_harness import chats  # noqa: E402
from meshpool_harness import cli  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.agent.tui_input import FakeKeys  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402


@pytest.fixture(autouse=True)
def _full_cowork_lane(monkeypatch):
    """These tests cover the full Cowork lane (classic tools, ask_user, grader); lean Work is the default since work1
    and is tested in test_lean_work.py."""
    monkeypatch.setenv("MH_LEAN_WORK", "0")


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def _say(text):
    return {"role": "assistant", "content": text}


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    sessions = tmp_path / "runs" / "sessions"                 # SESSIONS is fixed at import: point every holder here
    from meshpool_harness.agent import core as _core
    monkeypatch.setattr(_core, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(chats, "SESSIONS", sessions)
    monkeypatch.delenv("MESHPOOL_FRONTEND", raising=False)
    monkeypatch.delenv("MH_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("MH_AGENT_PROFILE", raising=False)
    A.LIVE.clear()
    yield
    for s in list(A.LIVE.values()):
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    A.LIVE.clear()


def script(monkeypatch, *replies):
    """The model says `replies` in order (a dict is the assistant message; a str is plain text)."""
    it = iter([_say(r) if isinstance(r, str) else r for r in replies])
    seen = []

    def fake(self, turn):
        for k, v in {"in": 100, "cached": 0, "out": 10, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        self.emit({"type": "usage", "in": 100, "cached": 0, "out": 10, "calls": 1})
        seen.append(list(self.messages))
        return next(it), False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    return seen


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def run_main(proj, capfd, **kw):
    rc = T.main(str(proj), no_mcp=True, **kw)
    cap = capfd.readouterr()
    return rc, cap.out, cap.err


# ---------- 1. one store: a terminal chat is an app chat ------------------------------------------------------------
def test_cli_chat_is_an_app_chat(monkeypatch, proj, capfd):
    script(monkeypatch, "hi there")
    rc, out, err = run_main(proj, capfd, prompt="say hi", json_out="json")
    assert rc == 0
    res = json.loads(out.strip().splitlines()[-1])
    sid = res["chat_id"]
    assert res["session_id"] == sid and res["result"] == "hi there"
    row = next(r for r in chats.listing() if r["id"] == sid)
    assert row["kind"] == "code" and row["workdir"] == str(proj.resolve())
    meta = chats.read_meta(sid)
    assert meta["kind"] == "code" and meta["mode"] == "ask"
    kinds = [e["type"] for e in chats.view(sid, 0)["events"]]   # read through the store (transcript.jsonl)
    assert "user" in kinds and "assistant" in kinds and "turn_end" in kinds
    A.LIVE.clear()                                          # the app opens it fresh from disk
    evs = chats.view(sid, 0)["events"]
    assert any(e["type"] == "user" and "say hi" in e["text"] for e in evs)
    assert any(e["type"] == "assistant" and "hi there" in e["text"] for e in evs)


def test_app_chat_resumes_in_cli(monkeypatch, proj, capfd):
    script(monkeypatch, "first answer", "second answer")
    v = chats.create({"workdir": str(proj), "kind": "cowork", "no_mcp": True})
    s = chats.get(v["id"])
    s.send("one")
    time.sleep(0.05)
    with s.changed:
        while s.state in ("working", "waiting"):
            s.changed.wait(0.2)
    s.close()
    A.LIVE.clear()
    rc, out, err = run_main(proj, capfd, prompt="two", session=v["id"], json_out="json")
    assert rc == 0
    res = json.loads(out.strip().splitlines()[-1])
    assert res["chat_id"] == v["id"] and res["result"] == "second answer"
    msgs = chats.saved_messages(v["id"])
    texts = " ".join(str(m.get("content")) for m in msgs)
    assert "one" in texts and "two" in texts and "first answer" in texts
    assert chats.read_meta(v["id"])["kind"] == "cowork"       # the app's Work chat stays Work


# ---------- 2. two processes never edit one chat ---------------------------------------------------------------------
def test_chat_lock_refuses_second_process(monkeypatch, proj):
    script(monkeypatch, "x")
    v = chats.create({"workdir": str(proj), "no_mcp": True})
    sid = v["id"]
    A.LIVE.pop(sid).close()
    live = chats.SESSIONS / sid / "live.json"
    live.write_text(json.dumps({"pid": os.getppid(), "frontend": "terminal", "t": time.time()}))
    with pytest.raises(chats.ChatBusy, match="open in the terminal"):
        chats.get(sid)
    live.write_text(json.dumps({"pid": 2 ** 22 + 12345, "frontend": "terminal", "t": 0}))   # a dead process
    s = chats.get(sid, no_mcp=True)
    assert json.loads(live.read_text())["pid"] == os.getpid()
    s.close()
    assert not live.exists()                                   # closing hands the chat back


def test_stale_live_session_reloads(monkeypatch, proj):
    script(monkeypatch, "x")
    v = chats.create({"workdir": str(proj), "no_mcp": True})
    sid = v["id"]
    s = chats.get(sid)
    f = chats.SESSIONS / sid / "session.json"
    data = json.loads(f.read_text())
    data["messages"].append({"role": "user", "content": "typed in another process"})
    time.sleep(0.02)
    f.write_text(json.dumps(data))
    s2 = chats.get(sid, no_mcp=True)
    assert s2 is not s
    assert any("typed in another process" in str(m.get("content")) for m in s2.agent.messages)
    assert chats.get(sid) is s2                                # unchanged since: kept


# ---------- 3. approvals show the diff, -p says what it refused ----------------------------------------------------
def test_approval_shows_diff_and_answers(monkeypatch, proj, capfd):
    (proj / "a.txt").write_text("old line\n")
    script(monkeypatch, _call("edit", {"path": "a.txt", "old": "old line", "new": "new line"}), "edited")
    out = io.StringIO()
    t = T.Terminal(print_mode=True, out=out, interactive=False)
    t.keys_factory = lambda: FakeKeys("y")
    t.info = out
    t.choose = lambda title, detail, options, **k: (out.write("\n".join(detail) + "\n"), 0)[1]
    s = chats.get(chats.create({"workdir": str(proj), "no_mcp": True})["id"])
    front = T.TermFrontend(s, t, headless=True)
    front.send("change it")
    assert (proj / "a.txt").read_text() == "new line\n"
    shown = out.getvalue()
    assert "-old line" in shown and "+new line" in shown


def test_p_without_auto_explains_denials(monkeypatch, proj, capfd):
    script(monkeypatch, _call("write_file", {"path": "out.txt", "content": "x"}), "I could not write it")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc, out, err = run_main(proj, capfd, prompt="write out.txt", json_out="json")
    assert rc == 3
    assert "not allowed" in err and "--auto" in err
    res = json.loads(out.strip().splitlines()[-1])
    assert len(res["denied"]) == 1 and not (proj / "out.txt").exists()


# ---------- 4. modes and agents ------------------------------------------------------------------------------------
def test_mode_and_agent_flags(monkeypatch, proj, capfd):
    script(monkeypatch, "done")
    rc = cli.main(["--dir", str(proj), "--mode", "work", "-p", "x", "--json", "--no-mcp"])
    res = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert rc == 0
    meta = chats.read_meta(res["chat_id"])
    assert meta["kind"] == "cowork"
    assert chats.get(res["chat_id"]).harness["id"] == "meshpool-work"
    A.LIVE.clear()
    from meshpool_harness import harness_router as HRT
    monkeypatch.setattr(HRT, "route", lambda task, kind, **k: {"harness": "meshpool-work", "name": "Meshpool Work",
                                                               "category": "docs", "reason": "documents"})
    script(monkeypatch, "done")
    cli.main(["--dir", str(proj), "--mode", "auto", "-p", "make a deck", "--json", "--no-mcp"])
    res = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert chats.read_meta(res["chat_id"])["routed"] == "meshpool-work"


# ---------- 5. undo, diff, rewind at the prompt -----------------------------------------------------------------
def _live_term(out):
    t = T.Terminal(print_mode=False, out=out, interactive=False)
    return t


def test_undo_and_rewind_in_tui(monkeypatch, proj, capfd):
    (proj / "a.txt").write_text("v0\n")
    script(monkeypatch, _call("write_file", {"path": "a.txt", "content": "v1\n"}, "c1"), "one",
           _call("write_file", {"path": "a.txt", "content": "v2\n"}, "c2"), "two")
    out = io.StringIO()
    t = _live_term(out)
    t.info = out
    s = chats.get(chats.create({"workdir": str(proj), "no_mcp": True, "mode": "auto"})["id"])
    front = T.TermFrontend(s, t)
    sess = T.Session(t, s.agent, None, None, front=front)
    sess.send("first")
    sess.send("second")
    assert (proj / "a.txt").read_text() == "v2\n"
    sess.command("/diff 1")
    assert "-v0" in out.getvalue() and "+v1" in out.getvalue()
    t.choose = lambda title, detail, options, **k: 0          # confirm the undo
    sess.command("/undo 2")
    assert (proj / "a.txt").read_text() == "v1\n"
    sess.command("/rewind 1")
    assert (proj / "a.txt").read_text() == "v0\n" and t.prefill == "first"


# ---------- 6. checks and receipts render ---------------------------------------------------------------------------
def test_checks_and_receipt_render(proj):
    out = io.StringIO()
    t = _live_term(out)
    t.info = out
    s = chats.get(chats.create({"workdir": str(proj), "no_mcp": True})["id"])
    T.TermFrontend(s, t)
    s.emit({"type": "checks_line", "text": "2 of 2 checks passed", "items": []})
    s.emit({"type": "task_receipt", "secs": 42, "cost": 0.03, "harness_name": "Meshpool Code", "model": "m1"})
    text = out.getvalue()
    assert "Checked: 2 of 2 checks passed" in text and "42 s" in text and "$0.03" in text and "Meshpool Code" in text


# ---------- 7. F-10, F-09, F-12 ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [["import", "/nope.zip"], ["work", "bogus:x"], ["import", "agents", "--from", "nothing"]])
def test_cli_errors_are_one_line(argv, capfd):
    rc = cli.main(argv)
    cap = capfd.readouterr()
    assert rc == 1 and "Traceback" not in cap.out + cap.err


def test_unexpected_exception_is_one_line_with_a_log(monkeypatch, capfd, tmp_path):
    from meshpool_harness import connectors

    def boom(*a, **k):
        raise ZeroDivisionError("boom")
    monkeypatch.setattr(connectors, "get", boom)
    assert cli.main(["work", "file:x"]) == 1
    err = capfd.readouterr().err
    assert err.startswith("error: boom") and "cli-error.log" in err and "Traceback" not in err
    assert "ZeroDivisionError" in (tmp_path / "runs" / "logs" / "cli-error.log").read_text()
    monkeypatch.setenv("MH_DEBUG", "1")
    with pytest.raises(ZeroDivisionError):
        cli.main(["work", "file:x"])


@pytest.mark.parametrize("cmd", sorted(set(cli._HELP) | {"worktree"}))
def test_subcommand_help_never_runs(cmd, monkeypatch, capfd):
    monkeypatch.setattr(T, "main", lambda *a, **k: pytest.fail("ran the agent"))
    from meshpool_harness import worktrees as WT
    monkeypatch.setattr(WT, "cli", lambda *a, **k: pytest.fail("ran worktree"))
    assert cli.main([cmd, "-h"]) == 0
    assert capfd.readouterr().out.startswith("Usage:")


def test_empty_print_refused(monkeypatch, capfd):
    monkeypatch.setattr(core.Agent, "_chat", lambda *a, **k: pytest.fail("asked the model"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = cli.main(["-p", ""])
    assert rc != 0 and "-p needs a request" in capfd.readouterr().err


# ---------- 8. the headless contract (G-07) ---------------------------------------------------------------------------
def test_json_and_stream_json_contract(monkeypatch, proj, capfd, tmp_path):
    script(monkeypatch, "all done")
    f = tmp_path / "last.txt"
    rc = cli.main(["--dir", str(proj), "-p", "go", "--json", "--no-mcp", "--output-last-message", str(f)])
    cap = capfd.readouterr()
    lines = cap.out.strip().splitlines()
    assert rc == 0 and len(lines) == 1                          # exactly one JSON line
    res = json.loads(lines[0])
    assert res["type"] == "result" and res["ok"] and res["usage"]["calls"] >= 1 and res["denied"] == []
    assert f.read_text() == "all done"
    A.LIVE.clear()
    script(monkeypatch, "streamed")
    rc = cli.main(["--dir", str(proj), "-p", "go", "--output-format", "stream-json", "--no-mcp"])
    lines = [json.loads(x) for x in capfd.readouterr().out.strip().splitlines()]
    types = [x["type"] for x in lines]
    assert rc == 0 and types[-1] == "result" and "assistant" in types and "text_delta" not in types
    A.LIVE.clear()
    monkeypatch.setattr(core.Agent, "_chat", lambda self, turn: (_ for _ in ()).throw(RuntimeError("model down")))
    rc = cli.main(["--dir", str(proj), "-p", "go", "--json", "--no-mcp"])
    res = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and res["ok"] is False


def test_slash_commands_alias_for_access():
    assert T.ALIASES["access"] == "auto" and "mode" not in T.ALIASES
    assert "permissions" not in T.ALIASES and hasattr(T.Session, "cmd_permissions")   # 0.0.17: lists rules, or sets a mode
    assert {"plan", "goal", "undo", "diff", "rewind", "attach", "agent", "mode", "resume"} <= {c[0] for c in T.COMMANDS}


def test_p_outage_fails_now_not_after_a_wait(monkeypatch, proj, capfd):
    """The app waits out an outage and carries on; a script run (-p) must get the failure at once."""
    def down(self, turn):
        self.emit({"type": "error", "text": "Couldn't reach the model."})
        return _say("x"), False
    monkeypatch.setattr(core.Agent, "_chat", down)
    t0 = time.time()
    rc, out, err = run_main(proj, capfd, prompt="go", json_out="json")
    assert rc == 1 and time.time() - t0 < 20
    assert json.loads(out.strip().splitlines()[-1])["ok"] is False


# ---------- 9. end to end: a real terminal process, then the app server over the same home --------------------------
def test_terminal_chat_shows_in_the_app(tmp_path):
    import re
    import socket
    import subprocess
    import urllib.request
    repo = Path(__file__).resolve().parents[2]
    mh, proj, home = tmp_path / "mh", tmp_path / "p", tmp_path / "h"
    for d in (mh, proj, home):
        d.mkdir()

    def port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    mport, uport = port(), port()
    (mh / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI_|CODEX_|MESHPOOL_|MH_)", k)}
    env.update(HOME=str(home), MESHPOOL_HOME=str(mh), PYTHONPATH=str(repo / "app"), PYTHONUTF8="1", MH_CLARITY="0",
               MH_SLOTS_DIR=str(tmp_path / "slots"), MH_LEARN="0", MH_GRADER="0", MH_AUTO_ROUTE="0",
               MH_UI_OPEN_PAGE="1")   # the page without the launch key, as the rest of the suite (conftest)
    mock = subprocess.Popen([sys.executable, str(repo / "testkit" / "mock_llm.py"), str(mport), str(tmp_path / "m.jsonl")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ui = None
    try:
        time.sleep(1.0)
        r = subprocess.run([sys.executable, "-m", "meshpool_harness", "--dir", str(proj), "-p", "good morning", "--json",
                            "--no-mcp", "--auto"], cwd=str(repo / "app"), env=env, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-800:]
        res = json.loads(r.stdout.strip().splitlines()[-1])
        ui = subprocess.Popen([sys.executable, "-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                              cwd=str(repo / "app"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base, end, html = f"http://127.0.0.1:{uport}", time.time() + 60, ""
        while time.time() < end:
            try:
                html = urllib.request.urlopen(base + "/", timeout=3).read().decode()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        tok = re.search(r'token: "([^"]+)"', html).group(1)
        rows = json.loads(urllib.request.urlopen(urllib.request.Request(
            base + "/api/agent", headers={"X-Meshpool-Token": tok}), timeout=30).read())
        rows = rows if isinstance(rows, list) else rows.get("sessions") or []
        assert res["chat_id"] in [x["id"] for x in rows]
    finally:
        mock.kill()
        if ui:
            ui.kill()


# ---------- 10. found by the 0.0.16 end-to-end run against a real model ---------------------------------------------
def _ask_term(out, answer: int):
    """A terminal that answers every approval menu with option `answer` and records the options it was offered."""
    t = T.Terminal(print_mode=True, out=out, interactive=False)
    t.keys_factory = lambda: FakeKeys("y")
    t.info = out
    t.offered = []
    t.choose = lambda title, detail, options, **k: (t.offered.append(list(options)), answer)[1]
    return t


def test_approval_menu_offers_only_the_grant_the_agent_keeps(monkeypatch, proj, capfd):
    """The terminal dropped the approval's scope: every command offered "don't ask again for commands in this
    session", even a piped one the agent never remembers, so the user picked it and was asked again."""
    script(monkeypatch, _call("bash", {"command": "echo hi | cat"}, "c1"), _call("bash", {"command": "echo hi | cat"}, "c2"),
           _call("bash", {"command": f"{sys.executable} -m pytest --version"}, "c3"), "done")
    out = io.StringIO()
    t = _ask_term(out, 0)
    s = chats.get(chats.create({"workdir": str(proj), "no_mcp": True})["id"])
    T.TermFrontend(s, t, headless=True).send("go")
    piped, _again, plain = t.offered
    assert piped == ["Yes", "No"]                            # a chained command is approved once, never for the chat
    assert len(plain) == 4 and "pytest" in plain[1] and "commands in this session" in plain[1]
    assert "always allow this in this folder" in plain[2]     # 0.0.17: a narrow rule saved for this folder


def test_p_json_usage_is_this_runs_on_a_resumed_chat(monkeypatch, proj, capfd):
    """`-c -p --json` reported the whole chat's calls and steps at the top level and this run's in by_model."""
    script(monkeypatch, _call("read_file", {"path": "x.txt"}, "c1"), "one", "two")
    (proj / "x.txt").write_text("x\n")
    rc, out, _ = run_main(proj, capfd, prompt="first", json_out="json", auto=True)
    first = json.loads(out.strip().splitlines()[-1])
    assert rc == 0 and first["usage"]["calls"] == 2 and first["steps"] == 1
    A.LIVE.clear()
    rc, out, _ = run_main(proj, capfd, prompt="second", session=first["chat_id"], json_out="json", auto=True)
    res = json.loads(out.strip().splitlines()[-1])
    assert rc == 0 and res["chat_id"] == first["chat_id"]
    assert res["usage"]["calls"] == 1 and res["usage"]["in"] == 100 and res["steps"] == 0


def test_p_unreachable_endpoint_fails_at_once_with_the_address(monkeypatch, proj, capfd):
    """A wrong port made `meshpool -p` probe the endpoint for 30 minutes ("Press Stop to end"), then say it had
    waited. A script run gets one probe and a plain error, in --json too; MH_RECOVER_MIN still opts in."""
    monkeypatch.delenv("MH_RECOVER_MIN", raising=False)
    s = chats.get(chats.create({"workdir": str(proj), "no_mcp": True})["id"])
    T.TermFrontend(s, T.Terminal(print_mode=True, out=io.StringIO(), interactive=False), headless=True)
    seen = []

    class H:
        def wait_until_up(self, probe, sleep, emit, budget, check_stop):
            seen.append(budget)
            return False
    s.agent._await_endpoint(H(), "http://127.0.0.1:9/v1", "k", "m")
    monkeypatch.setenv("MH_RECOVER_MIN", "2")
    s.agent._await_endpoint(H(), "http://127.0.0.1:9/v1", "k", "m")
    assert seen == [0.0, 120.0]
    s.close()
    A.LIVE.clear()
    monkeypatch.delenv("MH_RECOVER_MIN", raising=False)
    monkeypatch.setattr(core.Agent, "_chat", lambda self, turn: (_ for _ in ()).throw(
        RuntimeError("HTTP 0: the model endpoint did not come back")))
    rc, out, err = run_main(proj, capfd, prompt="go", json_out="json")
    res = json.loads(out.strip().splitlines()[-1])
    assert rc == 1 and res["ok"] is False and "Couldn't reach the model endpoint (" in res["error"]
    assert "even after waiting" not in err


def test_p_auto_explains_a_refused_destructive_command(monkeypatch, proj, capfd):
    """Full access still asks before rm -rf; in -p the refusal said "re-run with --auto" (it was) and listed the
    action as "destructive"."""
    seen = script(monkeypatch, _call("bash", {"command": "rm -rf build"}), "I left build alone")
    rc, out, err = run_main(proj, capfd, prompt="clean up", json_out="json", auto=True)
    res = json.loads(out.strip().splitlines()[-1])
    assert rc == 3 and res["denied"][0]["command"] == "rm -rf build" and res["denied"][0]["kind"] == "bash"
    assert "`rm -rf build`" in err and "even with --auto" in err and "re-run with --auto" not in err
    told = next(m["content"] for m in seen[-1] if m.get("role") == "tool")
    assert "even in full access" in told and "Re-run with --auto" not in told


def test_receipt_counts_this_turns_lines_not_uncommitted_work(monkeypatch, proj, capfd):
    """In a repo with the user's uncommitted edits the receipt said `ops.py (+8 -0)` for a 2-line change, and the
    check it ran showed as `cd /long/project/path && ...`."""
    import subprocess
    (proj / "ops.py").write_text("def div(a, b):\n    return a / b\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(git + ["add", "-A"], cwd=proj, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=proj, check=True)
    (proj / "ops.py").write_text("def sub(a, b):\n    return a - b\n\n\ndef div(a, b):\n    return a / b\n")
    script(monkeypatch, _call("edit", {"path": "ops.py", "old": "    return a / b", "new": "    return a / b if b else 0"}),
           _call("bash", {"command": f"cd {proj} && {sys.executable} -c 'import ops'"}, "c2"), "done")
    rc, out, err = run_main(proj, capfd, prompt="guard div", json_out="json", auto=True)
    assert rc == 0 and "def sub" in (proj / "ops.py").read_text()
    assert "ops.py (+1 -1)" in err, err[-600:]
    assert "Checked:** `" in err and "Checked:** `cd " not in err


# ---------- release review: --permission-mode plan holds in -p ------------------------------------------------------
def test_p_permission_mode_plan_is_honoured(monkeypatch, proj, capfd):
    """plan is not an access level, so normalize_mode drops it: read before normalizing, or -p edits files."""
    monkeypatch.setenv("MH_AGENT_PERMISSIONS", "edits")         # a default that would let the write through
    monkeypatch.setenv("MH_PERMISSION_MODE", "plan")
    script(monkeypatch, _call("write_file", {"path": "out.txt", "content": "x"}), "Plan: write out.txt")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc, out, err = run_main(proj, capfd, prompt="write out.txt", json_out="json")
    res = json.loads(out.strip().splitlines()[-1])
    assert chats.read_meta(res["chat_id"]).get("plan") is True
    assert not (proj / "out.txt").exists()


def test_resumed_chat_takes_permission_mode_plan(monkeypatch, proj, capfd):
    script(monkeypatch, "first", "second")
    sid = chats.create({"workdir": str(proj), "no_mcp": True})["id"]
    assert not chats.get(sid).plan
    A.LIVE.clear()
    monkeypatch.setenv("MH_PERMISSION_MODE", "plan")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    run_main(proj, capfd, prompt="go on", json_out="json", session=sid)
    assert chats.read_meta(sid).get("plan") is True
