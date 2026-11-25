"""The updater's persisted state machine (ws11-updater, M10-01 .. M10-10, M11-04, M12-02 back end).

No network, no hdiutil, no helper process: releases are dicts, downloads are served from memory, the Mac and Windows
helpers are recorded instead of started. updater.py's new interface (check(timeout=), prepare, apply_staged,
discard_stage, install_lock, UpdateBusy) is stubbed onto the module, because another change owns that file."""
import contextlib
import hashlib
import io
import json
import shutil
import time
from pathlib import Path

import pytest

from meshpool_harness import autoupdate as AU
from meshpool_harness import updater as U

OLD, NEW, NEWER = "v0.2.0", "v0.3.0", "v0.4.0"
PAYLOAD = b"installer bytes"


class Busy(Exception):
    pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    def _no_network(*a, **k):
        raise OSError("tests never reach the network")
    monkeypatch.setattr(U, "_get", _no_network)          # a test that needs releases stubs _get or check itself
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("MESHPOOL_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("MESHPOOL_NO_UPDATE", raising=False)
    monkeypatch.setattr(U.MH, "__version__", "0.1.0")
    AU._ST.clear()
    AU._ST["state"] = "idle"
    AU._CFG.update(started=False, busy=lambda: False)
    AU._CFG.pop("loaded_for", None)
    U._CACHE.clear()
    monkeypatch.setattr(U, "UpdateBusy", Busy, raising=False)
    monkeypatch.setattr(U, "install_lock", lambda: contextlib.nullcontext(), raising=False)
    monkeypatch.setattr(U, "discard_stage", lambda p: shutil.rmtree(str(p), ignore_errors=True), raising=False)
    monkeypatch.setattr(U, "prepare", lambda v, auto=True: pytest.fail("prepare was not expected"), raising=False)
    monkeypatch.setattr(U, "apply_staged", lambda st: pytest.fail("apply_staged was not expected"), raising=False)
    yield
    AU._ST.clear()
    AU._ST["state"] = "idle"
    AU._CFG.pop("loaded_for", None)


class _Resp:
    def __init__(self, data, start):
        self.status = 206 if start else 200
        self._b = io.BytesIO(data[start:])

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Env:
    """One fake world: the published releases, the sums, the download counter, the helper launches."""

    def __init__(self, monkeypatch, tmp_path):
        self.mp, self.tmp = monkeypatch, tmp_path
        self.tags = [OLD]
        self.newest = OLD
        self.newer = True
        self.pre = False
        self.kind = "win-app"
        self.payload = PAYLOAD
        self.sums = {"Meshpool-Setup.exe": hashlib.sha256(PAYLOAD).hexdigest()}
        self.fresh = None
        self.downloads = 0
        self.checks: list = []
        self.check_fails = False
        self.manifest = {"runtime_changed": True}
        self.launched: list = []
        app = tmp_path / "inst" / "app"
        (app.parent / "bin").mkdir(parents=True)
        (app.parent / "bin" / "meshpool.cmd").write_text("x")
        app.mkdir()
        monkeypatch.setattr(U, "APP", app)
        monkeypatch.setattr(U, "check", self._check, raising=False)
        monkeypatch.setattr(U, "_sums", lambda v: self.sums)
        monkeypatch.setattr(U, "_fresh_sums", lambda v: self.fresh if self.fresh is not None else self.sums)
        monkeypatch.setattr(U, "_asset", lambda v: v["assets"][0])
        monkeypatch.setattr(AU, "read_manifest", lambda v, s: self.manifest)
        monkeypatch.setattr(AU, "_open_range", self._open)
        monkeypatch.setattr(AU, "_launch_detached", self.launched.append)

    def _open(self, url, start):
        self.downloads += 1
        return _Resp(self.payload, start)

    def release(self, tag=None):
        tag = (tag or self.newest) if self.newer else ""        # the real check names no tag when nothing is newer
        return {"ok": True, "current": "0.1.0", "latest": tag.lstrip("v") or "0.1.0", "tag": tag, "newer": self.newer,
                "kind": self.kind, "url": "u", "prerelease": self.pre, "tags": list(self.tags),
                "checked_at": time.time(), "pending_restart": False,
                "assets": [{"name": "Meshpool-Setup.exe", "url": "d", "size": len(self.payload)},
                           {"name": U.SUMS, "url": "s"}]}

    def _check(self, force=False, timeout=None):
        self.checks.append((force, timeout))
        if self.check_fails:
            return {"ok": False, "current": "0.1.0", "error": "Could not reach GitHub to check for updates (OSError)."}
        v = self.release()
        U._CACHE.update(t=time.time(), v=v)
        return v

    def publish(self, tag):
        self.tags = [t for t in self.tags] + ([tag] if tag not in self.tags else [])
        self.newest = tag

    def make_ready(self, tag=OLD):
        self.publish(tag)
        AU.pipeline(self.release(tag), download=True)
        assert AU._ST["state"] == "ready", AU._ST
        return dict(AU._ST)


@pytest.fixture
def env(monkeypatch, tmp_path):
    return Env(monkeypatch, tmp_path)


def _state_file(tmp_path):
    return json.loads((tmp_path / "data" / "updates" / "state.json").read_text(encoding="utf-8"))


def _wait(cond, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ------------------------------------------------------------------ the table

def _entry(tmp_path, **kw):
    f = tmp_path / "data" / "updates" / OLD / "Meshpool-Setup.exe"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return dict(tag=OLD, latest="0.2.0", mode="win-installer", file=str(f), sha256="a" * 64, url="u", **kw)


def test_transition_table(tmp_path):
    assert set(AU.TRANSITIONS) == set(AU.STATES)
    for frm, tos in AU.TRANSITIONS.items():
        assert tos <= set(AU.STATES) and frm not in tos
        for to in tos:
            AU._ST.clear()
            AU._ST.update(state=frm, **_entry(tmp_path))
            AU._CFG.pop("loaded_for", None)
            AU._persist()
            AU._go(to)
            assert AU._ST["state"] == to
            assert _state_file(tmp_path)["state"] == to, (frm, to)
            AU._load()                                  # a fresh start reads it back
            want = "idle" if to in AU.TRANSIENT else "ready" if to == "installing" else to
            assert AU._ST["state"] == want, (frm, to, AU._ST["state"])
    for frm in AU.STATES:                               # every other edge is refused and changes nothing
        for to in set(AU.STATES) - AU.TRANSITIONS[frm] - {frm}:
            AU._ST.clear()
            AU._ST.update(state=frm, **_entry(tmp_path))
            AU._persist()
            before = (tmp_path / "data" / "updates" / "state.json").read_bytes()
            with pytest.raises(AU.BadTransition):
                AU._go(to, tag="v9.9.9")
            assert AU._ST["state"] == frm and AU._ST["tag"] == OLD
            assert (tmp_path / "data" / "updates" / "state.json").read_bytes() == before
    for frm, to in (("idle", "ready"), ("idle", "verifying"), ("ready", "checking"), ("installing", "downloading"),
                    ("uptodate", "ready"), ("available", "staging")):
        assert to not in AU.TRANSITIONS[frm]


def test_progress_is_not_written_to_the_file(env, tmp_path):
    env.publish(OLD)
    AU._go("downloading", tag=OLD)
    p = tmp_path / "data" / "updates" / "state.json"
    before = p.read_bytes()
    AU._set(done=5, total=10)
    assert AU._ST["done"] == 5 and p.read_bytes() == before and "done" not in _state_file(tmp_path)


# ------------------------------------------------------------------ M10-01

def test_failed_helper_is_not_rerun_on_quit(env, tmp_path):
    env.make_ready()
    assert AU.begin_apply()["ok"] and len(env.launched) == 1
    assert _state_file(tmp_path)["state"] == "installing"          # saved before the helper ran
    assert _state_file(tmp_path)["attempt"]["tag"] == OLD
    (tmp_path / "data" / "updates" / "result.json").write_text(
        json.dumps({"ok": False, "message": "The installer stopped with code 5"}), encoding="utf-8")
    AU._ST.clear()                                                 # the app was started again, still the old version
    AU._CFG.pop("loaded_for", None)
    AU._load()
    assert AU._ST["state"] == "ready" and AU._ST["install_failures"] == 1
    assert "code 5" in AU._ST["error"]
    assert (tmp_path / "data" / "updates" / "result.json").exists()
    AU.on_quit()                                                   # quitting must not run the failing helper again
    assert len(env.launched) == 1 and AU._ST["state"] == "ready"
    assert AU.begin_apply()["ok"] and len(env.launched) == 2       # the Restart button is an explicit retry


def test_installing_resolves_success_on_new_version(env, tmp_path, monkeypatch):
    st = env.make_ready()
    assert AU.begin_apply()["ok"]
    (tmp_path / "data" / "updates" / "result.json").write_text(json.dumps({"ok": True, "message": "Meshpool was updated."}))
    monkeypatch.setattr(U.MH, "__version__", "0.2.0")              # the helper installed it
    AU._ST.clear()
    AU._CFG.pop("loaded_for", None)
    AU._load()
    assert AU._ST["state"] == "idle" and not AU._ST.get("tag")
    assert not Path(st["file"]).exists() and not (tmp_path / "data" / "updates" / OLD).exists()
    assert _state_file(tmp_path)["state"] == "idle"
    assert AU.status()["last_result"]["ok"] is True               # the result is kept for the page


def test_helper_that_never_finished_is_a_failure_with_a_message(env, tmp_path):
    env.make_ready()
    assert AU.begin_apply()["ok"]
    AU._ST.clear()
    AU._CFG.pop("loaded_for", None)
    AU._load()                                                     # no result.json at all
    assert AU._ST["state"] == "ready" and AU._ST["error"] == "The update did not finish."


def test_a_launch_that_fails_goes_back_to_ready(env, monkeypatch):
    env.make_ready()
    monkeypatch.setattr(AU, "launch_helper", lambda st: {"ok": False, "error": "no helper"})
    r = AU.begin_apply()
    assert not r["ok"] and r["error"] == "no helper"
    assert AU._ST["state"] == "ready" and AU._ST["error"] == "no helper"


def test_busy_install_lock_is_reported(env, monkeypatch):
    env.make_ready()

    @contextlib.contextmanager
    def busy():
        raise Busy()
        yield
    monkeypatch.setattr(U, "install_lock", busy, raising=False)
    assert AU.begin_apply() == {"ok": False, "error": "An update is already being installed."}
    AU.on_quit()                                                   # skipped quietly
    assert env.launched == [] and AU._ST["state"] == "ready"


# ------------------------------------------------------------------ M10-02

def test_withdrawn_ready_release_is_dropped_and_not_installed(env, tmp_path):
    st = env.make_ready()
    env.tags, env.newer = [], False                                # the maintainers deleted v0.2.0
    AU.tick()
    assert AU._ST["state"] == "withdrawn"
    assert "was withdrawn by its maintainers, so it was not installed" in AU._ST["error"]
    assert not Path(st["file"]).exists() and not (tmp_path / "data" / "updates" / OLD).exists()
    AU.on_quit()
    assert env.launched == [] and not AU.begin_apply()["ok"]


def test_on_quit_rechecks_unconfirmed_release(env):
    env.make_ready()
    AU.on_quit()                                                   # confirmed a moment ago: no network
    assert env.checks == [] and len(env.launched) == 1
    env.launched.clear()
    AU._go("ready", attempt=None)                                  # the helper never ran (stand-in): back to ready
    AU._note(confirmed_at=time.time() - 48 * 3600)
    env.check_fails = True                                         # cannot confirm: do not install this time
    AU.on_quit()
    assert env.checks == [(True, 5)] and env.launched == [] and AU._ST["state"] == "ready"
    env.check_fails = False                                        # still published: install
    AU.on_quit()
    assert env.checks[-1] == (True, 5) and len(env.launched) == 1 and AU._ST["state"] == "installing"


def test_on_quit_drops_a_release_that_turns_out_withdrawn(env):
    st = env.make_ready()
    AU._note(confirmed_at=time.time() - 48 * 3600)
    env.tags, env.newer = [], False
    AU.on_quit()
    assert env.launched == [] and AU._ST["state"] == "withdrawn" and not Path(st["file"]).exists()


def test_restart_button_refuses_a_withdrawn_release_but_not_a_failed_check(env):
    env.make_ready()
    env.check_fails = True
    assert AU.begin_apply()["ok"]                                  # no network: go ahead
    AU._go("ready")
    env.launched.clear()
    env.check_fails = False
    env.tags, env.newer = [], False
    r = AU.begin_apply()
    assert not r["ok"] and "withdrawn" in r["error"] and env.launched == []


def test_a_newer_release_supersedes_the_ready_one(env, tmp_path):
    st = env.make_ready()
    env.publish(NEWER)
    AU.tick()
    assert AU._ST["state"] == "ready" and AU._ST["tag"] == NEWER
    assert not Path(st["file"]).exists() and not (tmp_path / "data" / "updates" / OLD).exists()


# ------------------------------------------------------------------ M10-03

def test_failed_check_keeps_ready_and_downloading(env):
    env.make_ready()
    env.check_fails = True
    AU.tick()
    assert AU._ST["state"] == "ready" and "GitHub" in AU._ST["check_error"] and AU._ST["checked_at"]
    AU._ST["state"] = "downloading"                                # a download in another thread
    AU.tick()
    assert AU._ST["state"] == "downloading"
    env.check_fails = False
    AU._ST["state"] = "ready"
    AU.tick()
    assert AU._ST["check_error"] == ""


def test_a_failed_check_on_an_idle_copy_stays_idle(env):
    env.check_fails = True
    AU.tick()
    assert AU._ST["state"] == "idle" and AU._ST["check_error"]


# ------------------------------------------------------------------ M10-04

def test_missing_installer_is_downloaded_again(env):
    st = env.make_ready()
    assert env.downloads == 1
    Path(st["file"]).unlink()                                      # antivirus, a cleaner, the user
    AU.tick()
    assert env.downloads == 2 and AU._ST["state"] == "ready" and Path(AU._ST["file"]).exists()


def test_launch_helper_with_the_file_gone_drops_the_entry(env, monkeypatch):
    st = env.make_ready()
    kicked = []
    monkeypatch.setattr(AU, "kick", lambda manual=False: kicked.append(manual))
    Path(st["file"]).unlink()
    r = AU.launch_helper(dict(AU._ST))
    assert not r["ok"] and "downloaded again" in r["error"]
    assert AU._ST["state"] == "idle" and kicked and env.launched == []
    env.make_ready()
    Path(AU._ST["file"]).unlink()
    r = AU.begin_apply()
    assert not r["ok"] and AU._ST["state"] == "idle" and len(kicked) == 2


def test_an_update_not_newer_than_this_copy_is_not_intact(env, monkeypatch):
    env.make_ready()
    monkeypatch.setattr(U.MH, "__version__", "0.2.0")
    assert not AU._intact(dict(AU._ST))
    AU._ST.clear()
    AU._CFG.pop("loaded_for", None)
    AU._load()
    assert AU._ST["state"] == "idle"


# ------------------------------------------------------------------ M10-05

def test_code_update_needing_installer_downloads_installer(env, monkeypatch):
    env.manifest = {"runtime_changed": False}
    prepared = []
    monkeypatch.setattr(U, "prepare", lambda v, auto=True: prepared.append(v["tag"]) or
                        {"ok": False, "need_installer": True, "error": "runtime changed"}, raising=False)
    env.publish(OLD)
    AU.pipeline(env.release(OLD), download=True)
    assert prepared == [OLD]                                       # one run: stage attempt, then the installer
    assert AU._ST["state"] == "ready" and AU._ST["mode"] == "win-installer" and env.downloads == 1
    assert AU._ST["need_installer"] == OLD


def test_code_update_is_staged_and_applied_at_restart(env, monkeypatch, tmp_path):
    env.manifest = {"runtime_changed": False}
    stage = tmp_path / "inst" / "app" / ".update-v0.2.0-abc"
    stage.mkdir()
    monkeypatch.setattr(U, "prepare", lambda v, auto=True: {"ok": True, "action": "staged", "stage": str(stage),
                                                            "tag": v["tag"], "latest": v["latest"]}, raising=False)
    applied = []
    monkeypatch.setattr(U, "apply_staged", lambda st: applied.append(st["stage"]) or
                        {"ok": True, "action": "restart", "message": "m"}, raising=False)
    env.make_ready()
    assert AU._ST["mode"] == "code" and AU._ST["stage"] == str(stage) and env.downloads == 0
    assert AU.begin_apply() == {"ok": True, "mode": "code"}
    assert applied == [str(stage)] and AU._ST["state"] == "installing" and env.launched == []


def test_code_update_that_cannot_be_applied_falls_back_to_the_installer(env, monkeypatch, tmp_path):
    env.manifest = {"runtime_changed": False}
    stage = tmp_path / "inst" / "app" / ".update-v0.2.0-abc"
    stage.mkdir()
    n = []
    monkeypatch.setattr(U, "prepare", lambda v, auto=True: n.append(1) or {"ok": True, "action": "staged",
                                                                           "stage": str(stage)}, raising=False)
    monkeypatch.setattr(U, "apply_staged", lambda st: {"ok": False, "error": "needs the installer",
                                                       "need_installer": True}, raising=False)
    env.make_ready()
    r = AU.begin_apply()
    assert not r["ok"] and r["need_installer"] and AU._ST["state"] == "failed" and not stage.exists()
    AU.tick()                                                      # now, not after a back-off
    assert len(n) == 1                                             # the code path is skipped for this tag
    assert AU._ST["state"] == "ready" and AU._ST["mode"] == "win-installer" and env.downloads == 1


# ------------------------------------------------------------------ M10-06

def test_checksum_failure_backs_off(env, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(AU.time, "time", lambda: now[0])
    env.sums = {"Meshpool-Setup.exe": "0" * 64}                    # the release's sums never match the file
    env.publish(OLD)
    AU.tick()
    assert env.downloads == 1 and AU._ST["state"] == "failed"
    fl = AU._ST["fail"]
    assert fl["tag"] == OLD and fl["count"] == 1 and fl["next_try"] == now[0] + AU.RETRY and fl["sums_fp"]
    now[0] += 60
    AU.tick()                                                      # inside the back-off: nothing is downloaded
    assert env.downloads == 1 and AU._ST["state"] == "failed"
    now[0] = fl["next_try"] + 1
    AU.tick()
    assert env.downloads == 2 and AU._ST["fail"]["count"] == 2
    assert AU._ST["fail"]["next_try"] == now[0] + 2 * AU.RETRY     # the delay doubled
    now[0] += AU.RETRY + 5                                         # past the first delay, inside the doubled one
    AU.tick()
    assert env.downloads == 2
    env.sums = {"Meshpool-Setup.exe": "1" * 64}                    # the release was re-uploaded: try at once
    AU.tick()
    assert env.downloads == 3 and AU._ST["fail"]["count"] == 3
    for _ in range(12):                                            # the delay stops growing at one day
        now[0] = AU._ST["fail"]["next_try"] + 1
        AU.tick()
    assert AU._ST["fail"]["next_try"] - now[0] == 24 * 3600
    now[0] += 10
    AU.tick(manual=True)                                           # the user asked: no waiting
    assert env.downloads == 3 + 12 + 1


def test_the_loop_sleeps_until_the_next_try(env, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(AU.time, "time", lambda: now[0])
    env.sums = {"Meshpool-Setup.exe": "0" * 64}
    env.publish(OLD)
    AU.tick()
    assert AU.RETRY <= AU._next_wait() <= AU.RETRY + 40            # the earlier of the next check and next_try
    env.sums = {"Meshpool-Setup.exe": hashlib.sha256(PAYLOAD).hexdigest()}
    AU.tick(manual=True)
    assert AU._ST["state"] == "ready" and AU._next_wait() >= AU.INTERVAL
    env.check_fails = True
    AU.tick()
    assert AU.RETRY <= AU._next_wait() < AU.INTERVAL               # a failed check is retried in RETRY


# ------------------------------------------------------------------ M10-07

def _mac(env, monkeypatch, tmp_path):
    apps = tmp_path / "Applications"
    bundle = apps / "Meshpool.app"
    bundle.mkdir(parents=True)
    env.kind = "mac-app"
    monkeypatch.setattr(AU, "mac_bundle", lambda: bundle)
    made = []

    def stage_mac(dmg, v, b, work):
        root = apps / f".Meshpool-update-{v['tag'].lstrip('v')}"
        (root / "Meshpool.app").mkdir(parents=True)
        made.append(root)
        if env.stage_fails:
            raise AU.UpdateError("The new Meshpool app failed its code-signature check, so it was not installed.")
        return {"staged": str(root / "Meshpool.app"), "stage_root": str(root), "needs_admin": False, "bundle": str(b)}
    env.stage_fails = False
    monkeypatch.setattr(AU, "stage_mac", stage_mac)
    return apps, made


def test_superseded_or_failed_mac_stage_is_removed(env, monkeypatch, tmp_path):
    apps, made = _mac(env, monkeypatch, tmp_path)
    (apps / ".Meshpool-update-0.1.5").mkdir()                      # a leftover from a run that never finished
    (apps / "Other.app").mkdir()
    env.make_ready(OLD)
    assert (apps / ".Meshpool-update-0.2.0" / "Meshpool.app").is_dir() and AU._ST["mode"] == "mac-swap"
    assert not (apps / ".Meshpool-update-0.1.5").exists()          # swept when the new download started
    env.publish(NEW)
    AU.tick()                                                      # v0.3.0 supersedes v0.2.0
    assert AU._ST["tag"] == NEW and AU._ST["state"] == "ready"
    assert not (apps / ".Meshpool-update-0.2.0").exists()
    assert (apps / ".Meshpool-update-0.3.0" / "Meshpool.app").is_dir()
    assert not (tmp_path / "data" / "updates" / OLD).exists() and (apps / "Other.app").is_dir()
    env.stage_fails = True                                         # a stage that fails verification
    env.publish(NEWER)
    AU.tick()
    assert AU._ST["state"] == "failed" and AU._ST["fail"]["tag"] == NEWER
    assert not (apps / ".Meshpool-update-0.4.0").exists() and not (apps / ".Meshpool-update-0.3.0").exists()


def test_mac_stage_leftovers_are_swept_at_load(env, monkeypatch, tmp_path):
    apps, _ = _mac(env, monkeypatch, tmp_path)
    (apps / ".Meshpool-update-0.9.9").mkdir()
    (tmp_path / "data" / "updates" / "v0.9.9").mkdir(parents=True)
    AU._load()
    assert not (apps / ".Meshpool-update-0.9.9").exists() and not (tmp_path / "data" / "updates" / "v0.9.9").exists()


def test_mac_helper_script_removes_the_stage_when_it_gives_up():
    s = AU.mac_helper_script(777, "/Applications/Meshpool.app", "/Applications/.s/Meshpool.app", "/Applications/.s",
                             "/Applications/Meshpool.app.previous", "/tmp/l", "/tmp/r", False)
    codesign = s[s.index("staged app failed codesign"):s.index('say "swapping')]
    assert 'rm -rf "$STAGE"' in codesign
    rolled = s[s.index("new app did not start"):]
    assert 'rm -rf "$STAGE"' in rolled
    swap_failed = s[s.index("swap failed or was cancelled"):s.index("# a quarantine flag")]
    assert "STAGE" not in swap_failed                              # kept so the user can retry with Restart


# ------------------------------------------------------------------ M10-08

def test_tick_does_not_consume_result(env, tmp_path):
    p = tmp_path / "data" / "updates" / "result.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"ok": False, "message": "Meshpool could not be updated."}))
    env.newer = False
    AU.tick()
    assert "seen" not in json.loads(p.read_text())
    assert AU.status()["last_result"]["message"] == "Meshpool could not be updated."
    assert "seen" not in json.loads(p.read_text())                 # status() alone has no side effect
    assert AU.status(consume=True)["last_result"]["ok"] is False
    assert json.loads(p.read_text())["seen"] is True
    assert AU.status()["last_result"] is None


def test_status_reports_the_last_check(env):
    env.publish(OLD)
    env.make_ready()
    s = AU.status()
    assert s["state"] == "ready"
    AU.tick()
    s = AU.status()
    assert s["check"]["tag"] == OLD and s["check"]["newer"] and s["check"]["kind"] == "win-app"
    assert s["check"]["pending_restart"] is False and s["check"]["prerelease"] is False
    assert s["check_error"] == "" and s["checked_at"] and s["install_failures"] == 0
    assert s["fail"] is None and s["manual"] is False and s["next_try"] is None


# ------------------------------------------------------------------ M10-09

def test_reread_sums_hash_is_recorded(env):
    env.sums = {"Meshpool-Setup.exe": "0" * 64}                    # the sums read first are stale
    env.fresh = {"Meshpool-Setup.exe": hashlib.sha256(PAYLOAD).hexdigest()}
    st = env.make_ready()
    assert st["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()     # what the Windows helper will compare with


# ------------------------------------------------------------------ M10-10

def test_enabling_auto_download_starts_download(env):
    AU.save_settings({"auto_download": False})
    env.publish(OLD)
    AU.tick()
    assert AU._ST["state"] == "available" and env.downloads == 0
    AU.save_settings({"auto_download": True})                      # no waiting for the next 6-hourly check
    assert _wait(lambda: AU._ST["state"] == "ready")
    assert env.downloads == 1
    AU.save_settings({"install_on_quit": False})                   # other changes start nothing
    assert AU.settings()["install_on_quit"] is False


# ------------------------------------------------------------------ M11-04

def test_manual_prerelease_download_runs(env):
    env.pre = True
    env.publish("v0.2.0-rc.1")
    AU.tick()                                                      # automatic: never
    assert AU._ST["state"] == "available" and AU._ST["manual"] and env.downloads == 0
    AU.kick(manual=True)                                           # the user clicked Download
    assert _wait(lambda: AU._ST["state"] == "ready")
    assert env.downloads == 1 and AU._ST["manual"] is True and AU._ST["tag"] == "v0.2.0-rc.1"
    AU.on_quit()                                                   # a prerelease is never installed by quitting
    assert env.launched == []
    assert AU.begin_apply()["ok"] and len(env.launched) == 1       # but Restart installs it


def test_manual_install_passes_manual_to_the_pipeline(env, monkeypatch):
    seen = []
    monkeypatch.setattr(AU, "pipeline", lambda v, download=True, manual=False: seen.append((v["tag"], manual)))
    env.pre = True
    env.publish("v0.2.0-rc.1")
    assert AU.manual_install(env.release())["action"] == "downloading"
    assert _wait(lambda: seen) and seen == [("v0.2.0-rc.1", True)]


# ------------------------------------------------------------------ persistence

def test_state_survives_restart_and_corrupt_file_is_quarantined(env, tmp_path):
    st = env.make_ready()
    saved = _state_file(tmp_path)
    assert saved["state"] == "ready" and saved["tag"] == OLD and saved["sha256"] == st["sha256"]
    AU._ST.clear()
    AU._CFG.pop("loaded_for", None)
    assert AU.status()["state"] == "ready"                         # loaded lazily on first use
    assert AU._ST["file"] == st["file"] and AU._ST["confirmed_at"]
    p = tmp_path / "data" / "updates" / "state.json"
    p.write_text("{ not json", encoding="utf-8")
    AU._load()
    assert AU._ST["state"] == "idle"
    assert list(p.parent.glob("state.json.corrupt-*"))


def test_an_interrupted_download_is_idle_after_a_restart(env, tmp_path):
    env.publish(OLD)
    AU._go("downloading", tag=OLD, latest="0.2.0", mode="win-installer")
    AU._ST.clear()
    AU._load()
    assert AU._ST["state"] == "idle"
    AU.tick()
    assert AU._ST["state"] == "ready"


def test_old_ready_json_is_migrated_once(env, tmp_path):
    f = tmp_path / "data" / "updates" / OLD / "Meshpool-Setup.exe"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    old = tmp_path / "data" / "updates" / "ready.json"
    old.write_text(json.dumps({"state": "ready", "mode": "win-installer", "tag": OLD, "latest": "0.2.0",
                               "file": str(f), "sha256": "a" * 64, "url": "u"}))
    AU._load()
    assert AU._ST["state"] == "ready" and AU._ST["file"] == str(f)
    assert not old.exists() and _state_file(tmp_path)["state"] == "ready"


def test_state_json_ready_with_missing_files_is_dropped_at_load(env, tmp_path):
    st = env.make_ready()
    Path(st["file"]).unlink()
    AU._ST.clear()
    AU._CFG.pop("loaded_for", None)
    AU._load()
    assert AU._ST["state"] == "idle" and _state_file(tmp_path)["state"] == "idle"


# ------------------------------------------------------------------ M12-02 back end

def test_in_place_install_is_reported_as_installing(env, tmp_path):
    assert AU.mark_installing(NEW, "0.3.0", "cli")
    assert AU._ST["state"] == "installing" and _state_file(tmp_path)["state"] == "installing"
    AU.install_finished(False, "pip failed")
    assert AU._ST["state"] == "failed" and AU._ST["error"] == "pip failed" and AU._ST["fail"]["tag"] == NEW
    assert AU.mark_installing(NEW, "0.3.0", "cli")
    AU.install_finished(True)
    assert AU._ST["state"] == "installing"                         # until the app restarts
