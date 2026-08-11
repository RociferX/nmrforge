"""资源路径解析：开发态 / 冻结态（AppImage/PyInstaller）通用。

- 开发态：资源位于仓库根目录（config/、presets/）。
- 冻结态：PyInstaller 把 config/presets 打入 _MEIPASS，路径自动切换。
- AppImage 打包与资源布局见 docs/packaging.md。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结环境。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """返回资源根目录（config/、presets/ 的父目录）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return PROJECT_ROOT


def resource_path(relative: str) -> Path:
    """返回仓库内资源路径（如 config/nmrforge.yaml）。"""
    return resource_root() / relative
