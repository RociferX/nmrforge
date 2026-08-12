"""左侧项目管理树:Project → Experiment → Data。

层级(契约 v1.2 §8.5):
- Project 节点:右键删除项目(强确认);
- Experiment 节点:可空白创建,右键导入数据/重命名/删除;
- Data 节点:实验下的数据条目(可多组),右键生成 FID/生成谱图/打开目录/删除。

Backend 落地 DataEntry 层级后直接使用 entry.data;当前兼容阶段
(schema 1.1)以实验自身作为单数据节点。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager

_STATUS_TEXT = {
    "registered": "已登记",
    "imported": "已导入",
    "fid_ready": "FID 就绪",
    "processed": "已处理",
    "picked": "已选峰",
    "analyzed": "已分析",
}


class ProjectTreePanel(QWidget):
    """项目管理树;selection_changed 在上下文(实验)变化时发出。"""

    selection_changed = pyqtSignal(str)  # experiment_id(选中实验或空串)
    open_requested = pyqtSignal(str)  # 双击实验:请求打开/聚焦该实验
    delete_project_requested = pyqtSignal()  # Project 右键:删除项目
    create_experiment_requested = pyqtSignal()  # 空白处右键:新建空白实验
    import_data_requested = pyqtSignal(str)  # Experiment 右键:导入数据(exp_id)
    data_action_requested = pyqtSignal(str, str)  # (action, data_id):生成FID/谱/删除
    rename_requested = pyqtSignal(str)  # 重命名实验(exp_id)
    delete_requested = pyqtSignal(str)  # 删除实验(exp_id)

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
        # 列宽可读:显式宽度 + 最小段宽(禁用 Stretch 覆盖)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.tree.header().setMinimumSectionSize(80)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 90)
        self.tree.setMinimumWidth(180)
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
        exp_item.setToolTip(0, f"{exp.id}\n右键: 导入数据 / 重命名 / 删除")
        exp_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "experiment", "exp_id": exp.id})
        for data_node in self._data_of(exp):
            data_item = self._make_data_item(exp, data_node)
            exp_item.addChild(data_item)
        exp_item.setExpanded(False)
        return exp_item

    def _data_of(self, exp) -> list:
        """实验下的数据节点;Backend DataEntry 落地后返回 entry.data。"""
        data = getattr(exp, "data", None)
        if data:
            return list(data)
        return [exp]  # schema 1.1 兼容:实验即数据

    def _make_data_item(self, exp, data_node) -> QTreeWidgetItem:
        data_id = getattr(data_node, "id", exp.id)
        source = getattr(data_node, "source", "") or getattr(exp, "source", "")
        status = self._data_status(exp, data_node)
        label = f"数据 {data_id}"
        data_item = QTreeWidgetItem([label, status])
        data_item.setIcon(0, self._icon("data"))
        data_item.setToolTip(0, f"{data_id}\n来源: {source}\n右键: 生成 FID / 生成谱图 / 删除")
        data_item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {"kind": "data", "exp_id": exp.id, "data_id": data_id},
        )
        return data_item

    def _data_status(self, exp, data_node) -> str:
        """按产物文件推断数据状态。"""
        try:
            spectra = self.manager.dir_path("spectra")
            exp_id = exp.id
            data_id = getattr(data_node, "id", exp_id)
            for ext in ("ft2", "ft3"):
                if spectra.joinpath(f"{exp_id}-{data_id}.{ext}").is_file():
                    return "已处理"
                if spectra.joinpath(f"{exp_id}.{ext}").is_file():
                    return "已处理"
        except Exception:  # noqa: BLE001 - 目录缺失时保守显示
            pass
        return "已导入"

    @staticmethod
    def _icon(kind: str):
        """简单 Unicode 图标(避免依赖外部资源文件)。"""
        from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

        glyph = {
            "project": "P",
            "experiment": "E",
            "data": "D",
        }.get(kind, "•")
        pix = QPixmap(16, 16)
        pix.fill(QColor("transparent"))
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
        """返回当前选中节点所属实验 id(选择 Data 节点时归一化到实验)。"""
        item = self.tree.currentItem()
        return self._experiment_id_of(item) if item is not None else ""

    def _experiment_id_of(self, item: QTreeWidgetItem | None) -> str:
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("exp_id"):
                return str(data["exp_id"])
            item = item.parent()
        return ""

    def _data_id_of(self, item: QTreeWidgetItem | None) -> str:
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("data_id"):
                return str(data["data_id"])
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
        menu = self._on_context_menu_impl(QMenu(self), item)
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_context_menu_impl(self, menu: QMenu, item) -> QMenu:
        """构建右键菜单(独立方法便于测试触发动作)。"""
        if item is None:
            # 空白处:新建空白实验(项目已打开时)
            if self.manager.project is not None:
                menu.addAction("新建空白实验...", self.create_experiment_requested.emit)
        else:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            kind = data.get("kind") if isinstance(data, dict) else None
            exp_id = self._experiment_id_of(item)
            data_id = self._data_id_of(item)
            if kind == "project":
                menu.addAction("新建空白实验...", self.create_experiment_requested.emit)
                menu.addSeparator()
                menu.addAction("删除项目...", self.delete_project_requested.emit)
            elif kind == "experiment" and exp_id:
                menu.addAction("导入数据...", lambda: self.import_data_requested.emit(exp_id))
                menu.addAction("重命名...", lambda: self.rename_requested.emit(exp_id))
                menu.addSeparator()
                menu.addAction("删除实验", lambda: self.delete_requested.emit(exp_id))
            elif kind == "data" and exp_id and data_id:
                menu.addAction("生成 FID", lambda: self.data_action_requested.emit("fid", data_id))
                menu.addAction(
                    "生成谱图", lambda: self.data_action_requested.emit("spectrum", data_id)
                )
                menu.addSeparator()
                source = self._source_of(exp_id)
                if source:
                    menu.addAction(
                        "打开所在目录",
                        lambda s=source: QDesktopServices.openUrl(
                            QUrl.fromLocalFile(str(s))
                        ),
                    )
                menu.addAction(
                    "删除数据",
                    lambda: self.data_action_requested.emit("delete", data_id),
                )
        return menu

    def _source_of(self, exp_id: str) -> Path | None:
        if self.manager.project is None:
            return None
        exp = self.manager.project.experiment(exp_id)
        if exp is None:
            return None
        source = getattr(exp, "source", "") or ""
        path = Path(source)
        if path.is_dir():
            return path
        return path.parent if path.exists() else None
