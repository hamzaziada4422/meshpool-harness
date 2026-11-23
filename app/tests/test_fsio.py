"""Shared crash-safe writes and cross-process locks (meshpool_harness/fsio.py)."""
import json
import multiprocessing as mp
import os

import pytest

from meshpool_harness import fsio


def test_atomic_write_leaves_old_file_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    fsio.atomic_write_json(p, {"v": 1})
    def boom(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(fsio, "_replace", boom)
    with pytest.raises(OSError):
        fsio.atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text()) == {"v": 1}
    assert [x.name for x in tmp_path.iterdir()] == ["s.json"]      # no temp file left behind


def test_unique_temp_names_for_concurrent_writers(tmp_path):
    import threading
    p = tmp_path / "t.json"
    errs = []
    def w(i):
        try:
            for _ in range(30):
                fsio.atomic_write_json(p, {"i": i, "pad": "x" * 2000}, fsync=False)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs
    assert json.loads(p.read_text())["pad"] == "x" * 2000


def test_read_json_distinguishes_missing_from_corrupt(tmp_path):
    assert fsio.read_json(tmp_path / "none.json", default=[]) == []
    bad = tmp_path / "bad.json"
    bad.write_text('{"half": ')
    with pytest.raises(fsio.CorruptFile):
        fsio.read_json(bad, default=[])
    (tmp_path / "empty.json").write_text("")
    with pytest.raises(fsio.CorruptFile):
        fsio.read_json(tmp_path / "empty.json", default=[])
    q = fsio.quarantine(bad)
    assert q and q.exists() and not bad.exists()


def _bump(path, n):
    for _ in range(n):
        fsio.update_json(path, lambda d: d.append(1), default=[])


def test_update_json_is_safe_across_processes(tmp_path):
    p = str(tmp_path / "counter.json")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_bump, args=(p, 25)) for _ in range(4)]
    [x.start() for x in procs]; [x.join(60) for x in procs]
    assert all(x.exitcode == 0 for x in procs)
    assert len(json.loads(open(p).read())) == 100


def test_update_json_refuses_to_overwrite_corrupt(tmp_path):
    p = tmp_path / "sched.json"
    p.write_text("[{")
    with pytest.raises(fsio.CorruptFile):
        fsio.update_json(p, lambda d: d.append(1), default=[])
    assert p.read_text() == "[{"
