"""Regression tests for the top-level module fixes (scheduler job, settings/tasks/memory safety, first message, TOML)."""
import json
import subprocess
import sys

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import config as C
from meshpool_harness import scheduler_os as SO
from tests.summaries_fixtures import chat, setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    C.ensure_home()
    return tmp_path / "home"


def _fake(calls, crontab=""):
    def run(cmd, **kw):
        calls.append((cmd, kw.get("input")))
        return subprocess.CompletedProcess(cmd, 0, stdout=crontab if cmd[:2] == ["crontab", "-l"] else "", stderr="")
    return run


def test_cron_line_sets_pythonpath_home_and_cwd(home):
    calls = []
    SO.install(run=_fake(calls), platform="linux")
    line = [i for c, i in calls if c == ["crontab", "-"]][0]
    assert f"PYTHONPATH={C.APP}" in line and f"MESHPOOL_HOME={home}" in line and line.startswith("*/10")
    assert "cd " in line and "run-due" in line


def test_launchd_plist_has_environment_and_workdir(home, tmp_path):
    SO.install(run=_fake([]), platform="darwin", home=tmp_path)
    text = (tmp_path / "Library/LaunchAgents/ai.meshpool.scheduler.plist").read_text()
    assert "EnvironmentVariables" in text and "PYTHONPATH" in text and "MESHPOOL_HOME" in text and "WorkingDirectory" in text


def test_windows_task_runs_a_hidden_wrapper_with_env(home):
    calls = []
    SO.install(run=_fake(calls), platform="win32")
    tr = calls[0][0][calls[0][0].index("/TR") + 1]
    assert "wscript" in tr and "meshpool-scheduler.vbs" in tr
    vb = (home / "meshpool-scheduler.vbs").read_text()
    assert "PYTHONPATH" in vb and "MESHPOOL_HOME" in vb and "run-due" in vb and ", 0, True" in vb
    SO.uninstall(run=_fake([]), platform="win32")
    assert not (home / "meshpool-scheduler.vbs").exists()


def test_missing_crontab_is_a_runtime_error_and_installed_is_false():
    def boom(cmd, **kw):
        raise FileNotFoundError("crontab")
    with pytest.raises(RuntimeError):
        SO.install(run=boom, platform="linux")
    assert SO.installed(run=boom, platform="linux") is False

    def bad(cmd, **kw):
        raise UnicodeDecodeError("cp1252", b"\x81", 0, 1, "undefined")
    assert SO.installed(run=bad, platform="win32") is False


def test_run_decodes_with_replacement():
    r = SO._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\x81\\xffb')"])
    assert r.stdout.startswith("a") and r.stdout.endswith("b")


def test_bad_settings_file_does_not_crash(home):
    (home / "memory").mkdir(exist_ok=True)
    (home / "memory" / "settings.json").write_text("{bad", encoding="utf-8")
    assert AS.settings()["memory_on"] is True
    (home / "memory" / "settings.json").write_text("[1]", encoding="utf-8")
    assert AS.settings()["memory_on"] is True
    assert AS.save_settings(memory_on=False)["memory_on"] is False


def test_corrupt_schedules_are_kept_not_wiped(home):
    (home / "schedules.json").write_text("{corrupt", encoding="utf-8")
    AS.add_task("n", "p", when="hourly")
    assert (home / "schedules.json.bak").read_text() == "{corrupt"
    assert len(AS.tasks()) == 1


def test_unreadable_schedules_are_not_overwritten(home, monkeypatch):
    t = AS.add_task("keep", "p", when="hourly")
    real = type(home / "x").read_text

    def deny(self, *a, **k):
        if self.name == "schedules.json":
            raise PermissionError("locked")
        return real(self, *a, **k)
    with monkeypatch.context() as m:
        m.setattr(type(home / "x"), "read_text", deny)
        with pytest.raises(RuntimeError):
            AS.add_task("other", "p", when="hourly")
        assert AS.due() == []
    assert [x["id"] for x in AS.tasks()] == [t["id"]]


def test_memory_write_does_not_wipe_on_unreadable_file(home, monkeypatch):
    from meshpool_harness import memstore as M
    M.add("I like tea", scope="everywhere")
    before = M.facts_path().read_text(encoding="utf-8")
    real = type(home).read_text

    def deny(self, *a, **k):
        if self.name == "facts.jsonl":
            raise PermissionError("locked")
        return real(self, *a, **k)
    with monkeypatch.context() as m:
        m.setattr(type(home), "read_text", deny)
        with pytest.raises(OSError):
            M._load_rw()
    assert M.facts_path().read_text(encoding="utf-8") == before


def test_memory_keeps_backup_of_unparseable_line(home):
    from meshpool_harness import memstore as M
    M.ensure()
    p = M.facts_path()
    p.write_text(p.read_text(encoding="utf-8") + "{trunc\n", encoding="utf-8")
    M._load_rw()
    assert p.with_name(p.name + ".bak").exists()


def test_single_user_message_chat_has_a_digest_and_counts(tmp_path, monkeypatch):
    from meshpool_harness import summaries as S
    from meshpool_harness import yourwork as YW
    from meshpool_harness.ui import agent_api as A
    setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MH_LEARN", "0")
    chat(tmp_path, "c1", ["Make a chart of sales please."], ["Done."])
    assert S.chat_digest("c1") is not None
    assert S.summarise_chat("c1").startswith("Make a chart")
    # a legacy save that still starts with a system message is skipped, the first user message is kept
    f = A.SESSIONS / "c1" / "session.json"
    d = json.loads(f.read_text())
    d["messages"].insert(0, {"role": "system", "content": "sys"})
    f.write_text(json.dumps(d))
    assert S.chat_digest("c1") is not None
    assert S._nosys(d["messages"])[0]["role"] == "user" and YW._nosys(d["messages"])[0]["role"] == "user"


def test_toml_fallback_parses_codex_servers():
    from meshpool_harness.importer import _mini_toml, toml_loads
    t = ('model = "x"\n[mcp_servers.fs]\ncommand = "npx"\nargs = ["-y", \'c\']\nenv = { KEY = "v" }\non = true\n'
         '[mcp_servers."my srv"]\nurl = "http://x"\nargs = [\n "a",\n "b",\n]\n')
    want = {"model": "x", "mcp_servers": {"fs": {"command": "npx", "args": ["-y", "c"], "env": {"KEY": "v"}, "on": True},
                                         "my srv": {"url": "http://x", "args": ["a", "b"]}}}
    assert _mini_toml(t) == want
    assert toml_loads(t) == want
    with pytest.raises(ValueError):
        _mini_toml("a = @@\n")
