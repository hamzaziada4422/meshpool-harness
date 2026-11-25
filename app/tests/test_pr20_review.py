"""Fixes for the reviews of PR 20: the order of the permission checks, wrapped commands, worktrees in use, the Office
panel's reach, unknown saved sub-agents and the mode of a branched chat. Each test is one finding, with its attack."""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from meshpool_harness import worktrees as WT
from meshpool_harness.agent import core

PUSH = "git " + "push"                       # spelled apart: some tools refuse a literal push in a command line


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    for k in ("MH_TRUST_PROJECT", "MH_ALLOW", "MH_DENY", "MH_ASK", "MH_WORKTREE_KEEP"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "home").mkdir()
    (tmp_path / "proj").mkdir()
    return tmp_path


def _yes_agent(root, asked=None, mode="ask"):
    return core.Agent(str(root), approve=lambda *x: ((asked.append(x) if asked is not None else None),
                                                       ("yes", ""))[1], mode=mode)


# 1. a command that reaches the main checkout: a yes to that question never gets it past a deny rule
def test_main_checkout_question_never_overrides_a_deny_rule(env, monkeypatch):
    monkeypatch.setenv("MH_DENY", f"bash:{PUSH}*")
    main = env / "repo"
    wt = main / ".meshpool" / "worktrees" / "w1"
    wt.mkdir(parents=True)
    asked = []
    a = _yes_agent(wt, asked)
    ok, why = a._permit("bash", {"command": f"git -C {main} push origin main"})
    assert not ok and "Not allowed by your saved rule" in why and not asked
    assert a._permit("bash", {"command": f"git -C {main} status"})[0] and asked     # the guard still asks


# 2. a command that names a power file: a yes to that question never gets it past a deny rule
def test_power_file_question_never_overrides_a_deny_rule(env, monkeypatch):
    monkeypatch.setenv("MH_DENY", f"bash:{PUSH}*")
    asked = []
    a = _yes_agent(env / "proj", asked)
    ok, why = a._permit("bash", {"command": f"cat .mcp.json; {PUSH}"})
    assert not ok and "Not allowed by your saved rule" in why and not asked
    assert a._permit("bash", {"command": "cat .mcp.json"})[0] and asked


# 3. wrappers with options and values, and sh -c scripts, no longer hide a command from a deny rule
@pytest.mark.parametrize("cmd", [
    f"sudo -u root {PUSH}", f"sudo -nu root {PUSH}", f"sudo --user=root {PUSH}", f"env -i {PUSH}",
    f"env -u HOME FOO=1 {PUSH}", f"nice -n 5 {PUSH}", f"nice -5 {PUSH}", f"time -p {PUSH}", f"timeout 5 {PUSH}",
    f'bash -c "{PUSH}"', f"sh -c '{PUSH} origin'", f"bash -lc 'cd x; {PUSH}'", f"/bin/zsh -c \"{PUSH} -f\"",
    f'env -S "{PUSH}"', f"sudo --some-new-option x {PUSH}", f"cd x && sudo -E -u root {PUSH}",
    "git -C /tmp -c a=b --no-pager pu" + "sh origin",
])
def test_deny_rules_see_through_wrappers_and_shell_scripts(env, monkeypatch, cmd):
    monkeypatch.setenv("MH_DENY", f"bash:{PUSH}*")
    a = _yes_agent(env / "proj", mode="auto")
    ok, why = a._permit("bash", {"command": cmd})
    assert not ok and "Not allowed by your saved rule" in why, cmd


def test_wrapped_commands_still_read_as_themselves():
    assert core._command_parts("sudo -u root make test") == ["make test"]
    assert core._command_parts("env -i FOO=1 ls -la") == ["ls -la"]
    assert core._command_parts("ls -la") == ["ls -la"]


# 4. a PreToolUse hook's "allow" comes after the deny rules and the guards
def test_a_hook_allow_never_skips_deny_rules_or_guards(env, monkeypatch):
    from meshpool_harness.agent import extras as EX
    monkeypatch.setattr(EX, "run_hooks_full", lambda *a, **k: {"blocked": False, "allow": True, "message": ""})
    monkeypatch.setenv("MH_DENY", f"bash:{PUSH}*")
    asked = []
    a = core.Agent(str(env / "proj"), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="ask")
    assert not a._permit("bash", {"command": f"{PUSH} origin"})[0]
    assert not a._permit("write_file", {"path": ".meshpool/hooks.json", "content": "{}"})[0] and asked
    assert a._permit("bash", {"command": "ls"})[0]                        # the hook still allows the rest


# 5. prune never removes a worktree a chat or sub-agent still works in
def _git(*a, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(env):
    r = env / "repo"
    r.mkdir()
    _git("init", "-q", cwd=r)
    (r / "a.txt").write_text("a")
    _git("add", "-A", cwd=r)
    _git("commit", "-qm", "init", cwd=r)
    return r


def test_prune_never_removes_a_worktree_in_use(env, repo, monkeypatch):
    import gc
    monkeypatch.setenv("MH_WORKTREE_KEEP", "1")
    sa = WT.create(repo, "sa")
    os.utime(sa["path"], (time.time() - 100, time.time() - 100))
    agent = core.Agent(sa["path"], approve=lambda *x: ("no", ""), mode="ask")   # a chat works in sa
    assert WT.in_use(sa["path"])
    sb = WT.create(repo, "sb")
    assert Path(sa["path"]).is_dir() and "sa" not in sb["pruned"]
    del agent
    gc.collect()                                                          # the chat is gone: sa may go now
    assert not WT.in_use(sa["path"])
    assert WT.prune(repo, 1) == ["sa"]


def test_a_worktree_held_by_another_live_process_is_kept(env, repo):
    w = WT.create(repo, "other")
    adm = WT._admin(Path(w["path"]).resolve())
    p = subprocess.Popen(["sleep", "30"])
    try:
        (adm / f"meshpool-in-use-{p.pid}").write_text("x")
        assert WT.in_use(w["path"])
    finally:
        p.kill()
        p.wait()
    assert not WT.in_use(w["path"])                                       # it ended: the marker is stale
    assert not list(adm.glob("meshpool-in-use-*"))


# 6. the Office panel's token reaches only the chats the panel started
def test_the_office_token_reaches_only_the_panels_own_chats(monkeypatch):
    import io
    from meshpool_harness.ui import server as S
    monkeypatch.setattr(S, "OFFICE_CHATS", set())
    sent = []

    def handler(method, path, tok):
        h = S.OfficeHandler.__new__(S.OfficeHandler)
        h.command, h.path = method, path
        h.headers = _Headers({"Host": "localhost", "X-Meshpool-Token": tok})
        h._send = lambda code, body, *a, **k: sent.append(code)
        return h
    assert not handler("GET", "/api/agent/someone-elses-chat", S.OFFICE_TOKEN)._gate() and sent[-1] == 403
    assert not handler("POST", "/api/agent/someone-elses-chat/send", S.OFFICE_TOKEN)._gate() and sent[-1] == 403
    assert handler("POST", "/api/agent", S.OFFICE_TOKEN)._gate()          # it may start a chat of its own
    # the reply to that start records the chat; from then on the panel reaches it
    h = S.OfficeHandler.__new__(S.OfficeHandler)
    h.command, h.path = "POST", "/api/agent"
    h.wfile = io.BytesIO()
    h.send_response = h.send_header = h.end_headers = lambda *a, **k: None
    h._send(200, {"id": "panel-chat", "state": "idle"})
    assert "panel-chat" in S.OFFICE_CHATS
    assert handler("GET", "/api/agent/panel-chat", S.OFFICE_TOKEN)._gate()
    assert handler("POST", "/api/agent/panel-chat/stop", S.OFFICE_TOKEN)._gate()


class _Headers(dict):
    def replace_header(self, k, v):
        self[k] = v


# 7a. a saved sub-agent name that is not there never runs as a general sub-agent with every tool
def test_an_unknown_saved_sub_agent_never_gets_the_full_tool_set(env, monkeypatch):
    from meshpool_harness.agent import delegate as D
    monkeypatch.setenv("MH_RUNS_DIR", str(env / "runs"))
    a = core.Agent(str(env / "proj"), mode="auto")
    rows = D.check_goals([{"goal": "Review calc.py for bugs", "agent": "ghost"}])
    d = D.Delegation(a, rows, D.settings(), 0, {})
    with pytest.raises(RuntimeError, match="no saved sub-agent named ghost"):
        d._child("sub-1", 0, {"trace": [], "errors": [], "usage": {}})


# 7b. a branch of a chat keeps the chat's permission mode
def test_a_branch_keeps_the_chats_mode(monkeypatch):
    from meshpool_harness.ui import agent_api as A

    class Src:
        lock = threading.Lock()
        events = [{"type": "user", "text": "hi"}, {"type": "assistant", "text": "hello"}]
        state, workdir, kind, mode = "idle", "/tmp", "code", "ask"
        harness = {"id": "meshpool-code"}
        agent = type("Ag", (), {"mode": "ask"})()
    got = []

    class Done(Exception):
        pass

    def create(body):
        got.append(body)
        raise Done
    monkeypatch.setattr(A, "get", lambda sid: Src())
    monkeypatch.setattr(A, "read_meta", lambda sid: {})
    monkeypatch.setattr(A, "create", create)
    with pytest.raises(Done):
        A.fork("x")
    assert got[0]["mode"] == "ask"


# ---------------------------------------------------------------- second review (code-review of pr20-fixes)
# R1. options before -c (long ones, -o/-O values, --rcfile) and $'...' quoting
@pytest.mark.parametrize("cmd", [
    f'bash --norc -c "{PUSH} --force"', f"bash --login -c '{PUSH}'", f'bash -o pipefail -c "{PUSH}"',
    f"bash -O extglob -c '{PUSH}'", f"bash --rcfile F -c '{PUSH}'", f"bash -lc $'{PUSH}'",
    f"bash --noprofile --norc -o errexit -lc $'cd x\\n{PUSH}'",
])
def test_deny_rules_read_shell_scripts_after_any_options(env, monkeypatch, cmd):
    monkeypatch.setenv("MH_DENY", f"bash:{PUSH}*")
    a = _yes_agent(env / "proj", mode="auto")
    assert not a._permit("bash", {"command": cmd})[0], cmd
    assert not a._permit("shell", {"command": ["bash", "--norc", "-c", PUSH]})[0]


# R2. a path or backslash on the command word, and more wrappers
@pytest.mark.parametrize("cmd", [
    f"/usr/bin/{PUSH} --force", "\\" + PUSH, f"watch {PUSH}", f"strace -f -o x {PUSH}", f"ltrace {PUSH}",
    f"flock /tmp/l {PUSH}", f"flock /tmp/l -c '{PUSH}'", f"chroot / {PUSH}", f"unshare -r {PUSH}",
    f"nsenter -t 1 -m {PUSH}", f"setsid {PUSH}", f"script -q -c '{PUSH}' /dev/null", f"parallel {PUSH} ::: a",
    f"find . -exec {PUSH} \\;", f"find . -execdir {PUSH} {{}} +",
])
def test_deny_rules_see_command_paths_and_more_wrappers(env, monkeypatch, cmd):
    monkeypatch.setenv("MH_DENY", f"bash:{PUSH}*")
    a = _yes_agent(env / "proj", mode="auto")
    assert not a._permit("bash", {"command": cmd})[0], cmd


def _wt_agent(env, asked):
    main = env / "repo"
    wt = main / ".meshpool" / "worktrees" / "w1"
    (wt / "src").mkdir(parents=True)
    a = core.Agent(str(wt), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="auto")
    return a, main, wt


# R3. the shell tool's workdir counts: outside the worktree it reaches the main checkout
def test_shell_workdir_outside_the_worktree_asks(env):
    asked = []
    a, main, wt = _wt_agent(env, asked)
    assert not a._permit("shell", {"command": ["git", "reset", "--hard"], "workdir": "../../.."})[0] and asked
    assert not a._permit("shell", {"command": ["ls"], "workdir": str(main)})[0]
    assert a._permit("shell", {"command": ["ls"], "workdir": "src"})[0]           # inside the worktree: fine


# R4. git -C after other global options, and cd ../../.. without a trailing slash
@pytest.mark.parametrize("cmd", [
    "git --no-pager -C ../../.. reset --hard", "git -c x=y -C ../../.. reset --hard",
    "git -c x=y --git-dir=../../../.git log", "cd ../../.. && git reset --hard", "cd ../../..; ls",
    "cd ..; cd ..; cd ..; git reset --hard", "cd ../.. && cd .. && rm -rf a.txt", "sudo git -p -C .. status",
])
def test_main_checkout_guard_sees_every_spelling(env, cmd):
    asked = []
    a, main, wt = _wt_agent(env, asked)
    assert not a._permit("bash", {"command": cmd})[0] and asked, cmd


def test_main_checkout_guard_leaves_the_worktree_alone(env):
    asked = []
    a, main, wt = _wt_agent(env, asked)
    (env / "elsewhere").mkdir()
    for cmd in ("cd src && ls", "cd src; cd .. && ls", f"cd {env / 'elsewhere'} && ls", "git status", "git log -1"):
        assert a._permit("bash", {"command": cmd})[0], cmd


# R5. a GC release racing a new hold never deletes the new holder's marker
def test_a_release_racing_a_hold_keeps_the_new_holders_marker(env, repo, monkeypatch):
    w = WT.create(repo, "race")
    top = Path(w["path"]).resolve()
    marker = WT._admin(top) / f"meshpool-in-use-{os.getpid()}"
    WT.hold(top)
    real, fired = WT._admin, []

    def admin(t):                                     # while the release runs, another thread starts a chat there
        if not fired:
            fired.append(1)
            th = threading.Thread(target=WT.hold, args=(top,))
            th.start()
            th.join(2)
        return real(t)
    monkeypatch.setattr(WT, "_admin", admin)
    WT.release(top)
    monkeypatch.setattr(WT, "_admin", real)
    assert WT._HELD.get(str(top)) == 1 and marker.exists()                  # a CLI prune still sees it in use
    WT.release(top)
    assert not marker.exists()


# R6. the approval prompt shows the shell tool's script and workdir, not a list
def test_approval_prompts_show_the_shell_script_and_workdir(env, monkeypatch):
    monkeypatch.setenv("MH_ASK", f"bash:{PUSH}*")
    asked = []
    a = core.Agent(str(env / "proj"), approve=lambda *x: (asked.append(x), ("no", ""))[1], mode="auto")
    a._permit("shell", {"command": ["bash", "-lc", PUSH], "workdir": "sub"})
    assert asked[-1][2] == f"cd sub && {PUSH}"
    monkeypatch.delenv("MH_ASK")
    asked2 = []
    b, main, wt = _wt_agent(env, asked2)
    b._permit("shell", {"command": ["git", "reset", "--hard"], "workdir": "../../.."})
    assert asked2[-1][2] == "cd ../../.. && git reset --hard"


# R7. a rename only in the worktree (" R new\0old\0") is one file, not two
def test_git_status_reads_worktree_renames(env, monkeypatch):
    from meshpool_harness.ui import workbench as W
    r = env / "gs"
    r.mkdir()
    _git("init", "-q", cwd=r)
    (r / "old.txt").write_text("same content here\n" * 5)
    _git("add", "-A", cwd=r)
    _git("commit", "-qm", "i", cwd=r)
    (r / "old.txt").rename(r / "new.txt")
    _git("add", "-N", "new.txt", cwd=r)
    raw = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=r, capture_output=True, text=True).stdout
    if not raw.startswith(" R"):
        pytest.skip("this git does not report worktree renames")
    monkeypatch.setattr(W, "check_root", lambda x: Path(x))
    st = W.git_status(str(r))
    assert [f["path"] for f in st["files"]] == ["new.txt"]


# R8. a deleted chat is no longer reachable with the panel's token
def test_a_deleted_chat_drops_out_of_the_office_list(monkeypatch, tmp_path):
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui import server as S
    monkeypatch.setattr(S, "OFFICE_CHATS", {"gone"})
    monkeypatch.setattr(A, "DELETED", set())
    monkeypatch.setattr(A, "read_meta", lambda sid: {"x": 1})
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.thread_action("gone", {"action": "delete"})
    assert "gone" not in S.OFFICE_CHATS
    S.OFFICE_CHATS.add("gone")                         # a stale entry is refused at the gate too
    sent = []
    h = S.OfficeHandler.__new__(S.OfficeHandler)
    h.command, h.path = "GET", "/api/agent/gone"
    h.headers = _Headers({"Host": "localhost", "X-Meshpool-Token": S.OFFICE_TOKEN})
    h._send = lambda code, body, *a, **k: sent.append(code)
    assert not h._gate() and sent[-1] == 403 and "gone" not in S.OFFICE_CHATS
