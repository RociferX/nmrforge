#!/usr/bin/env bash
# VM 全量测试统一入口:字节码/pytest/ruff 缓存全部落到 ~/nmrforge-test-artifacts/,
# 保持 ~/NMRForge 主目录干净(Architect,2026-08-18)。
#
# 用法:
#   bash scripts/vm_test.sh                  # 全量
#   bash scripts/vm_test.sh tests/test_x.py  # 单文件(pytest 额外参数透传)
set -euo pipefail

ART="$HOME/nmrforge-test-artifacts"
mkdir -p "$ART/pytest" "$ART/pycache" "$ART/ruff"
export PYTHONPYCACHEPREFIX="$ART/pycache"   # __pycache__ 重定向
export RUFF_CACHE_DIR="$ART/ruff"           # ruff 缓存重定向
export QT_QPA_PLATFORM=offscreen
cd "$HOME/NMRForge"
exec "$HOME/NMRForge/nmrforge/bin/python" -m pytest \
  -o addopts='' -p no:cacheprovider \
  --basetemp="$ART/pytest" -q --tb=no "$@"
