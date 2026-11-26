"""CPU use found in the 0.0.18 hunt (area CPU). Each test counts the work (file reads, parses, processes, tree walks)
that used to be redone per call, and checks the kept answer still sees a change.

- CPU-01 profiles.load_all parsed every profile YAML file on each model request (for_model runs several times a step).
- CPU-02 modelcaps._hints read and parsed model_hints.json on each resolve() once a reply had taught anything.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import fsio  # noqa: E402

OLD = time.time() - 3600


def count_opens(monkeypatch, match):
    """A live count of files opened for reading whose path matches match(path) (open, Path.read_text, read_bytes)."""
    import builtins
    import io
    box = {"n": 0, "paths": []}
    real = io.open

    def opener(file, mode="r", *a, **k):
        if "r" in str(mode) and "+" not in str(mode) and isinstance(file, (str, os.PathLike)) and match(os.fspath(file)):
            box["n"] += 1
            box["paths"].append(os.fspath(file))
        return real(file, mode, *a, **k)
    monkeypatch.setattr(io, "open", opener)
    monkeypatch.setattr(builtins, "open", opener)
    return box


def _age(*paths):
    for p in paths:
        os.utime(p, (OLD, OLD))


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(h))
    monkeypatch.setattr(fsio, "RACY_S", 0.0)          # files made by the test are seconds old; the guard has its own test
    return h


def test_parse_cached_rereads_a_file_changed_in_the_last_seconds(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"a": 1}')
    n0 = fsio.READS[0]
    for _ in range(3):
        assert fsio.parse_cached(f, json.loads, "t") == {"a": 1}
    assert fsio.READS[0] - n0 == 3                     # just written: a same-tick change could hide, so it is read
    _age(f)
    time.sleep(0.01)
    fsio.parse_cached(f, json.loads, "t")
    n1 = fsio.READS[0]
    for _ in range(5):
        fsio.parse_cached(f, json.loads, "t")
    assert fsio.READS[0] == n1 or fsio.RACY_S > 0     # ctime is new here; with the guard on it may still re-read
    f.unlink()
    assert fsio.parse_cached(f, json.loads, "t", missing="gone") == "gone"


# ---------------------------------------------------------------------------------------------------- CPU-01
def test_cpu01_profiles_are_parsed_once_while_unchanged(tmp_path, monkeypatch, home):
    import yaml

    from meshpool_harness import profiles as P
    d = tmp_path / "profiles"
    shutil.copytree(P.DIR, d)
    _age(*d.iterdir())
    monkeypatch.setattr(P, "DIR", d)
    calls = [0]
    real = yaml.safe_load

    def counting(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    monkeypatch.setattr(yaml, "safe_load", counting)
    first = P.for_model("claude-sonnet-5")
    for _ in range(30):                                # the body, the client, the context window, the caps: per step
        assert P.for_model("claude-sonnet-5") == first
    files = len(list(d.glob("*.yaml")))
    assert calls[0] == files, f"{calls[0]} YAML parses for 31 lookups over {files} files"
    # a caller that changes its profile does not change the next caller's
    P.load_all()["default"]["limits"] = "changed"
    assert P.load_all()["default"].get("limits") != "changed"
    # an edited profile is seen at once
    f = d / "default.yaml"
    f.write_text(f.read_text(encoding="utf-8") + "\nextra_key_018: 7\n", encoding="utf-8")
    os.utime(f, (OLD + 60, OLD + 60))
    assert P.load_all()["default"]["extra_key_018"] == 7


# ---------------------------------------------------------------------------------------------------- CPU-02
def test_cpu02_model_hints_are_not_reread_per_request(home, monkeypatch):
    from meshpool_harness.core import modelcaps as MC
    MC.reset()
    hints = home / "model_hints.json"
    hints.write_text(json.dumps({"models": [{"match": "^acme-", "reasoning": "effort", "efforts": ["low", "high"]}]}))
    _age(hints)
    base = "https://api.example.com/v1"
    MC.learn(base, "acme-1", usage_cached=True)       # what the first reply teaches: resolve() then skips its cache
    MC.resolve(base, "acme-1")                        # the first request reads the tables
    box = count_opens(monkeypatch, lambda p: p.endswith("model_hints.json"))
    for _ in range(20):
        assert MC.resolve(base, "acme-1").efforts == ("low", "high")
    assert box["n"] == 0, f"model_hints.json was read {box['n']} times for 20 requests"
    hints.write_text(json.dumps({"models": [{"match": "^acme-", "reasoning": "effort", "efforts": ["medium"]}]}))
    os.utime(hints, (OLD + 60, OLD + 60))
    assert MC.resolve(base, "acme-1").efforts == ("medium",)
    MC.reset()
