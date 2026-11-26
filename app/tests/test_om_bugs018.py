"""Bugs a backend developer persona (Omar: `meshpool -p`, the terminal UI and web Code chats) hit on 0.0.18, IDs
OM-01 to OM-10. Scripted model replies or the mock in testkit; no paid model is called."""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from meshpool_harness import capabilities as CAP  # noqa: E402
from meshpool_harness import chats  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent import tui as T  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402


# ---------------------------------------------------------------------------------------------------- OM-02
@pytest.mark.parametrize("text", [
    "make report.py print the totals by month",
    "make the tests in report.py pass",
    "update the docs for the cli and make the examples run",
    "make the docs build pass",
    "fix docs/index.md and make the links work",
    "add a usage section to README.md and make it short",
    "write docs for dedupe.py",
    "make the src/reports/ folder a package",
])
def test_om02_a_code_file_or_the_word_docs_is_not_a_word_document(text):
    """OM-02: "report" in report.py and the word "docs" counted as a request for a Word document, so a code request
    got a hidden note telling the agent to build one."""
    assert CAP.needs(text) == [], text


@pytest.mark.parametrize("text", ["write a report on Q3 sales", "make a Word doc of the notes", "I need a .docx",
                                  "make me a doc summarising the budget", "make report.docx with the totals",
                                  "export it as report.pdf"])
def test_om02_real_requests_for_a_document_still_count(text):
    assert CAP.needs(text), text


# ---------------------------------------------------------------------------------------------------- shared set-up
def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A fresh meshpool home and sessions folder (as in test_ws23_cli_session.py)."""
    monkeypatch.setenv("MESHPOOL_API_KEY", "sk-test-key-123456")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    sessions = tmp_path / "runs" / "sessions"
    monkeypatch.setattr(core, "SESSIONS", sessions)
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(chats, "SESSIONS", sessions)
    for k in ("MESHPOOL_FRONTEND", "MH_PERMISSION_MODE", "MH_AGENT_PROFILE", "MH_ALLOW", "MH_DENY", "MH_MODEL",
              "MH_SANDBOX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    A.LIVE.clear()
    yield tmp_path
    for s in list(A.LIVE.values()):
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    A.LIVE.clear()


def script(monkeypatch, *replies):
    """The model says `replies` in order (a dict is the assistant message; a str is plain text)."""
    it = iter([{"role": "assistant", "content": r} if isinstance(r, str) else r for r in replies])
    seen = []

    def fake(self, turn):
        for k, v in {"in": 100, "cached": 0, "out": 10, "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        seen.append([dict(m) for m in self.messages])
        return next(it), False
    monkeypatch.setattr(core.Agent, "_chat", fake)
    return seen


@pytest.fixture
def proj(home):
    p = home / "proj"
    (p / "ledgerly").mkdir(parents=True)
    (p / "ledgerly" / "storage.py").write_text("FIELDS = ['date']\n")
    return p


# ---------------------------------------------------------------------------------------------------- OM-01
OM01_CMDS = ["find . -name '*.py' | head -5 && cat ledgerly/storage.py", "python3 -m pytest -q"]


def _om01_run(monkeypatch, proj, capfd):
    script(monkeypatch, *[_call("bash", {"command": c}, f"c{i}") for i, c in enumerate(OM01_CMDS)],
           _call("write_file", {"path": "notes.md", "content": "hi\n"}, "c9"),
           "I looked at storage.py.")
    rc = T.main(str(proj), no_mcp=True, prompt="explain ledgerly/storage.py", json_out="json")
    cap = capfd.readouterr()
    return rc, cap.err, json.loads(cap.out.strip().splitlines()[-1])


def test_om01_p_names_each_refused_command_and_a_flag_that_really_allows_it(monkeypatch, proj, capfd):
    """OM-01: `meshpool -p` without a sandbox refused find, a pipe and &&; the summary said "Run this command?" five
    times and recommended --permission-mode edits, under which no command runs. Now each refused command is named,
    with a rule the permission code accepts for it, and edits mode is offered only for the file edit."""
    from meshpool_harness.core import sandbox as SB
    monkeypatch.setattr(SB, "available", lambda *a, **k: False)     # Windows, or Linux without bubblewrap
    rc, err, res = _om01_run(monkeypatch, proj, capfd)
    assert rc == 3 and len(res["denied"]) == 3, err
    note = err[err.index("action(s) were not allowed"):]
    assert "Run this command?" not in note
    for c in OM01_CMDS:
        assert f"`{c}`" in note, c
    assert '--allowed-tools "bash:python3 -m pytest*"' in note
    assert "--allowed-tools bash (every command" in note                  # a pipe or && needs the whole tool
    assert note.count("--permission-mode edits") == 1 and "Write notes.md: --permission-mode edits" in note
    assert "command sandbox" in note and "re-run with --permission-mode auto" in note    # what works on Windows
    # the advice works: the same run with the rules it named refuses nothing
    A.LIVE.clear()
    monkeypatch.setenv("MH_ALLOW", "bash")                             # --allowed-tools bash
    monkeypatch.setenv("MH_PERMISSION_MODE", "edits")                  # --permission-mode edits
    rc, err, res = _om01_run(monkeypatch, proj, capfd)
    assert rc == 0 and res["denied"] == [] and (proj / "notes.md").exists(), err


def test_om01_in_the_sandbox_p_runs_ordinary_commands_without_a_question(monkeypatch, proj, capfd):
    """With the OS sandbox (0.0.18's approvals redesign), -p in the default mode runs find, pipes and && in the
    folder: nothing is refused."""
    from meshpool_harness.core import sandbox as SB
    monkeypatch.setenv("MH_SANDBOX_AUTO", "1")                         # the shipped default (conftest turns it off)
    monkeypatch.setattr(SB, "available", lambda *a, **k: True)
    rc, err, res = _om01_run(monkeypatch, proj, capfd)
    assert rc == 0 and res["denied"] == [] and "not allowed" not in err, err


# ---------------------------------------------------------------------------------------------------- OM-04
@pytest.fixture
def endpoint(monkeypatch):
    """A local OpenAI-style endpoint that serves two models and answers 404 model_not_found for any other name.
    mock-slow streams a long answer, one word every 0.15 s, when asked to stream. `asked` lists the models called;
    asked.bodies keeps each request."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    served = ["mock-edit", "mock-slow"]
    listed = served + ["mock-locked"]               # in the model list, but this key may not call it

    class Asked(list):
        bodies: list = []
    asked = Asked()
    asked.bodies = []
    asked.denied_text = ("key not allowed to access model. This key can only access models=['mock-edit']. Key used: "
                         "sk-test-key-123456. " + "Ask your admin to add it. " * 20)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self._json(200, {"data": [{"id": m, "object": "model"} for m in listed]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            asked.append(body.get("model"))
            asked.bodies.append(body)
            if body.get("model") == "mock-locked":
                return self._json(400, {"error": {"type": "key_model_access_denied", "code": "400",
                                                  "message": asked.denied_text}})
            if body.get("model") not in served:
                return self._json(404, {"error": {"message": f"The model `{body.get('model')}` does not exist",
                                                  "type": "invalid_request_error", "code": "model_not_found"}})
            if body.get("stream") and body["model"] == "mock-slow":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for i in range(200):
                        ch = {"id": "x", "object": "chat.completion.chunk", "model": body["model"],
                              "choices": [{"index": 0, "delta": {"content": f"word{i} "}, "finish_reason": None}]}
                        self.wfile.write(b"data: " + json.dumps(ch).encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.15)
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
                return
            self._json(200, {"id": "x", "object": "chat.completion", "model": body["model"], "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "hello from " + body["model"]},
                 "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    asked.url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    monkeypatch.setenv("MESHPOOL_BASE_URL", asked.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("MH_STREAM", "0")
    yield asked
    srv.shutdown()


def test_om04_a_model_named_on_the_command_line_that_does_not_exist_stops_with_close_names(home, proj, capfd,
                                                                                            endpoint):
    """OM-04: `--model nope-model` printed "no longer available" and carried on with another model. A name given on
    the command line now stops the run with an error that lists the close names the endpoint serves."""
    rc = T.main(str(proj), no_mcp=True, prompt="hi", json_out="json", model="mock-sloww")
    cap = capfd.readouterr()
    res = json.loads(cap.out.strip().splitlines()[-1])
    assert rc == 1 and not res["ok"]
    assert "mock-sloww" in res["error"] and "mock-slow" in res["error"].replace("mock-sloww", ""), res["error"]
    assert "no longer available" not in cap.err
    assert set(endpoint) == {"mock-sloww"}                 # never carried on with another model
    A.LIVE.clear()
    from meshpool_harness import cli
    rc = cli.main(["--dir", str(proj), "-p", "hi", "--model", "nope-model", "--json", "--no-mcp"])   # the flag itself
    res = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and "does not serve the model nope-model" in res["error"]


def test_om04_a_model_the_key_may_not_use_says_so_and_is_not_offered_back(home, proj, capfd, endpoint):
    """OM-04 (review, 0.0.18): key_model_access_denied means the key is not allowed to use the model. It said "the
    endpoint does not serve the model". It now says the key is not allowed, adds the endpoint's own words (about 200
    characters, key masked) and never lists the requested name among the close names."""
    rc = T.main(str(proj), no_mcp=True, prompt="hi", json_out="json", model="mock-locked")
    res = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    err = res["error"]
    assert rc == 1 and not res["ok"]
    assert err.startswith("Your key is not allowed to use the model mock-locked."), err
    assert "does not serve" not in err
    assert "key not allowed to access model" in err and "sk-test-key-123456" not in err
    said = err.split("The endpoint said: ", 1)[1].split(" Close names", 1)[0].split(" Pick one", 1)[0]
    assert 0 < len(said) <= 200, len(said)
    assert "mock-locked" not in err.split("Close names it does serve:", 1)[-1] if "Close names" in err else True
    assert "Close names it does serve: mock-edit" in err or "mock-slow" in err, err
    assert set(endpoint) == {"mock-locked"}                # never carried on with another model


def test_om04_the_requested_name_is_never_a_close_name(monkeypatch):
    """Both errors leave the name asked for out of the "Close names it does serve" list, even when the endpoint's own
    list holds it."""
    from meshpool_harness.ui import workbench as W
    monkeypatch.setattr(W, "models", lambda *a, **k: {"models": [{"id": "gpt-x"}, {"id": "gpt-xl"}, {"id": "gpt-y"}]})
    for msg in ("", "HTTP 400: key_model_access_denied"):
        t = core._unknown_model_text("gpt-x", "key", msg, "sk-test-key-123456")
        close = t.split("Close names it does serve:", 1)[1].split(".", 1)[0]
        assert "gpt-xl" in close and "gpt-x," not in close + "," and " gpt-x " not in " " + close + " ", t
    assert core._unknown_model_text("gpt-x", "key", "").startswith("The endpoint does not serve the model gpt-x.")
    assert core._unknown_model_text("gpt-x", "key", "HTTP 400: key_model_access_denied").startswith(
        "Your key is not allowed to use the model gpt-x.")


def test_om04_a_saved_model_that_disappeared_still_falls_back(home, proj, capfd, endpoint, monkeypatch):
    """The fallback stays for a chat's saved model that the endpoint dropped (no --model given)."""
    v = chats.create({"workdir": str(proj), "no_mcp": True, "model": "gone-model"})
    A.LIVE.pop(v["id"]).close()
    monkeypatch.setenv("MH_MODEL", "mock-edit")
    rc = T.main(str(proj), no_mcp=True, prompt="hi", json_out="json", session=v["id"])
    cap = capfd.readouterr()
    res = json.loads(cap.out.strip().splitlines()[-1])
    assert rc == 0 and res["result"] == "hello from mock-edit"
    assert "no longer available" in cap.err and endpoint[0] == "gone-model"


# ---------------------------------------------------------------------------------------------------- OM-09
def test_om09_a_resumed_chat_takes_the_mode_and_model_from_the_command_line(home, proj, capfd, monkeypatch):
    """OM-09: -c ignored --model, and a chat last used in full access came back in full access without a word. It
    now resumes in the mode given (or the default mode) and on the model given."""
    script(monkeypatch, "first", "second", "third")
    v = chats.create({"workdir": str(proj), "no_mcp": True, "mode": "auto", "model": "mock-edit"})
    s = chats.get(v["id"])
    s.send("one")
    with s.changed:
        while s.state in ("working", "waiting"):
            s.changed.wait(0.2)
    s.close()
    A.LIVE.clear()
    assert chats.read_meta(v["id"])["mode"] == "auto"
    rc = T.main(str(proj), resume=True, no_mcp=True, prompt="two", json_out="json", model="mock-slow")
    cap = capfd.readouterr()
    assert rc == 0, cap.err
    meta = chats.read_meta(v["id"])
    assert meta["mode"] == "auto" and meta["model"] == "mock-slow"      # the mode is for this run only (see below)
    assert "This chat was in full access. It resumes in ask before changes" in cap.err
    A.LIVE.clear()
    rc = T.main(str(proj), resume=True, auto=True, no_mcp=True, prompt="three", json_out="json")
    assert rc == 0 and chats.read_meta(v["id"])["mode"] == "auto"     # --auto asked for it: full access


def test_om09_resuming_does_not_save_the_mode_it_runs_in(home, proj, capfd, monkeypatch):
    """Review, 0.0.18: -c applied the resumed mode through set_mode, which saved it. An Accept edits chat resumed with
    no flag was left in Ask for good. The mode now holds for this run only; the stored mode stays as it was."""
    for stored, flag, ran in (("edits", None, "ask"), ("edits", "edits", "edits"), ("ask", "edits", "edits")):
        v = chats.create({"workdir": str(proj), "no_mcp": True, "mode": stored, "model": "mock-edit"})
        A.LIVE.pop(v["id"]).close()
        seen = []
        orig = T._resume_access

        def spy(term, sess, mode, model):
            orig(term, sess, mode, model)
            seen.append(sess.agent.mode)                  # the mode this run works in, right after resuming
        monkeypatch.setattr(T, "_resume_access", spy)
        if flag:
            monkeypatch.setenv("MH_PERMISSION_MODE", flag)
        else:
            monkeypatch.delenv("MH_PERMISSION_MODE", raising=False)
        script(monkeypatch, "ok")
        rc = T.main(str(proj), session=v["id"], no_mcp=True, prompt="hi", json_out="json")
        capfd.readouterr()
        monkeypatch.setattr(T, "_resume_access", orig)
        assert rc == 0
        assert seen == [ran], (stored, flag, seen)                                  # this run
        assert chats.read_meta(v["id"])["mode"] == stored, (stored, flag)          # the chat's own setting
        A.LIVE.clear()



# ---------------------------------------------------------------------------------------------------- OM-06
TRACE = "Traceback (most recent call last):\n  File \"ledgerly/money.py\", line 7\ndecimal.InvalidOperation: XYZZY"


def _idle(s, secs=20):
    end = time.time() + secs
    with s.changed:
        while s.state in ("working", "waiting") and time.time() < end:
            s.changed.wait(0.2)


def test_om06_a_stopped_turn_is_kept_with_what_it_said_and_marked_stopped(home, proj, capfd, endpoint, monkeypatch):
    """OM-06: a turn the user stopped left no trace of the reply, and the next request read the pasted stack trace
    as a question still to answer. The history now keeps the message, what the reply said so far and a mark that it
    was stopped, and `meshpool -c` sends all of it to the model."""
    monkeypatch.setenv("MH_STREAM", "1")
    v = chats.create({"workdir": str(proj), "no_mcp": True, "model": "mock-slow"})
    s = chats.get(v["id"])
    s.send(TRACE)
    end = time.time() + 20
    while time.time() < end and not any(e.get("type") == "text_delta" for e in list(s.events)):
        time.sleep(0.05)
    time.sleep(0.5)
    s.stop()
    _idle(s)
    s.close()
    A.LIVE.clear()
    msgs = chats.saved_messages(v["id"])
    assert len(msgs) >= 2 and "XYZZY" in str(msgs[-2]["content"]) and msgs[-1]["role"] == "assistant", msgs
    assert msgs[-1]["content"].startswith("word0 word1") and msgs[-1]["content"].endswith(core.STOPPED_MARK)
    monkeypatch.setenv("MH_STREAM", "0")
    rc = T.main(str(proj), resume=True, no_mcp=True, prompt="what was the error?", json_out="json", model="mock-edit")
    assert rc == 0, capfd.readouterr().err
    sent = json.dumps(endpoint.bodies[-1]["messages"])
    assert "XYZZY" in sent and "Stopped by the user" in sent and "what was the error?" in sent


def test_om06_exit_typed_while_the_agent_works_stops_the_turn_and_quits(home, proj, endpoint):
    """OM-06: /exit typed in the terminal UI while a reply streamed did nothing until the reply ended. It now stops
    the turn and quits, and the stopped turn is saved. Driven in a pty, as a person types."""
    import os
    import pty
    import re
    import select
    import signal
    mh = home / "mh_pty"
    mh.mkdir()
    (mh / "meshpool.config").write_text(f"MESHPOOL_BASE_URL={endpoint.url}\nMESHPOOL_API_KEY=sk-test-key-123456\n"
                                        "MH_MODEL=mock-edit\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MH_", "MESHPOOL_"))}
    env.update(MESHPOOL_HOME=str(mh), PYTHONPATH=str(Path(__file__).resolve().parents[1]), TERM="xterm-256color",
               NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", MH_TRUST_PROJECT="1",
               COLUMNS="100", LINES="30")
    pid, fd = pty.fork()
    if pid == 0:                                                   # the child: meshpool's terminal UI
        os.chdir(str(proj))
        os.execve(sys.executable, [sys.executable, "-m", "meshpool_harness", "--model", "mock-slow"], env)
    out = bytearray()

    def pump(secs, until=None):
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    d = os.read(fd, 65536)
                except OSError:
                    return False
                if not d:
                    return False
                out.extend(d)
                if until and until in re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]", b"", bytes(out)):
                    return True
        return True

    def typ(text):
        for ch in text:
            os.write(fd, ch.encode())
            time.sleep(0.01)
    try:
        pump(30, until=b"shift+tab")                             # the prompt is up
        typ("explain this\r")
        pump(30, until=b"word3")                                  # the reply is streaming
        t0 = time.time()
        typ("/exit\r")
        pump(15, until=b"Session saved")
        done = 0
        while time.time() - t0 < 15 and not done:
            done, _ = os.waitpid(pid, os.WNOHANG)
            time.sleep(0.05)
        assert done, "still running after /exit: " + out.decode("utf8", "replace")[-600:]
        assert time.time() - t0 < 10
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass
    from meshpool_harness import session_store as SS
    saved = [SS.read(d) for d in (mh / "runs" / "sessions").iterdir()]
    msgs = [m for sd in saved for m in sd["messages"]]
    assert any(m["role"] == "assistant" and str(m["content"]).endswith(core.STOPPED_MARK) for m in msgs), msgs


# ---------------------------------------------------------------------------------------------------- OM-08
def test_om08_a_streaming_paragraph_shows_as_wrapped_rows_while_it_streams():
    """OM-08: the answer being streamed showed as one clipped line ("…the app. This is a long") next to the spinner
    and could not be read until the paragraph ended. It now shows as word-wrapped rows."""
    from testkit import vt
    out = io.StringIO()
    t = T.Terminal(out=out, width=60, interactive=True, clock=lambda: 0.0)
    t.turn_begin()
    words = [f"word{i}" for i in range(60)]
    for w in words:
        t.emit({"type": "text_delta", "text": w + " "})
    lines, _ = t._turn_live()
    rows = [ln.strip() for ln in lines[:-1]]
    assert len(rows) == T.Terminal.LIVE_ROWS and all(len(ln) <= 60 for ln in lines)
    assert " ".join(rows).split() == words[-len(" ".join(rows).split()):]        # whole words, in order, no "…"
    assert "…" not in " ".join(rows)
    t._turn = False
    t.emit({"type": "text_delta", "text": "\n"})
    screen = vt.render(out.getvalue(), 60).text()
    assert " ".join(screen.split()).count("word0 word1 word2") == 1                 # the paragraph is printed once


def test_om08_p_prints_no_ctrl_o_hint_and_no_exec_command_header_lines():
    """OM-08: `meshpool -p` printed "ctrl+o to expand" (no key can be pressed) and exec_command's lines meant for the
    model (Chunk ID, Wall time, Original token count)."""
    from meshpool_harness.core import tools as CT
    out = io.StringIO()
    t = T.Terminal(print_mode=True, out=out, interactive=False)
    t.info = out
    body = "\n".join(f"line {i}" for i in range(30))
    t.emit({"type": "tool_start", "id": "c1", "name": "exec_command", "summary": "python3 -m pytest -q"})
    t.emit({"type": "tool_end", "id": "c1", "name": "exec_command", "ok": True,
            "output": CT.exec_result(body, 0, 0.42)})
    text = out.getvalue()
    for bad in ("ctrl+o", "Chunk ID", "Wall time", "Original token count", "Output:"):
        assert bad not in text, bad
    assert "line 29" in text and "earlier lines)" in text


def test_om08_undo_picker_numbers_each_turn_once_and_says_what_was_undone(home, proj, monkeypatch, capfd):
    """OM-08: the /undo picker read "1. 1. explain storage.py", and after Undo the terminal said only "Undone. No
    files had changed." although the turn had left the conversation."""
    script(monkeypatch, "it reads the csv", "it writes the csv")
    out = io.StringIO()
    t = T.Terminal(print_mode=False, out=out, interactive=False)
    t.info = out
    s = chats.get(chats.create({"workdir": str(proj), "no_mcp": True})["id"])
    front = T.TermFrontend(s, t)
    sess = T.Session(t, s.agent, None, None, front=front)
    sess.send("explain storage.py")
    sess.send("and save()")
    shown = []

    def choose(title, detail, options, **k):
        shown.append(options)
        return 1 if title.startswith("Undo from") else 0
    t.choose = choose
    sess.command("/undo")
    assert shown[0][0].startswith("explain storage.py") and not shown[0][0].startswith("1.")
    text = out.getvalue()
    assert "The conversation is back to before turn 2 (and save())" in text
    assert [m["content"] for m in s.agent.messages if m["role"] == "user"][-1].startswith("explain storage.py")
    assert A.undo_message({"conversation": False})[0].startswith("Nothing to undo")
    assert A.undo_message({"conversation": True})[0] == "Undone. No files in this chat's folder had changed."


# ---------------------------------------------------------------------------------------------------- OM-03
def test_om03_undo_of_a_turn_removes_the_copy_of_a_file_dropped_into_it(home, proj, monkeypatch):
    """OM-03: dragging sample.csv into a Code chat left an untracked "sample (2).csv" in the repository, and Undo of
    that turn did not remove it. The same file dropped again is used where it is (WF-02); a different file is still
    copied, and Undo of the turn now removes the copy while it holds what was dropped."""
    import base64
    script(monkeypatch, "Biggest payee: rent co.", "Again: rent co.")
    (proj / "sample.csv").write_text("date,payee,amount\n2026-01-01,rent co,1200\n")
    v = chats.create({"workdir": str(proj), "no_mcp": True})
    s = chats.get(v["id"])
    dropped = b"date,payee,amount\n2026-02-01,blue cafe,13\n"            # his newer export, same name
    s.send("which payee is the biggest expense?", files=[{"name": "sample.csv",
                                                          "data": base64.b64encode(dropped).decode()}])
    _idle(s)
    assert (proj / "sample (2).csv").read_bytes() == dropped
    r = s.revert(s.cp.turns()[-1]["n"])
    assert not (proj / "sample (2).csv").exists() and "sample (2).csv" in r["deleted"]
    assert (proj / "sample.csv").exists()                                  # his own file stays
    assert "removed 1 file (sample (2).csv)" in A.undo_message(r)[0].lower()
    # a copy he changed after dropping it is his: Undo leaves it
    s.send("again", files=[{"name": "sample.csv", "data": base64.b64encode(dropped).decode()}])
    _idle(s)
    (proj / "sample (2).csv").write_text("edited by hand\n")
    r = s.revert(s.cp.turns()[-1]["n"])
    assert (proj / "sample (2).csv").read_text() == "edited by hand\n" and "sample (2).csv" in r["kept"]


# ---------------------------------------------------------------------------------------------------- OM-07
def test_om07_search_finds_a_code_chat_by_the_words_of_its_messages(home, proj, monkeypatch):
    """OM-07 (not a bug): "dedupe" did not find his chat about dedupe.py. Search reads message text (PF-07, through the
    store's cached raw text, no transcript scan per key). His dedupe message was in the turn he had undone just before,
    and Undo takes a turn out of the chat, so nothing in it said "dedupe" any more."""
    from meshpool_harness.ui import hub as HUB
    script(monkeypatch, "storage.py loads and saves the ledger.", "Added the docstring to find_duplicates.")
    v = chats.create({"workdir": str(proj), "no_mcp": True, "kind": "code"})
    s = chats.get(v["id"])
    s.send("explain what ledgerly/storage.py does")
    _idle(s)
    s.send("add a one-line docstring to find_duplicates in ledgerly/dedupe.py")
    _idle(s)
    assert [h["id"] for h in A.search("dedupe")] == [v["id"]]
    found = [i["id"] for g in HUB.search("dedupe")["groups"] for i in g["items"]]
    assert v["id"] in found
    s.revert(s.cp.turns()[-1]["n"])                                       # what he did before searching
    assert A.search("dedupe") == [] and [h["id"] for h in A.search("storage")] == [v["id"]]


# ---------------------------------------------------------------------------------------------------- OM-10
@pytest.mark.parametrize("model", ["mock-err200", "deepseek-v4.1-flash", "claude-sonnet", "mystery-7b"])
def test_om10_the_panel_names_the_context_size_the_chat_names(home, model):
    """OM-10: the chat said "context 200k (assumed)" and the panel "of 128K tokens" for the same model. The panel
    now reads the window from the same place as the chat's line (core/context.py), without asking the endpoint."""
    from meshpool_harness.core import context as CTX
    from meshpool_harness.ui import workbench as W
    size, src = W.model_window(model)
    assert size == CTX.window(model), (model, size)
    k = f"{size / 1000:,.0f}k" if size >= 10_000 else f"{size:,}"
    assert CTX.describe(model).startswith(f"context {k} ")
