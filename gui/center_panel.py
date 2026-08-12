"""中间上下文面板:随左侧树选中层级切换。

- Workspace 选中 → 新建项目/打开项目/最近项目(嵌入欢迎页);
- Project 选中 → 新建实验(内嵌表单);
- Experiment 选中 → 导入数据(内嵌表单);
- Data / 子目录选中 → Pipeline 五步(生成 FID → 分析)。

新建/导入表单直接内嵌在中间,不弹独立窗口。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.pipeline_panel import PipelinePanel
from gui.welcome_page import WelcomePage


class _ProjectPage(QWidget):
    """项目选中页:内嵌新建实验表单。"""

    create_experiment_requested = pyqtSignal(str)  # 实验标题

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        title = QLabel("项目")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(12)

        form = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("实验标题(可留空)")
        form.addWidget(self.title_edit, 1)
        self.create_button = QPushButton("新建实验")
        self.create_button.clicked.connect(self._on_create)
        form.addWidget(self.create_button)
        layout.addLayout(form)
        layout.addStretch(1)

    def _on_create(self) -> None:
        self.create_experiment_requested.emit(self.title_edit.text().strip())

    def set_context(self, project_name: str) -> None:
        self.context_label.setText(project_name)


class _ExperimentPage(QWidget):
    """实验选中页:内嵌导入数据表单。"""

    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        title = QLabel("实验")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(12)

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
        layout.addStretch(1)

    def set_context(self, exp_id: str, label: str) -> None:
        self._exp_id = exp_id
        self.context_label.setText(f"{label} ({exp_id})" if exp_id else "")

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择 Bruker 数据集目录", self.source_edit.text() or str(Path.home())
        )
        if path:
            self.source_edit.setText(path)

    def _on_import(self) -> None:
        source = self.source_edit.text().strip()
        if not source or not self._exp_id:
            return
        self.import_options_requested.emit(
            self._exp_id, self.name_edit.text().strip(), source, self.copy_check.isChecked()
        )


class CenterPanel(QWidget):
    """中间面板容器:按选中层级切换页面。"""

    log_message = pyqtSignal(str)
    manual_open_requested = pyqtSignal(str)
    import_data_requested = pyqtSignal(str)  # exp_id(兼容:打开导入表单)
    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    create_experiment_requested = pyqtSignal(str)  # 实验标题
    new_project_requested = pyqtSignal(str)  # 项目名称
    open_project_requested = pyqtSignal(str)  # 项目路径

    def __init__(
        self,
        manager=None,
        controller=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager

        self.welcome_page = WelcomePage()
        self.welcome_page.new_project_requested.connect(self.new_project_requested.emit)
        self.welcome_page.open_project_requested.connect(self.open_project_requested.emit)

        self.pipeline = PipelinePanel(manager, controller)
        self.pipeline.log_message.connect(self.log_message.emit)
        self.pipeline.manual_open_requested.connect(self.manual_open_requested.emit)
        self.pipeline.import_data_requested.connect(self.import_data_requested.emit)

        self.project_page = _ProjectPage()
        self.project_page.create_experiment_requested.connect(
            self.create_experiment_requested.emit
        )

        self.experiment_page = _ExperimentPage()
        self.experiment_page.import_options_requested.connect(
            self.import_options_requested.emit
        )

        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome_page)  # index 0: Workspace
        self.stack.addWidget(self.project_page)  # index 1: Project
        self.stack.addWidget(self.experiment_page)  # index 2: Experiment
        self.stack.addWidget(self.pipeline)  # index 3: Data / folder

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

    # ------------------------------------------------------------------
    def set_selection(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """按树选中层级切换中间页面。"""
        if kind == "workspace":
            self.stack.setCurrentIndex(0)
            self.welcome_page.refresh()
        elif kind == "project":
            self.stack.setCurrentIndex(1)
            if self._manager is not None and self._manager.project is not None:
                self.project_page.set_context(self._manager.project.name)
        elif kind == "experiment":
            self.stack.setCurrentIndex(2)
            if self._manager is not None and self._manager.project is not None:
                exp = self._manager.project.experiment(exp_id)
                label = exp.title if exp is not None else exp_id
                self.experiment_page.set_context(exp_id, label)
        else:  # data / folder / 其它:显示 Pipeline
            self.stack.setCurrentIndex(3)
            self.pipeline.set_selection(kind, exp_id, data_id)

    def refresh(self) -> None:
        self.welcome_page.refresh()
        self.pipeline.refresh()

    def run_step(self, step_id: str) -> None:
        self.pipeline.run_step(step_id)

    def current_experiment_id(self) -> str:
        return self.pipeline.current_experiment_id()
