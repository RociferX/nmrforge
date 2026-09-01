#!/usr/bin/env bash
# 安装 NMRForge 桌面入口与图标(开发态运行)。
#
# GNOME 默认不在窗口标题栏显示窗口图标;任务栏/启动器图标来自 .desktop
# 文件的 Icon= 与 hicolor 图标主题。开发态(venv + python main.py)运行
# 时用本脚本把图标与启动入口装到用户目录,启动器/任务栏即显示图标
# (0.2.199-补29eq)。
#
# 用法: bash scripts/install_desktop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=NMRForge

VENV_PY="$ROOT/nmrforge/bin/python"
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="$ROOT/nmrforge/Scripts/python.exe"
fi
if [ ! -x "$VENV_PY" ]; then
  echo "错误: 未找到 venv python($ROOT/nmrforge/bin/python)" >&2
  exit 1
fi

# 1) 图标 -> hicolor 主题(GNOME/其它 DE 按主题名查找)
ICON_DST="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICON_DST"
install -m 0644 "$ROOT/gui/assets/nmrforge.png" "$ICON_DST/nmrforge.png"

# 2) 桌面入口(Exec 指向当前开发启动命令)
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

# 3) 刷新图标/桌面数据库(可用时)
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "已安装: $APPS_DIR/$APP.desktop"
echo "图标: $ICON_DST/nmrforge.png"
echo "提示: 已运行的应用重启后,GNOME 启动器/任务栏即显示图标;"
echo "若启动器仍不显示,注销重新登录一次。"
