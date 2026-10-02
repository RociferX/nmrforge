"""Dashboard panel: project/Experiment overview(GUI_ARCHITECTURE_VISION §11-12). -
ProjectDashboard: project statistics (experiment/sample data/Processing completion) + recent
runs + new experiment form; - ExperimentDashboard: sample data list (data status of each sample)
+ import sample data form. Data source: core.project(ProjectManager); running history comes from
workflow_runs.
"""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from gui.file_dialogs import choose_directory
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_MUTED, TEXT_PRIMARY


def _active_data_of(exp) -> list:
    """Data entries that were not soft deleted under the experiment."""
    return [d for d in (getattr(exp, "data", None) or []) if not getattr(d, "trashed", False)]


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

    create_experiment_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel(tr("project"))
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};")
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
                "experiment: {p0} | sample data: {p1} | Processed: {p2}",
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
    """Persistent import form for single, segmented and batch datasets, with linking options."""

    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
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
            lambda _t: self.import_button.setEnabled(bool(self.source_edit.text().strip()))
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

        from gui.settings import data_root_path

        start = self.source_edit.text().strip() or str(data_root_path())
        path = choose_directory(self, tr("Select Bruker dataset directory"), start)
        if path:
            self.source_edit.setText(path)

    def _on_batch_add_folder(self) -> None:
        """Add data file folders to the batch list (automatically check Bruker datasets in sub-file
        folders). 0.2.199-patch29gg: Browse starting point = data directory.
        """
        from gui.settings import data_root_path

        path = choose_directory(
            self, tr("Select Bruker Data Folder (Batch)"), str(data_root_path())
        )
        if not path:
            return
        found = self._bruker_datasets_under(Path(path))
        if not found:
            InfoDialog.show_info(
                self,
                tr("No data found"),
                tr(
                    "There is no Bruker dataset containing acqus in the selected directory and its "
                    "subfolders",
                ),
            )
            return
        for dataset_dir in found:
            if not self.batch_list.findItems(str(dataset_dir), Qt.MatchFlag.MatchExactly):
                self.batch_list.addItem(str(dataset_dir))
        self.batch_import_button.setEnabled(self.batch_list.count() > 0)

    @staticmethod
    def _bruker_datasets_under(root: Path) -> list[Path]:
        """All data sets containing acqus in the root and sub-file folders directory (sorting and
        deduplication).
        """
        datasets: set[Path] = set()
        try:
            candidates = [Path(p).parent for p in root.rglob("acqus") if p.is_file()]
            candidates.append(root)
        except OSError:
            candidates = [root]
        for cand in candidates:
            if (cand / "acqus").is_file():
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
        the same batch group.
        """
        if not self._exp_id or self.batch_list.count() == 0:
            return
        folders = [self.batch_list.item(index).text() for index in range(self.batch_list.count())]
        self.batch_import_requested.emit(self._exp_id, folders, self.batch_group_check.isChecked())

        self._on_batch_clear()

    def _on_import(self) -> None:
        source = self.source_edit.text().strip()
        if not source:
            InfoDialog.show_info(
                self,
                tr("import sample data"),
                tr("Please select Bruker dataset directory (including acqus) first"),
            )
            return

        self.import_options_requested.emit(
            self._exp_id,
            self.name_edit.text().strip(),
            source,
            self.copy_check.isChecked(),
        )

    def clear_import_form(self) -> None:
        """After successful import, clear the single/The name and path of the segmented import
        form(0.2.112).
        """
        self.name_edit.clear()
        self.source_edit.clear()
        self.segmented_source_edit.clear()

    def _on_segmented_browse(self) -> None:
        """Select the segmented collection container directory (0.2.199-patch29gg: empty input
        starting point = total data directory).
        """
        from gui.settings import data_root_path

        path = choose_directory(
            self,
            tr("Select the segmented/duplicate experiment container directory"),
            self.segmented_source_edit.text().strip() or str(data_root_path()),
        )
        if path:
            self.segmented_source_edit.setText(path)

    def _on_segmented_import(self) -> None:
        """Segmented collection import: container directory (merge FID) directly send a request
        (with current experiment, 0.2.122).
        """
        source = self.segmented_source_edit.text().strip()
        if not source:
            InfoDialog.show_info(
                self,
                tr("segmented collection import"),
                tr("Please select the segmented collection container directory first"),
            )
            return
        self.segmented_import_requested.emit(self._exp_id, source)


class ExperimentDashboard(QWidget):
    """Experiment overview with dataset status, editable names and a persistent import form."""

    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
    data_rename_requested = Signal(str, str, str)  # (exp_id, data_id, new_name)
    segmented_import_requested = Signal(str, str)
    batch_import_requested = Signal(str, list, bool)  # (exp_id, folders, group)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager: ProjectManager | None = None
        self._exp_id = ""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.page_content = QWidget()
        layout = QVBoxLayout(self.page_content)
        layout.setContentsMargins(16, 16, 16, 16)
        self.scroll_area.setWidget(self.page_content)
        root_layout.addWidget(self.scroll_area)

        title = QLabel(tr("experiment"))
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};")
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

        self.data_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.data_table.itemChanged.connect(self._on_data_name_edited)
        self._loading_table = False
        layout.addWidget(self.data_table)
        layout.addSpacing(10)

        self.import_panel = ExperimentImportPanel(self.page_content)
        self.import_panel.import_options_requested.connect(self.import_options_requested.emit)
        self.import_panel.segmented_import_requested.connect(self.segmented_import_requested.emit)
        self.import_panel.batch_import_requested.connect(self.batch_import_requested.emit)

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
        layout.addWidget(self.import_panel)
        layout.addStretch(1)

    def set_context(self, manager: ProjectManager, exp_id: str, label: str) -> None:
        self.manager = manager
        self._exp_id = exp_id
        self.import_panel.set_context(exp_id)
        self.context_label.setText(f"{label} ({exp_id})" if exp_id else "")
        self.refresh()

    def _on_data_name_edited(self, item) -> None:
        """Rename the sample data (0.2.162-patch13) after editing the "Name" column of the data
        table.
        """
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
        data_entry = next((d for d in entry.data if d.id == data_id), None) if entry else None
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
                self.data_table.setItem(row, 1, QTableWidgetItem(getattr(data, "title", "") or ""))
                self.data_table.setItem(row, 2, QTableWidgetItem(getattr(data, "status", "") or ""))
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
        """Clear the import form after successful import."""
        self.import_panel.clear_import_form()

    def _on_segmented_browse(self) -> None:
        self.import_panel.browse_segmented()

    def _on_segmented_import(self) -> None:
        self.import_panel.set_context(self._exp_id)
        self.import_panel.import_segmented()
