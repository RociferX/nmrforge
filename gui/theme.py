"""全局暗色主题(0.2.199-补29am)。

无论系统是明亮还是黑暗主题,软件界面强制保持暗色:Fusion 样式 +
暗色 QPalette + 全局 QSS。主窗口与独立谱图查看器启动时调用
``apply_dark_theme(app)``。
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    """把 QApplication 设置为强制暗色主题。"""
    app.setStyle("Fusion")
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
        QGroupBox { border: 1px solid #3c3c3c; color: #e8e8e8; }
        QProgressBar { background-color: #252526; border: 1px solid #3c3c3c;
                       color: #e8e8e8; }
        QProgressBar::chunk { background-color: #0e639c; }
        QCheckBox, QRadioButton { color: #e8e8e8; }
        QListWidget { background-color: #252526; color: #e8e8e8; }
        QStackedWidget { background-color: #1e1e1e; }
        """
    )
