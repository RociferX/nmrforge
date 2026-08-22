"""Dashboard 面板:项目/实验类型概览(GUI_ARCHITECTURE_VISION §11-12)。

- ProjectDashboard:项目统计(实验类型/样品数据/处理完成度)+ 最近运行 + 新建实验类型表单;
- ExperimentDashboard:样品数据列表(每样品数据状态)+ 导入样品数据表单。

数据来源:core.project(ProjectManager);运行历史来自 workflow_runs。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.dialogs import InfoDialog


def _data_count(project) -> int:
    return sum(len(exp.data) for exp in project.experiments)


def _data_processed(project) -> int:
    count = 0
    for exp in project.experiments:
        for data in exp.data:
            status = getattr(data, "status", "") or ""
            if status in ("fid_ready", "processed"):
                count += 1
    return count


class ProjectDashboard(QWidget):
    """项目概览:统计 + 处理完成度 + 最近运行 + 新建实验类型。"""

    create_experiment_requested = pyqtSignal(str)  # 实验类型标题

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("项目")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #333;")
        layout.addWidget(self.stats_label)
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)
        layout.addSpacing(10)

        recent_title = QLabel("最近运行")
        recent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(recent_title)
        self.runs_table = QTableWidget(0, 4)
        self.runs_table.setHorizontalHeaderLabels(["运行", "流程", "状态", "时间"])
        self.runs_table.horizontalHeader().setStretchLastSection(True)
        self.runs_table.setMaximumHeight(150)
        layout.addWidget(self.runs_table)
        layout.addSpacing(10)

        form = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("实验类型标题(可留空)")
        form.addWidget(self.title_edit, 1)
        self.create_button = QPushButton("新建实验类型")
        self.create_button.clicked.connect(self._on_create)
        form.addWidget(self.create_button)
        layout.addLayout(form)
        layout.addStretch(1)

    def set_context(self, manager: ProjectManager) -> None:
        self.manager = manager
        self.refresh()

    def refresh(self) -> None:
        if self.manager is None or self.manager.project is None:
            return
        project = self.manager.project
        self.context_label.setText(project.name)
        exp_count = len(project.experiments)
        data_count = _data_count(project)
        processed = _data_processed(project)
        self.stats_label.setText(
            f"实验类型: {exp_count}  |  样品数据: {data_count}  |  已处理: {processed}"
        )
        if data_count:
            pct = round(processed * 100 / data_count)
            self.progress_label.setText(f"处理完成度: {pct}%")
        else:
            self.progress_label.setText("处理完成度: - (暂无数据)")

        self.runs_table.setRowCount(0)
        recent = list(project.workflow_runs)[-8:]
        for run in recent:
            row = self.runs_table.rowCount()
            self.runs_table.insertRow(row)
            self.runs_table.setItem(row, 0, QTableWidgetItem(run.run_id))
            self.runs_table.setItem(row, 1, QTableWidgetItem(run.workflow_ref))
            self.runs_table.setItem(row, 2, QTableWidgetItem(run.status))
            self.runs_table.setItem(
                row, 3, QTableWidgetItem((run.finished_at or run.started_at)[:19])
            )

    def _on_create(self) -> None:
        self.create_experiment_requested.emit(self.title_edit.text().strip())


class ExperimentImportPanel(QWidget):
    """导入数据面板:单个/分段/批量导入 + 链接选项(0.2.162-补11)。

    实验类型页不再内联展示,由主界面「导入数据」按钮下拉弹出。"""

    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    segmented_import_requested = pyqtSignal(str, str)  # (exp_id, 分段采集容器目录)
    batch_import_requested = pyqtSignal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.copy_check = QCheckBox("链接原始数据到项目(只读文件链接,必要时复制)")
        self.copy_check.setChecked(True)
        layout.addWidget(self.copy_check)
        layout.addSpacing(4)

        self.single_group = QGroupBox("单个导入")
        single_layout = QVBoxLayout(self.single_group)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("样品数据名称(可选)")
        single_layout.addWidget(self.name_edit)
        form = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Bruker 数据集目录(含 acqus)")
        form.addWidget(self.source_edit, 1)
        browse = QPushButton("浏览...")
        browse.clicked.connect(self._browse)
        form.addWidget(browse)
        single_layout.addLayout(form)
        self.import_button = QPushButton("导入样品数据")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._on_import)
        single_layout.addWidget(self.import_button)
        self.source_edit.textChanged.connect(
            lambda _t: self.import_button.setEnabled(
                bool(self.source_edit.text().strip())
            )
        )
        layout.addWidget(self.single_group)

        self.segmented_group = QGroupBox("分段采集导入(合并 FID)")
        segmented_layout = QVBoxLayout(self.segmented_group)
        segmented_hint = QLabel(
            "用于同一次实验分多段采集、需要合并 FID 的数据:选择容器目录"
            "(顶层无 acqus,至少 2 个子目录各含 acqus),导入后自动合并为一条样品数据"
        )
        segmented_hint.setWordWrap(True)
        segmented_hint.setStyleSheet("color: #666;")
        segmented_layout.addWidget(segmented_hint)
        segmented_form = QHBoxLayout()
        self.segmented_source_edit = QLineEdit()
        self.segmented_source_edit.setPlaceholderText(
            "分段采集容器目录(含多个 acqus 子目录)"
        )
        segmented_form.addWidget(self.segmented_source_edit, 1)
        segmented_browse = QPushButton("浏览...")
        segmented_browse.clicked.connect(self._on_segmented_browse)
        segmented_form.addWidget(segmented_browse)
        segmented_layout.addLayout(segmented_form)
        self.segmented_import_button = QPushButton("分段采集导入")
        self.segmented_import_button.setEnabled(False)
        self.segmented_import_button.clicked.connect(self._on_segmented_import)
        segmented_layout.addWidget(self.segmented_import_button)
        self.segmented_source_edit.textChanged.connect(
            lambda _t: self.segmented_import_button.setEnabled(
                bool(self.segmented_source_edit.text().strip())
            )
        )
        layout.addWidget(self.segmented_group)

        self.batch_group = QGroupBox("批量处理")
        batch_layout = QVBoxLayout(self.batch_group)
        batch_hint = QLabel(
            "可添加总文件夹(自动检查子文件夹中的 Bruker 数据集)或多个数据目录;"
            "同批数据绑定同一批量组标记,中间处理页操作对整组数据执行"
        )
        batch_hint.setWordWrap(True)
        batch_hint.setStyleSheet("color: #666;")
        batch_layout.addWidget(batch_hint)
        self.batch_list = QListWidget()
        self.batch_list.setMaximumHeight(110)
        batch_layout.addWidget(self.batch_list)
        self.batch_group_check = QCheckBox(
            "批量导入并成组(后续处理会一起处理);不勾选则不成组(相当于多个单次导入)"
        )
        self.batch_group_check.setChecked(True)
        batch_layout.addWidget(self.batch_group_check)
        batch_buttons = QHBoxLayout()
        self.batch_add_button = QPushButton("添加数据文件夹...")
        self.batch_add_button.clicked.connect(self._on_batch_add_folder)
        batch_buttons.addWidget(self.batch_add_button)
        self.batch_clear_button = QPushButton("清空列表")
        self.batch_clear_button.clicked.connect(self._on_batch_clear)
        batch_buttons.addWidget(self.batch_clear_button)
        self.batch_import_button = QPushButton("批量导入")
        self.batch_import_button.setEnabled(False)
        self.batch_import_button.clicked.connect(self._on_batch_import)
        batch_buttons.addWidget(self.batch_import_button)
        batch_layout.addLayout(batch_buttons)
        layout.addWidget(self.batch_group)
        layout.addStretch(1)

    def set_context(self, exp_id: str) -> None:
        self._exp_id = exp_id

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据集目录", self.source_edit.text() or str(Path.home())
        )
        if path:
            self.source_edit.setText(path)

    def _on_batch_add_folder(self) -> None:
        """批量列表添加数据文件夹(自动检查子文件夹中的 Bruker 数据集)。"""
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据文件夹(批量)"
        )
        if not path:
            return
        found = self._bruker_datasets_under(Path(path))
        if not found:
            InfoDialog.show_info(
                self,
                "未找到数据",
                "所选目录及其子文件夹中没有含 acqus 的 Bruker 数据集",
            )
            return
        for dataset_dir in found:
            if not self.batch_list.findItems(
                str(dataset_dir), Qt.MatchFlag.MatchExactly
            ):
                self.batch_list.addItem(str(dataset_dir))
        self.batch_import_button.setEnabled(self.batch_list.count() > 0)

    @staticmethod
    def _bruker_datasets_under(root: Path) -> list[Path]:
        """root 及子文件夹中所有含 acqus 的数据集目录(排序去重)。"""
        datasets: set[Path] = set()
        try:
            candidates = [
                Path(p).parent for p in root.rglob("acqus") if p.is_file()
            ]
            candidates.append(root)
        except OSError:
            candidates = [root]
        for cand in candidates:
            if (cand / "acqus").is_file():
                datasets.add(cand.resolve())
        return sorted(datasets)

    def _on_batch_clear(self) -> None:
        self.batch_list.clear()
        self.batch_import_button.setEnabled(False)

    def _on_batch_import(self) -> None:
        """把列表中的多个数据目录导入当前实验类型;group=True 同一批量组。"""
        if not self._exp_id or self.batch_list.count() == 0:
            return
        folders = [
            self.batch_list.item(index).text()
            for index in range(self.batch_list.count())
        ]
        self.batch_import_requested.emit(
            self._exp_id, folders, self.batch_group_check.isChecked()
        )

    def _on_import(self) -> None:
        source = self.source_edit.text().strip()
        if not source or not self._exp_id:
            return
        self.import_options_requested.emit(
            self._exp_id,
            self.name_edit.text().strip(),
            source,
            self.copy_check.isChecked(),
        )

    def clear_import_form(self) -> None:
        """导入成功后清空单个/分段导入表单的名称与路径(0.2.112)。"""
        self.name_edit.clear()
        self.source_edit.clear()
        self.segmented_source_edit.clear()

    def _on_segmented_browse(self) -> None:
        """选择分段采集容器目录。"""
        path = QFileDialog.getExistingDirectory(
            self,
            "选择分段采集容器目录",
            self.segmented_source_edit.text() or str(Path.home()),
        )
        if path:
            self.segmented_source_edit.setText(path)

    def _on_segmented_import(self) -> None:
        """分段采集导入:容器目录(合并 FID)直接发请求(带当前实验类型,0.2.122)。"""
        source = self.segmented_source_edit.text().strip()
        if not source:
            return
        self.segmented_import_requested.emit(self._exp_id, source)


class ImportDataDropdown(QWidget):
    """「导入数据」下拉面板:向下弹出,内含完整导入表单(0.2.162-补11)。"""

    import_options_requested = pyqtSignal(str, str, str, bool)
    segmented_import_requested = pyqtSignal(str, str)
    batch_import_requested = pyqtSignal(str, list, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self._anchor: QWidget | None = None
        self._app = QApplication.instance()
        if self._app is not None:
            self.destroyed.connect(self._remove_event_filter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("导入样品数据")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        self.panel = ExperimentImportPanel(self)
        self.panel.import_options_requested.connect(self.import_options_requested.emit)
        self.panel.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self.panel.batch_import_requested.connect(self.batch_import_requested.emit)
        layout.addWidget(self.panel)
        self.setMinimumWidth(560)

    def open_below(self, anchor: QWidget, exp_id: str) -> None:
        """在 anchor 按钮正下方弹出,屏幕边缘自动收进。"""
        self._anchor = anchor
        if self._app is not None:
            self._app.installEventFilter(self)
        self.panel.set_context(exp_id)
        pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = min(
                max(pos.x(), geo.left()),
                max(geo.left(), geo.right() - self.width()),
            )
            y = min(
                max(pos.y(), geo.top()),
                max(geo.top(), geo.bottom() - self.height()),
            )
            pos = QPoint(x, y)
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()

    def eventFilter(self, obj, event) -> bool:
        """非抓取窗口:点其它按钮/外部时先关掉本下拉,点击继续落到目标。"""
        try:
            visible = self.isVisible()
        except RuntimeError:  # pragma: no cover - 销毁竞态
            return False
        if visible and event.type() == QEvent.Type.MouseButtonPress:
            if hasattr(event, "globalPosition"):
                pos = event.globalPosition().toPoint()
            else:  # pragma: no cover - Qt5 兼容
                pos = event.globalPos()
            if self._anchor is not None and self._anchor.rect().contains(
                self._anchor.mapFromGlobal(pos)
            ):
                return False  # 锚点按钮:交给按钮处理(开关/切换)
            if not self.geometry().contains(pos):
                self.close()
        return False

    def hideEvent(self, event) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)
        super().hideEvent(event)

    def _remove_event_filter(self) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)


class GroupAnalysisDropdown(QWidget):
    """「数据组间分析」下拉面板(占位,0.2.162-补11)。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self._anchor: QWidget | None = None
        self._app = QApplication.instance()
        if self._app is not None:
            self.destroyed.connect(self._remove_event_filter)
        layout = QVBoxLayout(self)
        label = QLabel("数据组间分析\n\n功能开发中,敬请期待")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("padding: 24px 32px; color: #666;")
        layout.addWidget(label)
        self.setMinimumWidth(340)

    def open_below(self, anchor: QWidget) -> None:
        self._anchor = anchor
        if self._app is not None:
            self._app.installEventFilter(self)
        pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = min(
                max(pos.x(), geo.left()),
                max(geo.left(), geo.right() - self.width()),
            )
            y = min(
                max(pos.y(), geo.top()),
                max(geo.top(), geo.bottom() - self.height()),
            )
            pos = QPoint(x, y)
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()

    def eventFilter(self, obj, event) -> bool:
        """非抓取窗口:点其它按钮/外部时先关掉本下拉。"""
        try:
            visible = self.isVisible()
        except RuntimeError:  # pragma: no cover - 销毁竞态
            return False
        if visible and event.type() == QEvent.Type.MouseButtonPress:
            if hasattr(event, "globalPosition"):
                pos = event.globalPosition().toPoint()
            else:  # pragma: no cover - Qt5 兼容
                pos = event.globalPos()
            if self._anchor is not None and self._anchor.rect().contains(
                self._anchor.mapFromGlobal(pos)
            ):
                return False
            if not self.geometry().contains(pos):
                self.close()
        return False

    def hideEvent(self, event) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)
        super().hideEvent(event)

    def _remove_event_filter(self) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)


class ExperimentDashboard(QWidget):
    """实验类型概览:样品数据列表(状态,名称可改);导入块已移入「导入数据」下拉。"""

    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    data_rename_requested = pyqtSignal(str, str, str)  # (exp_id, data_id, new_name)
    segmented_import_requested = pyqtSignal(str, str)  # (exp_id, 分段采集容器目录)
    batch_import_requested = pyqtSignal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("实验类型")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        data_title = QLabel("样品数据")
        data_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(data_title)
        self.data_table = QTableWidget(0, 3)
        self.data_table.setHorizontalHeaderLabels(["样品数据", "名称", "状态"])
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.setMaximumHeight(160)
        # 0.2.162-补13:名称列可编辑(双击/选中点击/F2),改名走 manager.rename_data
        self.data_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.data_table.itemChanged.connect(self._on_data_name_edited)
        self._loading_table = False
        layout.addWidget(self.data_table)
        layout.addSpacing(10)

        # 0.2.162-补11:导入块移入「导入数据」下拉面板,实验类型页不再内联展示
        self.import_panel = ExperimentImportPanel(self)
        self.import_panel.hide()
        self.import_panel.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self.import_panel.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self.import_panel.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        # 兼容旧测试/旧调用:表单控件与处理器转发到导入面板
        self.copy_check = self.import_panel.copy_check
        self.single_group = self.import_panel.single_group
        self.name_edit = self.import_panel.name_edit
        self.source_edit = self.import_panel.source_edit
        self.import_button = self.import_panel.import_button
        self.segmented_group = self.import_panel.segmented_group
        self.segmented_source_edit = self.import_panel.segmented_source_edit
        self.segmented_import_button = self.import_panel.segmented_import_button
        self.batch_group = self.import_panel.batch_group
        self.batch_list = self.import_panel.batch_list
        self.batch_add_button = self.import_panel.batch_add_button
        self.batch_clear_button = self.import_panel.batch_clear_button
        self.batch_import_button = self.import_panel.batch_import_button
        # 0.2.162-补12:「导入数据」「数据组间分析」按钮(原导入块位置)
        action_row = QHBoxLayout()
        self.import_dropdown_button = QPushButton("导入数据")
        self.import_dropdown_button.clicked.connect(self._open_import_dropdown)
        action_row.addWidget(self.import_dropdown_button)
        self.group_analysis_button = QPushButton("数据组间分析")
        self.group_analysis_button.clicked.connect(
            self._open_group_analysis_dropdown
        )
        action_row.addWidget(self.group_analysis_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self._import_dropdown = ImportDataDropdown(self)
        self._import_dropdown.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self._import_dropdown.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self._import_dropdown.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        self._group_analysis_dropdown = GroupAnalysisDropdown(self)
        layout.addStretch(1)

    def _open_import_dropdown(self) -> None:
        """实验类型页「导入数据」:弹出/收起导入表单下拉(0.2.162-补13)。"""
        if self._import_dropdown.isVisible():
            self._import_dropdown.close()
            return
        self._group_analysis_dropdown.close()
        self._import_dropdown.open_below(self.import_dropdown_button, self._exp_id)

    def _open_group_analysis_dropdown(self) -> None:
        """实验类型页「数据组间分析」:占位下拉(0.2.162-补13)。"""
        if self._group_analysis_dropdown.isVisible():
            self._group_analysis_dropdown.close()
            return
        self._import_dropdown.close()
        self._group_analysis_dropdown.open_below(self.group_analysis_button)

    def set_context(self, manager: ProjectManager, exp_id: str, label: str) -> None:
        self.manager = manager
        self._exp_id = exp_id
        self.import_panel.set_context(exp_id)
        self.context_label.setText(f"{label} ({exp_id})" if exp_id else "")
        self.refresh()

    def _on_data_name_edited(self, item) -> None:
        """数据表「名称」列编辑后重命名样品数据(0.2.162-补13)。"""
        if item.column() != 1 or self._loading_table:
            return
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        data_item = self.data_table.item(item.row(), 0)
        if data_item is None:
            return
        data_id = data_item.text()
        new_name = item.text().strip()
        entry = self.manager.project.experiment(self._exp_id)
        data_entry = (
            next((d for d in entry.data if d.id == data_id), None) if entry else None
        )
        if data_entry is None or new_name == (getattr(data_entry, "title", "") or ""):
            return
        self.data_rename_requested.emit(self._exp_id, data_id, new_name)
        self.refresh()

    def refresh(self) -> None:
        self.data_table.setRowCount(0)
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        exp = self.manager.project.experiment(self._exp_id)
        if exp is None:
            return
        self._loading_table = True
        try:
            for data in exp.data:
                row = self.data_table.rowCount()
                self.data_table.insertRow(row)
                self.data_table.setItem(row, 0, QTableWidgetItem(data.id))
                self.data_table.setItem(
                    row, 1, QTableWidgetItem(getattr(data, "title", "") or "")
                )
                self.data_table.setItem(
                    row, 2, QTableWidgetItem(getattr(data, "status", "") or "")
                )
        finally:
            self._loading_table = False

    def _browse(self) -> None:
        self.import_panel._browse()

    def _on_batch_add_folder(self) -> None:
        self.import_panel._on_batch_add_folder()

    @staticmethod
    def _bruker_datasets_under(root: Path) -> list[Path]:
        return ExperimentImportPanel._bruker_datasets_under(root)

    def _on_batch_clear(self) -> None:
        self.import_panel._on_batch_clear()

    def _on_batch_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel._on_batch_import()

    def _on_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel._on_import()

    def clear_import_form(self) -> None:
        """导入成功后清空导入表单(含下拉面板,0.2.162-补12)。"""
        self.import_panel.clear_import_form()
        if self._import_dropdown is not None:
            self._import_dropdown.panel.clear_import_form()

    def _on_segmented_browse(self) -> None:
        self.import_panel._on_segmented_browse()

    def _on_segmented_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel._on_segmented_import()
