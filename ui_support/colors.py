"""Semantic colours for the forced-dark theme (Qt-free).

The application forces a dark theme (window ``#1e1e1e``) regardless of the desktop setting, so
light-theme leftovers - ``#2c3e50`` (~1.5:1), ``#333`` (~1.6:1), ``#444`` (~2.1:1), ``#555``
(~2.4:1), ``#666`` (~2.9:1) - are effectively invisible. Those literals are banned in
``gui/`` and ``viewer/``; take the colour from here instead. ``tests/test_gui_theme.py`` enforces
both the contrast requirement and the ban.

This module imports nothing, deliberately: it is usable from code that must not touch Qt.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 文字色(对比度按暗色窗口底色 #1e1e1e 计算)
# ---------------------------------------------------------------------------
TEXT_PRIMARY = "#e8e8e8"        # 正文与标题(≈13:1)
TEXT_SECONDARY = "#b0b0b0"      # 次级说明(≈7.7:1)
TEXT_MUTED = "#8a8a8a"          # 提示/占位/灰字(≈4.8:1)
TEXT_ON_LIGHT = "#222222"       # 浅色面板(白底详情框/内联编辑器)内的文字
WINDOW_BACKGROUND = "#1e1e1e"   # 与 apply_dark_theme 的 Window 色一致

# ---------------------------------------------------------------------------
# 分区(面板)外观:四个主分区用卡片底色 + 边框区分
# ---------------------------------------------------------------------------
PANEL_BACKGROUND = "#202124"
PANEL_BORDER = "#33363b"
SURFACE_ALT = "#232326"         # 表格/树交替行

# ---------------------------------------------------------------------------
# 状态语义色(Pipeline 步骤行图标与文字、树状态)
# ---------------------------------------------------------------------------
STATUS_COLORS: dict[str, str] = {
    "SUCCESS": "#5cb85c",
    "OUTDATED": "#f0ad4e",
    "FAILED": "#e05c5c",
    "RUNNING": "#4fc1ff",
    "READY": "#4fc1ff",
    "LOCKED": "#9e9e9e",
}
