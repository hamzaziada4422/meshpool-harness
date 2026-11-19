#!/usr/bin/env bash
# MeshPool preflight: everything that must pass before a push to GitHub Actions or a release. It never touches GitHub.
#
#   bash testkit/preflight.sh          # fast checks + the full test suite in a clean CI-like environment
#   bash testkit/preflight.sh --fast   # fast checks only (about a minute): lint, compile, installer, routing set
#
# Exit 0 only when every check passed. Each check prints PASS or FAIL; the last line is the verdict.
# Needs: ~/.venvs/meshpool (dev venv). Creates ~/.venvs/ci (exactly what .github/workflows/tests.yml installs) once.
# Optional tools are installed on first use where possible: actionlint (go), shellcheck and makensis (apt).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
FAST=0; [ "${1:-}" = "--fast" ] && FAST=1
DEV=~/.venvs/meshpool/bin/python
CI_PY=~/.venvs/ci/bin/python
LOG="${PREFLIGHT_LOG:-$REPO/runs-v2/preflight}"; mkdir -p "$LOG"
fails=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; fails=$((fails + 1)); }
run() {  # name, command...: runs it, keeps its output in $LOG/<name>.log
  local name="$1"; shift
  if "$@" > "$LOG/$name.log" 2>&1; then pass "$name"; else fail "$name (see $LOG/$name.log)"; tail -15 "$LOG/$name.log" | sed 's/^/      /'; fi
}

# ---------- tools ---------------------------------------------------------------------------------------------------
AL="$(command -v actionlint || echo "$HOME/go/bin/actionlint")"
[ -x "$AL" ] || GOBIN="$HOME/go/bin" go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.7 >/dev/null 2>&1
command -v shellcheck >/dev/null || apt-get install -y -q shellcheck >/dev/null 2>&1
command -v makensis >/dev/null || apt-get install -y -q nsis >/dev/null 2>&1

# ---------- 1. GitHub workflows: expressions, job conditions, inputs, and the shell inside run: steps ----------------
if [ -x "$AL" ]; then
  # -ignore SC2086: the workflows split $RUNTIME_EXTRAS and `set -- $pair` on purpose
  run workflows "$AL" -shellcheck="$(command -v shellcheck || true)" -ignore 'SC2086' .github/workflows/release.yml .github/workflows/tests.yml
else fail "workflows (actionlint not available)"; fi

# ---------- 2. packaging shell scripts (errors only; the known warnings are intentional) ----------------------------
if command -v shellcheck >/dev/null; then
  run shell-scripts shellcheck -S error packaging/mac/sign.sh packaging/mac/repack.sh packaging/mac/build_git.sh \
    packaging/release.sh packaging/mac/test.sh docs/plan/ci_local.sh testkit/preflight.sh
else fail "shell-scripts (shellcheck not available)"; fi

# ---------- 3. every Python file compiles, every front-end JavaScript file parses ------------------------------------
run python-compile "$DEV" -m compileall -q app/meshpool_harness app/engines packaging testkit/loop
js_check() { local f; for f in app/meshpool_harness/ui/*.js; do node --check "$f" || return 1; done; }
run javascript js_check

# ---------- 4. the Windows installer compiles (stamped the way build_all.py stamps it) -------------------------------
nsis_check() {
  command -v makensis >/dev/null || { echo "makensis not available"; return 1; }
  local T; T="$(mktemp -d)"
  mkdir -p "$T/Meshpool/app" "$T/Meshpool/runtime" "$T/Meshpool/bin"
  cp packaging/nsis/*.nsi packaging/nsis/*.nsh packaging/nsis/*.ps1 "$T/" && cp packaging/icons/Meshpool.ico "$T/Meshpool/Meshpool.ico"
  echo x > "$T/Meshpool/app/a.txt"; echo x > "$T/Meshpool/Meshpool.exe"; echo x > "$T/Meshpool/LICENSE.txt"
  local VER; VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/meshpool_harness/__init__.py)
  sed -i -e "s/@VERSION_NUM@/$VER/g" -e "s/@VERSION@/$VER/g" -e 's/@BETA@/ beta/g' "$T/installer.nsi"
  (cd "$T" && makensis -V2 installer.nsi) && [ -f "$T/Meshpool Setup.exe" ]
  local rc=$?; rm -rf "$T"; return $rc
}
run windows-installer nsis_check

# ---------- 5. versions agree (release.yml tags v<__version__>) ------------------------------------------------------
versions() {
  local a b; a=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/meshpool_harness/__init__.py)
  b=$(sed -n 's/^version = "\(.*\)"/\1/p' app/pyproject.toml)
  echo "__init__ $a, pyproject $b"; [ -n "$a" ] && [ "$a" = "$b" ]
}
run versions versions

# ---------- 6. MeshPool Auto routes every dev task to the right sub-harness (zero model calls) -----------------------
routing() {
  (cd app && MH_SLOTS_DIR="$(mktemp -d)" "$DEV" -m pytest -q -p no:cacheprovider tests/test_routing_v3.py tests/test_hrouter.py \
     tests/test_harness_router.py tests/test_profiles.py)
}
run routing routing

if [ $FAST = 1 ]; then
  echo; [ $fails = 0 ] && { echo "PREFLIGHT (fast) PASSED"; exit 0; } || { echo "PREFLIGHT (fast) FAILED: $fails check(s)"; exit 1; }
fi

# ---------- 7. the full test suite exactly as GitHub runs it: clean venv, CI=1 (no silent skips) ---------------------
if [ ! -x "$CI_PY" ]; then
  uv venv -q -p 3.12 ~/.venvs/ci && VIRTUAL_ENV=~/.venvs/ci uv pip install -q -r packaging/requirements-cli.txt playwright==1.63.0 pytest
fi
full_suite() {
  (cd app && CI=1 PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}" MH_ALLOW_SYSTEM_SOFFICE=1 \
     MH_SLOTS_DIR="$(mktemp -d)" "$CI_PY" -m pytest -q -p no:cacheprovider tests)
}
run full-test-suite full_suite
grep -E "[0-9]+ passed" "$LOG/full-test-suite.log" | tail -1 | sed 's/^/      /'

echo; [ $fails = 0 ] && { echo "PREFLIGHT PASSED"; exit 0; } || { echo "PREFLIGHT FAILED: $fails check(s)"; exit 1; }
