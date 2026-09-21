#!/usr/bin/env bash
# Install the NMRForge desktop entry and icon (development-mode runs).
#
# GNOME does not show a window icon in the title bar by default; the taskbar/launcher icon comes from the .desktop
# file's Icon= entry plus the hicolor icon theme. For development runs (venv + python main.py)
# this script installs the icon and launcher entry into the user directory, so launcher and taskbar show the icon
# (0.2.199-patch29eq).
#
# Usage: bash scripts/install_desktop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=NMRForge

VENV_PY="$ROOT/nmrforge/bin/python"
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="$ROOT/nmrforge/Scripts/python.exe"
fi
if [ ! -x "$VENV_PY" ]; then
  echo "error: venv python not found ($ROOT/nmrforge/bin/python)" >&2
  exit 1
fi

# 1) icon -> hicolor theme (GNOME and other DEs look it up by theme name)
ICON_DST="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICON_DST"
install -m 0644 "$ROOT/gui/assets/nmrforge.png" "$ICON_DST/nmrforge.png"

# 2) desktop entry (Exec points at the current development launch command)
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NMRForge
GenericName=NMR Processing and Optimization
Comment=Automated processing, optimization and QC for Bruker 2D/3D NMR data
Exec=$VENV_PY $ROOT/main.py --inside-venv
Icon=nmrforge
Terminal=false
Categories=Science;Chemistry;Education;
Keywords=NMR;Spectroscopy;Bruker;NUS;Processing;QC;
StartupNotify=true
EOF

# 3) refresh the icon/desktop database (when available)
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "installed: $APPS_DIR/$APP.desktop"
echo "icon: $ICON_DST/nmrforge.png"
echo "note: restart the running app and GNOME will pick the icon up in the launcher / task bar;"
echo "if it still does not show up, log out and back in once."
