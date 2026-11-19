"""Make the bundled LibreOffice small: delete everything Meshpool does not use after the pinned release is unpacked.

Meshpool runs LibreOffice only headless, for these jobs: draw a .pptx or .docx as PDF or PNG (render checks, slide
previews, convert_document) and recalculate an .xlsx (sheets QA and the forge validators). Nothing opens a window, so
the installers do not need the user interface, help, Base (databases), Math (formulas), Java, the Python-UNO runtime,
extensions and dictionaries, other UI languages, galleries, templates, spare icon themes, importers for formats
Meshpool never reads, or the spare fonts. PDF import stays (xpdfimport, pdfimportlo): convert_document turns a PDF
into a Word file with it. build_all.py calls prune() right after the download is unpacked (macOS and
Windows); packaging/verify_lo_prune.py proves that a pruned copy still converts (it was run on Linux against the same
release, see docs/plan/BUNDLED_TOOLS.md).

Rules are glob patterns per path segment, relative to the unpacked folder ("/" separators). A pattern that matches
nothing is fine, so one list serves every release and platform. Placeholders:
  {C}          install root: "" (Windows, Linux), LibreOffice.app/Contents (macOS)
  {S}          shared data: share (Windows, Linux), LibreOffice.app/Contents/Resources (macOS)
  {P}          program data and scripts: program (Windows, Linux), LibreOffice.app/Contents/Resources (macOS)
  {L}          program libraries: program, or LibreOffice.app/Contents/Frameworks (macOS)
  {lib:stem}   a library: program/<stem>.dll (Windows), program/lib<stem>.so* (Linux), Frameworks/lib<stem>*.dylib
  {exe:name}   a program: program/<name>.exe (Windows), program/<name> (Linux), Contents/MacOS/<name> (macOS)
Platforms: "win", "mac", "linux" (the proof copy of the same release; also the Debian copy the tests use).
PROTECT lists names that survive any pattern (the English language pack, one icon theme).
MUST_KEEP lists files whose absence afterwards means the rules are wrong for this release: prune() then raises.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
from pathlib import Path

LAYOUT = {
    "win": {"C": "", "S": "share", "P": "program", "L": "program", "X": "program", "pre": "", "suf": ".dll", "exe": ".exe"},
    "linux": {"C": "", "S": "share", "P": "program", "L": "program", "X": "program", "pre": "lib", "suf": ".so*", "exe": ""},
    "mac": {"C": "LibreOffice.app/Contents", "S": "LibreOffice.app/Contents/Resources",
            "P": "LibreOffice.app/Contents/Resources", "L": "LibreOffice.app/Contents/Frameworks",
            "X": "LibreOffice.app/Contents/MacOS", "pre": "lib", "suf": "*.dylib", "exe": ""},
}

# "common" is program/resource/common: OpenSymbol (the bullet font) lives there; the language folders are its siblings
PROTECT = {"Langpack-en-US.xcd", "fcfg_langpack_en-US.xcd", "images_colibre.zip", "en.lproj", "common",
           "simpress", "tabviewbar.ui"}

# ---- libraries: stems (libjvmaccesslo, libjvmfwklo, libclucene, libclewlo and libdbalo stay: soffice.bin or the engine
# libraries are linked against them, so removing one stops LibreOffice from starting)
#  (wildcards allowed) of program libraries that serve only what Meshpool never does
BASE = ["abplo", "biblo", "dbahsqllo", "dbalo", "sdbtlo", "dbaselo", "dbaxmllo", "dbplo", "dbulo", "evoablo", "firebird_sdbclo",
        "flatlo", "hsqldb", "jdbclo", "mysql_jdbclo", "mysqlclo", "odbclo", "postgresql-sdbc*", "rptlo", "rptuilo",
        "Engine12", "ifbclient", "fbclient*", "pcrlo", "mozbootstraplo", "macab*", "mork*"]
MATH = ["smlo", "smdlo"]
JAVA = ["javaloaderlo", "javavmlo", "java_uno", "juh", "juhx", "officebean"]
PYTHON = ["python3*", "pythonloaderlo", "pyuno*"]
FOREIGN = ["hwplo", "lwpftlo", "wpft*lo", "writerperfectlo", "mwaw*", "etonyek*", "wps*", "wpd*", "wpg*", "revenge*",
           "librevenge*", "odfgen*", "staroffice*", "libstaroffice*", "xsltfilterlo", "t602filterlo",
           "OGLTranslo", "PresentationMinimizerlo", "solverlo", "lpsolve55", "CoinMP*", "Cbc*", "Clp*", "Cgl*", "Osi*",
           "CoinUtils*", "pricinglo", "scnlo", "cmdmaillo", "vbaobjlo", "vbaswobjlo"]
UI = ["cuilo", "swuilo", "scuilo", "sduilo"]             # dialog libraries: nothing opens a dialog headless
MISC = ["updchklo", "updatecheckuilo", "losessioninstalllo", "migrationoo2lo", "migrationoo3lo", "deploymentgui",
        "ucpchelp1", "ucpcmis1lo", "ucpdav1", "ucpgio1lo", "kf5be1lo", "ldapbe2lo", "libreofficekitgtk",
        "vclplug_gtk3_kde5lo", "vclplug_gtk3lo", "vclplug_gtk4lo", "vclplug_kf5lo", "vclplug_qt5lo", "vclplug_qt6lo",
        "avmediagst", "avmediagtk", "avmediaqt6", "unopkgapp", "AppleRemotelo", "OOoSpotlight"]

# ---- fonts LibreOffice carries that Meshpool does not need. Kept: Liberation (Arial, Times New Roman, Courier New),
# Carlito (Calibri), Caladea (Cambria), DejaVu Sans, Serif and Mono (fallback), OpenSymbol (bullets), Noto Sans and
# Serif, Noto Naskh, Noto Sans Arabic, Amiri, Scheherazade and the Hebrew Noto faces. Selawik (Segoe UI) comes from the
# engine's own fonts folder (engines/forge/fonts), not from here.
FONTS_DROP = ["Alef-*", "David*", "Frank*", "GenBas*", "GenBkBas*", "LinBiolinum*", "LinLibertine*", "Miriam*",
              "Nachlieli*", "NotoSansArmenian*", "NotoSerifArmenian*", "NotoSansGeorgian*", "NotoSerifGeorgian*",
              "NotoSansLao*", "NotoSerifLao*", "NotoSansLisu*", "ReemKufi*", "Rubik*", "DejaVuSansCondensed*",
              "DejaVuSerifCondensed*", "DejaVuMathTeXGyre*", "DejaVuSans-ExtraLight*", "NotoKufiArabic*",
              "EmojiOne*", "Gentium*"]

RULES: list[tuple] = [
    # documentation and branding the full desktop app shows
    ("help, readmes, splash", "{S}/help"), ("help, readmes, splash", "{C}/help"), ("help, readmes, splash", "{C}/readmes"),
    ("help, readmes, splash", "{S}/readmes"), ("help, readmes, splash", "{C}/CREDITS.fodt"),
    ("help, readmes, splash", "{S}/CREDITS.fodt"), ("help, readmes, splash", "{S}/tipoftheday"),
    ("help, readmes, splash", "{P}/intro*.png"), ("help, readmes, splash", "{C}/update-settings.ini"),
    ("help, readmes, splash", "{S}/registry/*.sample"), ("help, readmes, splash", "{C}/_CodeSignature"),
    ("help, readmes, splash", "{C}/LICENSEs"),
    # galleries, templates, wizards, autocorrect lists, Basic libraries, export stylesheets
    ("gallery, templates, wizards, Basic", "{S}/gallery"), ("gallery, templates, wizards, Basic", "{S}/template"),
    ("gallery, templates, wizards, Basic", "{S}/autotext"), ("gallery, templates, wizards, Basic", "{S}/wizards"),
    ("gallery, templates, wizards, Basic", "{S}/labels"), ("gallery, templates, wizards, Basic", "{S}/wordbook"),
    ("gallery, templates, wizards, Basic", "{S}/palette"), ("gallery, templates, wizards, Basic", "{S}/classification"),
    ("gallery, templates, wizards, Basic", "{S}/toolbarmode"), ("gallery, templates, wizards, Basic", "{S}/autocorr"),
    ("gallery, templates, wizards, Basic", "{S}/Scripts"), ("gallery, templates, wizards, Basic", "{S}/basic"),
    ("gallery, templates, wizards, Basic", "{S}/xslt"), ("gallery, templates, wizards, Basic", "{S}/fingerprint"),
    ("gallery, templates, wizards, Basic", "{S}/firebird"), ("gallery, templates, wizards, Basic", "{S}/glade"),
    # extensions (dictionaries, hyphenation, thesaurus, report builder, solver ...), Java classes, JRE settings
    ("extensions, dictionaries, Java", "{S}/extensions/*"), ("extensions, dictionaries, Java", "{S}/uno_packages/cache"),
    ("extensions, dictionaries, Java", "{P}/classes"), ("extensions, dictionaries, Java", "{P}/JREProperties.class"),
    ("extensions, dictionaries, Java", "{P}/javavendors.xml"), ("extensions, dictionaries, Java", "{P}/jvmfwk3*"),
    ("extensions, dictionaries, Java", "{S}/java"),
    # the user interface: dialogs, toolbars, spare icon themes, every UI language but English
    # soffice.cfg: LibreOffice will not load a document without a few of its small .ui files (found by deleting them
    # one by one: svt/ui/scrollbars.ui, svt/ui/tabbuttons.ui, simpress/styles.xml, modules/simpress/ui/tabviewbar.ui).
    # Kept: sfx, svt, uui, vcl, editeng, formula and simpress (about 2 MB). Gone: every dialog, menu and toolbar of
    # the applications (about 35 MB).
    ("user interface and other languages", "{S}/config/soffice.cfg/cui"),
    ("user interface and other languages", "{S}/config/soffice.cfg/dbaccess"),
    ("user interface and other languages", "{S}/config/soffice.cfg/desktop"),
    ("user interface and other languages", "{S}/config/soffice.cfg/filter"),
    ("user interface and other languages", "{S}/config/soffice.cfg/fps"),
    ("user interface and other languages", "{S}/config/soffice.cfg/svx"),
    ("user interface and other languages", "{S}/config/soffice.cfg/writerperfect"),
    ("user interface and other languages", "{S}/config/soffice.cfg/xmlsec"),
    ("user interface and other languages", "{S}/config/soffice.cfg/modules/*"),
    ("user interface and other languages", "{S}/config/soffice.cfg/modules/simpress/menubar"),
    ("user interface and other languages", "{S}/config/soffice.cfg/modules/simpress/popupmenu"),
    ("user interface and other languages", "{S}/config/soffice.cfg/modules/simpress/toolbar"),
    ("user interface and other languages", "{S}/config/soffice.cfg/modules/simpress/statusbar"),
    ("user interface and other languages", "{S}/config/soffice.cfg/modules/simpress/ui/*"),
    ("user interface and other languages", "{S}/config/wizard"),
    ("user interface and other languages", "{S}/config/images_*.zip"),
    ("user interface and other languages", "{P}/resource/*"),
    ("user interface and other languages", "{S}/registry/Langpack-*.xcd"),
    ("user interface and other languages", "{S}/registry/res/fcfg_langpack_*.xcd"),
    ("user interface and other languages", "{S}/registry/res/registry_*.xcd"),
    ("user interface and other languages", "{C}/Resources/*.lproj"), ("user interface and other languages", "{S}/*.icns"),
    ("user interface and other languages", "{S}/MainMenu.nib"),
    # Base and Math configuration, importers and other desktop-only configuration
    ("Base, Math and desktop configuration", "{S}/registry/base.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/math.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/reportbuilder.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/postgresql.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/librelogo.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/xsltfilter.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/onlineupdate.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/gnome.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/kde*.xcd"),
    ("Base, Math and desktop configuration", "{S}/registry/ogltrans.xcd"),
    # Python-UNO runtime and the scripts that use it
    ("Python-UNO", "{P}/python"), ("Python-UNO", "{P}/python-core-*"), ("Python-UNO", "{P}/pyuno*"), ("Python-UNO", "{P}/*.py"),
    ("Python-UNO", "{P}/*.pyi"), ("Python-UNO", "{P}/__pycache__"), ("Python-UNO", "{P}/pythonloader.*"),
    ("Python-UNO", "{P}/wizards"), ("Python-UNO", "{L}/LibreOfficePython.framework"),
    ("Python-UNO", "{exe:python}"), ("Python-UNO", "{exe:python.bin}"), ("Python-UNO", "{exe:pythonw}"),
    # programs that are not the converter: other module launchers, updater, tools
    ("other programs", "{exe:updater}"), ("other programs", "{exe:update_service}"),
    ("other programs", "{exe:mar}"), ("other programs", "{exe:odbcconfig}"), ("other programs", "{exe:quickstart}"),
    ("other programs", "{exe:twain32shim}"), ("other programs", "{exe:spsupp_helper}"), ("other programs", "{exe:minidump_upload}"),
    ("other programs", "{exe:gengal}"), ("other programs", "{exe:gengal.bin}"), ("other programs", "{exe:gpgme-w32spawn}"),
    ("other programs", "{exe:lo_kde5filepicker}"), ("other programs", "{exe:opencltest}"),
    ("other programs", "{exe:unopkg}"), ("other programs", "{exe:unopkg.bin}"), ("other programs", "{exe:unopkg.com}"),
    ("other programs", "{exe:soffice_safe}"), ("other programs", "{exe:sweb}"), ("other programs", "{P}/intl"),
    ("other programs", "{P}/shlxthdl"), ("other programs", "{L}/intl"), ("other programs", "{P}/opencl"), ("other programs", "{P}/opengl"),
]
RULES += [("fonts LibreOffice carries that Meshpool does not use", f"{{S}}/fonts/truetype/{p}") for p in FONTS_DROP]
RULES += [("Base, Math and desktop configuration", "{lib:%s}" % s) for s in BASE + MATH]
RULES += [("Java and Python libraries", "{lib:%s}" % s) for s in JAVA + PYTHON]
RULES += [("importers for formats Meshpool never reads", "{lib:%s}" % s) for s in FOREIGN]
RULES += [("updates, remote file systems, other windowing systems", "{lib:%s}" % s) for s in MISC]
RULES += [("user interface libraries", "{lib:%s}" % s) for s in UI]

# What must still be there afterwards (prune() raises otherwise).
MUST_KEEP = {
    "win": ["program/soffice.exe", "program/soffice.bin", "program/mergedlo.dll", "program/swlo.dll", "program/sclo.dll",
            "program/sdlo.dll", "program/services.rdb", "program/types.rdb", "share/registry/main.xcd",
            "share/registry/writer.xcd", "share/registry/calc.xcd", "share/registry/impress.xcd",
            "share/registry/graphicfilter.xcd", "share/registry/Langpack-en-US.xcd",
            "program/resource/common/fonts/opens___.ttf"],
    "linux": ["program/soffice", "program/soffice.bin", "program/libmergedlo.so", "program/libswlo.so",
              "program/libsclo.so", "program/libsdlo.so", "program/services.rdb", "program/types.rdb",
              "share/registry/main.xcd", "share/registry/writer.xcd", "share/registry/calc.xcd",
              "share/registry/impress.xcd", "share/registry/graphicfilter.xcd", "share/registry/Langpack-en-US.xcd",
              "program/resource/common/fonts/opens___.ttf"],
    "mac": ["LibreOffice.app/Contents/MacOS/soffice", "LibreOffice.app/Contents/Frameworks/libmergedlo.dylib",
            "LibreOffice.app/Contents/Frameworks/libswlo.dylib", "LibreOffice.app/Contents/Frameworks/libsclo.dylib",
            "LibreOffice.app/Contents/Frameworks/libsdlo.dylib", "LibreOffice.app/Contents/Resources/registry/main.xcd",
            "LibreOffice.app/Contents/Resources/registry/graphicfilter.xcd",
            "LibreOffice.app/Contents/Resources/registry/Langpack-en-US.xcd",
            "LibreOffice.app/Contents/Resources/resource/common/fonts/opens___.ttf"],
}


def expand(pattern: str, plat: str) -> str:
    lay = LAYOUT[plat]

    def lib(m):
        stem = m.group(1)
        return f"{lay['L']}/{stem if stem.startswith('lib') else lay['pre'] + stem}{lay['suf']}"

    def exe(m):
        name = m.group(1)
        return f"{lay['X']}/{name}{lay['exe'] if not name.endswith('.bin') else ''}"
    p = re.sub(r"\{lib:([^}]+)\}", lib, pattern)
    p = re.sub(r"\{exe:([^}]+)\}", exe, p)
    for k in ("C", "S", "P", "L"):
        p = p.replace("{" + k + "}", lay[k])
    return re.sub(r"/+", "/", p).lstrip("/")


def patterns(plat: str) -> list[tuple[str, str]]:
    """(category, glob relative to the unpacked folder) for one platform. A rule may name its platform first."""
    out = []
    for rule in RULES:
        if rule[0] in LAYOUT:
            if rule[0] != plat:
                continue
            rule = rule[1:]
        out.append((rule[0], expand(rule[1], plat)))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Hard dependencies. On Windows and macOS several libraries that Linux loads on demand (the WordPerfect, Works and
# Apple importers, lp_solve ...) are linked into libmergedlo, so deleting them stops LibreOffice from starting. The
# rules above are therefore only a request: prune() keeps every library that a remaining file imports. The import
# tables are read with the small parsers below (no third-party package; tests compare them with pefile and macholib).
import struct
import subprocess


def pe_imports(path: Path) -> list[str]:
    """DLL names in a Windows executable's import table (lower case). Delay-loaded DLLs are loaded on demand and
    are not listed."""
    try:
        b = path.read_bytes()
        if b[:2] != b"MZ":
            return []
        pe = struct.unpack_from("<I", b, 0x3C)[0]
        if b[pe:pe + 4] != b"PE\0\0":
            return []
        nsec, = struct.unpack_from("<H", b, pe + 6)
        optsize, = struct.unpack_from("<H", b, pe + 20)
        opt = pe + 24
        magic, = struct.unpack_from("<H", b, opt)
        dd = opt + (112 if magic == 0x20B else 96)
        rva, size = struct.unpack_from("<II", b, dd + 8)            # data directory 1: imports
        if not rva:
            return []
        secs = []
        for i in range(nsec):
            o = opt + optsize + 40 * i
            vsize, va, rsize, raw = struct.unpack_from("<IIII", b, o + 8)
            secs.append((va, max(vsize, rsize), raw))

        def off(r):
            for va, n, raw in secs:
                if va <= r < va + n:
                    return raw + r - va
            return None
        out, o = [], off(rva)
        while o is not None:
            name_rva = struct.unpack_from("<I", b, o + 12)[0]
            if not name_rva:
                break
            n = off(name_rva)
            if n is None:
                break
            out.append(b[n:b.index(b"\0", n)].decode("latin-1").lower())
            o += 20
        return out
    except (OSError, struct.error, ValueError):
        return []


def macho_imports(path: Path) -> list[str]:
    """Library paths a Mach-O file (thin or universal) loads at start: LC_LOAD_DYLIB and its weak, re-export and
    upward forms."""
    try:
        b = path.read_bytes()
        out = []
        if b[:4] == b"\xca\xfe\xba\xbe":
            n = struct.unpack_from(">I", b, 4)[0]
            slices = [struct.unpack_from(">II", b, 8 + 20 * i + 8) for i in range(min(n, 8))]      # offset, size
        else:
            slices = [(0, len(b))]
        for off, _size in slices:
            if b[off:off + 4] != b"\xcf\xfa\xed\xfe":                # 64-bit little endian only
                continue
            ncmds = struct.unpack_from("<I", b, off + 16)[0]
            o = off + 32
            for _ in range(min(ncmds, 4000)):
                cmd, size = struct.unpack_from("<II", b, o)
                if cmd in (0xC, 0x80000018, 0x8000001F, 0x80000023):
                    nm = struct.unpack_from("<I", b, o + 8)[0]
                    end = b.index(b"\0", o + nm)
                    out.append(b[o + nm:end].decode("utf-8", "replace"))
                o += size
        return out
    except (OSError, struct.error, ValueError):
        return []


def elf_imports(path: Path) -> list[str]:
    try:
        if path.read_bytes()[:4] != b"\x7fELF":
            return []
        r = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.split("[", 1)[1].split("]", 1)[0] for ln in r.stdout.splitlines() if "(NEEDED)" in ln and "[" in ln]


def imports_of(path: Path, plat: str) -> list[str]:
    """Base names (lower case) of the libraries this file needs in order to start."""
    raw = pe_imports(path) if plat == "win" else macho_imports(path) if plat == "mac" else elf_imports(path)
    return [Path(x.replace("\\", "/")).name.lower() for x in raw]


def _is_binary(p: Path, plat: str) -> bool:
    if p.is_symlink() or not p.is_file():
        return False
    if plat == "win":
        return p.suffix.lower() in (".dll", ".exe", ".pyd", ".com")
    try:
        with open(p, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    return head in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe") if plat == "mac" else head == b"\x7fELF"


def _rescue(dest: Path, plat: str, doomed: dict[str, Path]) -> list[tuple[Path, Path]]:
    """Take out of `doomed` (path -> hit) every file that a file staying behind imports, until nothing more is
    needed. Returns [(kept library, the file that needs it)]."""
    binaries = {}
    for root, _dirs, files in os.walk(dest):
        for n in files:
            p = Path(root) / n
            if _is_binary(p, plat):
                binaries[str(p)] = p
    deps = {k: imports_of(p, plat) for k, p in binaries.items()}
    kept: list[tuple[Path, Path]] = []
    changed = True
    while changed:
        changed = False
        gone = {k for k in doomed if k in binaries}
        gone |= {k for k in binaries if any(k.startswith(d + os.sep) for d in doomed)}      # inside a doomed folder
        need: dict[str, str] = {}
        for k, ds in deps.items():
            if k in gone:
                continue
            for d in ds:
                need.setdefault(d, k)
        for k in list(doomed):
            if k in binaries and Path(k).name.lower() in need:
                kept.append((doomed.pop(k), Path(need[Path(k).name.lower()])))
                changed = True
    return kept


def _size(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        try:
            return p.lstat().st_size
        except OSError:
            return 0
    return sum(f.lstat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def _matches(base: Path, pattern: str) -> list[Path]:
    level = [base]
    for part in pattern.split("/"):
        nxt = []
        for p in level:
            if not p.is_dir() or p.is_symlink():
                continue
            if any(c in part for c in "*?["):
                try:
                    nxt += [p / n for n in os.listdir(p) if fnmatch.fnmatchcase(n, part)]
                except OSError:
                    pass
            elif os.path.lexists(p / part):
                nxt.append(p / part)
        level = nxt
    return level


def prune(dest: Path, plat: str, dry_run: bool = False, log=print, check: bool = True) -> dict:
    """Delete everything the rules list from the unpacked LibreOffice in dest, except libraries that a file which
    stays imports (see _rescue). Returns {category: bytes removed} plus _before, _after, _total and _kept (the rescued
    libraries). Raises RuntimeError when a MUST_KEEP file is missing afterwards (check=False skips that: the Debian
    system copy used in the tests has another layout, with OpenSymbol installed system-wide)."""
    dest = Path(dest)
    before = _size(dest)
    doomed: dict[str, Path] = {}            # path -> the category that asked for it
    cats: dict[str, str] = {}
    for cat, pat in patterns(plat):
        for hit in _matches(dest, pat):
            if hit.name in PROTECT or str(hit) in cats or not os.path.lexists(hit):
                continue
            cats[str(hit)] = cat
            doomed[str(hit)] = hit
    kept = _rescue(dest, plat, doomed)
    removed: dict[str, int] = {}
    for k, hit in sorted(doomed.items()):
        removed[cats[k]] = removed.get(cats[k], 0) + _size(hit)
    total = sum(removed.values())
    if not dry_run:
        for k, hit in sorted(doomed.items(), key=lambda kv: -len(kv[0])):
            if not os.path.lexists(hit):
                continue                                         # inside a folder that was already removed
            if hit.is_dir() and not hit.is_symlink():
                shutil.rmtree(hit, ignore_errors=True)
            else:
                try:
                    hit.unlink()
                except OSError:
                    pass
        # A link whose target was pruned (Mac: Frameworks/libjava_uno.jnilib -> libjava_uno.dylib) would make
        # `codesign --verify --strict` reject the whole app ("invalid destination for symbolic link in bundle").
        for root, dirs, files in os.walk(dest):
            for n in dirs + files:
                q = os.path.join(root, n)
                if os.path.islink(q) and not os.path.exists(q):
                    try:
                        os.unlink(q)
                    except OSError:
                        pass
        gone = [f for f in MUST_KEEP[plat] if not (dest / f).exists()] if check else []
        if gone:
            raise RuntimeError(f"LibreOffice prune removed required files: {', '.join(gone)}")
        if plat != "mac":                                    # LibreOffice expects these folders to exist
            for d in ("share/extensions", "share/uno_packages"):
                (dest / d).mkdir(parents=True, exist_ok=True)
    after = before - total if dry_run else _size(dest)
    for cat, n in sorted(removed.items(), key=lambda kv: -kv[1]):
        log(f"  pruned {n / 1e6:7.1f} MB  {cat}")
    if kept:
        log(f"  kept {len(kept)} library(ies) that the rules named, because files that stay import them: "
            + ", ".join(sorted(p.name for p, _ in kept)))
    log(f"LibreOffice pruned ({plat}): {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
    return {**removed, "_before": before, "_after": after, "_total": total, "_kept": [p.name for p, _ in kept]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Prune an unpacked LibreOffice (see the module docstring).")
    ap.add_argument("folder")
    ap.add_argument("platform", choices=sorted(LAYOUT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-check", action="store_true", help="do not insist on the files the official unpack has")
    a = ap.parse_args()
    prune(Path(a.folder), a.platform, a.dry_run, check=not a.no_check)
