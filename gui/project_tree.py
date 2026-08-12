"""左侧项目管理树:Project → Experiment → 数据阶段(Input/Processing/Output/Figures)。

树节点不是操作系统文件浏览器,而是项目的逻辑视图:
- Project 节点(项目名 + 路径提示);
- Experiment 节点(标题 + 状态,右键可重命名/删除/打开所在目录);
- 每个实验下挂 Input/Processing/Output/Figures 四个阶段节点,
  用于明确"当前对象属于哪个 Data/Experiment/Project"(见 GUI_ARCHITECTURE_VISION §4-6)。

选中任何节点时通过 :py:meth:`current_experiment_id` 归一化到所属实验,
中间 Pipeline 与右侧谱图面板始终围绕该实验工作。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from core.project import ProjectManager

STAGE_LABELS = ("Input", "Processing", "Output", "Figures")

_STATUS_TEXT = {
    "registered": "已登记",
    "imported": "已导入",
    "processed": "已处理",
    "picked": "已选峰",
    "analyzed": "已分析",
}


class ProjectTreePanel(QWidget):
    """项目管理树面板;selection_changed 在上下文(实验)变化时发出。"""

    selection_changed = pyqtSignal(str)  # experiment_id(选中实验或空串)
    open_requested = pyqtSignal(str)  # 双击实验:请求打开/聚焦该实验

    def __init__(
        self, manager: ProjectManager | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.manager = manager or ProjectManager()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["对象", "状态"])
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)
        self.refresh()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """按 ProjectManager 重建树。"""
        self.tree.clear()
        project = self.manager.project
        if project is None:
            return
        project_item = QTreeWidgetItem([project.name, ""])
        project_item.setIcon(0, self._icon("project"))
        project_item.setToolTip(0, str(self.manager.root))
        project_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "project"})
        self.tree.addTopLevelItem(project_item)
        for exp in project.experiments:
            exp_item = self._make_experiment_item(exp)
            project_item.addChild(exp_item)
        project_item.setExpanded(True)

    def _make_experiment_item(self, exp) -> QTreeWidgetItem:
        status = exp.status
        exp_item = QTreeWidgetItem([exp.title or exp.id, _STATUS_TEXT.get(status, status)])
        exp_item.setIcon(0, self._icon("experiment"))
        exp_item.setToolTip(0, f"{exp.id}\n来源: {exp.source}")
        exp_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "experiment", "exp_id": exp.id})
        for stage in STAGE_LABELS:
            stage_item = QTreeWidgetItem([stage, ""])
            stage_item.setIcon(0, self._icon(stage))
            stage_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {"kind": "stage", "exp_id": exp.id, "stage": stage},
            )
            exp_item.addChild(stage_item)
        exp_item.setExpanded(False)
        return exp_item

    @staticmethod
    def _icon(kind: str):
        """简单 Unicode 图标(避免依赖外部资源文件)。"""
        from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap

        glyph = {
            "project": "P",
            "experiment": "E",
            "Input": "I",
            "Processing": "P",
            "Output": "O",
            "Figures": "F",
        }.get(kind, "•")
        pix = QPixmap(16, 16)
        pix.fill(QColor("transparent"))
        from PyQt6.QtGui import QPainter

        painter = QPainter(pix)
        painter.setPen(QColor("#2c3e50"))
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        return QIcon(pix)

    # ------------------------------------------------------------------
    # 选择与上下文
    # ------------------------------------------------------------------
    def current_experiment_id(self) -> str:
        """返回当前选中节点所属实验 id(选择阶段节点时归一化到实验)。"""
        item = self.tree.currentItem()
        return self._experiment_id_of(item) if item is not None else ""

    def _experiment_id_of(self, item: QTreeWidgetItem | None) -> str:
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("exp_id"):
                return str(data["exp_id"])
            item = item.parent()
        return ""

    def select_experiment(self, exp_id: str) -> None:
        """按 id 定位并选中实验节点(用于外部联动)。"""
        root = self.tree.topLevelItem(0)
        if root is None:
            return
        for i in range(root.childCount()):
            item = root.child(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("exp_id") == exp_id:
                root.setExpanded(True)
                item.setExpanded(True)
                self.tree.setCurrentItem(item)
                return

    def _on_selection_changed(self) -> None:
        self.selection_changed.emit(self.current_experiment_id())

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        exp_id = self._experiment_id_of(item)
        if exp_id:
            self.open_requested.emit(exp_id)

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        exp_id = self._experiment_id_of(item)
        menu = QMenu(self)
        if exp_id:
            menu.addAction("打开", lambda: self.open_requested.emit(exp_id))
            menu.addAction("重命名...", lambda: self.rename_requested.emit(exp_id))
            menu.addAction("删除", lambda: self.delete_requested.emit(exp_id))
            source = self._source_of(exp_id)
            if source:
                menu.addAction(
                    "打开所在目录",
                    lambda s=source: QDesktopServices.openUrl(
                        QUrl.fromLocalFile(str(s))
                    ),
                )
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _source_of(self, exp_id: str) -> Path | None:
        if self.manager.project is None:
            return None
        exp = self.manager.project.experiment(exp_id)
        if exp is None or not exp.source:
            return None
        path = Path(exp.source)
        return path if path.is_dir() else path.parent if path.exists() else None

    # 供 MainWindow 连接的动作信号(避免直接引用对话框类)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

