"""主窗口:三栏布局(项目管理树 / Pipeline / 谱图查看器)+ 底部 Task/Log。

布局遵循 docs/GUI_ARCHITECTURE_VISION.md:
- 左侧:ProjectTreePanel(Project → Experiment → Input/Processing/Output/Figures);
- 中间:PipelinePanel(上下文面包屑 + 状态驱动的步骤列表 + 下一步提示);
- 右侧:SpectrumPanel(内嵌 viewer.SpectrumViewer + 项目谱图文件列表);
- 底部:LogPanel(任务日志,运行/失败时自动展开)。

所有项目数据一律经 core.project 访问(GUI 不直接读写 project.json);
所有后端处理一律经 gui/processing.ProcessingController 调用。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMenu,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import (
    JsonRecentProjectsStore,
    ProjectError,
    ProjectManager,
)
from gui.dialogs import (
    ConfirmDialog,
    ImportExperimentDialog,
    InfoDialog,
    SampleDialog,
)
from gui.log_panel import LogPanel
from gui.pipeline_panel import PipelinePanel, compute_step_statuses
from gui.processing import ProcessingController
from gui.project_tree import ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel


class MainWindow(QMainWindow):
    """NMRForge 主窗口;未打开项目时左侧树与中间面板显示空态。"""

    def __init__(
        self,
        manager: ProjectManager | None = None,
        recent: JsonRecentProjectsStore | None = None,
        controller: ProcessingController | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager or ProjectManager()
        self.recent = recent or JsonRecentProjectsStore()
        self.controller = controller or ProcessingController()
        self.setWindowTitle("NMRForge")
        self.resize(1280, 780)
        self._build_menus()
        self._build_central()
        self.refresh()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction("新建项目...", self.new_project)
        file_menu.addAction("打开项目...", self.open_project)
        save_action = QAction("保存项目", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project)
        file_menu.addAction(save_action)
        self.recent_menu = QMenu("最近项目", self)
        file_menu.addMenu(self.recent_menu)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close)

        project_menu = bar.addMenu("项目(&P)")
        project_menu.addAction("项目管理", self._noop_hint)
        project_menu.addAction("添加实验...", self.add_experiment)
        project_menu.addAction("重命名实验...", self.rename_experiment)
        project_menu.addAction("删除实验", self.delete_experiment)

        process_menu = bar.addMenu("处理(&R)")
        process_menu.addAction("运行自动化处理", self.run_auto)
        process_menu.addAction("人工参数表格(待实现)", self._manual_param_hint)
        process_menu.addAction("人工脚本编辑器(待实现)", self._manual_script_hint)

        sample_menu = bar.addMenu("样本(&S)")
        sample_menu.addAction("添加样本...", self.add_sample)
        sample_menu.addAction("删除样本...", self.delete_sample)

        view_menu = bar.addMenu("查看(&V)")
        view_menu.addAction("谱图查看器", self._show_viewer)
        view_menu.addAction("Task / Log", self._show_log)
        view_menu.addSeparator()
        self.view_left_action = QAction("左侧项目管理", self, checkable=True)
        self.view_left_action.setChecked(True)
        self.view_left_action.toggled.connect(self._toggle_left)
        view_menu.addAction(self.view_left_action)
        self.view_pipeline_action = QAction("中间 Pipeline", self, checkable=True)
        self.view_pipeline_action.setChecked(True)
        self.view_pipeline_action.toggled.connect(self._toggle_pipeline)
        view_menu.addAction(self.view_pipeline_action)
        self.view_spectrum_action = QAction("右侧谱图", self, checkable=True)
        self.view_spectrum_action.setChecked(True)
        self.view_spectrum_action.toggled.connect(self._toggle_spectrum)
        view_menu.addAction(self.view_spectrum_action)

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self.about)

    def _build_central(self) -> None:
        self.project_tree = ProjectTreePanel(self.manager)
        self.project_tree.selection_changed.connect(self._update_context)
        self.project_tree.open_requested.connect(self._on_open_experiment)
        self.project_tree.rename_requested.connect(self._rename_experiment_by_id)
        self.project_tree.delete_requested.connect(self._delete_experiment_by_id)

        self.pipeline = PipelinePanel(self.manager, self.controller)
        self.pipeline.log_message.connect(self._append_log)

        self.spectrum_panel = SpectrumPanel(self.manager)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.project_tree)
        self.main_splitter.addWidget(self.pipeline)
        self.main_splitter.addWidget(self.spectrum_panel)
        self.main_splitter.setStretchFactor(0, 22)
        self.main_splitter.setStretchFactor(1, 43)
        self.main_splitter.setStretchFactor(2, 35)
        self.main_splitter.setSizes([280, 540, 420])

        self.log_panel = LogPanel()
        self.log_panel.setVisible(False)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.main_splitter, 1)
        central_layout.addWidget(self.log_panel)
        self.setCentralWidget(central)

        # 兼容旧测试/旧代码:保留扁平实验表(隐藏),仍随 refresh() 同步。
        self.experiment_tree = QTreeWidget()
        self.experiment_tree.setColumnCount(5)
        self.experiment_tree.setHeaderLabels(["ID", "标题", "状态", "样本", "来源"])
        self.experiment_tree.setRootIsDecorated(False)
        self.experiment_tree.hide()
        self.experiment_tree.setParent(central)

    # ------------------------------------------------------------------
    # 项目动作
    # ------------------------------------------------------------------
    def new_project(self) -> None:
        start = str(Path.home())
        root = QFileDialog.getExistingDirectory(self, "选择新项目目录", start)
        if not root:
            return
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:", text="unnamed")
        if not ok:
            return
        try:
            self.manager = ProjectManager.create_project(root, name.strip() or "unnamed")
        except ProjectError as exc:
            InfoDialog.show_info(self, "新建项目失败", str(exc))
            return
        self.recent.push(str(self.manager.root))
        self._rebind_shared_manager()
        self.refresh()

    def open_project(self) -> None:
        root = QFileDialog.getExistingDirectory(self, "选择项目目录", str(Path.home()))
        if not root:
            return
        self._open_root(Path(root))

    def save_project(self) -> None:
        try:
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "保存失败", str(exc))
            return
        self.statusBar().showMessage("项目已保存")

    def _open_root(self, root: Path) -> None:
        try:
            self.manager = ProjectManager.open_project(root)
        except ProjectError as exc:
            InfoDialog.show_info(self, "打开项目失败", str(exc))
            return
        self.recent.push(str(self.manager.root))
        self._rebind_shared_manager()
        self.refresh()

    def _rebind_shared_manager(self) -> None:
        """项目对象更换后,让各面板共享同一个 ProjectManager 实例。"""
        self.project_tree.manager = self.manager
        self.pipeline.manager = self.manager
        self.spectrum_panel.manager = self.manager

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.recent.list():
            action = self.recent_menu.addAction(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self._open_root(Path(p))
            )

    # ------------------------------------------------------------------
    # 实验/样本动作
    # ------------------------------------------------------------------
    def add_experiment(self) -> None:
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        samples = [(s.sample_id, s.name) for s in self.manager.project.samples]
        dialog = ImportExperimentDialog(self, samples=samples)
        if dialog.exec() != ImportExperimentDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        try:
            self.manager.add_experiment(
                data["source"], title=data["title"], sample_id=data["sample_id"]
            )
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "添加实验失败", str(exc))
            return
        self.refresh()

    def rename_experiment(self) -> None:
        exp_id = self.project_tree.current_experiment_id()
        if exp_id:
            self._rename_experiment_by_id(exp_id)

    def _rename_experiment_by_id(self, exp_id: str) -> None:
        entry = self.manager.project.experiment(exp_id) if self.manager.project else None
        if entry is None:
            return
        title, ok = QInputDialog.getText(self, "重命名实验", "新标题:", text=entry.title)
        if ok:
            try:
                self.manager.rename_experiment(exp_id, title)
                self.manager.save()
            except ProjectError as exc:
                InfoDialog.show_info(self, "重命名失败", str(exc))
            self.refresh()

    def delete_experiment(self) -> None:
        exp_id = self.project_tree.current_experiment_id()
        if exp_id:
            self._delete_experiment_by_id(exp_id)

    def _delete_experiment_by_id(self, exp_id: str) -> None:
        if self.manager.project is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "删除实验",
            f"删除实验 {exp_id} 及其产物文件?\n(WorkflowRun 审计记录将保留)",
        )
        if not confirmed:
            return
        try:
            self.manager.delete_experiment(exp_id)
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "删除实验失败", str(exc))
            return
        self.refresh()

    def add_sample(self) -> None:
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        dialog = SampleDialog(self)
        if dialog.exec() != SampleDialog.DialogCode.Accepted:
            return
        try:
            sample = self.manager.add_sample(**dialog.result_data())
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "添加样本失败", str(exc))
            return
        self.statusBar().showMessage(f"已添加样本 {sample.sample_id}")

    def delete_sample(self) -> None:
        if self.manager.project is None:
            return
        sample_ids = [s.sample_id for s in self.manager.project.samples]
        if not sample_ids:
            InfoDialog.show_info(self, "提示", "项目中没有样本")
            return
        sample_id, ok = QInputDialog.getItem(
            self, "删除样本", "选择样本:", sample_ids, editable=False
        )
        if not ok:
            return
        try:
            self.manager.delete_sample(sample_id)
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "删除样本失败", str(exc))
            return
        self.refresh()

    def about(self) -> None:
        InfoDialog.show_info(
            self,
            "关于 NMRForge",
            "NMRForge:面向 Bruker 2D/3D NMR 的自动化处理、参数优化与质量控制平台。\n"
            "三栏布局:项目管理树 / Pipeline / 谱图查看器。",
        )

    # ------------------------------------------------------------------
    # 处理动作
    # ------------------------------------------------------------------
    def run_auto(self) -> None:
        exp_id = self.project_tree.current_experiment_id()
        if not exp_id:
            InfoDialog.show_info(self, "提示", "请先在左侧选择一个实验")
            return
        if self.manager.project is None:
            return
        statuses = compute_step_statuses(self.manager, exp_id)
        next_step = next((sid for sid, st in statuses.items() if st == "READY"), None)
        if next_step is None:
            InfoDialog.show_info(self, "提示", "当前没有可运行的步骤")
            return
        self.pipeline.run_step(next_step)

    def _manual_param_hint(self) -> None:
        InfoDialog.show_info(self, "提示", "人工参数表格编辑器待实现(接口已占位)")

    def _manual_script_hint(self) -> None:
        InfoDialog.show_info(self, "提示", "人工脚本编辑器待实现(接口已占位)")

    def _noop_hint(self) -> None:
        InfoDialog.show_info(self, "提示", "项目管理面板已集成在左侧树中")

    # ------------------------------------------------------------------
    # 查看动作
    # ------------------------------------------------------------------
    def _show_viewer(self) -> None:
        from viewer.app import SpectrumWindow

        self._viewer_window = SpectrumWindow()
        self._viewer_window.show()

    def _show_log(self) -> None:
        self.log_panel.setVisible(True)

    def _toggle_left(self, checked: bool) -> None:
        self.project_tree.setVisible(checked)

    def _toggle_pipeline(self, checked: bool) -> None:
        self.pipeline.setVisible(checked)

    def _toggle_spectrum(self, checked: bool) -> None:
        self.spectrum_panel.setVisible(checked)

    # ------------------------------------------------------------------
    # 上下文联动
    # ------------------------------------------------------------------
    def _on_open_experiment(self, exp_id: str) -> None:
        self.pipeline.set_context(exp_id)
        self.spectrum_panel.set_context(exp_id)
        self.statusBar().showMessage(
            f"实验 {exp_id}: 双击查看谱图文件,中间 Pipeline 显示处理步骤"
        )

    def _update_context(self, exp_id: str) -> None:
        """左侧选择变化 → 中间/右侧围绕所属实验刷新。"""
        self.pipeline.set_context(exp_id)
        self.spectrum_panel.set_context(exp_id)

    def _append_log(self, message: str) -> None:
        self.log_panel.append(message)
        self.log_panel.setVisible(True)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def _current_experiment_id(self) -> str | None:
        exp_id = self.project_tree.current_experiment_id()
        return exp_id or None

    def refresh(self) -> None:
        """刷新窗口标题、最近项目菜单、左侧树与兼容实验表。"""
        self._refresh_recent_menu()
        self.project_tree.refresh()
        tree = self.experiment_tree
        tree.clear()
        project = self.manager.project
        if project is None:
            self.setWindowTitle("NMRForge - 未打开项目")
            self.statusBar().showMessage("新建或打开项目开始工作")
            self.pipeline.set_context("")
            self.spectrum_panel.set_context("")
            return
        for exp in project.experiments:
            status = self.manager.infer_status(exp.id).value
            item = QTreeWidgetItem([exp.id, exp.title, status, exp.sample_id, exp.source])
            item.setData(0, Qt.ItemDataRole.UserRole, exp.id)
            tree.addTopLevelItem(item)
        self.setWindowTitle(f"NMRForge - {project.name}")
        self.statusBar().showMessage(f"项目: {self.manager.root}")
        # 打开/新建项目后默认聚焦第一个实验
        if project.experiments and not self.pipeline.current_experiment_id():
            self.project_tree.select_experiment(project.experiments[0].id)

    @staticmethod
    def run() -> int:
        """启动 Qt 应用(供 main.py 调用)。"""
        import sys

        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        return app.exec()
