"""Static check for a pruned LibreOffice that cannot be run on this machine (the Windows and macOS trees on a Linux
build host, or any tree before the first start): every library a program or library IMPORTS must still be there.

    python packaging/lo_deps_check.py <unpacked folder> win|mac|linux

Reads the import tables of every .exe/.dll, the load commands of every Mach-O file or the NEEDED
entries of every ELF file (readelf), and lists each dependency that is neither present in the tree nor an operating
system library (the readers are the ones lo_prune.py uses). Exit status 1 when anything is missing.
A library that is only loaded when a feature runs (dlopen, LoadLibrary) does not show up here: that is what
verify_lo_prune.py's conversions cover.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows libraries that are part of the operating system (or the universal C runtime): never in the tree
WIN_SYSTEM = ("api-ms-win-", "ext-ms-win-", "kernel32", "user32", "gdi32", "advapi32", "shell32", "ole32", "oleaut32",
              "ws2_32", "wsock32", "winmm", "comdlg32", "comctl32", "shlwapi", "version", "imm32", "crypt32", "ntdll",
              "msvcrt", "ucrtbase", "rpcrt4", "secur32", "setupapi", "wininet", "winspool", "uxtheme", "dwmapi", "d3d",
              "dxgi", "opengl32", "glu32", "bcrypt", "wldap32", "iphlpapi", "netapi32", "mpr", "psapi", "userenv",
              "usp10", "gdiplus", "msimg32", "windowscodecs", "dnsapi", "normaliz", "wtsapi32", "shfolder", "propsys",
              "dbghelp", "mscoree", "msi", "cabinet", "oledlg", "avrt", "mf", "mfplat", "mfreadwrite", "winhttp",
              "dwrite", "d2d1", "d3d11", "dcomp", "ncrypt", "sensapi", "credui", "urlmon", "wintrust", "powrprof",
              "twain_32", "gdi32full", "win32u", "oleacc", "wer", "httpapi", "activeds", "fontsub", "msvcp_win", "ucrt", "bcryptprimitives", "cfgmgr32", "avicap32", "msvfw32", "ole32")
LINUX_SYSTEM_PREFIXES = ("libc.so", "libm.so", "libdl.so", "libpthread.so", "librt.so", "libgcc_s", "libstdc++",
                         "ld-linux", "libX", "libxcb", "libGL", "libEGL", "libfontconfig", "libfreetype", "libz.so",
                         "libuuid", "libexpat", "libcups", "libdbus", "libglib", "libgobject", "libgio", "libgmodule",
                         "libpng", "libjpeg", "libxml2", "libxslt", "libicu", "libcrypto", "libssl", "libnss", "libnspr",
                         "libsmime", "libplc", "libplds", "libcairo", "libpixman", "libharfbuzz", "libgraphite",
                         "libSM", "libICE", "libsystemd", "libbz2", "liblzma", "libzstd", "libcurl", "libkrb5",
                         "libgssapi", "libk5crypto", "libcom_err", "libresolv", "libnsl", "libutil", "libselinux",
                         "libpcre", "libgpg", "libgcrypt", "libidn", "libldap", "liblber", "libsasl", "libwayland",
                         "libdrm", "libgbm", "libxkbcommon", "libatk", "libgdk", "libgtk", "libpango", "libwebp",
                         "libtiff", "libboost", "libmariadb", "libpq", "liblcms", "libfribidi", "libjbig", "libXss",
                         "libavahi")      # Impress links Avahi; every desktop Linux has it (docs/SETUP.md, Linux)


sys.path.insert(0, str(Path(__file__).resolve().parent))
import lo_prune  # noqa: E402  (the same import-table readers that prune() uses)


def pe_imports(p: Path):
    return lo_prune.pe_imports(p)


def macho_deps(p: Path):
    return lo_prune.macho_imports(p)


def elf_needed(p: Path):
    return lo_prune.elf_imports(p)


def check(root: Path, plat: str) -> dict[str, set[str]]:
    root = Path(root)
    have = {p.name.lower() for p in root.rglob("*") if p.is_file() or p.is_symlink()}
    missing: dict[str, set[str]] = {}
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        try:
            if plat == "win" and p.suffix.lower() in (".dll", ".exe", ".pyd", ".com"):
                deps = pe_imports(p)
                bad = [d for d in deps if d not in have and not d.startswith(WIN_SYSTEM)]
            elif plat == "mac" and p.read_bytes()[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf"):
                deps = macho_deps(p)
                bad = [d for d in deps if not d.startswith(("/usr/lib", "/System")) and Path(d).name.lower() not in have]
            elif plat == "linux" and p.read_bytes()[:4] == b"\x7fELF":
                deps = elf_needed(p)
                bad = [d for d in deps if d.lower() not in have and not d.startswith(LINUX_SYSTEM_PREFIXES)]
            else:
                continue
        except Exception as e:                                  # an unreadable or foreign file: report, do not stop
            missing.setdefault(f"(could not read {type(e).__name__})", set()).add(str(p.relative_to(root)))
            continue
        for d in bad:
            missing.setdefault(d, set()).add(str(p.relative_to(root)))
    return missing


if __name__ == "__main__":
    miss = check(Path(sys.argv[1]), sys.argv[2])
    for dep, users in sorted(miss.items()):
        print(f"MISSING {dep}: needed by {len(users)} file(s), e.g. {sorted(users)[0]}")
    print("every imported library is present" if not miss else f"{len(miss)} missing")
    sys.exit(1 if miss else 0)
