"""Task/Log 竖列面板:pipeline 与谱图查看器之间的任务日志/错误展示。"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogPanel(QWidget):
    """日志面板:按作用域(单个数据/数据组/实验/全局)隔离 + 追加 + 清空。

    用户要求:单个数据的日志各自独立,点击哪个数据显示哪个的日志;
    数据组内的成员共用同一个数据组日志。选中变化时主窗口调用
    set_scope 切换当前显示与追加目标。
    """

    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout()
        title = QLabel("Task / Log")
        title.setStyleSheet("font-weight: bold;")
        header.addWidget(title)
        self.scope_label = QLabel("")
        self.scope_label.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        header.addWidget(self.scope_label)
        header.addStretch(1)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.clear_button)
        self.stop_button = QPushButton("停止当前任务")
        self.stop_button.setToolTip(
            "终止正在运行的处理任务(含 SMILE/nmrPipe 子进程,不留残留)"
        )
        self.stop_button.clicked.connect(self.stop_requested.emit)
        header.addWidget(self.stop_button)
        layout.addLayout(header)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        layout.addWidget(self.text, 1)
        self._buffers: dict[str, list[str]] = {"global": []}
        self._current_scope = "global"

    @staticmethod
    def scope_key(
        kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> str:
        """选中上下文 → 日志作用域键。

        - 数据组:组内共用同一个组日志(group:{exp}:{group});
        - 单个数据:每个数据独立(data:{exp}:{data});
        - 实验:实验级日志(exp:{exp});
        - 其它(项目/工作区/未选中):全局(global)。
        """
        if kind == "group" and group_id:
            return f"group:{exp_id}:{group_id}"
        if kind == "data" and data_id:
            return f"data:{exp_id}:{data_id}"
        if kind == "experiment" and exp_id:
            return f"exp:{exp_id}"
        return "global"

    def set_scope(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """切换当前作用域:显示该作用域的既有日志,后续 append 落这里。"""
        self._current_scope = self.scope_key(kind, exp_id, data_id, group_id)
        self._buffers.setdefault(self._current_scope, [])
        self.scope_label.setText(
            {
                "global": "全局",
                f"exp:{exp_id}": f"实验 {exp_id}",
            }.get(self._current_scope, self._current_scope)
        )
        self._reload_text()

    def current_scope(self) -> str:
        return self._current_scope

    def append(self, message: str, scope: str | None = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        key = scope or self._current_scope
        self._buffers.setdefault(key, []).append(line)
        if key == self._current_scope:
            self._append_line(line)

    def clear(self) -> None:
        self._buffers[self._current_scope] = []
        self._reload_text()

    def _append_line(self, line: str) -> None:
        self.text.appendPlainText(line)
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _reload_text(self) -> None:
        self.text.clear()
        for line in self._buffers.get(self._current_scope, []):
            self.text.appendPlainText(line)
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

