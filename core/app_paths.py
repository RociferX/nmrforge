"""资源路径解析：开发态 / 冻结态（AppImage/PyInstaller）通用。

- 开发态：资源位于仓库根目录（config/、presets/）。
- 冻结态：PyInstaller 把 config/presets 打入 _MEIPASS，路径自动切换。
- AppImage 打包与资源布局见 docs/packaging.md。
"""

from __future__ import annotations

import os
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

def local_config_path(
    filename: str = "nmrforge.local.yaml", *, packaged: bool | None = None
) -> Path:
    """返回 GUI 与 Backend 共用的用户覆盖配置路径。

    冻结/AppImage 使用用户配置目录;开发工作区优先 config/,不可写时
    回退用户主目录 dotfile。packaged 参数供 GUI 测试和显式环境判定。
    """
    if packaged is None:
        packaged = is_frozen() or bool(os.environ.get("APPIMAGE"))
    if packaged:
        return Path.home() / ".config" / "NMRForge" / filename
    candidate = resource_path("config") / filename
    try:
        if os.access(candidate.parent, os.W_OK):
            return candidate
    except OSError:
        pass
    return Path.home() / f".{filename}"
