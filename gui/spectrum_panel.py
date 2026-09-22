"""Spectrum panel on the right: embedded independent viewer + project spectrum file list + peak
table edit write-back. Reuse viewer.SpectrumViewer (do not repeat the spectrum function); list
scan project spectrum directory, click.ft2/.ft3 to open on the right. Peak table supports
adding/delete/Edit line and write back data_dir(..., "peaks")/<exp>-<data>.list (Contract §6:
Peak file is Poky.list, registered by ProcessingController manual_peaks WorkflowRun). GUI Does
not directly touch the processing logic."""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtCore import QItemSelectionModel, Qt
from qtcompat.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from core.user_errors import describe_exception
from gui.dialogs import InfoDialog
from gui.peaks_io import (
    export_peaks_poky,
    import_peaks_poky,
    load_peaks,
    normalize_poky_label,
)
from gui.processing import ProcessingController
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_MUTED
from viewer.spectrum3d_panel import Spectrum3DPanel
from viewer.spectrum_viewer import SpectrumViewer

# 0.2.199-patch29dc: The Assignment editing component is only created for visible rows (+/- buffer).
# Thousands of rows of peak tables are built row by row. QWidget (2-3 input boxes per row) is the
# main reason for the lag in opening the 3D spectrum.
_ASSIGNMENT_WIDGET_BUFFER = 12


def _axis_step(axis) -> float:
    """Axisppm/point(median of adjacent positive steps); returns 0 on exception."""
    ppm = getattr(axis, "ppm", None)
    if ppm is None:
        return 0.0
    import numpy as np

    arr = np.asarray(ppm, dtype=float)
    if arr.size < 2:
        return 0.0
    diff = np.abs(np.diff(arr))
    diff = diff[diff > 0]
    return float(np.median(diff)) if diff.size else 0.0


class _AssignmentCell(QWidget):
    """Peak table Assignment cell: Fixed hyphen + segment input box (2D two paragraphs/3D three
    segments, default ?). 0.2.199-patch29cp(user): "-" Fixed display, one input box before and
    after; edit segment by segment Poky normalise and merge into label (such as G1H-G1N,
    G1H-G1N-G1CA)."""

    edited = Signal(int)  # row

    def __init__(self, ndim: int, row: int, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.ndim = max(1, int(ndim))
        self.row = int(row)
        self.lines: list[QLineEdit] = []
        segs = [s.strip() for s in str(text or "").split("-")]
        while len(segs) < self.ndim:
            segs.append("?")
        segs = segs[: self.ndim]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.setStyleSheet("QWidget { background: transparent; }")
        for i in range(self.ndim):
            if i:
                dash = QLabel("-")
                dash.setStyleSheet("color: #c8c8c8; background: transparent;")
                dash.setFixedWidth(10)
                lay.addWidget(dash)
            le = QLineEdit()
            le.setAlignment(Qt.AlignmentFlag.AlignCenter)
            le.setFixedWidth(38)
            le.setMaxLength(12)
            # 0.2.199-patch29cq: Unspecified segments are displayed with the placeholder "?"
            # (replaced as input, no residue added).
            if segs[i] in ("", "?"):
                le.setPlaceholderText("?")
            else:
                le.setText(segs[i])
            le.setStyleSheet(
                "QLineEdit { color: #e8e8e8; } "
                "QLineEdit::placeholder { color: #8a8a8a; }"
            )
            self.lines.append(le)
            lay.addWidget(le)
        for le in self.lines:
            le.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, *_args) -> None:
        self.edited.emit(self.row)

    def merged_text(self) -> str:
        """The label of the current merged segments (poky normalisation segment by segment, empty
        segment -> ?)."""
        segs = [
            normalize_poky_label((le.text() or "").strip(), ndim=1) or "?"
            for le in self.lines
        ]
        return "-".join(segs)


class SpectrumPanel(QWidget):
    """Spectrum panel: viewer + file list + peak table (add/delete/change/live)."""

    # Issued after the peak table is written back (main window refreshes Pipeline/log).
    peaks_saved = Signal()
    status_message = Signal(str)  # Status bar prompt (received by main window).
    log_message = Signal(str)  # Task log (received by the main window LogPanel, 0.2.199-patch29cz).
    _ft3_ready = Signal(object, object)  # (path, Spectrum3D) Background loading completed.
    _ft3_failed = Signal(object, str)  # (path, message)
    # 0.2.199-patch29bp: spectrum enlarge/close (the main window collapses the three parts on the
    # left).
    expand_requested = Signal(bool)

    # 0.2.89: Load.ft3 background threads exceeding this size to avoid stuck when reading large
    # files UI.
    _ASYNC_FT3_MIN_BYTES = 32 * 1024 * 1024

    def __init__(
        self,
        manager: ProjectManager | None = None,
        controller: ProcessingController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager or ProjectManager()
        self.controller = controller or ProcessingController()
        self.controller.set_manager(self._manager)
        self._current_exp_id: str = ""
        self._current_data_id: str = ""
        self._loading_peaks = False
        # Patch29cn: standardization to prevent recursion.
        self._applying_label_format = False
        self._viewer3d_state: dict[tuple[str, str], int] = {}
        # 0.2.199-patch29fz(user): spectrum display adjustment is isolated by data -- (exp_id,
        # data_id) -> {contour slider/series/aspect/Peak marker size}.
        self._display_states: dict[tuple[str, str], dict] = {}
        self._peak_keys: tuple[str, ...] = (
            "Peak_ID",
            "H_shift",
            "N_shift",
            "Intensity",
            "SN",
        )

        # 0.2.199-patch29hz-Repair 2: Partition card + title bar.
        self.setObjectName("PanelCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        panel_title = QLabel(tr("spectrum"))
        panel_title.setObjectName("PanelTitle")
        header.addWidget(panel_title)
        header.addStretch(1)
        layout.addLayout(header)
        self.viewer = SpectrumViewer()
        self._spectrum3d_panel = Spectrum3DPanel()
        self._spectrum3d_panel.setVisible(False)
        self._ft3_ready.connect(self._on_ft3_ready)
        self._ft3_failed.connect(self._on_ft3_failed)
        self._spectrum3d_panel.slice_changed.connect(self._render_3d_view)
        self._spectrum3d_panel.plane_combo.currentIndexChanged.connect(
            self._save_3d_state
        )
        self.viewer.add_control_panel(self._spectrum3d_panel)

        self.file_list = QListWidget()
        self.file_list.setAutoFillBackground(False)
        self.viewer.layer_list.setAutoFillBackground(False)
        self.file_list.setMaximumWidth(190)
        self.file_list.itemClicked.connect(self._on_file_clicked)

        self.peak_toolbar = QHBoxLayout()
        # 0.2.147: Peak operation is in one row, and the interval between columns is obvious; Show
        # peaks is located before Add peak.
        self.peak_toolbar.setSpacing(12)
        self.peak_toolbar.addWidget(self.viewer.show_peaks_checkbox)
        # 0.2.199-patch29at: selection mode (left-click and drag the box to select peaks), mutually
        # exclusive with 1D/Add peak.
        self.select_peaks_button = QPushButton("Select mode")
        self.select_peaks_button.setCheckable(True)
        self.select_peaks_button.setEnabled(False)
        self.select_peaks_button.setToolTip(
                tr(
                "Selection mode: hold down the left button and drag the frame to select multiple "
                "peaks; mutually exclusive with 1D view, Add "
                "peak",
            )
        )
        self.select_peaks_button.toggled.connect(self._on_select_mode_toggled)
        self.peak_toolbar.addWidget(self.select_peaks_button)
        # 0.2.199-patch29ar: Add peak is changed to switch -- After turning it on, click spectrum to
        # add peak (adsorb peak top).
        self.add_peak_button = QPushButton("Add peak mode")
        self.add_peak_button.setCheckable(True)
        self.add_peak_button.setEnabled(False)
        self.add_peak_button.setToolTip(
                tr(
                "Switch: After turning it on, click spectrum to add peaks (automatically adsorb to "
                "the peak; if you cannot find a significant peak, use the click "
                "position)",
            )
        )
        self.add_peak_button.toggled.connect(self._on_add_peak_toggled)
        self.peak_toolbar.addWidget(self.add_peak_button)
        # 0.2.199-patch29bb: The peak operation is divided into two lines -- put
        # Delete/Import/Export/Save in the second line.
        self.peak_toolbar2 = QHBoxLayout()
        self.peak_toolbar2.setSpacing(12)
        self.delete_peak_button = QPushButton("Delete selected")
        self.delete_peak_button.setEnabled(False)
        self.delete_peak_button.setToolTip(tr(
            "Delete the selected rows from the peak table (automatic and manual peaks "
            "alike)",
        ))
        self.delete_peak_button.clicked.connect(self._on_delete_peak)
        self.peak_toolbar2.addWidget(self.delete_peak_button)
        self.import_poky_button = QPushButton("Import peaks")
        self.import_poky_button.setEnabled(False)
        self.import_poky_button.setToolTip(tr("from Poky/Sparky.list import peak table"))
        self.import_poky_button.clicked.connect(self._on_import_poky)
        self.peak_toolbar2.addWidget(self.import_poky_button)
        self.export_poky_button = QPushButton("Export peaks")
        self.export_poky_button.setEnabled(False)
        self.export_poky_button.setToolTip(
                tr(
                "export peak table: direct export (original coordinates) or export after alignment "
                "(overall translation and alignment to the selected reference peak "
                "file)",
            )
        )
        self.export_menu = QMenu(self)
        self.export_direct_action = self.export_menu.addAction(
            tr("export directly"), self._export_peaks_poky
        )
        self.export_aligned_action = self.export_menu.addAction(
            tr("After alignment export"), self._export_peaks_aligned
        )
        self.export_poky_button.setMenu(self.export_menu)
        self.peak_toolbar2.addWidget(self.export_poky_button)
        self.save_peaks_button = QPushButton("Save peaks")
        self.save_peaks_button.setEnabled(False)
        self.save_peaks_button.setToolTip(
            tr(
            "Write peak table back to data/peaks/<exp>-<data>.list and "
            "register",
        )
        )
        self.save_peaks_button.clicked.connect(self._on_save_peaks)
        self.peak_toolbar2.addWidget(self.save_peaks_button)
        # 0.2.199-patch29az: Peak marker size (data coordinates, scaled with spectrum).
        self.peak_size_label = QLabel(tr("Mark size"))
        self.peak_size_spin = QDoubleSpinBox()
        self.peak_size_spin.setRange(0.5, 50.0)
        self.peak_size_spin.setSingleStep(0.5)
        self.peak_size_spin.setDecimals(1)
        self.peak_size_spin.setValue(1.5)
        self.peak_size_spin.setToolTip(tr(
            "Marker size (data coordinate units, scaled with "
            "spectrum)",
        ))
        self.peak_size_spin.setEnabled(False)
        self.peak_size_spin.valueChanged.connect(self.viewer.set_peak_size)
        # 0.2.199-patch29fz(user): Display adjustment (contour start/levels/aspect/mark size) will
        # be recorded in the current data every time, and will not affect each other when switching
        # data.
        self.viewer.level_slider.valueChanged.connect(
            self._save_display_state
        )
        self.viewer.count_slider.valueChanged.connect(
            self._save_display_state
        )
        self.viewer.aspect_slider.valueChanged.connect(
            self._save_display_state
        )
        self.peak_size_spin.valueChanged.connect(self._save_display_state)
        self.peak_toolbar.addWidget(self.peak_size_label)
        self.peak_toolbar.addWidget(self.peak_size_spin)
        self.peak_toolbar.addStretch(1)

        self.peak_table = QTableWidget(0, 5)
        self.peak_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.peak_table.setHorizontalHeaderLabels(list(self._peak_keys))
        self.peak_table.horizontalHeader().setStretchLastSection(True)
        self.peak_table.setMaximumHeight(150)
        # 0.2.199-patch29bo: spectrum Click/Programmed row selection caused by box selection, does
        # not trigger single-peak flashing.
        self._syncing_table_selection = False
        self.peak_table.itemSelectionChanged.connect(self._on_peak_row_selected)
        # 0.2.199-patch29cm: Clicking the selected row again will not trigger selectionChanged. You
        # need to connect another cellClicked to repeat the flash positioning.
        self.peak_table.cellClicked.connect(self._on_peak_cell_clicked)
        self.peak_table.itemChanged.connect(self._on_peak_cell_edited)
        # 0.2.199-patch29bf: Click the Assignment column header to switch the assignment label on
        # the graph.
        self.peak_table.horizontalHeader().sectionClicked.connect(
            self._on_peak_header_clicked
        )
        # 0.2.199-patch29dc:Create on demand while scrolling/destroy Assignment editing component.
        self.peak_table.verticalScrollBar().valueChanged.connect(
            self._ensure_assignment_widgets
        )
        self._peaks: list[dict] = []
        self._current_spectrum: Path | None = None
        self._viewer_1d_active = False  # 0.2.199-Patch29bd:1D turns on the hidden peak control.
        self._projection_active = False  # 0.2.199-Patch29db:projection file hidden peak UI.
        self.placeholder = QLabel(
                tr(
                "If the project is not open, select experiment under project on the left, or click "
                "on the spectrum file to view the "
                "results",
            )
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet(f"color: {TEXT_MUTED};")


        # Top and bottom layout: top file / Layers row, middle viewer, bottom peak operation + peak
        # table 0.2.147: file list and Layers list side by side, with obvious intervals.
        self.lists_row_widget = QWidget()
        # 0.2.199-patch29bs: Limit Files/Layers row height, the right column will not be exploded
        # when zooming in.
        self.lists_row_widget.setMaximumHeight(110)
        self.lists_row = QHBoxLayout(self.lists_row_widget)
        self.lists_row.setContentsMargins(0, 0, 0, 0)
        self.lists_row.setSpacing(16)
        files_box = QVBoxLayout()
        files_box.setSpacing(2)
        files_box.addWidget(QLabel("Files"))
        files_box.addWidget(self.file_list, 1)
        layers_box = QVBoxLayout()
        layers_box.setSpacing(2)
        layers_box.addWidget(QLabel("Layers"))
        layers_box.addWidget(self.viewer.layer_list, 1)
        self.lists_row.addLayout(files_box, 1)
        self.lists_row.addLayout(layers_box, 1)
        # 0.2.199-patch29bp: spectrum zoom button -- Collapse the three parts on the left, spectrum
        # fills the window.
        self.expand_button = QPushButton(tr("enlarge"))
        self.expand_button.setCheckable(True)
        self.expand_button.setToolTip(
            tr(
                "Zoom: the plot area alone extends to the left (covering the project tree / "
                "pipeline / log) while the buttons stay on the right; click again to "
                "restore",
            )
        )
        self.expand_button.toggled.connect(self._on_expand_toggled)
        # 0.2.199-patch29bs: Leave no large space between Layers and buttons.
        self.lists_row.addWidget(self.expand_button)
        # 0.2.199-patch29br: Spectrum viewer's " file " "Help" menu moved to the right of the zoom
        # button.
        self.file_menu = QMenu(self)
        # 0.2.199-patch29hz - Modification 27 (user): "Open spectrum" is split into two parts -- the
        # first one opens the spectrum of **current data** (the same effect as Pipeline's "display
        # spectrum"; a clear prompt when there is no spectrum), and the second one retains the
        # original arbitrary file entry.
        self.open_current_action = self.file_menu.addAction(
            tr("Open the current data spectrum"), self._on_menu_open_current_spectrum
        )
        self.open_any_action = self.file_menu.addAction(
            tr("Open any spectrum..."), self._on_menu_open_spectrum
        )
        self.file_menu.addAction(tr("clear spectrum"), self._on_menu_clear_spectrum)
        self.file_button = QPushButton(tr("document"))
        self.file_button.setMenu(self.file_menu)
        self.help_menu = QMenu(self)
        self.help_menu.addAction(tr("Operating Instructions"), self._on_menu_show_help)
        self.help_button = QPushButton(tr("help"))
        self.help_button.setMenu(self.help_menu)
        self.lists_row.addWidget(self.file_button)
        self.lists_row.addWidget(self.help_button)
        self.lists_row.addSpacing(12)  # 0.2.199-Patch29bq: do not paste the rightmost border.

        # 0.2.199-patch29bq: Magnification mode -- Switch between vertical splitter (default) and
        # horizontal [drawing area | right control column], only the drawing area extends to the
        # left.
        self._expanded = False
        self._expand_splitter: QSplitter | None = None
        self._expand_controls: QWidget | None = None
        self._expand_plot_area: QWidget | None = None
        self._expand_viewer_controls: QWidget | None = None
        self._collapsed_sizes: list[int] = []
        self._view_splitter_sizes: list[int] = []
        self._panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self._panel_splitter.addWidget(self.lists_row_widget)
        self._panel_splitter.addWidget(self.viewer)
        self.peak_toolbar_widget = QWidget()
        # 0.2.199-patch29bs: Two rows of peak buttons (Show/Select/Add/size +
        # Delete/Import/Export/Save).
        self.peak_toolbar_widget.setMinimumHeight(68)
        self.peak_toolbar_widget.setMaximumHeight(90)
        _peak_rows = QVBoxLayout(self.peak_toolbar_widget)
        _peak_rows.setContentsMargins(0, 0, 0, 0)
        _peak_rows.setSpacing(4)
        _peak_rows.addLayout(self.peak_toolbar)
        _peak_rows.addLayout(self.peak_toolbar2)
        self._panel_splitter.addWidget(self.peak_toolbar_widget)
        self._panel_splitter.addWidget(self.peak_table)
        self._panel_splitter.setStretchFactor(0, 0)
        self._panel_splitter.setStretchFactor(1, 1)
        self._panel_splitter.setStretchFactor(2, 0)
        self._panel_splitter.setStretchFactor(3, 0)
        self._panel_splitter.setSizes([90, 420, 76, 150])
        self.viewer.peak_clicked.connect(self._on_viewer_peak_clicked)
        self.viewer.manual_peak_requested.connect(self._on_manual_peak_added)
        self.viewer.peaks_box_selected.connect(self._on_peaks_box_selected)
        self.viewer.show_1d_button.toggled.connect(self._on_viewer_1d_toggled)
        self.file_list.setMaximumWidth(16777215)  # Remove horizontal width restrictions.
        layout.addWidget(self._panel_splitter)
        self.refresh()

    @property
    def manager(self) -> ProjectManager:
        return self._manager

    @manager.setter
    def manager(self, value: ProjectManager) -> None:
        self._manager = value
        self.controller.set_manager(value)

    def set_context(self, exp_id: str, data_id: str = "") -> None:
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id or ""
        self.refresh()

    def _sync_peak_ui_visibility(self) -> None:
        """Peak Correlation UI Visibility: 1D View or Projection File Open Hide all
        (0.2.199-patch29db)."""
        show = (
            self.manager.project is not None
            and not self._viewer_1d_active
            and not self._projection_active
        )
        self.peak_table.setVisible(show)
        self.peak_toolbar_widget.setVisible(show)
        self.viewer.peak_label.setVisible(show)

    def _display_key(self) -> tuple[str, str]:
        return (self._current_exp_id or "", self._current_data_id or "")

    def _save_display_state(self, *_args) -> None:
        """Instantly record the current display adjustment into the current data
        (0.2.199-patch29fz)."""
        key = self._display_key()
        if not all(key):
            return
        try:
            self._display_states[key] = {
                "level_slider": int(self.viewer.level_slider.value()),
                "level_count": int(self.viewer.count_slider.value()),
                "aspect": int(self.viewer.aspect_slider.value()),
                "peak_size": float(self.peak_size_spin.value()),
            }
            # 0.2.199-patch29ga: Mirror to d_xxx/ui_state.json.
            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "spectrum",
                dict(self._display_states[key]),
            )
        except Exception:  # noqa: BLE001 - Record/Persistence failure does not block adjustment.
            pass

    def _restore_display_state(self) -> None:
        """After the spectrum is successfully loaded, the display adjustment is restored according
        to the current data; if there is no record, the default is used and the file is dropped
        (0.2.199-patch29fz, user:threshold/contour start and other adjustments require data
        isolation)."""
        key = self._display_key()
        if not all(key):
            return
        state = self._display_states.get(key)
        if state is None:
            defaults = {
                "level_slider": 31,
                "level_count": 8,
                "aspect": 0,
                "peak_size": 1.5,
            }
            # 0.2.199-patch29ga: Restore from d_xxx/ui_state.json after reboot.
            try:
                from gui.per_data_records import load_ui_state

                file_state = (
                    load_ui_state(
                        self.manager, self._current_exp_id,
                        self._current_data_id,
                    ).get("spectrum")
                    or {}
                )
                for field in defaults:
                    if file_state.get(field) is not None:
                        defaults[field] = file_state[field]
            except Exception:  # noqa: BLE001 - If the read fails, use the default.
                pass
            state = defaults
            self._display_states[key] = dict(state)
        try:
            self.viewer.level_slider.setValue(
                int(state.get("level_slider", 31))
            )
            self.viewer.count_slider.setValue(
                int(state.get("level_count", 8))
            )
            self.viewer.aspect_slider.setValue(
                int(state.get("aspect", 0))
            )
            self.viewer.refresh_levels()
            self.peak_size_spin.setValue(
                float(state.get("peak_size", 1.5))
            )
        except Exception:  # noqa: BLE001 - Spectrum display will not be blocked if recovery fails.
            pass

    def refresh(self) -> None:
        """Refresh the spectrum file list; hide the list when there is no file (avoid the blank
        space in the lower right corner). 0.2.88: Do not automatically display the spectrum --
        click on the file list or open the Pipeline "Show Spectrum" button; clear the old
        spectrum when switching sample data to avoid leaving the previous spectrum."""
        self.file_list.clear()
        has_context = (
            self.manager.project is not None
            and bool(self._current_exp_id)
            and bool(self._current_data_id)
        )
        self.add_peak_button.setEnabled(has_context)
        self.select_peaks_button.setEnabled(has_context)
        self.peak_size_spin.setEnabled(has_context)
        self._update_delete_button()
        self.import_poky_button.setEnabled(has_context)
        if self.manager.project is None or not self._current_exp_id:
            self.file_list.setVisible(False)
            self.peak_table.setVisible(False)
            self.peak_toolbar_widget.setVisible(False)
            self._spectrum3d_panel.setVisible(False)
            return
        paths = self._spectrum_paths()
        for path in paths:
            self.file_list.addItem(path.name)
        self.file_list.setVisible(bool(paths))
        self._sync_peak_ui_visibility()
        self.export_poky_button.setEnabled(False)
        self.save_peaks_button.setEnabled(False)
        if not paths:
            self._current_spectrum = None
            self._spectrum3d_panel.clear()
            # If there is no spectrum in the spectrum file folder, leave the right side blank.
            self.viewer.clear()
            self._clear_peaks()
            return
        if self._current_spectrum is not None and self._current_spectrum not in paths:
            # The context has been switched: no automatic display, clear the old spectrum (wait for
            # the user to click file / "show spectrum").
            self._current_spectrum = None
            self._spectrum3d_panel.clear()
            self.viewer.clear()
            self._clear_peaks()

    def load_current_spectrum(self) -> bool:
        """Load the main spectrum of the current sample data (the projection file can be viewed
        directly by clicking on the list)."""
        paths = self._spectrum_paths()
        if not paths:
            return False
        main = [
            p for p in paths if not self._is_projection_name(p.name)
        ] or paths
        first = main[0]
        if not self.open_spectrum(first):
            return False
        self._current_spectrum = first
        self._load_peaks(first)
        return True

    def _spectrum_paths(self) -> list[Path]:
        """Current experiment/under sample data spectrum file (schema 1.4 data-level spectra/). The
        data-level context only lists this data; the experimental-level context summarizes all
        data under the experiment (not deleted). The back-end final spectrum is named according
        to dataset_id (such as hsqc_2d.ft2), and no prefix (0.2.112) is assumed."""
        exp_id = self._current_exp_id
        data_id = self._current_data_id
        if self.manager.project is None or not exp_id:
            return []
        entry = self.manager.project.experiment(exp_id)
        if entry is None:
            return []
        data_ids = (
            [data_id]
            if data_id
            else [d.id for d in (entry.data or []) if not getattr(d, "trashed", False)]
        )
        files: list[Path] = []
        for did in data_ids:
            spectra_dir = self.manager.data_dir(exp_id, did, "spectra")
            for ext in ("ft1", "ft2", "ft3"):
                try:
                    files.extend(spectra_dir.glob(f"*.{ext}"))
                except OSError:  # noqa: PERF203 - Directory Missing/Skip if unreadable.
                    continue
        return sorted(set(files))


    def open_spectrum(self, path: Path, name: str | None = None) -> bool:
        """Load spectrum into the viewer; return False on failure (no pop-up window, prompt
        determined by the caller)..ft3 takes the 3D viewing path (Contract §10): Bind Spectrum3D
        and display the default slice, 3D The panel provides a flat surface/slice/Projection
        switching;.ft2 takes the two-dimensional overlay."""
        # 0.2.199-patch29fz: Display adjustment according to data isolation -- Each adjustment has
        # been recorded into the current data immediately; after the spectrum is loaded
        # successfully, _restore_display_state() restores the respective values, and no longer takes
        # a snapshot before opening the spectrum file path. 0.2.199-patch29db: Projection file hides
        # all peak related UI,No peak correlation/peak operation.
        self._projection_active = (
            path.suffix.lower() == ".ft2"
            and self._is_projection_name(path.name)
        )
        # 0.2.199-patch29dh(user):axis sequence/Tags only start with The.ft3 header shall prevail,
        # and metadata will not be transmitted to the loader nuclear/Label (the software handles
        # axis rearrangement, and the metadata collection sequence cannot be divulged).
        try:
            if path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                size = path.stat().st_size if path.is_file() else 0
                if size >= self._ASYNC_FT3_MIN_BYTES:
                    # 0.2.89: Large 3D spectrum is loaded in the background to avoid UI being
                    # unresponsive for a long time.
                    self._current_spectrum = path
                    self.status_message.emit(
                        tr(
                            "Loading 3D spectrum in the background: {p0} ({p1} "
                            "MB)",
                            p0=path.name,
                            p1=size // (1024 * 1024),
                        )
                    )
                    self._load_ft3_async(path)
                    return True
                self._current_spectrum = path
                # Capture memory state before set_spectrum3d(will reset the plane/Project and
                # trigger save).
                state = self._viewer3d_state.get((self._current_exp_id, self._current_data_id))
                self._spectrum3d_panel.set_spectrum3d(
                    Spectrum3D.load_from_ft3(path, lazy=True)
                )
                self._spectrum3d_panel.setVisible(True)
                if state:
                    self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
                self._render_3d_view()
                self._restore_display_state()
                return True
            if path.suffix.lower() == ".ft1":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(
                    Spectrum1D.load_from_ft1(path), name=name or path.stem
                )
                self._viewer_1d_active = True
                self._sync_peak_ui_visibility()
                self._restore_display_state()
                return True
            if path.suffix.lower() == ".fid":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(
                    Spectrum1D.load_from_fid(path), name=path.stem
                )
                self._viewer_1d_active = True
                self._sync_peak_ui_visibility()
                self._restore_display_state()
                return True
            from viewer.spectrum import Spectrum

            if self._is_projection_name(path.name):
                proj_spec = self._load_projection_ft2(path)
                if proj_spec is None:
                    return False
                spectrum = proj_spec
                # 0.2.199-patch29db: Project file Not loading/correlation peak, clear peak table and
                # markers.
                self._clear_peaks()
            else:
                spectrum = Spectrum.load_from_ft2(path)
        except Exception as exc:  # noqa: BLE001 - the caller still prompts; the cause goes to the task log
            self.log_message.emit(
                tr(
                    "2D spectrum loading failed {p0}: {p1}",
                    p0=path.name,
                    p1=describe_exception(exc),
                )
            )
            return False
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self.viewer.add_spectrum(spectrum, name=name or path.stem)
        self._viewer_1d_active = False
        self._restore_display_state()
        self._sync_peak_ui_visibility()
        return True


    def open_with_peaks(self, path: Path, name: str | None = None) -> bool:
        """Open spectrum and load its peak table (for the main window to call to avoid external
        access to private members)."""
        target = Path(path)
        if not self.open_spectrum(target, name=name):
            return False
        self._current_spectrum = target
        self._load_peaks(target)
        return True

    def _load_ft3_async(self, path: Path) -> None:
        """The background thread reads the large.ft3, and after completion, the signal is sent back
        to the main thread to bind the rendering."""
        import threading

        def worker() -> None:
            try:
                from viewer.spectrum import Spectrum3D

                spectrum3d = Spectrum3D.load_from_ft3(path, lazy=True)
                self._ft3_ready.emit(path, spectrum3d)
            # Errors are unified back to the main thread prompt.
            except Exception as exc:  # noqa: BLE001 -
                self._ft3_failed.emit(path, describe_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ft3_ready(self, path, spectrum3d) -> None:
        """Large.ft3 loading completed (main thread): Bind the 3D panel and render; ignore if
        switched."""
        if path != self._current_spectrum:
            return
        state = self._viewer3d_state.get((self._current_exp_id, self._current_data_id))
        self._spectrum3d_panel.set_spectrum3d(spectrum3d)
        self._spectrum3d_panel.setVisible(True)
        if state:
            self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
        self._render_3d_view()
        self._restore_display_state()
        self._load_peaks(path)
        self.status_message.emit(tr("Loaded 3D spectrum: {p0}", p0=path.name))

    def _on_ft3_failed(self, path, message: str) -> None:
        """Large.ft3 failed to load (main thread)."""
        if path != self._current_spectrum:
            return
        self._current_spectrum = None
        self.status_message.emit(tr("3D spectrum loading failed: {p0}", p0=message))
        # 0.2.199-patch29hz: Failure cannot leave just one line in the status bar (often ignored),
        # and write it to the task log panel simultaneously.
        self.log_message.emit(tr("3D spectrum loading failed {p0}: {p1}", p0=path.name, p1=message))

    def _current_3d_nuclei(self) -> list[str] | None:
        """The complete kernel name of each F axis (F1/F2/F3) of the currently loaded 3D spectrum;
        kernel agnostic returns None. 0.2.199-patch29dk(user):.list/Peak table display according
        to external convention, internally interpreted according to F logic -- Here the kernel
        is taken from the loaded spectrum axis label (only the file header source is used, no
        metadata is used)."""
        s3d = self._spectrum3d_panel.spectrum3d
        axes3 = getattr(s3d, "axes", None) if s3d is not None else None
        if not axes3 or len(axes3) != 3:
            return None
        symbols = {
            "H": "1H", "N": "15N", "C": "13C",
            "F": "19F", "P": "31P", "D": "2H",
        }
        full = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}
        nuclei: list[str] = []
        for axis in axes3:
            label = str(getattr(axis, "label", "") or "").strip()
            if len(label) > 1 and label[-1:].lower() in ("x", "y", "z"):
                label = label[:-1]
            nuc = symbols.get(label, label)
            nuclei.append(nuc if nuc in full else "")
        if not all(nuclei):
            return None
        return nuclei

    def _axis_nuclei(self, required: int) -> list[str] | None:
        """Returns the logical axis core (F1/F2/F3 order) according to the current sample data
        metadata; if not, None."""
        from viewer.axis_labels import nuclei_from_metadata

        if self._manager is None or not (
            self._current_exp_id and self._current_data_id
        ):
            return None
        try:
            meta_path = self._manager.data_metadata_path(
                self._current_exp_id, self._current_data_id
            )
        except Exception:  # noqa: BLE001
            return None
        if meta_path is None or not meta_path.is_file():
            return None
        try:
            import json

            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        nuclei = nuclei_from_metadata(metadata)
        if not nuclei or len(nuclei) != required:
            return None
        return nuclei

    def _axis_labels(self, required: int) -> tuple[str, ...] | None:
        """Generate axis names based on the core information of the current sample data metadata
        (F1/F2/F3 -> H/N/C). Return None when dimension The numbers don’t match/none metadata is
        used (the caller falls back to F1/F2/F3)."""
        from viewer.axis_labels import axis_labels_from_nuclei

        nuclei = self._axis_nuclei(required)
        if not nuclei:
            return None
        return axis_labels_from_nuclei(nuclei)

    def _is_projection_name(self, name: str) -> bool:
        """Projection file identification: new named {data_id}_{coreA}-{coreB}.ft2 or old
        *_proj_*.ft2."""
        if "_proj_" in name:
            return True
        data_id = self._current_data_id or ""
        if data_id and name.startswith(f"{data_id}_") and name.endswith(".ft2"):
            body = name[len(data_id) + 1:-4]
            if "-" in body:
                return True
        return False

    def _load_projection_ft2(self, path: Path) -> object | None:
        """Load a single projection.ft2 (0.2.133, click directly to view) according to the file
        name. The kernel is parsed from the file name; display rules: abscissa priority H > N >
        C (0.2.153), transpose the data matrix if necessary. The axis parameter is first taken
        from the axis of the loaded 3D spectrum corresponding to the kernel (SW/OBS/CAR/ORIG),
        otherwise the file header slot is used. Return Pydantic Spectrum; parsing failure None."""
        import re as _re
        from types import SimpleNamespace as _Sn

        import nmrglue as ng
        import numpy as np

        from viewer.axis_labels import nucleus_symbol
        from viewer.spectrum import Spectrum, SpectrumAxis

        def _norm(nuc: str) -> str:
            return _re.sub(r"[^A-Za-z0-9]", "", str(nuc or "")).upper()

        def _base_norm(nuc: str) -> str:
            """Normalise the projection kernel name: remove the tail x/y/z index and compare (15Ny
            -> 15N)."""
            t = _norm(nuc)
            if t and t[-1] in "XYZ":
                t = t[:-1]
            return t

        name = path.name
        data_id = self._current_data_id or ""
        nuclei = self._axis_nuclei(3) or []
        a = b = None
        # 0.2.199-patch29x: Only _proj_F{n} naming can determine the logical mapping of the two axes
        # of the plane and the fixed axis (data orientation (b,a)=(remaining[0],remaining[1])); the
        # projection of the nuclear name naming cannot distinguish between repeated review,Use the
        # old same core/symbol tag.
        logical_mapped = False
        if data_id and name.startswith(f"{data_id}_") and name.endswith(".ft2"):
            body = name[len(data_id) + 1:-4]
            if "-" in body:
                parts = body.split("-", 1)
                a, b = parts[0], parts[1]
        if not (a and b):
            m = _re.search(r"_proj_F(\d)\.ft2$", name)
            if m and len(nuclei) == 3:
                fixed_axis = int(m.group(1)) - 1
                remaining = [i for i in range(3) if i != fixed_axis]
                a = nuclei[remaining[1]]
                b = nuclei[remaining[0]]
                logical_mapped = True
            else:
                # Old name _proj_NH and the like: try by suffix symbol.
                m2 = _re.search(r"_proj_([A-Za-z0-9]{2,6})\.ft2$", name)
                if m2 and len(nuclei) == 3:
                    body = m2.group(1)
                    symbols = {nucleus_symbol(n).upper(): n for n in nuclei}
                    match = [symbols.get(c.upper()) for c in body]
                    if len(match) == 2 and all(match):
                        a, b = match[0], match[1]
                else:
                    # Final fallback: load directly by file header (best effort).
                    try:
                        return Spectrum.load_from_ft2(str(path))
                    except Exception:  # noqa: BLE001
                        return None
        if not (a and b):
            return None
        try:
            dic, data = ng.pipe.read(str(path))
        except Exception:  # noqa: BLE001
            return None
        data = np.asarray(data, dtype=float)
        if data.ndim != 2:
            return None
        na, nb = _base_norm(a), _base_norm(b)
        same_nucleus = na == nb
        fixed_axis = -1
        if len(nuclei) == 3:
            for i, nuc in enumerate(nuclei):
                if _base_norm(nuc) not in (na, nb):
                    fixed_axis = i
                    break
        s3d = self._spectrum3d_panel.spectrum3d
        s3d_axes = list(getattr(s3d, "axes", []) or []) if s3d is not None else []
        # 0.2.199-patch29hd: When the panel is not loaded with 3D, lazily read the axis from the
        # same directory.ft3 as a guide -- The projection.ft2 file header is copied by proj3D.tcl to
        # the input plane header (all 15N/1H), which cannot be used for axis parameter; when double-
        # clicking the projection directly without loading.ft3 first, otherwise the projection axis
        # parameter will be completely messed up.
        if len(s3d_axes) != 3:
            from viewer.spectrum import Spectrum3D  # noqa: PLC0415

            parent = path.parent
            ft3_candidates = []
            if data_id:
                ft3_candidates.append(parent / f"{data_id}.ft3")
            ft3_candidates += list(parent.glob("*.ft3"))
            for ft3 in ft3_candidates:
                if not ft3.is_file() or not ft3.name.endswith(".ft3"):
                    continue
                try:
                    s3d_axes = list(Spectrum3D.load_from_ft3(ft3, lazy=True).axes or [])
                # If the brother tree reading fails, the return will be empty and the file header
                # will be returned.
                except Exception:  # noqa: BLE001 -
                    s3d_axes = []
                if len(s3d_axes) == 3:
                    break
        # 0.2.199-patch29w: Re-review (HNN double 15N) is distinguished by logical axis index -- The
        # projected core cannot be distinguished as F1 or F2's 15N by the core name alone, and the
        # fixed axis is used to derive the logical index of the remaining two axes (15Nx/15Ny).
        labels3 = None
        if len(nuclei) == 3:
            from viewer.axis_labels import axis_labels_from_nuclei as _alfn

            labels3 = _alfn(nuclei)
            # 0.2.199-patch29y:HNN Double 15N -- Nx corresponds to the N of HSQC (amide N(i),
            # directly connected to 1H). According to HNN convention, it is F2(t2);F1=N(i-1). The
            # order N is marked as Ny.
            if (
                len({_base_norm(n) for n in nuclei}) < len(nuclei)
                and _base_norm(nuclei[0]) == "15N"
                and _base_norm(nuclei[1]) == "15N"
            ):
                labels3 = ("Ny", "Nx", str(labels3[2]))
        x_params = y_params = None
        # 0.2.199-patch29aj: The parameter is first positioned according to the logical index symbol
        # (15Ny -> F1, 15Nx -> F2, the same core axis can also be distinguished), and then based on
        # the basic core name; the proj3D same core plane file header FDF*LABEL is unreliable, and
        # the file header slot is used last.
        if len(s3d_axes) == 3:
            sym_a, sym_b = nucleus_symbol(a), nucleus_symbol(b)
            # The original meaning of 0.2.133 is to "take the parameter from the axis of the
            # corresponding kernel of the loaded 3D spectrum"; the original metadata kernel order
            # (nuclei/labels3) is indexed by index i s3d_axes, and when the two orders are
            # inconsistent (CANH metadata is the Bruker collection order C,N,H, the loaded spectrum
            # logical order is N,H,C), there will be a mismatch. Instead, match the axis label of
            # the loaded spectrum itself (including 15Ny/15Nx index).
            s3d_syms = [str(ax.label) for ax in s3d_axes]
            for i, sym in enumerate(s3d_syms):
                if sym == sym_a and x_params is None:
                    x_params = s3d_axes[i]
                if sym == sym_b and y_params is None:
                    y_params = s3d_axes[i]
            if x_params is None or y_params is None:
                for i, ax in enumerate(s3d_axes):
                    if _base_norm(str(ax.label)) == na and x_params is None:
                        x_params = s3d_axes[i]
                    if _base_norm(str(ax.label)) == nb and y_params is None:
                        y_params = s3d_axes[i]
        if x_params is None or y_params is None:
            # Axis parameter is missing: use the file header slot to find out (consistent with
            # 0.2.126).
            x_params = _Sn(
                label=nucleus_symbol(a),
                size=int(data.shape[1]),
                sw_hz=float(dic.get("FDF2SW", 1.0) or 1.0),
                obs_mhz=float(dic.get("FDF2OBS", 1.0) or 1.0),
                carrier_ppm=float(dic.get("FDF2CAR", 0.0) or 0.0),
                orig_hz=float(dic.get("FDF2ORIG", 0.0) or 0.0),
            )
            y_params = _Sn(
                label=nucleus_symbol(b),
                size=int(data.shape[0]),
                sw_hz=float(dic.get("FDF1SW", 1.0) or 1.0),
                obs_mhz=float(dic.get("FDF1OBS", 1.0) or 1.0),
                carrier_ppm=float(dic.get("FDF1CAR", 0.0) or 0.0),
                orig_hz=float(dic.get("FDF1ORIG", 0.0) or 0.0),
            )
        # 0.2.153: Abscissa priority H > N > C (transpose the data matrix if necessary).
        _X_PRIORITY = {"1H": 0, "15N": 1, "13C": 2}
        if _X_PRIORITY.get(_base_norm(a), 100) > _X_PRIORITY.get(_base_norm(b), 100):
            data = data.T
            x_params, y_params = y_params, x_params
            a, b = b, a
            na, nb = nb, na
        if logical_mapped and labels3 is not None:
            remaining = [i for i in range(3) if i != fixed_axis]
            x_label = str(labels3[remaining[1]])
            y_label = str(labels3[remaining[0]])
        elif same_nucleus:
            # The homonuclear projection plane has no direct dimension semantics: according to the
            # display axis, take x/y (x axis is x); with index (15Ny -> Ny), it is displayed
            # directly, and the pure nuclear name is supplemented with index (0.2.199-patch29aj).
            from viewer.axis_labels import nucleus_symbol as _nsym

            sx, sy = _nsym(a), _nsym(b)
            x_label = sx if sx[-1:].upper() in ("X", "Y", "Z") else sx + "x"
            y_label = sy if sy[-1:].upper() in ("X", "Y", "Z") else sy + "y"
        else:
            x_label, y_label = nucleus_symbol(a), nucleus_symbol(b)
        # 0.2.199-patch29x:HNN Equal weight review projection -- The X axis corresponds to the
        # N(15N) of HSQC. The 15N-15N plane puts the Nx(F1) with a smaller logical order into X; the
        # ordinary spectrum maintains H>N>C.
        if logical_mapped and labels3 is not None:
            _remaining = [i for i in range(3) if i != fixed_axis]
            _dup = len({_base_norm(n) for n in nuclei}) < len(nuclei)
            if _dup and all(_base_norm(n) == "15N" for n in (a, b)):
                if not _norm(x_label).startswith("NX"):
                    data = data.T
                    x_label, y_label = y_label, x_label
                    x_params, y_params = y_params, x_params
            elif _dup and "15N" in (_base_norm(a), _base_norm(b)):
                if not _norm(x_label).startswith("N"):
                    data = data.T
                    x_label, y_label = y_label, x_label
                    x_params, y_params = y_params, x_params

        x_axis = SpectrumAxis(
            label=x_label,
            size=int(data.shape[1]),
            sw_hz=x_params.sw_hz,
            obs_mhz=x_params.obs_mhz,
            carrier_ppm=x_params.carrier_ppm,
            orig_hz=x_params.orig_hz,
        )
        y_axis = SpectrumAxis(
            label=y_label,
            size=int(data.shape[0]),
            sw_hz=y_params.sw_hz,
            obs_mhz=y_params.obs_mhz,
            carrier_ppm=y_params.carrier_ppm,
            orig_hz=y_params.orig_hz,
        )
        spectrum = Spectrum(data, [y_axis, x_axis], source=path)
        if fixed_axis >= 0:
            spectrum.dim_indices = tuple(
                i for i in range(3) if i != fixed_axis
            )
        return spectrum

    def _load_3d_projections(self) -> dict[int, object]:
        """Compatible interface: scan all projection files and press key = fixed axis index to
        return (0.2.133)."""
        proj: dict[int, object] = {}
        if not (self._current_exp_id and self._current_data_id):
            return proj
        try:
            spectra_dir = self._manager.data_dir(
                self._current_exp_id, self._current_data_id, "spectra"
            )
        except Exception:  # noqa: BLE001
            return proj
        data_id = self._current_data_id
        candidates: list[Path] = []
        for p in sorted(spectra_dir.glob(f"{data_id}_*.ft2")):
            if p.name == f"{data_id}.ft2":
                continue
            if p not in candidates:
                candidates.append(p)
        for p in sorted(spectra_dir.glob("*_proj_*.ft2")):
            if p not in candidates:
                candidates.append(p)
        for path in candidates:
            try:
                spec = self._load_projection_ft2(path)
            except Exception:  # noqa: BLE001
                continue
            if spec is None:
                continue
            indices = getattr(spec, "dim_indices", ())
            if len(indices) == 2:
                fixed = next((i for i in range(3) if i not in indices), None)
                if fixed is not None:
                    proj[fixed] = spec
        return proj


    def _render_3d_view(self) -> None:
        """Press 3D Panel current plane/slice/Projection renders 2D view and rehangs peak
        markers."""
        spectrum = self._spectrum3d_panel.current_spectrum()
        if spectrum is None:
            return
        base = self._current_spectrum.stem if self._current_spectrum else "3D"
        # 0.2.199-patch10: Slice switch in-situ update outline (not clear/reconstruction) to avoid
        # flickering and slowness; Change spectrum/When changing the plane update_spectrum_data
        # Automatically fall back clear+add.
        self.viewer.update_spectrum_data(
            spectrum,
            name=self._spectrum3d_panel.current_name(base),
        )
        if self._peaks:
            self.viewer.set_peaks(self._peaks)
    def _save_3d_state(self, *_args) -> None:
        """Remember the 3D viewing plane of the current sample data (0.2.133 slice mode only)."""
        if self._current_data_id:
            self._viewer3d_state[(self._current_exp_id, self._current_data_id)] = (
                self._spectrum3d_panel.plane_combo.currentIndex()
            )

    def _on_file_clicked(self, item) -> None:
        paths = [p for p in self._spectrum_paths() if p.name == item.text()]
        if not paths:
            return
        if not self.open_spectrum(paths[0]):
            self.viewer.clear()
        else:
            self._current_spectrum = paths[0]
        self._load_peaks(paths[0])

    # ------------------------------------------------------------------
    # Peak table (.list mainly, old CSV compatible with reading) and spectrum two-way linkage + edit
    # writeback.
    # ------------------------------------------------------------------
    def _peak_file_path(self, spectrum_path: Path) -> Path | None:
        """Peak table file:.list takes priority (peak table is list), and the old CSV is compatible
        with fallback."""
        if self.manager.project is None:
            return None
        try:
            if self._current_data_id:
                peaks_dir = self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
                for suffix in (".list", ".csv"):
                    candidate = peaks_dir / (
                        f"{self._current_exp_id}-{self._current_data_id}{suffix}"
                    )
                    if candidate.is_file():
                        return candidate
        except Exception:  # noqa: BLE001
            pass
        return None

    def _load_peaks(self, spectrum_path: Path) -> None:
        self._clear_peaks()
        if self._projection_active:
            # 0.2.199-patch29db: Projection file Do not do any peak correlation/peak operation.
            return
        peak_path = self._peak_file_path(spectrum_path)
        if peak_path is None:
            return
        if peak_path.suffix.lower() == ".list":
            # 0.2.199-patch29dk: 3D is interpreted according to the external convention
            # w1=15N/w2=13C/w3=1H, and is mapped back to the internal F1/F2/F3 through the current
            # spectrum core name.
            peaks = import_peaks_poky(
                peak_path, nuclei=self._current_3d_nuclei()
            )
        else:
            peaks = load_peaks(peak_path)
        if not peaks:
            return
        self._peaks = self._assign_peak_ids(peaks)
        self._populate_peak_table()
        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()
        self._sync_peak_ui_visibility()

    def _set_peak_columns(self, is_3d: bool) -> None:
        # 0.2.199-patch29dk(user):.list and peak table display are consistent with the external Poky
        # convention -- 3D w1=15N/w2=13C/w3=1H(N,C,H); the internal row key is still F1/F2/F3,
        # arranged by core name.
        keys: list[str]
        header_map: dict[str, str] = {}
        if is_3d:
            s3d = self._spectrum3d_panel.spectrum3d
            axes3 = getattr(s3d, "axes", None)
            nuclei3 = self._current_3d_nuclei()
            if (
                axes3
                and len(axes3) == 3
                and nuclei3
                and set(nuclei3) == {"15N", "13C", "1H"}
            ):
                order = [nuclei3.index(n) for n in ("15N", "13C", "1H")]
                keys = (
                    ["Peak_ID", "label"]
                    + [f"F{i + 1}_shift" for i in order]
                    + ["Intensity", "SN"]
                )
                header_map = {
                    f"F{i + 1}_shift": f"{axes3[i].label}_shift"
                    for i in order
                }
            else:
                keys = [
                    "Peak_ID", "label", "F1_shift", "F2_shift", "F3_shift",
                    "Intensity", "SN",
                ]
                if axes3 and len(axes3) == 3:
                    header_map = {
                        f"F{i + 1}_shift": f"{axes3[i].label}_shift"
                        for i in range(3)
                    }
        else:
            keys = ["Peak_ID", "label", "H_shift", "N_shift", "Intensity", "SN"]
        tuple_keys = tuple(keys)
        if tuple_keys == self._peak_keys:
            return
        self._peak_keys = tuple_keys
        self.peak_table.setColumnCount(len(tuple_keys))
        # 0.2.199-patch29dj: When the 3D spectrum is not loaded, the metadata core name is not used,
        # and the F1/F2/F3 neutral name is kept; after the spectrum is loaded, _load_peaks refills
        # the column name (axes3 label).
        self.peak_table.setHorizontalHeaderLabels(
            [
                (
                    "Assignment ✓"
                    if self.viewer.peak_labels_visible
                    else "Assignment ✗"
                )
                if k == "label"
                else header_map.get(k, k)
                for k in tuple_keys
            ]
        )

    def _ensure_assignment_widgets(self) -> None:
        """Create on demand/destroy Assignment editing component: Only build QWidget for visible
        rows (+/- buffer). 0.2.199-patch29dc: Thousands of rows of 3D peak tables are built row
        by row. _AssignmentCell (2-3 input boxes per row) is the main cause of lagging; it is
        changed to lazy creation in the viewport and scrolling maintenance. Rows without
        components are displayed with label item text, read/Save and go back to text."""
        table = self.peak_table
        n = table.rowCount()
        if n <= 0 or "label" not in self._peak_keys:
            return
        label_col = self._peak_keys.index("label")
        first = table.rowAt(0)
        last = table.rowAt(max(table.viewport().height() - 1, 0))
        if first < 0 and last < 0:
            return
        first = max(0, first - _ASSIGNMENT_WIDGET_BUFFER)
        last = min(n - 1, last + _ASSIGNMENT_WIDGET_BUFFER)
        is_3d = bool(self._peaks) and "F1_shift" in self._peaks[0]
        for row in range(n):
            has = table.cellWidget(row, label_col) is not None
            in_view = first <= row <= last
            if has and not in_view:
                # Move out of the viewport: destroy the component, write the label back to the item
                # text display.
                table.setCellWidget(row, label_col, None)
                item = table.item(row, label_col)
                if item is not None:
                    label = (
                        str(self._peaks[row].get("label", "") or "")
                        if 0 <= row < len(self._peaks)
                        else ""
                    )
                    item.setText(label)
            elif not has and in_view:
                label = (
                    str(self._peaks[row].get("label", "") or "")
                    if 0 <= row < len(self._peaks)
                    else ""
                )
                widget = _AssignmentCell(3 if is_3d else 2, row, label)
                widget.edited.connect(self._on_assignment_cell_edited)
                table.setCellWidget(row, label_col, widget)
                item = table.item(row, label_col)
                if item is not None:
                    item.setText("")

    def _populate_peak_table(self) -> None:
        """Write self._peaks into the table (2D/3D column automatically switches)."""
        is_3d = bool(self._peaks) and "F1_shift" in self._peaks[0]
        self._set_peak_columns(is_3d)
        label_col = self._peak_keys.index("label")
        self._loading_peaks = True
        try:
            self.peak_table.setRowCount(len(self._peaks))
            for row, peak in enumerate(self._peaks):
                for col, key in enumerate(self._peak_keys):
                    self.peak_table.setItem(
                        row, col, QTableWidgetItem(str(peak.get(key, "")))
                    )
                # 0.2.199-patch29dc: No longer create the Assignment editing component line by line
                # (thousands of lines are stuck), the label is first displayed as item text, and the
                # visible line is replaced by the segment input box component by
                # _ensure_assignment_widgets as needed; read/Save and go cellWidget/text fallback
                # The first cell of the line saves the complete peak dict (fields outside editing
                # such as label are retained along the way).
                self.peak_table.item(row, 0).setData(0x0100, dict(peak))
            self.peak_table.setColumnWidth(label_col, 142 if is_3d else 94)
        finally:
            self._loading_peaks = False
        self._ensure_assignment_widgets()

    def _table_peaks(self) -> list[dict]:
        """Read the current contents of the table back into a peak dict list (the unedited line is
        an empty string)."""
        peaks: list[dict] = []
        for row in range(self.peak_table.rowCount()):
            peak: dict = {}
            row_item = self.peak_table.item(row, 0)
            if row_item is not None:
                stored = row_item.data(0x0100)
                if isinstance(stored, dict):
                    peak.update(stored)
            for col, key in enumerate(self._peak_keys):
                if key == "label":
                    # 0.2.199-patch29cr:label is merged and read from the segment input box
                    # component (the item text has been cleared).
                    widget = self.peak_table.cellWidget(row, col)
                    if widget is not None and hasattr(widget, "merged_text"):
                        peak[key] = widget.merged_text()
                    else:
                        item = self.peak_table.item(row, col)
                        peak[key] = item.text() if item is not None else ""
                    continue
                item = self.peak_table.item(row, col)
                peak[key] = item.text() if item is not None else ""
            peaks.append(peak)
        return peaks

    def _update_delete_button(self) -> None:
        """Delete peak button: Available when there is a peak table (automatic/Manual) and data is
        selected (0.2.199-patch29ba)."""
        has_context = bool(
            self.manager.project is not None
            and self._current_exp_id
            and self._current_data_id
        )
        self.delete_peak_button.setEnabled(
            has_context and self.peak_table.rowCount() > 0
        )

    def _sync_peaks_in_memory(self) -> None:
        """Synchronize memory _peaks after table editing (do not rebuild viewer immediately to
        avoid lagging)."""
        if self._loading_peaks:
            return
        self._peaks = self._table_peaks()
        self._update_delete_button()

    def _on_peak_cell_edited(self, item) -> None:
        """Peak table cell editing: memory synchronization; the Assignment column is managed by the
        segment input box component (0.2.199-patch29cp) and is not processed here."""
        if self._applying_label_format or self._loading_peaks:
            return
        if item is None:
            return
        if self.peak_table.column(item) == self._peak_keys.index("label"):
            return
        self._sync_peaks_in_memory()

    def _on_assignment_cell_edited(self, row: int) -> None:
        """Assignment segment input box changes: segment by segment Poky normalisation (empty ->
        ?), merged into a fixed hyphen label, immediately effective to the label on the picture
        (0.2.199-patch29cp)."""
        if self._loading_peaks or self._applying_label_format:
            return
        widget = self.peak_table.cellWidget(
            row, self._peak_keys.index("label")
        )
        if widget is None or not hasattr(widget, "merged_text"):
            return
        label = widget.merged_text()
        # 0.2.199-patch29cr: Only update the memory and viewer, do not write the item text (to avoid
        # overlap).
        if 0 <= row < len(self._peaks):
            self._peaks[row]["label"] = label
        self.viewer.apply_label_edit(row, label)

    def _on_add_peak_toggled(self, checked: bool) -> None:
        """Add peak switch: After turning it on, click spectrum to add peak (adsorb peak top); with
        1D/Select mutually exclusive."""
        if checked:
            self.select_peaks_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
        self.viewer.set_peak_click_mode("add" if checked else "select")
        self.add_peak_button.setText("Add peak mode: ON" if checked else "Add peak mode")

    def _on_select_mode_toggled(self, checked: bool) -> None:
        """Selection mode: left-click and drag the box to select peaks; mutually exclusive with
        1D/Add peak."""
        if checked:
            self.add_peak_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_peak_click_mode("select")
        self.viewer.set_box_select_mode(checked)
        self.select_peaks_button.setText("Select mode: ON" if checked else "Select mode")

    def _on_viewer_1d_toggled(self, checked: bool) -> None:
        """1D View close selection when open/Peak mode, and hide the peak-related controls
        (0.2.199-patch29bd)."""
        if checked:
            self.select_peaks_button.setChecked(False)
            self.add_peak_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
            self.viewer.set_peak_click_mode("select")
        self._viewer_1d_active = bool(checked)
        self.viewer.peak_label.setVisible(not checked)
        self.refresh()

    def _on_peaks_box_selected(self, rows: list[int]) -> None:
        """Frame peak selection: Linked peak table multi-selection (programmed row selection,
        single peak flashing is not triggered)."""
        model = self.peak_table.selectionModel()
        if model is None:
            return
        self._syncing_table_selection = True
        try:
            model.clearSelection()
            for row in rows:
                if 0 <= row < self.peak_table.rowCount():
                    model.select(
                        self.peak_table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
        finally:
            self._syncing_table_selection = False

    def _on_manual_peak_added(self, peak: dict) -> None:
        """Click spectrum to add peaks: After adsorption, they are added to the peak table
        (automatic numbering) and displayed immediately."""
        if not (self.manager.project is not None and self._current_exp_id):
            return
        next_id = (
            max((int(p.get("Peak_ID", 0) or 0) for p in self._peaks), default=0) + 1
        )
        peak["Peak_ID"] = next_id
        self._peaks.append(peak)
        self._populate_peak_table()
        self.viewer.set_peaks(self._peaks)
        self._update_delete_button()
        self.save_peaks_button.setEnabled(True)

    def _on_delete_peak(self) -> None:
        rows = sorted(
            {index.row() for index in self.peak_table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows:
            return
        remove = set(rows)
        self._loading_peaks = True
        try:
            for row in rows:
                self.peak_table.removeRow(row)
        finally:
            self._loading_peaks = False
        # 0.2.199-patch29bg: Delete directly from the memory peak list, Avoid selection caused by
        # rereading the entire table/Remove lag.
        self._peaks = [
            peak for i, peak in enumerate(self._peaks) if i not in remove
        ]
        self.viewer.set_peaks(self._peaks)
        self.save_peaks_button.setEnabled(bool(self._peaks))
        self._update_delete_button()

    def _on_import_poky(self) -> None:
        if self.manager.project is None or not self._current_exp_id:
            return
        start = str(Path.home())
        try:
            start = str(
                self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
            )
        except Exception:  # noqa: BLE001
            pass
        path, _ = QFileDialog.getOpenFileName(
            self, tr(
                "import Poky peak "
                "table",
            ), start, tr(
                "Poky peak table (*.list);; all files "
                "(*)",
            )
        )
        if not path:
            return
        try:
            peaks = import_peaks_poky(
                path, nuclei=self._current_3d_nuclei()
            )
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, tr("import failed"), str(exc))
            return
        if not peaks:
            InfoDialog.show_info(self, tr(
                "import results",
            ), (
                tr(
                "There are no parsable peak lines in the "
                "file",
            )
            ))
            return
        self._peaks = self._assign_peak_ids(peaks)
        self._populate_peak_table()
        # 0.2.199-patch29fu:viewer has the same origin as the panel (the list after Peak_ID is
        # added).
        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()
        # Replace the peak table association (without overwriting file): Import only updates the
        # memory peak table, and when you click "Save Peak Table", write to disk as Poky.list.
        InfoDialog.show_info(
            self.import_poky_button,
            tr("import completed"),
            tr(
                "The current peak table association was replaced with the Poky peak table ({p0} "
                "peak(s));\n(click \"Save peak table\" to write back to the .list "
                "file)",
                p0=len(peaks),
            ),
        )

    def _on_save_peaks(self) -> None:
        """The peak table is written back to data/peaks/<exp>-<data>.list and registered
        manual_peaks to run."""
        if self.manager.project is None or not self._current_exp_id:
            InfoDialog.show_info(self, tr("hint"), tr("Please select sample data first"))
            return
        if not self._current_data_id:
            InfoDialog.show_info(self, tr("hint"), tr("Please select the sample data node first"))
            return
        peaks = self._table_peaks()
        try:
            self.controller.set_manager(self.manager)
            list_path = self.controller.save_peaks_manual(
                None,
                peaks,
                exp_id=self._current_exp_id,
                data_id=self._current_data_id,
                nuclei=self._current_3d_nuclei(),
            )
        except Exception as exc:  # noqa: BLE001 - Unified error prompts.
            InfoDialog.show_info(self, tr("save failed"), describe_exception(exc))
            return
        self._peaks = peaks
        self.viewer.set_peaks(peaks)
        self.save_peaks_button.setEnabled(True)
        self.peaks_saved.emit()
        InfoDialog.show_info(self, tr(
            "save completed",
        ), tr(
            "peak table has been "
            "written:\n{p0}",
            p0=list_path,
        ))

    def _export_peaks_poky(self) -> None:
        """Export the current peak table as Poky.list; disabled when there is no peak
        table/spectrum."""
        if not self._peaks or self.manager.project is None:
            return
        default = None
        try:
            default = (
                self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "peaks"
                )
                / f"{self._current_exp_id}-{self._current_data_id}.list"
            )
        except Exception:  # noqa: BLE001
            default = None
        start = str(default.parent) if default is not None else str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, tr("export Poky peak table"), str(default) if default else start,
            tr("Poky peak table (*.list);; all files (*)"),
        )
        if not path:
            return
        try:
            export_peaks_poky(
                path, self._peaks, nuclei=self._current_3d_nuclei()
            )
            InfoDialog.show_info(self, tr(
                "export completed",
            ), tr(
                "Poky peak table exported: "
                "{p0}",
                p0=path,
            ))
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, tr("export failed"), str(exc))

    def _export_peaks_aligned(self) -> None:
        """Export after alignment: Select reference.list -> Overall translation search -> Export
        peak table after translation. 0.2.199-patch29fw(user): The exported reference spectrum
        can be any.list file, decoupled from the peak selection reference; press the overall
        translation to move the current peak file and output it, based on the reference."""
        if not self._peaks or self.manager.project is None:
            return
        ref_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Select export reference peak file (.list)"),
            "",
            tr("Poky peak table (*.list);; all files (*)"),
        )
        if not ref_path:
            return
        from workflow.peak_align import (
            MIN_ACCEPTABLE_RATIO,
            align_peak_files,
            alignment_figure,
            shifted_rows,
        )

        try:
            # 0.2.199-patch29fx: Alignment tolerance can be changed in software settings (same place
            # as line width).
            from gui.settings import load_settings

            settings_tol = (
                load_settings().get("alignment_tolerance_ppm") or None
            )
            ref_path = Path(ref_path)
            ref_rows = import_peaks_poky(ref_path)
            if not ref_rows:
                InfoDialog.show_info(self, tr(
                    "Alignment export "
                    "failed",
                ), (
                    tr(
                    "Reference peak file is empty or cannot be "
                    "parsed",
                )
                ))
                return
            # 0.2.199-patch29fx: External 3D.list follows Poky convention w1=15N/w2=13C/w3=1H
            # (positional import is F1=N/F2=C/F3=H); the core name must be passed to the alignment
            # explicitly, otherwise the 3D reference line cannot resolve the common core coordinates
            # (the 2D reference comes with N_shift/H_shift and is not affected).
            ref_nuclei_ref = None
            if ref_rows and "F1_shift" in ref_rows[0]:
                ref_nuclei_ref = ["15N", "13C", "1H"]
            is_3d = "F1_shift" in (self._peaks[0] if self._peaks else {})
            nuclei = self._current_3d_nuclei() if is_3d else None
            result = align_peak_files(
                self._peaks,
                ref_rows,
                cur_nuclei=nuclei,
                ref_nuclei=ref_nuclei_ref,
                tol_ppm=settings_tol,
            )
            if result["status"] == "no_common":
                InfoDialog.show_info(
                    self, tr("Alignment export failed"), result["message"]
                )
                return
            if result["status"] == "low":
                InfoDialog.show_info(
                    self,
                    tr("Alignment rate is low"),
                    tr(
                        "Alignment rate {p0:.0%} < {p1:.0%}: check whether the reference spectrum "
                        "matches this "
                        "one",
                        p0=result["ratio"],
                        p1=MIN_ACCEPTABLE_RATIO,
                    ),
                )
                return
            out_rows = shifted_rows(self._peaks, result["shift"], nuclei)
            default = None
            try:
                default = (
                    self.manager.data_dir(
                        self._current_exp_id, self._current_data_id, "peaks"
                    )
                    / f"{self._current_exp_id}-{self._current_data_id}"
                    "_refaligned.list"
                )
            except Exception:  # noqa: BLE001
                default = None
            start = (
                str(default.parent)
                if default is not None
                else str(Path.home())
            )
            path, _ = QFileDialog.getSaveFileName(
                self,
                tr("export Poky peak table after alignment"),
                str(default) if default is not None else start,
                tr("Poky peak table (*.list);; all files (*)"),
            )
            if not path:
                return
            export_peaks_poky(path, out_rows, nuclei=nuclei)
            # Alignment check figure -> current data figures/; file name = current peak
            # table_aligned_reference peak table.
            fig_lines: list[str] = []
            try:
                cur_path = None
                if self._current_spectrum:
                    try:
                        cur_path = self._peak_file_path(
                            Path(self._current_spectrum)
                        )
                    except Exception:
                        cur_path = None
                cur_name = (
                    Path(cur_path).stem
                    if cur_path
                    else f"{self._current_exp_id}-{self._current_data_id}"
                )
                ref_name = Path(ref_path).stem
                figures_dir = self.manager.data_dir(
                    self._current_exp_id, self._current_data_id, "figures"
                )
                fig_path = figures_dir / f"{cur_name}_aligned_{ref_name}.png"
                alignment_figure(
                    self._peaks,
                    ref_rows,
                    result["shift"],
                    fig_path,
                    cur_nuclei=nuclei,
                    ref_nuclei=ref_nuclei_ref,
                    tol_ppm=settings_tol,
                    cur_label=cur_name,
                    ref_label=ref_name,
                )
                fig_lines.append(tr("Alignment check chart (PNG): {p0}", p0=fig_path))
                fig_lines.append(
                    tr("Alignment check chart (SVG): {p0}", p0=fig_path.with_suffix('.svg'))
                )
            except Exception as exc:  # noqa: BLE001 - Figure failure does not block export.
                fig_lines.append(tr("Alignment check map generation failed: {p0}", p0=exc))
            msg = (
                tr("Exported peak table after alignment: ")
                + str(path)
                + "\n"
                + tr(
                    "(alignment rate {p0:.0%}, shift "
                    "{p1})",
                    p0=result["ratio"],
                    p1=result["shift"],
                )
            )
            if fig_lines:
                msg += "\n" + "\n".join(fig_lines)
            InfoDialog.show_info(self, tr("export completed"), msg)
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, tr("Alignment export failed"), str(exc))

    def _clear_peaks(self) -> None:
        self._peaks = []
        self.peak_table.setRowCount(0)
        self.viewer.set_peaks([])
        self.export_poky_button.setEnabled(False)
        self.save_peaks_button.setEnabled(False)
        self.delete_peak_button.setEnabled(False)
        if self.add_peak_button.isChecked():
            self.add_peak_button.setChecked(False)
        if self.select_peaks_button.isChecked():
            self.select_peaks_button.setChecked(False)
        self.viewer.set_box_select_mode(False)
        self.viewer.set_peak_click_mode("select")

    @staticmethod
    def _assign_peak_ids(peaks: list[dict]) -> list[dict]:
        """Number rows that are missing Peak_ID in row order (Poky.list has no ID column)."""
        for i, peak in enumerate(peaks, start=1):
            if not (peak.get("Peak_ID") or ""):
                peak["Peak_ID"] = i
        return peaks

    def _on_viewer_peak_clicked(self, row: int) -> None:
        if 0 <= row < self.peak_table.rowCount():
            self._syncing_table_selection = True
            try:
                self.peak_table.selectRow(row)
            finally:
                self._syncing_table_selection = False

    def _on_menu_open_current_spectrum(self) -> None:
        """File menu: Open the spectrum of the current data (same effect as Pipeline's "display
        spectrum"). user 2026-09-11: This button is easily clicked when there is no spectrum. It
        must be clearly prompted that "the current data has not yet generated a spectrum",
        rather than silently responding."""
        if not self._current_exp_id:
            InfoDialog.show_info(self, tr("hint"), tr("No data is currently selected"))
            return
        if not self.load_current_spectrum():
            InfoDialog.show_info(self, tr(
                "hint",
            ), tr(
                "The current data has not generated spectrum "
                "yet",
            ))

    def _on_menu_open_spectrum(self) -> None:
        """File menu: Open any spectrum file to the current viewer (independent viewer entry)."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Open NMRPipe spectrum"),
            "",
            tr("NMRPipe spectrum (*.ft2 *.ft3 *.ft1 *.fid);; all files (*)"),
        )
        if not path:
            return
        target = Path(path)
        if self.open_spectrum(target):
            self._current_spectrum = target
            self._load_peaks(target)
            self.status_message.emit(tr("Opened: {p0}", p0=target.name))

    def _on_menu_clear_spectrum(self) -> None:
        """File menu: Clear the current viewer spectrum and peak table."""
        self._current_spectrum = None
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self._clear_peaks()

    def _on_menu_show_help(self) -> None:
        """Help menu: Viewer operating instructions."""
        InfoDialog.show_info(
            self,
            tr("Operating Instructions"),
            tr(
                "Left drag: box zoom; middle drag: pan; wheel: zoom\nHome / full-spectrum view: "
                "restore the full range\nSelection mode: drag with the left button to box-select "
                "peaks; with Add peak mode on, click to add a peak (snapped to the peak top)\n3D "
                "spectra (.ft3): pick the plane to view in the right-hand panel, and step through "
                "the planes with the slice\n  slider or switch to the MIP / summed projection\n2D "
                "spectra: right-click the spectrum to extract a 1D row/column slice; right-click a "
                "layer in the list to delete "
                "it",
            ),
        )

    def _on_expand_toggled(self, expanded: bool) -> None:
        """Spectrum enlarge/close: Only the drawing area extends to the left, and the keys remain
        on the right."""
        self.expand_button.setText(tr("close") if expanded else tr("enlarge"))
        if expanded:
            self._enter_expand_mode()
        else:
            self._exit_expand_mode()
        self.expand_requested.emit(expanded)

    def _enter_expand_mode(self) -> None:
        """Zoom in: Only the drawing area (plot_area) is moved to the left alone to cover the
        original left three column area, and all keys are retained in the right control column;
        the original small drawing area (viewer container) is hidden and not displayed."""
        if self._expanded or self._expand_splitter is not None:
            return
        outer = self.layout()
        viewer = self.viewer
        self._collapsed_sizes = list(self._panel_splitter.sizes())
        self._view_splitter_sizes = list(viewer.view_splitter.sizes())
        plot_area = viewer.plot_area
        controls_widget = viewer.controls_layout.parentWidget()
        # Remove the vertical splitter from the viewer to avoid displaying it in two places at the
        # same time (QSplitter without removeWidget; setParent(None) is removed from the splitter).
        plot_area.setParent(None)
        controls_widget.setParent(None)
        self._expand_plot_area = plot_area
        self._expand_viewer_controls = controls_widget
        # When zooming in, the height limit of the peak table is released and the remaining space in
        # the right column is used (more practical).
        self._peak_table_max = self.peak_table.maximumHeight()
        self.peak_table.setMaximumHeight(16777215)
        controls = QWidget()
        col = QVBoxLayout(controls)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(self.lists_row_widget, 0)
        col.addWidget(controls_widget, 0)
        col.addWidget(self.peak_toolbar_widget, 0)
        col.addWidget(self.peak_table, 1)
        self._expand_controls = controls
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.addWidget(plot_area)
        hsplit.addWidget(controls)
        hsplit.setStretchFactor(0, 1)
        hsplit.setStretchFactor(1, 0)
        right_w = max(360, min(self.width(), 560)) if self.width() > 100 else 520
        hsplit.setSizes([max(400, self.width() - right_w), right_w])
        self._expand_splitter = hsplit
        outer.replaceWidget(self._panel_splitter, hsplit)
        # 0.2.199-patch29hz - Modification 26 (user): After zooming in, there is an extra blank
        # space at the top, and there is only "spectrum" in it -- the horizontal splitter only
        # declares "horizontally scalable", and the remaining height of the vertical layout of the
        # panel is not occupied, and is all pressed to the top title row (VM measured 18px -> 311px,
        # the title label is the same height). Explicitly give it the stretch factor: the title row
        # returns to sizeHint, and the drawing area fills up the remaining height.
        _hsplit_index = outer.indexOf(hsplit)
        if _hsplit_index >= 0:
            outer.setStretch(_hsplit_index, 1)
        self._panel_splitter.setVisible(False)
        # The container where the original small drawing area is located is not displayed.
        viewer.setVisible(False)
        self._expanded = True

    def _exit_expand_mode(self) -> None:
        """Restore: The drawing area and control panel return to the viewer, and the controls on
        the right return to their original positions."""
        if not self._expanded or self._expand_splitter is None:
            return
        outer = self.layout()
        viewer = self.viewer
        outer.replaceWidget(self._expand_splitter, self._panel_splitter)
        # After restoration, the position is still a stretched item (the vertical splitter will eat
        # up the remaining height and the behaviour remains unchanged).
        self._expand_splitter.setVisible(False)
        self._expand_splitter = None
        self._expand_controls = None
        if self._expand_plot_area is not None:
            viewer.view_splitter.addWidget(self._expand_plot_area)
        if self._expand_viewer_controls is not None:
            viewer.view_splitter.addWidget(self._expand_viewer_controls)
        if self._view_splitter_sizes:
            viewer.view_splitter.setSizes(self._view_splitter_sizes)
        self._expand_plot_area = None
        self._expand_viewer_controls = None
        if getattr(self, "_peak_table_max", None) is not None:
            self.peak_table.setMaximumHeight(self._peak_table_max)
        self._panel_splitter.addWidget(self.lists_row_widget)
        self._panel_splitter.addWidget(viewer)
        self._panel_splitter.addWidget(self.peak_toolbar_widget)
        self._panel_splitter.addWidget(self.peak_table)
        if self._collapsed_sizes:
            self._panel_splitter.setSizes(self._collapsed_sizes)
        viewer.setVisible(True)
        self._panel_splitter.setVisible(True)
        self._expanded = False

    def _on_peak_header_clicked(self, section: int) -> None:
        """Click on the Assignment column heading: Peak Assignment Label on Switch Plot
        (0.2.199-patch29bf)."""
        if section != 1:
            return
        new_state = not self.viewer.peak_labels_visible
        self.viewer.set_peak_labels_visible(new_state)
        header_item = self.peak_table.horizontalHeaderItem(1)
        if header_item is not None:
            header_item.setText(
                "Assignment ✓" if new_state else "Assignment ✗"
            )

    def _jump_3d_slice_to_peak(self, row: int) -> None:
        """3D peak table point peak: Jump the slice to the section corresponding to the fixed axis
        of the peak, and then display it according to 2D logic (0.2.199-patch29dc). Peaks that
        lack fixed axis coordinates or coordinates out of bounds (2D peak table/Old peak
        selection results) will not jump and will prompt (0.2.199-patch29de: avoid jumping to
        the last section for any peak)."""
        s3d_panel = self._spectrum3d_panel
        s3d = s3d_panel.spectrum3d
        primary = self.viewer.primary_spectrum
        if s3d is None or primary is None:
            return
        if getattr(primary, "slice_axis", None) is None:
            return  # Not currently a 3D slice view.
        if not (0 <= row < len(self._peaks)):
            return
        slice_axis = getattr(s3d_panel, "_slice_axis", None)
        if slice_axis is None or not (0 <= int(slice_axis) < len(s3d.axes)):
            return
        peak = self._peaks[row]
        try:
            value = float(peak.get(f"F{int(slice_axis) + 1}_shift"))
        except (TypeError, ValueError):
            # There is no fixed axis coordinate and it is impossible to jump to the cutting plane.
            return
        if not value:
            self.log_message.emit(
                tr(
                    "peak {p0}: the fixed-axis coordinate is missing or 0, so the plane cannot be "
                    "located",
                    p0=row + 1,
                )
            )
            return
        axis = s3d.axes[int(slice_axis)]
        index = int(axis.index_at(value))
        # 0.2.199-patch29de: The coordinates exceed the axis range (the peak table does not match
        # the current spectral axis) and no jump will occur.
        if abs(value - float(axis.ppm[index])) > 3.0 * _axis_step(axis):
            self.log_message.emit(
                tr(
                    "Peak {p0} coordinate {p1} {p2:.2f} ppm is outside the current spectrum axis "
                    "range ({p3:.1f}-{p4:.1f}); it may be a stale peak pick, pick "
                    "again",
                    p0=row + 1,
                    p1=axis.label,
                    p2=value,
                    p3=float(axis.ppm[-1]),
                    p4=float(axis.ppm[0]),
                )
            )
            return
        if s3d_panel.slice_slider.value() != index:
            s3d_panel.slice_slider.setValue(index)
            s3d_panel.refresh()

    def _on_peak_row_selected(self) -> None:
        if self._syncing_table_selection:
            # Spectrum Click/Programmed row selection caused by box selection, only highlights
            # without flashing.
            return
        rows = self.peak_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._peaks):
            # 0.2.199-patch29dc: 3D jumps to the corresponding section of the peak first, and then
            # displays it according to 2D logical positioning.
            self._jump_3d_slice_to_peak(row)
            self.viewer.highlight_peak(row)

    def _on_peak_cell_clicked(self, row: int, column: int) -> None:
        """Clicking on the peak table cell: Clicking the selected row again will also trigger
        flickering positioning (0.2.199-patch29cm, selectionChanged will not trigger on the
        selected row)."""
        if self._syncing_table_selection:
            return
        if 0 <= row < len(self._peaks):
            # 0.2.199-patch29dc: 3D first jumps to the corresponding section of the peak, and then
            # positions and displays.
            self._jump_3d_slice_to_peak(row)
            self.viewer.highlight_peak(row)
