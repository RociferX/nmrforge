"""全局暗色主题(0.2.199-补29am)。

无论系统是明亮还是黑暗主题,软件界面强制保持暗色:Fusion 样式 +
暗色 QPalette + 全局 QSS。主窗口与独立谱图查看器启动时调用
``apply_dark_theme(app)``。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QStyle
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QProxyStyle


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
        if hint == QStyle.StyleHint.SH_ToolTipDelayOn:
            return 120
        if hint == QStyle.StyleHint.SH_ToolTipFallAsleepDelay:
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
            background-color: #252526; color: #e8e8e8; }
        QHeaderView::section { background-color: #2d2d30; color: #e8e8e8;
                               border: 1px solid #3c3c3c; }
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
                      border: 1px solid #555555; padding: 4px 8px; }
        QPushButton:hover { background-color: #4a4a4a; }
        QPushButton:disabled { color: #6e6e6e; }
        QTabWidget::pane { border: 1px solid #3c3c3c; }
        QTabBar::tab { background-color: #252526; color: #e8e8e8;
                       padding: 4px 8px; }
        QTabBar::tab:selected { background-color: #0e639c; }
        QSplitter::handle { background-color: #2d2d30; }
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
        """
        + _arrow_qss
    )
