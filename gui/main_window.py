"""主窗口:三栏布局(项目管理树 / Pipeline / 谱图查看器)+ 底部 Task/Log。

布局遵循 docs/GUI_ARCHITECTURE_VISION.md:
- 左侧:ProjectTreePanel(Project → Experiment → Input/Processing/Output/Figures);
- 中间:PipelinePanel(上下文面包屑 + 状态驱动的步骤列表 + 下一步提示);
- 右侧:SpectrumPanel(内嵌 viewer.SpectrumViewer + 项目谱图文件列表);
- 中间竖列:LogPanel(任务日志,位于 pipeline 与谱图查看器之间,自动展开);
- 窗口默认贴屏幕顶、高度用满可用区(不遮任务栏)。

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
from core.workspace import WorkspaceManager
from gui.center_panel import CenterPanel
from gui.dialogs import (
    ConfirmDialog,
    ImportExperimentDialog,
    InfoDialog,
    NotesDialog,
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
from workflow.import_workflow import ImportResult


def _pick_script_key(scripts: dict[str, str], data_id: str) -> str:
    """选择脚本编辑器的默认脚本:优先数据同名主脚本,回退常用名/首个。"""
    for key in (
        f"{data_id}_nus.com",
        f"{data_id}_process.com",
        "nus.com",
        "process.com",
        f"{data_id}_finalize.com",
    ):
        if key in scripts:
            return key
    return next(iter(scripts), "process.com")


class MainWindow(QMainWindow):
    """NMRForge 主窗口;未打开项目时显示欢迎页。"""

    import_failed = pyqtSignal(str)  # 导入失败信息(后台线程 → 主线程)
    import_finished = pyqtSignal(object)  # ImportResult(后台线程 → 主线程)
    batch_import_finished = pyqtSignal(str, str, int, object)  # (exp_id, batch_id, count, results)
    # 0.2.199-补29hd:批量导入逐条进度(后台线程 → 主线程逐条显示,不等批量完成)
    batch_import_progress = pyqtSignal(str, str, str, bool, str)  # 逐条进度
    # 0.2.199-补29hd:数据组批量完成后逐数据状态(成功/失败/跳过)写回左侧树
    group_data_done = pyqtSignal(str, str, str)  # (exp_id, data_id, status)
    manual_run_log = pyqtSignal(str)  # 人工脚本运行日志(后台线程 → 主线程)
    manual_run_done = pyqtSignal()  # 人工脚本运行完成(主线程刷新 UI)
    batch_run_done = pyqtSignal()  # 数据组批量处理完成(后台线程 → 主线程清运行标记)
    log_append_requested = pyqtSignal(str, object)  # 工作线程日志经队列信号(0.2.199-补29c)

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
        self.batch_import_progress.connect(self._on_batch_import_progress)
        self.group_data_done.connect(self._on_group_data_done)
        self.manual_run_log.connect(self._append_log)
        self.manual_run_done.connect(self._on_manual_run_done)
        self.batch_run_done.connect(self._on_batch_run_done)
        # 0.2.199-补29c:工作线程进度日志经队列信号;旧代码在 worker 里直接
        # 调 _append_log → LogPanel.append → QTextEdit(光标闪烁计时器)触发
        # QBasicTimer::start 错误并卡死
        self.log_append_requested.connect(self._append_log)
        self._pending_data_names: dict[str, str] = {}
        # 非模态脚本编辑器持有引用(0.2.192);0.2.193 起按 (data_id, step)
        # 去重——同数据同步骤只允许一个编辑器
        self._script_editors: dict[tuple[str, str], ScriptEditorDialog] = {}
        self._last_auto_fill: dict = {}
        self._last_raw_quality: dict | None = None
        # 日志作用域:当前选中上下文(由 _update_context 维护)
        self._log_kind = ""
        self._log_exp_id = ""
        self._log_data_id = ""
        self._log_group_id = ""
        self.setWindowTitle("NMRForge")
        # 0.2.199-补29eq:应用图标(窗口/任务栏)
        try:
            from gui.theme import app_icon

            _icon = app_icon()
            if _icon is not None:
                self.setWindowIcon(_icon)
        except Exception:  # noqa: BLE001 - 图标缺失不阻断启动
            pass
        self.setAcceptDrops(True)  # 拖拽 Bruker 数据目录导入
        self._build_menus()
        self._build_central()
        # 0.2.143:默认几何依赖 main_splitter 列宽,在中央构建后应用
        self._apply_default_geometry()
        self.refresh()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _apply_default_geometry(self) -> None:
        """打开默认几何:顶部贴屏幕上沿、高度用满可用区(不遮任务栏)。

        默认列宽固定 [420, 600, 300, 600] 合计 1920,适配 1080p 满宽;
        屏幕比 1920 窄时窗口收窄到可用宽,列由 QSplitter 自动分配
        (用户仍可自由拖拽每列长宽)。
        """
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QGuiApplication

        cols = [420, 600, 300, 600]
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.main_splitter.setSizes(cols)
            self.resize(1920, 1080)
            return
        avail = screen.availableGeometry()
        self.main_splitter.setSizes(cols)
        self.setGeometry(
            QRect(avail.left(), avail.top(), min(1920, avail.width()), avail.height())
        )

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

        experiment_menu = bar.addMenu("实验(&E)")
        experiment_menu.addAction("新建实验...", self._create_experiment)
        experiment_menu.addAction("重命名实验...", self.rename_experiment)
        experiment_menu.addAction("删除实验", self.delete_experiment)

        view_menu = bar.addMenu("查看(&V)")
        # 0.2.199-补29br:隐藏独立谱图查看器入口(文件/帮助已移到谱图面板)
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

        tools_menu = bar.addMenu("工具(&T)")
        tools_menu.addAction(
            "数据质量检测...",
            self._run_standalone_fid_diagnostics,
        )
        tools_menu.addAction(
            "谱图质量评估...",
            self._run_standalone_spectrum_quality,
        )

        settings_menu = bar.addMenu("设置(&S)")
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
        self.project_tree.open_terminal_requested.connect(self._open_terminal)
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
        self.project_tree.project_create_submitted.connect(
            self._on_project_create_submitted
        )
        self.project_tree.experiment_create_submitted.connect(
            self._on_experiment_create_submitted
        )
        self.project_tree.group_add_data_requested.connect(self._group_add_data)
        self.project_tree.group_remove_data_requested.connect(
            self._group_remove_data
        )
        self.project_tree.group_rename_requested.connect(self._group_rename)
        self.project_tree.group_delete_requested.connect(self._group_delete)
        self.project_tree.group_delete_with_members_requested.connect(
            self._group_delete_with_members
        )

        self.center_panel = CenterPanel(self.manager, self.controller)
        self.pipeline = self.center_panel.pipeline  # 兼容旧引用
        self.center_panel.log_message.connect(self._append_log)
        # 0.2.199-补29d:作用域日志(按数据/组)直接落对应缓冲,不随选中切换
        self.center_panel.log_scoped.connect(self._append_log)
        self.center_panel.manual_open_requested.connect(self._open_manual_dialog)
        self.center_panel.import_data_requested.connect(self._import_data_for)
        self.center_panel.import_options_requested.connect(
            self._import_data_with_options
        )
        self.center_panel.data_rename_requested.connect(self._rename_data)
        self.center_panel.batch_import_requested.connect(self._batch_import)
        self.center_panel.segmented_import_requested.connect(self._segmented_import)
        self.pipeline.view_log_requested.connect(self._on_view_step_log)
        self.pipeline.batch_summary_requested.connect(self._on_batch_summary)
        self.center_panel.memory_guard_requested.connect(self._on_memory_guard)
        # 首次导入提示:导入完成信号里触发(见 _on_import_done/_on_batch_import_done)
        self.center_panel.create_experiment_requested.connect(
            self._create_experiment_with_title
        )
        self.center_panel.new_project_requested.connect(
            self._on_project_create_submitted
        )
        self.center_panel.open_project_requested.connect(
            lambda path: self._open_root(Path(path))
        )
        self.center_panel.edit_notes_requested.connect(self._edit_notes)
        self.center_panel.group_run_requested.connect(self._run_group_batch)
        self.pipeline.run_finished.connect(self._on_pipeline_run_finished)
        self.pipeline.run_started.connect(self._on_pipeline_run_started)
        self.pipeline.show_spectrum_requested.connect(
            self._show_spectrum_from_pipeline
        )

        self.spectrum_panel = SpectrumPanel(self.manager, controller=self.controller)
        self.spectrum_panel.peaks_saved.connect(self._on_peaks_saved)
        # 0.2.199-补29cz:峰表可信度匹配等任务日志进 LogPanel
        self.spectrum_panel.log_message.connect(self._append_log)
        self.spectrum_panel.status_message.connect(
            self.statusBar().showMessage
        )
        # 0.2.199-补29bp:谱图放大/收起(收起左侧三部分)
        self.spectrum_panel.expand_requested.connect(
            self._toggle_spectrum_expand
        )

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.project_tree)
        self.main_splitter.addWidget(self.center_panel)
        self.log_panel = LogPanel()
        self.log_panel.set_manager(self.manager)
        self.log_panel.stop_requested.connect(self._on_stop_requested)
        # 0.2.143:log 界面常驻显示(不再默认隐藏),宽度不限可拖拽
        self.log_panel.setVisible(True)
        self.main_splitter.addWidget(self.log_panel)
        self.main_splitter.addWidget(self.spectrum_panel)
        self.main_splitter.setStretchFactor(0, 20)
        self.main_splitter.setStretchFactor(1, 40)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setStretchFactor(3, 40)
        # 0.2.143:不再设列宽硬限制,四列可自由拖拽调整;
        # 默认初始宽度见 _apply_default_geometry(基础列宽 x1.4)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.context_bar = QLabel("未打开项目")
        self.context_bar.setWordWrap(True)
        self.context_bar.setStyleSheet(
            "background: #1e1e1e; padding: 4px 10px; "
            "font-weight: bold; color: #ffffff;"
        )
        # 0.2.141:日志为中间竖列(水平分隔条内),不再占用底部高度
        central_layout.addWidget(self.context_bar)
        central_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(central)

        # 兼容旧测试/旧代码:保留扁平实验表(隐藏),仍随 refresh() 同步。
        self.experiment_tree = QTreeWidget()
        self.experiment_tree.setColumnCount(5)
        self.experiment_tree.setHeaderLabels(["ID", "标题", "状态", "项目", "来源"])
        self.experiment_tree.setRootIsDecorated(False)
        self.experiment_tree.hide()
        self.experiment_tree.setParent(central)

    # ------------------------------------------------------------------
    # 项目动作
    # ------------------------------------------------------------------
    def new_project(self) -> None:
        """新建项目:内联命名(不弹窗)。未打开项目时在欢迎页输入;已打开时在项目树输入。"""
        if self.manager.project is None:
            self.center_panel.welcome_page.begin_inline_name()
        else:
            self.project_tree.begin_create_project()

    def _on_project_create_submitted(self, name: str) -> None:
        """内联命名提交(欢迎页/项目树):直接创建工作区项目(0.2.199-补29dw
        不再弹常规信息表单,注释留空、创建后可通过「编辑注释」补充)。"""
        self._new_project_in_workspace(name.strip())

    def _new_project_in_workspace(
        self, name: str, fields: dict | None = None
    ) -> None:
        """在默认工作区下创建项目(契约 v1.3,create_project 返回 ProjectManager)。"""
        try:
            self.manager = self.workspace.create_project(name)
        except ProjectError as exc:
            InfoDialog.show_info(self, "新建项目失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - WorkspaceError 等统一提示
            InfoDialog.show_info(self, "新建项目失败", f"{type(exc).__name__}: {exc}")
            return
        if self.manager.project is not None:
            from gui.notes import set_sample_note_fields

            set_sample_note_fields(self.manager.project, fields or {})
            try:
                self.manager.save()
            except ProjectError:
                pass
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
        self.log_panel.set_manager(self.manager)
        self.controller.set_manager(self.manager)

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.recent.list():
            action = self.recent_menu.addAction(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self._open_root(Path(p))
            )

    # ------------------------------------------------------------------
    # 实验/项目动作
    # ------------------------------------------------------------------
    def add_experiment(self) -> None:
        """兼容入口:等同新建空白实验(导入样品数据走实验右键「导入样品数据」)。"""
        self._create_experiment()

    def add_experiment_via_import(self, source: str, title: str = "") -> None:
        """直接按路径导入(供测试与自动化场景使用,不弹对话框)。"""
        self._import_experiment_async(
            {"source": source, "title": title, "sample_id": "", "copy": True}
        )

    def _segmented_import(self, exp_id: str, source: str) -> None:
        """分段采集导入:容器目录(≥2 个含 acqus 的子目录)合并为一条样品数据。

        exp_id 为当前实验;为空时后端新建实验(G2B-011)。
        """
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        source = source.strip()
        if not source:
            InfoDialog.show_info(self, "提示", "请选择分段/重复实验容器目录")
            return
        from gui.processing import is_segmented_container

        if not is_segmented_container(source):
            InfoDialog.show_info(
                self,
                "分段/重复实验叠加导入",
                "所选目录不是分段/重复实验容器(需至少 2 个子目录各含 acqus "
                "数据段;非数据子目录已忽略;容器顶层本身不含 acqus 属正常)",
            )
            return
        self._import_experiment_async(
            {
                "source": source,
                "title": Path(source).name,
                "sample_id": "",
                "copy": True,
                "segmented": True,
                "experiment_id": exp_id,
            }
        )

    def _batch_import(self, exp_id: str, folders: list, group: bool = True) -> None:
        """批量导入多个数据目录(后台线程);group=True 同批标记同一 batch_id。"""
        import threading

        if self.manager.project is None or not exp_id or not folders:
            return
        self._append_log(
            f"开始批量导入: {len(folders)} 个目录 → 实验 {exp_id}"
        )

        def worker() -> None:
            try:
                self.controller.set_manager(self.manager)
                result = self.controller.batch_import(
                    exp_id, folders, group=group,
                    on_progress=lambda folder, data_id, ok, error: self.batch_import_progress.emit(
                        exp_id, folder, data_id, ok, error
                    ),
                )
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

    def _on_group_data_done(self, exp_id: str, data_id: str, status: str) -> None:
        """组批量某数据完成:左侧树写回状态(成功/失败/跳过)。"""
        self.project_tree.set_batch_status(exp_id, data_id, status)


    def _on_batch_import_progress(
        self, exp_id: str, folder: str, data_id: str, ok: bool, error: str
    ) -> None:
        """批量导入逐条进度(主线程):立即记录当前条,不等批量全部完成。"""
        if ok:
            self._append_log(f"  导入中: {folder} → {data_id}")
        else:
            self._append_log(f"  跳过/失败: {folder} - {error}")


    def _on_batch_import_done(
        self, exp_id: str, batch_id_value: str, count: int, results
    ) -> None:
        """批量导入完成(主线程):逐项日志 + 刷新并选中实验。"""
        for item in results or []:
            if item.get("ok"):
                self._append_log(
                    f"  导入完成: {item['folder']} → {item['data_id']}"
                )
                try:
                    from gui.notes import auto_fill_notes_from_metadata
                    from gui.raw_quality import (
                        check_raw_quality,
                        format_quality_report,
                    )

                    data_id = item.get("data_id", "")
                    meta = self._read_data_metadata(exp_id, data_id)
                    filled = auto_fill_notes_from_metadata(
                        self.manager, exp_id, data_id, meta
                    )
                    if filled:
                        summary = "、".join(
                            f"{key}={value}" for key, value in filled.items()
                        )
                        self._append_log(f"  自动填充注释: {summary}")
                    quality = check_raw_quality(self.manager, exp_id, data_id)
                    self._append_log("  原始数据质量:")
                    self._append_log(format_quality_report(quality))
                    self._log_experiment_type_check(exp_id, data_id)
                except Exception:  # noqa: BLE001 - 自动填充/质检失败不阻断批量导入
                    pass
            else:
                self._append_log(
                    f"  导入失败: {item['folder']} ({item.get('error')})"
                )
        try:
            self.manager.save()
        except ProjectError:
            pass
        ok_count = sum(1 for item in results or [] if item.get("ok"))
        fail_count = (len(results or []) - ok_count)
        self._append_log(
            f"批量导入汇总: 共 {count} 个数据, 成功 {ok_count} 个, 失败 {fail_count} 个"
        )
        if fail_count:
            for item in results or []:
                if not item.get("ok"):
                    self._append_log(
                        f"  失败: {item.get('folder')} → {item.get('error')}"
                    )
        self._append_log(
            f"批量导入完成: 实验 {exp_id} 组 {batch_id_value},共 {count} 个样品数据"
        )
        self.refresh()
        self.project_tree.select_experiment(exp_id)
        self.center_panel.set_selection("experiment", exp_id)
        # 0.2.87:批量导入自动填充注释后立即刷新注释条
        self.center_panel._update_notes("experiment", exp_id, "")
        self._maybe_show_first_import_hint()

    # ------------------------------------------------------------------
    # 数据组(schema 1.4)
    # ------------------------------------------------------------------
    def _data_ndim(self, exp_id: str, data_id: str) -> int:
        """从 metadata 读数据维度(3D 批量暂不支持用);读不到回退 2。"""
        try:
            meta_path = self._manager.data_metadata_path(exp_id, data_id)
            if meta_path is not None and meta_path.is_file():
                import json

                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                return int((metadata.get("dataset") or {}).get("ndim") or 2)
        except Exception:  # noqa: BLE001 - 读不到按 2D 放宽(不误拦)
            pass
        return 2


    def _group_add_data(self, exp_id: str, group_id: str, data_ids: list) -> None:
        """把多个数据加入数据组(project.json 为唯一来源,0.2.164-补1)。"""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            self._append_log(f"数据组不存在: {group_id}")
            return
        for data_id in data_ids or []:
            try:
                # 0.2.199-补29hd:批量暂仅支持 2D 谱——3D 数据加入数据组会
                # 在批量处理时触发 SMILE(不稳定主机断电),此处直接拦截提示。
                if self._data_ndim(exp_id, str(data_id)) >= 3:
                    self._append_log(
                        f"数据 {data_id} 为 3D 谱,批量暂仅支持 2D,未加入数据组"
                    )
                    continue
                self.manager.add_to_group(exp_id, group_id, str(data_id))
                self._append_log(f"数据 {data_id} 已加入数据组 {group_id}")
            except Exception as exc:  # noqa: BLE001 - 单数据失败继续
                self._append_log(f"加入失败 {data_id}: {exc}")
        self.manager.save()
        self.refresh()

    def _group_remove_data(
        self, exp_id: str, group_id: str, data_id: str
    ) -> None:
        """把数据移出数据组(project.json 为唯一来源,0.2.164-补1)。"""
        if self.manager.project is None:
            return
        self.manager.remove_from_group(exp_id, group_id, data_id)
        self.manager.save()
        self._append_log(f"数据 {data_id} 已移出数据组 {group_id}")
        self.refresh()

    def _group_rename(self, exp_id: str, group_id: str, title: str) -> None:
        """重命名数据组。"""
        if self.manager.project is None:
            return
        self.manager.rename_data_group(exp_id, group_id, title)
        self.manager.save()
        self._append_log(f"数据组 {group_id} 已重命名为 {title}")
        self.refresh()

    def _group_delete(self, exp_id: str, group_id: str) -> None:
        """删除数据组(仅移除组,成员数据保留为单个数据)。"""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            return
        ok = ConfirmDialog.confirm(
            self,
            "删除数据组",
            f"删除数据组 {group_id}?组内 {len(group.data_ids)} 个数据"
            "将恢复为单个数据(数据本身不删除)。",
        )
        if not ok:
            return
        self.manager.delete_data_group(exp_id, group_id)
        self.manager.save()
        self._append_log(f"数据组 {group_id} 已删除(成员恢复单个数据)")
        self.refresh()

    def _group_delete_with_members(self, exp_id: str, group_id: str) -> None:
        """删除数据组连同组内全部数据(产物移入回收站,可恢复)。"""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            return
        ok = ConfirmDialog.confirm(
            self,
            "删除数据组(含数据)",
            f"删除数据组 {group_id}?组内 {len(group.data_ids)} 个数据"
            "将连同组一起移入回收站(可恢复)。",
        )
        if not ok:
            return
        deleted = self.manager.delete_data_group_with_members(exp_id, group_id)
        self.manager.save()
        self._append_log(
            f"数据组 {group_id} 已删除,连同 {len(deleted)} 个数据移入回收站"
        )
        self.refresh()

    def _run_group_batch(
        self,
        exp_id: str,
        group_id: str,
        steps: list,
        reference_data_id: str = "",
        params: dict | None = None,
    ) -> None:
        """数据组批量处理:后台线程执行,进度经日志输出。"""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None or not group.data_ids:
            self._append_log(f"数据组 {group_id} 没有成员数据,无法批量处理")
            return
        step_label = " → ".join(steps) if steps else "(空)"
        ref_text = (
            f",参考数据 {reference_data_id}" if reference_data_id else ""
        )
        group_scope = self.log_panel.scope_key("group", exp_id, "", group_id)
        self._append_log(
            f"开始数据组 {group_id} 批量处理: {step_label}{ref_text}",
            scope=group_scope,
        )
        self.center_panel.group_page.set_progress("批量处理运行中...")
        # 0.2.199-补5:组批量开始,左侧树组内各数据显示「运行中」
        self.project_tree.clear_batch_status()
        for data_id in group.data_ids:
            self.project_tree.mark_running(exp_id, data_id)

        _done_map = {
            "import": "已导入",
            "fid": "已生成 FID",
            "spectrum": "已生成谱图",
            "peaks": "已选峰",
            "analysis": "已分析",
        }

        def _status_label(status: str) -> str:
            if status == "failed":
                return "失败"
            if status == "skipped":
                return "跳过"
            if status == "cancelled":
                return "已取消"
            return _done_map.get(str(steps[-1]) if steps else "", "成功")

        def on_data_done(per: dict) -> None:
            """0.2.199-补29hf:每数据完成即回主线程写回树状态(不等批量全部)。"""
            data_id = per.get("data_id", "")
            _st = _status_label(per.get("status", ""))
            self.group_data_done.emit(exp_id, data_id, _st)
            data_scope = self.log_panel.scope_key("data", exp_id, data_id)
            for _lg in per.get("logs") or []:
                self.log_append_requested.emit(_lg, data_scope)

        def worker() -> None:
            try:
                self.controller.set_manager(self.manager)
                result = self.controller.run_group_batch(
                    exp_id,
                    group_id,
                    steps,
                    reference_data_id=reference_data_id,
                    params=params or {},
                    on_data_done=on_data_done,
                    progress=lambda msg: self.log_append_requested.emit(
                        msg, group_scope
                    ),
                )
                summary = dict(result.get("summary") or {})
                failed = list(result.get("failed") or [])
                skipped = list(result.get("skipped") or [])
                results = result.get("results") or {}
                cancelled = [
                    d for d, r in results.items() if r.get("status") == "cancelled"
                ]
                info = (
                    f"数据组 {group_id} 批量处理完成: "
                    f"{summary.get('success', 0)}/{summary.get('total', 0)} 成功"
                )
                if cancelled:
                    info += f" · 已停止,{len(cancelled)} 个未处理"
                if failed:
                    info += " · 失败: " + ",".join(failed)
                if skipped:
                    info += " · 跳过(类型/条件不一致): " + ",".join(skipped)
                items = []
                for data_id, per in results.items():
                    status = per.get("status")
                    items.append(
                        {
                            "data_id": data_id,
                            "ok": status in ("success", "already_done"),
                            "failed": status == "failed",
                            "skipped": status == "skipped",
                            "cancelled": status == "cancelled",
                            "message": "",
                            "error": per.get("error", ""),
                        }
                    )
                self.center_panel.group_page.summary_requested.emit(
                    {"info": info, "items": items}
                )
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程
                self.import_failed.emit(f"{type(exc).__name__}: {exc}")
            finally:
                # 0.2.199-补29c:不再在 worker 里 set_progress/refresh(跨线程
                # 碰控件);全部移到 _on_batch_run_done(队列信号,主线程)
                self.batch_run_done.emit()

        import threading

        # 0.2.199-补6:新任务开始前清除上次的取消标志
        from backend.runtime import clear_cancel

        clear_cancel()
        threading.Thread(target=worker, daemon=True).start()

    def _import_experiment_async(self, data: dict) -> None:
        """后台线程执行导入(三步接口 import_data),避免复制大文件阻塞 UI。"""
        import threading

        source = data.get("source", "")
        exp_id = data.get("experiment_id", "")
        self._append_log(f"开始导入: {source} (实验 {exp_id or '自动创建'})")

        def worker() -> None:
            try:
                explicit_segmented = bool(data.get("segmented", False))
                if explicit_segmented:
                    resolved_source = source
                    segmented = True
                else:
                    # Task F:忽略非数据子文件夹;容器 ≥2 数据子目录走分段,
                    # 恰好 1 个按单个数据目录导入
                    from gui.processing import resolve_import_source

                    resolved_source, segmented = resolve_import_source(source)
                if segmented:
                    # 0.2.108/G2B-011:分段采集导入(容器目录合并为一条数据,
                    # 导入到当前实验;exp_id 为空时后端新建)
                    result = self.controller.import_segmented_dataset(
                        resolved_source,
                        exp_id=exp_id or "",
                        title=data.get("title", "") or "",
                        sample_id=data.get("sample_id", "") or "",
                        copy=bool(data.get("copy", True)),
                    )
                    target_exp_id = getattr(
                        result, "experiment_id", ""
                    ) or exp_id
                else:
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
                        resolved_source,
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
                        params={
                            "copy": bool(data.get("copy", True)),
                            "segmented": segmented,
                        },
                    )
                    notes = (data or {}).get("notes", "") or ""
                    if notes:
                        from gui.notes import set_data_note_fields

                        set_data_note_fields(
                            self.manager.project,
                            target_exp_id,
                            data_id,
                            {"notes": notes},
                        )
                    # 0.2.86:导入后自动填充注释 + 检查并报告原始数据质量
                    try:
                        from gui.notes import auto_fill_notes_from_metadata
                        from gui.raw_quality import check_raw_quality

                        meta = self._read_data_metadata(target_exp_id, data_id)
                        self._last_auto_fill = auto_fill_notes_from_metadata(
                            self.manager, target_exp_id, data_id, meta
                        )
                        self._last_raw_quality = check_raw_quality(
                            self.manager, target_exp_id, data_id
                        )
                    except Exception:  # noqa: BLE001 - 自动填充/质检失败不阻断导入
                        self._last_auto_fill = {}
                        self._last_raw_quality = None
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
        quality = getattr(self, "_last_raw_quality", None) or {}
        auto_fill = getattr(self, "_last_auto_fill", None) or {}
        if auto_fill:
            summary = "、".join(f"{key}={value}" for key, value in auto_fill.items())
            self._append_log(f"自动填充注释: {summary}")
        if quality:
            from gui.raw_quality import format_quality_report

            self._append_log("原始数据质量:")
            self._append_log(format_quality_report(quality))
        exp_id = getattr(result, "experiment_id", None) or result.get("experiment_id", "")
        data_id = getattr(result, "data_id", "") or ""
        self._log_experiment_type_check(exp_id, data_id)
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
        # 0.2.112:导入成功后清空导入表单(名称/路径),便于连续导入
        self.center_panel.experiment_page.clear_import_form()
        self.refresh()
        if exp_id:
            self.project_tree.select_experiment(exp_id)
            # 0.2.87:导入自动填充注释后立即刷新注释条
            self.center_panel._update_notes("experiment", exp_id, "")
        self._maybe_show_first_import_hint()
        warnings = list(result.warnings)
        quality = getattr(self, "_last_raw_quality", None) or {}
        if quality and quality.get("issues"):
            warnings.extend(
                f"质量警告: {issue}" for issue in quality["issues"]
            )
        if warnings:
            InfoDialog.show_info(
                self, "导入完成(有提示)", "\n".join(warnings)
            )

    def _log_experiment_type_check(self, exp_id: str, data_id: str) -> None:
        """导入后提示检查数据类型识别(0.2.199-补29fd)。"""
        if not exp_id or not data_id:
            return
        try:
            meta = self._read_data_metadata(exp_id, data_id)
        except Exception:
            return
        et = ((meta or {}).get("dataset") or {}).get("experiment_type") or {}
        name = str(et.get("name", "") or "")
        if not name:
            return
        try:
            conf = float(et.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        # 0.2.199-补29fd-修:不管是否命中,一律提示检查数据类型
        self._append_log(
            f"数据类型识别: {name}(置信 {conf:.2f});"
            "请检查数据类型是否识别正确(可在样品数据注释中修改)"
        )

    def _read_data_metadata(self, exp_id: str, data_id: str) -> dict:
        """读样品数据 metadata.json(缺失返回空 dict)。"""
        if self.manager.project is None:
            return {}
        try:
            meta_path = self.manager.data_metadata_path(exp_id, data_id)
            if meta_path is None or not meta_path.is_file():
                return {}
            import json

            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 读不到 metadata 按空处理
            return {}

    def rename_experiment(self) -> None:
        exp_id = self.project_tree.current_experiment_id()
        if exp_id:
            self.project_tree.begin_rename_experiment(exp_id)

    def _rename_experiment_by_id(self, exp_id: str, new_title: str) -> None:
        entry = self.manager.project.experiment(exp_id) if self.manager.project else None
        if entry is None:
            return
        try:
            self.manager.rename_experiment(exp_id, new_title)
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
            f"删除实验 {exp_id} 及其产物文件?\n将移入系统回收站,可恢复。"
            f"\n(WorkflowRun 审计记录将保留)",
        )
        if not confirmed:
            return
        try:
            self.manager.delete_experiment(exp_id)
            self.manager.save()
            self._append_log(f"实验 {exp_id} 已移入系统回收站(可从回收站恢复)")
        except ProjectError as exc:
            InfoDialog.show_info(self, "删除实验失败", str(exc))
            return
        self.refresh()

    def _ask_note_fields(self, title: str, kind: str) -> dict:
        """常规信息表单对话框:确定返回字段 dict,取消返回空 dict。"""
        dialog = NotesDialog(self, title, kind)
        if dialog.exec() == NotesDialog.DialogCode.Accepted:
            return dialog.result_fields()
        return {}

    def about(self) -> None:
        InfoDialog.show_info(
            self,
            "关于 NMRForge",
            "NMRForge:面向 Bruker 2D/3D NMR 的自动化处理、参数优化与质量控制平台。\n"
            "三栏布局:项目管理树 / Pipeline / 谱图查看器。\n\n"
            "轴序约定(内部统一按逻辑序 F1/F2/F3 组织):\n"
            "· 2D:F1=间接维、F2=直接维;3D:F1=第一间接、F2=第二间接、F3=直接维。\n"
            "· NMRPipe 3D 文件标准存储序为 (F2,F1,F3),加载时自动转置到逻辑序\n"
            "  (日志「轴序重排」即此适配,属正常行为)。\n"
            "· 同核维度(如 2D 1H-1H、HNN 双 15N)按「直接维 > acqu2 > acqu3」\n"
            "  优先级加 x/y/z 下标区分(如 Nx=HSQC 的 N、Ny=顺序 N)。",
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

    def _manual_script_editor_menu(self) -> None:
        self._open_manual_dialog("script")

    def _manual_fid_menu(self) -> None:
        self._open_manual_dialog("fid")

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

    def _run_standalone_fid_diagnostics(self) -> None:
        """Other menu: pick fid file/folder, run diagnostics to global log."""
        from pathlib import Path as _P

        from PyQt6.QtWidgets import QFileDialog

        default_dir = str(self.workspace.root)
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择 fid 文件", default_dir,
            "FID (*.fid);;所有文件 (*)",
        )
        if file_path:
            self._run_standalone_check([_P(file_path)], "fid")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "选择 fid 文件夹", default_dir
        )
        if folder:
            self._run_standalone_check([_P(folder)], "fid")

    def _run_standalone_spectrum_quality(self) -> None:
        """Other menu: pick spectrum file, run quality evaluation."""
        from pathlib import Path as _P

        from PyQt6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择谱图文件", str(self.workspace.root),
            "NMRPipe (*.ft2 *.ft3 *.fdf);;所有文件 (*)",
        )
        if file_path:
            self._run_standalone_check([_P(file_path)], "spectrum")

    def _run_standalone_check(self, paths, kind: str) -> None:
        """Run check in worker thread, emit report to top-level global log.

        0.2.199-补29em:先跳转到最顶层(工作区根节点),把日志面板切到
        NMRForgeWorkspace 全局作用域,再后台执行并逐行输出。
        """
        top = self.project_tree.tree.topLevelItem(0)
        if top is not None:
            self.project_tree.tree.setCurrentItem(top)
        self.log_panel.set_scope("global", "", "", "")
        self._log_kind = "global"

        def worker() -> None:
            scope = self._log_scope()
            target = ", ".join(str(p) for p in paths)
            label = "数据质量检测" if kind == "fid" else "谱图质量评估"
            self.log_append_requested.emit(
                f"正在进行{label}: {target}", scope
            )
            try:
                if kind == "fid":
                    from workflow.direct_diagnostics import (
                        run_fid_diagnostics_paths,
                    )

                    res = run_fid_diagnostics_paths(paths)
                    lines = (
                        ["== " + "数据质量检测"
                         + "(独立入口) =="]
                        + list(res.reports)
                    )
                else:
                    from workflow.optimization_report import (
                        spectrum_quality_report_lines,
                    )

                    lines = spectrum_quality_report_lines(
                        str(paths[0]),
                        progress=lambda ln: self.log_append_requested.emit(
                            ln, scope
                        ),
                    )
                for ln in lines:
                    if ln:
                        self.log_append_requested.emit(ln, scope)
                self.log_append_requested.emit(
                    f"{label}完成: {target}", scope
                )
            except Exception as exc:  # noqa: BLE001
                self.log_append_requested.emit(
                    "检测失败: "
                    f"{type(exc).__name__}: {exc}",
                    scope,
                )

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def _open_settings(self) -> None:
        """打开软件设置对话框(阶段 C3)。"""
        from gui.dialogs import SettingsDialog

        SettingsDialog(self).exec()

    def _on_memory_guard(self, message: str) -> None:
        """SMILE 内存不足:日志 + 弹窗(主线程)。"""
        self._append_log(message)
        InfoDialog.show_info(self, "内存不足", message)

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
            try:
                from gui.processing import resolve_import_source
                from workflow.import_workflow import ImportWorkflowError

                resolved, seg = resolve_import_source(path)
            except ImportWorkflowError as exc:
                InfoDialog.show_info(self, "导入失败", str(exc))
                continue
            exp_id = self.project_tree.current_experiment_id()
            if exp_id:
                self._pending_data_names[exp_id] = Path(resolved).name
            self._import_experiment_async(
                {
                    "source": str(resolved),
                    "title": Path(resolved).name,
                    "sample_id": "",
                    "copy": True,
                    "segmented": seg,
                    "experiment_id": exp_id or "",
                }
            )
            imported += 1
        if imported:
            self._append_log(f"拖拽导入: {imported} 个数据目录")

    def _open_manual_dialog(self, step_id: str) -> None:
        """人工处理入口:按步骤打开脚本编辑器/fid 编辑器。"""
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
            InfoDialog.show_info(self, "提示", "该实验还没有样品数据,请先导入样品数据")
            return
        data_id = getattr(data_node, "id", exp_id)
        if step_id == "fid":
            self._open_fid_editor(data_node, exp_id, data_id, label)
        elif step_id in ("spectrum", "script"):
            self._open_script_editor(data_node, exp_id, data_id, label)
        elif step_id == "peaks":
            InfoDialog.show_info(
                self,
                "峰表编辑",
                "峰表添加/删除/编辑请在右侧谱图面板峰表操作,保存为 Poky .list 文件。",
            )
        elif step_id == "analysis":
            InfoDialog.show_info(
                self,
                "分析",
                "分析为自动步骤(HSQC CSP):在分析步骤选「比对谱」(自由态数据)后"
                "点运行;产物为分析目录 csp_data.csv 与当前数据 figures/ 下 "
                "csp_plot.svg、overlay_spectra.svg。",
            )
        else:
            InfoDialog.show_info(self, "人工处理", f"暂不支持该步骤的人工入口: {step_id}")

    def _current_data_node(self, entry):
        """当前选中样品数据节点(未选中时回退首个;空白实验返回 None)。"""
        nodes = list(getattr(entry, "data", None) or [])
        if not nodes:
            return None
        data_id = self.project_tree._data_id_of(self.project_tree.tree.currentItem())
        if data_id:
            return next((n for n in nodes if getattr(n, "id", "") == data_id), nodes[0])
        return nodes[0]

    def _open_fid_editor(self, data_node, exp_id: str, data_id: str, label: str) -> None:
        """fid.com 查看/修改/运行(manual_fid_com / run_manual_fid_com)。"""
        existing = self._existing_script_editor(data_id, "fid")
        if existing is not None:
            self._focus_script_editor(existing)
            return
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
        self._keep_script_dialog(dialog, data_id, "fid")
        dialog.show()

    def _open_script_editor(self, data_node, exp_id: str, data_id: str, label: str) -> None:
        """脚本编辑器:已有脚本优先(自动运行过的直接展示),无则渲染默认 → 编辑/保存/运行。"""
        existing = self._existing_script_editor(data_id, "spectrum")
        if existing is not None:
            self._focus_script_editor(existing)
            return
        try:
            scripts = self.controller.manual_scripts(
                data_node, params=None, exp_id=exp_id, data_id=data_id
            )
        except Exception as exc:  # noqa: BLE001 - 后端缺失统一提示
            from workflow.manual import ManualRunError

            if isinstance(exc, ManualRunError) and "请先执行「生成 FID」步骤" in str(
                exc
            ):
                InfoDialog.show_info(self, "请先生成 FID", str(exc))
            else:
                InfoDialog.show_info(
                    self, "加载脚本失败", f"{type(exc).__name__}: {exc}"
                )
            return
        script_key = _pick_script_key(scripts, data_id)
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
        self._keep_script_dialog(dialog, data_id, "spectrum")
        dialog.show()

    def _wire_script_run(
        self, dialog, data_node, exp_id: str, data_id: str, script_name: str
    ) -> None:
        """脚本编辑器「运行」→ 后台执行并登记。"""
        dialog.run_requested.connect(
            lambda content: self._run_script_async(
                content, data_node, exp_id, data_id, script_name
            )
        )

    def _existing_script_editor(
        self, data_id: str, step: str
    ) -> ScriptEditorDialog | None:
        """同数据同步骤已打开的编辑器:存在且可见时复用,不重复弹出(0.2.193)。"""
        dialog = self._script_editors.get((data_id, step))
        if dialog is None:
            return None
        if dialog.isVisible():
            return dialog
        self._script_editors.pop((data_id, step), None)
        return None

    def _focus_script_editor(self, dialog: ScriptEditorDialog) -> None:
        """把已打开的编辑器带到前台(重复打开时复用,0.2.193)。"""
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _keep_script_dialog(
        self, dialog: ScriptEditorDialog, data_id: str, step: str
    ) -> None:
        """持有非模态脚本编辑器引用,关闭后释放(0.2.192/0.2.193)。

        打开时不锁定主界面(show 而非 exec);WA_DeleteOnClose + destroyed
        保证关闭即释放;按 (data_id, step) 去重,同数据同步骤不重复弹窗。
        """
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._script_editors[(data_id, step)] = dialog

        def _drop() -> None:
            key = (data_id, step)
            if self._script_editors.get(key) is dialog:
                self._script_editors.pop(key, None)

        dialog.destroyed.connect(_drop)

    def _run_script_async(
        self, content: str, data_node, exp_id: str, data_id: str, script_name: str
    ) -> None:
        """后台运行人工脚本;实时转发脚本输出,完成后主线程刷新。"""
        import threading

        # 0.2.199-补29d:人工运行日志按数据作用域,切换选中不串
        manual_scope = self.log_panel.scope_key("data", exp_id, data_id)
        # 0.2.199-补29h:运行前脚本检测(续行符/CRLF/BOM/缺输出等常见错误)
        from workflow.script_check import check_script

        check_warnings = check_script(content, script_name)
        if check_warnings:
            for w in check_warnings:
                self.log_append_requested.emit(f"⚠ 脚本检查: {w}", manual_scope)
            if not ConfirmDialog.confirm(
                self,
                "脚本检查发现警告",
                f"{script_name} 运行前检查发现 {len(check_warnings)} 项问题:\n\n"
                + "\n".join(f"· {w}" for w in check_warnings)
                + "\n\n仍要继续运行吗?",
            ):
                self.log_append_requested.emit(
                    "已取消运行(脚本检查警告)", manual_scope
                )
                return
        self.log_append_requested.emit(
            f"开始人工运行: {script_name} (数据 {data_id})", manual_scope
        )

        def worker() -> None:
            def forward(line: str) -> None:
                if line:
                    self.log_append_requested.emit(
                        f"[{script_name}] {line}", manual_scope
                    )

            try:
                if script_name == "fid.com":
                    result = self.controller.run_manual_fid_com(
                        data_node,
                        content,
                        exp_id=exp_id,
                        data_id=data_id,
                        progress=forward,
                    )
                    message = f"fid.com 运行完成: {result}"
                else:
                    result = self.controller.run_manual_spectrum(
                        data_node,
                        {script_name: content},
                        exp_id=exp_id,
                        data_id=data_id,
                        progress=forward,
                    )
                    message = f"{script_name} 运行完成: {result}"
            except Exception as exc:  # noqa: BLE001 - 错误统一回主线程
                self.log_append_requested.emit(
                    f"人工运行失败: {type(exc).__name__}: {exc}", manual_scope
                )
                self.manual_run_done.emit()
                return
            self.log_append_requested.emit(message, manual_scope)
            self.manual_run_done.emit()

        # 0.2.199-补5:人工运行开始,左侧树该数据显示「运行中」
        self.project_tree.mark_running(exp_id, data_id)
        # 0.2.199-补6:新任务开始前清除上次的取消标志
        from backend.runtime import clear_cancel

        clear_cancel()
        threading.Thread(target=worker, daemon=True).start()

    def _on_pipeline_run_started(self, exp_id: str, data_id: str) -> None:
        """处理开始:左侧树该数据显示「运行中」,并把日志面板切到目标
        数据/组作用域(否则进度日志只进缓冲不显示,观感像日志被吞)。"""
        self.project_tree.mark_running(exp_id, data_id)
        try:
            group = (
                self.manager.group_of_data(exp_id, data_id)
                if self.manager is not None and self.manager.project is not None
                else None
            )
        except Exception:  # noqa: BLE001 - 组解析失败按单数据作用域
            group = None
        if group is not None:
            self.log_panel.set_scope("group", exp_id, "", group.id)
        else:
            self.log_panel.set_scope("data", exp_id, data_id)

    def _on_pipeline_run_finished(self) -> None:
        """Pipeline 处理步骤完成后刷新左侧树/中间/谱图面板(主线程)。"""
        self.project_tree.clear_running()
        self.refresh()
        self.center_panel.refresh()
        # 0.2.88:不自动显示谱,刷新文件列表即可(「展示谱图」按钮已出现)
        self.spectrum_panel.refresh()

    def _show_spectrum_from_pipeline(self, _step_id: str = "") -> None:
        """「展示谱图」按钮:在右侧谱图面板显示当前数据的最终谱。"""
        exp_id = self.pipeline.current_experiment_id()
        data_id = getattr(self.pipeline, "_current_data_id", "")
        if not (exp_id and data_id) or self.manager.project is None:
            return
        self.spectrum_panel.set_context(exp_id, data_id)
        if not self.spectrum_panel.load_current_spectrum():
            InfoDialog.show_info(self, "提示", "该样品数据还没有谱图文件")

    def _on_batch_run_done(self) -> None:
        """数据组批量处理完成:清除运行中标记、进度并刷新(主线程)。

        0.2.199-补29c:批量进度清理与刷新从 worker finally 移到这里
        (batch_run_done 为队列信号,槽在主线程执行)。
        """
        self.project_tree.clear_running()
        self.center_panel.group_page.set_progress("")
        self.refresh()
        self.center_panel.refresh()

    def _on_manual_run_done(self) -> None:
        """人工脚本运行完成后刷新 Pipeline/报告页(主线程)。"""
        self.project_tree.clear_running()
        self.refresh()
        self.center_panel.refresh()

    def _on_peaks_saved(self) -> None:
        """峰表写回后刷新 Pipeline(peaks 状态)并记录日志。"""
        self._append_log("峰表已保存并登记 manual_peaks 运行")
        self.center_panel.refresh()

    def _show_report(self) -> None:
        """查看菜单:打开报告页(当前选中实验/样品数据)。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先打开项目")
            return
        self.center_panel.show_report()

    # ------------------------------------------------------------------
    # 树动作(契约 v1.2 §8.5)
    # ------------------------------------------------------------------
    def _rename_project(self, new_name: str = "") -> None:
        """重命名当前项目:优先 WorkspaceManager.rename_project(目录+name)。"""
        if self.manager.project is None or self.manager.root is None:
            return
        old_name = self.manager.root.name
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
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
            f"删除项目 {self.manager.project.name} 及其全部样品数据/产物?"
            "\n项目将移入系统回收站,可恢复(审计历史将保留)\n路径: {self.manager.root}",
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
        self.statusBar().showMessage("项目已移入系统回收站,可从回收站恢复")

    def _create_experiment(self) -> None:
        """新建空白实验(菜单/Project/空白处右键):项目树内内联命名(不弹窗)。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        self.project_tree.begin_create_experiment()

    def _on_experiment_create_submitted(self, title: str) -> None:
        """项目树内联命名提交:直接创建空白实验(0.2.199-补29dw 不再弹常规
        信息表单,注释留空、创建后可通过「编辑注释」补充)。"""
        if self.manager.project is None:
            return
        self._create_experiment_with_title_and_fields(title.strip())

    def _import_data_with_options(
        self, exp_id: str, name: str, source: str, copy: bool
    ) -> None:
        """中间面板内嵌导入表单:按指定实验导入样品数据(可命名)。"""
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

    def _rename_data(self, exp_id: str, data_id: str, new_name: str = "") -> None:
        """数据右键重命名:优先 manager.rename_data 落盘;缺失时直接写 title 并提示。"""
        entry = self.manager.project.experiment(exp_id) if self.manager.project else None
        if entry is None:
            return
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        new_name = new_name.strip()
        if not new_name:
            return
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
        """删除样品数据(不删实验);确认 + manager.delete_data + save + refresh。"""
        if self.manager.project is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "删除样品数据",
            f"删除样品数据 {data_id} 及其产物文件?\n将移入系统回收站,可恢复。"
            f"\n(WorkflowRun 审计记录将保留)",
        )
        if not confirmed:
            return
        try:
            self.manager.delete_data(exp_id, data_id)
            self.manager.save()
            self._append_log(f"样品数据 {data_id} 已移入系统回收站(可从回收站恢复)")
        except ProjectError as exc:
            InfoDialog.show_info(self, "删除样品数据失败", str(exc))
            return
        self.refresh()

    def _create_experiment_with_title(self, title: str) -> None:
        """中间面板内嵌表单:新建空白实验。"""
        self._create_experiment_with_title_and_fields(title.strip())

    def _create_experiment_with_title_and_fields(
        self, title: str, fields: dict | None = None
    ) -> None:
        """按标题(可带常规信息字段)创建空白实验。"""
        if self.manager.project is None:
            InfoDialog.show_info(self, "提示", "请先新建或打开项目")
            return
        try:
            create = getattr(self.manager, "create_experiment", None)
            if create is not None:
                entry = create(title=title.strip())
            else:
                entry = self.manager.add_experiment("", title=title.strip())
            if fields:
                from gui.notes import set_experiment_note_fields

                set_experiment_note_fields(self.manager.project, entry.id, fields)
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "新建实验失败", str(exc))
            return
        self.refresh()
        self.project_tree.select_experiment(entry.id)

    def _import_data_for(self, exp_id: str) -> None:
        """在指定实验下导入样品数据。"""
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
        """样品数据右键三步操作:生成 FID / 生成谱图 / 删除样品数据。"""
        exp_id = self.project_tree.current_experiment_id()
        if not exp_id:
            return
        if action == "delete":
            self._delete_data(exp_id, data_id)
            return
        step = "fid" if action == "fid" else "spectrum"
        self.center_panel.set_selection("data", exp_id, data_id)
        self.center_panel.run_step(step, data_id=data_id)

    def _edit_notes(self, kind: str, exp_id: str, data_id: str) -> None:
        """编辑项目/实验/样品数据注释(中间顶部注释条「编辑注释」)。"""
        if self.manager.project is None or not kind:
            return
        from gui.notes import (
            data_note_fields,
            experiment_note_fields,
            sample_note_fields,
            set_data_note_fields,
            set_experiment_note_fields,
            set_sample_note_fields,
        )

        title = {
            "project": "项目注释",
            "experiment": "实验注释",
            "data": "样品数据注释",
        }.get(kind, "注释")
        if kind == "project":
            current = sample_note_fields(self.manager.project)
        elif kind == "experiment":
            current = experiment_note_fields(self.manager.project, exp_id)
        else:
            kind = "data"
            current = data_note_fields(self.manager.project, exp_id, data_id)
        dialog = NotesDialog(self, f"编辑{title}", kind, current)
        if dialog.exec() != NotesDialog.DialogCode.Accepted:
            return
        fields = dialog.result_fields()
        if kind == "project":
            set_sample_note_fields(self.manager.project, fields)
        elif kind == "experiment":
            set_experiment_note_fields(self.manager.project, exp_id, fields)
        else:
            set_data_note_fields(self.manager.project, exp_id, data_id, fields)
            exptype = str((fields or {}).get("experiment_type", "") or "")
            if exptype:
                from workflow.import_workflow import apply_user_experiment_type

                if apply_user_experiment_type(
                    self.manager, exp_id, data_id, exptype
                ):
                    self._append_log(f"数据类型已按用户选择更新: {exptype}")
        try:
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, "保存失败", str(exc))
            return
        self.center_panel._update_notes(kind, exp_id, data_id)
        self._append_log(f"已保存{title}")

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

    def _toggle_spectrum_expand(self, expanded: bool) -> None:
        """谱图放大:隐藏左侧三部分(项目树/Pipeline/Log),谱图占满窗口。"""
        for widget in (self.project_tree, self.center_panel, self.log_panel):
            widget.setVisible(not expanded)

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

    def _open_terminal(self, path: str) -> None:
        """在终端中打开目录(优先 csh,便于直接运行 NMRPipe 命令)。"""
        from gui.project_tree import open_in_terminal

        if not open_in_terminal(path):
            InfoDialog.show_info(self, "提示", "未找到可用的终端程序")

    def _open_path(self, path: str) -> None:
        """用系统文件管理器打开目录(双击/右键 data/子文件夹),中间保持 Pipeline。"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        target = Path(path)
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        # 保持中间 Pipeline 上下文(当前选中实验时切回样品数据页)
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
        current = self.project_tree.tree.currentItem()
        data_id = self.project_tree._data_id_of(current)
        parts = [self.manager.project.name]
        if exp_id:
            exp = self.manager.project.experiment(exp_id)
            parts.append((exp.title or exp_id) if exp is not None else exp_id)
        current_data = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        if (
            isinstance(current_data, dict)
            and current_data.get("kind") == "group"
            and exp_id
        ):
            group_id = str(current_data.get("group_id", ""))
            group = self.manager.group(exp_id, group_id)
            label = getattr(group, "title", "") or f"Group {group_id}"
            parts.append(f"{label} ({len(group.data_ids or [])} 个数据)" if group else label)
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

    def _update_context(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """左侧选择变化 → 中间按选中类型显示,右侧围绕数据刷新,日志切换作用域。"""
        self.center_panel.set_selection(kind, exp_id, data_id, group_id)
        if kind == "group":
            self.spectrum_panel.set_context(exp_id, "")
        else:
            self.spectrum_panel.set_context(exp_id, data_id)
        self._log_kind = kind or ""
        self._log_exp_id = exp_id or ""
        self._log_data_id = data_id or ""
        self._log_group_id = group_id or ""
        self.log_panel.set_scope(kind, exp_id, data_id, group_id)
        self._update_context_bar()

    def closeEvent(self, event) -> None:
        """关闭应用前终止全部后端任务并清理孤儿进程,避免下次遗留。"""
        try:
            from backend.runtime import (
                cleanup_orphan_tasks,
                request_cancel,
                terminate_current_tasks,
            )

            terminate_current_tasks()
            request_cancel()
            try:
                from backend.config import load_config, nmrpipe_path

                bin_dir = str(nmrpipe_path(load_config()) or "")
            except Exception:  # noqa: BLE001 - 配置读取失败不阻断清理
                bin_dir = ""
            cleanup_orphan_tasks(
                bin_dir=bin_dir or None,
                workspace=str(self.manager.root or ""),
            )
        except Exception:  # noqa: BLE001 - 关闭清理失败不阻断退出
            pass
        super().closeEvent(event)

    def _on_stop_requested(self) -> None:
        """停止当前任务:终止进程树 + 请求取消内存计算 + 清理孤儿进程。"""
        from backend.runtime import (
            cleanup_orphan_tasks,
            request_cancel,
            terminate_current_tasks,
        )

        killed = terminate_current_tasks()
        # 0.2.199-补6:内存相位搜索无子进程可杀,需显式请求取消,内存计算
        # 会在检查点退出;再清扫上次异常退出遗留的孤儿进程
        request_cancel()
        orphan_count = 0
        try:
            from backend.config import load_config, nmrpipe_path

            bin_dir = str(nmrpipe_path(load_config()) or "")
        except Exception:  # noqa: BLE001 - 配置读取失败不阻断清理
            bin_dir = ""
        try:
            orphan_count = cleanup_orphan_tasks(
                bin_dir=bin_dir or None,
                workspace=str(self.manager.root or ""),
            )
        except Exception:  # noqa: BLE001 - 清理失败不阻断
            orphan_count = 0
        if killed or orphan_count:
            self._append_log(
                f"已停止当前任务({killed} 个进程树,清理残留 {orphan_count} 个;"
                "内存计算已请求取消,稍候自动退出)"
            )
        else:
            self._append_log("当前没有正在运行的任务(已请求取消内存计算)")

    def _append_log(self, message: str, scope: str | None = None) -> None:
        self.log_panel.append(message, scope=scope)
        self.log_panel.setVisible(True)

    def _log_scope(self) -> str:
        """当前选中上下文对应的日志作用域键。"""
        return self.log_panel.scope_key(
            self._log_kind,
            self._log_exp_id,
            self._log_data_id,
            self._log_group_id,
        )

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
            if getattr(exp, "trashed", False):
                continue
            status = self.manager.infer_status(exp.id).value
            item = QTreeWidgetItem([exp.id, exp.title, status, exp.sample_id, exp.source])
            item.setData(0, Qt.ItemDataRole.UserRole, exp.id)
            tree.addTopLevelItem(item)
        self.setWindowTitle(f"NMRForge - {project.name}")
        self.statusBar().showMessage(f"项目: {self.manager.root}")
        # 打开/新建项目后默认聚焦第一个实验
        active_exps = [e for e in project.experiments if not getattr(e, "trashed", False)]
        if active_exps and not self.center_panel.current_experiment_id():
            self.project_tree.select_experiment(active_exps[0].id)
        self._update_context_bar()

    @staticmethod
    def run() -> int:
        """启动 Qt 应用(供 main.py 调用)。"""
        import sys

        app = QApplication(sys.argv)
        from gui.dialogs import install_dialog_centering
        from gui.theme import app_icon, apply_dark_theme

        install_dialog_centering(app)
        apply_dark_theme(app)
        # 0.2.199-补29eq:GNOME 按 desktop 文件名匹配运行中的窗口到
        # .desktop,任务栏/启动器才显示正确图标(终端启动同样生效)
        app.setApplicationName("NMRForge")
        try:
            app.setDesktopFileName("NMRForge")
        except AttributeError:
            pass
        _icon = app_icon()
        if _icon is not None:
            app.setWindowIcon(_icon)
        window = MainWindow()
        window.show()
        code = app.exec()
        # 0.2.199:PyQt6/SIP 在解释器收尾时遍历已悬空的 sip 包装指针
        # (cleanup_on_exit -> sip_api_get_address(0x1e80))导致 SIGSEGV,
        # 现象为关闭主窗口退出时核心转储。在事件循环返回后、Python 进入
        # Py_FinalizeEx 之前,显式销毁全部顶层窗口并处理 deleteLater,让
        # Qt 对象树在 sip 仍追踪时按序析构,绕开进程结束阶段对无有效 C++
        # 对象包装的访问。
        try:
            for _w in list(app.topLevelWidgets()):
                try:
                    _w.close()
                    _w.deleteLater()
                except RuntimeError:  # pragma: no cover - 已销毁
                    pass
            app.processEvents()
            app.sendPostedEvents(None, 0)
            app.processEvents()
        except Exception:  # noqa: BLE001 - 清理失败不阻断退出
            pass
        return code
