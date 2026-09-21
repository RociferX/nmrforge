"""Dashboard panel: project/Experiment overview(GUI_ARCHITECTURE_VISION §11-12). -
ProjectDashboard: project statistics (experiment/sample data/Processing completion) + recent
runs + new experiment form; - ExperimentDashboard: sample data list (data status of each sample)
+ import sample data form. Data source: core.project(ProjectManager); running history comes from
workflow_runs."""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtCore import QEvent, QPoint, Qt, QTimer
from qtcompat.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from gui.dialogs import InfoDialog
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_MUTED, TEXT_PRIMARY


def _active_data_of(exp) -> list:
    """Data entries that were not soft deleted under the experiment."""
    return [
        d for d in (getattr(exp, "data", None) or []) if not getattr(d, "trashed", False)
    ]


def _data_count(project) -> int:
    return sum(
        len(_active_data_of(exp))
        for exp in project.experiments
        if not getattr(exp, "trashed", False)
    )


def _data_processed(project) -> int:
    count = 0
    for exp in project.experiments:
        if getattr(exp, "trashed", False):
            continue
        for data in _active_data_of(exp):
            status = getattr(data, "status", "") or ""
            if status in ("fid_ready", "processed"):
                count += 1
    return count


class ProjectDashboard(QWidget):
    """Project overview: statistics + processing completion + recent runs + new experiments."""

    create_experiment_requested = Signal(str)  # Experiment title.

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel(tr("project"))
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        layout.addWidget(self.stats_label)
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)
        layout.addSpacing(10)

        recent_title = QLabel(tr("recently run"))
        recent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(recent_title)
        self.runs_table = QTableWidget(0, 4)
        self.runs_table.setHorizontalHeaderLabels(
            [tr("run"), tr("process"), tr("state"), tr("time")]
        )
        self.runs_table.horizontalHeader().setStretchLastSection(True)
        self.runs_table.setMaximumHeight(150)
        layout.addWidget(self.runs_table)
        layout.addSpacing(10)

        form = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("experiment title (can be left blank)"))
        form.addWidget(self.title_edit, 1)
        self.create_button = QPushButton(tr("New experiment"))
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
            tr(
                "experiment: {p0} | sample data: {p1} | Processed: "
                "{p2}",
                p0=exp_count,
                p1=data_count,
                p2=processed,
            )
        )
        if data_count:
            pct = round(processed * 100 / data_count)
            self.progress_label.setText(tr("Processing completion: {p0}%", p0=pct))
        else:
            self.progress_label.setText(tr("Processing completion: - (no data yet)"))

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


class ExperimentImportPanel(QWidget):
    """Import data panel: single/segmentation/Batch import + link options (0.2.162-patch11). The
    experiment page is no longer displayed inline, and is popped up by the "Import data" button
    on the main interface."""

    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
    # Segmented collection container directory).
    segmented_import_requested = Signal(str, str)
    batch_import_requested = Signal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.copy_check = QCheckBox(tr("Link raw data to project (read only, copy if necessary)"))
        self.copy_check.setChecked(True)
        layout.addWidget(self.copy_check)
        layout.addSpacing(4)

        self.single_group = QGroupBox(tr("single import"))
        single_layout = QVBoxLayout(self.single_group)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("sample data name (optional)"))
        single_layout.addWidget(self.name_edit)
        form = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(tr("Bruker dataset directory (including acqus)"))
        form.addWidget(self.source_edit, 1)
        browse = QPushButton(tr("Browse..."))
        browse.clicked.connect(self._browse)
        form.addWidget(browse)
        single_layout.addLayout(form)
        self.import_button = QPushButton(tr("import sample data"))
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._on_import)
        single_layout.addWidget(self.import_button)
        self.source_edit.textChanged.connect(
            lambda _t: self.import_button.setEnabled(
                bool(self.source_edit.text().strip())
            )
        )
        layout.addWidget(self.single_group)

        self.segmented_group = QGroupBox(tr("segmented data or repeated experiment overlay import"))
        segmented_layout = QVBoxLayout(self.segmented_group)
        segmented_hint = QLabel(
            tr(
                "For one acquisition split into several segments (complementary NUS points "
                "completing the grid) or averaged repeat experiments (same parameters / sampling "
                "points, better SNR): pick the container directory (no acqus at the top level, at "
                "least 2 subdirectories each holding acqus). On import the segments are merged "
                "into a single sample data "
                "set.",
            )
        )
        segmented_hint.setWordWrap(True)
        segmented_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        segmented_layout.addWidget(segmented_hint)
        segmented_form = QHBoxLayout()
        self.segmented_source_edit = QLineEdit()
        self.segmented_source_edit.setPlaceholderText(
                tr(
                "segmented/duplicate experiment container directory (containing multiple acqus "
                "subdirectories)",
            )
        )
        segmented_form.addWidget(self.segmented_source_edit, 1)
        segmented_browse = QPushButton(tr("Browse..."))
        segmented_browse.clicked.connect(self._on_segmented_browse)
        segmented_form.addWidget(segmented_browse)
        segmented_layout.addLayout(segmented_form)
        self.segmented_import_button = QPushButton(
            tr("segmented/repeated experiment overlay import")
        )
        self.segmented_import_button.setEnabled(False)
        self.segmented_import_button.clicked.connect(self._on_segmented_import)
        segmented_layout.addWidget(self.segmented_import_button)
        self.segmented_source_edit.textChanged.connect(
            lambda _t: self.segmented_import_button.setEnabled(
                bool(self.segmented_source_edit.text().strip())
            )
        )
        layout.addWidget(self.segmented_group)

        self.batch_group = QGroupBox(tr("Batch processing"))
        batch_layout = QVBoxLayout(self.batch_group)
        batch_hint = QLabel(
            tr(
                "You may add an umbrella folder (Bruker data sets in its subfolders are detected "
                "automatically) or several data directories. Data imported together share one "
                "batch-group tag, and operations on the processing page then apply to the whole "
                "group. Note: batching only supports 2D spectra for now; 3D data are skipped "
                "(process them one at a "
                "time).",
            )
        )
        batch_hint.setWordWrap(True)
        batch_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        batch_layout.addWidget(batch_hint)
        self.batch_list = QListWidget()
        self.batch_list.setMaximumHeight(110)
        batch_layout.addWidget(self.batch_list)
        self.batch_group_check = QCheckBox(tr("Batch import and group (only supports 2D spectra)"))
        self.batch_group_check.setToolTip(
            tr(
                "Checked: import into one data group and process them together later (2D spectra "
                "only). Unchecked: no grouping, equivalent to several single "
                "imports.",
            )
        )
        self.batch_group_check.setChecked(True)
        batch_layout.addWidget(self.batch_group_check)
        batch_buttons = QHBoxLayout()
        self.batch_add_button = QPushButton(tr("Add data folder..."))
        self.batch_add_button.clicked.connect(self._on_batch_add_folder)
        batch_buttons.addWidget(self.batch_add_button)
        self.batch_clear_button = QPushButton(tr("Clear list"))
        self.batch_clear_button.clicked.connect(self._on_batch_clear)
        batch_buttons.addWidget(self.batch_clear_button)
        self.batch_import_button = QPushButton(tr("Batch import"))
        self.batch_import_button.setEnabled(False)
        self.batch_import_button.clicked.connect(self._on_batch_import)
        batch_buttons.addWidget(self.batch_import_button)
        batch_layout.addLayout(batch_buttons)
        layout.addWidget(self.batch_group)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # Public action interface (0.2.199-patch29hz) Dashboard shortcut button originally directly
    # called _browse/_on_import and other private methods, internal renaming will cause silent
    # failure; here is a stable entry.
    # ------------------------------------------------------------------
    def browse_single(self) -> None:
        """Select a single Bruker dataset directory."""
        self._browse()

    def add_batch_folder(self) -> None:
        """Add directory to the batch import list."""
        self._on_batch_add_folder()

    def clear_batch_list(self) -> None:
        """Clear the batch import list."""
        self._on_batch_clear()

    def import_batch(self) -> None:
        """Perform bulk import by list (or repeat experiment overlay)."""
        self._on_batch_import()

    def import_single(self) -> None:
        """Import the currently selected single dataset."""
        self._on_import()

    def browse_segmented(self) -> None:
        """Select segmented data/Overlapping containers directory."""
        self._on_segmented_browse()

    def import_segmented(self) -> None:
        """Execute segmented data/Repeat overlay import."""
        self._on_segmented_import()

    def set_context(self, exp_id: str) -> None:
        self._exp_id = exp_id

    def _browse(self) -> None:
        # 0.2.199-patch29gg: When empty input, start from "data directory" (default user main
        # directory).
        from gui.settings import data_root_path

        start = self.source_edit.text().strip() or str(data_root_path())
        path = QFileDialog.getExistingDirectory(
            self, tr("Select Bruker dataset directory"), start
        )
        if path:
            self.source_edit.setText(path)

    def _on_batch_add_folder(self) -> None:
        """Add data file folders to the batch list (automatically check Bruker datasets in sub-file
        folders). 0.2.199-patch29gg: Browse starting point = data directory."""
        from gui.settings import data_root_path

        path = QFileDialog.getExistingDirectory(
            self, tr("Select Bruker Data Folder (Batch)"), str(data_root_path())
        )
        if not path:
            return
        found = self._bruker_datasets_under(Path(path))
        if not found:
            InfoDialog.show_info(
                self,
                tr("No data found"),
                (
                    tr(
                    "There is no Bruker dataset containing acqus in the selected directory and its "
                    "subfolders",
                )
                ),
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
        """All data sets containing acqus in the root and sub-file folders directory (sorting and
        deduplication)."""
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
                # 0.2.199-patch29hd: Batch only supports 2D -- 1D (without acqu2s)/3D (including
                # acqu3s/ acqu3) are not included in the batch list (1D has no peak selection, 3D
                # SMILE is prone to power outage).
                if (
                    not (cand / "acqu2s").is_file()
                    or (cand / "acqu3s").is_file()
                    or (cand / "acqu3").is_file()
                ):
                    continue
                datasets.add(cand.resolve())
        return sorted(datasets)

    def _on_batch_clear(self) -> None:
        self.batch_list.clear()
        self.batch_import_button.setEnabled(False)

    def _on_batch_import(self) -> None:
        """Import multiple data directories in the list into the current experiment; group=True is
        the same batch group."""
        if not self._exp_id or self.batch_list.count() == 0:
            return
        folders = [
            self.batch_list.item(index).text()
            for index in range(self.batch_list.count())
        ]
        self.batch_import_requested.emit(
            self._exp_id, folders, self.batch_group_check.isChecked()
        )
        # 0.2.199-patch29gn: Clear the list to be imported after importing to prevent the file
        # folder from being occupied all the time.
        self._on_batch_clear()

    def _on_import(self) -> None:
        source = self.source_edit.text().strip()
        if not source:
            InfoDialog.show_info(
                self, tr("import sample data"), (
                    tr("Please select Bruker dataset directory (including acqus) first")
                )
            )
            return
        # When exp_id is empty (no experiment selected), the experiment will be automatically
        # created by the main window, and it will not be silent or respond.
        self.import_options_requested.emit(
            self._exp_id,
            self.name_edit.text().strip(),
            source,
            self.copy_check.isChecked(),
        )

    def clear_import_form(self) -> None:
        """After successful import, clear the single/The name and path of the segmented import
        form(0.2.112)."""
        self.name_edit.clear()
        self.source_edit.clear()
        self.segmented_source_edit.clear()

    def _on_segmented_browse(self) -> None:
        """Select the segmented collection container directory (0.2.199-patch29gg: empty input
        starting point = total data directory)."""
        from gui.settings import data_root_path

        path = QFileDialog.getExistingDirectory(
            self,
            tr("Select the segmented/duplicate experiment container directory"),
            self.segmented_source_edit.text().strip()
            or str(data_root_path()),
        )
        if path:
            self.segmented_source_edit.setText(path)

    def _on_segmented_import(self) -> None:
        """Segmented collection import: container directory (merge FID) directly send a request
        (with current experiment, 0.2.122)."""
        source = self.segmented_source_edit.text().strip()
        if not source:
            InfoDialog.show_info(
                self, tr("segmented collection import"), (
                    tr("Please select the segmented collection container directory first")
                )
            )
            return
        self.segmented_import_requested.emit(self._exp_id, source)


def _dropdown_geometry(
    anchor: QWidget,
    host: QWidget,
    natural_h: int,
    width: int,
    margin: int = 8,
) -> tuple[QPoint, int]:
    """Calculate the position and maximum height of the drop-down in the host (parent window):
    priority is placed directly below the button, and if the bottom is not enough, place it
    above to ensure that the trigger button is not blocked; when the height exceeds the
    available space, it is truncated (carried by the scroll bar). All relative coordinates are
    used, consistent on any platform (0.2.194 Revision: Keep the main window sub-component
    scheme and do not return to the top-level window with position problems). Return (pos,
    max_height)."""
    anchor_top = anchor.mapTo(host, QPoint(0, 0)).y()
    anchor_bottom = anchor.mapTo(host, QPoint(0, anchor.height())).y()
    host_w = max(host.width(), 1)
    host_h = max(host.height(), 1)
    below = host_h - anchor_bottom - margin
    above = anchor_top - margin
    target_h = min(natural_h, max(below, above, margin))
    if below >= target_h:
        y = anchor_bottom
    else:
        y = anchor_top - target_h
    x = anchor.mapTo(host, QPoint(0, 0)).x()
    x = min(max(x, 0), max(0, host_w - width))
    y = min(max(y, 0), max(0, host_h - target_h))
    return QPoint(x, y), target_h


#: Preferred minimum width of the drop-down. When less space is available the form keeps
#: its natural width and a horizontal scrollbar carries the overflow instead.
_DROPDOWN_MIN_WIDTH = 400
#: Side margins of the drop-down (8+8) plus a vertical scrollbar (16) when it is shown
_DROPDOWN_CHROME = 32


class ImportDataDropdown(QWidget):
    """"Import Data" drop-down panel: Pops down, containing the complete import form
    (0.2.162-patch11)."""

    import_options_requested = Signal(str, str, str, bool)
    segmented_import_requested = Signal(str, str)
    batch_import_requested = Signal(str, list, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # WA_AlwaysStackOnTop: Under the sub-component scheme, it is guaranteed to be drawn on the
        # central component, and the first pop-up of Windows is not visible (0.2.194 revision, not
        # returning to the top window).
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self._anchor: QWidget | None = None
        self._host_window: QWidget | None = None
        self._app = QApplication.instance()
        if self._app is not None:
            self.destroyed.connect(self._remove_event_filter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(tr("import sample data"))
        title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)
        self.panel = ExperimentImportPanel(self)
        self.panel.import_options_requested.connect(self.import_options_requested.emit)
        self.panel.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self.panel.batch_import_requested.connect(self.batch_import_requested.emit)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 0.2.199-patch30 (user): on a small screen the page may clip the right edge of this
        # drop-down. Clipping is acceptable, but the form must stay reachable: when the width
        # is clamped, _place_below pins the form to its natural width so QScrollArea offers a
        # horizontal scrollbar instead of cutting content off silently.
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setWidget(self.panel)
        layout.addWidget(self._scroll, 1)
        self.setMinimumWidth(_DROPDOWN_MIN_WIDTH)
        # Construction means hiding: In the sub-component scheme, the parent page display will also
        # display the sub-component. If it is not hidden, a (0,0) afterimage will appear at the top
        # of the page (0.2.194-patch2 measured), and isVisible is true, causing open_below to never
        # be executed.
        self.hide()

    def open_below(self, anchor: QWidget, exp_id: str) -> None:
        """Pops up just below the anchor button (the main window covers the sub-component), and
        adds a scroll bar if it is too long. Hang it on the top-level window where the anchor is
        located, and position it with relative coordinates (Qt controls it itself, and does not
        rely on the Wayland window protocol); WA_AlwaysStackOnTop ensures that it is drawn on
        the central component, and fixes the invisible pop-up of Windows for the first time
        (0.2.194 revised; does not return to the top-level window -- there is an unsolvable
        position problem in the top-level window, 0.2.163-patch4 therefore deprecated)."""
        self._anchor = anchor
        self._host_window = anchor.window()
        if self._app is not None:
            self._app.installEventFilter(self)
        self.panel.set_context(exp_id)
        # Keep it as a sub-component of the experimental page, positioned relative to this page, and
        # not reparent to the main window -- opening reparent for the first time will trigger
        # position recalculation, and the drop-down will run to the top of the page and only the
        # scroll bar will be exposed (0.2.194-patch2 measured; the sub-component scheme is
        # consistent on any platform, and there is no top-level window position problem).
        host = self.parentWidget() or anchor.parentWidget()
        self.setMaximumHeight(16777215)  # Reset the last limit and take the natural height again.
        # The hidden state is placed first (the sub-component move is relative to the parent window,
        # which is the required semantics); show only really takes effect in the event loop, and the
        # following synchronized move will be lost -- correct it again after the first event loop to
        # ensure that the first opening is also directly under the button (0.2.194-patch2).
        self._place_below(anchor, host)
        self.show()
        QTimer.singleShot(0, self._deferred_place)
        self.activateWindow()

    def _place_below(self, anchor: QWidget, host: QWidget) -> None:
        """Calculate and apply the geometry directly below the button (hide/The status can be
        displayed,0.2.194-patch2)."""
        # Drop the limits left over from the previous placement first, so the natural size can
        # be measured again (a narrow host clamps the width; the next opening has to be able to
        # measure the unclamped width).
        self.setMaximumWidth(16777215)
        self.setMaximumHeight(16777215)
        self.adjustSize()
        natural_w = max(self.sizeHint().width(), _DROPDOWN_MIN_WIDTH)
        # 0.2.199-patch30 (user): on a small VM screen the centre column can be narrower than
        # the drop-down, so its right edge is clipped by the parent. That is allowed, but while
        # the drop-down is clamped the inner form has to keep its natural width and the
        # overflow is carried by a horizontal scrollbar -- otherwise nothing tells the user
        # that content continues to the right. Without a clamp the historical behaviour is kept
        # (the form follows the viewport, so no needless scrollbar appears on a wide screen).
        avail_w = max(host.width(), 1)
        clamped = avail_w < natural_w
        width = max(1, min(natural_w, avail_w))
        self.setMinimumWidth(min(_DROPDOWN_MIN_WIDTH, width))
        self.setMaximumWidth(width)
        self.panel.setMinimumWidth(
            max(1, natural_w - _DROPDOWN_CHROME) if clamped else 0
        )
        self.adjustSize()
        pos, max_h = _dropdown_geometry(anchor, host, self.sizeHint().height(), width)
        self.setMaximumHeight(max_h)
        self.adjustSize()
        self.move(pos)
        self.raise_()

    def _deferred_place(self) -> None:
        """The position after show takes effect correction: replay according to the current anchor
        point (0.2.194-patch2)."""
        if self._anchor is None or not self.isVisible():
            return
        host = self.parentWidget()
        if host is not None:
            self._place_below(self._anchor, host)

    def eventFilter(self, obj, event) -> bool:
        """Non-grab window: Click other button/When external, close this drop-down first, click to
        continue to the target."""
        try:
            visible = self.isVisible()
        except RuntimeError:  # pragma: no cover - Burn race.
            return False
        # When the host's top-level window is closed, the drop-down is synchronously closed and the
        # applied filter is removed to prevent residual filters from hanging at the end of the
        # process (0.2.194 life cycle reinforcement).
        if (
            visible
            and getattr(self, "_host_window", None) is obj
            and event.type() == QEvent.Type.Close
        ):
            self.close()
            return False
        if visible and event.type() == QEvent.Type.MouseButtonPress:
            if hasattr(event, "globalPosition"):
                pos = event.globalPosition().toPoint()
            else:  # pragma: no cover - Qt5 Compatible.
                pos = event.globalPos()
            if self._anchor is not None and self._anchor.rect().contains(
                self._anchor.mapFromGlobal(pos)
            ):
                return False  # Anchor button: leave it to the button (switch/switch).
            if not self.rect().contains(self.mapFromGlobal(pos)):
                self.close()
        return False

    def hideEvent(self, event) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)
        super().hideEvent(event)

    def _remove_event_filter(self) -> None:
        if self._app is not None:
            try:
                self._app.removeEventFilter(self)
            except RuntimeError:  # pragma: no cover - Application has been destroyed.
                pass


class ExperimentDashboard(QWidget):
    """Experiment overview: Sample data list (status, name can be changed); the import block has
    been moved to the "Import data" drop-down."""

    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
    data_rename_requested = Signal(str, str, str)  # (exp_id, data_id, new_name)
    # Segmented collection container directory).
    segmented_import_requested = Signal(str, str)
    batch_import_requested = Signal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        self._exp_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel(tr("experiment"))
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)
        self.context_label = QLabel("")
        layout.addWidget(self.context_label)
        layout.addSpacing(8)

        data_title = QLabel(tr("sample data"))
        data_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(data_title)
        self.data_table = QTableWidget(0, 3)
        self.data_table.setHorizontalHeaderLabels([tr("sample data"), tr("name"), tr("state")])
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.setMaximumHeight(160)
        # 0.2.162-patch13: The name column can be edited (double click/Select click/F2), change the
        # name and go to manager.rename_data.
        self.data_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.data_table.itemChanged.connect(self._on_data_name_edited)
        self._loading_table = False
        layout.addWidget(self.data_table)
        layout.addSpacing(10)

        # 0.2.162-patch11: The import block is moved to the "Import data" drop-down panel, and the
        # experiment page is no longer displayed inline.
        self.import_panel = ExperimentImportPanel(self)
        self.import_panel.hide()
        self.import_panel.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self.import_panel.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self.import_panel.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        # Compatible with old tests/old call: Form controls and processors are forwarded to the
        # import panel.
        self.copy_check = self.import_panel.copy_check
        self.single_group = self.import_panel.single_group
        self.name_edit = self.import_panel.name_edit
        self.source_edit = self.import_panel.source_edit
        self.import_button = self.import_panel.import_button
        self.segmented_group = self.import_panel.segmented_group
        self.segmented_source_edit = self.import_panel.segmented_source_edit
        self.segmented_import_button = self.import_panel.segmented_import_button
        self.batch_group = self.import_panel.batch_group
        self.batch_list = self.import_panel.batch_list
        self.batch_add_button = self.import_panel.batch_add_button
        self.batch_clear_button = self.import_panel.batch_clear_button
        self.batch_import_button = self.import_panel.batch_import_button
        # 0.2.162-patch12: "Import data" button (original import block location); "Analysis between
        # data groups" has been hidden (2026-09-03).
        action_row = QHBoxLayout()
        self.import_dropdown_button = QPushButton(tr("import data"))
        self.import_dropdown_button.clicked.connect(self._open_import_dropdown)
        action_row.addWidget(self.import_dropdown_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self._import_dropdown = ImportDataDropdown(self)
        self._import_dropdown.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self._import_dropdown.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )
        self._import_dropdown.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        layout.addStretch(1)

    def _open_import_dropdown(self) -> None:
        """Experimental page "Import data": There is always clear feedback when clicking. The drop-
        down is not opened -> pops up under the button; it is opened -> top focus (does not
        repeat setParent, avoids repeated installation of event filters). Close is triggered by
        clicking on the external (EventFilter)."""
        if self._import_dropdown.isVisible():
            self._import_dropdown.raise_()
            self._import_dropdown.activateWindow()
            return
        self._import_dropdown.open_below(self.import_dropdown_button, self._exp_id)

    def set_context(self, manager: ProjectManager, exp_id: str, label: str) -> None:
        self.manager = manager
        self._exp_id = exp_id
        self.import_panel.set_context(exp_id)
        self.context_label.setText(f"{label} ({exp_id})" if exp_id else "")
        self.refresh()

    def _on_data_name_edited(self, item) -> None:
        """Rename the sample data (0.2.162-patch13) after editing the "Name" column of the data
        table."""
        if item.column() != 1 or self._loading_table:
            return
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        data_item = self.data_table.item(item.row(), 0)
        if data_item is None:
            return
        data_id = data_item.text()
        new_name = item.text().strip()
        entry = self.manager.project.experiment(self._exp_id)
        data_entry = (
            next((d for d in entry.data if d.id == data_id), None) if entry else None
        )
        if data_entry is None or new_name == (getattr(data_entry, "title", "") or ""):
            return
        self.data_rename_requested.emit(self._exp_id, data_id, new_name)
        self.refresh()

    def refresh(self) -> None:
        self.data_table.setRowCount(0)
        if self.manager is None or self.manager.project is None or not self._exp_id:
            return
        exp = self.manager.project.experiment(self._exp_id)
        if exp is None:
            return
        self._loading_table = True
        try:
            for data in _active_data_of(exp):
                row = self.data_table.rowCount()
                self.data_table.insertRow(row)
                self.data_table.setItem(row, 0, QTableWidgetItem(data.id))
                self.data_table.setItem(
                    row, 1, QTableWidgetItem(getattr(data, "title", "") or "")
                )
                self.data_table.setItem(
                    row, 2, QTableWidgetItem(getattr(data, "status", "") or "")
                )
        finally:
            self._loading_table = False

    def _browse(self) -> None:
        self.import_panel.browse_single()

    def _on_batch_add_folder(self) -> None:
        self.import_panel.add_batch_folder()

    @staticmethod
    def _bruker_datasets_under(root: Path) -> list[Path]:
        return ExperimentImportPanel._bruker_datasets_under(root)

    def _on_batch_clear(self) -> None:
        self.import_panel.clear_batch_list()

    def _on_batch_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_batch()

    def _on_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_single()

    def clear_import_form(self) -> None:
        """After successful import, clear the import form (including drop-down panel,
        0.2.162-patch12)."""
        self.import_panel.clear_import_form()
        if self._import_dropdown is not None:
            self._import_dropdown.panel.clear_import_form()

    def _on_segmented_browse(self) -> None:
        self.import_panel.browse_segmented()

    def _on_segmented_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_segmented()
