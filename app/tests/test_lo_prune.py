"""packaging/lo_prune.py: the list of files deleted from the bundled LibreOffice. The patterns are checked on small
synthetic trees shaped like the Windows, macOS and Linux unpacks of 26.2.7 (the real proof, which converts a deck, a
workbook and a document with a pruned copy, is packaging/verify_lo_prune.py; set MH_TEST_PRUNE_SYSTEM_LO=1 to run it
here on a pruned copy of the system LibreOffice)."""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("mp_lo_prune", ROOT / "packaging" / "lo_prune.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


LP = _load()


def touch(base: Path, rel: str, size: int = 10):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


# (kept, removed) per platform: representative files of the real unpacks
TREES = {
    "win": (["program/soffice.exe", "program/soffice.bin", "program/mergedlo.dll", "program/swlo.dll",
             "program/sclo.dll", "program/sdlo.dll", "program/services.rdb", "program/types.rdb",
             "program/jvmfwklo.dll", "program/clucene.dll", "program/vcruntime140.dll", "program/xpdfimport.exe",
             "share/xpdfimport/a.dat", "share/registry/pdfimport.xcd",
             "program/resource/common/fonts/opens___.ttf", "share/registry/main.xcd", "share/registry/writer.xcd",
             "share/registry/calc.xcd", "share/registry/impress.xcd", "share/registry/graphicfilter.xcd",
             "share/registry/Langpack-en-US.xcd", "share/registry/res/fcfg_langpack_en-US.xcd",
             "share/config/images_colibre.zip", "share/config/soffice.cfg/svt/ui/x.ui", "NOTICE", "LICENSE.html",
             "share/liblangtag/a.xml", "share/numbertext/a.txt"],
            ["help/en-US/a.html", "program/smlo.dll", "program/dbulo.dll", "program/python312.dll",
             "program/python-core-3.12.16/lib.py", "program/python.exe", "program/pyuno.pyd", "program/uno.py",
             "program/classes/a.jar", "program/Engine12.dll", "program/hwplo.dll",
             "program/mwaw.dll", "program/updchklo.dll", "program/resource/de/LC_MESSAGES/cui.mo",
             "program/resource/ja/LC_MESSAGES/cui.mo", "share/extensions/dict-de/de.aff", "share/gallery/a.sdg",
             "share/template/a.ott", "share/wizards/a.py", "share/registry/Langpack-de.xcd", "share/registry/base.xcd",
             "share/registry/math.xcd", "share/registry/res/fcfg_langpack_de.xcd", "share/registry/res/registry_de.xcd",
             "share/config/images_sifr.zip", "share/config/images_karasa_jaga_svg.zip",
             "share/config/soffice.cfg/modules/scalc/ui/a.ui", "share/config/soffice.cfg/cui/ui/a.ui",
             "share/basic/a.xba", "share/autocorr/a.dat", "CREDITS.fodt", "readmes/a.txt"]),
    "linux": (["program/soffice", "program/soffice.bin", "program/libmergedlo.so", "program/libswlo.so",
               "program/libsclo.so", "program/libsdlo.so", "program/services.rdb", "program/types.rdb",
               "program/libjvmfwklo.so", "program/libclucene.so", "program/resource/common/fonts/opens___.ttf",
               "share/registry/main.xcd", "share/registry/writer.xcd", "share/registry/calc.xcd",
               "share/registry/impress.xcd", "share/registry/graphicfilter.xcd", "share/registry/Langpack-en-US.xcd",
               "share/fonts/truetype/LiberationSans-Regular.ttf", "share/fonts/truetype/Carlito-Regular.ttf",
               "share/fonts/truetype/opens___.ttf", "share/config/images_colibre.zip"],
              ["help/en-US/a.html", "program/libsmlo.so", "program/libdbulo.so", "program/libpython3.12.so.1.0",
               "program/python-core-3.12.16/lib.py", "program/classes/a.jar", "program/libmysqlclo.so",
               "program/libpostgresql-sdbc-impllo.so", "program/libhwplo.so", "program/libmwaw-0.3-lo.so.3",
               "share/extensions/dict-en/en.aff", "share/fonts/truetype/Rubik-Regular.ttf",
               "share/fonts/truetype/DavidCLM-Bold.otf", "share/fonts/truetype/GenBasR.ttf",
               "share/config/images_sifr.zip"]),
    "mac": (["LibreOffice.app/Contents/MacOS/soffice", "LibreOffice.app/Contents/Frameworks/libmergedlo.dylib",
             "LibreOffice.app/Contents/Frameworks/libswlo.dylib", "LibreOffice.app/Contents/Frameworks/libsclo.dylib",
             "LibreOffice.app/Contents/Frameworks/libsdlo.dylib", "LibreOffice.app/Contents/Frameworks/libjvmfwklo.dylib",
             "LibreOffice.app/Contents/Resources/registry/main.xcd",
             "LibreOffice.app/Contents/Resources/registry/graphicfilter.xcd",
             "LibreOffice.app/Contents/Resources/registry/Langpack-en-US.xcd",
             "LibreOffice.app/Contents/Resources/resource/common/fonts/opens___.ttf",
             "LibreOffice.app/Contents/Resources/fonts/truetype/LiberationSans-Regular.ttf",
             "LibreOffice.app/Contents/Resources/en.lproj/a.strings", "LibreOffice.app/Contents/Info.plist"],
            ["LibreOffice.app/Contents/Resources/help/en-US/a.html",
             "LibreOffice.app/Contents/Frameworks/LibreOfficePython.framework/Versions/3.12/Python",
             "LibreOffice.app/Contents/Frameworks/libsmlo.dylib", "LibreOffice.app/Contents/Frameworks/libdbulo.dylib",
             "LibreOffice.app/Contents/Frameworks/libmwaw-0.3.3.dylib",
             "LibreOffice.app/Contents/Frameworks/libClp.1.dylib", "LibreOffice.app/Contents/Resources/java/a.jar",
             "LibreOffice.app/Contents/Resources/extensions/dict-en/en.aff",
             "LibreOffice.app/Contents/Resources/de.lproj/a.strings", "LibreOffice.app/Contents/Resources/resource/de/LC_MESSAGES/a.mo",
             "LibreOffice.app/Contents/Resources/registry/Langpack-de.xcd",
             "LibreOffice.app/Contents/Resources/fonts/truetype/Rubik-Regular.ttf",
             "LibreOffice.app/Contents/Resources/gallery/a.sdg", "LibreOffice.app/Contents/Resources/uno.py",
             "LibreOffice.app/Contents/_CodeSignature/CodeResources"]),
}


def fake_pe(imports: list[str]) -> bytes:
    """A minimal 64-bit PE whose import table names the given DLLs (one section holds the table)."""
    import struct
    names = b"".join(n.encode() + b"\0" for n in imports)
    n_desc = len(imports) + 1
    sec_va, sec_raw = 0x1000, 0x400
    desc = b"".join(struct.pack("<IIIII", 0, 0, 0, sec_va + 20 * n_desc + sum(len(n) + 1 for n in imports[:i]), 0)
                    for i in range(len(imports))) + b"\0" * 20
    sec = desc + names
    pe_off = 0x80
    opt = struct.pack("<H", 0x20B).ljust(112, b"\0") + struct.pack("<II", sec_va, len(desc)) + b"\0" * (16 * 8 - 8)
    opt = opt[:112 + 8] + struct.pack("<II", sec_va, len(desc)) + opt[112 + 16:]    # data directory 1: imports
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, len(opt), 0x2022)
    section = b".idata\0\0" + struct.pack("<IIIIIIHHI", len(sec), sec_va, len(sec), sec_raw, 0, 0, 0, 0, 0x40000040)
    head = b"MZ".ljust(0x3C, b"\0") + struct.pack("<I", pe_off)
    img = head.ljust(pe_off, b"\0") + b"PE\0\0" + coff + opt + section
    return img.ljust(sec_raw, b"\0") + sec


def fake_macho(libs: list[str]) -> bytes:
    import struct
    cmds = b""
    for lib in libs:
        name = lib.encode() + b"\0"
        size = (24 + len(name) + 7) // 8 * 8
        cmds += struct.pack("<IIIIII", 0xC, size, 24, 0, 0, 0) + name.ljust(size - 24, b"\0")
    return struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, len(libs), len(cmds), 0, 0) + cmds


def test_import_readers_see_what_a_binary_needs(tmp_path):
    pe = tmp_path / "a.dll"
    pe.write_bytes(fake_pe(["KERNEL32.dll", "wpd.dll", "mergedlo.dll"]))
    assert LP.pe_imports(pe) == ["kernel32.dll", "wpd.dll", "mergedlo.dll"]
    mo = tmp_path / "libx.dylib"
    mo.write_bytes(fake_macho(["/usr/lib/libSystem.B.dylib", "@loader_path/libwpd-0.10.10.dylib"]))
    assert LP.macho_imports(mo) == ["/usr/lib/libSystem.B.dylib", "@loader_path/libwpd-0.10.10.dylib"]
    assert LP.imports_of(mo, "mac") == ["libsystem.b.dylib", "libwpd-0.10.10.dylib"]
    (tmp_path / "t.txt").write_text("hello")
    assert LP.pe_imports(tmp_path / "t.txt") == [] and LP.macho_imports(tmp_path / "t.txt") == []


def test_a_library_the_rules_name_stays_when_a_remaining_file_imports_it(tmp_path):
    """On Windows and macOS libmergedlo links the WordPerfect importers: deleting them would stop soffice starting."""
    keep, drop = TREES["win"]
    for f in keep + drop:
        touch(tmp_path, f)
    (tmp_path / "program/mergedlo.dll").write_bytes(fake_pe(["kernel32.dll", "wpd.dll"]))
    touch(tmp_path, "program/wpd.dll")
    touch(tmp_path, "program/wpg.dll")
    out = LP.prune(tmp_path, "win", log=lambda *a: None)
    assert (tmp_path / "program/wpd.dll").exists() and "wpd.dll" in out["_kept"]
    assert not (tmp_path / "program/wpg.dll").exists()                    # nothing imports it
    assert not (tmp_path / "program/smlo.dll").exists()
    # a library imported only by a file that is itself pruned goes too
    touch(tmp_path, "program/helper.dll")
    (tmp_path / "program/mwaw.dll").write_bytes(fake_pe(["helper.dll"]))
    LP.prune(tmp_path, "win", log=lambda *a: None)
    assert not (tmp_path / "program/mwaw.dll").exists()


@pytest.mark.parametrize("plat", sorted(TREES))
def test_prune_removes_what_is_listed_and_keeps_the_rest(tmp_path, plat):
    keep, drop = TREES[plat]
    for f in keep + drop:
        touch(tmp_path, f)
    out = LP.prune(tmp_path, plat, log=lambda *a: None)
    for f in keep:
        assert (tmp_path / f).exists(), f"{f} must stay"
    for f in drop:
        assert not (tmp_path / f).exists(), f"{f} must go"
    assert out["_total"] > 0 and out["_after"] < out["_before"]


@pytest.mark.parametrize("plat", sorted(TREES))
def test_dry_run_deletes_nothing(tmp_path, plat):
    keep, drop = TREES[plat]
    for f in keep + drop:
        touch(tmp_path, f)
    out = LP.prune(tmp_path, plat, dry_run=True, log=lambda *a: None)
    assert all((tmp_path / f).exists() for f in keep + drop)
    assert out["_total"] >= len(drop) * 10


@pytest.mark.parametrize("plat", sorted(TREES))
def test_prune_refuses_when_a_required_file_is_gone(tmp_path, plat):
    keep, drop = TREES[plat]
    for f in keep + drop:
        if "mergedlo" not in f:
            touch(tmp_path, f)
    with pytest.raises(RuntimeError, match="required files"):
        LP.prune(tmp_path, plat, log=lambda *a: None)


def test_every_pattern_is_relative_and_resolved():
    for plat in LP.LAYOUT:
        pats = LP.patterns(plat)
        assert pats
        for cat, pat in pats:
            assert "{" not in pat and not pat.startswith(("/", ".")) and ".." not in pat, (plat, pat)
            if plat == "mac":
                assert pat.startswith("LibreOffice.app/Contents/"), pat


def test_the_engines_are_never_in_the_rules():
    """No rule may name the libraries or registry files the conversions run on."""
    keep = ("mergedlo", "swlo", "sclo", "sdlo", "oox", "graphicfilterlo", "svgfilterlo", "pdffilterlo", "localedata",
            "icudata", "skialo", "cairo", "main.xcd", "writer.xcd", "calc.xcd", "impress.xcd", "draw.xcd",
            "graphicfilter.xcd", "lingucomponent.xcd", "services.rdb", "types.rdb", "liblangtag", "numbertext")
    for plat in LP.LAYOUT:
        for cat, pat in LP.patterns(plat):
            leaf = pat.rsplit("/", 1)[-1].lower()
            assert not any(leaf == k or leaf.startswith(k + ".") or leaf.startswith("lib" + k + ".") for k in keep), pat


@pytest.mark.skipif(os.environ.get("MH_TEST_PRUNE_SYSTEM_LO") != "1", reason="slow: set MH_TEST_PRUNE_SYSTEM_LO=1")
def test_pruned_system_libreoffice_still_converts(tmp_path):
    src = Path("/usr/lib/libreoffice")
    if not (src / "program" / "soffice").is_file():
        pytest.skip("no system LibreOffice")
    dst = tmp_path / "lo"
    shutil.copytree(src, dst, symlinks=False, ignore_dangling_symlinks=True)
    for rc in (dst / "program").glob("*rc"):          # Debian's copy points at /usr/lib/libreoffice and /etc/libreoffice
        t = rc.read_text()
        rc.write_text(t.replace("file:///usr/lib/libreoffice", dst.as_uri()).replace("file:///etc/libreoffice/registry",
                                                                                      (dst / "share" / "registry").as_uri()))
    LP.prune(dst, "linux", log=lambda *a: None, check=False)
    r = subprocess.run([sys.executable, str(ROOT / "packaging" / "verify_lo_prune.py"), str(dst / "program" / "soffice")],
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
