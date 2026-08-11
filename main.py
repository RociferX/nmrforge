"""NMRForge 程序入口。

遵循 NMRFlow 开发流程：开发态首次运行自动创建 venv（nmrforge/）并安装本项目，
然后切换到 venv 解释器执行真正的入口逻辑；AppImage/PyInstaller 冻结态直接启动。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / "nmrforge"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结环境（AppImage）。"""
    return bool(getattr(sys, "frozen", False))


def _bootstrap_venv() -> None:
    """首次运行：创建 venv 并安装本项目（之后直接复用）。"""
    if VENV_PYTHON.exists():
        return
    print("未检测到虚拟环境，正在创建 nmrforge/ 并安装依赖（首次运行需要几分钟）...")
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    subprocess.check_call([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([str(VENV_PYTHON), "-m", "pip", "install", "-e", str(PROJECT_ROOT)])


def main() -> int:
    """Launch the Qt main window (GUI is built on core.project)."""
    from gui.main_window import MainWindow

    return MainWindow.run()
