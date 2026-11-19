#!/bin/bash
# Refresh the Meshpool app code inside existing Mac app trees, then recompile, sign and build the dmgs.
# Usage: packaging/mac/repack.sh [WORK]
#   WORK holds app-mac-arm64/Meshpool.app and app-mac-x64/Meshpool.app (from build_all.py and build_mac.sh).
#   Output: WORK/out/Meshpool.dmg (Apple silicon) and WORK/out/Meshpool-Intel.dmg.
# Run it on a Mac, from anywhere.
#
# Signing: with the Apple Developer secrets set (see packaging/mac/sign.sh) the apps are signed with the Developer ID
# certificate and the dmgs are notarized and stapled, so they open with no Gatekeeper warning. Without them the apps
# are ad hoc signed, as before, and the build still works.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
W="${1:-$HOME/Meshpool-Build/v4/work}"; OUT="$W/out"
rm -rf "${OUT:?}"; mkdir -p "$OUT"
# MAC_ARCHS: the apps to build ("arm64 x64" by default; the release builds each on a runner of its own CPU)
ARCHS="${MAC_ARCHS:-arm64 x64}"
# compileall with an app's own Python that this Mac runs natively (the bytecode is the same for both CPUs)
HOSTA=x64; [ "$(uname -m)" = arm64 ] && HOSTA=arm64
PYA=$HOSTA; [ -d "$W/app-mac-$PYA/Meshpool.app" ] || PYA="${ARCHS%% *}"
PY="$W/app-mac-$PYA/Meshpool.app/Contents/Resources/python/bin/python3"
# The version comes from one place, app/meshpool_harness/__init__.py; Info.plist holds placeholders for it.
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/app/meshpool_harness/__init__.py")"
NUM="$(printf '%s' "$VER" | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+')"
[ -n "$NUM" ] || { echo "Could not read the version from app/meshpool_harness/__init__.py"; exit 1; }
for a in $ARCHS; do
  APP="$W/app-mac-$a/Meshpool.app"; R="$APP/Contents/Resources"
  for d in meshpool_harness engines profiles defaults; do
    rm -rf "${R:?}/app/$d"; rsync -a --exclude __pycache__ --exclude '*.pyc' "$REPO/app/$d/" "$R/app/$d/"
  done
  cp "$REPO/THIRD_PARTY_NOTICES.md" "$REPO/app/requirements.txt" "$REPO/packaging/requirements-cli.txt" "$R/app/"
  cp "$REPO/packaging/hermes-NOTICE-MESHPOOL.txt" "$R/app/vendor/hermes/NOTICE-MESHPOOL.txt"
  # Hermes arrives patched (deps/vendor-hermes); patching again here wrote *.rej files into the app
  find "$R/app/vendor/hermes" \( -name '*.rej' -o -name '*.orig' \) -delete
  # The oldest macOS the app opens on: 11 on Apple silicon and on Intel (MIN_MACOS in packaging/build_all.py)
  MIN=11.0
  sed -e "s/@VERSION_NUM@/$NUM/g" -e "s/@VERSION@/$VER/g" -e "s/@MIN_MACOS@/$MIN/g" "$REPO/packaging/mac/Info.plist" > "$APP/Contents/Info.plist"
  for d in meshpool_harness engines profiles vendor/hermes/acp_adapter; do
    PYTHONPATH= "$PY" -m compileall -q -j0 --invalidation-mode unchecked-hash -x 'tests?/|test_' \
      -s "$R" -p /Applications/Meshpool.app/Contents/Resources "$R/app/$d" >/dev/null 2>&1
  done
  xattr -cr "$APP"
done
# Sign the app trees before the dmgs are built, so the copy inside each image is already signed. sign.sh does
# nothing (and exits 0) when the Apple Developer secrets are not set.
bash "$REPO/packaging/mac/sign.sh" sign "$W"
for a in $ARCHS; do
  APP="$W/app-mac-$a/Meshpool.app"
  # Without the Developer ID secrets sign.sh did nothing, so the app is still ad hoc signed here, as before.
  if [ -z "${MAC_SIGN_IDENTITY:-}" ] || [ -z "${MAC_CERT_P12_BASE64:-}" ]; then
    codesign --force --deep --sign - "$APP" 2>&1 | grep -v "replacing existing" | tail -2
  fi
  # A broken signature must stop the release: the auto-updater refuses an app that fails this exact check.
  codesign --verify --deep --strict "$APP" || { echo "repack.sh: $APP fails codesign --verify --deep --strict"; exit 1; }
  echo "signature ok ($a) $(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP/Contents/Info.plist")"
  ST="$W/stage-$a"; rm -rf "$ST"; mkdir -p "$ST"; ditto "$APP" "$ST/Meshpool.app"; ln -s /Applications "$ST/Applications"
  SIZE_MB=$(( $(du -sm "$ST" | cut -f1) * 12 / 10 + 200 ))
  NAME=Meshpool; [ $a = x64 ] && NAME=Meshpool-Intel
  rm -f "$W/$NAME-udzo.dmg"
  # hdiutil sometimes fails with "Resource busy" on a loaded machine; try again before giving up
  ok=; for try in 1 2 3 4; do
    # an explicit size: hdiutil's own estimate for ~50,000 files can come out too small ("No space left on device")
    hdiutil create -quiet -volname Meshpool -srcfolder "$ST" -ov -format UDZO -size "${SIZE_MB}m" "$W/$NAME-udzo.dmg" && { ok=1; break; }
    echo "hdiutil create failed (attempt $try), retrying"; sleep $((try * 10))
  done
  [ -n "$ok" ] || exit 1
  # convert fails the same way now and then (seen: the Intel image silently missing); retry, then stop the build
  ok=; for try in 1 2 3 4; do
    rm -f "$OUT/$NAME.dmg"
    hdiutil convert -quiet "$W/$NAME-udzo.dmg" -format ULMO -o "$OUT/$NAME.dmg" && [ -s "$OUT/$NAME.dmg" ] && { ok=1; break; }
    echo "hdiutil convert failed (attempt $try), retrying"; sleep $((try * 10))
  done
  [ -n "$ok" ] || { echo "could not build $NAME.dmg"; exit 1; }
  rm -f "$W/$NAME-udzo.dmg"; rm -rf "$ST"; echo "built $NAME.dmg"; df -h "$W" | tail -1      # disk is tight on the runner
done
for a in $ARCHS; do        # never hand the publish step half a release
  f=Meshpool.dmg; [ "$a" = x64 ] && f=Meshpool-Intel.dmg
  [ -s "$OUT/$f" ] || { echo "missing $f"; exit 1; }
done
# Notarize, staple and check both disk images. sign.sh does nothing (and exits 0) when the Apple Developer secrets
# are not set, so this is safe before the owner's accounts are ready.
bash "$REPO/packaging/mac/sign.sh" notarize "$W"
ls -la "$OUT"
