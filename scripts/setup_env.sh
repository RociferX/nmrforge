#!/usr/bin/env bash
# 创建 venv（nmrforge/）并安装 NMRForge（Linux 开发环境）
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv nmrforge
nmrforge/bin/python -m pip install --upgrade pip
nmrforge/bin/python -m pip install -e .
echo "环境就绪：nmrforge/bin/python main.py"
