"""Omar (developer persona) fixes, part 3: the quoted apply_patch form, verification after reads, the slot cap,
mode names, a JSON tool call as the final text."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.hrouter import profiles as PROF  # noqa: E402
from meshpool_harness.hrouter.route import Route  # noqa: E402
from meshpool_harness.core import tools as T  # noqa: E402

PATCH = ("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = \"two\"\n*** End Patch")


def _quoted(patch=PATCH):
    return 'apply_patch "' + patch.replace("\\", "\\\\").replace('"', '\\"') + '"'


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    return T.Workspace(tmp_path, tmp_path / ".run")


def test_3_the_quoted_form_is_a_patch_not_a_command():
    hit = T.patch_from_shell(_quoted())
    assert hit and hit[0] == PATCH and hit[1] is None
    single = "apply_patch '" + PATCH + "'"
    assert T.patch_from_shell(single)[0] == PATCH
    assert T.patch_from_shell("cd sub && " + _quoted())[1] == "sub"


def test_3_the_quoted_form_with_commands_after_it_splits():
    sp = T.split_patch_script(_quoted() + "\npython -m pytest -q")
    assert sp and sp[0] == PATCH and sp[2] == "python -m pytest -q" and sp[3] is False
    sp = T.split_patch_script(_quoted() + " && python -m pytest -q")
    assert sp and sp[2] == "python -m pytest -q" and sp[3] is True


def test_3_other_quoted_text_and_piped_forms_are_not_patches():
    assert T.patch_from_shell('apply_patch "not a patch"') is None
    assert T.patch_from_shell(_quoted() + " | tee out.txt") is None
    assert T.split_patch_script(_quoted() + " | tee out.txt") is None
    assert T.patch_from_shell('echo "*** Begin Patch"') is None


def test_3_exec_command_applies_the_quoted_patch(ws, tmp_path):
    out = ws.exec_command(_quoted())
    assert "command not found" not in out
    assert (tmp_path / "a.py").read_text() == 'x = "two"\n'


def test_3_the_step_row_reads_as_an_edit_not_the_whole_patch():
    row = core._patch_step(_quoted() + "\npython -m pytest -q")
    assert row.startswith("Edit a.py") and "Begin Patch" not in row and len(row) < 80
    assert core._patch_step(_quoted()) == "Edit a.py"


# ---------------------------------------------------------------------------------------- 4: verification
@pytest.mark.parametrize("cmd,same", [
    ("git status --short", True),
    ("git diff", True),
    ("git log --oneline -3", True),
    ("git stash -q && python -m pytest -q 2>&1 | tail -4; git stash pop -q && git status --short", True),
    ("git stash && git stash pop", True),
    ("git stash", False),                                   # shelved with no pop: the files are not as they were
    ("git stash pop", False),
    ("sed -i s/a/b/ a.py", False),
    ("git checkout -- a.py", False),
])
def test_4_reversible_and_read_only_commands_do_not_change_the_files(cmd, same):
    assert core._leaves_tree_as_it_was(cmd) is same


def test_4_a_stash_round_trip_after_a_passing_test_run_keeps_the_receipt_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")
    p = tmp_path / "work"
    p.mkdir()
    run = lambda *c: subprocess.run(["git", "-C", str(p), *c], check=True, capture_output=True)   # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (p / "a.py").write_text("x = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "first")
    a = Agent(str(p), mode="auto")
    a.set_route(Route(spec="code.debug", family="code", sub="debug", base="code", profile="c171",
                      settings=PROF.settings("c171")), "fix it")           # the profile that has exec_command

    def call(name, args):
        return a._run_tool({"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}})
    a.ws.write_file("a.py", "x = 2\n")
    call("exec_command", {"cmd": "python -m pytest --version"})
    call("exec_command", {"cmd": "git stash -q && python -m pytest --version; git stash pop -q && git status --short"})
    assert (p / "a.py").read_text() == "x = 2\n"
    r = a.receipt()
    assert "not verified" not in r, r
    assert "passed" in r or "ran" in r


# ---------------------------------------------------------------------------------------- 7: one text per mode
def test_7_the_web_and_the_terminal_describe_each_mode_in_the_same_words():
    import re

    from meshpool_harness import config as C
    js = (Path(core.__file__).resolve().parents[1] / "ui" / "app.js").read_text(encoding="utf-8")
    line = next(x for x in js.splitlines() if x.startswith("const MODES = "))
    web = re.findall(r'(\w+): \["([^"]+)", "([^"]+)"\]', line)
    assert [w[0] for w in web] == ["ask", "edits", "auto"]
    for key, name, text in web:
        assert (name, text) == C.MODE_INFO[key], key
    helptext = next(f[3] for f in C.FIELDS if f[0] == "MH_AGENT_PERMISSIONS")
    assert all(C.MODE_INFO[k][1] in helptext for k in C.MODE_INFO)


def test_7_the_descriptions_say_what_each_mode_really_does():
    from meshpool_harness import config as C
    ask, edits = C.MODE_INFO["ask"][1], C.MODE_INFO["edits"][1]
    assert "sandbox" in ask and "asks first" in ask             # in the sandbox it does not ask; without, it does
    assert "never ask" in edits and "git" in edits and edits != ask       # no longer "almost the same" as Ask


def test_7_the_terminal_shows_the_same_text_in_status_and_first_run(monkeypatch):
    import io

    from meshpool_harness import config as C
    from meshpool_harness.agent import onboard as OB
    from meshpool_harness.agent import tui as T
    src = Path(OB.__file__).read_text(encoding="utf-8")
    assert "C.MODE_INFO" in src                                 # the first-run choice reads the shared words
    t = T.Terminal(out=io.StringIO(), interactive=False)
    t.mode = "edits"
    t.version, t.model, t.provider, t.cwd, t.windows = "x", "m", "p", "/w", {}
    sess = T.Session.__new__(T.Session)
    sess.term, sess.agent, sess.mcp = t, type("A", (), {"messages": [], "title": ""})(), None
    monkeypatch.setattr(T.Terminal, "refresh_facts", lambda self, a: None)
    sess.cmd_status("")
    text = " ".join(t.out.getvalue().split())
    assert "Accept edits" in text and C.MODE_INFO["edits"][1] in text


# ---------------------------------------------------------------------------------------- 5: the shared slot cap
def test_5_a_local_endpoint_takes_six_calls_and_the_setting_still_wins(monkeypatch):
    from meshpool_harness.core import gateway as GW
    assert GW.provider_cap("http://localhost:11434/v1") == 6
    assert GW.provider_cap("http://127.0.0.1:8080/v1") == 6
    assert GW.provider_cap("https://llm.meshpool.ai/v1") == 16
    assert GW.wait_line(2, 6) == "Waiting: other meshpool chats are using the model right now (2 of 6)."
    monkeypatch.setitem(GW._CFG, "v", "3")
    monkeypatch.setitem(GW._CFG, "t", 1e18)                 # the cached setting is fresh
    assert GW.health("http://localhost:11434/v1", "k-omar5").gate.limit == 3


def test_5_the_shared_wait_names_how_many_of_how_many_without_the_setting_name(tmp_path, monkeypatch):
    import threading
    import time
    from meshpool_harness.core import gateway as GW
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "slots"))
    monkeypatch.setitem(GW._CFG, "v", "2")
    monkeypatch.setitem(GW._CFG, "t", 1e18)
    h = GW.health("http://localhost:11999/v1", "k-omar5b")
    a = h.acquire(lambda: None)
    other = GW._Slots(h.gate.name).take(2, lambda: None, lambda: None)   # another process holds slot 1
    seen = []
    t = threading.Thread(target=lambda: seen.append(h.acquire(lambda: None, events.append)))
    events = []
    # the in-process gate has room (1 of 2), the shared folder does not: slot 0 is ours, slot 1 is held elsewhere
    t.start()
    time.sleep(0.8)
    texts = [e["text"] for e in events if e["type"] == "status"]
    GW._Slots.give(other)
    t.join(5)
    assert texts and all("MH_MAX_INFLIGHT" not in x and x.startswith("Waiting: other meshpool chats") for x in texts)
    for x in seen:
        h.release(True, x)
    h.release(True, a)
