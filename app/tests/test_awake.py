"""No idle sleep while a task runs: counted across chats, off with MH_KEEP_AWAKE=0."""
import sys

from meshpool_harness import awake as AW


def test_held_while_any_turn_runs(monkeypatch):
    started, stopped = [], []
    monkeypatch.setattr(AW, "_start", lambda: started.append(1))
    monkeypatch.setattr(AW, "_stop", lambda: stopped.append(1))
    monkeypatch.setattr(AW, "_count", 0)
    AW.hold(); AW.hold()
    assert started == [1] and AW.held()
    AW.release()
    assert stopped == [] and AW.held()
    AW.release(); AW.release()                          # an extra release never goes below zero
    assert stopped == [1] and not AW.held()


def test_off_by_setting(monkeypatch):
    monkeypatch.setenv("MH_KEEP_AWAKE", "0")
    monkeypatch.setattr(AW, "_count", 0)
    monkeypatch.setattr(AW, "_start", lambda: (_ for _ in ()).throw(AssertionError("started")))
    AW.hold()
    assert not AW.held()


def test_the_commands(monkeypatch):
    monkeypatch.setattr(AW.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert AW._command()[:2] == ["caffeinate", "-i"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert AW._command()[0] == "systemd-inhibit" and "--what=idle:sleep" in AW._command()


def test_a_real_holder_starts_and_stops(monkeypatch):
    monkeypatch.setattr(AW, "_count", 0)
    monkeypatch.setattr(AW, "_gave_up", False)     # an earlier test's holder may have ended by itself (no D-Bus here)
    monkeypatch.setattr(AW, "_command", lambda: [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(sys, "platform", "linux")
    AW.hold()
    p = AW._proc
    assert p is not None and p.poll() is None
    AW.release()
    assert p.poll() is not None and AW._proc is None
