#!/usr/bin/env bash
# Local replica of .github/workflows/tests.yml (Linux pytest job). Never touches GitHub.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY=~/.venvs/meshpool/bin/python
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"   # CI: /home/runner/pw-browsers
export MH_ALLOW_SYSTEM_SOFFICE=1   # set by tests.yml: tests use the apt LibreOffice, the app itself bundles its own
# 1. system packages (CI installs these with apt-get; skipped here when present)
dpkg -s libreoffice-calc libreoffice-impress libreoffice-writer fonts-crosextra-carlito fonts-dejavu poppler-utils >/dev/null 2>&1 || \
  { apt-get update -q && apt-get install -y -q --no-install-recommends libreoffice-calc libreoffice-impress libreoffice-writer fonts-crosextra-carlito fonts-dejavu poppler-utils; }
# 2. python deps (same file CI uses, plus playwright and pytest)
uv pip install --python "$PY" -q -r "$REPO/packaging/requirements-cli.txt" playwright pytest
# 3. Segoe UI stand-in (Selawik), as in CI
mkdir -p ~/.local/share/fonts ~/.config/fontconfig
cp "$REPO"/app/engines/forge/fonts/Selawik-*.ttf ~/.local/share/fonts/
printf '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig><alias><family>Segoe UI</family><prefer><family>Selawik</family></prefer></alias></fontconfig>\n' > ~/.config/fontconfig/fonts.conf
fc-cache -f >/dev/null; fc-match "Segoe UI"
# 4. tests, then the skip count (CI: cd app; pytest -rs --junitxml, then packaging/ci_skips.py on the report)
REPORT="$(mktemp -t meshpool-pytest-XXXXXX.xml)"
cd "$REPO/app" && time "$PY" -m pytest -q -p no:cacheprovider -rs --junitxml="$REPORT" tests "$@" && \
  "$PY" "$REPO/packaging/ci_skips.py" "$REPORT"
