"""Undo only undoes what the agent's own tool calls changed (M05-01, M05-03), turns.json survives damage (M05-04), and
the Undo store is shared, pruned and cheap (H-13, P-05)."""
import contextlib
import errno
import json
import os
import time
import zlib
import hashlib
from pathlib import Path

import pytest

from meshpool_harness import checkpoints as ck
from meshpool_harness import fsio
from meshpool_harness.checkpoints import Checkpoints


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MH_RUNS_DIR", raising=False)
    monkeypatch.delenv("MH_UNDO_TURNS", raising=False)


def _proj(tmp_path, files=None):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    for n, c in (files or {"calc.py": "v1\n", "keep.md": "user\n"}).items():
        (root / n).write_text(c)
    return root


def _cp(tmp_path, root, name="chat1", mode="journal", **kw):
    return Checkpoints(str(root), tmp_path / name, mode=mode, shared=tmp_path / "shared", **kw)


def _write(cp, root, rel, text):
    with cp.tool("write_file", {"path": rel}):
        (root / rel).write_text(text)


def test_user_file_created_during_turn_is_not_undone(tmp_path):
    root = _proj(tmp_path)
    cp = _cp(tmp_path, root)
    cp.begin_turn("agent works")
    _write(cp, root, "calc.py", "v2\n")
    _write(cp, root, "agent.py", "x\n")
    (root / "notes.md").write_text("mine\n")            # the user, in their editor, between tool calls
    (root / "keep.md").write_text("user edit\n")
    cp.end_turn()
    assert sorted(c["path"] for c in cp.turns()[0]["changed"]) == ["agent.py", "calc.py"]
    r = cp.revert_to_before(1)
    assert (root / "notes.md").read_text() == "mine\n" and (root / "keep.md").read_text() == "user edit\n"
    assert (root / "calc.py").read_text() == "v1\n" and not (root / "agent.py").exists()
    assert r["kept"] == [] and r["restored"] == ["calc.py"] and r["deleted"] == ["agent.py"]


def test_user_edit_during_shell_window_is_backed_up(tmp_path):
    root = _proj(tmp_path, {"a.py": "a0\n", "b.py": "b0\n", "u.txt": "u0\n"})
    cp = _cp(tmp_path, root)
    cp.begin_turn("shell")
    with cp.tool("bash", {"command": "make"}):
        (root / "a.py").write_text("a1\n")               # the agent's shell
        (root / "b.py").unlink()
        (root / "new.py").write_text("n\n")
        (root / "u.txt").write_text("u1 typed by the user meanwhile\n")   # not distinguishable inside a window
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert (root / "a.py").read_text() == "a0\n" and (root / "b.py").read_text() == "b0\n"
    assert not (root / "new.py").exists()
    bk = Path(r["backup"])
    assert (bk / "a.py").read_text() == "a1\n" and (bk / "new.py").read_text() == "n\n"
    assert (bk / "u.txt").read_text() == "u1 typed by the user meanwhile\n"      # the user's text is not lost
    assert not (bk / "b.py").exists()                    # it was absent, nothing to copy


def test_undo_in_other_chat_is_not_booked(tmp_path):
    root = _proj(tmp_path, {"a.py": "v1\n"})
    a, b = _cp(tmp_path, root, "chatA"), _cp(tmp_path, root, "chatB")
    a.begin_turn("A edits")
    _write(a, root, "a.py", "v2\n")
    a.end_turn()
    b.begin_turn("B works")
    with b.tool("bash", {"command": "sleep 1"}):
        r = a.revert_to_before(1)                        # the user presses Undo in chat A meanwhile
        assert r["restored"] == ["a.py"]
        (root / "b.py").write_text("mine\n")
    b.end_turn()
    assert [c["path"] for c in b.turns()[0]["changed"]] == ["b.py"]
    assert (root / "a.py").read_text() == "v1\n"
    rb = b.revert_to_before(1)
    assert rb["deleted"] == ["b.py"] and (root / "a.py").read_text() == "v1\n"


def test_overlapping_windows_mark_ambiguous_and_undo_keeps_them(tmp_path):
    root = _proj(tmp_path, {"x.py": "x0\n", "y.py": "y0\n"})
    a, b = _cp(tmp_path, root, "chatA"), _cp(tmp_path, root, "chatB")
    a.begin_turn("A")
    b.begin_turn("B")
    with contextlib.ExitStack() as st:
        st.enter_context(a.tool("bash", {}))
        st.enter_context(b.tool("bash", {}))
        (root / "x.py").write_text("x1\n")               # A's shell
        (root / "y.py").write_text("y1\n")               # B's shell
    _write(a, root, "z.py", "z\n")                       # a plain write tool: never ambiguous
    a.end_turn()
    b.end_turn()
    ch = {c["path"]: c for c in a.turns()[0]["changed"]}
    assert ch["x.py"].get("ambiguous") and ch["y.py"].get("ambiguous") and not ch["z.py"].get("ambiguous")
    r = a.revert_to_before(1)
    assert (root / "x.py").read_text() == "x1\n" and (root / "y.py").read_text() == "y1\n"
    assert sorted(r["kept"]) == ["x.py", "y.py"] and r["deleted"] == ["z.py"]
    assert any("another chat was working in this folder at the same time" in n for n in r["notes"])


def test_turns_json_survives_disk_full_and_corruption(tmp_path, monkeypatch):
    root = _proj(tmp_path)
    store = tmp_path / "chat1"
    cp = _cp(tmp_path, root)
    cp.begin_turn("one")
    cp.end_turn()
    cp.begin_turn("two")
    cp.end_turn()
    good = (store / "turns.json").read_text()
    real = fsio.atomic_write_json

    def full(path, obj, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(fsio, "atomic_write_json", full)
    cp.begin_turn("three")                               # must not raise, must not touch the file
    assert cp.warning and (store / "turns.json").read_text() == good
    assert [t["n"] for t in _cp(tmp_path, root).turns()] == [1, 2]
    assert not [p for p in store.iterdir() if p.name.endswith(".tmp")]
    monkeypatch.setattr(fsio, "atomic_write_json", real)
    cp.end_turn()                                        # the retry succeeds
    assert [t["n"] for t in _cp(tmp_path, root).turns()] == [1, 2, 3]

    # truncated file: recovered from .bak, damaged copy quarantined, numbers never reused
    cp = _cp(tmp_path, root, "chat2")
    cp.begin_turn("one")
    cp.end_turn()
    cp.begin_turn("two")                                 # the .bak now lacks turn 2
    data = (tmp_path / "chat2" / "turns.json").read_text()
    (tmp_path / "chat2" / "turns.json").write_text(data[: len(data) // 2])
    cp2 = _cp(tmp_path, root, "chat2")
    assert [t["n"] for t in cp2.turns()] == [1]
    assert list((tmp_path / "chat2").glob("turns.json.corrupt-*"))
    assert cp2.begin_turn("again")["n"] == 3

    # a revert drops turns but never frees their numbers
    cp3 = _cp(tmp_path, root, "chat3", seq_floor=10)
    cp3.begin_turn("a")
    cp3.end_turn()
    cp3.begin_turn("b")
    cp3.end_turn()
    cp3.revert_to_before(11)
    assert cp3.begin_turn("c")["n"] == 13 and _cp(tmp_path, root, "chat3").seq == 13


def test_store_is_shared_and_pruned(tmp_path, monkeypatch):
    root = _proj(tmp_path, {"a.txt": "alpha\n", "b.txt": "beta\n", "c.txt": "gamma\n"})
    for f in root.iterdir():
        os.utime(f, (time.time() - 3600, time.time() - 3600))
    a, b = _cp(tmp_path, root, "chatA", "scan"), _cp(tmp_path, root, "chatB", "scan")
    reads = []
    real = Path.read_bytes

    def counting(self):
        if str(self).startswith(str(root)):
            reads.append(self.name)
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", counting)
    a.begin_turn("A")
    b.begin_turn("B")
    objs = [p for p in (tmp_path / "shared" / "objects").rglob("*") if p.is_file()]
    assert len(objs) == 3 and not (tmp_path / "chatA" / "objects").exists()    # each content stored once
    assert len(reads) == 3                                                       # B's walk re-read nothing
    a.end_turn()
    b.end_turn()
    reads.clear()
    c = _cp(tmp_path, root, "chatA", "scan")             # a restart: new object, same folder
    c.begin_turn("again")
    assert reads == []                                   # the persisted stat cache covers unchanged files
    c.end_turn()
    monkeypatch.setattr(Path, "read_bytes", real)

    # pruning: 150 turns, only the last 100 stay undoable
    j = _cp(tmp_path, root, "chatJ")
    for i in range(150):
        j.begin_turn(f"t{i}")
        _write(j, root, f"f{i}.txt", f"content {i}\n")
        j.end_turn()
    ts = j.turns()
    assert len(ts) == 150 and sum(1 for t in ts if t["pruned"]) == 50
    assert ts[0]["changed"] == [{"path": "f0.txt", "status": "added"}]       # the file list survives
    with pytest.raises(ValueError, match="too old"):
        j.revert_to_before(ts[0]["n"])
    # GC: unreferenced objects older than an hour go; referenced ones stay
    for p in (tmp_path / "shared" / "objects").rglob("*"):
        if p.is_file():
            os.utime(p, (time.time() - 7200, time.time() - 7200))
    before = len([p for p in (tmp_path / "shared" / "objects").rglob("*") if p.is_file()])
    res = j.gc(force=True)
    after = [p for p in (tmp_path / "shared" / "objects").rglob("*") if p.is_file()]
    assert res["deleted"] > 0 and len(after) == before - res["deleted"]
    sha_last = hashlib.sha1(b"content 149\n").hexdigest()
    sha_first = hashlib.sha1(b"content 0\n").hexdigest()
    assert (tmp_path / "shared" / "objects" / sha_last[:2] / sha_last[2:]).exists()
    assert not (tmp_path / "shared" / "objects" / sha_first[:2] / sha_first[2:]).exists()
    j.revert_to_before(ts[-1]["n"])                      # and a recent turn still undoes
    assert not (root / "f149.txt").exists() and (root / "f148.txt").exists()


def _listing(top):
    return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in Path(top).rglob("*") if p.is_file())


@pytest.mark.parametrize("mode", ["scan", "journal"])
def test_running_diff_writes_nothing(tmp_path, mode):
    root = _proj(tmp_path)
    cp = _cp(tmp_path, root, mode=mode)
    cp.begin_turn("running")
    if mode == "journal":
        _write(cp, root, "calc.py", "v2\n")
    else:
        (root / "calc.py").write_text("v2\n")
    (root / "new.py").write_text("n\n") if mode == "scan" else None
    snap = _listing(tmp_path)
    d = cp.diff(None)
    assert "+v2" in d["patch"] and [f["path"] for f in d["files"]][0] == "calc.py"
    assert _listing(tmp_path) == snap
    assert "+v2" in cp.diff(1)["patch"] and _listing(tmp_path) == snap


def test_old_turn_records_still_undo(tmp_path):
    root = _proj(tmp_path, {"a.py": "new\n", "added.py": "agent\n", "user.md": "mine\n"})
    store = tmp_path / "old"

    def put(data):
        sha = hashlib.sha1(data).hexdigest()
        p = store / "objects" / sha[:2] / sha[2:]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(zlib.compress(data))
        return sha
    old_a, new_a, add = put(b"old\n"), put(b"new\n"), put(b"agent\n")
    (store / "manifests").mkdir(parents=True)
    (store / "manifests" / "m1.json").write_text(json.dumps({"a.py": old_a + ":644"}))
    (store / "manifests" / "m2.json").write_text(json.dumps({"a.py": new_a + ":644", "added.py": add + ":644"}))
    (store / "turns.json").write_text(json.dumps([{
        "n": 1, "title": "old", "before": "m1", "after": "m2", "time": 1.0, "message_index": 4,
        "changed": [{"path": "a.py", "status": "modified"}, {"path": "added.py", "status": "added"}]}]))
    cp = Checkpoints(str(root), store, shared=tmp_path / "shared")
    d = cp.diff(1)
    assert "-old" in d["patch"] and "+new" in d["patch"]
    assert cp.turns()[0]["done"] is True
    r = cp.revert_to_before(1)
    assert (root / "a.py").read_text() == "old\n" and not (root / "added.py").exists()
    assert (root / "user.md").read_text() == "mine\n"
    assert r["restored"] == ["a.py"] and r["deleted"] == ["added.py"] and r["message_index"] == 4
    assert not (store / "objects").exists() and not (store / "manifests").exists()   # legacy copies cleaned up
    assert cp.begin_turn("next")["n"] == 2


def test_apply_patch_paths_are_recorded(tmp_path):
    root = _proj(tmp_path, {"edit.py": "e0\n", "gone.py": "g0\n", "mv.py": "m0\n", "uni.py": "u0\n"})
    patch = ("*** Begin Patch\n*** Add File: made.py\n+hello\n*** Update File: edit.py\n@@\n-e0\n+e1\n"
             "*** Delete File: gone.py\n*** Update File: mv.py\n*** Move to: moved.py\n*** End Patch\n"
             "--- a/uni.py\t2024\n+++ b/uni.py\n@@ -1 +1 @@\n-u0\n+u1\n--- /dev/null\n")
    cp = _cp(tmp_path, root)
    assert sorted(cp._targets("apply_patch", {"patch": patch})) == ["edit.py", "gone.py", "made.py", "moved.py",
                                                                    "mv.py", "uni.py"]
    cp.begin_turn("patch")
    with cp.tool("apply_patch", {"patch": patch}):
        (root / "made.py").write_text("hello\n")
        (root / "edit.py").write_text("e1\n")
        (root / "gone.py").unlink()
        (root / "mv.py").rename(root / "moved.py")
        (root / "uni.py").write_text("u1\n")
    (root / "stray.py").write_text("outside any tool\n")
    cp.end_turn()
    st = {c["path"]: c["status"] for c in cp.turns()[0]["changed"]}
    assert st == {"made.py": "added", "edit.py": "modified", "gone.py": "deleted", "mv.py": "deleted",
                  "moved.py": "added", "uni.py": "modified"}
    cp.revert_to_before(1)
    assert {p.name: p.read_text() for p in root.iterdir()} == {"edit.py": "e0\n", "gone.py": "g0\n", "mv.py": "m0\n",
                                                               "uni.py": "u0\n", "stray.py": "outside any tool\n"}


def test_background_window_and_final_scan(tmp_path):
    root = _proj(tmp_path)
    cp = _cp(tmp_path, root)
    cp.begin_turn("dev server")
    cp.window_open()
    (root / "built.txt").write_text("by a process still running\n")
    cp.end_turn()                                        # one final scan because a window is still open
    assert [c["path"] for c in cp.turns()[0]["changed"]] == ["built.txt"]
    cp.revert_to_before(1)
    assert not (root / "built.txt").exists()


def test_tool_outside_a_turn_and_read_only_record_nothing(tmp_path):
    root = _proj(tmp_path)
    cp = _cp(tmp_path, root)
    _write(cp, root, "calc.py", "v2\n")                  # no turn open: no-op
    cp.begin_turn("t")
    with cp.tool("read_file", {"path": "calc.py"}, read_only=True):
        (root / "keep.md").write_text("x\n")
    cp.end_turn()
    assert cp.turns()[0]["changed"] == []


def test_overlap_detected_across_processes(tmp_path):
    import subprocess
    import sys
    root = _proj(tmp_path, {"x.py": "x0\n", "y.py": "y0\n"})
    a = _cp(tmp_path, root, "chatA")
    code = (
        "import sys, json\n"
        "from pathlib import Path\n"
        "from meshpool_harness.checkpoints import Checkpoints\n"
        "root, store, shared = sys.argv[1:4]\n"
        "c = Checkpoints(root, Path(store), mode='journal', shared=Path(shared))\n"
        "c.begin_turn('B')\n"
        "with c.tool('bash', {}):\n"
        "    (Path(root) / 'y.py').write_text('y1\\n')\n"
        "c.end_turn()\n"
        "print(json.dumps(c.turns()[0]['changed']))\n"
    )
    app = str(Path(ck.__file__).resolve().parent.parent)
    a.begin_turn("A")
    with a.tool("bash", {}):
        r = subprocess.run([sys.executable, "-c", code, str(root), str(tmp_path / "chatB"), str(tmp_path / "shared")],
                           capture_output=True, text=True, cwd=app, env={**os.environ, "PYTHONPATH": app})
        assert r.returncode == 0, r.stderr
        (root / "x.py").write_text("x1\n")
    a.end_turn()
    ch_a = {c["path"]: c for c in a.turns()[0]["changed"]}
    ch_b = {c["path"]: c for c in json.loads(r.stdout.strip().splitlines()[-1])}
    assert ch_a["x.py"].get("ambiguous"), ch_a
    assert ch_b["y.py"].get("ambiguous"), ch_b
    assert fsio.read_json(tmp_path / "shared" / "windows.json", {}) == {}


def test_stale_window_entry_is_ignored(tmp_path):
    import socket
    import subprocess
    import sys
    root = _proj(tmp_path, {"x.py": "x0\n"})
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()                                              # its pid is now dead
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    fsio.atomic_write_json(shared / "windows.json", {
        "999-1-dead": {"store": str(tmp_path / "ghost"), "pid": p.pid, "start": time.time(),
                       "host": socket.gethostname()},
        "999-2-old": {"store": str(tmp_path / "ghost2"), "pid": os.getpid(), "start": time.time() - 7 * 3600,
                      "host": socket.gethostname()}})
    a = _cp(tmp_path, root, "chatA")
    a.begin_turn("A")
    with a.tool("bash", {}):
        (root / "x.py").write_text("x1\n")
    a.end_turn()
    ch = {c["path"]: c for c in a.turns()[0]["changed"]}
    assert "x.py" in ch and not ch["x.py"].get("ambiguous")
    assert fsio.read_json(shared / "windows.json", {}) == {}


def test_corrupt_windows_file_is_quarantined(tmp_path):
    root = _proj(tmp_path, {"x.py": "x0\n"})
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    (shared / "windows.json").write_text("{not json")
    a = _cp(tmp_path, root, "chatA")
    a.begin_turn("A")
    with a.tool("bash", {}):
        (root / "x.py").write_text("x1\n")
    a.end_turn()
    assert not a.turns()[0]["changed"][0].get("ambiguous")
    assert list(shared.glob("windows.json.corrupt-*"))
