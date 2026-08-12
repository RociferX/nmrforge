"""底部 Task/Log 面板:任务日志与错误展示(可折叠)。"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogPanel(QWidget):
    """日志面板:追加消息 + 清空 + 折叠/展开。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout()
        title = QLabel("Task / Log")
        title.setStyleSheet("font-weight: bold;")
        header.addWidget(title)
        header.addStretch(1)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.clear_button)
        layout.addLayout(header)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        layout.addWidget(self.text, 1)

    def append(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text.appendPlainText(f"[{timestamp}] {message}")
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear(self) -> None:
        self.text.clear()

