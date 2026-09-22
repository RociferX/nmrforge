"""Main window: three-column layout (Project management tree / Pipeline / spectrum viewer) + bottom
Task/Log. layout follows docs/GUI_ARCHITECTURE_VISION.md: - Left: ProjectTreePanel(Project ->
Experiment -> Input/Processing/Output/Figures); - Middle: PipelinePanel (contextual breadcrumbs
+ state-driven step list + next step prompt); - Right: SpectrumPanel (embedded
viewer.SpectrumViewer + project spectrum file list); - Middle vertical column: LogPanel (task
log, located between pipeline and spectrum viewer, automatically expanded); - The window is
attached to the top of the screen by default, and the height fills the available area (does not
cover the taskbar). All project data are accessed through core.project (GUI are not read and
written directly project.json); all back-end processing is called through
gui/processing.ProcessingController."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from qtcompat.QtCore import Qt, QTimer
from qtcompat.QtGui import QAction
from qtcompat.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.project import (
    JsonRecentProjectsStore,
    ProjectError,
    ProjectManager,
)
from core.user_errors import describe_exception
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
from gui.pipeline_panel import STEP_LABEL
from gui.processing import ProcessingController
from gui.project_tree import ProjectTreePanel
from gui.tutorial import TutorialDialog
from qtcompat import Signal
from ui_support.i18n import tr

# 0.2.199-patch29ht(user): Heavy modules are changed to delayed import -- the spectrum panel
# (pyqtgraph/viewer) and the import workflow are not loaded when `import gui.main_window` is not in
# `import gui.main_window`. The window appears first, and then _finish_startup() imports and adds
# the spectrum panel in the background; only light imports are left at the top level.
if TYPE_CHECKING:  # pragma: no cover - Only type checking, not executed at runtime.
    from gui.spectrum_panel import SpectrumPanel
    from workflow.import_workflow import ImportResult


def _pick_script_key(scripts: dict[str, str], data_id: str) -> str:
    """Select the default script of the script editor: give priority to the main script with the
    same name as the data, Fallback common name/first."""
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
    """NMRForge main window; displays the welcome page when the project is not open."""

    import_failed = Signal(str)  # Import failure information (background thread -> main thread).
    import_finished = Signal(object)  # ImportResult(Background thread -> main thread).
    batch_import_finished = Signal(str, str, int, object)  # (exp_id, batch_id, count, results)
    # 0.2.199-patch29hd: Batch import progress one by one (background thread -> main thread displays
    # one by one, not waiting for batch completion).
    batch_import_progress = Signal(str, str, str, bool, str)  # Progress step by step.
    # 0.2.199-patch29hd: After the data group is completed in batches, the data status
    # (success/fail/jump over) is written back to the left tree.
    group_data_done = Signal(str, str, str)  # (exp_id, data_id, status)
    manual_run_log = Signal(str)  # Manual script running log (background thread -> main thread).
    manual_run_done = Signal()  # Manual script running completed (main thread refreshes UI).
    # Data group batch processing is completed (background thread -> main thread clears running
    # mark).
    batch_run_done = Signal()
    # Worker thread log via queue signal(0.2.199-patch29c).
    log_append_requested = Signal(str, object)
    # 0.2.199-patch29ht: Background preheating completed -> Main thread re-builds the spectrum
    # panel.
    _spectrum_panel_ready = Signal()

    def __init__(
        self,
        manager: ProjectManager | None = None,
        recent: JsonRecentProjectsStore | None = None,
        controller: ProcessingController | None = None,
        *,
        defer_spectrum_panel: bool = False,
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
        # 0.2.199-patch29c: The worker thread progress log passes the queue signal; the old code is
        # directly adjusted in the worker _append_log -> LogPanel.append -> QTextEdit (cursor flash
        # timer) triggers QBasicTimer::start error and stuck.
        self.log_append_requested.connect(self._append_log)
        self._spectrum_panel_ready.connect(self._on_spectrum_panel_ready)
        self._pending_data_names: dict[str, str] = {}
        # The non-modal script editor holds the reference (0.2.192);0.2.193 and presses (data_id,
        # step) to remove duplicates -- only one editor is allowed for the same data and the same
        # step.
        self._script_editors: dict[tuple[str, str], ScriptEditorDialog] = {}
        self._last_auto_fill: dict = {}
        self._last_raw_quality: dict | None = None
        # 0.2.199-patch29ht: The spectrum panel can be built delayed (re-imported into pyqtgraph);
        # it is built immediately by default, and only MainWindow.run() takes the delayed path. The
        # placeholder control first occupies the fourth column.
        self._defer_spectrum_panel = bool(defer_spectrum_panel)
        self.spectrum_panel: SpectrumPanel | None = None
        self._spectrum_placeholder: QWidget | None = None
        # Log scope: currently selected context (maintained by _update_context).
        self._log_kind = ""
        self._log_exp_id = ""
        self._log_data_id = ""
        self._log_group_id = ""
        self.setWindowTitle("NMRForge")
        # 0.2.199-patch29eq: Application icon (window/task bar).
        try:
            from ui_support.theme import app_icon

            _icon = app_icon()
            if _icon is not None:
                self.setWindowIcon(_icon)
        except Exception:  # noqa: BLE001 - Missing icon does not block startup.
            pass
        self.setAcceptDrops(True)  # Drag and drop Bruker data directory to import.
        self._build_menus()
        self._build_central()
        # 0.2.143: Default geometry dependency main_splitter column width, applied after central
        # construction.
        self._apply_default_geometry()
        self.refresh()

    # ------------------------------------------------------------------
    # UI Build.
    # ------------------------------------------------------------------
    def _apply_default_geometry(self) -> None:
        """Open the default geometry: the top is attached to the upper edge of the screen, and the
        height fills the available area (not covering the taskbar). The default column width is
        fixed [420, 600, 300, 600], totaling 1920, suitable for 1080p full width; when the
        screen is narrower than 1920, the window is narrowed to the available width, and the
        columns are automatically allocated by QSplitter (the user can still freely drag the
        length and width of each column)."""
        from qtcompat.QtCore import QRect
        from qtcompat.QtGui import QGuiApplication

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

        file_menu = bar.addMenu(tr("&File"))
        file_menu.addAction(tr("New project..."), self.new_project)
        file_menu.addAction(tr("Open project..."), self.open_project)
        save_action = QAction(tr("save project"), self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project)
        file_menu.addAction(save_action)
        self.recent_menu = QMenu(tr("recent project"), self)
        file_menu.addMenu(self.recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(tr("quit"), self.close)

        experiment_menu = bar.addMenu(tr("&experiment"))
        experiment_menu.addAction(tr("New experiment..."), self._create_experiment)
        experiment_menu.addAction(tr("rename experiment..."), self.rename_experiment)
        experiment_menu.addAction(tr("delete experiment"), self.delete_experiment)

        view_menu = bar.addMenu(tr("&View"))
        # 0.2.199-patch29br: Hide the independent spectrum viewer entrance (file/help has been moved
        # to the spectrum panel).
        view_menu.addSeparator()
        self.view_left_action = QAction(tr("Project management on the left"), self, checkable=True)
        self.view_left_action.setChecked(True)
        self.view_left_action.toggled.connect(self._toggle_left)
        view_menu.addAction(self.view_left_action)
        self.view_pipeline_action = QAction(tr("Intermediate Pipeline"), self, checkable=True)
        self.view_pipeline_action.setChecked(True)
        self.view_pipeline_action.toggled.connect(self._toggle_pipeline)
        view_menu.addAction(self.view_pipeline_action)
        self.view_spectrum_action = QAction(tr("right spectrum"), self, checkable=True)
        self.view_spectrum_action.setChecked(True)
        self.view_spectrum_action.toggled.connect(self._toggle_spectrum)
        view_menu.addAction(self.view_spectrum_action)

        tools_menu = bar.addMenu(tr("&Tools"))
        tools_menu.addAction(
            tr("Data quality inspection..."),
            self._run_standalone_fid_diagnostics,
        )
        tools_menu.addAction(
            tr("spectrum quality assessment..."),
            self._run_standalone_spectrum_quality,
        )

        settings_menu = bar.addMenu(tr("&Settings"))
        settings_menu.addAction(tr("Software settings..."), self._open_settings)
        help_menu = bar.addMenu(tr("&Help"))
        help_menu.addAction(tr("Usage tutorial..."), self.show_tutorial)
        help_menu.addAction(tr("about"), self.about)

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
        self.pipeline = self.center_panel.pipeline  # Compatible with old references.
        self.center_panel.log_message.connect(self._append_log)
        # 0.2.199-patch29d: Scope log (by data/Group) directly falls into the corresponding buffer
        # and does not switch with selection.
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
        # First import prompt: Triggered in the import completion signal (see
        # _on_import_done/_on_batch_import_done).
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
        self.pipeline.rank1_run_requested.connect(self._on_rank1_rerun)

        self._spectrum_placeholder = QWidget()
        if not self._defer_spectrum_panel:
            self._build_spectrum_panel()

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.project_tree)
        self.main_splitter.addWidget(self.center_panel)
        self.log_panel = LogPanel()
        self.log_panel.set_manager(self.manager)
        self.log_panel.stop_requested.connect(self._on_stop_requested)
        # 0.2.143: The log interface is permanently displayed (no longer hidden by default), the
        # width is not limited and can be dragged.
        self.log_panel.setVisible(True)
        self.main_splitter.addWidget(self.log_panel)
        self.main_splitter.addWidget(
            self.spectrum_panel
            if self.spectrum_panel is not None
            else self._spectrum_placeholder
        )
        self.main_splitter.setStretchFactor(0, 20)
        self.main_splitter.setStretchFactor(1, 40)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setStretchFactor(3, 40)
        # 0.2.143: There is no hard limit on column width, and the four columns can be adjusted
        # freely by dragging; see _apply_default_geometry for the default initial width (base column
        # width x1.4).

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.context_bar = QLabel(tr("project not open"))
        self.context_bar.setWordWrap(True)
        self.context_bar.setStyleSheet(
            "background: #202124; border-left: 3px solid #0e639c; "
            "padding: 6px 12px; font-weight: bold; color: #ffffff;"
        )
        # 0.2.141: log is the middle vertical column (within the horizontal separator bar) and no
        # longer occupies the bottom height.
        central_layout.addWidget(self.context_bar)
        central_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    # Spectrum panel construction (can be postponed).
    # ------------------------------------------------------------------
    def _build_spectrum_panel(self, apply_context: bool = False):
        """Build the spectrum panel on the right (return directly if it has been built).
        0.2.199-patch29ht: The panel import pyqtgraph/viewer is relatively heavy and can be
        called after the window is displayed; when the user preemptively triggers the spectrum
        operation, it is built synchronously by _ensure_spectrum_panel(). When
        apply_context=True, the application context is selected according to the current tree
        (delayed use of the build path)."""
        if self.spectrum_panel is not None:
            return self.spectrum_panel
        from gui.spectrum_panel import SpectrumPanel

        panel = SpectrumPanel(self.manager, controller=self.controller)
        panel.peaks_saved.connect(self._on_peaks_saved)
        # 0.2.199-patch29cz: Peak panel task log (3D loading failure, etc.) into LogPanel.
        panel.log_message.connect(self._append_log)
        panel.status_message.connect(self.statusBar().showMessage)
        # 0.2.199-patch29bp: spectrum enlarge/close(collapse the three parts on the left).
        panel.expand_requested.connect(self._toggle_spectrum_expand)
        self.spectrum_panel = panel
        placeholder = self._spectrum_placeholder
        splitter = getattr(self, "main_splitter", None)
        if placeholder is not None and splitter is not None:
            index = splitter.indexOf(placeholder)
            if index >= 0:
                splitter.replaceWidget(index, panel)
            else:  # pragma: no cover - When the placeholder is missing, add it directly.
                splitter.addWidget(panel)
            placeholder.deleteLater()
            self._spectrum_placeholder = None
        elif placeholder is not None and splitter is None:
            # Build the path immediately (test/Direct construction): When the divider is created
            # later, the panel will be added directly.
            self._spectrum_placeholder = None
        if apply_context:
            panel.set_context(
                self.project_tree.current_experiment_id(),
                self.project_tree.current_data_id(),
            )
        return panel

    def _ensure_spectrum_panel(self):
        """Make sure the spectrum panel is ready (if it is not ready, it will be built immediately
        for user to click first)."""
        return self.spectrum_panel or self._build_spectrum_panel(
            apply_context=True
        )

    def _on_spectrum_panel_ready(self) -> None:
        """Background preheating is completed -> Main thread builds spectrum panel."""
        try:
            self._build_spectrum_panel(apply_context=True)
        except Exception:  # noqa: BLE001 - Panel build failure does not block startup.
            pass

    def _finish_startup(self) -> None:
        """After the window is displayed: import the heavy module in the background, and then build
        the spectrum panel after completion. 0.2.199-patch29ht(user): show first and then import
        -- user can use other functions immediately, and the import is performed in the
        background thread; after preheating nmrglue/scipy/matplotlib, there will be no
        additional waiting for subsequent clicks on spectrum/peak selection (the cost is moved
        from "when clicking spectrum" to "after the window is available")."""
        import threading

        def work() -> None:
            # ① First import the spectrum panel itself (including pyqtgraph/viewer) -> Create the
            # panel immediately, and the right column will be visible as soon as possible;
            try:
                import gui.spectrum_panel  # noqa: F401 - Panel depends on pyqtgraph + viewer.
            except Exception:  # noqa: BLE001 - Failure to warm up does not affect functionality.
                pass
            self._spectrum_panel_ready.emit()
            # ② After the panel, warm up the heavy modules that will be used later (all in the
            # background, not blocking the first frame): nmrglue spectrum reading, scipy Peak
            # detection/interpolation/Hilbert, matplotlib contours.
            for _mod in (
                "nmrglue",
                "scipy.ndimage",
                "scipy.signal",
                "matplotlib.pyplot",
            ):
                try:
                    import importlib

                    importlib.import_module(_mod)
                except Exception:  # noqa: BLE001 - No interruption if a single module fails.
                    continue

        self._prewarm_thread = threading.Thread(
            target=work, daemon=True, name="nmrforge-prewarm"
        )
        self._prewarm_thread.start()

    # ------------------------------------------------------------------
    # Project actions.
    # ------------------------------------------------------------------
    def new_project(self) -> None:
        """New project: Inline naming (no pop-up window). Enter on the welcome page when the
        project is not open; enter on the project tree when it is open."""
        if self.manager.project is None:
            self.center_panel.welcome_page.begin_inline_name()
        else:
            self.project_tree.begin_create_project()

    def _on_project_create_submitted(self, name: str) -> None:
        """Inline named submission (Welcome page/Project tree): Create a workspace project directly
        (0.2.199-patch29dw will no longer pop up the general information form, leave the
        comments blank, and can be added through "Edit Comments" after creation)."""
        self._new_project_in_workspace(name.strip())

    def _new_project_in_workspace(
        self, name: str, fields: dict | None = None
    ) -> None:
        """Create a project under the default workspace (contract v1.3,create_project returns
        ProjectManager)."""
        try:
            self.manager = self.workspace.create_project(name)
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("New project failed"), str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - WorkspaceError Wait for unified prompts.
            InfoDialog.show_info(self, tr("New project failed"), describe_exception(exc))
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
        root = QFileDialog.getExistingDirectory(self, tr(
            "Select project "
            "directory",
        ), str(Path.home()))
        if not root:
            return
        self._open_root(Path(root))

    def save_project(self) -> None:
        try:
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("save failed"), str(exc))
            return
        self.statusBar().showMessage(tr("project saved"))

    def _open_root(self, root: Path) -> None:
        try:
            self.manager = ProjectManager.open_project(root)
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("Failed to open project"), str(exc))
            return
        self.recent.push(str(self.manager.root))
        self._rebind_shared_manager()
        self.refresh()

    def _rebind_shared_manager(self) -> None:
        """After the project object is replaced, let each panel share the same ProjectManager
        instance."""
        self.project_tree.manager = self.manager
        self.center_panel.set_manager(self.manager)
        self.pipeline.manager = self.manager
        if self.spectrum_panel is not None:
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
    # Experiment/Project actions.
    # ------------------------------------------------------------------
    def add_experiment(self) -> None:
        """Compatible entrance: equivalent to creating a new blank experiment (to import sample
        data, right-click the experiment and "Import sample data")."""
        self._create_experiment()

    def _segmented_import(self, exp_id: str, source: str) -> None:
        """Segmented collection import: Container directory (>= 2 subdirectories containing acqus)
        are merged into one sample data. exp_id is the current experiment; when it is empty, the
        backend creates a new experiment (G2B-011)."""
        if self.manager.project is None:
            InfoDialog.show_info(self, tr("hint"), tr("Please create or open a project first"))
            return
        source = source.strip()
        if not source:
            InfoDialog.show_info(self, tr(
                "hint",
            ), (
                tr(
                "Please select the segmented/duplicate experiment container "
                "directory",
            )
            ))
            return
        from gui.processing import is_segmented_container

        if not is_segmented_container(source):
            InfoDialog.show_info(
                self,
                tr("segmented/repeated experiment overlay import"),
                tr(
                    "The selected directory is not a segmented/repeat-experiment container (it "
                    "needs at least 2 subdirectories that each hold an acqus data segment; "
                    "non-data subdirectories were ignored; it is normal for the container itself "
                    "to have no "
                    "acqus)",
                ),
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
        """Batch import multiple data directory (background thread); group=True marks the same
        batch_id in the same batch."""
        import threading

        if self.manager.project is None or not exp_id or not folders:
            return
        self._append_log(
            tr("Start batch import: {p0} directory -> experiment {p1}", p0=len(folders), p1=exp_id)
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
                        tr("The batch import part failed:\n")
                        + "\n".join(
                            f"{item['folder']}: {item.get('error')}"
                            for item in failed
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - Errors are unified back to the main thread.
                self.import_failed.emit(describe_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_group_data_done(self, exp_id: str, data_id: str, status: str) -> None:
        """The group batch of certain data is completed: the left tree writes back the status
        (success/fail/jump over)."""
        self.project_tree.set_batch_status(exp_id, data_id, status)


    def _on_batch_import_progress(
        self, exp_id: str, folder: str, data_id: str, ok: bool, error: str
    ) -> None:
        """Batch import item-by-item progress (main thread): record the current item immediately,
        without waiting for the entire batch to be completed."""
        if ok:
            self._append_log(tr(" import in: {p0} → {p1}", p0=folder, p1=data_id))
        else:
            self._append_log(tr("  skipped/failed: {p0} - {p1}", p0=folder, p1=error))


    def _on_batch_import_done(
        self, exp_id: str, batch_id_value: str, count: int, results
    ) -> None:
        """Batch import completed (main thread): log item by item + refresh and select the
        experiment."""
        for item in results or []:
            if item.get("ok"):
                self._append_log(
                    tr(" import completion: {p0} → {p1}", p0=item['folder'], p1=item['data_id'])
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
                        summary = ",".join(
                            f"{key}={value}" for key, value in filled.items()
                        )
                        self._append_log(tr(" Autofill comments: {p0}", p0=summary))
                    quality = check_raw_quality(self.manager, exp_id, data_id)
                    self._append_log(tr(" Raw data quality:"))
                    self._append_log(format_quality_report(quality))
                    self._log_experiment_type_check(exp_id, data_id)
                # Autofill/Failure in quality inspection does not block batch import.
                except Exception:  # noqa: BLE001 -
                    pass
            else:
                self._append_log(
                    tr(" import failed: {p0} ({p1})", p0=item['folder'], p1=item.get('error'))
                )
        try:
            self.manager.save()
        except ProjectError:
            pass
        ok_count = sum(1 for item in results or [] if item.get("ok"))
        fail_count = (len(results or []) - ok_count)
        self._append_log(
            tr(
                "Batch import summary: Total {p0} data, success {p1} failure {p2} "
                "indivual",
                p0=count,
                p1=ok_count,
                p2=fail_count,
            )
        )
        if fail_count:
            for item in results or []:
                if not item.get("ok"):
                    self._append_log(
                        tr(" fail: {p0} → {p1}", p0=item.get('folder'), p1=item.get('error'))
                    )
        self._append_log(
                tr(
                "Batch import completed: experiment {p0} Group {p1}, common {p2} sample "
                "data",
                p0=exp_id,
                p1=batch_id_value,
                p2=count,
            )
        )
        self.refresh()
        self.project_tree.select_experiment(exp_id)
        self.center_panel.set_selection("experiment", exp_id)
        # 0.2.87: Refresh the comment bar immediately after batch importing auto-fill comments.
        self.center_panel.update_notes("experiment", exp_id, "")
        self._maybe_show_first_import_hint()

    # ------------------------------------------------------------------
    # DataGroup(schema 1.4).
    # ------------------------------------------------------------------
    def _data_ndim(self, exp_id: str, data_id: str) -> int:
        """Read data dimension from metadata (3D batch is not supported yet); fallback if unable to
        read 2."""
        try:
            meta_path = self._manager.data_metadata_path(exp_id, data_id)
            if meta_path is not None and meta_path.is_file():
                import json

                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                return int((metadata.get("dataset") or {}).get("ndim") or 2)
        # If you can't read it, press 2D to relax (don't block it by mistake).
        except Exception:  # noqa: BLE001 -
            pass
        return 2


    def _group_add_data(self, exp_id: str, group_id: str, data_ids: list) -> None:
        """Add multiple data to the data group (project.json is the only source, 0.2.164-patch1)."""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            self._append_log(tr("data group does not exist: {p0}", p0=group_id))
            return
        for data_id in data_ids or []:
            try:
                # 0.2.199-patch29hd: Batch only supports 2D spectrum for the time being -- adding 3D
                # data to the data group will trigger SMILE (unstable host power outage) during
                # batch processing, intercept the prompt directly here.
                if self._data_ndim(exp_id, str(data_id)) >= 3:
                    self._append_log(
                            tr(
                            "data {p0} It is a 3D spectrum. Batch only supports 2D for now and has "
                            "not been added to the data "
                            "group",
                            p0=data_id,
                        )
                    )
                    continue
                self.manager.add_to_group(exp_id, group_id, str(data_id))
                self._append_log(tr("data {p0} Joined data group {p1}", p0=data_id, p1=group_id))
            except Exception as exc:  # noqa: BLE001 - Single data failure continues.
                self._append_log(tr("Failed to join {p0}: {p1}", p0=data_id, p1=exc))
        self.manager.save()
        self.refresh()

    def _group_remove_data(
        self, exp_id: str, group_id: str, data_id: str
    ) -> None:
        """Move data out of the data group (project.json is the only source, 0.2.164-patch1)."""
        if self.manager.project is None:
            return
        self.manager.remove_from_group(exp_id, group_id, data_id)
        self.manager.save()
        self._append_log(tr("data {p0} Moved out of data group {p1}", p0=data_id, p1=group_id))
        self.refresh()

    def _group_rename(self, exp_id: str, group_id: str, title: str) -> None:
        """Rename the data group."""
        if self.manager.project is None:
            return
        self.manager.rename_data_group(exp_id, group_id, title)
        self.manager.save()
        self._append_log(tr("data group {p0} renamed to {p1}", p0=group_id, p1=title))
        self.refresh()

    def _group_delete(self, exp_id: str, group_id: str) -> None:
        """Delete a data group (only the group is removed, member data remains as individual
        data)."""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            return
        ok = ConfirmDialog.confirm(
            self,
            tr("delete data group"),
            tr(
                "delete data group {p0}?In the group {p1} data set(s) will be restored as "
                "individual data sets (the data themselves are not "
                "deleted).",
                p0=group_id,
                p1=len(group.data_ids),
            ),
        )
        if not ok:
            return
        self.manager.delete_data_group(exp_id, group_id)
        self.manager.save()
        self._append_log(tr("data group {p0} deleted (member restores single data)", p0=group_id))
        self.refresh()

    def _group_delete_with_members(self, exp_id: str, group_id: str) -> None:
        """Delete the data group together with all data in the group (the product is moved to the
        recycle bin and can be recovered)."""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            return
        ok = ConfirmDialog.confirm(
            self,
            tr("delete data group (including data)"),
            tr(
                "delete data group {p0}?In the group {p1} data set(s) will be moved to the trash "
                "together with the group "
                "(recoverable).",
                p0=group_id,
                p1=len(group.data_ids),
            ),
        )
        if not ok:
            return
        deleted = self.manager.delete_data_group_with_members(exp_id, group_id)
        self.manager.save()
        self._append_log(
            tr(
                "data group {p0} deleted, together with {p1} data moved to "
                "trash",
                p0=group_id,
                p1=len(deleted),
            )
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
        """Data group batch processing: background thread execution, progress output via log."""
        if self.manager.project is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None or not group.data_ids:
            self._append_log(
                tr(
                "data group {p0} Without member data, batch processing cannot be "
                "performed",
                p0=group_id,
            )
            )
            return
        step_label = " → ".join(steps) if steps else tr("(null)")
        ref_text = (
            tr(", reference data {p0}", p0=reference_data_id) if reference_data_id else ""
        )
        group_scope = self.log_panel.scope_key("group", exp_id, "", group_id)
        self._append_log(
            tr(
                "Start data group {p0} Batch processing: "
                "{p1}{p2}",
                p0=group_id,
                p1=step_label,
                p2=ref_text,
            ),
            scope=group_scope,
        )
        self.center_panel.group_page.set_progress(tr("Batch processing run..."))
        # 0.2.199-patch5: Group batch starts, each data in the tree group on the left displays
        # "Running".
        self.project_tree.clear_batch_status()
        for data_id in group.data_ids:
            self.project_tree.mark_running(exp_id, data_id)

        _done_map = {
            "import": tr("Already imported"),
            "fid": tr("Generated FID"),
            "spectrum": tr("Spectrum has been generated"),
            "peaks": tr("Already peak picking"),
        }

        def _status_label(status: str) -> str:
            if status == "failed":
                return tr("Failed")
            if status == "skipped":
                return tr("Skipped")
            if status == "cancelled":
                return tr("Canceled")
            return _done_map.get(str(steps[-1]) if steps else "", tr("Success"))

        def on_data_done(per: dict) -> None:
            """0.2.199-patch29hf: Every time the data is completed, the tree status is written back
            to the main thread (not waiting for the entire batch)."""
            data_id = per.get("data_id", "")
            _st = _status_label(per.get("status", ""))
            self.group_data_done.emit(exp_id, data_id, _st)
            data_scope = self.log_panel.scope_key("data", exp_id, data_id)
            for _lg in per.get("logs") or []:
                self.log_append_requested.emit(_lg, data_scope)

        def worker() -> None:
            try:
                self.controller.set_manager(self.manager)
                _t0 = time.monotonic()
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
                _elapsed = time.monotonic() - _t0
                _success = int(summary.get("success", 0))
                _total = int(summary.get("total", 0))
                info = (
                    tr(
                        "Data group {p0} batch finished: {p1} ok, {p2} failed, "
                        "{p3:.1f}s",
                        p0=group_id,
                        p1=_success,
                        p2=len(failed),
                        p3=_elapsed,
                    )
                )
                if _total:
                    info += tr("(common {p0})", p0=_total)
                if cancelled:
                    info += tr(" · Stopped,{p0} unprocessed", p0=len(cancelled))
                if skipped:
                    info += tr(" - skipped (type/condition mismatch):") + ",".join(skipped)
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
            except Exception as exc:  # noqa: BLE001 - Errors are unified back to the main thread.
                self.import_failed.emit(describe_exception(exc))
            finally:
                # 0.2.199-patch29c: No longer in the worker set_progress/refresh (cross-thread touch
                # control); all moved to _on_batch_run_done (queue signal, main thread).
                self.batch_run_done.emit()

        import threading

        # 0.2.199-patch6: Clear the last cancellation flag before starting a new task.
        from backend.runtime import clear_cancel

        clear_cancel()
        threading.Thread(target=worker, daemon=True).start()

    def _import_experiment_async(self, data: dict) -> None:
        """The background thread executes the import (three-step interface import_data) to avoid
        blocking when copying large files UI."""
        import threading

        source = data.get("source", "")
        exp_id = data.get("experiment_id", "")
        self._append_log(
            tr(
            "Start importing: {p0} (experiment "
            "{p1})",
            p0=source,
            p1=exp_id or 'automatically created',
        )
        )

        def worker() -> None:
            try:
                explicit_segmented = bool(data.get("segmented", False))
                if explicit_segmented:
                    resolved_source = source
                    segmented = True
                else:
                    # Task F: Ignore non-data sub-directories; containers >= 2 data sub-directories
                    # are segmented, and exactly 1 is imported as a single data directory.
                    from gui.processing import resolve_import_source

                    resolved_source, segmented = resolve_import_source(source)
                if segmented:
                    # 0.2.108/G2B-011: Segmented collection and import (the container directory is
                    # merged into one piece of data and imported into the current experiment; when
                    # exp_id is empty, the backend is created).
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
                            raise ProjectError(tr("project not loaded"))
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
                    # 0.2.86: Automatically populate comments after import + Check and report on raw
                    # data quality.
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
                    # Autofill/Import will not be blocked if quality inspection fails.
                    except Exception:  # noqa: BLE001 -
                        self._last_auto_fill = {}
                        self._last_raw_quality = None
                self.manager.save()
                self.import_finished.emit(result)  # Return to the main thread to refresh UI.
            # Errors are unified back to the main thread prompt.
            except Exception as exc:  # noqa: BLE001 -
                self.import_failed.emit(describe_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_import_failed(self, message: str) -> None:
        """The main thread handles import failures; Kinetics uses an explicit reject import
        prompt."""
        if message.startswith("KineticsUnsupportedError:"):
            detail = message.partition(":")[2].strip() or message
            self._append_log(tr("import rejected: {p0}", p0=detail))
            InfoDialog.show_info(self, tr("Import is not supported"), detail)
            return
        self._append_log(tr("import failed: {p0}", p0=message))
        InfoDialog.show_info(self, tr("import failed"), message)

    def _on_import_done(self, result: ImportResult) -> None:
        """After the import is successful, refresh and select the new experiment; display
        warnings."""
        data_id = getattr(result, "data_id", "") or ""
        self._append_log(
            tr(
                "import completion: {p0}/{p1} ({p2} document, {p3} bytes, run "
                "{p4})",
                p0=result.experiment_id,
                p1=data_id or '-',
                p2=getattr(result, 'file_count', 0),
                p3=getattr(result, 'total_bytes', 0),
                p4=result.run_id,
            )
        )
        for warning in result.warnings:
            self._append_log(tr(" hint: {p0}", p0=warning))
        quality = getattr(self, "_last_raw_quality", None) or {}
        auto_fill = getattr(self, "_last_auto_fill", None) or {}
        if auto_fill:
            summary = ",".join(f"{key}={value}" for key, value in auto_fill.items())
            self._append_log(tr("Autofill comments: {p0}", p0=summary))
        if quality:
            from gui.raw_quality import format_quality_report

            self._append_log(tr("Raw data quality:"))
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
        # 0.2.112: Clear the import form (name/path) after successful import to facilitate
        # continuous import.
        self.center_panel.experiment_page.clear_import_form()
        self.refresh()
        if exp_id:
            self.project_tree.select_experiment(exp_id)
            # 0.2.87: Refresh the comment bar immediately after importing auto-fill comments.
            self.center_panel.update_notes("experiment", exp_id, "")
        self._maybe_show_first_import_hint()
        warnings = list(result.warnings)
        quality = getattr(self, "_last_raw_quality", None) or {}
        if quality and quality.get("issues"):
            warnings.extend(
                tr("Quality warning: {p0}", p0=issue) for issue in quality["issues"]
            )
        if warnings:
            InfoDialog.show_info(
                self, tr("import completed (prompt)"), "\n".join(warnings)
            )

    def _log_experiment_type_check(self, exp_id: str, data_id: str) -> None:
        """After importing, you are prompted to check data type identification
        (0.2.199-patch29fd)."""
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
        # 0.2.199-patch29fd-Fixed: No matter whether it is hit or not, always prompt to check the
        # data type.
        self._append_log(
            tr(
                "Data type check: {p0} (confidence {p1:.2f}); please verify the data type is "
                "correct (it can be edited in the sample-data "
                "comment)",
                p0=name,
                p1=conf,
            )
        )

    def _read_data_metadata(self, exp_id: str, data_id: str) -> dict:
        """Read sample data metadata.json (returns empty dict if missing)."""
        if self.manager.project is None:
            return {}
        try:
            meta_path = self.manager.data_metadata_path(exp_id, data_id)
            if meta_path is None or not meta_path.is_file():
                return {}
            import json

            return json.loads(meta_path.read_text(encoding="utf-8"))
        # If the metadata cannot be read, it will be treated as empty.
        except Exception:  # noqa: BLE001 -
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
            InfoDialog.show_info(self, tr("rename failed"), str(exc))
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
            tr("delete experiment"),
            tr(
                "delete experiment {p0} and its output files?\nIt will be moved to the system "
                "trash and can be restored.\n(the WorkflowRun audit trail is "
                "kept)",
                p0=exp_id,
            ),
        )
        if not confirmed:
            return
        try:
            self.manager.delete_experiment(exp_id)
            self.manager.save()
            self._append_log(
                tr(
                "experiment {p0} Moved to system trash (can be restored from "
                "trash)",
                p0=exp_id,
            )
            )
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("delete experiment failed"), str(exc))
            return
        self.refresh()

    def about(self) -> None:
        InfoDialog.show_info(
            self,
            tr("About NMRForge"),
            tr(
                "NMRForge: an automated processing, parameter-optimisation and quality-control "
                "platform for Bruker 2D/3D NMR.\nFour columns: project tree / processing "
                "pipeline / task log / spectrum viewer.\n\nAxis-order convention (internally "
                "everything is organised in "
                "the logical order F1/F2/F3):\n- 2D: F1 = indirect dimension, F2 = direct "
                "dimension; 3D: F1 = first indirect, F2 = second indirect, F3 = direct "
                "dimension.\n- NMRPipe stores 3D files in the standard order (F2,F1,F3); loading "
                "transposes them into the logical order\n  (the \"axis reorder\" log line reports "
                "exactly this and is normal behaviour).\n- Dimensions of the same nucleus (e.g. 2D "
                "1H-1H, HNN with two 15N) are told apart by an x/y/z subscript\n  following the "
                "priority \"direct > acqu2 > acqu3\" (e.g. Nx = the N of the HSQC, Ny = the "
                "sequential "
                "N).",
            ),
        )

    def show_tutorial(self) -> None:
        """Help -> Usage tutorial: open the shipped tutorial (text in the current language)."""
        TutorialDialog(self).exec()

    # ------------------------------------------------------------------
    # Processing action.
    # ------------------------------------------------------------------
    def _on_view_step_log(self, step_id: str) -> None:
        """Position the log panel: append the marked line and expand it (append automatically
        scrolls to the bottom)."""
        self._append_log(
            tr("── {p0} run log (most recent)──", p0=STEP_LABEL.get(step_id, step_id))
        )

    def _maybe_show_first_import_hint(self) -> None:
        """The "Next" highlighted prompt after the first import only appears once (status save
        settings)."""
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

        from qtcompat.QtWidgets import QFileDialog

        default_dir = str(self.workspace.root)
        file_path, _ = QFileDialog.getOpenFileName(
            self, tr("Select fid file"), default_dir,
            tr("FID (*.fid);; All files (*)"),
        )
        if file_path:
            self._run_standalone_check([_P(file_path)], "fid")
            return
        folder = QFileDialog.getExistingDirectory(
            self, tr("Select fid folder"), default_dir
        )
        if folder:
            self._run_standalone_check([_P(folder)], "fid")

    def _run_standalone_spectrum_quality(self) -> None:
        """Other menu: pick spectrum file, run quality evaluation."""
        from pathlib import Path as _P

        from qtcompat.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self, tr("Select spectrum file"), str(self.workspace.root),
            tr("NMRPipe (*.ft2 *.ft3 *.fdf);; All files (*)"),
        )
        if file_path:
            self._run_standalone_check([_P(file_path)], "spectrum")

    def _run_standalone_check(self, paths, kind: str) -> None:
        """Run check in worker thread, emit report to top-level global log. 0.2.199-patch29em:
        First jump to the top level (workspace root node), switch the log panel to
        NMRForgeWorkspace global scope, then execute in the background and output line by line."""
        top = self.project_tree.tree.topLevelItem(0)
        if top is not None:
            self.project_tree.tree.setCurrentItem(top)
        self.log_panel.set_scope("global", "", "", "")
        self._log_kind = "global"

        def worker() -> None:
            scope = self._log_scope()
            target = ", ".join(str(p) for p in paths)
            label = tr(
                "Data quality "
                "inspection",
            ) if kind == "fid" else tr(
                "spectrum quality "
                "assessment",
            )
            self.log_append_requested.emit(
                tr("ongoing{p0}: {p1}", p0=label, p1=target), scope
            )
            try:
                if kind == "fid":
                    from workflow.direct_diagnostics import (
                        run_fid_diagnostics_paths,
                    )

                    res = run_fid_diagnostics_paths(paths)
                    lines = (
                        ["== " + tr("Data quality inspection")
                         + tr("(Independent entrance) ==")]
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
                    tr("{p0}Finish: {p1}", p0=label, p1=target), scope
                )
            except Exception as exc:  # noqa: BLE001
                self.log_append_requested.emit(
                    tr("Detection failed: {p0}", p0=describe_exception(exc)),
                    scope,
                )

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def _open_settings(self) -> None:
        """Open the software settings dialog (stage C3)."""
        from gui.dialogs import SettingsDialog

        SettingsDialog(self).exec()

    def _on_memory_guard(self, message: str) -> None:
        """SMILE Out of memory: log + popup (main thread)."""
        self._append_log(message)
        InfoDialog.show_info(self, tr("Out of memory"), message)

    def _on_batch_summary(self, summary: dict) -> None:
        """Batch processing summary pop-up window: double-click the failed item to locate the data
        (stage C1)."""
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
        """Drag and drop import: directory containing acqus is regarded as a Bruker dataset, import
        the current experiment or create a new experiment."""
        imported = 0
        for path in paths:
            if not path.is_dir():
                continue
            try:
                from gui.processing import resolve_import_source
                from workflow.import_workflow import ImportWorkflowError

                resolved, seg = resolve_import_source(path)
            except ImportWorkflowError as exc:
                InfoDialog.show_info(self, tr("import failed"), str(exc))
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
            self._append_log(tr("Drag and drop import: {p0} data directory", p0=imported))

    def _open_manual_dialog(self, step_id: str) -> None:
        """Manual processing entry: Open the script Editor/fid editor according to the steps."""
        exp_id = self.project_tree.current_experiment_id()
        if not exp_id:
            InfoDialog.show_info(self, tr(
                "hint",
            ), tr(
                "Please select an experiment on the left "
                "first",
            ))
            return
        entry = self.manager.project.experiment(exp_id) if self.manager.project else None
        if entry is None:
            return
        label = f"{entry.title or entry.id} ({exp_id})"
        data_node = self._current_data_node(entry)
        if data_node is None:
            InfoDialog.show_info(self, tr(
                "hint",
            ), (
                tr(
                "This experiment does not have sample data yet, please import sample data "
                "first",
            )
            ))
            return
        data_id = getattr(data_node, "id", exp_id)
        if step_id == "fid":
            self._open_fid_editor(data_node, exp_id, data_id, label)
        elif step_id in ("spectrum", "script"):
            self._open_script_editor(data_node, exp_id, data_id, label)
        elif step_id == "peaks":
            InfoDialog.show_info(
                self,
                tr("peak table edit"),
                (
                    tr(
                    "To add/delete/edit peak table, please operate the peak table on the spectrum "
                    "panel on the right, and save it as a Poky.list "
                    "file",
                )
                ),
            )
        else:
            InfoDialog.show_info(self, tr(
                "Manual processing",
            ), (
                tr(
                "Manual entry for this step is not currently supported: "
                "{p0}",
                p0=step_id,
            )
            ))

    def _current_data_node(self, entry):
        """The currently selected sample data node (returns to the first one when not selected;
        returns None for blank experiments)."""
        nodes = list(getattr(entry, "data", None) or [])
        if not nodes:
            return None
        data_id = self.project_tree.current_data_id()
        if data_id:
            return next((n for n in nodes if getattr(n, "id", "") == data_id), nodes[0])
        return nodes[0]

    def _open_fid_editor(self, data_node, exp_id: str, data_id: str, label: str) -> None:
        """Fid.com Check/Revise/run(manual_fid_com / run_manual_fid_com)."""
        existing = self._existing_script_editor(data_id, "fid")
        if existing is not None:
            self._focus_script_editor(existing)
            return
        try:
            content = self.controller.manual_fid_com(
                data_node, exp_id=exp_id, data_id=data_id
            )
        except Exception as exc:  # noqa: BLE001 - The backend is missing unified prompts.
            InfoDialog.show_info(
                self, "fid.com", tr("Unable to get fid.com: {p0}", p0=describe_exception(exc))
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
        """Script editor: Priority will be given to existing scripts (those that have been
        automatically run will be displayed directly), if not, the default will be rendered ->
        edit/save/run."""
        existing = self._existing_script_editor(data_id, "spectrum")
        if existing is not None:
            self._focus_script_editor(existing)
            return
        try:
            scripts = self.controller.manual_scripts(
                data_node, params=None, exp_id=exp_id, data_id=data_id
            )
        except Exception as exc:  # noqa: BLE001 - The backend is missing unified prompts.
            from workflow.manual import ManualRunError

            if isinstance(exc, ManualRunError) and (
                tr(
                "perform the \"Generate FID\" step "
                "first",
            )
            ) in str(
                exc
            ):
                InfoDialog.show_info(self, tr("Please generate FID first"), str(exc))
            else:
                InfoDialog.show_info(
                    self, tr("Failed to load script"), describe_exception(exc)
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
        """Script editor "Run" -> background execution and registration."""
        dialog.run_requested.connect(
            lambda content: self._run_script_async(
                content, data_node, exp_id, data_id, script_name
            )
        )

    def _existing_script_editor(
        self, data_id: str, step: str
    ) -> ScriptEditorDialog | None:
        """Open editor with the same data and steps: reuse when it exists and is visible, and does
        not pop up repeatedly (0.2.193)."""
        dialog = self._script_editors.get((data_id, step))
        if dialog is None:
            return None
        if dialog.isVisible():
            return dialog
        self._script_editors.pop((data_id, step), None)
        return None

    def _focus_script_editor(self, dialog: ScriptEditorDialog) -> None:
        """Bring the open editor to the foreground (reuse when opening repeatedly, 0.2.193)."""
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _keep_script_dialog(
        self, dialog: ScriptEditorDialog, data_id: str, step: str
    ) -> None:
        """Holds a reference to the non-modal script editor and releases it after closing
        (0.2.192/0.2.193). The main interface is not locked when opened (show instead of exec);
        WA_DeleteOnClose + destroyed is guaranteed to be released when closed; press (data_id,
        step) to remove duplicates, and the same data and steps will not be repeated in pop-up
        windows."""
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
        """Run manual script in the background; forward the script output in real time, and refresh
        the main thread after completion."""
        import threading

        # 0.2.199-patch29d: Manually run the log according to the data scope, switch the selection
        # to non-string.
        manual_scope = self.log_panel.scope_key("data", exp_id, data_id)
        # 0.2.199-patch29h: script detection before running (line continuation
        # character/CRLF/BOM/Common errors such as missing output).
        from workflow.script_check import check_script

        check_warnings = check_script(content, script_name)
        if check_warnings:
            for w in check_warnings:
                self.log_append_requested.emit(tr("⚠ script check: {p0}", p0=w), manual_scope)
            if not ConfirmDialog.confirm(
                self,
                tr("script check found warning"),
                tr(
                    "{p0} Check before run and find {p1} "
                    "Questions:\n",
                    p0=script_name,
                    p1=len(check_warnings),
                )
                + "\n".join(f"· {w}" for w in check_warnings)
                + tr("Do you want to continue running anyway?"),
            ):
                self.log_append_requested.emit(
                    tr("run cancelled (script check warning)"), manual_scope
                )
                return
        self.log_append_requested.emit(
            tr("Start manual run: {p0} (data {p1})", p0=script_name, p1=data_id), manual_scope
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
                    message = tr("fid.com run completed: {p0}", p0=result)
                else:
                    result = self.controller.run_manual_spectrum(
                        data_node,
                        {script_name: content},
                        exp_id=exp_id,
                        data_id=data_id,
                        progress=forward,
                    )
                    message = tr("{p0} run complete: {p1}", p0=script_name, p1=result)
            except Exception as exc:  # noqa: BLE001 - Errors are unified back to the main thread.
                self.log_append_requested.emit(
                    tr("Manual run failed: {p0}", p0=describe_exception(exc)), manual_scope
                )
                self.manual_run_done.emit()
                return
            self.log_append_requested.emit(message, manual_scope)
            self.manual_run_done.emit()

        # 0.2.199-patch5: Manual operation starts, the data in the tree on the left shows "Running".
        self.project_tree.mark_running(exp_id, data_id)
        # 0.2.199-patch6: Clear the last cancellation flag before starting a new task.
        from backend.runtime import clear_cancel

        clear_cancel()
        threading.Thread(target=worker, daemon=True).start()

    def _on_pipeline_run_started(self, exp_id: str, data_id: str) -> None:
        """Processing starts: The data on the left tree displays "Running", and the log panel is
        switched to the target data/group scope (otherwise the progress log will only be
        buffered and not displayed, and it will look like the log has been swallowed)."""
        self.project_tree.mark_running(exp_id, data_id)
        try:
            group = (
                self.manager.group_of_data(exp_id, data_id)
                if self.manager is not None and self.manager.project is not None
                else None
            )
        except Exception:  # noqa: BLE001 - Group parsing failed by single data scope.
            group = None
        if group is not None:
            self.log_panel.set_scope("group", exp_id, "", group.id)
        else:
            self.log_panel.set_scope("data", exp_id, data_id)

    def _on_pipeline_run_finished(self) -> None:
        """Pipeline Refresh the left tree after the processing step is completed/middle/ spectrum
        panel (main thread)."""
        self.project_tree.clear_running()
        self.refresh()
        self.center_panel.refresh()
        # 0.2.88: Do not automatically display the spectrum, just refresh the file list (the "Show
        # spectrum" button has appeared).
        if self.spectrum_panel is not None:
            self.spectrum_panel.refresh()

    def _show_spectrum_from_pipeline(self, _step_id: str = "") -> None:
        """"Show spectrum" button: Displays the final spectrum of the current data in the spectrum
        panel on the right."""
        exp_id = self.pipeline.current_experiment_id()
        data_id = self.pipeline.current_data_id
        if not (exp_id and data_id) or self.manager.project is None:
            return
        panel = self._ensure_spectrum_panel()
        panel.set_context(exp_id, data_id)
        if not panel.load_current_spectrum():
            InfoDialog.show_info(self, tr(
                "hint",
            ), tr(
                "There is no spectrum file for this sample data "
                "yet",
            ))

    def _on_rank1_rerun(self) -> None:
        """"Rerun by Rank1": Rerun the final spectrum using the Rank1 script scanned by SMILE
        (Option B)."""
        import threading

        exp_id = self.pipeline.current_experiment_id()
        data_id = self.pipeline.current_data_id
        if not (exp_id and data_id):
            return
        self._append_log(tr(
            "Press Rank1 to rerun the final script and start: "
            "{p0}/{p1}",
            p0=exp_id,
            p1=data_id,
        ))

        def worker() -> None:
            try:
                out = self.controller.rerun_smile_rank1(
                    exp_id,
                    data_id,
                    progress=lambda msg: self.manual_run_log.emit(msg),
                )
                self.manual_run_log.emit(tr("Rank1 rerun completed: {p0}", p0=out))
            except Exception as exc:  # noqa: BLE001 - Failure to write log does not throw.
                self.manual_run_log.emit(tr("Rank1 rerun failed: {p0}", p0=exc))
            self.manual_run_done.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_batch_run_done(self) -> None:
        """Data group batch processing is completed: clear the running mark, progress and refresh
        (main thread). 0.2.199-patch29c: Batch progress cleaning and refresh are moved here from
        worker finally (batch_run_done is a queue signal, and the slot is executed in the main
        thread)."""
        self.project_tree.clear_running()
        self.center_panel.group_page.set_progress("")
        self.refresh()
        self.center_panel.refresh()

    def _on_manual_run_done(self) -> None:
        """Refresh the Pipeline (main thread) after the manual script is completed."""
        self.project_tree.clear_running()
        self.refresh()
        self.center_panel.refresh()

    def _on_peaks_saved(self) -> None:
        """After the peak table is written back, refresh the Pipeline (peaks status) and record the
        log."""
        self._append_log(tr("peak table saved and registered manual_peaks run"))
        self.center_panel.refresh()

    def _rename_project(self, new_name: str = "") -> None:
        """Rename the current project: Prioritize WorkspaceManager.rename_project(directory
        +name)."""
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
                self, tr(
                    "rename project",
                ), tr(
                    "{p0}Currently only the name of project.json is "
                    "updated",
                    p0=exc,
                )
            )
            self.manager.project.name = new_name
            try:
                self.manager.save()
            except ProjectError as exc2:
                InfoDialog.show_info(self, tr("rename project failed"), str(exc2))
                return
        except Exception as exc:  # noqa: BLE001 - WorkspaceError Wait for unified prompts.
            InfoDialog.show_info(self, tr("rename project failed"), describe_exception(exc))
            return
        self._rebind_shared_manager()
        self.refresh()
        self.statusBar().showMessage(tr("project has been renamed to {p0}", p0=new_name))

    def _delete_project(self) -> None:
        if self.manager.project is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            tr("delete project"),
            tr(
                "delete project {p0} and all of its sample data and outputs?\nThe project will be "
                "moved to the system trash and can be restored (the audit history is kept)\npath: "
                "{p1}",
                p0=self.manager.project.name,
                p1=str(self.manager.root),
            ),
        )
        if not confirmed:
            return
        try:
            project_name = self.manager.root.name if self.manager.root is not None else ""
            if project_name:
                self.workspace.delete_project(project_name)
        except NotImplementedError as exc:
            InfoDialog.show_info(
                self, tr(
                    "delete project",
                ), (
                    tr(
                    "{p0}Currently only project is closed and directory is "
                    "retained",
                    p0=exc,
                )
                )
            )
        except Exception as exc:  # noqa: BLE001 - WorkspaceError Wait for unified prompts.
            InfoDialog.show_info(self, tr("delete project failed"), describe_exception(exc))
            return
        if hasattr(self.recent, "remove") and self.manager.root is not None:
            self.recent.remove(str(self.manager.root))
        self.manager.close()
        self.refresh()
        self.center_panel.welcome_page.refresh()
        self.statusBar().showMessage(
            tr(
            "The project has been moved to the system trash and can be restored from "
            "trash",
        )
        )

    def _create_experiment(self) -> None:
        """New blank experiment (menu/Project/Right click on blank space): named inline in the
        project tree (no pop-up window)."""
        if self.manager.project is None:
            InfoDialog.show_info(self, tr("hint"), tr("Please create or open a project first"))
            return
        self.project_tree.begin_create_experiment()

    def _on_experiment_create_submitted(self, title: str) -> None:
        """Project tree inline named submission: directly create a blank experiment
        (0.2.199-patch29dw no longer pops up the regular information form, the comments are left
        blank, and can be added through "Edit Comments" after creation)."""
        if self.manager.project is None:
            return
        self._create_experiment_with_title_and_fields(title.strip())

    def _import_data_with_options(
        self, exp_id: str, name: str, source: str, copy: bool
    ) -> None:
        """Embedded import form in the middle panel: Import sample data (can be named) according to
        specified experiments."""
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
        """Right-click the data to rename: Prioritize manager.rename_data to drop to the disk; if
        missing, directly write the title and prompt."""
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
            except Exception as exc:  # noqa: BLE001 - Unified prompt for backend exceptions.
                InfoDialog.show_info(self, tr("rename data failed"), str(exc))
                return
        elif data_entry is not None:
            # Backend rename_data has not been landed: write DataEntry.title directly to the disk.
            data_entry.title = new_name
        try:
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("rename data failed"), str(exc))
            return
        self.project_tree.refresh()

    def _delete_data(self, exp_id: str, data_id: str) -> None:
        """Delete sample data (do not delete experiments); confirm + manager.delete_data + save +
        refresh."""
        if self.manager.project is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            tr("delete sample data"),
            tr(
                "delete sample data {p0} and its output files?\nIt will be moved to the system "
                "trash and can be restored.\n(the WorkflowRun audit trail is "
                "kept)",
                p0=data_id,
            ),
        )
        if not confirmed:
            return
        try:
            self.manager.delete_data(exp_id, data_id)
            self.manager.save()
            self._append_log(
                tr(
                "sample data {p0} Moved to system trash (can be restored from "
                "trash)",
                p0=data_id,
            )
            )
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("Delete sample data failed"), str(exc))
            return
        self.refresh()

    def _create_experiment_with_title(self, title: str) -> None:
        """Embedded form in middle panel: Create a new blank experiment."""
        self._create_experiment_with_title_and_fields(title.strip())

    def _create_experiment_with_title_and_fields(
        self, title: str, fields: dict | None = None
    ) -> None:
        """Create a blank experiment by title (can have general information fields)."""
        if self.manager.project is None:
            InfoDialog.show_info(self, tr("hint"), tr("Please create or open a project first"))
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
            InfoDialog.show_info(self, tr("Failed to create new experiment"), str(exc))
            return
        self.refresh()
        self.project_tree.select_experiment(entry.id)

    def _import_data_for(self, exp_id: str) -> None:
        """Import sample data under the specified experiment."""
        if self.manager.project is None:
            InfoDialog.show_info(self, tr("hint"), tr("Please create or open a project first"))
            return
        samples = [(s.sample_id, s.name) for s in self.manager.project.samples]
        dialog = ImportExperimentDialog(self, samples=samples)
        if dialog.exec() != ImportExperimentDialog.DialogCode.Accepted:
            return
        data = dialog.result_data()
        data["experiment_id"] = exp_id
        self._import_experiment_async(data)



    def _on_data_action(self, action: str, data_id: str) -> None:
        """Three-step operation by right-clicking on sample data: generate FID / generate spectrum
        / delete sample data."""
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
        """Edit project/experiment/Sample data annotation(middle top comment bar "Edit Comment")."""
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
            "project": tr("project comments"),
            "experiment": tr("experiment notes"),
            "data": tr("sample data annotation"),
        }.get(kind, tr("Comment"))
        if kind == "project":
            current = sample_note_fields(self.manager.project)
        elif kind == "experiment":
            current = experiment_note_fields(self.manager.project, exp_id)
        else:
            kind = "data"
            current = data_note_fields(self.manager.project, exp_id, data_id)
        dialog = NotesDialog(self, tr("edit{p0}", p0=title), kind, current)
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
            previous_exptype = str(
                (current or {}).get("experiment_type", "") or ""
            )
            if exptype and exptype != previous_exptype:
                from workflow.import_workflow import apply_user_experiment_type

                if apply_user_experiment_type(
                    self.manager, exp_id, data_id, exptype
                ):
                    self._append_log(tr("Data type updated by user selection: {p0}", p0=exptype))
        try:
            self.manager.save()
        except ProjectError as exc:
            InfoDialog.show_info(self, tr("save failed"), str(exc))
            return
        self.center_panel.update_notes(kind, exp_id, data_id)
        self._append_log(tr("saved{p0}", p0=title))

    # ------------------------------------------------------------------
    # View actions.
    # ------------------------------------------------------------------
    def _toggle_left(self, checked: bool) -> None:
        self.project_tree.setVisible(checked)

    def _toggle_pipeline(self, checked: bool) -> None:
        self.pipeline.setVisible(checked)

    def _toggle_spectrum(self, checked: bool) -> None:
        if self.spectrum_panel is not None:
            self.spectrum_panel.setVisible(checked)

    def _toggle_spectrum_expand(self, expanded: bool) -> None:
        """Spectrum enlargement: Hide the three parts on the left (Project tree/Pipeline/Log),
        spectrum fills the window."""
        for widget in (self.project_tree, self.center_panel, self.log_panel):
            widget.setVisible(not expanded)

    # ------------------------------------------------------------------
    # Contextual linkage.
    # ------------------------------------------------------------------
    def _open_spectrum_from_tree(self, path: str) -> None:
        """Double-click the spectrum file in the tree: the spectrum panel on the right directly
        displays and loads the peak table."""
        target = Path(path)
        panel = self._ensure_spectrum_panel()
        if panel.open_with_peaks(target):
            self.statusBar().showMessage(tr("Opened: {p0}", p0=target.name))
        else:
            # 0.2.199-patch29hz: It turns out that the silence failed, and the user thought the
            # click was invalid.
            self.statusBar().showMessage(tr("Unable to open spectrum: {p0}", p0=target.name))
            self._append_log(
                tr(
                    "Unable to open spectrum file (format not supported or file damaged): "
                    "{p0}",
                    p0=target,
                )
            )

    def _open_terminal(self, path: str) -> None:
        """Open directory in the terminal (csh is preferred, so that you can run the NMRPipe
        command directly)."""
        from gui.project_tree import open_in_terminal

        if not open_in_terminal(path):
            InfoDialog.show_info(self, tr("hint"), tr("No available terminal program found"))

    def _open_path(self, path: str) -> None:
        """Use the system file manager to open the directory (double click/Right click data/son
        file folder) and keep Pipeline in the middle."""
        from qtcompat.QtCore import QUrl
        from qtcompat.QtGui import QDesktopServices

        target = Path(path)
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        # Maintain the intermediate Pipeline context (switch back to the sample data page when the
        # experiment is currently selected).
        exp_id = self.project_tree.current_experiment_id()
        data_id = self.project_tree.current_data_id()
        if exp_id:
            self.center_panel.set_selection("data", exp_id, data_id or "")
        self.statusBar().showMessage(tr("Opened: {p0}", p0=target))

    def _on_open_experiment(self, exp_id: str) -> None:
        self.center_panel.set_selection("experiment", exp_id, "")
        if self.spectrum_panel is not None:
            self.spectrum_panel.set_context(exp_id, "")
        self.statusBar().showMessage(
                tr(
                "experiment {p0}: Double-click to view the spectrum file, and the pipeline in the "
                "middle displays the processing "
                "steps",
                p0=exp_id,
            )
        )

    _DATA_STATUS_TEXT = {
        "imported": tr("Already imported"),
        "fid_ready": tr("FID Ready"),
        "processed": tr("Processed"),
        "picked": tr("Already peak picking"),
        "analyzed": tr("Analyzed"),
        "registered": tr("Registered"),
    }

    def _update_context_bar(self) -> None:
        """Top context bar: Project / Experiment / Data + status summary."""
        if self.manager.project is None:
            self.context_bar.setText(tr("project not open"))
            return
        exp_id = self.project_tree.current_experiment_id()
        current = self.project_tree.tree.currentItem()
        data_id = self.project_tree.current_data_id()
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
            parts.append(tr(
                "{p0} ({p1} "
                "data)",
                p0=label,
                p1=len(group.data_ids or []),
            ) if group else label)
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
            except Exception:  # noqa: BLE001 - Context parsing failure guarantee.
                label = data_id
            parts.append(f"{label} · {status_text}" if status_text else label)
        self.context_bar.setText(" / ".join(parts))

    def _locate_pipeline(self, exp_id: str, data_id: str) -> None:
        """Spectrum panel "Locate in Pipeline": Select the tree node and switch to the processing
        page."""
        self.project_tree.select_data(exp_id, data_id)
        self.center_panel.set_selection("data", exp_id, data_id)

    def _update_context(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """Select changes on the left -> Display according to selected type in the middle, refresh
        around data on the right, log switches scope."""
        self.center_panel.set_selection(kind, exp_id, data_id, group_id)
        # Patch29ht: The panel may not have been built yet (delayed build), and the application
        # context will be filled in by the build finisher when ready.
        if self.spectrum_panel is not None:
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
        """Before closing the application, terminate all back-end tasks and clean up orphan
        processes to avoid leaving them behind next time."""
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
            except Exception:  # noqa: BLE001 - Configuration read failure does not block cleanup.
                bin_dir = ""
            cleanup_orphan_tasks(
                bin_dir=bin_dir or None,
                workspace=str(self.manager.root or ""),
            )
        except Exception:  # noqa: BLE001 - Failure to close cleanup does not block exit.
            pass
        super().closeEvent(event)

    def _on_stop_requested(self) -> None:
        """Stop the current task: terminate the process tree + request cancellation of memory
        calculation + clean up orphan processes."""
        from backend.runtime import (
            cleanup_orphan_tasks,
            request_cancel,
            terminate_current_tasks,
        )

        killed = terminate_current_tasks()
        # 0.2.199-patch6: There is no child process to kill in the memory phase search. You need to
        # explicitly request cancellation. Memory Computing will exit at the checkpoint; then clean
        # up the orphan processes left behind by the last abnormal exit.
        request_cancel()
        orphan_count = 0
        try:
            from backend.config import load_config, nmrpipe_path

            bin_dir = str(nmrpipe_path(load_config()) or "")
        except Exception:  # noqa: BLE001 - Configuration read failure does not block cleanup.
            bin_dir = ""
        try:
            orphan_count = cleanup_orphan_tasks(
                bin_dir=bin_dir or None,
                workspace=str(self.manager.root or ""),
            )
        except Exception:  # noqa: BLE001 - Cleaning fails and does not block.
            orphan_count = 0
        if killed or orphan_count:
            self._append_log(
                tr(
                    "Current task has been stopped ({p0} Process tree, clean up the residue {p1} "
                    "item(s); cancellation of the memory computation was requested, exiting "
                    "shortly)",
                    p0=killed,
                    p1=orphan_count,
                )
            )
        else:
            self._append_log(
                tr(
                "There are currently no tasks running (cancellation of memory calculations has "
                "been requested)",
            )
            )

    def _append_log(self, message: str, scope: str | None = None) -> None:
        self.log_panel.append(message, scope=scope)
        self.log_panel.setVisible(True)

    def _log_scope(self) -> str:
        """The log scope key corresponding to the currently selected context."""
        return self.log_panel.scope_key(
            self._log_kind,
            self._log_exp_id,
            self._log_data_id,
            self._log_group_id,
        )

    # ------------------------------------------------------------------
    # Refresh.
    # ------------------------------------------------------------------
    def _current_experiment_id(self) -> str | None:
        exp_id = self.project_tree.current_experiment_id()
        return exp_id or None

    def refresh(self) -> None:
        """Refresh the window title, recent items menu and left tree."""
        self._refresh_recent_menu()
        self.project_tree.refresh()
        project = self.manager.project
        if project is None:
            self.setWindowTitle(tr("NMRForge - welcome"))
            self.statusBar().showMessage(tr(
                "Create a new project or open a project to start "
                "working",
            ))
            self.center_panel.welcome_page.refresh()
            self.center_panel.set_selection("workspace", "", "")
            if self.spectrum_panel is not None:
                self.spectrum_panel.set_context("", "")
            self.main_splitter.setVisible(True)  # The welcome page is displayed in three columns.
            self._update_context_bar()
            return
        self.setWindowTitle(f"NMRForge - {project.name}")
        self.statusBar().showMessage(tr("project: {p0}", p0=self.manager.root))
        # Open/After creating a new project, the first experiment will be focused by default..
        active_exps = [e for e in project.experiments if not getattr(e, "trashed", False)]
        if active_exps and not self.center_panel.current_experiment_id():
            self.project_tree.select_experiment(active_exps[0].id)
        self._update_context_bar()

    @staticmethod
    def run() -> int:
        """Start the Qt application (called by main.py)."""
        import sys

        app = QApplication(sys.argv)
        from gui.dialogs import install_dialog_centering
        from ui_support.theme import app_icon, apply_dark_theme

        install_dialog_centering(app)
        apply_dark_theme(app)
        # 0.2.199-patch29eq:GNOME Match the running window to.desktop by desktop file name, task
        # bar/The launcher will display the correct icon (the same applies to terminal startup).
        app.setApplicationName("NMRForge")
        try:
            app.setDesktopFileName("NMRForge")
        except AttributeError:
            pass
        _icon = app_icon()
        if _icon is not None:
            app.setWindowIcon(_icon)
        # 0.2.199-patch29ht(user): The window appears first (the spectrum panel is built later).
        # After the first frame, heavy modules are imported in the background and the panel is
        # rebuilt. During this period, the user can use other functions normally.
        window = MainWindow(defer_spectrum_panel=True)
        window.show()
        QTimer.singleShot(0, window._finish_startup)
        code = app.exec()
        # 0.2.199: Qt binding will traverse the dangling packaging pointer (measured as sip under
        # PyQt6 cleanup_on_exit -> sip_api_get_address(0x1e80)) at the end of the interpreter,
        # resulting in SIGSEGV. The phenomenon is a core dump when closing the main window and
        # exiting. After the event loop returns, before Python enters Py_FinalizeEx, explicitly
        # destroy all top-level windows and process deleteLater, letting Qt The object tree is
        # destroyed in order while sip is still tracking, bypassing end-of-process accesses to
        # invalid C++ object wrappers.
        try:
            for _w in list(app.topLevelWidgets()):
                try:
                    _w.close()
                    _w.deleteLater()
                except RuntimeError:  # pragma: no cover - Destroyed.
                    pass
            app.processEvents()
            app.sendPostedEvents(None, 0)
            app.processEvents()
        except Exception:  # noqa: BLE001 - Cleanup failure does not block exit.
            pass
        return code
