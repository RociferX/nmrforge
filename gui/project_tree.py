"""左侧项目管理树:项目 → 数据类型 → 样品数据。

层级(契约 v1.2 §8.5):
- 项目节点:右键删除项目(强确认);
- 数据类型节点:可空白创建,右键导入样品数据/重命名/删除;
- 样品数据节点:数据类型下的样品数据条目(可多组),右键生成 FID/生成谱图/打开目录/删除。

Backend 落地 DataEntry 层级后直接使用 entry.data;当前兼容阶段
(schema 1.1)以数据类型自身作为单样品数据节点。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager

# 数据节点下真实目录(契约 v1.3 §9:raw/process/spectra/peaks/figures/report)
DATA_SUBFOLDERS = ("raw", "process", "spectra", "peaks", "figures", "report")

_STATUS_TEXT = {
    "registered": "已登记",
    "imported": "已导入",
    "fid_ready": "FID 就绪",
    "processed": "已处理",
    "picked": "已选峰",
    "analyzed": "已分析",
}


def _terminal_argv(directory: str) -> list[str] | None:
    """构造「在终端中打开目录」的命令(优先 csh);无可用终端返回 None。

    Linux/macOS:优先 gnome-terminal/konsole/x-terminal-emulator/xterm
    启动 csh(自动读取 ~/.cshrc 的 NMRPipe 环境);Windows 优先找到的
    csh(如 Cygwin/Git Bash),否则回退 cmd。
    """
    import shlex
    import shutil
    import sys

    if sys.platform.startswith("win"):
        csh = shutil.which("csh")
        if csh:
            return [csh, "-c", f"cd {shlex.quote(directory)} && exec csh"]
        cmd = shutil.which("cmd") or "cmd.exe"
        return [cmd, "/K", f'cd /d "{directory}"']

    csh = shutil.which("csh")
    terminal = None
    for name in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        candidate = shutil.which(name)
        if candidate:
            terminal = candidate
            break
    if terminal is None:
        return None
    base = terminal
    if "gnome-terminal" in base:
        args = [base, f"--working-directory={directory}"]
        if csh:
            args += ["--", "csh"]
        return args
    if "konsole" in base:
        args = [base, f"--workdir={directory}"]
        if csh:
            args += ["-e", "csh"]
        return args
    shell = "csh" if csh else "bash"
    quoted = shlex.quote(directory)
    return [base, "-e", shell, "-c", f"cd {quoted} && exec {shell}"]


def open_in_terminal(path: str) -> bool:
    """在终端中打开目录(优先 csh),返回是否成功启动。"""
    from PyQt6.QtCore import QProcess

    argv = _terminal_argv(str(path))
    if not argv:
        return False
    return QProcess.startDetached(argv[0], argv[1:])


class _InlineRenameEditor(QWidget):
    """轻量重命名输入框:显示在右键菜单位置(菜单原地变输入框),回车提交/Esc 取消。"""

    submitted = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self._finished = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._edit = QLineEdit()
        self._edit.setMinimumWidth(180)
        self._edit.setStyleSheet(
            "QLineEdit { border: 1px solid #2c3e50; border-radius: 3px; "
            "padding: 2px 6px; background: white; }"
        )
        self._edit.installEventFilter(self)
        self._edit.editingFinished.connect(self._commit)
        layout.addWidget(self._edit)
        self.setFocusProxy(self._edit)

    def open_at(self, point, text: str) -> None:
        """在全局坐标 point 处显示并聚焦(文本默认全选,屏幕边缘自动收进)。"""
        self._finished = False
        self._edit.setText(text)
        self._edit.selectAll()
        self.adjustSize()
        screen = QApplication.screenAt(point) or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = min(
                max(point.x(), geo.left()),
                max(geo.left(), geo.right() - self.width()),
            )
            y = min(
                max(point.y(), geo.top()),
                max(geo.top(), geo.bottom() - self.height()),
            )
            point = QPoint(x, y)
        self.move(point)
        self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()

    def eventFilter(self, obj, event) -> bool:
        """回车提交 / Esc 取消(其它事件交回默认处理)。"""
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Return:
                self._commit()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._cancel()
                return True
        return super().eventFilter(obj, event)

    def _commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        text = self._edit.text().strip()
        self.hide()
        if text:
            self.submitted.emit(text)

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.cancelled.emit()


class ProjectTreePanel(QWidget):
    """项目管理树;selection_changed 在上下文(数据类型)变化时发出。"""

    selection_changed = pyqtSignal(str, str, str)  # (kind, exp_id, data_id)
    open_requested = pyqtSignal(str)  # 双击实验:请求打开/聚焦该实验
    open_project_requested = pyqtSignal(str)  # 双击未打开项目:请求打开
    data_rename_requested = pyqtSignal(str, str, str)  # (exp_id, data_id, new_name):重命名数据
    rename_project_requested = pyqtSignal(str)  # (new_name):重命名当前项目
    project_create_submitted = pyqtSignal(str)  # 内联命名提交:项目名称
    experiment_create_submitted = pyqtSignal(str)  # 内联命名提交:数据类型标题
    open_path_requested = pyqtSignal(str)  # 打开所在目录(子文件夹右键)
    open_terminal_requested = pyqtSignal(str)  # 在终端中打开(子文件夹右键)
    open_spectrum_requested = pyqtSignal(str)  # 双击谱图文件:右侧直接显示
    delete_project_requested = pyqtSignal()  # Project 右键:删除项目
    create_experiment_requested = pyqtSignal()  # 空白处右键:新建空白实验
    import_data_requested = pyqtSignal(str)  # 数据类型 右键:导入样品数据(exp_id)
    data_action_requested = pyqtSignal(str, str)  # (action, data_id):生成FID/谱/删除
    batch_assign_requested = pyqtSignal(str, str, str)  # (exp_id, data_id, batch_id)
    batch_remove_requested = pyqtSignal(str, str)  # (exp_id, data_id)
    rename_requested = pyqtSignal(str, str)  # (exp_id, new_title):重命名数据类型
    delete_requested = pyqtSignal(str)  # 删除数据类型(exp_id)

    def __init__(
        self,
        manager: ProjectManager | None = None,
        workspace=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager or ProjectManager()
        self.workspace = workspace

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
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        # 内联命名(新建项目/数据类型):监听树内编辑器提交/取消
        delegate = self.tree.itemDelegate()
        delegate.commitData.connect(self._on_editor_commit_data)
        delegate.closeEditor.connect(self._on_editor_closed)
        self._pending_item: QTreeWidgetItem | None = None
        self._pending_kind: str | None = None
        self._pending_text: str | None = None
        # 重命名输入框:右键菜单原地变成输入框(回车提交/Esc 取消)
        self._rename_editor = _InlineRenameEditor()
        self._rename_editor.submitted.connect(self._on_rename_editor_submitted)
        self._rename_editor.cancelled.connect(self._clear_rename_state)
        self._rename_target: tuple | None = None
        layout.addWidget(self.tree)
        self.refresh()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """增量刷新树:只更新变化节点/子目录,未变化节点保留实例。"""
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            workspace_item = QTreeWidgetItem([self._workspace_name(), ""])
            workspace_item.setIcon(0, self._icon("workspace"))
            workspace_item.setToolTip(0, self._workspace_path())
            workspace_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "workspace"})
            self.tree.addTopLevelItem(workspace_item)

        current_root = self.manager.root
        project_dirs = self._workspace_projects()
        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(workspace_item.childCount()):
            item = workspace_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "project":
                existing[str(data.get("path"))] = item
        new_paths = {str(path) for path in project_dirs}
        for path in list(existing):
            if path not in new_paths:
                workspace_item.removeChild(existing[path])

        for project_dir in project_dirs:
            key = str(project_dir)
            is_current = (
                current_root is not None
                and project_dir == Path(current_root).resolve()
            )
            display_name = project_dir.name
            if is_current and self.manager.project is not None:
                display_name = self.manager.project.name or project_dir.name
            project_item = existing.get(key)
            if project_item is None:
                project_item = QTreeWidgetItem([display_name, ""])
                project_item.setIcon(0, self._icon("project"))
                project_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"kind": "project", "path": key},
                )
                workspace_item.addChild(project_item)
            project_item.setText(0, display_name)
            project_item.setText(1, "当前" if is_current else "")
            project_item.setToolTip(
                0,
                f"{project_dir}\n"
                + ("单击打开项目" if not is_current else "当前项目"),
            )
            if is_current and self.manager.project is not None:
                self._refresh_experiments(project_item)
                project_item.setExpanded(True)
            else:
                for index in range(project_item.childCount() - 1, -1, -1):
                    project_item.removeChild(project_item.child(index))
        workspace_item.setExpanded(True)

    def _refresh_experiments(self, project_item: QTreeWidgetItem) -> None:
        """增量刷新数据类型节点(按 exp_id 复用未变化实例)。"""
        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(project_item.childCount()):
            item = project_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "experiment":
                existing[str(data.get("exp_id"))] = item
        experiments = (
            self.manager.project.experiments if self.manager.project else []
        )
        ids = {exp.id for exp in experiments}
        for exp_id in list(existing):
            if exp_id not in ids:
                project_item.removeChild(existing[exp_id])
        for exp in experiments:
            exp_item = existing.get(exp.id)
            if exp_item is None:
                exp_item = self._make_experiment_item(exp)
                project_item.addChild(exp_item)
            else:
                self._update_experiment_item(exp_item, exp)

    def _update_experiment_item(
        self, exp_item: QTreeWidgetItem, exp
    ) -> None:
        """更新数据类型节点文本并增量刷新样品数据节点。"""
        exp_item.setText(0, exp.title or exp.id)
        exp_item.setText(1, _STATUS_TEXT.get(exp.status, exp.status))
        exp_item.setToolTip(0, f"{exp.id}\n右键: 导入样品数据 / 重命名 / 删除")
        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(exp_item.childCount()):
            item = exp_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "data":
                existing[str(data.get("data_id"))] = item
        data_nodes = self._data_of(exp)
        ids = {getattr(n, "id", exp.id) for n in data_nodes}
        for data_id in list(existing):
            if data_id not in ids:
                exp_item.removeChild(existing[data_id])
        for data_node in data_nodes:
            data_id = getattr(data_node, "id", exp.id)
            data_item = existing.get(data_id)
            if data_item is None:
                data_item = self._make_data_item(exp, data_node)
                exp_item.addChild(data_item)
            else:
                self._update_data_item(data_item, exp, data_node)

    def _update_data_item(
        self, data_item: QTreeWidgetItem, exp, data_node
    ) -> None:
        """更新样品数据节点文本/状态,并按目录指纹增量刷新子文件夹文件。"""
        data_id = getattr(data_node, "id", exp.id)
        status = self._data_status(exp, data_node)
        title = getattr(data_node, "title", "") or ""
        from gui.pipeline_state import batch_id

        batch = (
            batch_id(self.manager, exp.id, data_id)
            if self.manager is not None
            else ""
        )
        label = title or f"样品数据 {data_id}"
        if batch:
            label = f"{label} [{batch}]"
        data_item.setText(0, label)
        data_item.setText(1, status)

        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(data_item.childCount()):
            item = data_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "folder":
                existing[str(data.get("folder"))] = item
        for sub in DATA_SUBFOLDERS:
            folder_item = existing.get(sub)
            fingerprint = self._folder_fingerprint(exp.id, data_id, sub)
            if folder_item is None:
                folder_item = QTreeWidgetItem([sub, ""])
                folder_item.setIcon(0, self._icon("folder"))
                folder_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {
                        "kind": "folder",
                        "exp_id": exp.id,
                        "data_id": data_id,
                        "folder": sub,
                        "fingerprint": fingerprint,
                    },
                )
                data_item.addChild(folder_item)
                self._populate_folder_children(folder_item, exp.id, data_id, sub)
            else:
                role = folder_item.data(0, Qt.ItemDataRole.UserRole)
                if role.get("fingerprint") != fingerprint:
                    role["fingerprint"] = fingerprint
                    for index in range(folder_item.childCount() - 1, -1, -1):
                        folder_item.removeChild(folder_item.child(index))
                    self._populate_folder_children(
                        folder_item, exp.id, data_id, sub
                    )
        for sub in list(existing):
            if sub not in DATA_SUBFOLDERS:
                data_item.removeChild(existing[sub])

    def _folder_fingerprint(
        self, exp_id: str, data_id: str, folder: str
    ) -> str:
        """目录指纹:dir mtime + (名称, 大小, mtime) 列表,判断子目录是否变化。"""
        path = self._folder_path(exp_id, data_id, folder)
        if path is None or not path.is_dir():
            return ""
        try:
            parts = [str(path.stat().st_mtime_ns)]
            for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
                try:
                    cst = child.stat()
                    parts.append(
                        f"{child.name}:{cst.st_size}:{cst.st_mtime_ns}"
                    )
                except OSError:
                    continue
            return "|".join(parts)
        except OSError:
            return ""

    def _workspace_projects(self) -> list[Path]:
        """工作区内全部项目目录(含 project.json)。"""
        try:
            if self.workspace is not None:
                return list(self.workspace.list_projects())
            from core.workspace import WorkspaceManager

            return WorkspaceManager().list_projects()
        except Exception:  # noqa: BLE001 - 工作区不可用时回退当前项目
            if self.manager.root is not None:
                return [Path(self.manager.root)]
            return []

    # ------------------------------------------------------------------
    # 工作区路径(契约 v1.3;core/workspace 落地后改用 WorkspaceManager)
    # ------------------------------------------------------------------
    def _workspace_path(self) -> str:
        from core.workspace import default_workspace_path

        try:
            return str(default_workspace_path())
        except Exception:  # noqa: BLE001
            if self.manager.root is not None:
                return str(self.manager.root.parent)
            return str(Path.home())

    def _workspace_name(self) -> str:
        return Path(self._workspace_path()).name or "Workspace"

    def _make_experiment_item(self, exp) -> QTreeWidgetItem:
        status = exp.status
        exp_item = QTreeWidgetItem([exp.title or exp.id, _STATUS_TEXT.get(status, status)])
        exp_item.setIcon(0, self._icon("experiment"))
        exp_item.setToolTip(0, f"{exp.id}\n右键: 导入样品数据 / 重命名 / 删除")
        exp_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "experiment", "exp_id": exp.id})
        for data_node in self._data_of(exp):
            data_item = self._make_data_item(exp, data_node)
            exp_item.addChild(data_item)
        exp_item.setExpanded(False)
        return exp_item

    def _data_of(self, exp) -> list:
        """数据类型下的样品数据节点(空白数据类型无样品数据则不显示样品数据子节点)。"""
        return list(getattr(exp, "data", None) or [])

    def _make_data_item(self, exp, data_node) -> QTreeWidgetItem:
        data_id = getattr(data_node, "id", exp.id)
        source = getattr(data_node, "source", "") or getattr(exp, "source", "")
        status = self._data_status(exp, data_node)
        title = getattr(data_node, "title", "") or ""
        from gui.pipeline_state import batch_id

        batch = (
            batch_id(self.manager, exp.id, data_id)
            if self.manager is not None
            else ""
        )
        label = title or f"样品数据 {data_id}"
        if batch:
            label = f"{label} [{batch}]"
        data_item = QTreeWidgetItem([label, status])
        data_item.setIcon(0, self._icon("data"))
        tooltip = f"{data_id}\n来源: {source}"
        if batch:
            tooltip += f"\n批量组: {batch}"
        tooltip += "\n右键: 生成 FID / 生成谱图 / 删除"
        data_item.setToolTip(0, tooltip)
        data_item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {"kind": "data", "exp_id": exp.id, "data_id": data_id},
        )
        for sub in DATA_SUBFOLDERS:
            sub_item = QTreeWidgetItem([sub, ""])
            sub_item.setIcon(0, self._icon("folder"))
            sub_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {
                    "kind": "folder",
                    "exp_id": exp.id,
                    "data_id": data_id,
                    "folder": sub,
                    "fingerprint": self._folder_fingerprint(
                        exp.id, data_id, sub
                    ),
                },
            )
            self._populate_folder_children(sub_item, exp.id, data_id, sub)
            data_item.addChild(sub_item)
        data_item.setExpanded(False)
        return data_item

    def _populate_folder_children(
        self, folder_item: QTreeWidgetItem, exp_id: str, data_id: str, folder: str
    ) -> None:
        """把子文件夹内的文件挂到文件夹节点下(可下拉查看)。"""
        path = self._folder_path(exp_id, data_id, folder)
        if path is None or not path.is_dir():
            return
        for child in sorted(path.iterdir(), key=lambda p: (p.is_dir(), p.name.lower())):
            name = child.name
            file_item = QTreeWidgetItem([name, ""])
            file_item.setIcon(0, self._icon("folder" if child.is_dir() else "file"))
            file_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {
                    "kind": "file",
                    "exp_id": exp_id,
                    "data_id": data_id,
                    "folder": folder,
                    "name": name,
                },
            )
            folder_item.addChild(file_item)

    def _data_status(self, exp, data_node) -> str:
        """按产物文件推断样品数据状态。"""
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
            "workspace": "W",
            "project": "P",
            "experiment": "E",
            "data": "D",
            "folder": "▸",
            "file": "•",
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
        """返回当前选中节点所属数据类型 id(选择样品数据节点时归一化到数据类型)。"""
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

    def select_data(self, exp_id: str, data_id: str) -> None:
        """选中数据类型下的样品数据节点(展开数据类型;找不到时静默)。"""
        exp_item = self._find_experiment_item(exp_id)
        if exp_item is None:
            return
        for index in range(exp_item.childCount()):
            child = exp_item.child(index)
            if self._data_id_of(child) == data_id:
                self.tree.expandItem(exp_item)
                self.tree.setCurrentItem(child)
                return

    def select_experiment(self, exp_id: str) -> None:
        """按 id 递归定位并选中数据类型节点(Workspace → Project → Experiment)。"""
        target = self._find_experiment_item(exp_id)
        if target is None:
            return
        parent = target.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        target.setExpanded(True)
        self.tree.setCurrentItem(target)

    def _find_experiment_item(
        self, exp_id: str, item: QTreeWidgetItem | None = None
    ) -> QTreeWidgetItem | None:
        """深度优先查找数据类型节点。"""
        if item is None:
            for i in range(self.tree.topLevelItemCount()):
                found = self._find_experiment_item(exp_id, self.tree.topLevelItem(i))
                if found is not None:
                    return found
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("exp_id") == exp_id:
            return item
        for i in range(item.childCount()):
            found = self._find_experiment_item(exp_id, item.child(i))
            if found is not None:
                return found
        return None

    def _on_selection_changed(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            self.selection_changed.emit("", "", "")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        kind = data.get("kind") if isinstance(data, dict) else ""
        self.selection_changed.emit(
            kind, self._experiment_id_of(item), self._data_id_of(item)
        )

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """单击项目节点即打开(无需双击)。"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("kind") != "project":
            return
        path = data.get("path")
        if not path:
            return
        is_current = (
            self.manager.root is not None
            and Path(str(path)) == Path(self.manager.root).resolve()
        )
        if not is_current:
            self.open_project_requested.emit(str(path))

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        kind = data.get("kind") if isinstance(data, dict) else ""
        if kind == "project" and data.get("path"):
            path = str(data["path"])
            if self.manager.root is None or Path(path) != Path(self.manager.root).resolve():
                self.open_project_requested.emit(path)
            return
        if kind == "file":
            # 双击文件:谱图文件直接右侧显示,其它文件打开所在目录
            folder = data.get("folder", "")
            name = data.get("name", "")
            if folder == "spectra" and name.lower().endswith((".ft2", ".ft3")):
                path = self._folder_path_for_item(item)
                if path is not None and path.is_file():
                    self.open_spectrum_requested.emit(str(path))
            else:
                folder_path = self._folder_path_for_item(item)
                if folder_path is not None:
                    target = (
                        folder_path.parent
                        if not folder_path.is_dir()
                        else folder_path
                    )
                    self.open_path_requested.emit(str(target))
            return
        if kind in ("data", "folder"):
            # 双击样品数据/子文件夹:打开文件管理器对应目录,中间保持 Pipeline
            folder_path = self._folder_path_for_item(item)
            if folder_path is not None:
                self.open_path_requested.emit(str(folder_path))
            return
        exp_id = self._experiment_id_of(item)
        if exp_id:
            self.open_requested.emit(exp_id)

    def _request_batch_assign(self, exp_id: str, data_id: str) -> None:
        """弹出批量组选择(可输入新编号,留空自动编号)。"""
        from gui.pipeline_state import batch_id, batch_ids_in_experiment

        groups = batch_ids_in_experiment(self.manager, exp_id)
        current = batch_id(self.manager, exp_id, data_id)
        items = list(groups)
        if current and current not in items:
            items.insert(0, current)
        if not items:
            items = [""]
        value, ok = QInputDialog.getItem(
            self,
            "加入批量组",
            "选择或输入批量组编号(留空自动编号):",
            items,
            editable=True,
        )
        if ok:
            self.batch_assign_requested.emit(exp_id, data_id, value)

    def _folder_path_for_item(self, item: QTreeWidgetItem) -> Path | None:
        """解析 data 或 folder 节点的真实目录(双击打开用)。"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return None
        exp_id = self._experiment_id_of(item)
        data_id = self._data_id_of(item)
        if not exp_id or not data_id:
            return None
        if data.get("kind") == "folder":
            return self._folder_path(exp_id, data_id, data.get("folder", ""))
        if data.get("kind") == "file":
            folder = self._folder_path(exp_id, data_id, data.get("folder", ""))
            if folder is not None:
                candidate = folder / str(data.get("name", ""))
                return candidate if candidate.exists() else None
        # data 节点:优先 raw 目录,缺失时回退 data 基座
        try:
            raw_dir = self.manager.data_dir(exp_id, data_id, "raw")
            if raw_dir.is_dir():
                return raw_dir
        except Exception:  # noqa: BLE001
            pass
        try:
            base = self.manager.data_base(exp_id, data_id)
            return base if base.is_dir() else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = self._on_context_menu_impl(QMenu(self), item, pos)
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_context_menu_impl(self, menu: QMenu, item, pos=None) -> QMenu:
        """构建右键菜单(独立方法便于测试触发动作);pos 为视口内坐标,用于弹窗定位。"""
        anchor = self.tree.viewport().mapToGlobal(pos) if pos is not None else None
        if item is None:
            # 空白处:新建空白数据类型(项目已打开时)
            if self.manager.project is not None:
                menu.addAction("新建空白数据类型...", self.create_experiment_requested.emit)
        else:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            kind = data.get("kind") if isinstance(data, dict) else None
            exp_id = self._experiment_id_of(item)
            data_id = self._data_id_of(item)
            if kind == "project":
                is_current = (
                    self.manager.root is not None
                    and data.get("path")
                    and Path(str(data["path"])) == Path(self.manager.root).resolve()
                )
                if not is_current and data.get("path"):
                    menu.addAction(
                        "打开项目...",
                        lambda p=str(data["path"]): self.open_project_requested.emit(p),
                    )
                else:
                    menu.addAction("新建空白数据类型...", self.create_experiment_requested.emit)
                    menu.addAction(
                        "重命名项目...",
                        lambda _checked=False: self._begin_rename("project", item, anchor),
                    )
                    menu.addSeparator()
                    menu.addAction("删除项目...", self.delete_project_requested.emit)
            elif kind == "experiment" and exp_id:
                menu.addAction("导入样品数据...", lambda: self.import_data_requested.emit(exp_id))
                menu.addAction(
                    "重命名...",
                    lambda _checked=False: self._begin_rename("experiment", item, anchor),
                )
                menu.addSeparator()
                menu.addAction("删除数据类型", lambda: self.delete_requested.emit(exp_id))
            elif kind == "data" and exp_id and data_id:
                folder_path = self._folder_path_for_item(item)
                if folder_path is not None:
                    menu.addAction(
                        "打开所在目录",
                        lambda p=folder_path: self.open_path_requested.emit(str(p)),
                    )
                    menu.addAction(
                        "在终端中打开",
                        lambda p=folder_path: self.open_terminal_requested.emit(str(p)),
                    )
                menu.addAction(
                    "重命名...",
                    lambda _checked=False: self._begin_rename("data", item, anchor),
                )
                menu.addSeparator()
                from gui.pipeline_state import batch_id

                current_batch = batch_id(self.manager, exp_id, data_id)
                menu.addAction(
                    "加入批量组...",
                    lambda: self._request_batch_assign(exp_id, data_id),
                )
                if current_batch:
                    menu.addAction(
                        "移出批量组",
                        lambda: self.batch_remove_requested.emit(exp_id, data_id),
                    )
                menu.addAction(
                    "删除样品数据",
                    lambda: self.data_action_requested.emit("delete", data_id),
                )
            elif kind == "folder" and exp_id and data_id:
                folder_path = self._folder_path(exp_id, data_id, data.get("folder", ""))
                if folder_path is not None:
                    menu.addAction(
                        "打开所在目录",
                        lambda p=folder_path: self.open_path_requested.emit(str(p)),
                    )
                    menu.addAction(
                        "在终端中打开",
                        lambda p=folder_path: self.open_terminal_requested.emit(str(p)),
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

    # ------------------------------------------------------------------
    # 内联命名(新建项目/数据类型,不弹窗)
    # ------------------------------------------------------------------
    def begin_create_project(self, initial: str = "unnamed") -> None:
        """新建项目:项目树内内联命名(不弹窗),回车提交 / Esc 取消。"""
        self._cancel_pending_create()
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            return
        item = QTreeWidgetItem([initial or "unnamed", ""])
        item.setIcon(0, self._icon("project"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "pending_project"})
        item.setToolTip(0, "输入项目名称后回车创建,Esc 取消")
        workspace_item.addChild(item)
        workspace_item.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self._pending_item = item
        self._pending_kind = "project"
        self._pending_text = initial
        self._start_editing(item)

    def begin_create_experiment(self, initial: str = "") -> None:
        """新建空白数据类型:项目树内内联命名(不弹窗),回车提交 / Esc 取消。"""
        if self.manager.project is None:
            return
        project_item = self._current_project_item()
        if project_item is None:
            return
        self._cancel_pending_create()
        item = QTreeWidgetItem([initial or "新数据类型", ""])
        item.setIcon(0, self._icon("experiment"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "pending_experiment"})
        item.setToolTip(0, "输入数据类型标题后回车创建,Esc 取消")
        project_item.addChild(item)
        project_item.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self._pending_item = item
        self._pending_kind = "experiment"
        self._pending_text = initial
        self._start_editing(item)

    def _start_editing(self, item: QTreeWidgetItem) -> None:
        """树可见时直接进入编辑(测试/离屏场景由提交钩子驱动)。"""
        if self.tree.isVisible():
            self.tree.editItem(item, 0)

    def _on_editor_commit_data(self, editor) -> None:
        if self._pending_item is not None and editor is not None:
            self._pending_text = editor.text()

    def _on_editor_closed(self, _editor, hint) -> None:
        if self._pending_item is None:
            return
        if hint == QAbstractItemDelegate.EndEditHint.RevertModelCache:
            self._cancel_pending_create()
            return
        self._commit_pending_create(self._pending_text or "")

    def _commit_pending_create(self, text: str) -> None:
        """提交内联命名(回车/失焦提交,测试可直调);空文本视为取消。"""
        kind = self._pending_kind
        self._remove_pending()
        text = (text or "").strip()
        if kind == "project" and text:
            self.project_create_submitted.emit(text)
        elif kind == "experiment" and text:
            self.experiment_create_submitted.emit(text)

    def _cancel_pending_create(self) -> None:
        """取消内联命名(移除待命名节点)。"""
        self._remove_pending()

    def _remove_pending(self) -> None:
        item, self._pending_item = self._pending_item, None
        self._pending_kind = None
        self._pending_text = None
        if item is None:
            return
        parent = item.parent()
        if parent is not None and parent.indexOfChild(item) >= 0:
            parent.removeChild(item)

    def _current_project_item(self) -> QTreeWidgetItem | None:
        """当前打开项目对应的树节点(找不到时回退首个项目节点)。"""
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            return None
        root = self.manager.root
        for index in range(workspace_item.childCount()):
            child = workspace_item.child(index)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict) or data.get("kind") != "project":
                continue
            path = data.get("path")
            if root is None:
                return child
            try:
                if path and Path(str(path)).resolve() == Path(root).resolve():
                    return child
            except OSError:
                continue
        return workspace_item.child(0) if workspace_item.childCount() else None

    # ------------------------------------------------------------------
    # 重命名:右键菜单原地变成重命名输入框(回车提交/Esc 取消)
    # ------------------------------------------------------------------
    def begin_rename_experiment(self, exp_id: str) -> None:
        """菜单「重命名数据类型」:在树节点附近显示重命名输入框。"""
        item = self._find_experiment_item(exp_id)
        if item is None:
            return
        exp = self.manager.project.experiment(exp_id) if self.manager.project else None
        current = exp.title if exp is not None else item.text(0)
        anchor = self.tree.viewport().mapToGlobal(self.tree.visualItemRect(item).center())
        self._rename_target = ("experiment", exp_id)
        self._rename_editor.open_at(anchor, current)

    def _begin_rename(self, kind: str, item: QTreeWidgetItem, anchor) -> None:
        """右键「重命名」:记录目标并在右键位置打开重命名输入框。"""
        current = ""
        target: tuple | None = None
        if kind == "project":
            current = (
                self.manager.project.name
                if self.manager.project is not None
                else item.text(0)
            )
            target = ("project",)
        elif kind == "experiment":
            exp_id = self._experiment_id_of(item)
            exp = (
                self.manager.project.experiment(exp_id)
                if self.manager.project is not None
                else None
            )
            current = exp.title if exp is not None else item.text(0)
            target = ("experiment", exp_id)
        elif kind == "data":
            exp_id = self._experiment_id_of(item)
            data_id = self._data_id_of(item)
            entry = (
                self.manager.project.experiment(exp_id)
                if self.manager.project is not None
                else None
            )
            data_entry = (
                next((d for d in entry.data if d.id == data_id), None)
                if entry is not None
                else None
            )
            current = getattr(data_entry, "title", "") or ""
            target = ("data", exp_id, data_id)
        if target is None or anchor is None:
            return
        self._rename_target = target
        self._rename_editor.open_at(anchor, current)

    def _on_rename_editor_submitted(self, text: str) -> None:
        """重命名输入框回车提交:按目标发出重命名信号。"""
        target = self._rename_target
        self._rename_target = None
        if target is None:
            return
        kind = target[0]
        if kind == "project":
            self.rename_project_requested.emit(text)
        elif kind == "experiment":
            self.rename_requested.emit(target[1], text)
        elif kind == "data":
            self.data_rename_requested.emit(target[1], target[2], text)

    def _clear_rename_state(self) -> None:
        """重命名取消(Esc/点击外部):清理目标。"""
        self._rename_target = None

    def _folder_path(self, exp_id: str, data_id: str, folder: str) -> Path | None:
        """样品数据子文件夹真实路径(契约 v1.3:data_dir(exp_id, data_id, key))。"""
        try:
            return self.manager.data_dir(exp_id, data_id, folder)
        except Exception:  # noqa: BLE001 - 旧布局/未落地时回退 None
            return None
