"""中间上下文面板:随左侧树选中层级切换。

- Workspace 选中 → 新建项目/打开项目/最近项目(嵌入欢迎页);
- Project 选中 → 新建实验(内嵌表单);
- Experiment 选中 → 导入数据(内嵌表单);
- Data / 子目录选中 → Pipeline 五步(生成 FID → 分析)。

新建/导入表单直接内嵌在中间,不弹独立窗口。
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
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
    manual_open_requested = pyqtSignal(str)
    import_data_requested = pyqtSignal(str)  # exp_id(兼容:打开导入表单)
    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    batch_import_requested = pyqtSignal(str, list)  # (exp_id, folders)
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
        self.pipeline.report_requested.connect(self.show_report)
        self.pipeline.import_data_requested.connect(self.import_data_requested.emit)

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

        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome_page)  # index 0: Workspace
        self.stack.addWidget(self.project_page)  # index 1: Project
        self.stack.addWidget(self.experiment_page)  # index 2: Experiment
        self.stack.addWidget(self.pipeline)  # index 3: Data / folder
        self.report_page = ReportPanel(manager)
        self.stack.addWidget(self.report_page)  # index 4: 报告

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
