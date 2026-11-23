"""Regression guards for bug classes reported in other open-source coding-agent harnesses (OSS-nn ids are in
docs/audit/ledger/oss-guards.md). Each test reproduces the reported scenario against Meshpool and asserts the safe
behaviour. No network, no model."""
import json
import os
import re
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness.agent import compaction, core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core.client import MockClient, tool_call  # noqa: E402
from meshpool_harness.core.tools import ToolError, Workspace, validate  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "meshpool_harness"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_AGENT_V3", "1")


def _ws(tmp_path, **files):
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    for n, c in files.items():
        (root / n).write_bytes(c if isinstance(c, bytes) else c.encode())
    return Workspace(str(root), str(tmp_path / "out")), root


# ---------------------------------------------------------------- OSS-01
def test_oss01_concurrent_settings_writes_lose_nothing(tmp_path):
    path = tmp_path / "settings.env"
    path.write_text("# my comment\nKEEP=1\n")
    errs = []

    def w(i):
        try:
            for j in range(5):
                C.write({f"K_{i}_{j}": str(i * 10 + j)}, path)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    got = dict(ln.split("=", 1) for ln in path.read_text().splitlines() if "=" in ln and not ln.startswith("#"))
    assert got["KEEP"] == "1" and "# my comment" in path.read_text()
    assert all(got[f"K_{i}_{j}"] == str(i * 10 + j) for i in range(8) for j in range(5))
    assert not list(tmp_path.glob(".settings.env.*.tmp"))          # no torn temp files left


def test_oss01_concurrent_writes_across_processes(tmp_path):
    path = tmp_path / "settings.env"
    code = ("import sys; sys.path.insert(0, %r); from pathlib import Path; from meshpool_harness import config as C\n"
            "for j in range(10): C.write({'P%%s_%%d' %% (sys.argv[1], j): 'v'}, Path(sys.argv[2]))\n"
            % str(APP.parent))
    ps = [subprocess.Popen([sys.executable, "-c", code, str(i), str(path)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           env={**os.environ, "MESHPOOL_HOME": str(tmp_path / "h")})
          for i in range(4)]
    assert all(p.wait(90) == 0 for p in ps)
    keys = {ln.split("=")[0] for ln in path.read_text().splitlines() if "=" in ln}
    assert keys == {f"P{i}_{j}" for i in range(4) for j in range(10)}


# ---------------------------------------------------------------- OSS-03 / OSS-04
def test_oss03_prompt_goes_over_stdio_not_argv():
    # codex #31776: a .bat wrapper forwarding %* mangles multi-line prompts. The ACP and Pi RPC harnesses spawn with
    # fixed args and a stdin pipe; the prompt is a JSON-RPC message written to that pipe.
    acp = (APP / "harnesses" / "acp.py").read_text(encoding="utf-8")
    pi = (APP / "harnesses" / "pi_rpc.py").read_text(encoding="utf-8")
    assert '"session/prompt", {"sessionId": self.acp_session, "prompt": prompt}' in acp
    spawn = re.search(r"subprocess\.Popen\(\[exe, \*\[str\(a\) for a in m\.get\(\"args\", \[\]\)\]\][^)]*\)", acp)
    assert spawn and "stdin=subprocess.PIPE" in spawn.group(0) and "prompt" not in spawn.group(0)
    spawn = re.search(r"subprocess\.Popen\(args,[^)]*\)", pi)
    assert spawn and "stdin=subprocess.PIPE" in spawn.group(0) and "prompt" not in spawn.group(0)
    # the message that carries a multi-line prompt is one JSON line: newlines and quotes survive, %* is inert
    p = "line one\nline two %* \"quoted\" & more\r\nline three"
    wire = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "session/prompt",
                       "params": {"sessionId": "s", "prompt": [{"type": "text", "text": p}]}})
    assert "\n" not in wire and json.loads(wire)["params"]["prompt"][0]["text"] == p


def test_oss04_bash_tool_never_inherits_stdin(tmp_path):
    # codex #20919: a spawn that inherits a non-TTY stdin pipe waits forever for EOF
    ws, root = _ws(tmp_path)
    r = ws.bash("cat; echo reached-the-end", timeout_s=20)
    assert "reached-the-end" in r                  # cat got EOF at once (stdin is the null device)


def test_oss04_every_spawn_sets_stdin_explicitly():
    for rel in ("core/tools.py", "harnesses/__init__.py"):
        src = (APP / rel).read_text(encoding="utf-8")
        found = list(re.finditer(r"subprocess\.Popen\((?:[^()]|\([^()]*\))*\)", src, re.S))
        assert found
        for m in found:
            assert "stdin=" in m.group(0), f"{rel}: Popen without an explicit stdin: {m.group(0)[:80]}"
    assert (APP / "core/tools.py").read_text(encoding="utf-8").count("stdin=subprocess.DEVNULL") >= 2


# ---------------------------------------------------------------- OSS-06 / OSS-07
def _long_history(n=30):
    msgs = [{"role": "system", "content": "SYSTEM PROMPT with AGENTS.md rules: always use tabs"},
            {"role": "user", "content": "ORIGINAL TASK: refactor the parser"}]
    for i in range(n):
        cid = f"c{i}"
        name = "todo_write" if i == 3 else "edit" if i % 2 else "bash"
        args = {"todos": [{"content": "write tests", "status": "pending"}]} if i == 3 else \
            {"path": f"f{i}.py"} if name == "edit" else {"command": f"pytest -k t{i}"}
        msgs.append({"role": "assistant", "content": f"step {i}", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
        msgs.append({"role": "tool", "tool_call_id": cid, "content": "[exit 1]\nboom" if name == "bash" else "ok"})
    return msgs


def _note(msgs):
    return next(m["content"] for m in msgs if "summarized to save space" in str(m.get("content")))


def test_oss06_compaction_keeps_system_task_todo_and_files(monkeypatch):
    monkeypatch.setenv("MH_COMPACT_MODEL", "0")                    # rules only
    msgs = _long_history()
    first_two = [dict(m) for m in msgs[:2]]
    r = compaction.compact_history(msgs)
    assert r and r["removed"] > 0
    assert msgs[:2] == first_two                                   # system prompt (AGENTS.md rules) and task verbatim
    note = _note(msgs)
    assert "write tests" in note and "Plan at that point" in note  # the todo list
    assert "f1.py" in note and "pytest -k" in note and "exit 1" in note
    ids = {c["id"] for m in msgs if m.get("tool_calls") for c in m["tool_calls"]}
    assert all(m["tool_call_id"] in ids for m in msgs if m["role"] == "tool")   # no orphaned tool result


def test_oss07_failed_or_interrupted_summary_call_still_keeps_facts_and_history():
    class Dead:
        def chat(self, **kw):
            raise ConnectionResetError("network dropped mid-compaction")

    class Tiny:                                                     # "succeeds" with an empty stub
        def chat(self, **kw):
            return {"choices": [{"message": {"content": ""}}], "usage": {}}
    for client in (Dead(), Tiny()):
        msgs = _long_history()
        r = compaction.compact_history(msgs, client=client)
        assert r and r["model"] is False
        note = _note(msgs)
        assert "could not be written" in note and "f1.py" in note and "Plan at that point" in note
        assert msgs[0]["content"].startswith("SYSTEM PROMPT") and msgs[1]["content"].startswith("ORIGINAL TASK")
        assert msgs[-1] == _long_history()[-1]                      # the recent tail is word for word
    short = _long_history(2)                                        # too little to summarise: nothing is touched
    assert compaction.compact_history(short, client=Dead()) is None and short == _long_history(2)


# ---------------------------------------------------------------- OSS-08 (N/A: JSON files, no database)
def test_oss08_crash_mid_save_or_migration_leaves_sessions_loadable(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    a = Agent(str(work), mode="auto")
    a.messages.append({"role": "user", "content": "hello"})
    a.save()
    sid = a.id
    f = core.SESSIONS / sid / "session.json"
    good = f.read_text(encoding="utf-8")
    a.messages.append({"role": "user", "content": "second"})
    with monkeypatch.context() as mp:                               # a kill after the temp file, before the rename
        mp.setattr(os, "replace", lambda *a_, **k: (_ for _ in ()).throw(OSError("killed")))
        a.save()
    assert f.read_text(encoding="utf-8") == good                    # the old session is untouched
    (core.SESSIONS / sid / "session.json.tmp").write_text('{"id": "x", "messag')     # half-written leftovers
    (core.SESSIONS / "broken").mkdir()
    (core.SESSIONS / "broken" / "session.json").write_text("{not json")
    b = Agent.resume(str(work))
    assert b is not None and b.id == sid
    assert any(m.get("content") == "hello" for m in b.messages)


# ---------------------------------------------------------------- OSS-10
def test_oss10_toolUse_stop_with_zero_tool_calls_is_not_silent(tmp_path):
    from meshpool_harness.core.loop import run

    class Truncated(MockClient):
        def chat(self, **kw):
            r = super().chat(**kw)
            r["choices"][0]["finish_reason"] = "tool_calls"          # stop reason says toolUse, none arrived
            return r
    ws, root = _ws(tmp_path)
    client = Truncated([{"role": "assistant", "content": ""}] * 5)
    res = run("do it", client=client, model="m", profile=None, ws=ws, ctx={"task_id": "t"}, system_text="s",
              require_check=False)
    assert res.ok is False and res.reason == "model stopped calling tools"
    nudges = [m for m in client.requests[-1]["messages"]
              if m["role"] == "user" and "Use the tools to continue" in m["content"]]
    assert len(nudges) == 2 and len(client.requests) == 3            # nudged twice, then stopped with a reason


def test_oss10_app_agent_reports_an_empty_toolUse_reply_as_an_error(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    events = []
    a.emit = events.append
    a._post = lambda base, key, body, stream: ({"choices": [{"message": {"role": "assistant", "content": ""},
                                                            "finish_reason": "tool_calls"}], "usage": {}}, False)
    done = []
    t = threading.Thread(target=lambda: done.append(a.send("go")), daemon=True)
    t.start()
    t.join(30)
    assert done == [""]                                              # the turn ended, it did not hang
    errs = [e for e in events if e.get("type") == "error"]
    assert errs and "empty reply" in errs[0]["text"]                 # and the user was told why
    assert not any(m.get("role") == "assistant" and not m.get("content") for m in a.messages)   # no empty turn kept


# ---------------------------------------------------------------- OSS-13
@pytest.mark.parametrize("src,old,new,want", [
    ("def f():   \n    return 1\n", "def f():\n    return 1", "def f():\n    return 2", "def f():\n    return 2\n"),
    ("a\r\n  b = 1\r\nc\r\n", "  b = 1", "  b = 2", "a\r\n  b = 2\r\nc\r\n"),
    ("a\r\nb = 1\r\nc = 2\r\n", "b = 1\nc = 2", "b = 5\nc = 6", "a\r\nb = 5\r\nc = 6\r\n"),
    ("if x:\n\tdo()\n\tdone()\n", "if x:\n    do()\n    done()", "if x:\n    do()\n    stop()", None),
    ("class A:\n    def m(self):\n        return 1\n", "def m(self):\n    return 1", "def m(self):\n    return 2", None),
])
def test_oss13_whitespace_and_crlf_edits_apply(tmp_path, src, old, new, want):
    ws, root = _ws(tmp_path, **{"a.txt": src.encode()})
    ws.read_file("a.txt")
    r = ws.edit("a.txt", old, new)
    out = (root / "a.txt").read_bytes().decode()
    assert r.startswith("edited") and new.splitlines()[-1].strip() in out
    if want:
        assert out == want
    assert out.count("\r\n") == src.count("\r\n")                    # CRLF files stay CRLF


def test_oss13_failed_match_points_at_the_closest_text(tmp_path):
    ws, root = _ws(tmp_path, **{"a.py": "alpha = 1\nbeta = 2\ngamma = 3\n"})
    ws.read_file("a.py")
    with pytest.raises(ToolError) as e:
        ws.edit("a.py", "beta = 22", "beta = 3")
    assert "Closest text in the file" in str(e.value) and "beta = 2" in str(e.value)
    assert (root / "a.py").read_text() == "alpha = 1\nbeta = 2\ngamma = 3\n"


# ---------------------------------------------------------------- OSS-16
@pytest.fixture
def _link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET\n")
    ws, root = _ws(tmp_path)
    try:
        (root / "link_dir").symlink_to(outside, target_is_directory=True)
        (root / "link_file").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    return ws, root, outside


def test_oss16_write_and_edit_through_symlink_are_refused(_link):
    ws, root, outside = _link
    with pytest.raises(ToolError, match="inside the workdir"):
        ws.write_file("link_dir/new.txt", "pwned")
    with pytest.raises(ToolError, match="inside the workdir"):
        ws.write_file("link_file", "pwned")
    with pytest.raises(ToolError):
        ws.edit("link_file", "TOP", "BOTTOM")
    assert not (outside / "new.txt").exists() and (outside / "secret.txt").read_text() == "TOP SECRET\n"


def test_oss16_search_skips_symlinks_that_leave_the_project(_link):
    ws, root, outside = _link
    (root / "ok.txt").write_text("TOP SECRET inside\n")
    out = ws.grep("TOP SECRET", ".", inside_only=True)
    assert "ok.txt" in out and "secret.txt" not in out and "link_file" not in out and "link_dir" not in out
    assert not ws._inside(root / "link_file") and ws._inside(root / "ok.txt")


# ---------------------------------------------------------------- OSS-19
def test_oss19_edit_after_the_user_changed_the_file_is_refused(tmp_path):
    ws, root = _ws(tmp_path, **{"a.py": "x = 1\ny = 2\n"})
    ws.read_file("a.py")
    (root / "a.py").write_text("x = 1\ny = 2\n# user's concurrent edit\n")      # IDE save
    with pytest.raises(ToolError, match="changed since you last read it"):
        ws.edit("a.py", "y = 2", "y = 3")
    assert "concurrent edit" in (root / "a.py").read_text() and "y = 3" not in (root / "a.py").read_text()
    ws.read_file("a.py")                                                         # after a fresh read it works
    ws.edit("a.py", "y = 2", "y = 3")
    assert "concurrent edit" in (root / "a.py").read_text() and "y = 3" in (root / "a.py").read_text()


def test_oss19_line_ending_only_difference_is_not_a_conflict(tmp_path):
    ws, root = _ws(tmp_path, **{"a.py": "x = 1\ny = 2\n"})
    ws.read_file("a.py")
    (root / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    ws.edit("a.py", "y = 2", "y = 3")
    assert (root / "a.py").read_bytes() == b"x = 1\r\ny = 3\r\n"


# ---------------------------------------------------------------- OSS-20
def test_oss20_no_os_keyring_and_large_secrets_round_trip_in_a_private_file(tmp_path):
    for p in APP.rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"^\s*(import keyring|from keyring)\b", src, re.M), p
    big = "t" * 20_000                                                # far over the 2560-byte Windows credential limit
    path = tmp_path / "settings.env"
    C.write({"MCP_OAUTH_TOKEN": big}, path)
    assert dict(ln.split("=", 1) for ln in path.read_text().splitlines())["MCP_OAUTH_TOKEN"] == big
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
    from meshpool_harness import fsio
    j = tmp_path / "mcp.json"
    fsio.atomic_write_json(j, {"token": big})
    assert json.loads(j.read_text())["token"] == big


# ---------------------------------------------------------------- OSS-25
def test_oss25_every_parallel_tool_call_gets_a_result_after_a_stop(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    calls = [{"id": f"c{i}", "type": "function", "function": {"name": "glob", "arguments": "{}"}} for i in range(4)]
    a.messages += [{"role": "user", "content": "go"}, {"role": "assistant", "content": "", "tool_calls": calls},
                   {"role": "tool", "tool_call_id": "c1", "content": "real result"}]
    a._repair()
    got = {m["tool_call_id"]: m["content"] for m in a.messages if m["role"] == "tool"}
    assert set(got) == {"c0", "c1", "c2", "c3"} and got["c1"] == "real result"
    a._repair()                                                      # idempotent
    assert len([m for m in a.messages if m["role"] == "tool"]) == 4


# ---------------------------------------------------------------- OSS-27
def test_oss27_windows_bash_prefers_bundled_or_git_and_rejects_the_wsl_stub(monkeypatch, tmp_path):
    from meshpool_harness import bundled
    monkeypatch.delenv("MH_BASH", raising=False)

    class _NtOs:                                                     # config sees Windows; pathlib keeps the real os
        name = "nt"

        def __getattr__(self, k):
            return getattr(os, k)
    monkeypatch.setattr(C, "os", _NtOs())
    monkeypatch.setattr(bundled, "bundled_bash", lambda: None)
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(C.shutil, "which", lambda n, *a, **k: r"C:\Windows\System32\bash.exe")
    assert C.find_bash() is None                                     # only the WSL stub: refuse it
    git = tmp_path / "pf" / "Git" / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    git.write_text("")
    assert C.find_bash() == str(git)                                 # Git Bash beats the stub on PATH
    gb = str(tmp_path / "bundled" / "bash.exe")
    monkeypatch.setattr(bundled, "bundled_bash", lambda: gb)
    assert C.find_bash() == gb                                       # the copy shipped with Meshpool beats Git
    monkeypatch.setattr(bundled, "bundled_bash", lambda: None)
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    assert C.find_bash() is None                                     # packaged app never uses the customer's PATH


# ---------------------------------------------------------------- OSS-31
def test_oss31_invalid_and_truncated_tool_args_are_reported_not_fatal():
    with pytest.raises(ToolError, match="not valid JSON"):
        validate("read_file", '{"path": "a.py', ["read_file"])
    with pytest.raises(ToolError, match="cut off"):
        validate("read_file", '{"__truncated__": true}', ["read_file"])
    with pytest.raises(ToolError, match="must be a JSON object"):
        validate("read_file", "[1,2]", ["read_file"])
    with pytest.raises(ToolError, match="missing required argument"):      # a ToolError the model can read, not a crash
        validate("glob", "", ["glob"])


def test_oss31_loop_keeps_going_after_merged_or_truncated_calls(tmp_path):
    from meshpool_harness.core.loop import _sanitize_calls, run
    merged = tool_call("read_file", {}, "m1")
    merged["tool_calls"][0]["function"]["arguments"] = '{"path": "a.py"}{"path": "b.py"}'
    cut = tool_call("write_file", {}, "m2")
    cut["tool_calls"][0]["function"]["arguments"] = '{"path": "a.py", "content": "unterminated'
    for c in _sanitize_calls(merged["tool_calls"] + cut["tool_calls"]):
        assert isinstance(json.loads(c["function"]["arguments"]), dict)       # the next request is valid JSON
    ws, root = _ws(tmp_path)
    client = MockClient([merged, cut, tool_call("finish", {"answer": "done", "evidence": []})])
    res = run("go", client=client, model="m", profile=None, ws=ws, ctx={"task_id": "t"}, system_text="s",
              require_check=False)
    outs = [m["content"] for m in client.requests[-1]["messages"] if m["role"] == "tool"]
    assert res.ok and len(outs) == 2 and all(o.startswith("ERROR") for o in outs) and "cut off" in outs[1]
    assert not (root / "a.py").exists()                                       # the half-call never ran


# ---------------------------------------------------------------- OSS-32
def test_oss32_update_is_staged_beside_the_app_and_the_running_files_are_untouched(tmp_path, monkeypatch):
    from meshpool_harness import updater as U
    app = tmp_path / "inst" / "app"
    (app / "meshpool_harness").mkdir(parents=True)
    init = app / "meshpool_harness" / "__init__.py"
    init.write_text('__version__ = "1.0.0"\n')
    exe = app.parent / "Meshpool.exe"
    exe.write_bytes(b"MZ running exe")
    (app.parent / "bin").mkdir()
    (app.parent / "bin" / "meshpool.cmd").write_text("@echo off\n")
    (app / "requirements.txt").write_text("x\n")
    rel = tmp_path / "rel" / "meshpool-harness-2.0.0" / "app" / "meshpool_harness"
    rel.mkdir(parents=True)
    (rel / "__init__.py").write_text('__version__ = "2.0.0"\n')
    (rel.parent / "requirements.txt").write_text("x\n")
    tgz = tmp_path / "src.tgz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(tmp_path / "rel" / "meshpool-harness-2.0.0", arcname="meshpool-harness-2.0.0")
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U, "SRC", "")
    v = {"ok": True, "newer": True, "kind": "win-app", "current": "1.0.0", "latest": "2.0.0", "tag": "v2.0.0",
         "assets": [{"name": U.SRC_ASSET, "url": "src"}, {"name": U.SUMS, "url": "s"}], "url": "u"}
    monkeypatch.setattr(U, "check", lambda force=False: v)
    monkeypatch.setattr(U, "_sums", lambda v_: {U.SRC_ASSET: U._sha256(tgz)})
    monkeypatch.setattr(U, "_download", lambda url, dst: __import__("shutil").copy(tgz, dst) and dst)
    r = U.prepare(v)
    assert r["ok"] and r["action"] == "staged", r
    stage = Path(r["stage"])
    assert stage.parent == app and stage.name.startswith(".update-") and (stage / ".complete").exists()
    assert '"1.0.0"' in init.read_text() and exe.read_bytes() == b"MZ running exe"   # nothing replaced while running
    assert (stage / "meshpool_harness" / "__init__.py").read_text() == '__version__ = "2.0.0"\n'
