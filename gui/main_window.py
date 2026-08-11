"""主窗口骨架（Phase 1 起接入 Qt）。

布局对应框架 §51：Dataset / Experiment / Processing Plan / Spectrum Viewer / Quality。
"""

from __future__ import annotations


class MainWindow:
    """主窗口占位；后续继承 QMainWindow。"""

    def __init__(self) -> None:
        raise NotImplementedError("Phase 1: 接入 PyQt6 主窗口")
