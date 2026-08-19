"""Dashboard 面板:项目/实验类型概览(GUI_ARCHITECTURE_VISION §11-12)。

- ProjectDashboard:项目统计(实验类型/样品数据/处理完成度)+ 最近运行 + 新建实验类型表单;
- ExperimentDashboard:样品数据列表(每样品数据状态)+ 导入样品数据表单。

数据来源:core.project(ProjectManager);运行历史来自 workflow_runs。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
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


class ExperimentDashboard(QWidget):
    """实验类型概览:样品数据列表(状态)+ 导入样品数据表单。"""

    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    segmented_import_requested = pyqtSignal(str, str)  # (exp_id, 分段采集容器目录)
    batch_import_requested = pyqtSignal(str, list)  # (exp_id, folders)

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
        layout.addWidget(self.data_table)
        layout.addSpacing(10)

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

    def set_context(self, manager: ProjectManager, exp_id: str, label: str) -> None:
        self.manager = manager
        self._exp_id = exp_id
        self.context_label.setText(f"{label} ({exp_id})" if exp_id else "")
        self.refresh()

    def refresh(self) -> None:
        self.data_table.setRowCount(0)
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        exp = self.manager.project.experiment(self._exp_id)
        if exp is None:
            return
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
        """把列表中的多个数据目录以同一批量组导入当前实验类型。"""
        if not self._exp_id or self.batch_list.count() == 0:
            return
        folders = [
            self.batch_list.item(index).text()
            for index in range(self.batch_list.count())
        ]
        self.batch_import_requested.emit(self._exp_id, folders)

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
