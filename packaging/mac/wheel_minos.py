"""Which macOS do the Mac builds of a Python package (or a downloaded program) need? Runs on Linux, no Mac required.

The Mac apps promise macOS 11. A wheel's tag does not settle it (pypdfium2 5.x says macosx_11_0 and needs 12, the
onnxruntime and opencv wheels needed 12 to 15 and broke a release), so this reads the real thing: the load commands
of every Mach-O file inside the wheel, this architecture's slice of universal files (packaging/mac/check_minos.py does
the same on the finished app). Standard library plus pip.

  python packaging/mac/wheel_minos.py scan FILE... [--arch arm64|x86_64]     wheels or zip files already downloaded
  python packaging/mac/wheel_minos.py closure -r REQ... [-c CONSTRAINTS]     download the Mac wheels of every package
        [--arch arm64|x86_64|both] [--record]                                  these list, dependencies included, and
                                                                              scan them (--record writes the result
                                                                              to packaging/mac/verified_minos.json)
  python packaging/mac/wheel_minos.py programs [--only uv] [--record]        download the pinned ffmpeg, pandoc and uv wheels
                                                                              (packaging/bundled_tools.py), read the
                                                                              program inside and the libraries it loads
  python packaging/mac/wheel_minos.py check                                  re-download what verified_minos.json lists
                                                                              and fail when a wheel changed or needs
                                                                              more than the floor

verified_minos.json is what the offline test (app/tests/test_macos_floor.py) holds the requirements files to: a package
that is not in it, or needs more than 11.0 there, fails the test before it can fail a release build.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_minos import minos, parse  # noqa: E402
from old_macos_wheels import platforms  # noqa: E402

FLOOR = (11, 0)
# Packages that may need more than the floor, and why that is safe. {package: (highest macOS allowed, reason)}.
# An exemption has to be a feature the app turns off on older systems with a message, or a file the build replaces.
EXEMPT = {
    "playwright": ((13, 5), "its driver/node needs macOS 13.5; the release build replaces it with the app's own Node 22 "
                            "(macOS 11), see .github/workflows/release.yml"),
}
PYTHON_VERSION = "3.12"                      # the bundled runtime (rt-mac, python-build-standalone cpython 3.12)
ARCHS = ("arm64", "x86_64")
RECORD = HERE / "verified_minos.json"
MACHO_MAGICS = {b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}


def scan_zip(path: Path, arch: str | None = None) -> dict:
    """{member name: (major, minor)} for every Mach-O file in a wheel or zip (members without load commands left out)."""
    out = {}
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
        for i, info in enumerate(z.infolist()):
            if info.is_dir() or info.file_size < 32:
                continue
            with z.open(info) as f:
                head = f.read(4)
                if head not in MACHO_MAGICS:
                    continue
                dst = Path(tmp) / str(i)
                dst.write_bytes(head + f.read())
            v = minos(dst, arch)
            if v:
                out[info.filename] = v
    return out


LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB = 0xC, 0x80000018, 0x8000001F, 0x80000023
SYSTEM_LIB_PREFIXES = ("/usr/lib/", "/System/Library/")


def needed_libs(data: bytes, arch: str | None = None) -> list[str]:
    """The dynamic libraries a Mach-O file loads (their install names), this architecture's slice of a universal file.
    A program that only loads /usr/lib and /System/Library libraries runs on a Mac with nothing else installed."""
    from check_minos import CPU, THIN

    def thin(off):
        magic = struct.unpack_from("<I", data, off)[0]
        if magic not in THIN:
            return []
        e, hdr = THIN[magic]
        ncmds = struct.unpack_from(e + "I", data, off + 16)[0]
        p, out = off + hdr, []
        for _ in range(ncmds):
            cmd, size = struct.unpack_from(e + "II", data, p)
            if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB):
                nameoff = struct.unpack_from(e + "I", data, p + 8)[0]
                out.append(data[p + nameoff:p + size].split(b"\0")[0].decode("utf-8", "replace"))
            if size <= 0:
                break
            p += size
        return out
    if struct.unpack(">I", data[:4])[0] == 0xCAFEBABE:
        n = struct.unpack_from(">I", data, 4)[0]
        sl = [struct.unpack_from(">III", data, 8 + 20 * i) for i in range(n)]
        pick = [off for cpu, _, off in sl if cpu == CPU.get(arch or "")] or [off for _, _, off in sl]
        return thin(pick[0])
    return thin(0)


def highest(found: dict):
    return max(found.values()) if found else None


def allowed(name: str, v) -> bool:
    """True when a package whose Mach-O files need v (or nothing) may ship: at most the floor, or its exemption."""
    if not v or tuple(v) <= FLOOR:
        return True
    limit = EXEMPT.get(name.split("==")[0])
    return bool(limit and tuple(v) <= limit[0])


def show(v) -> str:
    return "none (no Mach-O file)" if not v else f"{v[0]}.{v[1]}"


def closure(requirements: list[str], arch: str, dest: Path, constraints: list[str] = ()) -> list[Path]:
    """pip download the macOS wheels (dependencies too, for the bundled Python 3.12) that run on macOS 11 by their tags."""
    cmd = [sys.executable, "-m", "pip", "download", "-q", "--only-binary=:all:", "--python-version", PYTHON_VERSION,
           "--implementation", "cp", "-d", str(dest)]
    for p in platforms(arch, FLOOR):
        cmd += ["--platform", p]
    for r in requirements:
        cmd += ["-r", r] if Path(r).is_file() else [r]
    for c in constraints:
        cmd += ["-c", c]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"pip could not get macOS wheels for {requirements} on {arch}:\n{r.stderr[-1500:]}")
    return sorted(dest.glob("*.whl"))


def name_version(whl: Path) -> tuple:
    n, v = whl.name.split("-")[:2]
    return re.sub(r"[-_.]+", "-", n).lower(), v


def scan_closure(requirements, arch, constraints=()) -> dict:
    """{ 'name==version': {"minos": [major, minor] | None, "file": wheel name, "worst": member} } for the closure."""
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for whl in closure(requirements, arch, Path(tmp), constraints):
            n, v = name_version(whl)
            found = scan_zip(whl, arch)
            worst = max(found, key=found.get) if found else None
            res[f"{n}=={v}"] = {"minos": list(highest(found)) if found else None, "file": whl.name, "worst": worst}
    return res


def _bundled_tools():
    spec = importlib.util.spec_from_file_location("mp_bundled_tools", HERE.parent / "bundled_tools.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def scan_programs(cache: Path, only=()) -> dict:
    """{program: {platform key: {minos, libs, wheel, sha256, member}}} for the Mac builds of every program that
    bundled_tools.WHEEL_PROGRAMS takes out of a wheel (or only those named), read from the pinned, hash-checked wheel.
    A program with `also` members (uvx beside uv) is held to the highest macOS any of them needs."""
    bt = _bundled_tools()
    out: dict = {}
    for name, spec in bt.WHEEL_PROGRAMS.items():
        if only and name not in only:
            continue
        for key, (url, sha, size) in spec["pins"].items():
            if not key.startswith("mac-"):
                continue
            arch = "arm64" if key == "mac-arm64" else "x86_64"
            whl = cache / url.rsplit("/", 1)[1]
            if not (whl.exists() and bt.sha256_of(whl) == sha):
                bt.download_verified([url], whl, sha, size, label=f"{name} {key}")
            with zipfile.ZipFile(whl) as z:
                hit = next(i for i in z.infolist() if re.fullmatch(spec["member"], i.filename))
                data = z.read(hit)
                extra = [z.read(next(i for i in z.infolist() if re.fullmatch(rx, i.filename)))
                         for rx in spec.get("also", {}).values()]
            v, libs = None, set()
            for blob in [data, *extra]:
                with tempfile.TemporaryDirectory() as tmp:
                    f = Path(tmp) / "prog"
                    f.write_bytes(blob)
                    got = minos(f, arch)
                v = max(v, got) if v and got else (v or got)
                libs |= set(needed_libs(blob, arch))
            out.setdefault(name, {})[key] = {"minos": list(v) if v else None, "libs": sorted(libs), "wheel": whl.name,
                                              "sha256": sha, "member": hit.filename}
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("scan", "closure", "check", "programs"):
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    arch = "both"
    if "--arch" in rest:
        i = rest.index("--arch")
        arch = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    archs = ARCHS if arch == "both" else (arch,)
    if cmd == "scan":
        bad = 0
        for f in rest:
            for a in archs:
                found = scan_zip(Path(f), a)
                v = highest(found)
                over = v and v > FLOOR
                bad += bool(over)
                print(f"{'ERROR' if over else 'ok   '} {Path(f).name} [{a}]: needs macOS {show(v)}"
                      + (f" ({max(found, key=found.get)})" if over else ""))
        return 1 if bad else 0
    if cmd == "programs":
        only = rest[rest.index("--only") + 1].split(",") if "--only" in rest else ()
        with tempfile.TemporaryDirectory() as tmp:
            res = scan_programs(Path(os.environ.get("MH_WHEEL_CACHE") or tmp), only)
        for name, per in res.items():
            for key, d in per.items():
                foreign = [x for x in d["libs"] if not x.startswith(SYSTEM_LIB_PREFIXES)]
                print(f"{name} {key}: macOS {show(tuple(d['minos']) if d['minos'] else None)}, loads {len(d['libs'])} libraries"
                      + (f", NOT from the system: {foreign}" if foreign else ", all from the system"))
        if "--record" in rest:
            old = json.loads(RECORD.read_text()) if RECORD.exists() else {}
            old.setdefault("programs", {}).update(res)
            RECORD.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n")
            print(f"wrote {RECORD.name}")
        return 0
    if cmd == "closure":
        record = "--record" in rest
        reqs, cons, it = [], [], iter([x for x in rest if x != "--record"])
        for x in it:
            if x == "-r":
                reqs.append(next(it))
            elif x == "-c":
                cons.append(next(it))
            else:
                reqs.append(x)
        out, bad = {}, 0
        for a in archs:
            out[a] = scan_closure(reqs, a, cons)
            for k, d in sorted(out[a].items()):
                over = not allowed(k, d["minos"])
                bad += bool(over)
                if over or d["minos"]:
                    print(f"{'ERROR' if over else 'ok   '} {k} [{a}]: macOS {show(tuple(d['minos']) if d['minos'] else None)}"
                          + (f" ({d['worst']})" if over else ""))
        if record:
            old = json.loads(RECORD.read_text()) if RECORD.exists() else {}
            for a in archs:
                old.setdefault(a, {}).update(out[a])
            RECORD.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n")
            print(f"wrote {RECORD.name}")
        return 1 if bad else 0
    # check: every recorded package must still download and scan the same (a re-uploaded wheel is caught)
    old = json.loads(RECORD.read_text())
    old.pop("programs", None)
    bad = 0
    for a in archs:
        reqs = list(old.get(a, {}))
        got = scan_closure(reqs, a)
        for k, d in old.get(a, {}).items():
            g = got.get(k)
            if not g or g["minos"] != d["minos"]:
                bad += 1
                print(f"ERROR {k} [{a}]: recorded {d['minos']}, now {g and g['minos']}")
    print("verified_minos.json matches the published wheels" if not bad else f"{bad} package(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
