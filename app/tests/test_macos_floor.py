"""The Mac apps promise macOS 11. Every Python package and program the app carries must have been READ (the Mach-O load
commands inside the wheel, not its tag) and found to need macOS 11 or less, or be a named exemption. 0.0.10 broke the
release build because new packages (opencv, onnxruntime) needed macOS 12 to 15; these tests fail on a Linux laptop
instead, before any release build starts. packaging/mac/wheel_minos.py makes the record (verified_minos.json); to add or
move a package: edit the requirements, then run
    python packaging/mac/wheel_minos.py closure -r packaging/requirements-cli.txt -r <pywebview, playwright> \\
        -c packaging/mac/constraints.txt --record
(no Mac needed, it downloads the macOS wheels) and commit the new record."""
import importlib.util
import json
import re
import struct
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAC = ROOT / "packaging" / "mac"


def _load(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


WM = _load("wheel_minos", MAC / "wheel_minos.py")
BT = _load("mp_bundled_tools_floor", ROOT / "packaging" / "bundled_tools.py")
RECORD = json.loads((MAC / "verified_minos.json").read_text(encoding="utf-8"))
X86, ARM = 0x01000007, 0x0100000C


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_lines(path: Path):
    """[(canonical name, pinned version or None, marker)] of a requirements or constraints file."""
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        marker = line.split(";", 1)[1].strip() if ";" in line else ""
        spec = line.split(";", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(==\s*([^\s,]+))?", spec)
        out.append((canon(m.group(1)), (m.group(3) or None), marker))
    return out


# ---- the reader itself (synthetic Mach-O files; the real check on finished apps is packaging/mac/check_minos.py) ----
def macho(cpu: int, minos: tuple, libs=("/usr/lib/libSystem.B.dylib",)) -> bytes:
    cmds = struct.pack("<IIIIII", 0x32, 24, 1, (minos[0] << 16) | (minos[1] << 8), 0, 0)
    for lib in libs:
        name = lib.encode() + b"\0"
        name += b"\0" * (-(24 + len(name)) % 8)
        cmds += struct.pack("<IIIIII", 0xC, 24 + len(name), 24, 2, 0, 0) + name
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, 3, 6, 1 + len(libs), len(cmds), 0, 0) + cmds


def wheel(tmp_path, name: str, members: dict) -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return p


def test_a_wheel_is_judged_by_its_files_not_its_tag(tmp_path):
    lying = wheel(tmp_path, "pkg-1-py3-none-macosx_11_0_arm64.whl",
                  {"pkg/__init__.py": b"x" * 100, "pkg/_ext.so": macho(ARM, (15, 0)), "pkg/ok.so": macho(ARM, (11, 0))})
    found = WM.scan_zip(lying, "arm64")
    assert found == {"pkg/_ext.so": (15, 0), "pkg/ok.so": (11, 0)}
    assert WM.highest(found) == (15, 0) and not WM.allowed("pkg", WM.highest(found))
    assert WM.scan_zip(wheel(tmp_path, "pure-1-py3-none-any.whl", {"a.py": b"print(1)" * 20}), "arm64") == {}


def test_the_floor_and_the_exemptions():
    assert WM.FLOOR == (11, 0)
    assert WM.allowed("numpy", (10, 13)) and WM.allowed("numpy", (11, 0)) and WM.allowed("numpy", None)
    assert not WM.allowed("onnxruntime", (14, 0)) and not WM.allowed("opencv-python-headless", (12, 0))
    assert WM.allowed("playwright", (13, 5)) and not WM.allowed("playwright", (14, 0))      # exempt only up to its limit
    assert all(isinstance(v[1], str) and len(v[1]) > 30 for v in WM.EXEMPT.values())       # every exemption says why


def test_libraries_a_program_loads_are_read(tmp_path):
    prog = macho(ARM, (12, 0), libs=("/usr/lib/libz.1.dylib", "/opt/homebrew/lib/libfoo.dylib"))
    libs = WM.needed_libs(prog, "arm64")
    assert libs == ["/usr/lib/libz.1.dylib", "/opt/homebrew/lib/libfoo.dylib"]
    assert [x for x in libs if not x.startswith(WM.SYSTEM_LIB_PREFIXES)] == ["/opt/homebrew/lib/libfoo.dylib"]


# ---- the record, and the requirements the app is built from -------------------------------------------------------------
def test_every_recorded_package_needs_macos_11_or_less():
    for arch in WM.ARCHS:
        assert RECORD[arch], arch
        for key, d in RECORD[arch].items():
            assert WM.allowed(key, d["minos"]), (f"{key} [{arch}] needs macOS {d['minos']} ({d['worst']}): the app promises "
                                                 f"11. Pin an older version or add an explained exemption "
                                                 f"(packaging/mac/wheel_minos.py EXEMPT).")


def test_every_package_the_app_installs_was_read():
    """Adding a package to the app's requirements without reading its Mac build fails here, not in the release build."""
    wanted = requirement_lines(ROOT / "packaging" / "requirements-cli.txt")        # what the release installs
    assert wanted
    for arch in WM.ARCHS:
        have = {k.split("==")[0]: k.split("==")[1] for k in RECORD[arch]}
        for name, pin, marker in wanted:
            if "sys_platform" in marker and "darwin" not in marker:
                continue                                                     # not installed on a Mac
            assert name in have, (f"{name} is in the requirements but was never read for macOS ({arch}). Run "
                                  "packaging/mac/wheel_minos.py closure ... --record (see this file's docstring).")
            if pin:
                assert have[name] == pin, f"{name}=={pin} is pinned but the record read {have[name]} ({arch}): read it again"


def test_runtime_and_pinned_lists_agree_with_the_install_list():
    """requirements-pinned.txt (exact) and requirements-runtime.txt (loose) mirror what the build installs from
    requirements-cli.txt: the packages added for 0.0.11 are in all three, at the same version."""
    cli = {n: p for n, p, _ in requirement_lines(ROOT / "packaging" / "requirements-cli.txt")}
    pinned = {n: p for n, p, _ in requirement_lines(ROOT / "packaging" / "requirements-pinned.txt")}
    runtime = {n for n, _, _ in requirement_lines(ROOT / "packaging" / "requirements-runtime.txt")}
    for name in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "nbformat", "nbclient", "nbconvert", "ipykernel",
                 "debugpy", "pillow-heif", "matplotlib"):
        assert cli[name], f"{name} must be pinned with == in requirements-cli.txt"
        assert pinned.get(name) == cli[name], name
        assert name in runtime, name


def test_native_packages_the_app_added_are_pinned_where_the_mac_build_reads_them():
    """The Mac runner is a newer macOS than 11, so pip there picks the newest build: the packages whose newer versions
    need macOS 12 to 15 must be pinned to the versions that were read."""
    cli = {n: p for n, p, _ in requirement_lines(ROOT / "packaging" / "requirements-cli.txt")}
    cons = {n: p for n, p, _ in requirement_lines(MAC / "constraints.txt")}
    pinned_somewhere = {**cons, **{n: p for n, p in cli.items() if p}}
    for name in ("onnxruntime", "debugpy", "protobuf", "ctranslate2", "av", "tokenizers", "hf-xet", "pyzmq", "tornado",
                 "psutil", "markupsafe", "rpds-py", "contourpy", "kiwisolver", "pillow-heif", "matplotlib", "pypdfium2"):
        want = pinned_somewhere.get(name)
        assert want, f"{name} has a macOS 12+ build in some version: pin the one that was read"
        for arch in WM.ARCHS:
            assert f"{name}=={want}" in RECORD[arch], f"{name}=={want} ({arch}) was not read; the record has " \
                f"{[k for k in RECORD[arch] if k.startswith(name + '==')]}"


def test_the_known_bad_versions_stay_out():
    """These need more than macOS 11 on at least one architecture (read from their files); pinning them breaks the Mac build."""
    cli = {n: p for n, p, _ in requirement_lines(ROOT / "packaging" / "requirements-cli.txt")}
    cons = {n: p for n, p, _ in requirement_lines(MAC / "constraints.txt")}
    assert cli["onnxruntime"] == "1.19.2"                       # 1.20 and later: macOS 13 or 14
    assert cli["debugpy"] == "1.8.5"                            # 1.8.9 and later ship an attach helper for macOS 14 or 15
    assert cons["protobuf"] == "6.33.5"                         # 7.x: macOS 12
    names = {n for n, _, _ in requirement_lines(ROOT / "packaging" / "requirements-cli.txt")}
    assert not names & {"opencv-python", "opencv-python-headless", "rapidocr"}      # OCR: opencv needs macOS 12 on Intel


# ---- the programs taken out of wheels -----------------------------------------------------------------------------------
def test_ffmpeg_and_pandoc_pins_are_the_files_that_were_read():
    progs = RECORD["programs"]
    for name, spec in BT.WHEEL_PROGRAMS.items():
        for key in ("mac-arm64", "mac-x64"):
            url, sha, _size = spec["pins"][key]
            rec = progs[name][key]
            assert rec["sha256"] == sha and url.endswith(rec["wheel"]), (name, key, "the pinned wheel is not the one that was read")
            assert re.fullmatch(r"[0-9a-f]{64}", sha)


def test_every_mac_program_needs_macos_11_except_the_one_exemption():
    progs = RECORD["programs"]
    for name in ("ffmpeg", "pandoc", "uv"):
        for key in ("mac-arm64", "mac-x64"):
            rec = progs[name][key]
            limit = (12, 0) if (name, key) == ("ffmpeg", "mac-arm64") else (11, 0)
            assert tuple(rec["minos"]) <= limit, (name, key, rec["minos"])
            assert all(x.startswith(WM.SYSTEM_LIB_PREFIXES) for x in rec["libs"]), \
                f"{name} {key} loads a library the Mac may not have: {[x for x in rec['libs'] if not x.startswith(WM.SYSTEM_LIB_PREFIXES)]}"
    from meshpool_harness import bundled
    assert tuple(progs["ffmpeg"]["mac-arm64"]["minos"]) == bundled.FFMPEG_ARM64_NEEDS_MACOS      # the runtime message says 12
    assert BT.FFMPEG_MAC_ARM64_NEEDS == "12.0"


def test_the_release_workflow_exempts_only_what_is_read_to_need_more():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "vendor/browsers/*=13.0" in wf and "vendor/ffmpeg/*=12.0" in wf
    exempt = re.findall(r"--exempt '([^']+)'", wf)
    assert len(exempt) == 2, f"every other file must pass at 11.0: {exempt}"
