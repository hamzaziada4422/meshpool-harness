"""CPU use in the agent loop and the processes it starts (0.0.17 audit, area "agent").

- App chats (Manager(chat=...)) share one office and one assistant process, and opening a chat starts no process at
  all: a chat id in those servers' environment used to make three Python processes per chat (office, assistant,
  browser), each started when the chat opened.
- A chat's browser starts on its first call and is never stopped for being idle.
- The environment snapshot asks each program for its version once per program file, not once per chat.
- git_config_safe keeps a "safe" answer while nothing it read changed, and notices every change.
- grep and glob ask git once per search, not twice.
- The skills list is kept per skill folder: 130 skills were read and YAML-parsed about 40 times per ten-step turn.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

psutil = pytest.importorskip("psutil")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import skills as SK  # noqa: E402
from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402
from meshpool_harness.harnesses.acp import office_extra  # noqa: E402

DEFAULTS = json.loads((C.APP / "defaults" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
GIT = shutil.which("git")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("MH_BROWSER_USE", "cdp")             # the browser server never fetches Chromium here
    monkeypatch.setenv("MH_BROWSER_CDP_URL", "http://127.0.0.1:9")
    (tmp_path / "home").mkdir(exist_ok=True)
    servers = {k: DEFAULTS[k] for k in ("meshpool-assistant", "browser")}
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    yield tmp_path
    M.stop_all()


class Spawns:
    """Counts the processes started through subprocess.Popen, with their command lines."""

    def __init__(self, monkeypatch):
        self.cmds = []
        real = subprocess.Popen.__init__
        me = self

        def counting(self_, args, *a, **k):
            me.cmds.append(args if isinstance(args, str) else " ".join(str(x) for x in args))
            return real(self_, args, *a, **k)
        monkeypatch.setattr(subprocess.Popen, "__init__", counting)

    def take(self):
        out, self.cmds = self.cmds, []
        return out


def _kids():
    out = []
    for k in psutil.Process().children(recursive=True):
        try:
            if k.status() != psutil.STATUS_ZOMBIE:
                out.append(k)
        except psutil.Error:
            pass
    return out


def _servers(kind):
    return [k for k in _kids() if any(kind in a for a in k.cmdline())]


def _chat(folder, cid):
    return M.Manager(roots=[str(folder)], extra=office_extra(str(folder)), chat=cid).start(timeout=60)


def _tool(m, word):
    return next(k for k in m.map if k.endswith(word))


def _folders(tmp_path, n):
    out = []
    for i in range(n):
        d = tmp_path / f"chat{i}"
        d.mkdir()
        out.append(d)
    return out


def test_five_app_chats_start_nothing_on_open_and_share_office_and_assistant(home):
    _chat(home, "prime").close()                         # the first start ever saves the tool lists
    before = {k.pid for k in _kids()}
    chats = [_chat(d, f"c{i}") for i, d in enumerate(_folders(home, 5))]
    assert not [k for k in _kids() if k.pid not in before]   # was 15 processes (3 per chat), each started on open
    for c in (chats[0], chats[3]):
        assert not c.call(_tool(c, "find_icon"), {"query": "money", "limit": 1})[1]
    for c in (chats[1], chats[4]):
        assert not c.call(_tool(c, "memory_view"), {})[1]
    assert len(_servers("office_mcp_server")) == 1 and len(_servers("assistant_mcp_server")) == 1
    assert not _servers("browser_mcp_server")            # no chat used its browser
    for c in chats:
        c.close()
    time.sleep(0.5)
    assert not [k for k in _kids() if k.pid not in before]


def test_chat_browser_starts_on_first_call_and_is_kept_while_idle(home, monkeypatch):
    monkeypatch.setenv("MH_MCP_IDLE_S", "1")
    _chat(home, "prime").close()
    a, b = [_chat(d, f"b{i}") for i, d in enumerate(_folders(home, 2))]
    assert not _servers("browser_mcp_server")
    assert not a.call(_tool(a, "browser_close"), {})[1]
    assert not b.call(_tool(b, "memory_view"), {})[1]
    assert len(_servers("browser_mcp_server")) == 1      # only the chat that used it has one
    deadline = time.time() + 15
    while time.time() < deadline and _servers("assistant_mcp_server"):
        time.sleep(0.3)
    assert not _servers("assistant_mcp_server")          # an idle shared server is stopped ...
    assert len(_servers("browser_mcp_server")) == 1      # ... a chat's browser is not (its pages, its sign-ins)
    a.close()
    b.close()
    deadline = time.time() + 15
    while time.time() < deadline and _servers("browser_mcp_server"):
        time.sleep(0.3)
    assert not _servers("browser_mcp_server")            # closing the chat ends it


def test_each_call_carries_its_chat_and_folder(home, monkeypatch):
    m = M.Manager(roots=[str(home)], chat="chat-7")
    spec = m._with_chat(dict(DEFAULTS["meshpool-assistant"]))
    s = M.Server("meshpool-assistant", spec, home / "logs", [str(home)])
    sent = []
    monkeypatch.setattr(s, "_rpc", lambda method, params, timeout: sent.append(params) or {"content": []})
    s.call("memory_view", {})
    args = sent[0]["arguments"]
    assert args["_chat"] == "chat-7" and args["_project"] == str(home.resolve())
    assert M._pool_key(spec, [str(home / "a")]) == M._pool_key(spec, [str(home / "b")])   # one process for all chats
    assert "MH_CHAT_ID" not in (spec.get("env") or {})
    browser = m._with_chat(dict(DEFAULTS["browser"]))
    assert browser["env"]["MH_CHAT_ID"] == "chat-7"      # the browser stays the chat's own
    assert M._cache_key("browser", browser) == M._cache_key("browser", m._with_chat(dict(DEFAULTS["browser"])) | {
        "env": {"MH_CHAT_ID": "other"}})                 # one saved tool list for every chat's browser


def test_program_versions_are_asked_once_per_program_file(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("a shell script stands in for a program")
    exe = tmp_path / "tool"
    exe.write_text("#!/bin/sh\necho tool 1.0\n", encoding="utf-8")
    exe.chmod(0o755)
    sp = Spawns(monkeypatch)
    assert C.program_version(str(exe)) == (0, "tool 1.0")
    assert C.program_version(str(exe)) == (0, "tool 1.0")
    assert len(sp.take()) == 1
    exe.write_text("#!/bin/sh\necho tool 2.0 new\n", encoding="utf-8")   # reinstalled: asked again
    assert C.program_version(str(exe)) == (0, "tool 2.0 new")
    assert len(sp.take()) == 1


def test_environment_snapshot_runs_no_program_the_second_time(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as A
    first = A._env_snapshot(tmp_path)
    sp = Spawns(monkeypatch)
    assert A._env_snapshot(tmp_path) == first
    assert sp.take() == []                               # was 7 version checks for every new chat


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    return path


def _age(path: Path, home: Path):
    old = time.time() - 3600
    for d, ds, fs in os.walk(path):
        for n in ds + fs:
            os.utime(os.path.join(d, n), (old, old))
    os.utime(path, (old, old))
    os.utime(home, (old, old))


@pytest.mark.skipif(not GIT, reason="needs git")
def test_git_config_safe_is_kept_and_sees_every_change(tmp_path, monkeypatch):
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    T._GCS.clear()
    repo = _repo(tmp_path / "repo")
    _age(repo, home)
    sp = Spawns(monkeypatch)
    assert T.git_config_safe(repo) and len(sp.take()) == 2
    for _ in range(5):
        assert T.git_config_safe(repo)
    assert sp.take() == []                               # was two git processes on every git read the agent ran
    subprocess.run(["git", "status", "--short"], cwd=repo, capture_output=True)   # rewrites the index: still kept
    sp.take()
    assert T.git_config_safe(repo) and sp.take() == []

    cfg = repo / ".git" / "config"
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text + "[core]\n\tfsmonitor = ./evil.sh\n", encoding="utf-8")
    assert not T.git_config_safe(repo)                   # a program in the repo's config: refused at once
    assert not T.git_config_safe(repo) and len(sp.take()) == 2   # a refusal is never kept: git each time
    cfg.write_text(text, encoding="utf-8")
    assert T.git_config_safe(repo)

    _age(repo, home)
    assert T.git_config_safe(repo)
    sp.take()
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    assert not T.git_config_safe(repo)                   # a hook appeared
    hook.unlink()
    _age(repo, home)
    assert T.git_config_safe(repo)
    (repo / ".gitmodules").write_text("[submodule \"x\"]\n", encoding="utf-8")
    assert not T.git_config_safe(repo)                   # a submodule file appeared
    (repo / ".gitmodules").unlink()
    _age(repo, home)
    assert T.git_config_safe(repo)
    (home / ".gitconfig").write_text("[core]\n\thooksPath = /elsewhere\n", encoding="utf-8")
    sp.take()
    T.git_config_safe(repo)
    assert len(sp.take()) == 2                           # the user's own config changed: asked again


@pytest.mark.skipif(not GIT, reason="needs git")
def test_git_config_safe_is_not_kept_for_a_file_changed_just_now(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    T._GCS.clear()
    repo = _repo(tmp_path / "repo")                      # everything in it was written a moment ago
    sp = Spawns(monkeypatch)
    assert T.git_config_safe(repo) and T.git_config_safe(repo)
    assert len(sp.take()) == 4                           # a change in the same clock tick would not show: asked again


@pytest.mark.skipif(not GIT, reason="needs git")
def test_search_asks_git_once_per_search(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "x.txt").write_text("needle\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "y.txt").write_text("needle\n", encoding="utf-8")
    ws = T.Workspace(str(repo), str(tmp_path / "run"))
    sp = Spawns(monkeypatch)
    out = ws.grep("needle")
    assert "src/y.txt" in out and "build" not in out and len(sp.take()) == 1
    assert ws.glob("**/*.txt").split().count("src/y.txt") == 1 and len(sp.take()) == 1
    assert "y.txt" in ws.grep("needle", path="src") and len(sp.take()) == 2
    assert "x.txt" in ws.grep("needle", path="build")    # an ignored folder named on purpose is searched
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "z.txt").write_text("needle\n", encoding="utf-8")
    assert "z.txt" in T.Workspace(str(plain), str(tmp_path / "run2")).grep("needle")   # not a repo: all searched


def _skill(root: Path, name: str, desc: str):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nDo it.\n", encoding="utf-8")
    return d


def test_skills_list_is_kept_and_sees_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    SK._ROWS.clear()
    SK.ensure_defaults()
    root = SK.folder()
    _skill(root, "my-skill", "first words")
    _age(tmp_path / "home", tmp_path / "home")
    parses = []
    real = SK.parse
    monkeypatch.setattr(SK, "parse", lambda text: parses.append(1) or real(text))
    first = SK.load_all()
    n = len(first)
    assert n > 10 and len(parses) == n
    parses.clear()
    for _ in range(40):                                  # about what one ten-step turn asks for
        assert SK.load_all() == first
    assert parses == []
    row = next(s for s in SK.load_all() if s["id"] == "my-skill")
    row["files"].append("x")                             # a caller changing its copy changes nothing kept
    assert next(s for s in SK.load_all() if s["id"] == "my-skill")["files"] == []

    _skill(root, "my-skill", "second, longer words")     # edited: read again
    assert next(s for s in SK.load_all() if s["id"] == "my-skill")["description"] == "second, longer words"
    (root / "my-skill" / "scripts").mkdir()
    (root / "my-skill" / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    assert next(s for s in SK.load_all() if s["id"] == "my-skill")["files"] == [os.path.join("scripts", "run.py")]
    _age(tmp_path / "home", tmp_path / "home")
    SK.load_all()
    (root / "my-skill" / "scripts" / "more.py").write_text("print(2)\n", encoding="utf-8")   # deeper folder
    assert len(next(s for s in SK.load_all() if s["id"] == "my-skill")["files"]) == 2
    SK.toggle("my-skill", False)
    assert not next(s for s in SK.load_all() if s["id"] == "my-skill")["enabled"]
    shutil.rmtree(root / "my-skill")
    assert "my-skill" not in {s["id"] for s in SK.load_all()}
    _skill(root, "new-one", "added")
    assert "new-one" in {s["id"] for s in SK.load_all()}


def test_ten_step_turn_runs_only_its_own_commands_the_second_time(tmp_path, monkeypatch):
    """Two chats in one process, each a scripted ten-step turn of bash, read, grep and glob calls in a git project.
    The second chat runs its five commands and one git check-ignore per search, nothing else; it reads no SKILL.md."""
    if not GIT or os.name == "nt":
        pytest.skip("needs git and bash")
    from meshpool_harness.agent import core as A
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("MH_ENGINE_STYLE", "meshpool")    # the classic tools: bash, read_file, grep, glob
    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    proj = _repo(tmp_path / "proj")
    for i in range(4):
        (proj / f"f{i}.py").write_text(f"print({i})\n", encoding="utf-8")
    SK.ensure_defaults()
    _age(tmp_path, tmp_path)
    T._GCS.clear()
    SK._ROWS.clear()
    steps = [("bash", {"command": "ls"}), ("read_file", {"path": "f0.py"}), ("bash", {"command": "git status"}),
             ("read_file", {"path": "f1.py"}), ("bash", {"command": "cat f2.py"}), ("grep", {"pattern": "print"}),
             ("bash", {"command": "git diff --stat"}), ("read_file", {"path": "f3.py"}),
             ("bash", {"command": "wc -l f3.py"}), ("glob", {"pattern": "*.py"})]

    def run_chat(n):
        it = iter(enumerate(steps + [None]))

        def fake_chat(self, turn):
            i, nxt = next(it)
            if nxt is None:
                return {"role": "assistant", "content": "done"}, False
            return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}-{i}", "type": "function",
                    "function": {"name": nxt[0], "arguments": json.dumps(nxt[1])}}]}, False
        monkeypatch.setattr(A.Agent, "_chat", fake_chat)
        a = A.Agent(str(proj), approve=lambda *x: ("yes", ""), mode="ask", sessions_dir=str(tmp_path / f"s{n}"))
        evs = []
        a.emit = evs.append
        a.send("do the ten steps")
        ends = [e for e in evs if e.get("type") == "tool_end"]
        assert len(ends) == 10 and all(e.get("ok") for e in ends), [e.get("output") for e in ends]

    run_chat(0)
    sp = Spawns(monkeypatch)
    parses = []
    real = SK.parse
    monkeypatch.setattr(SK, "parse", lambda text: parses.append(1) or real(text))
    run_chat(1)
    cmds = sp.take()
    bash = [c for c in cmds if " -c " in c and "check-ignore" not in c]
    others = [c for c in cmds if c not in bash]
    assert len(bash) == 5
    assert len(others) == 2 and all("check-ignore" in c for c in others), others   # was 15: versions, config, ...
    assert parses == []                                  # was ~5000 SKILL.md reads and parses for one turn


# ---------------------------------------------------------------- one headless renderer for view_output, ui_audit, ui_smoke
def test_renders_share_one_headless_browser_and_it_closes_when_idle(monkeypatch):
    from meshpool_harness import ui_audit as UA
    from meshpool_harness.agent import browser_mcp_server as BM

    class Ctx:
        def __init__(self, owner):
            self.owner = owner

        def close(self):
            self.owner.contexts.remove(self)

    class FakeBrowser:
        def __init__(self):
            self.contexts, self.closed = [], False

        def is_connected(self):
            return not self.closed

        def new_context(self, **kw):
            c = Ctx(self)
            self.contexts.append(c)
            return c

        def close(self):
            self.closed = True

    started = []

    def launch():
        started.append(FakeBrowser())
        return started[-1]
    monkeypatch.setattr(BM, "RENDER_IDLE_S", 0.3)
    monkeypatch.setattr(BM, "B", BM.Browser())
    monkeypatch.setattr(UA, "launcher", lambda pw: launch)

    def one_render():                                   # what view.py, ui_audit and ui_smoke do with launch()
        b = BM._launcher()()
        b.new_context()
        b.close()
        return b
    for _ in range(3):
        BM.B.run(one_render)
    assert len(started) == 1                           # was one Chromium per call
    assert not started[0].closed and started[0].contexts == []   # each call's contexts are closed, the browser kept
    deadline = time.time() + 5
    while not started[0].closed and time.time() < deadline:
        time.sleep(0.05)
    assert started[0].closed and BM.B.render is None    # closed on the browser thread after the idle time
    BM.B.run(one_render)
    assert len(started) == 2                           # and started again on the next call
