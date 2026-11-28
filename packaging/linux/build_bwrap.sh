#!/bin/bash
# Build the bubblewrap that ships inside the Linux download: the command sandbox (app/meshpool_harness/core/sandbox.py)
# uses it when the computer has no bwrap of its own, so ordinary commands run in the sandbox without a question and
# nobody has to install a package first. Usage: packaging/linux/build_bwrap.sh OUTDIR   (Linux x64, with gcc and make)
# The result is OUTDIR/bwrap, linked statically with libcap (no shared library to be missing, whatever the distribution),
# and the two licences. bubblewrap is LGPL-2.0-or-later and libcap BSD-3-Clause or GPL-2.0: both built unmodified from
# the pinned source tarballs (packaging/bundled_tools.py), whose URLs THIRD_PARTY_NOTICES.md gives.
# It is not setuid: it needs unprivileged user namespaces. Where the kernel refuses them, the app sees that bwrap cannot
# start (core/sandbox.bwrap_starts) and runs commands without it, asking before risky steps, as on Windows.
set -euo pipefail
OUT="${1:?output folder}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
read -r BV BSHA BURL CV CSHA CURL < <(python3 - "$REPO" <<'PY'
import importlib.util, sys
s = importlib.util.spec_from_file_location("bt", sys.argv[1] + "/packaging/bundled_tools.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.BWRAP_VERSION, m.BWRAP_SOURCE_SHA256, m.BWRAP_SOURCE_URL, m.LIBCAP_VERSION, m.LIBCAP_SOURCE_SHA256, m.LIBCAP_SOURCE_URL)
PY
)
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fetch() {   # url sha dest: a tarball that does not match its pin is never built
  curl -fsSL --retry 4 --retry-all-errors --retry-delay 5 --connect-timeout 30 --max-time 600 "$1" -o "$3"
  echo "$2  $3" | sha256sum -c - >/dev/null || { echo "build_bwrap.sh: $1 does not match SHA-256 $2"; exit 1; }
}
fetch "$BURL" "$BSHA" "$W/bwrap.tar.xz"
fetch "$CURL" "$CSHA" "$W/libcap.tar.xz"
tar -xJf "$W/bwrap.tar.xz" -C "$W"
tar -xJf "$W/libcap.tar.xz" -C "$W"
make -C "$W/libcap-$CV/libcap" -j"$(nproc)" libcap.a >/dev/null
cd "$W/bubblewrap-$BV"
# bubblewrap's meson.build only writes PACKAGE_STRING into config.h (SELinux off, no require_userns): the same build
# with gcc alone, so the build host needs no meson
printf '#define PACKAGE_STRING "bubblewrap %s"\n' "$BV" > config.h
gcc -static -O2 -D_GNU_SOURCE -I. -I"$W/libcap-$CV/libcap/include" -include config.h \
    -o bwrap bubblewrap.c bind-mount.c network.c utils.c "$W/libcap-$CV/libcap/libcap.a"
strip bwrap
rm -rf "$OUT"; mkdir -p "$OUT"
cp bwrap "$OUT/bwrap"
cp COPYING "$OUT/COPYING-bubblewrap-LGPL-2.0.txt"
cp "$W/libcap-$CV/License" "$OUT/LICENSE-libcap.txt"
# It must be static and run.
if readelf -d "$OUT/bwrap" 2>/dev/null | grep -q NEEDED; then
  echo "build_bwrap.sh: $OUT/bwrap links shared libraries:"; readelf -d "$OUT/bwrap" | grep NEEDED; exit 1
fi
"$OUT/bwrap" --version | grep -qx "bubblewrap $BV" || { echo "build_bwrap.sh: $OUT/bwrap does not run"; exit 1; }
echo "bubblewrap $BV built for Linux into $OUT (static, with libcap $CV)"
