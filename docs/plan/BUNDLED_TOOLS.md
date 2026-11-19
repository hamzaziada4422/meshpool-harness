# Bundled tools: everything inside the installer

Owner rule (WP13): the macOS DMGs (Apple silicon, Intel) and the Windows installer carry every program the app needs. The installed app uses none of the customer's tools and downloads nothing at runtime. In a source checkout or command-line install nothing changes (PATH, `npx`, system LibreOffice with an opt-in).

"Packaged" means `config.packaged()` (the app has its own `python/` or `runtime/` beside `app/`) or `sys.frozen`. One definition, used by `meshpool_harness/bundled.py` and `office_runtime.py`.

## Resolver order

LibreOffice (`app/meshpool_harness/office_runtime.py`; `config.find_soffice()`, `office_mcp_server._soffice()` and `engines/forge/common.find_soffice()` are thin wrappers):

1. `MH_SOFFICE` (path to soffice; developers/CI).
2. Bundled: `<app>/vendor/libreoffice` (macOS `LibreOffice.app/Contents/MacOS/soffice`, Windows `program/soffice.exe`).
3. System copy (PATH, /Applications, Program Files) only outside the packaged app and only with `MH_ALLOW_SYSTEM_SOFFICE=1`.

No downloader exists in the app (removed per the owner decision). The doctor row says which copy is used. `.github/workflows/tests.yml` sets `MH_ALLOW_SYSTEM_SOFFICE=1` (CI installs LibreOffice with apt).

Node, Git, Codex, OpenCode, Pi, Chromium (`app/meshpool_harness/bundled.py`):

| Thing | Packaged app | Source checkout |
|---|---|---|
| Node | `<root>/node` only (`bundled.node_exe()`; `pi_rpc.node_exe()` has no PATH fallback) | `MH_NODE`, else PATH |
| PATH | `use_bundled_tools()` (called from `config.load_into_env` and the desktop shell) puts bundled Node, Git (`git/cmd`, `git/mingw64/bin`, `git/usr/bin` on Windows, `git/bin` on macOS) and the LibreOffice folder FIRST. Before this change the desktop shell put Node last, so a customer's Node won | untouched |
| Codex | bundled Node + `vendor/codex-acp` (its `package.json` bin), no `npx -y`; if missing: unavailable with "Reinstall Meshpool", never npx | `npx -y @agentclientprotocol/codex-acp@2.6.0` |
| Codex sign-in | `vendor/codex-acp/.../@openai/codex/bin/codex.js login` (also in `meshpool setup`) | `npx -y @openai/codex@latest login` |
| OpenCode | the native binary from `vendor/opencode/node_modules/opencode-<os>-<arch>/bin/opencode[.exe]` (`acp`). Its npm postinstall is skipped at build, so the binary is taken directly | `npx -y opencode-ai@1.19.5 acp` |
| Pi | `vendor/pi` + bundled Node (already so) | same |
| Hermes | `app/vendor/hermes` run by the bundled Python (already so; `release.yml` clones the pinned `HERMES_COMMIT`) | same |
| Git / Bash | Windows `<root>/git` MinGit (`MH_BASH` = its bash; `find_bash()` never looks at Program Files in the packaged app); macOS `<Resources>/git` | PATH |
| Browser use | Chromium from `vendor/browsers` (`PLAYWRIGHT_BROWSERS_PATH`), no Chrome/Edge channels, no `playwright install` | Chrome/Edge first, then downloads Chromium once |

## Packaging steps (`packaging/build_all.py`, helpers in `packaging/bundled_tools.py`)

Each platform tree now also gets, in this order after Pi and Node:

1. `libreoffice()`: downloads the pinned release into `deps/download-cache`, checks SHA-256, unpacks into `<app>/vendor/libreoffice` (macOS: `hdiutil attach` + `ditto` of `LibreOffice.app`, so build the Mac trees on a Mac; Windows: `msiexec /a`, or `msiextract`/`7z` on a non-Windows host), writes `MESHPOOL-LIBREOFFICE.json`. `MH_BUNDLE_LIBREOFFICE=0` builds a slim TEST app without it (prints a warning; never for releases).
2. `node_agents()`: `npm ci --os --cpu --ignore-scripts` from `packaging/node-agents/{codex-acp,opencode}/package-lock.json` (integrity hashes for every package), only this platform's native packages, OpenCode `-baseline` builds dropped. Needs `npm` on the build machine (the release runners already have it).
3. `chromium()`: `playwright install chromium` into `<app>/vendor/browsers` with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE`. NOT yet run end to end.
4. Git: Windows `git_win()` (`deps/git-win`, as the release workflow already fills it, else the pinned download); macOS `git_mac()` (`deps/git-mac-*`, else `packaging/mac/build_git.sh`, which builds the pinned source tarball relocatably). NOT yet run (no Mac here).

Mac signing: `sign.sh` already signs every Mach-O under the bundle with the app's entitlements (JIT, unsigned memory, library validation off), which covers LibreOffice, Codex, OpenCode, Chromium and Git. Must be tried once with real Apple secrets: LibreOffice and Chromium have helper bundles that sometimes need their own entitlements.

Updating a pin: `python packaging/build_all.py --check-pins` compares the pinned LibreOffice hashes with the ones The Document Foundation publishes.

## Pins

| What | Version | SHA-256 | Source |
|---|---|---|---|
| LibreOffice macOS arm64 | 26.2.7 | `58601808ae2b919c82dc7c4d6a26389de0e167f344ca48e5b6c9189218eb95ae` | `LibreOffice_26.2.7_MacOS_aarch64.dmg` |
| LibreOffice macOS x86_64 | 26.2.7 | `26a830205c617cc624b2a83783ef46434a4d6c337c3a873f79c43012171f83de` | `LibreOffice_26.2.7_MacOS_x86-64.dmg` |
| LibreOffice Windows x64 | 26.2.7 | `480fecf759fc6393bd614fbf43753eb95a8198fc1a065d41b727e3b664cd1e59` | `LibreOffice_26.2.7_Win_x86-64.msi` |
| Git source (macOS build) | 2.56.1 | `f0b0bf89264b9bd2e1c640363e7529643989da76281600473c50745cd34a607f` | kernel.org `sha256sums.asc` |
| MinGit (Windows) | FILL-IN | FILL-IN | needs GitHub access: `gh release view --repo git-for-windows/git --json tagName,assets -q '.tagName, (.assets[]\|select(.name\|test("^MinGit-[0-9.]+-64-bit.zip$"))\|.name+" "+.digest)'`, then set `MINGIT_URL` and `MINGIT_SHA256` in `packaging/bundled_tools.py` (until then the release workflow's unpinned download in `deps/git-win` is used, as today) |
| Codex ACP / OpenCode | 2.6.0 / 1.19.5 | lock files `packaging/node-agents/*/package-lock.json` (sha512 integrity per package) | npm; versions are the ones in `harnesses/__init__.py` (a test keeps them equal) |

The LibreOffice hashes come from the `.sha256` files on download.documentfoundation.org (HTTPS). Before the first release also verify the `.asc` signatures with gpg (`https://www.documentfoundation.org/gpg/`). The Windows extraction was run here on Linux (`msiextract`) and produced `program/soffice.exe`; the macOS dmg path was not runnable here.

## Size impact (estimates; the "before" size of today's installers is not measurable in this checkout)

Unpacked numbers for Windows, Codex and OpenCode are measured from real `npm ci` / `msiextract` runs here. DMG (ULMO) and NSIS (LZMA) compress the native binaries to roughly 0.35-0.45 of that.

| Item | macOS arm64 | macOS x86_64 | Windows x64 |
|---|---|---|---|
| LibreOffice, unpacked | about 0.8-1.0 GB (estimate; the vendor dmg is 298 MB) | about 0.8-1.0 GB (dmg 308 MB) | 1.5 GB (measured; the msi is 373 MB) |
| Codex (+ native codex binary), unpacked | 368 MB | 392 MB | 501 MB |
| OpenCode, unpacked (baseline builds dropped) | 173 MB | about 190 MB | about 200 MB |
| Chromium, unpacked | about 400 MB (estimate) | about 400 MB | about 450 MB |
| Git | about 25 MB | about 25 MB | about 60 MB (already shipped) |
| Added to the app tree | about 1.8-2.0 GB | about 1.9-2.1 GB | about 2.6 GB |
| Added to the installer (compressed estimate) | about +0.8 GB | about +0.85 GB | about +1.0 GB |

So each installer grows from today's size by roughly 0.8-1.0 GB. Codex's native binary and Chromium are the next biggest after LibreOffice. NSIS installers over 2 GB need care (the 2 GB data limit of the classic NSIS stub: use the 64-bit NSIS build, or ship Chromium separately in the same installer as a second archive); check the first build.

## Audit: customer-installed tools the app used (before this work)

| Tool | Where | Shipped before? | Now |
|---|---|---|---|
| LibreOffice | `config.py`, `office_mcp_server.py`, `forge/common.py` (+ vendored hermes `pptx_render.py` via PATH) | No | Bundled; wrappers share one resolver; hermes skill sees it via PATH |
| Node.js | `harnesses/pi_rpc.py`, `core/tools.py:365`, MCP `npx`/`node`, `desktop.py` | Yes, but placed last on PATH, and `pi_rpc` fell back to PATH | Bundled first; no PATH fallback in packaged |
| `npx -y` Codex, OpenCode, Codex login | `harnesses/__init__.py`, `agent/onboard.py` | No (registry download at each first use) | Vendored at build time |
| Pi | `vendor/pi` | Yes | unchanged |
| Hermes | `vendor/hermes` | Yes (release workflow, pinned commit; confirmed in `release.yml`) | unchanged |
| Python | `python3` in agent subprocesses | App's own runtime for the app; the agent shell's `python` is the customer's by design | see "cannot be bundled" |
| Git | `plugins.py`, `mcp_apps.py`, `core/guards.py`, `core/review.py`, `engines/code/*`, `hrouter/packs.py`, `agent/core.py` | Windows yes (MinGit, on PATH in desktop shell); macOS no (Xcode CLT) | macOS bundled build; PATH first. Callers using bare `git` pick it up through PATH |
| Bash | `config.find_bash()` | Windows: MinGit existed but `find_bash` also tried Program Files; macOS `/bin/bash` (OS component) | Windows bundled only when packaged; macOS stays `/bin/bash` |
| Chrome / Edge, then downloaded Chromium | `agent/browser_mcp_server.py` | No | Bundled Chromium, no channels, no download |
| `gh` (GitHub CLI) | `mcp_apps.py:725` | No | Not bundled (see below) |
| `docker` | `core/guards.py`, benchmark runs | No | Not bundled (optional, user's) |
| `pdftoppm` / poppler | vendored hermes `pdf/_raster.py` | No | Not bundled; the skill falls back or says so |
| `fc-match` | `forge/common.py` | Linux dev only | n/a |
| MCP library servers (`npx`, `uvx`) | `mcp_library.py`, `agent/mcp.py` | No | See below |
| System web view | desktop window (WKWebView, WebView2); fallback uses the customer's Chrome/Edge (`ui/desktop.py`) | OS components | See below |

## Cannot be bundled (and why)

1. The user's own toolchain for their project: `python`, `pytest`, `node` project scripts, `ruby`, `go`, `cargo`, `java`, `gcc`, `docker` (`hrouter/packs.py`, `core/tools.py`, `core/guards.py`). The agent runs the user's project with the user's tools; bundling would change the project's results. Node and Git are exceptions because the app itself needs them and now come first on PATH.
2. MCP connector servers launched with `npx` or `uvx` (`mcp_library.py`, about 15 entries, many `@latest`): third-party packages the user turns on. They run on the bundled Node/npx but fetch the package from npm when first used, and `uvx` servers need `uv`, which is not shipped. Bundling would mean vendoring each (unpinned) package; offer a pinned vendored subset if the owner wants offline connectors.
3. The window itself: macOS WKWebView and Windows WebView2 belong to the OS (WebView2 ships with Windows 11 and current Windows 10). The fallback that opens the customer's Chrome or Edge as an app window (`desktop.py`) stays; it only runs when the native web view fails.
4. `gh` and `docker`: user accounts and daemons; optional.
5. `/bin/bash`, `sh`, `curl` and the other macOS system tools are the OS, not customer installs.

## Open decisions for the owner

1. Size: roughly +0.8 to +1.0 GB per installer, with LibreOffice about half. Alternatives: drop OpenCode or Codex, or keep Chromium out and use the OS web engine for browse. Say if you want a slim edition.
2. Intel Mac on 10.15: LibreOffice 26.2, Codex, OpenCode and Chromium likely need macOS 11 or later. `release.yml` now exempts Codex and OpenCode (like Node) in `check_minos`; LibreOffice is deliberately not exempted, so the first Intel build will say. Options: raise the Intel minimum to 11, pin LibreOffice 24.8 for Intel only, or turn Office conversion off on 10.15 with a note like `macos.py`.
3. MinGit pin (needs GitHub access) and the first runs of `build_git.sh`, `chromium()` and the macOS LibreOffice unpack on a Mac, plus a signing and notarization trial (LibreOffice has many nested helpers).
4. OpenCode baseline builds (for CPUs without AVX2) are dropped to save about 200 MB. Say if those machines matter.
5. `MH_BUNDLE_LIBREOFFICE=0` exists for fast test builds; do you want it removed so a release can never ship without it? Currently a release build is the default and the flag prints a warning.
6. LibreOffice stays on 26.2.7 until `download.documentfoundation.org/stable` moves on; the build then falls back to `downloadarchive`. Consider hosting the pinned files yourself.
7. `tools.py` (not edited here) calls `find_soffice()` only to decide whether to warn that results are unverified; no change needed now that nothing downloads.

## Release changes for auto-update (docs/plan/AUTO_UPDATE.md)

- `release.yml` "Attach to the release": writes `dist/update-manifest.json` with `packaging/updater/make_manifest.py`. The fingerprint covers `requirements-pinned.txt`, `requirements-runtime.txt`, `bundled_tools.py` (LibreOffice, Git pins), and both Node agent lock files, so any change to a bundled program's version makes `runtime_changed` true and the app takes the full installer. The manifest is in `SHA256SUMS.txt`, in the upload list and in the uploaded-names check; `release.sh` documents it as an argument. Node and Pi are versioned in the workflow itself (not fingerprinted): bump one of the listed files with them.
- NSIS: a silent install (`/S`) no longer recreates the Desktop shortcut. The files are now extracted to `$INSTDIR.new` and swapped in folder by folder, with the old folder kept as `<name>.old` and put back if a swap fails (not compiled here: `makensis` is not available; check the first build, including a silent update over an existing install).
- DMGs: `repack.sh` builds each image from a folder holding exactly one `Meshpool.app` and the `Applications` link, with no licence agreement; unchanged.
