"""Release code review (core): file writes keep links and permission bits, secrets stay owner-only, MCP servers on
Windows are ended with their children, sub-agents in an isolated copy keep office output in the copy, plugin hooks
wait for the plugin to be switched on, non-ASCII skill names, connector lock order, atomic Undo."""
from __future__ import annotations

import errno
import io
import json
import os
import stat
import threading

import pytest

from meshpool_harness import fsio

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX links and permission bits")


@pytest.fixture
def umask022():
    old = os.umask(0o022)
    fsio._UMASK = None
    try:
        yield
    finally:
        os.umask(old)
        fsio._UMASK = None


# ---------- 4: links survive an atomic write -------------------------------------------------------------------------
@POSIX
def test_write_through_a_symlink_keeps_the_link_and_updates_its_target(tmp_path):
    from meshpool_harness.core import tools as T
    (tmp_path / "AGENTS.md").write_text("old\n")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    T.write_src(tmp_path / "CLAUDE.md", "new rules\n")
    assert (tmp_path / "CLAUDE.md").is_symlink() and os.readlink(tmp_path / "CLAUDE.md") == "AGENTS.md"
    assert (tmp_path / "AGENTS.md").read_text() == "new rules\n"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


@POSIX
def test_write_to_a_hard_linked_file_keeps_every_name_on_one_file(tmp_path):
    from meshpool_harness.core import tools as T
    (tmp_path / "AGENTS.md").write_text("old\n")
    os.link(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md")
    T.write_src(tmp_path / "CLAUDE.md", "shared\n")
    assert (tmp_path / "AGENTS.md").read_text() == "shared\n"
    assert os.stat(tmp_path / "AGENTS.md").st_ino == os.stat(tmp_path / "CLAUDE.md").st_ino
    assert os.stat(tmp_path / "CLAUDE.md").st_nlink == 2


# ---------- 5: new files get the umask default, secrets stay 0600 ----------------------------------------------------
@POSIX
def test_new_file_from_write_src_gets_the_umask_default_not_0600(tmp_path, umask022):
    from meshpool_harness.core import tools as T
    T.write_src(tmp_path / "new.py", "x = 1\n")
    assert stat.S_IMODE(os.stat(tmp_path / "new.py").st_mode) == 0o644
    fsio.atomic_write_text(tmp_path / "plain.txt", "x")
    assert stat.S_IMODE(os.stat(tmp_path / "plain.txt").st_mode) == 0o644


@POSIX
def test_existing_bits_are_kept_and_private_files_stay_owner_only(tmp_path, umask022):
    p = tmp_path / "group.txt"
    p.write_text("a")
    os.chmod(p, 0o640)
    fsio.atomic_write_text(p, "b")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640
    s = tmp_path / "secret.json"
    fsio.atomic_write_json(s, {"k": 1}, private=True)
    assert stat.S_IMODE(os.stat(s).st_mode) == 0o600
    os.chmod(s, 0o644)                                       # an older version left it readable
    fsio.update_json(s, lambda d: {**d, "k": 2}, private=True)
    assert stat.S_IMODE(os.stat(s).st_mode) == 0o600


@POSIX
def test_a_private_write_is_never_readable_by_others_even_for_a_moment(tmp_path, monkeypatch, umask022):
    """Every temporary file a private write makes is 0600 from its first byte (checked at the rename)."""
    seen = []
    real = fsio._replace

    def spy(src, dst):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))
        real(src, dst)
    monkeypatch.setattr(fsio, "_replace", spy)
    fsio.atomic_write_text(tmp_path / "k.env", "KEY=1\n", private=True)
    assert seen == [0o600]


def test_umask_is_read_once_without_changing_it(monkeypatch, umask022):
    calls = []
    real = os.umask
    monkeypatch.setattr(os, "umask", lambda m: calls.append(m) or real(m))
    fsio._UMASK = None
    first = fsio.default_mode()
    assert first == fsio.default_mode()
    if os.path.exists("/proc/self/status"):
        assert calls == []                                   # read from /proc: the process umask never moves
    assert len(calls) <= 2


@POSIX
def test_settings_with_keys_and_style_samples_are_owner_only(tmp_path, monkeypatch, umask022):
    from meshpool_harness import config as C
    seen = []
    real = fsio._replace

    def spy(src, dst):
        seen.append((os.path.basename(dst), stat.S_IMODE(os.stat(src).st_mode)))
        real(src, dst)
    monkeypatch.setattr(fsio, "_replace", spy)
    C._store(tmp_path / "meshpool.config", "MESHPOOL_API_KEY=sk-1\n")
    assert stat.S_IMODE(os.stat(tmp_path / "meshpool.config").st_mode) == 0o600
    assert seen == [("meshpool.config", 0o600)]              # the temporary file too, not chmod-ed afterwards


# ---------- 11: an old mcp.json with tokens becomes 0600 ------------------------------------------------------------
@POSIX
def test_old_world_readable_mcp_json_is_made_owner_only(tmp_path):
    from meshpool_harness.agent import mcp as M
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"gh": {"url": "https://x", "headers": {"Authorization": "Bearer t"}}}}))
    os.chmod(p, 0o644)
    assert "gh" in M.load_config(p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    os.chmod(p, 0o644)
    M.edit_config(lambda s: s.update(new={"command": "x"}), path=p)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


# ---------- 6: Windows ends the whole server tree while its leader still lives ---------------------------------------
def test_windows_close_runs_taskkill_tree_before_terminate(monkeypatch):
    from meshpool_harness.agent import mcp as M
    from meshpool_harness.core import tools as T
    order = []

    class P:
        pid = 4242
        stdin = stdout = stderr = io.BytesIO()
        alive = True

        def poll(self):
            return None if self.alive else 1

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            order.append("terminate")
            self.alive = False                   # the leader dies; npx/uvx children would survive it

        def kill(self):
            order.append("kill")

    def taskkill(p):
        order.append(("taskkill /T", p.poll() is None))
    monkeypatch.setattr(T, "_kill_tree", taskkill)
    s = M._Stdio.__new__(M._Stdio)
    s.p, s.grace, s.closing = P(), 0, False
    s._rt = threading.Thread(target=lambda: None)
    s._rt.start()
    s.err = io.BytesIO()
    monkeypatch.setattr(M.os, "name", "nt")
    try:
        s.close()
    finally:
        monkeypatch.setattr(M.os, "name", "posix" if os.sep == "/" else "nt")
    assert order and order[0] == ("taskkill /T", True)      # while the leader is alive, before terminate()
    assert order.index("terminate") > 0


# ---------- 9: a sub-agent in an isolated copy gets office output in the copy ----------------------------------------
def test_child_in_an_isolated_copy_sends_the_copy_as_the_office_project(tmp_path, monkeypatch):
    from meshpool_harness.agent import core as K
    from meshpool_harness.agent import delegate as D
    from meshpool_harness.agent import mcp as M
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("MH_SUBAGENT_ACCESS", raising=False)
    proj, copy = tmp_path / "proj", tmp_path / "copy"
    proj.mkdir()
    copy.mkdir()
    sent = []
    s = M.Server("meshpool-office", {"command": "x", "per_call_project": True}, tmp_path, [str(proj)])
    s.status = "connected"
    s._rpc = lambda method, params, timeout: sent.append(params["arguments"]) or {"content": []}
    m = M.Manager.__new__(M.Manager)
    m.servers, m.map, m.last_images = {"meshpool-office": s}, {
        "mcp__meshpool_office__make_document": ("meshpool-office", "make_document")}, []
    parent = K.Agent(str(proj), mode="auto")
    parent.mcp = m
    deleg = D.Delegation(parent, D.check_goals([{"goal": "Write the memo"}]), dict(D.settings(), access="inherit"), 0)
    rec = {"trace": [], "files": set(), "errors": [], "usage": {"in": 0, "cached": 0, "out": 0, "calls": 0},
           "wt": {"id": "x", "workdir": str(copy)}}
    child = deleg._child("sa-1", 0, rec)
    child.mcp.call("mcp__meshpool_office__make_document", {"markdown": "# hi", "filename": "memo"})
    assert sent[-1]["_project"] == str(copy.resolve())       # not the real project the server was started for
    D.ChildMcp(child.mcp).call("mcp__meshpool_office__make_document", {"markdown": "# nested"})
    assert sent[-1]["_project"] == str(copy.resolve())       # its own sub-agent, sharing the copy, too
    m.call("mcp__meshpool_office__make_document", {"markdown": "# hi"})
    assert sent[-1]["_project"] == str(proj.resolve())       # the chat itself is unchanged


def test_office_server_never_falls_back_to_its_own_folder_for_a_named_one(tmp_path, monkeypatch):
    from meshpool_harness.agent import office_mcp_server as O
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    O._CALL.project = str(tmp_path / "gone-copy")
    try:
        with pytest.raises(ValueError, match="gone"):
            O._project()
    finally:
        O._CALL.project = None
    assert O._project() == tmp_path


# ---------- 12: plugin hooks wait for the plugin; a dot-file hooks path is read ---------------------------------------
@pytest.fixture
def plug_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "home" / "mcp.json"))
    return tmp_path


def test_plugin_hooks_are_off_until_the_plugin_is_switched_on(plug_home):
    from meshpool_harness import plugins as PL
    d = plug_home / "src" / "guard"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "guard", "version": "1", "hooks": "./.meshpool-hooks.json"}))
    (d / ".meshpool-hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    PL.install_plugin(str(d))
    on, off = PL._hooks_file("guard"), PL._hooks_file("guard", off=True)
    assert off.is_file() and not on.exists()                 # ".meshpool-hooks.json" was found (lstrip ate the dot)
    PL.enable_plugin("guard", True)
    assert on.is_file() and not off.exists()
    assert json.loads(on.read_text())["PreToolUse"][0]["command"] == "echo hi"
    PL.enable_plugin("guard", False)
    assert off.is_file() and not on.exists()


# ---------- 14: non-ASCII names do not all become "skill" -------------------------------------------------------------
def test_non_ascii_skill_and_plugin_names_get_distinct_slugs(tmp_path):
    from meshpool_harness import plugins as PL
    from meshpool_harness import skills as SK
    a, b = SK.slug("日本語のスキル"), SK.slug("مهارة")
    assert a != b and a.startswith("skill-") and len(a) == len("skill-") + 8
    assert SK.slug(a) == a and SK.slug("Hello World") == "hello-world" and SK.slug("") == "skill"
    r1, r2 = tmp_path / "p1", tmp_path / "p2"
    r1.mkdir()
    r2.mkdir()
    assert PL._manifest(r1, {"name": "日本"})["name"] != PL._manifest(r2, {"name": "中文"})["name"]


# ---------- 15: connector files are always locked state-then-auth ----------------------------------------------------
def test_connector_locks_are_taken_state_then_auth_and_a_busy_lock_is_a_value_error(tmp_path, monkeypatch):
    from meshpool_harness import mcp_apps as MA
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    order = []
    real = fsio.file_lock

    def spy(p, **kw):
        order.append(os.path.basename(str(p)))
        return real(p, **kw)
    monkeypatch.setattr(MA.fsio, "file_lock", spy)
    with MA._locked(MA._auth_file(), MA._state_file()):
        pass
    assert order == [MA._state_file().name, MA._auth_file().name]
    src = open(MA.__file__, encoding="utf-8").read()
    assert "_locked(_auth_file(), _state_file())" not in src  # finish() used the opposite order

    def busy(p, **kw):
        raise TimeoutError("lock busy")
    monkeypatch.setattr(MA.fsio, "file_lock", busy)
    with pytest.raises(ValueError, match="busy"):
        with MA._locked(MA._state_file(), MA._auth_file()):
            pass


# ---------- 13: Undo writes each file atomically ----------------------------------------------------------------------
def test_undo_never_leaves_a_file_cut_short_when_the_disk_fills(tmp_path, monkeypatch):
    from meshpool_harness.checkpoints import Checkpoints
    root = tmp_path / "proj"
    root.mkdir()
    old = "".join(f"line {i}\n" for i in range(2000))
    (root / "calc.py").write_text(old)
    cp = Checkpoints(str(root), tmp_path / "store")
    cp.begin_turn("agent edits")
    (root / "calc.py").write_text("agent version\n")
    cp.end_turn()
    real_open = io.open
    hit = {"n": 0}

    class Full:
        def __init__(self, f):
            self.f = f

        def write(self, b):                                  # half the bytes reach the disk, then it is full
            self.f.write(b[: len(b) // 2])
            self.f.flush()
            raise OSError(errno.ENOSPC, "No space left on device")

        def __getattr__(self, k):
            return getattr(self.f, k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    def fake_open(file, mode="r", *a, **kw):
        f = real_open(file, mode, *a, **kw)
        target = isinstance(file, int) or str(root) in os.fspath(file)
        if "w" in mode and target and not hit["n"]:
            hit["n"] += 1
            return Full(f)
        return f
    monkeypatch.setattr(io, "open", fake_open)
    try:
        cp.revert_to_before(1)
    except OSError:
        pass
    monkeypatch.setattr(io, "open", real_open)
    assert hit["n"] == 1
    assert (root / "calc.py").read_text() in (old, "agent version\n")     # all of one, never half of the old file
    assert not [p for p in root.iterdir() if p.name.endswith(".tmp")]
