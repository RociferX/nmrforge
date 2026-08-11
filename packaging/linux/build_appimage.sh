#!/usr/bin/env bash
# 构建 NMRForge AppImage（在 Linux 构建机/VM 上运行，见 docs/packaging.md）
# 依赖：python3.10+、pip、appimagetool（https://github.com/AppImage/appimagetool/releases）
set -euo pipefail
cd "$(dirname "$0")/../.."

APP=NMRForge
ARCH="$(uname -m)"
VERSION="$("${PYTHON:-python3}" -c 'from core import __version__; print(__version__)')"
BUILD_DIR="build/appimage"
APPDIR="$BUILD_DIR/$APP.AppDir"

rm -rf "$BUILD_DIR"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# 1) venv + 依赖
"${PYTHON:-python3}" -m venv "$BUILD_DIR/venv"
"$BUILD_DIR/venv/bin/pip" install --upgrade pip
"$BUILD_DIR/venv/bin/pip" install -e .
"$BUILD_DIR/venv/bin/pip" install pyinstaller

# 2) PyInstaller 打包（自动收集 PyQt6 插件）
"$BUILD_DIR/venv/bin/pyinstaller" --clean --noconfirm \
  --distpath "$BUILD_DIR/dist" packaging/linux/NMRForge.spec
cp -a "$BUILD_DIR/dist/$APP/." "$APPDIR/usr/bin/"

# 3) desktop 与图标
install -m 0644 packaging/linux/NMRForge.desktop "$APPDIR/usr/share/applications/"
install -m 0644 packaging/linux/icons/nmrforge.png \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
cp "$APPDIR/usr/share/applications/NMRForge.desktop" "$APPDIR/NMRForge.desktop"
cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/nmrforge.png" "$APPDIR/nmrforge.png"

# 4) appimagetool 生成 AppImage
appimagetool "$APPDIR" "$BUILD_DIR/${APP}-${VERSION}-${ARCH}.AppImage"
echo "产物：$BUILD_DIR/${APP}-${VERSION}-${ARCH}.AppImage"
