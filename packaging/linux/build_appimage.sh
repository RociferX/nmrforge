#!/usr/bin/env bash
# 构建 NMRForge AppImage（在 Linux 构建机/VM 上运行，见 docs/packaging.md）
# 依赖：python3.12+、pip、appimagetool（https://github.com/AppImage/appimagetool/releases）
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

# 3.5) AppRun 入口(appimagetool 不自动生成;AppImage 运行时经它启动主程序)
cat > "$APPDIR/AppRun" <<'APPRUN_EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"

# 桌面集成自安装/移除(0.2.199-补29ew):
# - 首次/移动后自动把 desktop 入口与图标装到 ~/.local/share,Exec/TryExec 指向
#   AppImage 真实路径(AppImage 文件被删除后菜单项自动隐藏);
# - 运行 --remove-desktop(或 --uninstall-desktop)移除桌面入口与图标;
# - 设 NMRFORGE_NO_DESKTOP=1 可跳过自安装。
if [ "$1" = "--remove-desktop" ] || [ "$1" = "--uninstall-desktop" ]; then
    rm -f "$HOME/.local/share/applications/nmrforge.desktop"
    rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/nmrforge.png"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
    echo "已移除 NMRForge 桌面入口与图标(项目数据不受影响;删除 AppImage 文件即完全卸载)"
    exit 0
fi

if [ -z "${NMRFORGE_NO_DESKTOP:-}" ] && [ -n "${APPIMAGE:-}" ]; then
    APPIMG_PATH="$(readlink -f "$APPIMAGE")"
    DESKTOP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
    DESKTOP_FILE="$DESKTOP_DIR/nmrforge.desktop"
    if [ ! -f "$DESKTOP_FILE" ] || ! grep -qF "Exec=$APPIMG_PATH" "$DESKTOP_FILE"; then
        mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
        sed -e "s|^Exec=.*|Exec=\"$APPIMG_PATH\"|" \
            -e "1a TryExec=$APPIMG_PATH" \
            "$HERE/NMRForge.desktop" > "$DESKTOP_FILE"
        cp -f "$HERE/nmrforge.png" "$ICON_DIR/nmrforge.png"
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
        fi
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
        fi
    fi
fi

exec "$HERE/usr/bin/NMRForge" "$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

# 4) appimagetool 生成 AppImage(runtime 优先用本地缓存,避免每次联网下载)
RUNTIME_FILE="${RUNTIME_FILE:-$HOME/.cache/nmrforge-appimage/runtime-${ARCH}}"
APPIMAGE_TOOL_ARGS=()
if [ -f "$RUNTIME_FILE" ]; then
    APPIMAGE_TOOL_ARGS+=(--runtime-file "$RUNTIME_FILE")
fi
appimagetool "${APPIMAGE_TOOL_ARGS[@]}" \
  "$APPDIR" "$BUILD_DIR/${APP}-${VERSION}-${ARCH}.AppImage"
echo "产物：$BUILD_DIR/${APP}-${VERSION}-${ARCH}.AppImage"
