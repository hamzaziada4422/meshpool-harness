"""The Linux download (Meshpool-Linux-x64.tar.gz): how it is built and how the packaged app finds its own tools there.
No network, no real installs."""
import importlib.util
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

from meshpool_harness import bundled
from meshpool_harness import office_runtime as o
from meshpool_harness import updater as U

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


BT = _load("mp_bundled_tools_linux", "packaging/bundled_tools.py")
BA = _load("mp_build_all_linux", "packaging/build_all.py")


def test_the_linux_libreoffice_is_pinned_like_the_others():
    d, name, sha, size = BT.LO_PINNED["linux-x64"]
    assert d == "deb/x86_64" and name == f"LibreOffice_{BT.LO_BUILD}_Linux_x86-64_deb.tar.gz"
    assert len(sha) == 64 and size > 100_000_000
    assert BT.LO_EXE["linux-x64"] == "program/soffice"
    assert BA.LO_KEY["linux"] == "linux-x64" and BA.PI_NATIVE["linux"] == "linux-x64"
    assert {"linux"} <= set(BA.NODE) & set(BA.RT) & set(BA.PW_PLATFORM)


def _ar(members: dict) -> bytes:
    out = b"!<arch>\n"
    for n, data in members.items():
        out += f"{n + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n".encode() + data
        if len(data) % 2:
            out += b"\n"
    return out


def _deb(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as t:
        for path, data in files.items():
            ti = tarfile.TarInfo(path)
            ti.size = len(data)
            ti.mode = 0o755
            t.addfile(ti, io.BytesIO(data))
    return _ar({"debian-binary": b"2.0\n", "control.tar.xz": b"", "data.tar.xz": buf.getvalue()})


def test_the_deb_packages_unpack_into_one_libreoffice_folder(tmp_path):
    core = _deb({"./opt/libreoffice26.2/program/soffice": b"#!/bin/sh\n",
                 "./opt/libreoffice26.2/program/libmergedlo.so": b"x"})
    impress = _deb({"./opt/libreoffice26.2/program/libsdlo.so": b"y", "./usr/share/doc/x": b"z"})
    archive = tmp_path / "lo.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        for n, data in (("LO/DEBS/core.deb", core), ("LO/DEBS/impress.deb", impress)):
            ti = tarfile.TarInfo(n)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    dest = tmp_path / "libreoffice"
    BT.extract(archive, dest, "linux-x64")
    assert (dest / "program" / "soffice").is_file()
    assert (dest / "program" / "libsdlo.so").is_file()           # every package lands in the same tree
    assert not (dest / "usr").exists()


def test_a_file_that_is_not_a_deb_is_refused(tmp_path):
    p = tmp_path / "x.deb"
    p.write_bytes(b"not an ar archive")
    with pytest.raises(BT.BundleError):
        BT._deb_members(p)


def test_the_packaged_linux_app_finds_its_own_libreoffice(monkeypatch, tmp_path):
    exe = tmp_path / "vendor" / "libreoffice" / "program" / "soffice"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(o.sys, "platform", "linux")
    monkeypatch.setattr(o, "bundled_dir", lambda: tmp_path / "vendor" / "libreoffice")
    assert o._bundled_exe() == str(exe)


def test_the_linux_app_puts_its_libreoffice_on_path(monkeypatch, tmp_path):
    prog = tmp_path / "app" / "vendor" / "libreoffice" / "program"
    prog.mkdir(parents=True)
    monkeypatch.setattr(bundled.sys, "platform", "linux")
    monkeypatch.setattr(bundled.os, "name", "posix")
    monkeypatch.setattr(bundled, "vendor_dir", lambda name: tmp_path / "app" / "vendor" / name)
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.setattr(bundled, "git_dirs", lambda: [])
    monkeypatch.setattr(bundled, "node_dir", lambda: tmp_path / "node")
    monkeypatch.setenv("PATH", "/usr/bin")
    bundled.use_bundled_tools()
    assert str(prog) in os.environ["PATH"].split(os.pathsep)


def test_the_linux_download_is_its_own_kind_and_updates_by_hand(monkeypatch, tmp_path):
    app = tmp_path / "Meshpool" / "app"
    app.mkdir(parents=True)
    (app.parent / "python").mkdir()
    monkeypatch.setattr(U, "APP", app)
    monkeypatch.setattr(U.C, "packaged", lambda: True)
    monkeypatch.setattr(U.sys, "platform", "linux")
    monkeypatch.setattr(U.os, "name", "posix")
    assert U.kind() == "linux-app"
    monkeypatch.setattr(U, "check", lambda force=False: {"ok": True, "newer": True, "kind": "linux-app",
                                                         "latest": "0.1.0", "url": "https://example.invalid/r"})
    r = U.install()
    assert not r["ok"] and "Meshpool-Linux-x64.tar.gz" in r["error"] and r["url"]


def test_the_linux_launchers_and_menu_entry():
    lin = ROOT / "packaging" / "linux"
    for name in ("meshpool", "Meshpool", "install.sh", "build_git.sh"):
        assert os.access(lin / name, os.X_OK), name
    assert "-P -m meshpool_harness" in (lin / "meshpool").read_text()
    entry = (lin / "meshpool.desktop").read_text()
    assert "__EXEC__" in entry and "__ICON__" in entry and "@VERSION@" in entry


def test_attaching_linux_never_touches_the_files_the_mac_and_windows_updaters_check():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    job = text[text.index("\n  linux-attach:"):text.index("\n  publish:")]
    upload = next(ln for ln in job.splitlines() if "gh release upload" in ln)
    assert "Meshpool-Linux-x64.tar.gz" in upload
    for name in ("SHA256SUMS.txt", "update-manifest.json", ".dmg", "Setup.exe", "Meshpool-src.tar.gz"):
        assert name not in upload, name
    assert "refs/heads/main" in job and "grep -qx false" in job          # main only, and only a public release
