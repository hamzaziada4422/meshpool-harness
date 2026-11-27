"""Assemble the Meshpool app for Apple silicon, Intel Macs, Windows and Linux (x64). One package per platform: the desktop app
with the meshpool terminal command, browser use, computer use, Office output, Pi and Hermes inside.

The version has one source, __version__ in app/meshpool_harness/__init__.py: this script stamps it into Info.plist and
installer.nsi (their @VERSION@ placeholders), and stops if packaging/win/launcher/winres/winres.json (the version
inside Meshpool.exe) disagrees. `python packaging/build_all.py --winres` rewrites winres.json to match; then regenerate
the launcher's .syso and rebuild Meshpool.exe (docs/RELEASING.md).

Everything the app needs ships inside the installer; the installed app uses none of the customer's tools and downloads
nothing (docs/plan/BUNDLED_TOOLS.md). This script therefore also puts in the bundle, each pinned and verified by
packaging/bundled_tools.py: LibreOffice (<app>/vendor/libreoffice, pruned to headless conversion by
packaging/lo_prune.py), Chromium (<app>/vendor/browsers, English only, no headless shell), Git (Windows: MinGit;
macOS: packaging/mac/build_git.sh), ffmpeg and pandoc (<app>/vendor/ffmpeg and vendor/pandoc), the speech-to-text model
(<app>/vendor/whisper), and Hermes without its tests, website and apps, next to the Node runtime and Pi it
already carried. Codex and OpenCode are NOT shipped (Meshpool's own agent is the product; the benchmark in testkit/
runs them with npx). MH_BUNDLE_LIBREOFFICE=0 builds a slim test app without LibreOffice on a developer machine only:
it is refused when CI is set, so a release can never ship without it.
`python packaging/build_all.py --check-pins` compares the pinned checksums with the ones the publisher lists.

Third-party runtimes are not in the repo. Put them in a deps folder (default: <repo>/deps, or set MESHPOOL_DEPS);
docs/BUILDING.md lists each one and where it comes from. Output goes to <repo>/dist-src.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = Path(os.environ.get("MESHPOOL_DEPS") or ROOT / "deps")
APP_SRC = ROOT / "app"
VENDOR = DEPS / "vendor-hermes"                           # Hermes Agent with packaging/hermes-acp-server.patch applied
OUT = ROOT / "dist-src"
IGN = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
PKG = ROOT / "packaging"
WIN_LAUNCH = DEPS / "win/Meshpool"                        # Meshpool.exe built from packaging/win/launcher
PI = DEPS / "pi/node_modules"                             # Pi coding agent (npm), platform-neutral JS + wasm
NODE = {"mac-arm64": DEPS / "node-mac-arm64", "mac-x64": DEPS / "node-mac-x64", "win": DEPS / "node-win",
        "linux": DEPS / "node-linux-x64"}
PI_NATIVE = {"mac-arm64": "darwin-arm64", "mac-x64": "darwin-x64", "win": "win32-x64", "linux": "linux-x64"}
RT = {"mac-arm64": DEPS / "rt-mac/python", "mac-x64": DEPS / "rt-macx64/python", "win": DEPS / "rt-win/python",
      "linux": DEPS / "rt-linux/python"}
# The oldest macOS each Mac app opens on (Info.plist's LSMinimumSystemVersion): 11 Big Sur on both. Node 22, Chromium and
# LibreOffice need it, so the Intel app no longer promises 10.15. The launcher and packaging/mac/check_minos.py use the
# same number (.github/workflows/release.yml, packaging/mac/repack.sh).
MIN_MACOS = {"mac-arm64": "11.0", "mac-x64": "11.0"}
WINRES = PKG / "win/launcher/winres/winres.json"


def version() -> str:
    m = re.search(r'^__version__ = "([^"]+)"', (APP_SRC / "meshpool_harness/__init__.py").read_text(), re.M)
    if not m or not re.match(r"\d+\.\d+\.\d+", m.group(1)):
        sys.exit("build_all: app/meshpool_harness/__init__.py needs __version__ = \"X.Y.Z...\"")
    return m.group(1)


VERSION = version()
VERSION_NUM = re.match(r"\d+\.\d+\.\d+", VERSION).group(0)          # Windows and macOS want plain numbers


def stamp(src: Path, dst: Path, extra: dict | None = None) -> None:
    """Copy a text file with its version placeholders (and any in extra, {"NAME": value} for @NAME@) filled in."""
    text = src.read_text(encoding="utf-8")
    text = (text.replace("@VERSION_NUM@", VERSION_NUM).replace("@VERSION@", VERSION)
            .replace("@BETA@", " beta" if VERSION.startswith("0.") else ""))
    for k, v in (extra or {}).items():
        text = text.replace(f"@{k}@", v)
    left = sorted(set(re.findall(r"@[A-Z_]+@", text)))
    if left:
        sys.exit(f"build_all: {src.relative_to(ROOT)} has unknown placeholders: {', '.join(left)}")
    dst.write_text(text, encoding="utf-8")


def winres_versions(data: dict) -> dict:
    m = data["RT_MANIFEST"]["#1"]["0409"]
    v = data["RT_VERSION"]["#1"]["0000"]
    info = v["info"]["0409"]
    return {"manifest identity": m["identity"]["version"], "file_version": v["fixed"]["file_version"],
            "product_version": v["fixed"]["product_version"], "FileVersion": info["FileVersion"],
            "ProductVersion": info["ProductVersion"]}


def winres_want() -> dict:
    four = VERSION_NUM + ".0"
    return {"manifest identity": four, "file_version": four, "product_version": four, "FileVersion": VERSION,
            "ProductVersion": VERSION}


def check_winres(fix: bool = False) -> None:
    """Meshpool.exe carries its own version (winres.json, compiled into the launcher's .syso): it must match."""
    data = json.loads(WINRES.read_text(encoding="utf-8"))
    have, want = winres_versions(data), winres_want()
    if have == want:
        return
    if fix:
        m = data["RT_MANIFEST"]["#1"]["0409"]
        v = data["RT_VERSION"]["#1"]["0000"]
        m["identity"]["version"] = want["manifest identity"]
        v["fixed"]["file_version"], v["fixed"]["product_version"] = want["file_version"], want["product_version"]
        v["info"]["0409"]["FileVersion"], v["info"]["0409"]["ProductVersion"] = VERSION, VERSION
        WINRES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {WINRES.relative_to(ROOT)} to {VERSION}. Now regenerate the .syso and rebuild Meshpool.exe "
              "(docs/RELEASING.md).")
        return
    bad = ", ".join(f"{k} {have[k]} (want {want[k]})" for k in want if have[k] != want[k])
    sys.exit(f"build_all: {WINRES.relative_to(ROOT)} does not match __version__ {VERSION}: {bad}.\n"
             "Run `python packaging/build_all.py --winres`, regenerate the .syso and rebuild Meshpool.exe "
             "(docs/RELEASING.md).")


def _bt():
    """packaging/bundled_tools.py loaded by path (stdlib only): the pinned versions, hashes and unpacking code."""
    spec = importlib.util.spec_from_file_location("mp_bundled_tools", PKG / "bundled_tools.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


LO_KEY = {"mac-arm64": "mac-arm64", "mac-x64": "mac-x64", "win": "win-x64", "linux": "linux-x64"}
CACHE = DEPS / "download-cache"                           # downloads are kept here between builds (hash-checked each time)


def _lo_prune():
    """packaging/lo_prune.py loaded by path (stdlib only)."""
    spec = importlib.util.spec_from_file_location("mp_lo_prune", PKG / "lo_prune.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def in_ci() -> bool:
    return any(os.environ.get(v, "").lower() in ("1", "true", "yes") for v in ("CI", "GITHUB_ACTIONS"))


def libreoffice(app_dir: Path, plat: str):
    """The pinned LibreOffice for this platform, verified, unpacked and pruned to headless conversion
    (packaging/lo_prune.py) into <app>/vendor/libreoffice: the app uses only this copy. macOS: LibreOffice.app copied
    out of the dmg (hdiutil, so on a Mac). Windows: `msiexec /a` administrative extract (on a non-Windows build host:
    msiextract or 7z). MH_BUNDLE_LIBREOFFICE=0 builds an app without it, for smoke tests on a developer machine
    only; it is refused in CI, so no release can ship without LibreOffice."""
    if os.environ.get("MH_BUNDLE_LIBREOFFICE", "1") == "0":
        if in_ci():
            sys.exit("build_all: MH_BUNDLE_LIBREOFFICE=0 is not allowed in CI: a release must ship with LibreOffice. "
                     "Unset it (it is for local test builds only).")
        print(f"build_all: MH_BUNDLE_LIBREOFFICE=0: {plat} is built WITHOUT LibreOffice (local test build, not a release)")
        return
    bt = _bt()
    dest = app_dir / "vendor" / "libreoffice"
    bt.fetch_libreoffice(LO_KEY[plat], CACHE, dest)
    full = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file() and not f.is_symlink()) // 1_000_000
    _lo_prune().prune(dest, "win" if plat == "win" else "linux" if plat == "linux" else "mac")
    mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file() and not f.is_symlink()) // 1_000_000
    print(f"build_all: LibreOffice {bt.LO_VERSION} for {plat}: {full} MB unpacked, {mb} MB after pruning")


# Playwright's names for its downloads. Its current Chromium only exists for macOS 14 and later hosts (mac11, mac12 and mac13
# are refused: "Playwright does not support chromium on mac11"), and the binary itself needs macOS 13. mac14 only picks the
# download (Chrome for Testing, arm64 or x64), it does not make the app need macOS 14.
PW_PLATFORM = {"mac-arm64": "mac14-arm64", "mac-x64": "mac14", "win": "win64", "linux": "ubuntu22.04-x64"}


def chromium(app_dir: Path, plat: str):
    """Playwright's Chromium into <app>/vendor/browsers, so browser use never downloads an engine nor uses the customer's
    Chrome or Edge. Browser use opens a visible window (sign-ins), so it is the full Chromium; `--no-shell` leaves out the
    headless shell that Playwright installs next to it by default (about 280 MB), and bundled_tools.prune_chromium drops
    the other languages and Widevine. Without the shell, every launch in the app starts this full Chromium, hidden when
    it should be (browser_use.launch_chromium); packaging/smoke.py starts it from the built app on every system before
    a release is published. Uses the target runtime's own playwright package when this host can run it (so the revision
    matches the bundled package), else the build machine's."""
    import subprocess
    dest = app_dir / "vendor" / "browsers"
    exe = RT[plat] / ("python.exe" if plat == "win" else "bin/python3")
    host_ok = ((plat == "win") == (os.name == "nt") and (plat == "win" or sys.platform == "darwin")) if plat != "linux" \
        else sys.platform.startswith("linux")
    py = str(exe) if host_ok and exe.exists() else sys.executable
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(dest), PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=PW_PLATFORM[plat])
    import time
    for attempt in range(4):                                  # a download that times out once must not stop a release
        r = subprocess.run([py, "-m", "playwright", "install", "chromium", "--no-shell"], env=env)
        if r.returncode == 0:
            break
        print(f"build_all: Chromium download failed (attempt {attempt + 1} of 4), retrying", flush=True)
        time.sleep(15 * (attempt + 1))
    if r.returncode != 0:
        sys.exit(f"build_all: could not vendor Chromium for {plat} (python used: {py})")
    full = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) // 1_000_000
    _bt().prune_chromium(dest)
    mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) // 1_000_000
    print(f"build_all: vendored Chromium for {plat}: {full} MB, {mb} MB after dropping other languages")


def media_tools(app_dir: Path, plat: str):
    """ffmpeg and pandoc into <app>/vendor/ffmpeg and <app>/vendor/pandoc, taken out of the pinned, hash-checked PyPI
    wheels (bundled_tools.WHEEL_PROGRAMS), so converting audio, video and documents never needs a program the customer
    installed. The Apple silicon ffmpeg needs macOS 12 (read from the file; bundled.ffmpeg_needs_newer_macos tells the
    user) and is exempt from the macOS 11 check up to that."""
    bt = _bt()
    key = LO_KEY[plat]
    for name in ("ffmpeg", "pandoc"):
        exe = bt.fetch_wheel_program(name, key, CACHE, app_dir / "vendor" / name)
        mb = exe.stat().st_size // 1_000_000
        print(f"build_all: {name} for {plat}: {mb} MB ({bt.WHEEL_PROGRAMS[name]['version']})")


def speech_model(app_dir: Path):
    """The speech-to-text model (Whisper base, CTranslate2 format) into <app>/vendor/whisper, from the pinned revision
    with each file's SHA-256 checked. faster-whisper (in the runtime's packages) loads it from there, offline."""
    bt = _bt()
    out = bt.fetch_whisper_model(CACHE, app_dir / "vendor" / "whisper")
    mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) // 1_000_000
    print(f"build_all: speech model {bt.WHISPER_MODEL}: {mb} MB")


def git_win(root: Path):
    """MinGit into <app root>/git. deps/git-win (the release workflow unpacks MinGit there) wins; otherwise the pinned,
    checksum-verified download in bundled_tools.py."""
    if (DEPS / "git-win").is_dir():
        shutil.copytree(DEPS / "git-win", root / "git")
    else:
        _bt().fetch_mingit(CACHE, root / "git")
    # MinGit has no bash.exe, only usr/bin/sh.exe, which is the same MSYS2 bash binary (named sh it runs in POSIX
    # mode). A copy named bash.exe is full bash: the agents' shell (config.find_bash) needs it, else Windows falls
    # back to PowerShell and every bash command the model writes fails.
    ub = root / "git" / "usr" / "bin"
    if not (ub / "bash.exe").exists():
        if not (ub / "sh.exe").is_file():
            sys.exit("build_all: MinGit has neither usr/bin/bash.exe nor usr/bin/sh.exe; the agents would have no bash")
        shutil.copy2(ub / "sh.exe", ub / "bash.exe")


def git_mac(root: Path, arch: str):
    """Git for macOS into <Resources>/git (bin/git, libexec/git-core). deps/git-<arch> if present, else built from the
    pinned source tarball by packaging/mac/build_git.sh (run on a Mac)."""
    src = DEPS / f"git-{arch}"
    if not src.is_dir():
        import subprocess
        if sys.platform != "darwin":
            sys.exit(f"build_all: {src} is missing. Build it on a Mac: packaging/mac/build_git.sh {arch} {src}")
        subprocess.run(["bash", str(PKG / "mac/build_git.sh"), arch, str(src)], check=True)
    shutil.copytree(src, root / "git", symlinks=True)


def check_pins():
    """`--check-pins`: compare each pinned hash with the one the publisher lists."""
    import urllib.request
    bt = _bt()
    for key, (_d, name, sha, _size) in bt.LO_PINNED.items():
        got = "?"
        for url in bt.lo_urls(key):
            try:
                with urllib.request.urlopen(url + ".sha256", timeout=60) as r:
                    got = r.read().decode().split()[0]
                break
            except Exception as e:
                got = f"unreachable ({e})"
        print(f"{key:10s} {name}\n  pinned {sha}\n  TDF    {got}  {'OK' if got == sha else 'DIFFERENT, check the .asc signature'}")
    print(f"git source {bt.GIT_VERSION}: pinned {bt.GIT_SOURCE_SHA256}")
    print(f"MinGit: {bt.MINGIT_URL} {bt.MINGIT_SHA256}")


def pi_and_node(app_dir: Path, node_dst: Path, plat: str):
    """Pi in app/vendor/pi (only this platform's native prebuilds) and the Node runtime beside the app."""
    keep = PI_NATIVE[plat]

    def ign(d, names):
        out = set(IGN(d, names))
        if "/prebuilds" in d.replace("\\", "/") and d.rstrip("/").endswith("prebuilds"):
            out |= {n for n in names if n != keep}
        if d.replace("\\", "/").endswith("pi-tui/native"):
            out |= {n for n in names if n != {"darwin-arm64": "darwin", "darwin-x64": "darwin", "win32-x64": "win32",
                                               "linux-x64": "linux"}[keep]}
        return out
    shutil.copytree(PI, app_dir / "vendor" / "pi" / "node_modules", ignore=ign, symlinks=True)
    shutil.copytree(NODE[plat], node_dst, symlinks=True, ignore=shutil.ignore_patterns("corepack*", "include", "share"))


def app_code(dst: Path):
    dst.mkdir(parents=True)
    for name in ("meshpool_harness", "engines", "profiles", "defaults"):
        shutil.copytree(APP_SRC / name, dst / name, ignore=IGN)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", dst / "THIRD_PARTY_NOTICES.md")
    # Start-up recovery of an interrupted in-place update (M11-06): beside the code folders, not inside them, so it
    # still works when meshpool_harness/ is missing. sitecustomize.py runs it when the app folder is on PYTHONPATH.
    for name in ("meshpool_recover.py", "sitecustomize.py"):
        shutil.copy2(APP_SRC / name, dst / name)
    shutil.copy2(APP_SRC / "requirements.txt", dst / "requirements.txt")
    # The package list the bundled runtime is built from (docs/BUILDING.md); the in-app update compares it.
    shutil.copy2(PKG / "requirements-cli.txt", dst / "requirements-cli.txt")
    shutil.copytree(VENDOR, dst / "vendor" / "hermes", ignore=_bt().hermes_ignore(VENDOR))    # without tests, website, apps ...
    shutil.copy2(PKG / "hermes-NOTICE-MESHPOOL.txt", dst / "vendor" / "hermes" / "NOTICE-MESHPOOL.txt")
    if (DEPS / "ocr-models").is_dir():                   # extra-script OCR models (optional; Latin/CJK built in)
        shutil.copytree(DEPS / "ocr-models", dst / "vendor" / "ocr")


def docs(dst: Path):
    d = PKG / "harness-docs"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(d / "README.md", dst / "README.md")
    shutil.copytree(d / "ci", dst / "ci")
    shutil.copytree(d / "editors", dst / "editors")


def mac_app(arch: str):
    root = OUT / f"app-{arch}" / "Meshpool.app" / "Contents"
    (root / "MacOS").mkdir(parents=True)
    (root / "Resources").mkdir()
    stamp(PKG / "mac/Info.plist", root / "Info.plist", {"MIN_MACOS": MIN_MACOS[arch]})
    shutil.copy2(PKG / "mac/meshpool", root / "Resources/meshpool")
    shutil.copy2(PKG / "icons/Meshpool.icns", root / "Resources/Meshpool.icns")
    shutil.copytree(RT[arch], root / "Resources/python", symlinks=True, ignore=IGN)
    app_code(root / "Resources/app")
    pi_and_node(root / "Resources/app", root / "Resources/node", arch)
    libreoffice(root / "Resources/app", arch)
    chromium(root / "Resources/app", arch)
    media_tools(root / "Resources/app", arch)
    speech_model(root / "Resources/app")
    git_mac(root / "Resources", arch)
    docs(root / "Resources/harness-docs")
    # Contents/MacOS/Meshpool is compiled on the Mac from this source (see build_mac.sh)
    shutil.copy2(PKG / "mac/launcher/main.c", OUT / f"app-{arch}" / "launcher.c")


def git_linux(root: Path):
    """Git for Linux into <app root>/git (bin/git, libexec/git-core). deps/git-linux if present, else built from the
    pinned source tarball by packaging/linux/build_git.sh (on a Linux build host)."""
    src = DEPS / "git-linux"
    if not src.is_dir():
        import subprocess
        if not sys.platform.startswith("linux"):
            sys.exit(f"build_all: {src} is missing. Build it on Linux: packaging/linux/build_git.sh {src}")
        subprocess.run(["bash", str(PKG / "linux/build_git.sh"), str(src)], check=True)
    shutil.copytree(src, root / "git", symlinks=True)


def linux_app():
    """Linux x64: one folder, Meshpool/, packed as Meshpool-Linux-x64.tar.gz. The `meshpool` command and the
    `Meshpool` app launcher sit at its top with install.sh (a menu entry and the command on PATH); the runtime, app
    code, Node, Git, LibreOffice and Chromium sit beside them, the same layout as the Windows app."""
    root = OUT / "app-linux" / "Meshpool"
    root.mkdir(parents=True)
    for name in ("meshpool", "Meshpool", "install.sh"):
        shutil.copy2(PKG / "linux" / name, root / name)
        (root / name).chmod(0o755)
    stamp(PKG / "linux/meshpool.desktop", root / "meshpool.desktop")
    shutil.copy2(PKG / "icons/icon-256.png", root / "meshpool.png")
    shutil.copytree(RT["linux"], root / "python", symlinks=True, ignore=IGN)
    git_linux(root)
    app_code(root / "app")
    pi_and_node(root / "app", root / "node", "linux")
    libreoffice(root / "app", "linux")
    chromium(root / "app", "linux")
    media_tools(root / "app", "linux")
    speech_model(root / "app")
    docs(root / "harness-docs")
    # Playwright's own Node needs libatomic, which a desktop may lack: it runs on the app's Node 22 instead (as on macOS)
    drv = next((root / "python" / "lib").glob("python3.*/site-packages/playwright/driver"), None)
    if drv and (drv / "node").exists():
        (drv / "node").unlink()
        (drv / "node").symlink_to(os.path.relpath(root / "node" / "bin" / "node", drv))


def win_runtime(root: Path):
    """The Windows Python into <app root>/runtime, with python3.exe beside python.exe (bundled_tools.add_python_aliases):
    skills and models type python3, which on Windows otherwise opens the Microsoft Store stub."""
    shutil.copytree(RT["win"], root / "runtime", ignore=IGN)
    _bt().add_python_aliases(root / "runtime")


def win_app():
    root = OUT / "app-win" / "Meshpool"
    root.mkdir(parents=True)
    shutil.copy2(WIN_LAUNCH / "Meshpool.exe", root / "Meshpool.exe")
    shutil.copy2(PKG / "icons/Meshpool.ico", root / "Meshpool.ico")
    # The terminal command in a folder of its own: only bin goes on PATH, since next to Meshpool.exe the name
    # `meshpool` would start the app (Windows tries .EXE before .CMD).
    (root / "bin").mkdir()
    shutil.copy2(PKG / "win/meshpool.cmd", root / "bin" / "meshpool.cmd")
    win_runtime(root)
    git_win(root)
    app_code(root / "app")
    pi_and_node(root / "app", root / "node", "win")
    libreoffice(root / "app", "win")
    chromium(root / "app", "win")
    media_tools(root / "app", "win")
    speech_model(root / "app")
    docs(root / "harness-docs")
    stamp(PKG / "nsis/installer.nsi", OUT / "app-win" / "installer.nsi")
    # The uninstaller generator and the shared macro: the release build writes and signs the uninstaller with these
    # before it builds the installer, so the uninstaller carries a signature too.
    stamp(PKG / "nsis/uninstaller.nsi", OUT / "app-win" / "uninstaller.nsi")
    shutil.copy2(PKG / "nsis/winsetup.nsh", OUT / "app-win" / "winsetup.nsh")
    shutil.copy2(PKG / "nsis/winsetup.ps1", OUT / "app-win" / "winsetup.ps1")


if __name__ == "__main__":
    if "--winres" in sys.argv[1:]:
        check_winres(fix=True)
        sys.exit(0)
    if "--check-pins" in sys.argv[1:]:
        check_pins()
        sys.exit(0)
    only = next((x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--only=")), "mac-arm64,mac-x64,win")
    if "win" in only.split(","):         # only the Windows job runs --winres to repair it, so only it can be held up by it
        check_winres()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for a in ("mac-arm64", "mac-x64"):
        if a in only.split(","):
            mac_app(a)
    if "win" in only.split(","):
        win_app()
    if "linux" in only.split(","):
        linux_app()
    for p in sorted(OUT.iterdir()):
        print(p.name)
