#!/usr/bin/env bash
# 构建 NMRForge AppImage（在 Linux 构建机/VM 上运行，见 docs/packaging.md）
# 依赖：python3.12+、pip、appimagetool（https://github.com/AppImage/appimagetool/releases）
#
# 许可（LGPL）相关：AppImage 会把 PySide6/Qt 打进产物，因此属于「分发 LGPL 库」，
# 必须随产物提供许可正文与声明，并允许接收方替换/重链接该库。本脚本因此：
#   - 构建前校验 packaging/linux/THIRD_PARTY_LICENSES 的正文与哈希；
#   - 把该目录复制进 AppDir（usr/share/doc/NMRForge/third-party/），产物内可提取；
#   - 写入 BUILD_INFO.txt 记录版本/提交/依赖版本，作为「对应源码」的定位依据；
#   - 支持用自建 PySide6/shiboken6 替换默认库后重建（即 LGPL 的可替换机制）：
#       PYSIDE6_REQUIREMENT="PySide6 @ file:///path/to/PySide6-....whl" \
#       EXTRA_PIP_ARGS="--find-links /path/to/wheels" \
#       bash packaging/linux/build_appimage.sh
#   详见 packaging/linux/THIRD_PARTY_LICENSES/NOTICE.md。
set -euo pipefail
cd "$(dirname "$0")/../.."

# 可替换库（留空 = 用 pyproject.toml 声明的版本）
PYSIDE6_REQUIREMENT="${PYSIDE6_REQUIREMENT:-}"
SHIBOKEN6_REQUIREMENT="${SHIBOKEN6_REQUIREMENT:-}"
EXTRA_PIP_ARGS="${EXTRA_PIP_ARGS:-}"
LICENSE_DIR="packaging/linux/THIRD_PARTY_LICENSES"

APP=NMRForge
ARCH="$(uname -m)"
VERSION="$("${PYTHON:-python3}" -c 'from core import __version__; print(__version__)')"
BUILD_DIR="build/appimage"
APPDIR="$BUILD_DIR/$APP.AppDir"

# 0) 许可文本完整性（缺失或被改动即失败，避免产物静默漏发许可）
"${PYTHON:-python3}" scripts/check_third_party_licenses.py

rm -rf "$BUILD_DIR"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# 1) venv + 依赖（如指定替换库，先装替换版本，使 pyproject 的 PySide6 需求由它满足）
"${PYTHON:-python3}" -m venv "$BUILD_DIR/venv"
"$BUILD_DIR/venv/bin/pip" install --upgrade pip
if [ -n "$PYSIDE6_REQUIREMENT" ] || [ -n "$SHIBOKEN6_REQUIREMENT" ]; then
  echo "使用替换的 Qt 绑定：${PYSIDE6_REQUIREMENT:-'(默认 PySide6)'} ${SHIBOKEN6_REQUIREMENT:-''}"
  # shellcheck disable=SC2086
  "$BUILD_DIR/venv/bin/pip" install $EXTRA_PIP_ARGS \
    ${SHIBOKEN6_REQUIREMENT:+"$SHIBOKEN6_REQUIREMENT"} \
    ${PYSIDE6_REQUIREMENT:+"$PYSIDE6_REQUIREMENT"}
fi
# shellcheck disable=SC2086
"$BUILD_DIR/venv/bin/pip" install $EXTRA_PIP_ARGS -e .
"$BUILD_DIR/venv/bin/pip" install pyinstaller
echo "=== 产物实际使用的版本 ==="
"$BUILD_DIR/venv/bin/pip" list --format=freeze | grep -Ei '^(PySide6|shiboken6|pyqtgraph|numpy)=' || true

# 2) PyInstaller 打包（自动收集 PySide6 插件）
"$BUILD_DIR/venv/bin/pyinstaller" --clean --noconfirm \
  --distpath "$BUILD_DIR/dist" packaging/linux/NMRForge.spec
cp -a "$BUILD_DIR/dist/$APP/." "$APPDIR/usr/bin/"

# 2.5) 许可正文、声明与构建信息一并打进产物
DOC_DIR="$APPDIR/usr/share/doc/$APP"
mkdir -p "$DOC_DIR/third-party"
cp -a "$LICENSE_DIR"/. "$DOC_DIR/third-party/"
{
  echo "application : $APP"
  echo "version     : $VERSION"
  echo "commit      : $(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
  echo "worktree    : $(git status --porcelain --untracked-files=no 2>/dev/null | wc -l) modified tracked file(s)"
  echo "built       : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "build host  : $(uname -srm)"
  echo "python      : $("$BUILD_DIR/venv/bin/python" -V)"
  echo "packages    :"
  "$BUILD_DIR/venv/bin/pip" list --format=freeze | grep -Ei '^(PySide6|PySide6_Essentials|PySide6_Addons|shiboken6|pyqtgraph|numpy|scipy|matplotlib|nmrglue)=' | sed 's/^/  /'
} > "$DOC_DIR/BUILD_INFO.txt"
# 打包进 _internal 的每个 wheel 的 dist-info/licenses 也随产物保留（其余依赖的许可正文）

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
if [ "$1" = "--licenses" ] || [ "$1" = "--third-party-licenses" ]; then
    DOC="$HERE/usr/share/doc/NMRForge"
    echo "NMRForge third-party licences and build information"
    echo "--------------------------------------------------"
    echo "This AppImage bundles Qt and PySide6, used under the LGPL-3.0 option."
    echo "Full texts, the notice, and the build provenance:"
    echo "  $DOC/third-party/LGPL-3.0.txt"
    echo "  $DOC/third-party/GPL-3.0.txt   (LGPL-3.0 incorporates GPL-3.0 by reference)"
    echo "  $DOC/third-party/NOTICE.md"
    echo "  $DOC/third-party/PROVENANCE.txt"
    echo "  $DOC/BUILD_INFO.txt"
    echo
    echo "How to replace or relink the LGPL libraries: see NOTICE.md"
    echo "--- NOTICE.md ---"
    cat "$DOC/third-party/NOTICE.md" 2>/dev/null || echo "(NOTICE.md missing from this build)"
    exit 0
fi

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
echo "=== 产物内许可文件 ==="
find "$APPDIR/usr/share/doc/$APP" -maxdepth 2 -type f | sed "s|$APPDIR||"

