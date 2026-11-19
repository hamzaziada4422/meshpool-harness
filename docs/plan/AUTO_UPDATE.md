# Automatic updates

Goal: no one downloads a new version and replaces the app by hand. Meshpool checks in the background, downloads in the
background, verifies, and installs: on one click ("Restart to update") or, with no click, when the app next quits.

Code: `app/meshpool_harness/autoupdate.py` (new), `updater.py` (check/pick/code update), `ui/server.py` (endpoints and
start-up), `ui/app.js` (update bar and About panel). Tests: `app/tests/test_updater_auto.py`.

## What changed in the repo, and the REPO fix

The git remote is `hamzaziada4422/meshpool-harness`. `updater.py`, `install.sh`, `install.ps1`, `README.md`,
`docs/SETUP.md`, `.github/ISSUE_TEMPLATE/config.yml` and the UI (`app.js`: issue link, "All releases") all said
`hamzaaziada/meshpool-harness`, so the updater asked GitHub for releases of a repository that does not exist (or is
another account's): every check said "could not reach GitHub" or found nothing. These are fixed. Still wrong, in files
owned by the packaging agent: `packaging/release.sh` line 9 and seven links in `packaging/release-notes.md`; apply
`sed -i 's#hamzaaziada/meshpool-harness#hamzaziada4422/meshpool-harness#g' packaging/release.sh packaging/release-notes.md`.
`docs/plan/HARNESS_ROUTER_PLAN.md` line 240 mentions both names as an open question; left as is. `MESHPOOL_REPO` still
overrides the repository everywhere.

## Flow

Common (Mac app and Windows app; the command-line install and a git checkout are unchanged):

1. `start_background()` runs from `server.serve()` in the desktop app. 15 s after start, then every 6 h (+0 to 5 min
   of jitter; 15 min after a failure) it calls `updater.check()`.
2. `_pick` only offers a newer stable release that has `SHA256SUMS.txt` (a release still uploading is skipped; drafts,
   pre-releases and the old 2.x line are skipped). A pre-release is never downloaded or installed automatically, even
   for a user on a pre-release (they get the "available" state and the manual button).
3. If the setting `auto_download` is on (default), the pipeline runs; otherwise the state is `available` and the About
   panel offers "Download update".
4. Download: `<data folder>/updates/<tag>/<asset>.part`, resumable (HTTP Range; a server that ignores Range restarts
   the file; a partial file survives quitting the app). Needs about 2.3 times the file size free.
5. Verify: SHA-256 against `SHA256SUMS.txt`. Refused (file deleted, retried in 15 min) when the release has no
   `SHA256SUMS.txt`, the file is not listed, or the hash differs (sums are re-read once in case a release was
   re-uploaded). No unverified file is ever installed or opened.
6. State `ready` is saved to `updates/ready.json` (it survives a restart of the app). The bar shows "Update ready -
   Restart". `POST /api/update/apply` starts the helper and quits the app; if the user does nothing, the same helper
   starts when the app quits (`ON_QUIT` hook, setting `install_on_quit`, default on). It is refused while a task runs.
7. The helper writes `updates/result.json`; the app shows "Meshpool was updated" (or why not) once at the next start.
   Log: `updates/apply.log`.

Settings live in `<data folder>/updates/settings.json` (`auto_download`, `install_on_quit`) so `config.py` is not
touched; `MESHPOOL_AUTO_UPDATE=0` turns auto-download off, `MESHPOOL_NO_UPDATE=1` turns the whole thing off.

### Mac (signed app: nothing inside the bundle is ever modified)

Stage (in the background, right after the download is verified):

- `hdiutil attach -nobrowse -readonly -noverify -noautoopen -plist -mountpoint <updates/tag/mnt> Meshpool.dmg`
- `ditto` the `.app` to a staging folder: `.Meshpool-update-<ver>/` next to the current app if its folder is writable,
  else `updates/<tag>/stage/` (and the swap then needs administrator rights). `hdiutil detach -force` always runs.
- Checks on the staged app: `codesign --verify --deep --strict`; same `CFBundleIdentifier` as the running app;
  `CFBundleShortVersionString` equals the release tag and is newer than the running version; the executable exists;
  same Team ID as the running app when that one has one; `spctl -a -t exec` must pass whenever it passes for the running
  app (a signed and notarized lineage). Any failure deletes the staging folder and shows the message with a link to the
  release page. It never falls back to opening the DMG by itself.
- Refused up front when the app runs from a disk image or from App Translocation (`/Volumes/...`, `AppTranslocation`):
  the message says to move it to Applications first.

Apply (after the app quits), `updates/apply-mac.sh`, started with `nohup /bin/bash` in a new session:

1. wait for the old process to exit (`kill -0`, up to 2 minutes; if it never exits, nothing is changed);
2. `codesign --verify --deep --strict` the staged app again (it sat in a user-writable folder);
3. `rm -rf Meshpool.app.previous && mv Meshpool.app Meshpool.app.previous && mv <staged> Meshpool.app`; if the second
   `mv` fails, the first is undone;
4. `xattr -dr com.apple.quarantine` only if `xattr -p` says it is there;
5. `open Meshpool.app`, wait up to 30 s for a process from `Meshpool.app/Contents/MacOS/`; then delete the `.previous`
   copy and the staging folder; if no process appears, roll back (new one renamed to `.failed` and deleted, `.previous`
   moved back) and `open` the old app;
6. Folder not writable (for example a root-owned /Applications): the same `mv`/`rm` commands run through
   `osascript ... with administrator privileges` (the macOS password prompt). The commands are passed inline to
   osascript; no user-writable script ever runs as root. If the user cancels, the old app is relaunched and a message is
   shown once; the next update asks again.

### Windows

The NSIS installer (`packaging/nsis/installer.nsi`, `RequestExecutionLevel user`, `InstallDirRegKey HKCU`) installs to
`$LOCALAPPDATA\Programs\Meshpool` and needs no admin rights, so it can run silently. After the app quits,
`updates/apply-win.ps1` (started by `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle
Hidden -File ...`, detached, no console window):

1. waits for the app process (120 s); 2. re-hashes the installer; refuses a bad Authenticode status (`HashMismatch`,
`NotTrusted`; an unsigned installer is allowed because signing is not set up for every build, its SHA-256 is what is
checked); 3. runs `Meshpool-Setup.exe /S /D=<install dir>` (`/D=` is last and unquoted, as NSIS requires; the
install dir is the folder that holds `Meshpool.exe`); 4. starts `Meshpool.exe` again (also when the installer failed, so the
user is not left with nothing). The installer itself runs `taskkill /F /IM Meshpool.exe` first and keeps `%APPDATA%\Meshpool`.

When the release's manifest says `runtime_changed: false`, the Windows app keeps the existing in-place code update
(`updater.install(auto=True)`): the code archive `Meshpool-src.tar.gz` is checked against SHA256SUMS, swapped by rename
with rollback, and `ready` (mode `code`) shows "Restart" as before. It runs in the background only when no task is running.

### Command line and git checkout

`kind() == "cli"`: unchanged (the page shows "Update now", the in-place code update runs from the button). The
background loop only checks. `kind() == "dev"`: no background work; "git pull" message as before.

## What the release must publish

| Asset | Purpose |
| --- | --- |
| `Meshpool.dmg` (Apple silicon), `Meshpool-Intel.dmg` | one `Meshpool.app` at the root of the image |
| `Meshpool-Setup.exe` | NSIS installer, silent-capable |
| `Meshpool-src.tar.gz` | code archive (git archive, prefix `meshpool-harness/`), for the in-place code update |
| `update-manifest.json` | `{"version": "0.2.0", "runtime_changed": true, "runtime_fingerprint": "...", "min_manifest": 1}` |
| `SHA256SUMS.txt` | `sha256  filename` for every asset above, including `update-manifest.json` |

`runtime_changed` is false only when the bundled Python, pinned packages and bundled tools (LibreOffice, Node, Codex,
OpenCode, Pi, Hermes, git) are identical to the previous release. The manifest is trusted only if listed in
`SHA256SUMS.txt` with a matching hash. No manifest means "runtime changed": the full installer is used.
`packaging/updater/make_manifest.py` (new) writes it from a list of runtime files and the previous release's manifest.

## Exact changes for the packaging agent (not applied, packaging/ is not mine to edit)

1. Repo name in `packaging/release.sh` and `packaging/release-notes.md` (the sed above).
2. `.github/workflows/release.yml`, in the step near line 284 (the "Release" step), before computing the sums:

   ```bash
   gh release download "$(gh release list --exclude-pre-releases --limit 1 --json tagName -q '.[0].tagName')" \
     --pattern update-manifest.json --dir prev 2>/dev/null || true
   python3 packaging/updater/make_manifest.py --version "$VER" \
     --runtime-file packaging/requirements-pinned.txt --runtime-file packaging/requirements-runtime.txt \
     --runtime-file packaging/bundled_tools.py --previous prev/update-manifest.json -o dist/update-manifest.json
   (cd dist && sha256sum Meshpool.dmg Meshpool-Intel.dmg Meshpool-Setup.exe Meshpool-src.tar.gz update-manifest.json > SHA256SUMS.txt)
   ```

   and add `dist/update-manifest.json` to the `gh release upload` list and to the `printf ... | diff` check of uploaded
   names (lines 301 and 304). `--runtime-file` should list every file that decides what is bundled (the WP13 pinned tool
   versions file; adjust the paths).
3. `packaging/release.sh`: add `update-manifest.json` to its usage line/arguments (the script already hashes every file
   given and uploads `SHA256SUMS.txt`); the manifest must be among the files passed in.
4. `packaging/nsis/installer.nsi`: so a silent update does not undo user choices and is safer to interrupt:

   ```nsis
   !include "LogicLib.nsh"
   ...
   ${IfNot} ${Silent}
     CreateShortcut "$DESKTOP\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
   ${EndIf}
   ```
   (the Start Menu shortcut stays). Optional hardening: have the installer extract to `$INSTDIR.new`, then rename old
   to `.old` and new into place, deleting `.old` last, instead of `RMDir /r` first, so a failure halfway (power loss,
   antivirus) leaves a working copy. NSIS cannot roll back by itself.
5. Mac DMG: keep exactly one `*.app` at the image root and no license agreement (silent `hdiutil attach` cannot accept one).
6. Signing: with the apps only ad hoc signed (no Apple Developer secrets yet) `spctl` has no verdict; auto-install still
   happens, protected by the SHA-256 from the same GitHub release, the bundle id and version checks. Once the Developer ID
   secrets are set, the Team ID and Gatekeeper checks turn on by themselves.

## Code-only updates on the Mac (not implemented)

Re-downloading about 1 GB for a code fix is the cost of never touching the bundle. The clean way that still does not modify the
signed bundle is a code overlay: the app downloads `Meshpool-src.tar.gz` (a few MB, checked against SHA256SUMS), extracts
`app/` to `~/Library/Application Support/Meshpool/code/<ver>/`, writes `overlay.json` with the per-file SHA-256s, the
`base_version` of the bundle it is valid for and a hash of that file listed in SHA256SUMS, and the launcher puts it first
on the import path: `PYTHONPATH="$OVERLAY:$RES/app"` when `overlay.json` verifies and `base_version` equals the bundle's
version, otherwise it is ignored (so a new full app always wins). Python imports from outside a signed app are not a
signature or notarization problem; the bundle itself is untouched. It is not implemented here because it needs a change
in the compiled launcher (`Contents/MacOS/Meshpool`) and `packaging/mac/meshpool`, which are packaging files; and the
overlay may only be used when the manifest says `runtime_changed: false`. Suggested launcher snippet (bash launcher):

```bash
OV="$MESHPOOL_HOME/code/current"
if [ -f "$OV/overlay.json" ] && /usr/bin/python3 -I "$RES/overlay_check.py" "$OV" "$BUNDLE_VERSION"; then
  export PYTHONPATH="$OV/app:$RES/app"
fi
```

## Decisions for the owner

1. Which repository is the home: `hamzaziada4422/meshpool-harness` (the git remote) or `hamzaaziada/...`? Everything now follows the remote.
2. Install on quit is on by default, like Chrome. Say if it should be click-only.
3. Unsigned Mac builds: auto-update trusts the SHA-256 of the GitHub release only (any one who can edit the release can ship
   code). A Developer ID certificate makes the Team ID and Gatekeeper checks bind updates to the developer.
4. A release without `update-manifest.json` triggers a full installer download on Windows (before, a code update). Publish the manifest to get small updates.
