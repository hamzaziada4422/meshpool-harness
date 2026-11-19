#!/bin/bash
# Build the Git that ships inside Meshpool.app (macOS has no Git without Xcode's command line tools, and the app must
# not depend on the customer's). Usage: packaging/mac/build_git.sh mac-arm64|mac-x64 OUTDIR   (run on a Mac)
# The result is OUTDIR/bin/git and OUTDIR/libexec/git-core, relocatable (RUNTIME_PREFIX), no Perl/Python/Tcl/gettext, so
# it needs only the system libraries every Mac has. build_all.py copies OUTDIR into Meshpool.app/Contents/Resources/git.
#
# NOT YET RUN: written without a Mac. First run: build both archs, then `OUTDIR/bin/git --version` (Intel one under
# Rosetta or on an Intel Mac) and `otool -L OUTDIR/bin/git` (only /usr/lib and /System). check_minos.py then verifies it.
set -euo pipefail
PLAT="${1:?mac-arm64 or mac-x64}"; OUT="${2:?output folder}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
# one source of truth for the pin: packaging/bundled_tools.py
read -r VER SHA URL < <(python3 - "$REPO" <<'PY'
import importlib.util, sys
s = importlib.util.spec_from_file_location("bt", sys.argv[1] + "/packaging/bundled_tools.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.GIT_VERSION, m.GIT_SOURCE_SHA256, m.GIT_SOURCE_URL)
PY
)
case "$PLAT" in
  mac-arm64) ARCH=arm64;  MIN=11.0 ;;
  mac-x64)   ARCH=x86_64; MIN=10.15 ;;
  *) echo "unknown platform $PLAT"; exit 1 ;;
esac
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
curl -fsSL "$URL" -o "$W/git.tar.xz"
echo "$SHA  $W/git.tar.xz" | shasum -a 256 -c -        # a tarball that does not match the pin is never built
tar -xJf "$W/git.tar.xz" -C "$W"
cd "$W/git-$VER"
FLAGS="-arch $ARCH -mmacosx-version-min=$MIN"
make -j"$(sysctl -n hw.ncpu)" prefix=/git RUNTIME_PREFIX=1 gitexecdir=libexec/git-core \
  NO_GETTEXT=1 NO_TCLTK=1 NO_PERL=1 NO_PYTHON=1 NO_EXPAT=1 NO_OPENSSL=1 \
  CFLAGS="-O2 $FLAGS" LDFLAGS="$FLAGS" all
rm -rf "$OUT"; mkdir -p "$OUT"
make prefix=/git RUNTIME_PREFIX=1 gitexecdir=libexec/git-core NO_GETTEXT=1 NO_TCLTK=1 NO_PERL=1 NO_PYTHON=1 NO_EXPAT=1 \
  NO_OPENSSL=1 CFLAGS="-O2 $FLAGS" LDFLAGS="$FLAGS" DESTDIR="$W/inst" install
cp -R "$W/inst/git/." "$OUT/"
rm -rf "$OUT/share/man" "$OUT/share/doc" "$OUT/share/git-gui" "$OUT/share/gitk" "$OUT/share/gitweb"
cp COPYING "$OUT/COPYING-git-GPL-2.0.txt"
echo "Git $VER built for $PLAT into $OUT"; "$OUT/bin/git" --version 2>/dev/null || true
