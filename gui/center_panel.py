"""中间上下文面板:随左侧树选中层级切换。

- Workspace 选中 → 新建项目/打开项目/最近项目(嵌入欢迎页);
- 项目 选中 → 新建实验类型(内嵌表单);
- 实验类型 选中 → 导入样品数据(内嵌表单);
- Data / 子目录选中 → Pipeline 五步(生成 FID → 分析)。

新建/导入表单直接内嵌在中间,不弹独立窗口。
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.dashboards import ExperimentDashboard, ProjectDashboard
from gui.pipeline_panel import PipelinePanel
from gui.report_panel import ReportPanel
from gui.welcome_page import WelcomePage


class CenterPanel(QWidget):
    """中间面板容器:按选中层级切换页面。"""

    log_message = pyqtSignal(str)
    memory_guard_requested = pyqtSignal(str)  # 0.2.112:转发 SMILE 内存不足
    manual_open_requested = pyqtSignal(str)
    import_data_requested = pyqtSignal(str)  # exp_id(兼容:打开导入表单)
    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    batch_import_requested = pyqtSignal(str, list, bool)  # (exp_id, folders, group)
    segmented_import_requested = pyqtSignal(str, str)  # (exp_id, 分段采集容器目录)
    create_experiment_requested = pyqtSignal(str)  # 实验类型标题
    edit_notes_requested = pyqtSignal(str, str, str)  # (kind, exp_id, data_id)
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
        self.pipeline.memory_guard_requested.connect(
            self.memory_guard_requested.emit
        )
        self.pipeline.manual_open_requested.connect(self.manual_open_requested.emit)
        self.pipeline.report_requested.connect(self.show_report)

        self.project_page = ProjectDashboard()
        self.project_page.create_experiment_requested.connect(
            self.create_experiment_requested.emit
        )

        self.experiment_page = ExperimentDashboard()
        self.experiment_page.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self.experiment_page.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        self.experiment_page.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )

        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome_page)  # index 0: Workspace
        self.stack.addWidget(self.project_page)  # index 1: Project
        self.stack.addWidget(self.experiment_page)  # index 2: Experiment
        self.stack.addWidget(self.pipeline)  # index 3: Data / folder
        self.report_page = ReportPanel(manager)
        self.stack.addWidget(self.report_page)  # index 4: 报告

        # 顶部注释条:项目/实验类型/样品数据三级注释展示 + 后补编辑入口
        self.notes_header = QHBoxLayout()
        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        self.notes_label.setStyleSheet(
            "background: #f0f4f8; border: 1px solid #d5d8dc; "
            "color: #333; padding: 4px 8px;"
        )
        self.notes_header.addWidget(self.notes_label, 1)
        self.edit_notes_button = QPushButton("编辑注释")
        self.edit_notes_button.setToolTip("添加/修改当前项目、实验类型或样品数据的注释信息")
        self.edit_notes_button.clicked.connect(self._on_edit_notes)
        self.notes_header.addWidget(self.edit_notes_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.notes_header)
        layout.addWidget(self.stack)

    # ------------------------------------------------------------------
    def set_selection(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """按树选中层级切换中间页面,并刷新顶部注释条。"""
        self._update_notes(kind, exp_id, data_id)
        if kind == "workspace":
            self.stack.setCurrentIndex(0)
            self.welcome_page.refresh()
        elif kind == "project":
            self.stack.setCurrentIndex(1)
            if self._manager is not None and self._manager.project is not None:
                self.project_page.set_context(self._manager)
        elif kind == "experiment":
            self.stack.setCurrentIndex(2)
            if self._manager is not None and self._manager.project is not None:
                exp = self._manager.project.experiment(exp_id)
                label = exp.title if exp is not None else exp_id
                self.experiment_page.set_context(self._manager, exp_id, label)
        else:  # data / folder / 其它:显示 Pipeline
            self.stack.setCurrentIndex(3)
            self.pipeline.set_selection(kind, exp_id, data_id)

    # ------------------------------------------------------------------
    def _update_notes(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """按选中层级显示项目/实验类型/样品数据注释(中间区域最上方)。"""
        from gui.notes import data_note, experiment_note, sample_note

        self._notes_kind = kind if kind in ("project", "experiment", "data", "folder") else ""
        self._notes_exp_id = exp_id
        self._notes_data_id = data_id if kind in ("data", "folder") else ""
        show = bool(self._notes_kind)
        self.notes_label.setVisible(show)
        self.edit_notes_button.setVisible(show)
        if not show:
            return
        project = self._manager.project if self._manager is not None else None
        text = ""
        if project is not None:
            if kind == "project":
                text = sample_note(project)
            elif kind == "experiment":
                text = experiment_note(project, exp_id)
            elif kind in ("data", "folder"):
                text = data_note(project, exp_id, data_id)
        self.notes_label.setText(f"注释:\n{text}" if text else "注释: (未填写)")

    def _on_edit_notes(self) -> None:
        """点击「编辑注释」:发出编辑请求(主窗口打开注释对话框)。"""
        self.edit_notes_requested.emit(
            self._notes_kind, self._notes_exp_id, self._notes_data_id
        )

    def refresh(self) -> None:
        self.welcome_page.refresh()
        self.pipeline.refresh()
        self.project_page.refresh()
        self.experiment_page.refresh()
        self.report_page.manager = self._manager
        self.report_page.refresh()

    def show_report(self, _step_id: str = "", exp_id: str = "", data_id: str = "") -> None:
        """打开报告页(分析产物存在时);缺省用当前选中实验/数据。"""
        exp_id = exp_id or self.pipeline.current_experiment_id()
        data_id = data_id or getattr(self.pipeline, "_current_data_id", "")
        self.report_page.manager = self._manager
        self.report_page.set_context(exp_id, data_id)
        self.stack.setCurrentIndex(4)

    def run_step(self, step_id: str, data_id: str | None = None) -> None:
        self.pipeline.run_step(step_id, data_id=data_id)

    def current_experiment_id(self) -> str:
        return self.pipeline.current_experiment_id()
