#!/bin/bash
# Build the Git that ships inside Meshpool.app (macOS has no Git without Xcode's command line tools, and the app must
# not depend on the customer's). Usage: packaging/mac/build_git.sh mac-arm64|mac-x64 OUTDIR   (run on a Mac)
# The result is OUTDIR/bin/git and OUTDIR/libexec/git-core, relocatable (RUNTIME_PREFIX), no Perl/Python/Tcl/gettext, so
# it needs only the system libraries every Mac has. build_all.py copies OUTDIR into Meshpool.app/Contents/Resources/git.
#
# The end of this script checks the result: one architecture, only /usr/lib and /System libraries, and `git --version`
# runs (the Intel one under Rosetta). check_minos.py then checks the macOS version it needs.
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
  mac-x64)   ARCH=x86_64; MIN=11.0 ;;
  *) echo "unknown platform $PLAT"; exit 1 ;;
esac
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
curl -fsSL "$URL" -o "$W/git.tar.xz"
echo "$SHA  $W/git.tar.xz" | shasum -a 256 -c -        # a tarball that does not match the pin is never built
tar -xJf "$W/git.tar.xz" -C "$W"
cd "$W/git-$VER"
FLAGS="-arch $ARCH -mmacosx-version-min=$MIN"
# NO_RUST: Git 2.56 builds part of itself in Rust by default, with cargo for the build machine's CPU only, so the
#   Intel build would link an arm64 library (and stop where cargo is missing).
# NO_HOMEBREW: on macOS 15+ Git links Homebrew's libiconv and gettext when the build machine has them, and the app
#   would then need /opt/homebrew on every customer's Mac. The system libiconv and libcurl are on every Mac.
MAKEVARS=(prefix=/git RUNTIME_PREFIX=1 gitexecdir=libexec/git-core NO_GETTEXT=1 NO_TCLTK=1 NO_PERL=1 NO_PYTHON=1
          NO_EXPAT=1 NO_OPENSSL=1 NO_RUST=1 NO_HOMEBREW=1 CURL_CONFIG=/usr/bin/curl-config CURL_LDFLAGS=-lcurl)
make -j"$(sysctl -n hw.ncpu)" "${MAKEVARS[@]}" CFLAGS="-O2 $FLAGS" LDFLAGS="$FLAGS" all
rm -rf "$OUT"; mkdir -p "$OUT"
# NO_INSTALL_HARDLINKS: git installs its ~140 built-in commands as hard links to one binary, and the copies made
# below (cp, build_all.py's copytree) would turn each into a full copy of it; symlinks stay symlinks.
make "${MAKEVARS[@]}" NO_INSTALL_HARDLINKS=1 CFLAGS="-O2 $FLAGS" LDFLAGS="$FLAGS" DESTDIR="$W/inst" install
cp -R "$W/inst/git/." "$OUT/"
rm -rf "$OUT/share/man" "$OUT/share/doc" "$OUT/share/git-gui" "$OUT/share/gitk" "$OUT/share/gitweb"
cp COPYING "$OUT/COPYING-git-GPL-2.0.txt"
# The build must be for this CPU only, link nothing outside the system, and run.
archs="$(lipo -archs "$OUT/bin/git")"
[ "$archs" = "$ARCH" ] || { echo "build_git.sh: $OUT/bin/git is for '$archs', not $ARCH"; exit 1; }
bad=""
for f in "$OUT/bin/git" "$OUT/libexec/git-core/git-remote-http" "$OUT/libexec/git-core/git-http-fetch"; do
  [ -f "$f" ] || continue
  libs="$(otool -L "$f" | tail -n +2 | awk '{print $1}' | grep -vE '^(/usr/lib/|/System/)' || true)"
  [ -z "$libs" ] || bad="$bad $f: $libs;"
done
[ -z "$bad" ] || { echo "build_git.sh: links libraries a customer's Mac does not have:$bad"; exit 1; }
if [ "$ARCH" = x86_64 ]; then v="$(arch -x86_64 "$OUT/bin/git" --version)"; else v="$("$OUT/bin/git" --version)"; fi
echo "Git $VER built for $PLAT into $OUT: $v"
