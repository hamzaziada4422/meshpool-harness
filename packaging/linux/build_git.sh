#!/bin/bash
# Build the Git that ships inside the Linux download (a desktop may have no Git, and the packaged app must not depend
# on the customer's). Usage: packaging/linux/build_git.sh OUTDIR   (run on Linux x64; the release builds on Ubuntu
# 22.04, so the result runs on glibc 2.35 and later)
# The result is OUTDIR/bin/git and OUTDIR/libexec/git-core, relocatable (RUNTIME_PREFIX), with no Perl, Python, Tcl or
# gettext. It links only libc, zlib and libcurl, which every desktop Linux has; the end of this script checks that.
set -euo pipefail
OUT="${1:?output folder}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
# one source of truth for the pin: packaging/bundled_tools.py
read -r VER SHA URL < <(python3 - "$REPO" <<'PY'
import importlib.util, sys
s = importlib.util.spec_from_file_location("bt", sys.argv[1] + "/packaging/bundled_tools.py"); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.GIT_VERSION, m.GIT_SOURCE_SHA256, m.GIT_SOURCE_URL)
PY
)
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
F="${URL##*/}"; ok=
for u in "$URL" "https://cdn.kernel.org/pub/software/scm/git/$F" "https://www.kernel.org/pub/software/scm/git/$F"; do
  if curl -fsSL --retry 4 --retry-all-errors --retry-delay 5 --connect-timeout 30 --max-time 600 "$u" -o "$W/git.tar.xz" \
     && echo "$SHA  $W/git.tar.xz" | sha256sum -c - >/dev/null; then ok=1; break; fi
  echo "build_git.sh: $u failed, trying the next mirror"
done
[ -n "$ok" ] || { echo "build_git.sh: could not download $F with SHA-256 $SHA from any mirror"; exit 1; }
echo "$SHA  $W/git.tar.xz" | sha256sum -c -           # a tarball that does not match the pin is never built
tar -xJf "$W/git.tar.xz" -C "$W"
cd "$W/git-$VER"
MAKEVARS=(prefix=/git RUNTIME_PREFIX=1 gitexecdir=libexec/git-core NO_GETTEXT=1 NO_TCLTK=1 NO_PERL=1 NO_PYTHON=1
          NO_EXPAT=1 NO_OPENSSL=1 NO_RUST=1)
make -j"$(nproc)" "${MAKEVARS[@]}" CFLAGS="-O2" all
rm -rf "$OUT"; mkdir -p "$OUT"
make "${MAKEVARS[@]}" NO_INSTALL_HARDLINKS=1 CFLAGS="-O2" DESTDIR="$W/inst" install
cp -R "$W/inst/git/." "$OUT/"
rm -rf "$OUT/share/man" "$OUT/share/doc" "$OUT/share/git-gui" "$OUT/share/gitk" "$OUT/share/gitweb"
cp COPYING "$OUT/COPYING-git-GPL-2.0.txt"
# It must link nothing a desktop Linux lacks, and run.
bad=""
for f in "$OUT/bin/git" "$OUT/libexec/git-core/git-remote-http"; do
  [ -f "$f" ] || continue
  # only what git itself links (readelf NEEDED), not libcurl's own dependencies, which come with libcurl
  libs="$(readelf -d "$f" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | grep -vE '^(libc\.so|libm\.so|libz\.so|libcurl\.so|libpthread|libdl|librt)' || true)"
  [ -z "$libs" ] || bad="$bad $f: $libs;"
done
[ -z "$bad" ] || { echo "build_git.sh: links libraries outside the allowed set:$bad"; exit 1; }
echo "Git $VER built for Linux into $OUT: $("$OUT/bin/git" --version)"
