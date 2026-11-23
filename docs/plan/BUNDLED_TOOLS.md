# Bundled tools: everything inside the installer

Owner rule (WP13): the macOS DMGs (Apple silicon, Intel) and the Windows installer carry every program the app needs. The installed app uses none of the customer's tools and downloads nothing at runtime. In a source checkout or command-line install nothing changes (PATH, `npx`, system LibreOffice with an opt-in).

Owner decisions of 19 Nov 2026 (final), which cut about 1 GB from each installer: keep the installers small; **Codex and OpenCode are not shipped** (Meshpool's own agent is the product; the benchmark in `testkit/` still runs them with `npx` from a source checkout); **LibreOffice is pruned** to headless pptx/xlsx/docx conversion; **Chromium** is the full build without the headless shell and with English only; **Hermes** is copied without its tests, website and apps; the **Intel Mac minimum is macOS 11**; LibreOffice is pinned to the permanent download archive; `MH_BUNDLE_LIBREOFFICE=0` is refused in CI.

"Packaged" means `config.packaged()` (the app has its own `python/` or `runtime/` beside `app/`) or `sys.frozen`. One definition, used by `meshpool_harness/bundled.py` and `office_runtime.py`.

Update for 0.0.11: ffmpeg, pandoc, the speech model and the Python packages the skills use are bundled too, and every Mac wheel and program is read for the macOS it needs: see `docs/plan/0.0.11-bundled.md` (the audit table, the macOS 11 floor and what only the release can verify).

## Resolver order

LibreOffice (`app/meshpool_harness/office_runtime.py`; `config.find_soffice()`, `office_mcp_server._soffice()` and `engines/forge/common.find_soffice()` are thin wrappers):

1. `MH_SOFFICE` (path to soffice; developers/CI).
2. Bundled: `<app>/vendor/libreoffice` (macOS `LibreOffice.app/Contents/MacOS/soffice`, Windows `program/soffice.exe`).
3. System copy (PATH, /Applications, Program Files) only outside the packaged app and only with `MH_ALLOW_SYSTEM_SOFFICE=1`.

No downloader exists in the app (removed per the owner decision). The doctor row says which copy is used. `.github/workflows/tests.yml` sets `MH_ALLOW_SYSTEM_SOFFICE=1` (CI installs LibreOffice with apt).

Node, Git, Pi, Chromium (`app/meshpool_harness/bundled.py`):

| Thing | Packaged app | Source checkout |
|---|---|---|
| Node | `<root>/node` only (`bundled.node_exe()`; `pi_rpc.node_exe()` has no PATH fallback) | `MH_NODE`, else PATH |
| PATH | `use_bundled_tools()` (called from `config.load_into_env` and the desktop shell) puts bundled Node, Git (`git/cmd`, `git/mingw64/bin`, `git/usr/bin` on Windows, `git/bin` on macOS) and the LibreOffice folder FIRST | untouched |
| Codex, OpenCode | **not shipped.** `harnesses.load_all()` lists them as unavailable and `hidden: true`, with no install note and no "Reinstall" text; starting one, or `login("codex")`, raises "... is not part of the Meshpool app." There is no `npx` and no look at the customer's PATH | `npx -y @agentclientprotocol/codex-acp@2.6.0`, `npx -y opencode-ai@1.19.5 acp`, `npx -y @openai/codex@latest login` (the benchmark `testkit/loop/bench.py` uses the same manifests) |
| Pi | `vendor/pi` + bundled Node (already so) | same |
| Hermes | `app/vendor/hermes` (pruned copy) run by the bundled Python; `release.yml` clones the pinned `HERMES_COMMIT` | same |
| Git / Bash | Windows `<root>/git` MinGit (`MH_BASH` = its bash; `find_bash()` never looks at Program Files in the packaged app); macOS `<Resources>/git` | PATH |
| Browser use | Chromium from `vendor/browsers` (`PLAYWRIGHT_BROWSERS_PATH`), no Chrome/Edge channels, no `playwright install`. It opens a visible window (sign-ins persist in a profile), so it is the full Chromium, not the headless shell. On a Mac older than 13 it answers that it needs macOS 13 (`bundled.browser_needs_newer_macos`) | Chrome/Edge first, then downloads Chromium once |

## Packaging steps (`packaging/build_all.py`, helpers in `packaging/bundled_tools.py`)

Each platform tree gets, in this order after Pi and Node:

1. `libreoffice()`: downloads the pinned release into `deps/download-cache`, checks SHA-256, unpacks into `<app>/vendor/libreoffice` (macOS: `hdiutil attach` + `ditto` of `LibreOffice.app`, so build the Mac trees on a Mac; Windows: `msiexec /a`, or `msiextract`/`7z` on a non-Windows host; the Visual C++ runtime that the MSI keeps in `System64` is copied next to the program files, because a clean Windows does not have it), then **prunes** it with `packaging/lo_prune.py` and writes `MESHPOOL-LIBREOFFICE.json`. `MH_BUNDLE_LIBREOFFICE=0` builds an app without it for a local smoke test and **exits with an error when `CI` or `GITHUB_ACTIONS` is set**, so a release can never ship without it.
2. `chromium()`: `playwright install chromium --no-shell` into `<app>/vendor/browsers` with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE`, then `bundled_tools.prune_chromium` (English only, no Widevine, no hyphenation data, no Playwright ffmpeg). Playwright 1.65 only offers its Chromium for `mac14`, `mac14-arm64` and `win64` (`mac11` stopped working: "Playwright does not support chromium on mac11"), so `PW_PLATFORM` names those. NOT yet run on a Mac or Windows build host.
3. Git: Windows `git_win()` (`deps/git-win`, as the release workflow still fills it, else the pinned download once `MINGIT_*` is filled in; the old version of this function called itself forever when `deps/git-win` existed); macOS `git_mac()` (`deps/git-mac-*`, else `packaging/mac/build_git.sh`, which builds the pinned source tarball relocatably). NOT yet run (no Mac here).
4. Hermes (in `app_code()`): copied with `bundled_tools.hermes_ignore`, which leaves out tests, website, apps, docs, evals, contributors, nix, docker, the terminal UI, optional skills, assets and the repository's own readmes and lock files.

Codex and OpenCode have no step any more (`node_agents()`, `install_node_agent()` and `packaging/node-agents/` are gone).

Mac signing: `sign.sh` signs every Mach-O under the bundle with the app's entitlements (JIT, unsigned memory, library validation off), which covers LibreOffice, Chromium and Git. Pruning removes the vendor's `_CodeSignature` from `LibreOffice.app`; the bundle seal is not recreated (only the files inside are signed), so check `codesign --verify --deep --strict` on the first signed build. Chromium's helper apps sometimes need their own entitlements.

Updating a pin: `python packaging/build_all.py --check-pins` compares the pinned LibreOffice hashes with the ones The Document Foundation publishes. A new LibreOffice release needs `packaging/verify_lo_prune.py` run again (see below).

## The pruned LibreOffice

Meshpool runs LibreOffice only headless: render a deck or document to PDF or PNG, recalculate a workbook, and `convert_document`. `packaging/lo_prune.py` deletes everything else after the unpack. It is one list of patterns for the three layouts (`{S}` shared data, `{P}` program data, `{L}` libraries, `{lib:stem}`, `{exe:name}`); the Linux layout is the proof copy and the Debian copy the tests use.

Removed: help, readmes, splash, credits; galleries, templates, wizards, autotext, autocorrect lists, Basic libraries, export stylesheets; all extensions (dictionaries, hyphenation, thesaurus, report builder, solver); Java classes and JRE settings; the dialogs, menus and toolbars (`soffice.cfg`, except about 2 MB of small files LibreOffice will not start without), every icon theme but `colibre`, every UI language (translations, `Langpack-*`, `.lproj`) but English; Base (databases, report builder) and Math; the Python-UNO runtime; importers for formats Meshpool never reads (WordPerfect, Works, Apple iWork, Lotus, Hangul, StarOffice, T602), the solver, VBA support, mail merge; the dialog libraries (`cuilo`, `swuilo`, `scuilo`, `sduilo`); updater and shell integration; other windowing systems; the LibreOffice fonts Meshpool does not use (kept: Liberation, Carlito, Caladea, DejaVu, OpenSymbol, Noto Sans/Serif, Arabic and Hebrew faces; Selawik for Segoe UI comes from `engines/forge/fonts`).

Kept on purpose: the engines (`mergedlo`, `swlo`, `sclo`, `sdlo`), the Calc add-in functions (`analysislo`: EDATE, EOMONTH, NETWORKDAYS ...), the PDF, PNG and SVG filters, PDF import (`xpdfimport`, `pdfimportlo`, `share/xpdfimport`: about 18 MB, needed for PDF to Word), `pdfiumlo`, locale data (number formats of every language), ICU data, `liblangtag`, the Skia and Cairo drawing code, OpenSymbol.

**Hard dependencies.** Libreoffice on Windows and macOS links libraries into `libmergedlo` that Linux loads on demand (the WordPerfect, Works and Apple importers, lp_solve, CoinMP). A rule that names one of them cannot delete it: `prune()` reads the import tables (PE, Mach-O, ELF; small parsers, no third-party package) and keeps every library that a file which stays imports, repeating until nothing more is needed. On the real Windows tree that keeps `wpd`, `wpg`, `revenge`, `odfgen` and four more; on macOS 19 libraries. `packaging/lo_deps_check.py` re-checks a pruned tree on its own (it passes on the pruned Windows and both Mac trees, apart from the operating system's libraries). Libraries that are only loaded when a feature runs are covered by the conversions below.

**PDF to Word.** Plain `soffice --convert-to docx file.pdf` fails even in full LibreOffice ("no export filter": it opens the PDF in Draw, which cannot write Word). With `--infilter=writer_pdf_import` the same copy does write a `.docx` whose text, headings and tables come across (in text frames, so the layout is approximate), so `convert_document` passes that filter for `.pdf` to `docx`, `odt` and `rtf`, which is why PDF import stays in the pruned copy. A PDF can also become `png`, `jpg` or `svg`; any other target (pptx, xlsx ...) is refused with a message that says so. Before that fix the tool could never turn a PDF into Word.

**Proof** (`packaging/verify_lo_prune.py`, run on Linux with the same release, 26.2.7.3, from the pinned archive, and on the Debian system copy): a deck with a title, a picture, a column chart, a pie chart, a table, shapes with gradients, Arabic text, a hyperlink, speaker notes and a Segoe UI run; a workbook with formulas, EDATE/EOMONTH/NETWORKDAYS/SUMIFS/SUMPRODUCT/INDEX-MATCH/TEXT, a named range, a cross-sheet reference, a chart, validation, a filter and conditional formatting; a document with header, footer, picture, numbered list, merged-cell table and a page break. With `MH_SOFFICE` pointing at the pruned `soffice`:

| Conversion (code path) | Result |
|---|---|
| pptx to pdf and to png (`engines/forge/common.soffice_convert`) | 3 pages, text identical to the unpruned copy, 0.0000 % of the pixels differ, picture and chart drawn, Segoe UI drawn with the bundled Selawik |
| xlsx recalculation (`engines/sheets/qa.recalc`, `engines/forge/validate_xlsx.recalc`) | every value equal to the expected ones (NPV, dates, SUMIFS, TEXT ...) |
| docx to pdf and back to docx (`office_mcp_server.convert_document`) | 2 pages, text identical, header and footer present; the docx made from the PDF holds the headings, paragraphs and table text; a PDF to pptx request is refused |
| UI language set to German or Japanese with those resources pruned | all three conversions still succeed |
| `DISPLAY` set (Xvfb) | still starts (falls back to the generic plugin) |

The same script runs in `release.yml` against the pruned LibreOffice inside each built Mac and Windows tree before anything is published. The tests are `app/tests/test_lo_prune.py` (synthetic trees for all three layouts, the import readers, the rescue of linked libraries; `MH_TEST_PRUNE_SYSTEM_LO=1` also runs the proof on a pruned copy of the system LibreOffice).

## The pruned Hermes

`app_code()` copies the Hermes checkout with `bundled_tools.hermes_ignore`: it leaves out `tests` (at any depth), `website`, `apps`, `docs`, `evals`, `contributors`, `nix`, `docker`, `ui-tui`, `web`, `optional-skills`, `assets`, `scripts`, example data, the repository's readmes, Docker, flake, lock and setup files and every dotfile. What stays is every Python package the adapter can import (`acp_adapter`, `agent`, `tools`, `hermes_cli`, `gateway`, `plugins`, `providers`, `cron`, `tui_gateway`, `locales`), `skills`, `LICENSE`, `SOUL.md` and `pyproject.toml`: 199 MB to 43 MB on the local checkout of the pinned commit. `packaging/verify_hermes_prune.py` starts `python -m acp_adapter` from the copy with Meshpool's environment and a throwaway config, does `initialize` and `session/new`, and sends a prompt that a local mock OpenAI-compatible server answers: it passes (3 mock calls, reply received). It runs in `release.yml` against each built tree.

## Pins

| What | Version | SHA-256 | Source |
|---|---|---|---|
| LibreOffice macOS arm64 | 26.2.7 (build 26.2.7.3) | `58601808ae2b919c82dc7c4d6a26389de0e167f344ca48e5b6c9189218eb95ae` | `downloadarchive.documentfoundation.org/libreoffice/old/26.2.7.3/mac/aarch64/LibreOffice_26.2.7.3_MacOS_aarch64.dmg` |
| LibreOffice macOS x86_64 | 26.2.7 (26.2.7.3) | `26a830205c617cc624b2a83783ef46434a4d6c337c3a873f79c43012171f83de` | `.../mac/x86_64/LibreOffice_26.2.7.3_MacOS_x86-64.dmg` |
| LibreOffice Windows x64 | 26.2.7 (26.2.7.3) | `480fecf759fc6393bd614fbf43753eb95a8198fc1a065d41b727e3b664cd1e59` | `.../win/x86_64/LibreOffice_26.2.7.3_Win_x86-64.msi` |
| Git source (macOS build) | 2.56.1 | `f0b0bf89264b9bd2e1c640363e7529643989da76281600473c50745cd34a607f` | kernel.org `sha256sums.asc` |
| MinGit (Windows) | FILL-IN | FILL-IN | **not filled in**: GitHub was not reachable from the authoring session (403 from the API and from the release page). Run `gh release view --repo git-for-windows/git --json tagName,assets -q '.tagName, (.assets[]\|select(.name\|test("^MinGit-[0-9.]+-64-bit.zip$"))\|.name+" "+.digest)'`, then set `MINGIT_URL` and `MINGIT_SHA256` in `packaging/bundled_tools.py` and delete the unpinned download from the Windows job in `release.yml` (until then it fills `deps/git-win`, as before) |

The LibreOffice URL is the permanent archive only (the old `download.../stable/` fallback is gone: it moves on to the next release and its links then break old builds). The archive names every build; the three files above are byte for byte the ones served as 26.2.7 (checked against the SHA-256 values: all three downloaded here and matched). The hashes come from the `.sha256` files next to each download. Before the first release also verify the `.asc` signatures with gpg (`https://www.documentfoundation.org/gpg/`). The Windows extraction was run here on Linux through `build_all.libreoffice()` (`msiextract`); the macOS dmg was unpacked here with `7z` only to measure and to check library dependencies (`hdiutil` is needed for the real build).

## Size impact (measured where marked, MB = 10^6 bytes, unpacked)

"Before" is the plan as first built (everything unpruned, Codex and OpenCode in, `playwright install chromium` with its headless shell). "After" is what the build now puts in the tree. Measured = taken from a real download and unpack on the Linux authoring machine (Windows MSI with `msiextract`, both Mac dmgs with `7z`, the pinned `.deb` bundle for Linux, Playwright's own downloads with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE`); est. = scaled from those.

| Component | | macOS arm64 | macOS x86_64 | Windows x64 | Linux (proof only) |
|---|---|---|---|---|---|
| LibreOffice 26.2.7 | before | 798 (measured) | 810 (measured) | 1521 (measured) | 730 (measured, `.deb` bundle) |
| | after | **384** (measured) | **397** (measured) | **410** (measured; 391 through `build_all.libreoffice()` before PDF import was kept, which added 19) | **300** (measured) |
| | compressed after | about 89 (est., 0.23 of the `xz -6` size measured on the earlier pruned tree) | about 92 (est.) | about 103 (est., 0.25) | |
| Codex + OpenCode | before | 541 (368 + 173) | 582 (392 + 190) | 701 (501 + 200) | |
| | after | **0** | **0** | **0** | |
| Chromium (Playwright 1255, Chrome for Testing 159) | before | about 700 (est.: 397 + a headless shell like Linux's 282) | about 710 (est.) | about 760 (est.: 478 + shell) | 701 (measured: 414 + 282 + 5) |
| | after | **323** (measured) | **331** (measured) | **427** (measured) | 343 (measured, runs headed under Xvfb) |
| Hermes Agent (the `vendor-hermes` checkout) | before | 199 | 199 | 199 | |
| | after | **43** | **43** | **43** | |
| Git, Pi, Node, Python runtime | | unchanged | unchanged | unchanged | |
| **Added by LibreOffice + Codex/OpenCode + Chromium** | before | about 2040 | about 2100 | about 2980 | |
| | after | **707** | **728** | **837** | |

Hermes counts the working tree (the Hermes clone in `release.yml` loses its `.git` before the copy, so the history never ships); the 43 MB are measured on the local checkout of the pinned commit with `hermes_ignore`. Compressed (DMG ULMO, NSIS LZMA), LibreOffice shrinks to about a quarter and Chromium to about 0.4-0.45, so the installers are roughly 0.6-0.7 GB smaller than the first plan, and the three components add about +0.2 to +0.3 GB (LibreOffice 89-103 plus Chromium 130-190 compressed) per installer instead of +0.8 to +1.0 GB. Check the first real DMG and installer sizes: these are estimates. NSIS installers over 2 GB needed care before; they no longer come close.

Chromium notes: browser use opens a visible window and keeps a profile so that sign-ins persist (`MH_BROWSER_HEADLESS=1` is opt-in), so the headless shell is no use as the only browser; it is not installed (`--no-shell`, about 280 MB). Playwright 1.65's Chromium needs **macOS 13** (read from the binary: every file of both Mac builds needs 12 or 13), so on a Mac running 11 or 12 browser use says so and web search and page reading still work (`bundled.browser_needs_newer_macos`; `release.yml` exempts only `vendor/browsers` from the macOS 11 check, at 13.0). Playwright's own `ffmpeg` (video recording, needs macOS 15) is deleted.

## Audit: customer-installed tools the app used (before this work)

| Tool | Where | Shipped before? | Now |
|---|---|---|---|
| LibreOffice | `config.py`, `office_mcp_server.py`, `forge/common.py` (+ vendored hermes `pptx_render.py` via PATH) | No | Bundled; wrappers share one resolver; hermes skill sees it via PATH |
| Node.js | `harnesses/pi_rpc.py`, `core/tools.py:365`, MCP `npx`/`node`, `desktop.py` | Yes, but placed last on PATH, and `pi_rpc` fell back to PATH | Bundled first; no PATH fallback in packaged |
| `npx -y` Codex, OpenCode, Codex login | `harnesses/__init__.py`, `agent/onboard.py` | No (registry download at each first use) | Not shipped and not offered in the packaged app (no npx, no system copy); a source checkout and the benchmark keep `npx` |
| Pi | `vendor/pi` | Yes | unchanged |
| Hermes | `vendor/hermes` | Yes (release workflow, pinned commit; confirmed in `release.yml`) | Shipped without tests, website, apps and docs (199 to 47 MB) |
| Python | `python3` in agent subprocesses | App's own runtime for the app; the agent shell's `python` is the customer's by design | see "cannot be bundled" |
| Git | `plugins.py`, `mcp_apps.py`, `core/guards.py`, `core/review.py`, `engines/code/*`, `hrouter/packs.py`, `agent/core.py` | Windows yes (MinGit, on PATH in desktop shell); macOS no (Xcode CLT) | macOS bundled build; PATH first. Callers using bare `git` pick it up through PATH |
| Bash | `config.find_bash()` | Windows: MinGit existed but `find_bash` also tried Program Files; macOS `/bin/bash` (OS component) | Windows bundled only when packaged; macOS stays `/bin/bash` |
| Chrome / Edge, then downloaded Chromium | `agent/browser_mcp_server.py` | No | Bundled Chromium (full, English only), no channels, no download |
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

1. **ChatGPT sign-in in the installed app.** The ChatGPT subscription provider signs in with Codex's own login (`codex login`, which writes `~/.codex/auth.json`; Meshpool's relay then reads that file). With Codex gone from the installers, the installed app cannot start that sign-in: `login("codex")` raises "Signing in with ChatGPT needs Codex, which is not part of the Meshpool app ..." and `meshpool setup` prints the same hint. A customer who already signed in with Codex on their computer keeps working (`signed_in()` only looks for the file). The UI (`ui/app.js`) still offers "Sign in with ChatGPT" (it opens `login` on the hidden Codex row), so it shows that error. Options: hide the ChatGPT option in the installed app, or implement the sign-in natively (an OAuth PKCE flow against `auth.openai.com` with the public Codex client id that `core/chatgpt.py` already holds; about 100 lines, but it needs a live trial). Snippets for the UI are in the hand-off note of this change.
2. **Browser use needs macOS 13** (see Chromium notes). Alternatives: pin an older Playwright whose Chromium still runs on 11 (a Chromium from before mid-2025, with its security cost), or accept 13.
3. **MinGit pin**: GitHub was not reachable, so `MINGIT_URL` and `MINGIT_SHA256` are still `FILL-IN`. Until they are filled in, the Windows release job downloads MinGit unpinned into `deps/git-win` (as before).
4. First runs on real hosts: `build_git.sh`, `chromium()` on a Mac and on Windows, the macOS LibreOffice unpack with `hdiutil`, and a signing and notarization trial (LibreOffice and Chromium have nested helpers; the vendor's `_CodeSignature` is removed by the prune). The pruned-LibreOffice conversion check (`verify_lo_prune.py`) runs in `release.yml` against each built tree and stops the release if a conversion fails; that is the first time the Mac and Windows lists are executed, since only the Linux layout could be run here (the Windows and macOS trees were unpacked, pruned and checked statically with `lo_deps_check.py`).
5. Math objects (OMML equations in a .docx or .pptx) no longer render, because Math is removed as asked. Nothing in Meshpool's engines writes them.
6. LibreOffice is pinned to the permanent archive only. Consider hosting the three pinned files yourself as well.

## Release changes for auto-update (docs/plan/AUTO_UPDATE.md)

- `release.yml` "Attach to the release": writes `dist/update-manifest.json` with `packaging/updater/make_manifest.py`. The fingerprint covers `requirements-pinned.txt`, `requirements-runtime.txt`, `bundled_tools.py` (LibreOffice, Git pins and the Hermes and Chromium prune lists) and `lo_prune.py`, so any change to a bundled program's version makes `runtime_changed` true and the app takes the full installer. The manifest is in `SHA256SUMS.txt`, in the upload list and in the uploaded-names check; `release.sh` documents it as an argument. Node and Pi are versioned in the workflow itself (not fingerprinted): bump one of the listed files with them.
- NSIS: a silent install (`/S`) no longer recreates the Desktop shortcut. The files are now extracted to `$INSTDIR.new` and swapped in folder by folder, with the old folder kept as `<name>.old` and put back if a swap fails (not compiled here: `makensis` is not available; check the first build, including a silent update over an existing install).
- DMGs: `repack.sh` builds each image from a folder holding exactly one `Meshpool.app` and the `Applications` link, with no licence agreement; unchanged.
