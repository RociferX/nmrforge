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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
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
from gui.center_panel import CenterPanel
from gui.dialogs import (
    ConfirmDialog,
    ImportExperimentDialog,
    InfoDialog,
    ParameterTableDialog,
    SampleDialog,
    ScriptEditorDialog,
)
from gui.log_panel import LogPanel
from gui.pipeline_panel import (
    STEP_LABEL,
    compute_data_step_statuses,
    compute_step_statuses,
)
from gui.processing import ProcessingController
from gui.project_tree import ProjectTreePanel
from gui.spectrum_panel import SpectrumPanel
from gui.workspace import WorkspaceManager
from workflow.import_workflow import ImportResult


class MainWindow(QMainWindow):
    """NMRForge 主窗口;未打开项目时显示欢迎页。"""

    import_failed = pyqtSignal(str)  # 导入失败信息(后台线程 → 主线程)
    import_finished = pyqtSignal(object)  # ImportResult(后台线程 → 主线程)
    batch_import_finished = pyqtSignal(str, str, int, object)  # (exp_id, batch_id, count, results)
    manual_run_log = pyqtSignal(str)  # 人工脚本运行日志(后台线程 → 主线程)
    manual_run_done = pyqtSignal()  # 人工脚本运行完成(主线程刷新 UI)

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
        self.controller.set_manager(self.manager)
        self.workspace = WorkspaceManager()
        self.workspace.ensure()
        self.import_failed.connect(self._on_import_failed)
        self.import_finished.connect(self._on_import_done)
        self.batch_import_finished.connect(self._on_batch_import_done)
        self.manual_run_log.connect(self._append_log)
        self.manual_run_done.connect(self._on_manual_run_done)
        self._pending_data_names: dict[str, str] = {}
        self.setWindowTitle("NMRForge")
        self.resize(1280, 720)
        self.setAcceptDrops(True)  # 拖拽 Bruker 数据目录导入
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
        project_menu.addAction("新建实验...", self._create_experiment)
        project_menu.addAction("重命名实验...", self.rename_experiment)
        project_menu.addAction("删除实验", self.delete_experiment)

        process_menu = bar.addMenu("处理(&R)")
        process_menu.addAction("运行自动化处理", self.run_auto)
        process_menu.addAction("人工参数表格...", self._manual_param_table_menu)
        process_menu.addAction("人工脚本编辑器...", self._manual_script_editor_menu)
        process_menu.addAction("人工 FID 脚本...", self._manual_fid_menu)
        process_menu.addSeparator()
        process_menu.addAction("运行历史...", self._show_run_history)

        sample_menu = bar.addMenu("样本(&S)")
        sample_menu.addAction("添加样本...", self.add_sample)
        sample_menu.addAction("删除样本...", self.delete_sample)

        view_menu = bar.addMenu("查看(&V)")
        view_menu.addAction("谱图查看器", self._show_viewer)
        view_menu.addAction("Task / Log", self._show_log)
        view_menu.addAction("报告...", self._show_report)
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

        settings_menu = bar.addMenu("设置(&T)")
        settings_menu.addAction("软件设置...", self._open_settings)
        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction("关于", self.about)

    def _build_central(self) -> None:
        self.project_tree = ProjectTreePanel(self.manager, workspace=self.workspace)
        self.project_tree.selection_changed.connect(self._update_context)
        self.project_tree.open_requested.connect(self._on_open_experiment)
        self.project_tree.open_project_requested.connect(
            lambda path: self._open_root(Path(path))
        )
        self.project_tree.open_path_requested.connect(self._open_path)
        self.project_tree.open_spectrum_requested.connect(self._open_spectrum_from_tree)
        self.project_tree.rename_requested.connect(self._rename_experiment_by_id)
        self.project_tree.delete_requested.connect(self._delete_experiment_by_id)
        self.project_tree.delete_project_requested.connect(self._delete_project)
        self.project_tree.rename_project_requested.connect(self._rename_project)
        self.project_tree.create_experiment_requested.connect(
            self._create_experiment
        )
        self.project_tree.import_data_requested.connect(self._import_data_for)
        self.project_tree.data_action_requested.connect(self._on_data_action)
        self.project_tree.data_rename_requested.connect(self._rename_data)
        self.project_tree.batch_assign_requested.connect(self._assign_batch)
        self.project_tree.batch_remove_requested.connect(self._remove_batch)

        self.center_panel = CenterPanel(self.manager, self.controller)
        self.pipeline = self.center_panel.pipeline  # 兼容旧引用
        self.center_panel.log_message.connect(self._append_log)
        self.center_panel.manual_open_requested.connect(self._open_manual_dialog)
        self.center_panel.import_data_requested.connect(self._import_data_for)
        self.center_panel.import_options_requested.connect(
            self._import_data_with_options
        )
        self.center_panel.batch_import_requested.connect(self._batch_import)
        self.pipeline.manual_with_params_requested.connect(
            self._open_manual_with_params
        )
        self.pipeline.view_log_requested.connect(self._on_view_step_log)
        self.pipeline.batch_summary_requested.connect(self._on_batch_summary)
        # 首次导入提示:导入完成信号里触发(见 _on_import_done/_on_batch_import_done)
        self.center_panel.create_experiment_requested.connect(
            self._create_experiment_with_title
        )
        self.center_panel.new_project_requested.connect(
            self._new_project_in_workspace
        )
        self.center_panel.open_project_requested.connect(
            lambda path: self._open_root(Path(path))
        )

        self.spectrum_panel = SpectrumPanel(self.manager, controller=self.controller)
        self.spectrum_panel.peaks_saved.connect(self._on_peaks_saved)
        self.spectrum_panel.locate_pipeline_requested.connect(
            self._locate_pipeline
        )

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.project_tree)
        self.main_splitter.addWidget(self.center_panel)
        self.main_splitter.addWidget(self.spectrum_panel)
        self.main_splitter.setStretchFactor(0, 20)
        self.main_splitter.setStretchFactor(1, 40)
        self.main_splitter.setStretchFactor(2, 40)
        self.main_splitter.setSizes([260, 460, 560])

        self.log_panel = LogPanel()
        self.log_panel.setMaximumHeight(160)
        self.log_panel.setVisible(False)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.context_bar = QLabel("未打开项目")
        self.context_bar.setWordWrap(True)
        self.context_bar.setStyleSheet(
            "background: #ecf0f1; padding: 4px 10px; "
            "font-weight: bold; color: #2c3e50;"
        )
        central_layout.addWidget(self.context_bar)
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
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:", text="unnamed")
        if ok and name.strip():
            self._new_project_in_workspace(name.strip())

    def _new_project_in_workspace(self, name: str) -> None:
        """在默认工作区下创建项目(契约 v1.3,create_project 返回 ProjectManager)。"""
        try:
            self.manager = self.workspace.create_project(name)
        except ProjectError as exc:
            InfoDialog.show_info(self, "新建项目失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - WorkspaceError 等统一提示
            InfoDialog.show_info(self, "新建项目失败", f"{type(exc).__name__}: {exc}")
            return
        self.recent.push(str(self.manager.root))
        self._rebind_shared_manager()
        self.center_panel.welcome_page.refresh()
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
        self.center_panel._manager = self.manager
        self.pipeline.manager = self.manager
        self.spectrum_panel.manager = self.manager
        self.controller.set_manager(self.manager)

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
        """兼容入口:等同新建空白实验(导入数据走实验右键「导入数据」)。"""
        self._create_experiment()

    def add_experiment_via_import(self, source: str, title: str = "") -> None:
        """直接按路径导入(供测试与自动化场景使用,不弹对话框)。"""
        self._import_experiment_async(
            {"source": source, "title": title, "sample_id": "", "copy": True}
        )

    def _batch_import(self, exp_id: str, folders: list) -> None:
        """批量导入多个数据目录(后台线程,同批标记同一 batch_id)。"""
        import threading

        if self.manager.project is None or not exp_id or not folders:
            return
        self._append_log(
            f"开始批量导入: {len(folders)} 个目录 → 实验 {exp_id}"
        )

        def worker() -> None:
            try:
                self.controller.set_manager(self.manager)
                result = self.controller.batch_import(exp_id, folders)
                results = list(result.get("results", []))
                ok = [item for item in results if item.get("ok")]
                failed = [item for item in results if not item.get("ok")]
                self.batch_import_finished.emit(
                    exp_id, result["batch_id"], len(ok), results
                )
                if failed:
                    self.import_failed.emit(
                        "批量导入部分失败:\n"
                        + "\n".join(
                            f"{item['folder']}: {item.get('error')}"
                            for item in failed
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程
                self.import_failed.emit(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_batch_import_done(
        self, exp_id: str, batch_id_value: str, count: int, results
    ) -> None:
        """批量导入完成(主线程):逐项日志 + 刷新并选中实验。"""
        for item in results or []:
            if item.get("ok"):
                self._append_log(
                    f"  导入完成: {item['folder']} → {item['data_id']}"
                )
            else:
                self._append_log(
                    f"  导入失败: {item['folder']} ({item.get('error')})"
                )
        self._append_log(
            f"批量导入完成: 实验 {exp_id} 组 {batch_id_value},共 {count} 个数据"
        )
        self.refresh()
        self.project_tree.select_experiment(exp_id)
        self.center_panel.set_selection("experiment", exp_id)
        self._maybe_show_first_import_hint()

    def _assign_batch(self, exp_id: str, data_id: str, batch: str) -> None:
        """把数据加入(已有或新建的)批量组。"""
        from gui.pipeline_state import next_batch_id, set_batch_id

        if self.manager.project is None:
            return
        batch = (batch or "").strip()
        if not batch:
            batch = next_batch_id(self.manager, exp_id)
        set_batch_id(self.manager, exp_id, data_id, batch)
        self._append_log(f"数据 {data_id} 已加入批量组 {batch}")
        self.refresh()

    def _remove_batch(self, exp_id: str, data_id: str) -> None:
        """把数据移出批量组(恢复单一数据)。"""
        from gui.pipeline_state import clear_batch_id

        if self.manager.project is None:
            return
        clear_batch_id(self.manager, exp_id, data_id)
        self._append_log(f"数据 {data_id} 已移出批量组(恢复单一数据)")
        self.refresh()

    def _import_experiment_async(self, data: dict) -> None:
        """后台线程执行导入(三步接口 import_data),避免复制大文件阻塞 UI。"""
        import threading

        source = data.get("source", "")
        exp_id = data.get("experiment_id", "")
        self._append_log(f"开始导入: {source} (实验 {exp_id or '自动创建'})")

        def worker() -> None:
            try:
                from workflow.import_workflow import import_data

                target_exp_id = exp_id
                if not target_exp_id:
                    if self.manager.project is None:
                        raise ProjectError("未加载项目")
                    entry = self.manager.create_experiment(
                        title=data.get("title", "") or "unnamed"
                    )
                    target_exp_id = entry.id
                result = import_data(
                    self.manager,
                    target_exp_id,
                    source,
                    copy=bool(data.get("copy", True)),
                )
                data_id = getattr(result, "data_id", "") or ""
                if data_id:
                    from gui.pipeline_state import record_step_success

                    record_step_success(
                        self.manager,
                        target_exp_id,
                        data_id,
                        "import",
                        params={"copy": bool(data.get("copy", True))},
                    )
                self.manager.save()
                self.import_finished.emit(result)  # 回主线程刷新 UI
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程提示
                self.import_failed.emit(f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_import_failed(self, message: str) -> None:
        """主线程处理导入失败(弹窗 + 日志)。"""
        self._append_log(f"导入失败: {message}")
        InfoDialog.show_info(self, "导入失败", message)

    def _on_import_done(self, result: ImportResult) -> None:
        """导入成功后刷新并选中新实验;展示 warnings。"""
        data_id = getattr(result, "data_id", "") or ""
        self._append_log(
            f"导入完成: {result.experiment_id}/{data_id or '-'} "
            f"({getattr(result, 'file_count', 0)} 文件, "
            f"{getattr(result, 'total_bytes', 0)} 字节, 运行 {result.run_id})"
        )
        for warning in result.warnings:
            self._append_log(f"  提示: {warning}")
        exp_id = getattr(result, "experiment_id", None) or result.get("experiment_id", "")
        data_id = getattr(result, "data_id", "") or ""
        name = self._pending_data_names.pop(exp_id, "") if exp_id else ""
        if exp_id and data_id and name and self.manager.project is not None:
            entry = self.manager.project.experiment(exp_id)
            data_entry = next((d for d in entry.data if d.id == data_id), None) if entry else None
            if data_entry is not None:
                data_entry.title = name
                try:
                    self.manager.save()
                except ProjectError:
                    pass
        self.refresh()
        if exp_id:
            self.project_tree.select_experiment(exp_id)
        self._maybe_show_first_import_hint()
        if result.warnings:
            InfoDialog.show_info(
                self, "导入完成(有提示)", "\n".join(result.warnings)
            )

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
        data_id = getattr(self.pipeline, "_current_data_id", "")
        statuses = (
            compute_data_step_statuses(self.manager, exp_id, data_id)
            if data_id
            else compute_step_statuses(self.manager, exp_id)
        )
        next_step = next(
            (sid for sid, st in statuses.items() if st == "OUTDATED"), None
        )
        if next_step is None:
            next_step = next(
                (sid for sid, st in statuses.items() if st == "READY"), None
            )
        if next_step is None:
            InfoDialog.show_info(self, "提示", "当前没有可运行的步骤")
            return
        self.center_panel.run_step(next_step)

    def _show_run_history(self) -> None:
        """打开运行历史对话框(全部 workflow_runs)。"""
        from gui.dialogs import RunHistoryDialog

        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先打开项目")
            return
        runs = list(self.manager.project.workflow_runs)
        dialog = RunHistoryDialog(
            self, runs, self.manager.project.name,
            project_root=self.manager.root,
        )
        dialog.exec()

    def _manual_param_table_menu(self) -> None:
        self._open_manual_dialog("spectrum")

    def _manual_script_editor_menu(self) -> None:
        self._open_manual_dialog("script")

    def _manual_fid_menu(self) -> None:
        self._open_manual_dialog("fid")

    def _open_manual_with_params(self, step_id: str, params: dict) -> None:
        """用最近运行参数打开人工参数表格(参数预填)。"""
        exp_id = self.project_tree.current_experiment_id()
        if not exp_id or self.manager.project is None:
            return
        entry = self.manager.project.experiment(exp_id)
        if entry is None:
            return
        data_node = self._current_data_node(entry)
        if data_node is None:
            return
        data_id = getattr(data_node, "id", exp_id)
        label = f"{entry.title or entry.id} ({exp_id})"
        schema = self.controller.param_schema()
        defaults = schema.setdefault("default", {})
        if isinstance(defaults, dict):
            for key, value in (params or {}).items():
                if key in defaults:
                    defaults[key] = value
        dialog = ParameterTableDialog(self, label, params=schema)
        dialog.render_requested.connect(
            lambda p: self._render_scripts_and_edit(
                p, data_node, exp_id, data_id, label
            )
        )
        dialog.exec()

    def _on_view_step_log(self, step_id: str) -> None:
        """定位日志面板:追加标记行并展开(append 自动滚底)。"""
        self._append_log(
            f"── {STEP_LABEL.get(step_id, step_id)} 运行日志(最近一次)──"
        )

    def _maybe_show_first_import_hint(self) -> None:
        """首次导入后的「下一步」高亮提示,只出现一次(状态存设置)。"""
        from gui.settings import load_settings, save_settings

        settings = load_settings()
        guide = settings.get("guide") or {}
        if guide.get("first_import_hint_shown"):
            return
        guide["first_import_hint_shown"] = True
        settings["guide"] = guide
        save_settings(settings)
        self.pipeline.show_first_import_hint()

    def _open_settings(self) -> None:
        """打开软件设置对话框(阶段 C3)。"""
        from gui.dialogs import SettingsDialog

        SettingsDialog(self).exec()

    def _on_batch_summary(self, summary: dict) -> None:
        """批量处理汇总弹窗:失败项双击定位到数据(阶段 C1)。"""
        from gui.dialogs import BatchSummaryDialog

        name = self.manager.project.name if self.manager.project else ""
        dialog = BatchSummaryDialog(self, summary, name)
        dialog.locate_requested.connect(self._locate_pipeline_from_batch)
        dialog.exec()

    def _locate_pipeline_from_batch(self, data_id: str) -> None:
        exp_id = self.project_tree.current_experiment_id()
        if exp_id and data_id:
            self._locate_pipeline(exp_id, data_id)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        self._handle_dropped_import_paths(paths)

    def _handle_dropped_import_paths(self, paths: list) -> None:
        """拖拽导入:目录含 acqus 视为 Bruker 数据集,导入当前实验或新建实验。"""
        imported = 0
        for path in paths:
            if not path.is_dir():
                continue
            if not (path / "acqus").is_file():
                InfoDialog.show_info(
                    self,
                    "导入失败",
                    f"不是 Bruker 数据集目录(缺少 acqus):\n{path}",
                )
                continue
            exp_id = self.project_tree.current_experiment_id()
            if exp_id:
                self._pending_data_names[exp_id] = path.name
            self._import_experiment_async(
                {
                    "source": str(path),
                    "title": path.name,
                    "sample_id": "",
                    "copy": True,
                    "experiment_id": exp_id or "",
                }
            )
            imported += 1
        if imported:
            self._append_log(f"拖拽导入: {imported} 个数据目录")

    def _open_manual_dialog(self, step_id: str) -> None:
        """人工处理入口:按步骤打开参数表格/脚本编辑器/fid 编辑器。"""
        exp_id = self.project_tree.current_experiment_id()
        if not exp_id:
            InfoDialog.show_info(self, "提示", "请先在左侧选择一个实验")
            return
        entry = self.manager.project.experiment(exp_id) if self.manager.project else None
        if entry is None:
            return
        label = f"{entry.title or entry.id} ({exp_id})"
        data_node = self._current_data_node(entry)
        if data_node is None:
            InfoDialog.show_info(self, "提示", "该实验还没有数据,请先导入数据")
            return
        data_id = getattr(data_node, "id", exp_id)
        if step_id == "fid":
            self._open_fid_editor(data_node, exp_id, data_id, label)
        elif step_id == "spectrum":
            self._open_param_table(data_node, exp_id, data_id, label)
        elif step_id == "script":
            self._open_script_editor(data_node, exp_id, data_id, label)
        elif step_id == "peaks":
            InfoDialog.show_info(
                self,
                "峰表编辑",
                "峰表添加/删除/编辑请在右侧谱图面板峰表操作,保存后写回 CSV。",
            )
        elif step_id == "analysis":
            InfoDialog.show_info(
                self,
                "分析",
                "分析步骤后端为占位(workflow.analyze);完成后报告页展示 report/ 产物。",
            )
        else:
            InfoDialog.show_info(self, "人工处理", f"暂不支持该步骤的人工入口: {step_id}")

    def _current_data_node(self, entry):
        """当前选中数据节点(未选中时回退首个;空白实验返回 None)。"""
        nodes = list(getattr(entry, "data", None) or [])
        if not nodes:
            return None
        data_id = self.project_tree._data_id_of(self.project_tree.tree.currentItem())
        if data_id:
            return next((n for n in nodes if getattr(n, "id", "") == data_id), nodes[0])
        return nodes[0]

    def _open_fid_editor(self, data_node, exp_id: str, data_id: str, label: str) -> None:
        """fid.com 查看/修改/运行(manual_fid_com / run_manual_fid_com)。"""
        try:
            content = self.controller.manual_fid_com(
                data_node, exp_id=exp_id, data_id=data_id
            )
        except Exception as exc:  # noqa: BLE001 - 后端缺失统一提示
            InfoDialog.show_info(
                self, "fid.com", f"无法获取 fid.com: {type(exc).__name__}: {exc}"
            )
            return
        save_dir = None
        try:
            save_dir = self.manager.data_dir(exp_id, data_id, "raw")
        except Exception:  # noqa: BLE001
            save_dir = None
        dialog = ScriptEditorDialog(
            self, label, script_name="fid.com", content=content, save_dir=save_dir
        )
        self._wire_script_run(dialog, data_node, exp_id, data_id, "fid.com")
        if dialog.exec() == ScriptEditorDialog.DialogCode.Accepted:
            dialog.save_script()

    def _open_param_table(self, data_node, exp_id: str, data_id: str, label: str) -> None:
        """参数表格:param_schema 填充 → 渲染脚本 → 脚本编辑器运行。"""
        schema = self.controller.param_schema()
        dialog = ParameterTableDialog(self, label, params=schema)
        dialog.render_requested.connect(
            lambda params: self._render_scripts_and_edit(
                params, data_node, exp_id, data_id, label
            )
        )
        dialog.exec()

    def _open_script_editor(self, data_node, exp_id: str, data_id: str, label: str) -> None:
        """脚本编辑器:manual_scripts 渲染当前脚本 → 编辑/保存/运行。"""
        try:
            scripts = self.controller.manual_scripts(
                data_node, params=None, exp_id=exp_id, data_id=data_id
            )
        except Exception as exc:  # noqa: BLE001 - 后端缺失统一提示
            InfoDialog.show_info(
                self, "加载脚本失败", f"{type(exc).__name__}: {exc}"
            )
            return
        script_key = next(iter(scripts), "process.com")
        save_dir = None
        try:
            save_dir = self.manager.data_dir(exp_id, data_id, "process")
        except Exception:  # noqa: BLE001
            save_dir = None
        dialog = ScriptEditorDialog(
            self,
            label,
            script_name=script_key,
            content=scripts.get(script_key, ""),
            save_dir=save_dir,
        )
        self._wire_script_run(dialog, data_node, exp_id, data_id, script_key)
        dialog.exec()

    def _render_scripts_and_edit(
        self, params: dict, data_node, exp_id: str, data_id: str, label: str
    ) -> None:
        """按参数渲染谱图脚本并打开脚本编辑器(保存到 process/ 后可运行)。"""
        try:
            scripts = self.controller.manual_scripts(
                data_node, params=params, exp_id=exp_id, data_id=data_id
            )
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(
                self, "渲染失败", f"{type(exc).__name__}: {exc}"
            )
            return
        if not scripts:
            InfoDialog.show_info(self, "渲染失败", "后端未返回脚本")
            return
        script_key = next(iter(scripts), "process.com")
        save_dir = None
        try:
            save_dir = self.manager.data_dir(exp_id, data_id, "process")
        except Exception:  # noqa: BLE001
            save_dir = None
        editor = ScriptEditorDialog(
            self,
            label,
            script_name=script_key,
            content=scripts.get(script_key, ""),
            save_dir=save_dir,
        )
        self._wire_script_run(editor, data_node, exp_id, data_id, script_key)
        if editor.exec() == ScriptEditorDialog.DialogCode.Accepted:
            editor.save_script()
            self._append_log(
                f"已保存脚本: {save_dir / script_key}" if save_dir else f"已保存脚本: {script_key}"
            )

    def _wire_script_run(
        self, dialog, data_node, exp_id: str, data_id: str, script_name: str
    ) -> None:
        """脚本编辑器「运行」→ 后台执行并登记。"""
        dialog.run_requested.connect(
            lambda content: self._run_script_async(
                content, data_node, exp_id, data_id, script_name
            )
        )

    def _run_script_async(
        self, content: str, data_node, exp_id: str, data_id: str, script_name: str
    ) -> None:
        """后台运行人工脚本;完成后主线程刷新 Pipeline 与运行历史。"""
        import threading

        def worker() -> None:
            try:
                if script_name == "fid.com":
                    result = self.controller.run_manual_fid_com(
                        data_node, content, exp_id=exp_id, data_id=data_id
                    )
                    message = f"fid.com 运行完成: {result}"
                else:
                    result = self.controller.run_manual_spectrum(
                        data_node, {script_name: content}, exp_id=exp_id, data_id=data_id
                    )
                    message = f"{script_name} 运行完成: {result}"
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程
                self.manual_run_log.emit(
                    f"人工运行失败: {type(exc).__name__}: {exc}"
                )
                self.manual_run_done.emit()
                return
            self.manual_run_log.emit(message)
            self.manual_run_done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_manual_run_done(self) -> None:
        """人工脚本运行完成后刷新 Pipeline/报告页(主线程)。"""
        self.refresh()
        self.center_panel.refresh()

    def _on_peaks_saved(self) -> None:
        """峰表写回后刷新 Pipeline(peaks 状态)并记录日志。"""
        self._append_log("峰表已保存并登记 manual_peaks 运行")
        self.center_panel.refresh()

    def _show_report(self) -> None:
        """查看菜单:打开报告页(当前选中实验/数据)。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先打开项目")
            return
        self.center_panel.show_report()

    # ------------------------------------------------------------------
    # 树动作(契约 v1.2 §8.5)
    # ------------------------------------------------------------------
    def _rename_project(self) -> None:
        """重命名当前项目:优先 WorkspaceManager.rename_project(目录+name)。"""
        if self.manager.project is None or self.manager.root is None:
            return
        old_name = self.manager.root.name
        new_name, ok = QInputDialog.getText(
            self, "重命名项目", "项目名称:", text=self.manager.project.name
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        try:
            new_root = self.workspace.rename_project(old_name, new_name)
            self.manager = ProjectManager.open_project(new_root)
        except NotImplementedError as exc:
            InfoDialog.show_info(
                self, "重命名项目", f"{exc}\n当前仅更新 project.json 的 name。"
            )
            self.manager.project.name = new_name
            try:
                self.manager.save()
            except ProjectError as exc2:
                InfoDialog.show_info(self, "重命名项目失败", str(exc2))
                return
        except Exception as exc:  # noqa: BLE001 - WorkspaceError 等统一提示
            InfoDialog.show_info(self, "重命名项目失败", f"{type(exc).__name__}: {exc}")
            return
        self._rebind_shared_manager()
        self.refresh()
        self.statusBar().showMessage(f"项目已重命名为 {new_name}")

    def _delete_project(self) -> None:
        if self.manager.project is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "删除项目",
            f"删除项目 {self.manager.project.name} 及其全部数据/产物?"
            "(操作不可恢复,审计历史将保留)\n路径: {self.manager.root}",
        )
        if not confirmed:
            return
        try:
            project_name = self.manager.root.name if self.manager.root is not None else ""
            if project_name:
                self.workspace.delete_project(project_name)
        except NotImplementedError as exc:
            InfoDialog.show_info(
                self, "删除项目", f"{exc}\n当前仅关闭项目,目录保留。"
            )
        except Exception as exc:  # noqa: BLE001 - WorkspaceError 等统一提示
            InfoDialog.show_info(self, "删除项目失败", f"{type(exc).__name__}: {exc}")
            return
        if hasattr(self.recent, "remove") and self.manager.root is not None:
            self.recent.remove(str(self.manager.root))
        self.manager.close()
        self.refresh()
        self.center_panel.welcome_page.refresh()
        self.statusBar().showMessage("项目已关闭")

    def _create_experiment(self) -> None:
        """新建空白实验(Project/空白处右键)。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        title, ok = QInputDialog.getText(self, "新建实验", "实验标题:")
        if not ok:
            return
        create = getattr(self.manager, "create_experiment", None)
        try:
            if create is not None:
                entry = create(title=title.strip())
            else:
                entry = self.manager.add_experiment("", title=title.strip())
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "新建实验失败", str(exc))
            return
        self.refresh()
        self.project_tree.select_experiment(entry.id)

    def _import_data_with_options(
        self, exp_id: str, name: str, source: str, copy: bool
    ) -> None:
        """中间面板内嵌导入表单:按指定实验导入数据(可命名)。"""
        self._pending_data_names[exp_id] = name
        self._import_experiment_async(
            {
                "source": source,
                "title": "",
                "sample_id": "",
                "copy": copy,
                "experiment_id": exp_id,
            }
        )

    def _rename_data(self, exp_id: str, data_id: str) -> None:
        """数据右键重命名:优先 manager.rename_data 落盘;缺失时直接写 title 并提示。"""
        entry = self.manager.project.experiment(exp_id) if self.manager.project else None
        if entry is None:
            return
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        current = getattr(data_entry, "title", "") or "" if data_entry else ""
        new_name, ok = QInputDialog.getText(self, "重命名数据", "数据名称:", text=current)
        if not ok:
            return
        new_name = new_name.strip()
        rename_data = getattr(self.manager, "rename_data", None)
        if rename_data is not None:
            try:
                rename_data(exp_id, data_id, new_name)
            except Exception as exc:  # noqa: BLE001 - 后端异常统一提示
                InfoDialog.show_info(self, "重命名数据失败", str(exc))
                return
        elif data_entry is not None:
            # Backend rename_data 未落地:直接写 DataEntry.title 落盘
            data_entry.title = new_name
        try:
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "重命名数据失败", str(exc))
            return
        self.project_tree.refresh()

    def _delete_data(self, exp_id: str, data_id: str) -> None:
        """删除数据(不删实验);确认 + manager.delete_data + save + refresh。"""
        if self.manager.project is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "删除数据",
            f"删除数据 {data_id} 及其产物文件?\n(WorkflowRun 审计记录将保留)",
        )
        if not confirmed:
            return
        try:
            delete_data = getattr(self.manager, "delete_data", None)
            if delete_data is None:
                raise ProjectError("后端 delete_data 接口待实现")
            delete_data(exp_id, data_id)
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "删除数据失败", str(exc))
            return
        self.refresh()

    def _create_experiment_with_title(self, title: str) -> None:
        """中间面板内嵌表单:新建空白实验。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        try:
            entry = self.manager.create_experiment(title=title)
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "新建实验失败", str(exc))
            return
        self.refresh()
        self.project_tree.select_experiment(entry.id)

    def _import_data_for(self, exp_id: str) -> None:
        """在指定实验下导入数据。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        samples = [(s.sample_id, s.name) for s in self.manager.project.samples]
        dialog = ImportExperimentDialog(self, samples=samples)
        if dialog.exec() != ImportExperimentDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        data["experiment_id"] = exp_id
        self._import_experiment_async(data)

    def _on_data_action(self, action: str, data_id: str) -> None:
        """Data 右键三步操作:生成 FID / 生成谱图 / 删除数据。"""
        exp_id = self.project_tree.current_experiment_id()
        if not exp_id:
            return
        if action == "delete":
            self._delete_data(exp_id, data_id)
            return
        step = "fid" if action == "fid" else "spectrum"
        self.center_panel.set_selection("data", exp_id, data_id)
        self.center_panel.run_step(step, data_id=data_id)

    def _noop_hint(self) -> None:
        InfoDialog.show_info(self, "提示", "项目管理面板已集成在左侧树中")

    # ------------------------------------------------------------------
    # 查看动作
    # ------------------------------------------------------------------
    def _show_viewer(self) -> None:
        from viewer.app import SpectrumWindow

        start_dir = ""
        exp_id = self.project_tree.current_experiment_id()
        if exp_id and self.manager.project is not None:
            try:
                data_id = self.spectrum_panel._current_data_id or ""
                start_dir = str(self.manager.data_dir(exp_id, data_id, "spectra"))
            except Exception:  # noqa: BLE001
                start_dir = ""
        self._viewer_window = SpectrumWindow(start_dir=start_dir)
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
    def _open_spectrum_from_tree(self, path: str) -> None:
        """树中双击谱图文件:右侧谱图面板直接显示并加载峰表。"""
        target = Path(path)
        if self.spectrum_panel.open_spectrum(target):
            self.spectrum_panel._current_spectrum = target
            self.spectrum_panel._load_peaks(target)
            self.statusBar().showMessage(f"已打开: {target.name}")

    def _open_path(self, path: str) -> None:
        """用系统文件管理器打开目录(双击/右键 data/子文件夹),中间保持 Pipeline。"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        target = Path(path)
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        # 保持中间 Pipeline 上下文(当前选中实验时切回数据页)
        exp_id = self.project_tree.current_experiment_id()
        data_id = self.project_tree._data_id_of(self.project_tree.tree.currentItem())
        if exp_id:
            self.center_panel.set_selection("data", exp_id, data_id or "")
        self.statusBar().showMessage(f"已打开: {target}")

    def _on_open_experiment(self, exp_id: str) -> None:
        self.center_panel.set_selection("experiment", exp_id, "")
        self.spectrum_panel.set_context(exp_id, "")
        self.statusBar().showMessage(
            f"实验 {exp_id}: 双击查看谱图文件,中间 Pipeline 显示处理步骤"
        )

    _DATA_STATUS_TEXT = {
        "imported": "已导入",
        "fid_ready": "FID 就绪",
        "processed": "已处理",
        "picked": "已选峰",
        "analyzed": "已分析",
        "registered": "已登记",
    }

    def _update_context_bar(self) -> None:
        """顶部上下文条:Project / Experiment / Data + 状态摘要。"""
        if self.manager.project is None:
            self.context_bar.setText("未打开项目")
            return
        exp_id = self.project_tree.current_experiment_id()
        data_id = self.project_tree._data_id_of(self.project_tree.tree.currentItem())
        parts = [self.manager.project.name]
        if exp_id:
            exp = self.manager.project.experiment(exp_id)
            parts.append(exp.title if exp is not None else exp_id)
        if data_id and exp_id:
            label = data_id
            status_text = ""
            try:
                exp = self.manager.project.experiment(exp_id)
                data = next((d for d in exp.data if d.id == data_id), None)
                if data is not None:
                    label = getattr(data, "title", "") or data_id
                    status_text = self._DATA_STATUS_TEXT.get(
                        getattr(data, "status", ""), getattr(data, "status", "")
                    )
            except Exception:  # noqa: BLE001 - 上下文解析失败保底
                label = data_id
            parts.append(f"{label} · {status_text}" if status_text else label)
        self.context_bar.setText(" / ".join(parts))

    def _locate_pipeline(self, exp_id: str, data_id: str) -> None:
        """谱图面板「在 Pipeline 中定位」:选中树节点并切到处理页。"""
        self.project_tree.select_data(exp_id, data_id)
        self.center_panel.set_selection("data", exp_id, data_id)

    def _update_context(self, kind: str, exp_id: str, data_id: str = "") -> None:
        """左侧选择变化 → 中间按选中类型显示,右侧围绕数据刷新。"""
        self.center_panel.set_selection(kind, exp_id, data_id)
        self.spectrum_panel.set_context(exp_id, data_id)
        self._update_context_bar()

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
            self.setWindowTitle("NMRForge - 欢迎")
            self.statusBar().showMessage("新建或打开项目开始工作")
            self.center_panel.welcome_page.refresh()
            self.center_panel.set_selection("workspace", "", "")
            self.spectrum_panel.set_context("", "")
            self.main_splitter.setVisible(True)  # 欢迎页在三栏中显示
            self._update_context_bar()
            return
        for exp in project.experiments:
            status = self.manager.infer_status(exp.id).value
            item = QTreeWidgetItem([exp.id, exp.title, status, exp.sample_id, exp.source])
            item.setData(0, Qt.ItemDataRole.UserRole, exp.id)
            tree.addTopLevelItem(item)
        self.setWindowTitle(f"NMRForge - {project.name}")
        self.statusBar().showMessage(f"项目: {self.manager.root}")
        # 打开/新建项目后默认聚焦第一个实验
        if project.experiments and not self.center_panel.current_experiment_id():
            self.project_tree.select_experiment(project.experiments[0].id)
        self._update_context_bar()

    @staticmethod
    def run() -> int:
        """启动 Qt 应用(供 main.py 调用)。"""
        import sys

        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        return app.exec()
