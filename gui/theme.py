"""全局暗色主题(0.2.199-补29am)。

无论系统是明亮还是黑暗主题,软件界面强制保持暗色:Fusion 样式 +
暗色 QPalette + 全局 QSS。主窗口与独立谱图查看器启动时调用
``apply_dark_theme(app)``。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QProxyStyle, QStyle

# ---------------------------------------------------------------------------
# 暗色主题语义色(0.2.199-补29hz)
# 主题强制暗色(窗口 #1e1e1e)后,浅色主题遗留的深色文字对比度全部低于
# 可读线:#2c3e50 ≈ 1.5:1、#333 ≈ 1.6:1、#444 ≈ 2.1:1、#555 ≈ 2.4:1、
# #666 ≈ 2.9:1 —— 实际等于看不见。GUI 里禁止再写这些字面量,统一取这里;
# tests/test_gui_theme.py 会拦截回退写法。
# ---------------------------------------------------------------------------
TEXT_PRIMARY = "#e8e8e8"       # 正文与标题(≈13:1)
TEXT_SECONDARY = "#b0b0b0"     # 次级说明(≈7.7:1)
TEXT_MUTED = "#8a8a8a"         # 提示/占位/灰字(≈4.8:1)
TEXT_ON_LIGHT = "#222222"      # 浅色面板(白底详情框/内联编辑器)内的文字
WINDOW_BACKGROUND = "#1e1e1e"  # 与 apply_dark_theme 的 Window 色一致

# 分区(面板)外观(0.2.199-补29hz-修2):四个主分区用卡片底色 + 边框区分,
# 分隔条加宽并在悬停时高亮,让「树 / 处理流程 / 日志 / 谱图」一眼可分。
PANEL_BACKGROUND = "#202124"
PANEL_BORDER = "#33363b"
SURFACE_ALT = "#232326"      # 表格/树交替行

# 状态语义色(Pipeline 步骤行图标与文字、树状态)::
STATUS_COLORS: dict[str, str] = {
    "SUCCESS": "#5cb85c",
    "OUTDATED": "#f0ad4e",
    "FAILED": "#e05c5c",
    "RUNNING": "#4fc1ff",
    "READY": "#4fc1ff",
    "LOCKED": "#9e9e9e",
}


def app_icon() -> QIcon | None:
    """应用图标(gui/assets/nmrforge.png,开发/冻结通用);缺失返回 None。

    0.2.199-补29eq:主窗口与独立查看器启动时调用,设置窗口/任务栏图标。
    """
    try:
        from core.app_paths import resource_path

        assets = Path(resource_path("gui")) / "assets"
    except Exception:  # noqa: BLE001
        assets = Path(__file__).resolve().parent / "assets"
    candidates = (
        assets / "nmrforge.png",
        Path(__file__).resolve().parent.parent
        / "packaging" / "linux" / "icons" / "nmrforge.png",
    )
    for p in candidates:
        if p.is_file():
            return QIcon(str(p))
    return None


def _theme_assets_dir() -> Path | None:
    """主题箭头图片目录(gui/assets/,开发/冻结通用);缺失返回 None。"""
    try:
        from core.app_paths import resource_path
        assets = Path(resource_path("gui")) / "assets"
    except Exception:  # noqa: BLE001
        assets = Path(__file__).resolve().parent / "assets"
    if (assets / "spin_up.png").is_file() and (assets / "spin_down.png").is_file():
        return assets
    return None


class _FastTooltipStyle(QProxyStyle):
    """29ed: shorten tooltip delay so button hover hints appear quickly."""

    def styleHint(
        self,
        hint,
        option=None,
        widget=None,
        returnData=None,
    ):
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return 120
        if hint == QStyle.StyleHint.SH_ToolTip_FallAsleepDelay:
            return 120
        return super().styleHint(hint, option, widget, returnData)


def apply_dark_theme(app: QApplication) -> None:
    """把 QApplication 设置为强制暗色主题。"""
    app.setStyle("Fusion")
    app.setStyle(_FastTooltipStyle(app.style()))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#252526"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2d2d30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#3c3c3c"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d2d30"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0e639c"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9e9e9e"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#4fc1ff"))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor("#6e6e6e"),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor("#6e6e6e"),
    )
    app.setPalette(palette)
    _arrow_qss = ""
    _assets = _theme_assets_dir()
    if _assets is not None:
        _up = (_assets / "spin_up.png").as_posix()
        _down = (_assets / "spin_down.png").as_posix()
        _arrow_qss = (
            f'QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{'
            f' image: url("{_up}"); width: 12px; height: 12px; }}\n'
            f'QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{'
            f' image: url("{_down}"); width: 12px; height: 12px; }}\n'
        )
    app.setStyleSheet(
        """
        QToolTip { color: #e8e8e8; background-color: #2d2d30;
                   border: 1px solid #3c3c3c; }
        QMenuBar { background-color: #1e1e1e; color: #e8e8e8; }
        QMenuBar::item:selected { background-color: #0e639c; }
        QMenu { background-color: #252526; color: #e8e8e8;
                border: 1px solid #3c3c3c; }
        QMenu::item:selected { background-color: #0e639c; }
        QStatusBar { background-color: #1e1e1e; color: #e8e8e8; }
        QTreeView, QListView, QTableView, QTableWidget, QTreeWidget {
            background-color: #252526; color: #e8e8e8;
            alternate-background-color: #232326; }
        QHeaderView::section { background-color: #2d2d30; color: #e8e8e8;
                               border: none;
                               border-bottom: 1px solid #3c3c3c;
                               padding: 4px 6px; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit,
        QTextEdit, QDateEdit, QTimeEdit {
            background-color: #1e1e1e; color: #e8e8e8;
            border: 1px solid #3c3c3c; }
        /* 0.2.199-补29at:spinbox 上下箭头按钮深底,白色箭头可见 */
        QSpinBox::up-button, QDoubleSpinBox::up-button,
        QSpinBox::down-button, QDoubleSpinBox::down-button {
            background-color: #3c3c3c; border: none; width: 16px; }
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
            background-color: #4a4a4a; }
        QPushButton { background-color: #3c3c3c; color: #e8e8e8;
                      border: 1px solid #555555; padding: 5px 10px;
                      border-radius: 4px; }
        QPushButton:hover { background-color: #4a4a4a; }
        QPushButton:disabled { color: #6e6e6e; }
        QTabWidget::pane { border: 1px solid #3c3c3c; }
        QTabBar::tab { background-color: #252526; color: #e8e8e8;
                       padding: 4px 8px; }
        QTabBar::tab:selected { background-color: #0e639c; }
        QSplitter::handle { background-color: #3a3d42; }
        QSplitter::handle:horizontal { width: 6px; }
        QSplitter::handle:vertical { height: 6px; }
        QSplitter::handle:hover { background-color: #0e639c; }
        QSplitter::handle:pressed { background-color: #1177bb; }
        QScrollBar { background-color: #252526; }
        QScrollBar::handle { background-color: #3c3c3c; }
        QLabel { color: #e8e8e8; }
        /* 0.2.199-补29cc:QSS 自定义边框会压住标题,给标题留 margin-top */
        QGroupBox { border: 1px solid #3c3c3c; margin-top: 18px;
                    color: #e8e8e8; }
        QGroupBox::title { subcontrol-origin: margin;
                           subcontrol-position: top left;
                           left: 8px; padding: 0 4px;
                           color: #4fc1ff; }
        QProgressBar { background-color: #252526; border: 1px solid #3c3c3c;
                       color: #e8e8e8; }
        QProgressBar::chunk { background-color: #0e639c; }
        QCheckBox, QRadioButton { color: #e8e8e8; }
        QListWidget { background-color: #252526; color: #e8e8e8; }
        QStackedWidget { background-color: #1e1e1e; }
        /* 0.2.199-补29hz-修2:分区卡片与标题 */
        QWidget#PanelCard { background-color: #202124;
                            border: 1px solid #33363b;
                            border-radius: 6px; }
        QLabel#PanelTitle { font-size: 13px; font-weight: bold;
                            color: #e8e8e8; }
        QLabel#PanelSubtitle { font-size: 11px; color: #b0b0b0; }
        /* 步骤行:底部细线分隔 + 悬停高亮 */
        QWidget#StepRow { border-bottom: 1px solid #2d2d30; }
        QWidget#StepRow:hover { background-color: #26282b; }
        /* 选中 / 悬停态统一 */
        QTreeView::item:selected, QListView::item:selected,
        QTableView::item:selected, QTableWidget::item:selected {
            background-color: #0e639c; color: #ffffff; }
        QTreeView::item:hover, QListView::item:hover,
        QTableView::item:hover, QTableWidget::item:hover {
            background-color: #2a2d2e; }
        /* 输入焦点 */
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
            border: 1px solid #0e639c; }
        /* 按钮状态 */
        QPushButton:pressed { background-color: #2f2f2f; }
        QPushButton:checked { background-color: #0e639c;
                              border-color: #1177bb; }
        /* 滚动条:细身圆角,不抢视线 */
        QScrollBar:vertical { background: transparent; width: 10px;
                              margin: 2px 0 2px 0; }
        QScrollBar:horizontal { background: transparent; height: 10px;
                                margin: 0 2px 0 2px; }
        QScrollBar::handle { background-color: #3c3c3c;
                             border-radius: 4px;
                             min-height: 28px; min-width: 28px; }
        QScrollBar::handle:hover { background-color: #4d4d4d; }
        QScrollBar::add-line, QScrollBar::sub-line {
            width: 0; height: 0; background: transparent; }
        QScrollBar::add-page, QScrollBar::sub-page {
            background: transparent; }
        QToolTip { padding: 4px 6px; border-radius: 4px; }
        /* 0.2.199-补29hz-修2(方案 A):分区内部一律用卡片底色,
           让「树 / 处理流程 / 日志 / 谱图」各自是一整块,而不是被
           内容控件(滚动区/文本框/列表)切成深浅不一的小块。 */
        QWidget#PanelCard QTreeWidget, QWidget#PanelCard QTableWidget,
        QWidget#PanelCard QListWidget, QWidget#PanelCard QPlainTextEdit,
        QWidget#PanelCard QTextEdit, QWidget#PanelCard QStackedWidget,
        QWidget#PanelCard QScrollArea, QWidget#PanelCard QScrollArea > QWidget,
        QWidget#PanelCard QScrollArea > QWidget > QWidget {
            background-color: #202124; border: none; }
        """
        + _arrow_qss
    )
