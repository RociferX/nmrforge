"""中间上下文面板:随左侧树选中层级切换。

- Workspace 选中 → 新建项目/打开项目/最近项目(嵌入欢迎页);
- 项目 选中 → 新建实验类型(内嵌表单);
- 实验类型 选中 → 导入样品数据(内嵌表单);
- Data / 子目录选中 → Pipeline 处理步骤(生成 FID → 峰挑选;

新建/导入表单直接内嵌在中间,不弹独立窗口。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.dashboards import ExperimentDashboard, ProjectDashboard
from gui.group_panel import GroupBatchPanel
from gui.pipeline_panel import PipelinePanel
from gui.report_panel import ReportPanel
from gui.welcome_page import WelcomePage


class CenterPanel(QWidget):
    """中间面板容器:按选中层级切换页面。"""

    log_message = pyqtSignal(str)
    log_scoped = pyqtSignal(str, str)  # (message, scope):转发 pipeline 作用域日志(0.2.199-补29d)
    memory_guard_requested = pyqtSignal(str)  # 0.2.112:转发 SMILE 内存不足
    manual_open_requested = pyqtSignal(str)
    import_data_requested = pyqtSignal(str)  # exp_id(兼容:打开导入表单)
    import_options_requested = pyqtSignal(str, str, str, bool)  # (exp_id, name, source, copy)
    data_rename_requested = pyqtSignal(str, str, str)  # (exp_id, data_id, new_name)
    batch_import_requested = pyqtSignal(str, list, bool)  # (exp_id, folders, group)
    segmented_import_requested = pyqtSignal(str, str)  # (exp_id, 分段采集容器目录)
    create_experiment_requested = pyqtSignal(str)  # 实验类型标题
    edit_notes_requested = pyqtSignal(str, str, str)  # (kind, exp_id, data_id)
    group_run_requested = pyqtSignal(str, str, list, str, dict)
    # (exp_id, group_id, steps, reference_data_id)
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
        self.pipeline.log_scoped.connect(self.log_scoped.emit)
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
        self.experiment_page.data_rename_requested.connect(
            self.data_rename_requested.emit
        )
        self.experiment_page.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self.experiment_page.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        self.experiment_page.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )

        self.group_page = GroupBatchPanel()
        self.group_page.log_message.connect(self.log_message.emit)
        self.group_page.run_group_batch_requested.connect(
            self.group_run_requested.emit
        )
        self.group_page.summary_requested.connect(self._on_group_summary)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome_page)  # index 0: Workspace
        self.stack.addWidget(self.project_page)  # index 1: Project
        self.stack.addWidget(self.experiment_page)  # index 2: Experiment
        self.stack.addWidget(self.pipeline)  # index 3: Data / folder
        self.report_page = ReportPanel(manager)
        self.stack.addWidget(self.report_page)  # index 4: 报告
        self.stack.addWidget(self.group_page)  # index 5: 数据组

        # 顶部注释条:项目/实验类型/样品数据三级注释展示 + 后补编辑入口
        self.notes_header = QHBoxLayout()
        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        self.notes_label.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #3c3c3c; "
            "color: #ffffff; padding: 4px 8px;"
        )
        self.notes_header.addWidget(self.notes_label, 1)
        # 0.2.199-补29gp:数据组注释按每数据一列展示,带横向/纵向滚动条防过长过宽
        self.group_notes_scroll = QScrollArea()
        self.group_notes_scroll.setWidgetResizable(True)
        self.group_notes_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.group_notes_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.group_notes_scroll.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #3c3c3c; color: #ffffff;"
        )
        self.group_notes_scroll.setVisible(False)
        self.notes_header.addWidget(self.group_notes_scroll, 1)
        self.edit_notes_button = QPushButton("编辑注释")
        self.edit_notes_button.setToolTip("添加/修改当前项目、实验类型或样品数据的注释信息")
        self.edit_notes_button.clicked.connect(self._on_edit_notes)
        self.notes_header.addWidget(self.edit_notes_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.notes_header)
        layout.addWidget(self.stack)

    # ------------------------------------------------------------------
    def set_selection(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """按树选中层级切换中间页面,并刷新顶部注释条。"""
        self._update_notes(kind, exp_id, data_id, group_id)
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
        elif kind == "group":
            self.stack.setCurrentIndex(5)
            self.group_page.set_context(self._manager, exp_id, group_id)
        else:  # data / folder / 其它:显示 Pipeline
            self.stack.setCurrentIndex(3)
            self.pipeline.set_selection(kind, exp_id, data_id)

    # ------------------------------------------------------------------
    def _update_notes(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """按选中层级显示项目/实验类型/样品数据/数据组注释(中间区域最上方)。"""
        from gui.notes import data_note, experiment_note, sample_note

        self._notes_kind = kind if kind in (
            "project", "experiment", "data", "folder", "group"
        ) else ""
        self._notes_exp_id = exp_id
        self._notes_data_id = data_id if kind in ("data", "folder") else ""
        self._notes_group_id = group_id if kind == "group" else ""
        show = bool(self._notes_kind)
        # 组注释用每数据一列的滚动区;其它层级用单标签,编辑按钮仅非组可用
        # 0.2.199-补29gw:组注释改由数据组页面展示(置于"按参考数据处理"上方),
        # 不再占用顶部注释条。
        self.notes_label.setVisible(show and kind != "group")
        self.group_notes_scroll.setVisible(False)
        self.edit_notes_button.setVisible(show and kind != "group")
        if not show or kind == "group":
            return
        project = self._manager.project if self._manager is not None else None
        if project is None:
            return
        text = ""
        if kind == "project":
            text = sample_note(project)
        elif kind == "experiment":
            text = experiment_note(project, exp_id)
        elif kind in ("data", "folder"):
            text = data_note(project, exp_id, data_id)
        self.notes_label.setText(f"注释:\n{text}" if text else "注释: (未填写)")

    def _set_group_notes(self, project, exp_id: str, group_id: str) -> None:
        """数据组注释:注记字段为行(第一列字段名),每个数据为一列。"""
        from gui.notes import DATA_FIELDS, data_note_fields

        exp = project.experiment(exp_id) if project is not None else None
        group = None
        if exp is not None:
            group = next(
                (g for g in (getattr(exp, "groups", None) or []) if g.id == group_id),
                None,
            )
        member_ids = (group.data_ids or []) if group is not None else []
        cols_data: list[tuple[str, str, dict]] = []
        for data_id in member_ids:
            d = (
                next((x for x in exp.data if x.id == data_id), None)
                if exp is not None
                else None
            )
            label_text = (d.title or f"Data {data_id}") if d else f"Data {data_id}"
            cols_data.append(
                (
                    data_id,
                    label_text,
                    data_note_fields(project, exp_id, data_id),
                )
            )
        # 字段行:先按标准 DATA_FIELDS 顺序,再补各数据里出现的额外字段
        row_keys: list[str] = [k for k, _ in DATA_FIELDS]
        display = {k: v for k, v in DATA_FIELDS}
        for _di, _lb, fields in cols_data:
            for k in fields:
                if k not in row_keys:
                    row_keys.append(k)
                    display[k] = k

        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setSpacing(2)
        # 表头行:第一格"数据",之后每数据一列
        head0 = QLabel("数据")
        head0.setStyleSheet(
            "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
        )
        grid.addWidget(head0, 0, 0)
        for ci, (data_id, label_text, _f) in enumerate(cols_data, start=1):
            head = QLabel(f"◆ {label_text}\n({data_id})")
            head.setWordWrap(True)
            head.setStyleSheet(
                "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
            )
            grid.addWidget(head, 0, ci)
        # 字段行
        for ri, key in enumerate(row_keys, start=1):
            fname = QLabel(display.get(key, key))
            fname.setStyleSheet(
                "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
            )
            grid.addWidget(fname, ri, 0)
            for ci, (_di, _lb, fields) in enumerate(cols_data, start=1):
                val = fields.get(key)
                val_lb = QLabel(str(val) if val not in (None, "") else "—")
                val_lb.setWordWrap(True)
                val_lb.setFixedWidth(190)
                val_lb.setStyleSheet(
                    "color: #ffffff; border: none; background: transparent;"
                )
                grid.addWidget(val_lb, ri, ci)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(len(cols_data) + 1, 0)
        self.group_notes_scroll.setWidget(container)

    def _on_group_summary(self, summary: dict) -> None:
        """组批量处理完成汇总:日志输出 + 面板清进度。"""
        info = str(summary.get("info", ""))
        if info:
            self.log_message.emit(info)
        for item in summary.get("items") or []:
            data_id = item.get("data_id", "")
            if item.get("skipped"):
                self.log_message.emit(
                    f"  跳过 {data_id}: {item.get('error', '')}"
                )
            elif item.get("failed"):
                self.log_message.emit(
                    f"  失败 {data_id}: {item.get('error', '')}"
                )
            elif item.get("cancelled"):
                self.log_message.emit(
                    f"  已取消 {data_id}: {item.get('error', '')}"
                )
            elif item.get("ok"):
                self.log_message.emit(
                    f"  完成 {data_id}: {item.get('message', '')}"
                )
            else:
                self.log_message.emit(
                    f"  失败 {data_id}: {item.get('error', '')}"
                )
        self.group_page.set_progress("")

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
        if self._exp_group_context():
            self.group_page._refresh()
        self.report_page.manager = self._manager
        self.report_page.refresh()

    def _exp_group_context(self) -> bool:
        """当前是否停留在数据组页面(供 refresh 刷新)。"""
        return bool(
            self.stack.currentWidget() is self.group_page
            and getattr(self.group_page, "_group_id", "")
        )

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
