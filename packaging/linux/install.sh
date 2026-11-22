#!/bin/bash
# Add Meshpool to your menu and the meshpool command to your PATH, for this user only (nothing system-wide).
#   ./install.sh              set up from the folder this script is in
#   ./install.sh --uninstall  remove the menu entry and the command (the folder and your data stay)
set -euo pipefail
ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$BIN/meshpool" "$APPS/meshpool.desktop"
  echo "Removed the Meshpool menu entry and the meshpool command. Your data in ~/.config/meshpool is kept."
  exit 0
fi
[ "$(uname -m)" = x86_64 ] || { echo "This download is for x86_64 (64-bit Intel or AMD); this computer is $(uname -m)."; exit 1; }
mkdir -p "$BIN" "$APPS"
ln -sf "$ROOT/meshpool" "$BIN/meshpool"
sed -e "s|__EXEC__|\"$ROOT/Meshpool\"|" -e "s|__ICON__|$ROOT/meshpool.png|" "$ROOT/meshpool.desktop" > "$APPS/meshpool.desktop"
chmod 644 "$APPS/meshpool.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "Meshpool is set up."
echo "  Open it from your applications menu, or run: $ROOT/Meshpool"
echo "  Terminal command: meshpool   (in $BIN)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  Add $BIN to your PATH to use the meshpool command in new terminals.";; esac
