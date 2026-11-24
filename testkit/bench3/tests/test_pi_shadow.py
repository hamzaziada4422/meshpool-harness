"""The app copy with vendored Pi is refreshed when the source at the same path changes (a stale copy ran old code)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from testkit.bench3 import arms as A  # noqa: E402


def test_shadow_follows_source_changes(tmp_path):
    cache = tmp_path / "cache"
    cli = cache / "pi" / "node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// pi")
    src = tmp_path / "app"
    (src / "pkg").mkdir(parents=True)
    f = src / "pkg" / "mod.py"
    f.write_text("V = 1\n")
    s1 = A.pi_mp_src(src, cache=cache)
    assert (s1 / "pkg" / "mod.py").read_text() == "V = 1\n" and (s1 / "vendor" / "pi").exists()
    f.write_text("V = 22\n")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))
    s2 = A.pi_mp_src(src, cache=cache)
    assert s2 == s1 and (s2 / "pkg" / "mod.py").read_text() == "V = 22\n"
    assert A.pi_mp_src(src, cache=cache) == s1          # unchanged source: no new copy needed
