"""Dashboard 面板:项目/实验概览(GUI_ARCHITECTURE_VISION §11-12)。

- ProjectDashboard:项目统计(实验/数据/处理完成度)+ 最近运行 + 新建实验表单;
- ExperimentDashboard:数据列表(每数据状态)+ 导入数据表单。

数据来源:core.project(ProjectManager);运行历史来自 workflow_runs。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
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
    """项目概览:统计 + 处理完成度 + 最近运行 + 新建实验。"""

    create_experiment_requested = pyqtSignal(str)  # 实验标题

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
        self.title_edit.setPlaceholderText("实验标题(可留空)")
        form.addWidget(self.title_edit, 1)
        self.create_button = QPushButton("新建实验")
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
            f"实验: {exp_count}  |  数据: {data_count}  |  已处理: {processed}"
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
    """实验概览:数据列表(状态)+ 导入数据表单。"""

    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    batch_import_requested = pyqtSignal(str, list)  # (exp_id, folders)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("实验")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        data_title = QLabel("数据")
        data_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(data_title)
        self.data_table = QTableWidget(0, 3)
        self.data_table.setHorizontalHeaderLabels(["数据", "名称", "状态"])
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.setMaximumHeight(160)
        layout.addWidget(self.data_table)
        layout.addSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("数据名称(可选)")
        layout.addWidget(self.name_edit)
        form = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Bruker 数据集目录(含 acqus)")
        form.addWidget(self.source_edit, 1)
        browse = QPushButton("浏览...")
        browse.clicked.connect(self._browse)
        form.addWidget(browse)
        layout.addLayout(form)

        self.copy_check = QCheckBox("复制数据到项目(raw, SHA-256 指纹)")
        self.copy_check.setChecked(True)
        layout.addWidget(self.copy_check)

        self.import_button = QPushButton("导入数据")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._on_import)
        layout.addWidget(self.import_button)
        self.source_edit.textChanged.connect(
            lambda _t: self.import_button.setEnabled(
                bool(self.source_edit.text().strip())
            )
        )
        layout.addSpacing(10)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        batch_title = QLabel("批量处理")
        batch_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(batch_title)
        batch_hint = QLabel(
            "添加多个 Bruker 数据目录后批量导入;同批数据绑定同一批量组标记,"
            "中间处理页操作对整组数据执行"
        )
        batch_hint.setWordWrap(True)
        batch_hint.setStyleSheet("color: #666;")
        layout.addWidget(batch_hint)
        self.batch_list = QListWidget()
        self.batch_list.setMaximumHeight(110)
        layout.addWidget(self.batch_list)
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
        layout.addLayout(batch_buttons)
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
        """批量列表添加一个数据文件夹(去重)。"""
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据集目录(批量)"
        )
        if path and not self.batch_list.findItems(
            path, Qt.MatchFlag.MatchExactly
        ):
            self.batch_list.addItem(path)
        self.batch_import_button.setEnabled(self.batch_list.count() > 0)

    def _on_batch_clear(self) -> None:
        self.batch_list.clear()
        self.batch_import_button.setEnabled(False)

    def _on_batch_import(self) -> None:
        """把列表中的多个数据目录以同一批量组导入当前实验。"""
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
