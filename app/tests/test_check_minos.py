"""The build check packaging/mac/check_minos.py: it reads each architecture's slice of the bundled Mac files and
stops the build when a file needs a newer macOS than the app promises (11 on both Apple silicon and Intel)."""
import importlib.util
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ---------- the build check (packaging/mac/check_minos.py) ---------------------------------------------------------
def _check_minos():
    spec = importlib.util.spec_from_file_location("check_minos", ROOT / "packaging/mac/check_minos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def thin(cpu: int, minos: tuple, filetype: int = 6) -> bytes:
    """A 64-bit Mach-O header with one LC_BUILD_VERSION command (filetype 6: a dylib; 0xA: debug symbols)."""
    cmd = struct.pack("<IIIIII", 0x32, 24, 1, (minos[0] << 16) | (minos[1] << 8), 0, 0)
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, 3, filetype, 1, len(cmd), 0, 0) + cmd


def fat(*slices: bytes, cpus: tuple) -> bytes:
    head = struct.pack(">II", 0xCAFEBABE, len(slices))
    off, body, table = 4096, b"", b""
    for cpu, s in zip(cpus, slices):
        table += struct.pack(">IIIII", cpu, 3, off + len(body), len(s), 12)
        body += s + b"\0" * (4096 - len(s))
    return (head + table).ljust(4096, b"\0") + body


X86, ARM = 0x01000007, 0x0100000C


def test_minos_reads_the_slice_for_the_architecture(tmp_path):
    cm = _check_minos()
    u = tmp_path / "lib.so"
    u.write_bytes(fat(thin(X86, (10, 13)), thin(ARM, (11, 0)), cpus=(X86, ARM)))
    assert cm.minos(u, "x86_64") == (10, 13)
    assert cm.minos(u, "arm64") == (11, 0)
    assert cm.minos(u) == (11, 0)                            # no architecture: the highest slice
    d = tmp_path / "lib.dSYM"
    d.write_bytes(thin(X86, (11, 0), filetype=0xA))
    assert cm.minos(d, "x86_64") is None                    # debug symbols are never loaded
    t = tmp_path / "notes.txt"
    t.write_text("hello")
    assert cm.minos(t) is None


def _app(tmp_path, node: tuple, lib: tuple) -> Path:
    app = tmp_path / "Meshpool.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources/node/bin").mkdir(parents=True)
    (app / "Contents/Resources/python/bin").mkdir(parents=True)
    (app / "Contents/MacOS/Meshpool").write_bytes(thin(X86, (10, 15)))
    (app / "Contents/Resources/python/bin/python3").write_bytes(thin(X86, (10, 15)))
    (app / "Contents/Resources/node/bin/node").write_bytes(thin(X86, node))
    (app / "Contents/Resources/python/lib.so").write_bytes(fat(thin(X86, lib), thin(ARM, (11, 0)), cpus=(X86, ARM)))
    return app


def _run(cm, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["check_minos.py", *map(str, argv)])
    with pytest.raises(SystemExit) as e:
        cm.main()
    return e.value.code


def test_check_minos_intel_exemptions(tmp_path, monkeypatch, capsys):
    cm = _check_minos()
    ex = ["--exempt", "Contents/Resources/node/*=11.0"]
    app = _app(tmp_path / "a", node=(11, 0), lib=(10, 13))
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64", *ex) == 0
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64") == 1             # Node is not exempt by itself
    assert "node/bin/node needs macOS 11.0" in capsys.readouterr().out
    app = _app(tmp_path / "b", node=(12, 0), lib=(10, 13))
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64", *ex) == 1        # exempt only up to 11.0
    app = _app(tmp_path / "c", node=(11, 0), lib=(11, 0))
    assert _run(cm, monkeypatch, app, "10.15", "--arch", "x86_64", *ex) == 1        # any other file: 10.15
    assert "python/lib.so needs macOS 11.0" in capsys.readouterr().out
