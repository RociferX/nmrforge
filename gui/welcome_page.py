"""首次启动工作区欢迎页(G2B-003 / 契约 v1.3 §9.4)。

显示工作区路径、最近项目列表与新建项目入口。
工作区统一使用 core/workspace.WorkspaceManager(0.2.164 起删除
兼容层 _FallbackWorkspaceManager 与重复的 workspace_manager 工厂)。
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class WelcomePage(QWidget):
    """首次启动/未打开项目时的欢迎页。"""

    new_project_requested = pyqtSignal(str)  # 项目名称
    open_project_requested = pyqtSignal(str)  # 项目路径

    def __init__(
        self, parent: QWidget | None = None, workspace=None
    ) -> None:
        super().__init__(parent)
        if workspace is None:
            from core.workspace import WorkspaceManager

            workspace = WorkspaceManager()
        self.ws = workspace
        self._name_committing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)

        title = QLabel("NMRForge")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)

        subtitle = QLabel("面向 Bruker 2D/3D NMR 的自动化处理、参数优化与质量控制平台")
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self.workspace_label = QLabel("")
        self.workspace_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.workspace_label)
        layout.addSpacing(10)

        recent_title = QLabel("最近项目")
        recent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(recent_title)

        self.recent_list = QListWidget()
        self.recent_list.setMinimumHeight(140)
        self.recent_list.itemClicked.connect(self._on_recent_clicked)
        layout.addWidget(self.recent_list, 1)

        actions = QHBoxLayout()
        self.new_button = QPushButton("新建项目...")
        self.new_button.clicked.connect(self._on_new_clicked)
        actions.addWidget(self.new_button)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("输入项目名称")
        self._name_edit.setMaximumWidth(240)
        self._name_edit.setVisible(False)
        self._name_edit.installEventFilter(self)
        self._name_edit.editingFinished.connect(self._commit_name)
        actions.addWidget(self._name_edit)
        self._name_ok_button = QPushButton("确定")
        self._name_ok_button.setVisible(False)
        self._name_ok_button.clicked.connect(self._commit_name)
        actions.addWidget(self._name_ok_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        path = self.ws.ensure()
        self.workspace_label.setText(f"工作区: {path}")
        self.workspace_label.setToolTip(str(path))
        self.recent_list.clear()
        for project_dir in self.ws.list_projects():
            item = QListWidgetItem(project_dir.name)
            item.setData(0x0100, str(project_dir))  # Qt.UserRole
            item.setToolTip(str(project_dir))
            self.recent_list.addItem(item)
        if self.recent_list.count() == 0:
            self.recent_list.addItem("(工作区中还没有项目,点击「新建项目」开始)")

    def refresh(self) -> None:
        self._refresh()

    def workspace_path(self) -> str:
        return str(self.ws.ensure())

    # ------------------------------------------------------------------
    def _on_recent_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(0x0100)
        if path:
            self.open_project_requested.emit(str(path))

    def _on_new_clicked(self) -> None:
        self.begin_inline_name()

    def begin_inline_name(self, initial: str = "") -> None:
        """页内内联命名(不弹窗):显示名称输入行 + 确定按钮并聚焦,回车提交 / Esc 取消。"""
        self._name_edit.setText(initial)
        self._name_edit.selectAll()
        self._name_edit.setVisible(True)
        self._name_ok_button.setVisible(True)
        self._name_edit.setFocus()

    def _commit_name(self) -> None:
        if self._name_committing:
            return
        self._name_committing = True
        try:
            name = self._name_edit.text().strip()
            self._name_edit.setVisible(False)
            self._name_ok_button.setVisible(False)
            if name:
                self.new_project_requested.emit(name)
        finally:
            self._name_committing = False

    def _cancel_name(self) -> None:
        self._name_edit.setVisible(False)
        self._name_ok_button.setVisible(False)

    def eventFilter(self, obj, event) -> bool:
        """Esc 取消内联命名(其它事件交回默认处理)。"""
        if (
            obj is self._name_edit
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._cancel_name()
            return True
        return super().eventFilter(obj, event)

    def recent_paths(self) -> list[str]:
        return [
            str(item.data(0x0100))
            for item in self.recent_list
            if item.data(0x0100)
        ]
