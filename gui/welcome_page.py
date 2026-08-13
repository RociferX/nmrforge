"""首次启动工作区欢迎页(G2B-003 / 契约 v1.3 §9.4)。

显示工作区路径、最近样本列表与新建项目入口。
``WorkspaceManager``(core/workspace.py)由 Backend 按 G2B-003 落地;在落地前
提供兼容层:优先使用 core.workspace.WorkspaceManager,缺失时回退到
``~/NMRForgeWorkspace``(幂等创建),保证 GUI 先行可用。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def workspace_manager():
    """返回 WorkspaceManager 兼容实例(Backend 落地后为 core.workspace 实现)。"""
    try:
        from core.workspace import WorkspaceManager  # type: ignore[import-not-found]

        return WorkspaceManager()
    except ImportError:
        return _FallbackWorkspaceManager()


def default_workspace_path() -> Path:
    """默认工作区目录(Backend 契约:~/NMRForgeWorkspace)。"""
    return Path.home() / "NMRForgeWorkspace"


class _FallbackWorkspaceManager:
    """core/workspace.py 落地前的兼容实现(幂等 ensure + 目录即项目)。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else default_workspace_path()

    def ensure(self) -> Path:
        """确保工作区目录存在(幂等),返回路径。"""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def list_projects(self) -> list[Path]:
        """列出工作区下含 project.json 的项目目录。"""
        if not self.root.is_dir():
            return []
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_dir() and (p / "project.json").is_file()
        )

    def create_project(self, name: str) -> Path:
        """在工作区下创建项目目录(实际项目初始化由 ProjectManager 完成)。"""
        self.ensure()
        return self.root / name


class WelcomePage(QWidget):
    """首次启动/未打开项目时的欢迎页。"""

    new_project_requested = pyqtSignal(str)  # 项目名称
    open_project_requested = pyqtSignal(str)  # 项目路径

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ws = workspace_manager()
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

        recent_title = QLabel("最近样本")
        recent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(recent_title)

        self.recent_list = QListWidget()
        self.recent_list.setMinimumHeight(140)
        self.recent_list.itemClicked.connect(self._on_recent_clicked)
        layout.addWidget(self.recent_list, 1)

        actions = QHBoxLayout()
        self.new_button = QPushButton("新建样本...")
        self.new_button.clicked.connect(self._on_new_clicked)
        actions.addWidget(self.new_button)
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
            self.recent_list.addItem("(工作区中还没有样本,点击「新建样本」开始)")

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
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "新建样本", "样本名称:", text="unnamed"
        )
        if ok and name.strip():
            self.new_project_requested.emit(name.strip())

    def recent_paths(self) -> list[str]:
        return [
            str(item.data(0x0100))
            for item in self.recent_list
            if item.data(0x0100)
        ]


def make_welcome_card(text: str) -> QFrame:
    """欢迎页提示卡片(通用小组件)。"""
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    label = QLabel(text)
    label.setWordWrap(True)
    layout = QVBoxLayout(frame)
    layout.addWidget(label)
    return frame
