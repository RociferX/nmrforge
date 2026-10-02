"""Spectrum panel on the right: embedded independent viewer + project spectrum file list + peak
table edit write-back. Reuse viewer.SpectrumViewer (do not repeat the spectrum function); list
scan project spectrum directory, click.ft2/.ft3 to open on the right. Peak table supports
adding/delete/Edit line and write back data_dir(..., "peaks")/<exp>-<data>.list (Contract §6:
Peak file is Poky.list, registered by ProcessingController manual_peaks WorkflowRun). GUI Does
not directly touch the processing logic.
"""

from __future__ import annotations

import threading
from pathlib import Path

from qtcompat.QtCore import QItemSelectionModel, QSignalBlocker, Qt, QTimer
from qtcompat.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressBar,
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

_ASSIGNMENT_WIDGET_BUFFER = 12


def _contiguous_runs(indices: list[int]) -> list[tuple[int, int]]:
    """Convert sorted indices to contiguous (start, length) runs for block reads that skip cached
    planes.
    """
    runs: list[tuple[int, int]] = []
    for index in indices:
        if runs and index == runs[-1][0] + runs[-1][1]:
            start, length = runs[-1]
            runs[-1] = (start, length + 1)
        else:
            runs.append((index, 1))
    return runs


def _format_snr(value) -> str:
    """Format numeric peak SNR to one decimal place; leave missing or nonnumeric values blank."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number != number:  # NaN
        return ""
    return f"{number:.1f}"


def _peak_snr_text(peak: dict) -> str:
    """Return the SN table cell, preferring SN and falling back to pipeline CSV SNR."""
    text = _format_snr(peak.get("SN"))
    if text:
        return text
    return _format_snr(peak.get("SNR"))


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
    G1H-G1N-G1CA).
    """

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

            if segs[i] in ("", "?"):
                le.setPlaceholderText("?")
            else:
                le.setText(segs[i])
            le.setStyleSheet(
                "QLineEdit { color: #e8e8e8; } QLineEdit::placeholder { color: #8a8a8a; }"
            )
            self.lines.append(le)
            lay.addWidget(le)
        for le in self.lines:
            le.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, *_args) -> None:
        self.edited.emit(self.row)

    def merged_text(self) -> str:
        """The label of the current merged segments (poky normalisation segment by segment, empty
        segment -> ?).
        """
        segs = [normalize_poky_label((le.text() or "").strip(), ndim=1) or "?" for le in self.lines]
        return "-".join(segs)


class SpectrumPanel(QWidget):
    """Spectrum panel: viewer + file list + peak table (add/delete/change/live)."""

    peaks_saved = Signal()
    status_message = Signal(str)
    log_message = Signal(str)
    _ft3_ready = Signal(int, object, object)
    _ft3_failed = Signal(int, object, str)  # (token, path, message)
    _ft2_ready = Signal(int, object, object)
    _ft2_failed = Signal(int, object, str)

    plane_loaded = Signal(int, int)
    _plane_ready = Signal(int, int, int, int)  # (token, index, ready, total)
    _plane_stream_done = Signal(int)  # token

    expand_requested = Signal(bool)

    _ASYNC_FT3_MIN_BYTES = 32 * 1024 * 1024
    _ASYNC_FT2_MIN_BYTES = 8 * 1024 * 1024
    _AUTOLOAD_DELAY_MS = 150

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
        self._applying_label_format = False
        self._viewer3d_state: dict[tuple[str, str], int] = {}

        self._display_states: dict[tuple[str, str], dict] = {}
        self._restoring_display_state = False
        self._peak_keys: tuple[str, ...] = (
            "Peak_ID",
            "H_shift",
            "N_shift",
            "Intensity",
            "SN",
        )

        self._stream_thread = None
        self._stream_cancel = None
        self._stream_token = 0
        self._stream_axis = -1

        self._stream_key: tuple[object, int] | None = None
        self._planes_ready = 0
        self._planes_total = 0

        self._ft3_load_token = 0
        self._autoload_timer = QTimer(self)
        self._autoload_timer.setSingleShot(True)
        self._autoload_timer.timeout.connect(self._load_auto_spectrum)
        self._ft2_lock = threading.Lock()
        self._ft2_pending = None
        self._ft2_worker_active = False

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
        self._ft2_ready.connect(self._on_ft2_ready)
        self._ft2_failed.connect(self._on_ft2_failed)
        self._spectrum3d_panel.slice_changed.connect(self._render_3d_view)
        self._spectrum3d_panel.slice_changed.connect(self._ensure_plane_stream)
        self._spectrum3d_panel.plane_combo.currentIndexChanged.connect(self._save_3d_state)
        self.viewer.add_control_panel(self._spectrum3d_panel)

        self.loading_indicator = QWidget()
        self.loading_indicator.setObjectName("SpectrumLoadingIndicator")
        indicator_row = QHBoxLayout(self.loading_indicator)
        indicator_row.setContentsMargins(0, 0, 0, 0)
        indicator_row.setSpacing(8)
        self.loading_label = QLabel("")
        self.loading_label.setStyleSheet(f"color: {TEXT_MUTED};")
        indicator_row.addWidget(self.loading_label)
        self.loading_progress = QProgressBar()
        self.loading_progress.setTextVisible(True)
        self.loading_progress.setFixedHeight(14)
        self.loading_progress.setFixedWidth(180)
        indicator_row.addWidget(self.loading_progress)
        indicator_row.addStretch(1)
        self.loading_indicator.setVisible(False)
        self._plane_ready.connect(self._on_plane_ready)
        self._plane_stream_done.connect(self._on_plane_stream_done)

        self.file_list = QListWidget()
        self.file_list.setAutoFillBackground(False)
        self.viewer.layer_list.setAutoFillBackground(False)
        self.file_list.setMaximumWidth(190)
        self.file_list.itemClicked.connect(self._on_file_clicked)

        self.peak_toolbar = QHBoxLayout()

        self.peak_toolbar.setSpacing(12)
        self.peak_toolbar.addWidget(self.viewer.show_peaks_checkbox)

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

        self.peak_toolbar2 = QHBoxLayout()
        self.peak_toolbar2.setSpacing(12)
        self.delete_peak_button = QPushButton("Delete selected")
        self.delete_peak_button.setEnabled(False)
        self.delete_peak_button.setToolTip(
            tr(
                "Delete the selected rows from the peak table (automatic and manual peaks alike)",
            )
        )
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
                "Write peak table back to data/peaks/<exp>-<data>.list and register",
            )
        )
        self.save_peaks_button.clicked.connect(self._on_save_peaks)
        self.peak_toolbar2.addWidget(self.save_peaks_button)

        self.peak_size_label = QLabel(tr("Mark size"))
        self.peak_size_spin = QDoubleSpinBox()
        self.peak_size_spin.setRange(0.5, 50.0)
        self.peak_size_spin.setSingleStep(0.5)
        self.peak_size_spin.setDecimals(1)
        self.peak_size_spin.setValue(1.5)
        self.peak_size_spin.setToolTip(
            tr(
                "Marker size (data coordinate units, scaled with spectrum)",
            )
        )
        self.peak_size_spin.setEnabled(False)
        self.peak_size_spin.valueChanged.connect(self.viewer.set_peak_size)

        self.viewer.level_slider.valueChanged.connect(self._save_display_state)
        self.viewer.level_label.valueChanged.connect(self._save_display_state)
        self.viewer.count_slider.valueChanged.connect(self._save_display_state)
        self.viewer.aspect_slider.valueChanged.connect(self._save_display_state)
        self.peak_size_spin.valueChanged.connect(self._save_display_state)
        self.peak_toolbar.addWidget(self.peak_size_label)
        self.peak_toolbar.addWidget(self.peak_size_spin)
        self.peak_toolbar.addStretch(1)

        self.peak_table = QTableWidget(0, 5)
        self.peak_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.peak_table.setHorizontalHeaderLabels(list(self._peak_keys))
        self.peak_table.horizontalHeader().setStretchLastSection(True)
        self.peak_table.setMaximumHeight(150)

        self._syncing_table_selection = False
        self.peak_table.itemSelectionChanged.connect(self._on_peak_row_selected)

        self.peak_table.cellClicked.connect(self._on_peak_cell_clicked)
        self.peak_table.itemChanged.connect(self._on_peak_cell_edited)

        self.peak_table.horizontalHeader().sectionClicked.connect(self._on_peak_header_clicked)

        self.peak_table.verticalScrollBar().valueChanged.connect(self._ensure_assignment_widgets)
        self._peaks: list[dict] = []
        self._current_spectrum: Path | None = None
        self._viewer_1d_active = False
        self._projection_active = False
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

        self.lists_row_widget = QWidget()

        self.lists_row_widget.setMaximumHeight(110)

        lists_column = QVBoxLayout(self.lists_row_widget)
        lists_column.setContentsMargins(0, 0, 0, 0)
        lists_column.setSpacing(2)
        lists_column.addWidget(self.loading_indicator)
        self.lists_row = QHBoxLayout()
        lists_column.addLayout(self.lists_row, 1)
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

        self.lists_row.addWidget(self.expand_button)
        self.compare_menu = QMenu(self)
        self.compare_menu.aboutToShow.connect(self._refresh_compare_menu)
        self.compare_menu.triggered.connect(self._on_compare_action)
        self.compare_button = QPushButton(tr("Compare"))
        self.compare_button.setMenu(self.compare_menu)
        self.compare_button.setToolTip(
            tr(
                "Compare with another result spectrum: choose a spectrum to show both side by "
                "side, each with its own display controls"
            )
        )
        self.compare_button.setVisible(False)
        self.lists_row.addWidget(self.compare_button)

        self.file_menu = QMenu(self)

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
        self.lists_row.addSpacing(12)

        self._expanded = False
        self._expand_splitter: QSplitter | None = None
        self._expand_controls: QWidget | None = None
        self._expand_plot_area: QWidget | None = None
        self._expand_viewer_controls: QWidget | None = None
        self._collapsed_sizes: list[int] = []
        self._view_splitter_sizes: list[int] = []
        self._compare_viewer: SpectrumViewer | None = None
        self._compare_3d_panel: Spectrum3DPanel | None = None
        self._compare_spectrum: Path | None = None
        self._compare_column: QWidget | None = None
        self._current_column: QWidget | None = None
        self._comparison_active = False
        self._comparison_layout_timer = QTimer(self)
        self._comparison_layout_timer.setSingleShot(True)
        self._comparison_layout_timer.timeout.connect(self._align_comparison_plot_height)
        self.viewer.view_splitter.splitterMoved.connect(
            lambda *_args: self._align_comparison_plot_height(self.viewer.view_splitter.sizes()[0])
        )
        self._panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self._panel_splitter.addWidget(self.lists_row_widget)
        self._panel_splitter.addWidget(self.viewer)
        self.peak_toolbar_widget = QWidget()

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
        self.file_list.setMaximumWidth(16777215)
        layout.addWidget(self._panel_splitter)
        self.refresh()

    @property
    def manager(self) -> ProjectManager:
        return self._manager

    @manager.setter
    def manager(self, value: ProjectManager) -> None:
        self._manager = value
        self.controller.set_manager(value)

    @property
    def planes_ready(self) -> int:
        """Return the number of immediately available planes along the selected 3D slicing axis."""
        return int(self._planes_ready)

    @property
    def planes_total(self) -> int:
        """Return the selected slicing axis's plane count, or zero without a 3D spectrum."""
        return int(self._planes_total)

    @property
    def plane_stream_active(self) -> bool:
        """Return whether background plane streaming is still reading."""
        thread = self._stream_thread
        return bool(thread is not None and thread.is_alive())

    def set_context(self, exp_id: str, data_id: str = "", *, auto_load_delay_ms: int = 0) -> None:
        changed = (exp_id or "", data_id or "") != (
            self._current_exp_id,
            self._current_data_id,
        )
        if changed:
            self._deactivate_comparison()
            self._clear_current_spectrum()
        self._current_exp_id = exp_id or ""
        self._current_data_id = data_id or ""
        if auto_load_delay_ms > 0 and changed and exp_id and data_id:
            self._autoload_timer.start(auto_load_delay_ms)
        elif auto_load_delay_ms <= 0:
            self._autoload_timer.stop()
        self.refresh()

    def _load_auto_spectrum(self) -> None:
        if self._current_exp_id and self._current_data_id and self._current_spectrum is None:
            self.load_current_spectrum()

    def _sync_peak_ui_visibility(self) -> None:
        """Peak Correlation UI Visibility: 1D View or Projection File Open Hide all
        (0.2.199-patch29db).
        """
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
        (0.2.199-patch29fz).
        """
        key = self._display_key()
        if not all(key) or self._restoring_display_state:
            return
        try:
            self._display_states[key] = {
                "level_slider": int(self.viewer.level_slider.value()),
                "level_percent": float(self.viewer.level_label.value()),
                "level_count": int(self.viewer.count_slider.value()),
                "aspect": int(self.viewer.aspect_slider.value()),
                "peak_size": float(self.peak_size_spin.value()),
            }

            from gui.per_data_records import update_ui_state

            update_ui_state(
                self.manager,
                self._current_exp_id,
                self._current_data_id,
                "spectrum",
                dict(self._display_states[key]),
            )
        except Exception:  # noqa: BLE001
            pass

    def _restore_display_state(self) -> None:
        """After the spectrum is successfully loaded, the display adjustment is restored according
        to the current data; if there is no record, the default is used and the file is dropped
        (0.2.199-patch29fz, user:threshold/contour start and other adjustments require data
        isolation).
        """
        key = self._display_key()
        if not all(key):
            return
        state = self._display_states.get(key)
        if state is None:
            defaults = {
                "level_slider": 31,
                "level_percent": None,
                "level_count": 8,
                "aspect": 0,
                "peak_size": 1.5,
            }

            try:
                from gui.per_data_records import load_ui_state

                file_state = (
                    load_ui_state(
                        self.manager,
                        self._current_exp_id,
                        self._current_data_id,
                    ).get("spectrum")
                    or {}
                )
                for field in defaults:
                    if file_state.get(field) is not None:
                        defaults[field] = file_state[field]
            except Exception:  # noqa: BLE001
                pass
            state = defaults
            self._display_states[key] = dict(state)
        self._restoring_display_state = True
        try:
            self.viewer.level_slider.setValue(int(state.get("level_slider", 31)))
            if state.get("level_percent") is not None:
                self.viewer.set_contour_start(float(state["level_percent"]))
            else:
                value = max(1, min(100, int(state.get("level_slider", 31))))
                self.viewer.set_contour_start((value / 100.0) ** 3 * 100.0)
            self.viewer.count_slider.setValue(int(state.get("level_count", 8)))
            self.viewer.aspect_slider.setValue(int(state.get("aspect", 0)))
            self.viewer.refresh_levels()
            self.peak_size_spin.setValue(float(state.get("peak_size", 1.5)))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._restoring_display_state = False

    def refresh_after_processing(self, exp_id: str, data_id: str) -> None:
        """Reload the main result only if the completed target is still selected; never change
        dataset context.
        """
        if (exp_id, data_id) != self._display_key():
            return
        self._autoload_timer.stop()
        self.refresh(reload_current=True)

    def refresh(self, *, reload_current: bool = False) -> None:
        """Refresh the spectrum file list and hide it when empty.

        Dataset contexts automatically show the main spectrum without rereading on ordinary
        refreshes. Successful processing explicitly requests reload even when path and mtime are
        unchanged. Group/experiment contexts refresh only the list; set_context clears the
        previous dataset's display.
        """
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
            self._clear_current_spectrum()
            return
        if self._current_spectrum is not None and self._current_spectrum not in paths:
            self._clear_current_spectrum()
        if (
            has_context
            and (reload_current or self._current_spectrum is None)
            and not self._autoload_timer.isActive()
        ):
            self.load_current_spectrum()

    def load_current_spectrum(self) -> bool:
        """Load the main spectrum of the current sample data (the projection file can be viewed
        directly by clicking on the list).
        """
        paths = self._spectrum_paths()
        if not paths:
            return False
        main = [p for p in paths if not self._is_projection_name(p.name)] or paths

        first = next((p for p in main if p.suffix.lower() == ".ft3"), main[0])
        if self._current_data_id:
            entry = self.manager.data(self._current_exp_id, self._current_data_id)
            if entry.spectrum_path:
                registered = Path(entry.spectrum_path)
                if not registered.is_absolute():
                    registered = self.manager.root / registered
                if registered in main:
                    first = registered
        if not self.open_spectrum(first):
            return False
        self._current_spectrum = first
        self._load_peaks(first)
        return True

    def _spectrum_paths(self) -> list[Path]:
        """Current experiment/under sample data spectrum file (schema 1.4 data-level spectra/). The
        data-level context only lists this data; the experimental-level context summarizes all
        data under the experiment (not deleted). The back-end final spectrum is named according
        to dataset_id (such as hsqc_2d.ft2), and no prefix (0.2.112) is assumed.
        """
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
                except OSError:  # noqa: PERF203
                    continue
        return sorted(set(files))

    def open_spectrum(self, path: Path, name: str | None = None) -> bool:
        """Load spectrum into the viewer; return False on failure (no pop-up window, prompt
        determined by the caller)..ft3 takes the 3D viewing path (Contract §10): Bind Spectrum3D
        and display the default slice, 3D The panel provides a flat surface/slice/Projection
        switching;.ft2 takes the two-dimensional overlay.
        """

        self._autoload_timer.stop()
        self._ft3_load_token += 1
        self._stop_plane_stream()
        self._hide_loading_indicator()

        self._projection_active = path.suffix.lower() == ".ft2" and self._is_projection_name(
            path.name
        )

        try:
            if path.suffix.lower() == ".ft3":
                from viewer.spectrum import Spectrum3D

                size = path.stat().st_size if path.is_file() else 0
                if size >= self._ASYNC_FT3_MIN_BYTES:
                    self._current_spectrum = path
                    self.status_message.emit(
                        tr(
                            "Loading 3D spectrum in the background: {p0} ({p1} MB)",
                            p0=path.name,
                            p1=size // (1024 * 1024),
                        )
                    )

                    self._show_loading_indicator(tr("Loading 3D spectrum: {p0}", p0=path.name))
                    self._load_ft3_async(path)
                    return True
                self._current_spectrum = path
                self._display_3d_spectrum(Spectrum3D.load_from_ft3(path, lazy=True))
                return True
            if path.suffix.lower() == ".ft1":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(Spectrum1D.load_from_ft1(path), name=name or path.stem)
                self._viewer_1d_active = True
                self._sync_peak_ui_visibility()
                self._restore_display_state()
                return True
            if path.suffix.lower() == ".fid":
                from viewer.spectrum import Spectrum1D

                self._current_spectrum = path
                self._spectrum3d_panel.clear()
                self.viewer.add_spectrum(Spectrum1D.load_from_fid(path), name=path.stem)
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

                self._clear_peaks()
            else:
                if path.is_file() and path.stat().st_size >= self._ASYNC_FT2_MIN_BYTES:
                    self._current_spectrum = path
                    self._show_loading_indicator(tr("Loading 2D spectrum: {p0}", p0=path.name))
                    self._load_ft2_async(path)
                    return True
                spectrum = Spectrum.load_from_ft2(path)
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(
                tr(
                    "2D spectrum loading failed {p0}: {p1}",
                    p0=path.name,
                    p1=describe_exception(exc),
                )
            )
            return False
        self._display_2d_spectrum(spectrum, name=name or path.stem)
        return True

    def _display_2d_spectrum(self, spectrum, name: str) -> None:
        """Bind and draw on the main thread using the same display path for synchronous and
        background reads.
        """
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self.viewer.add_spectrum(spectrum, name=name)
        self._viewer_1d_active = False
        self._restore_display_state()
        self._sync_peak_ui_visibility()

    def _load_ft2_async(self, path: Path) -> None:
        """Read large 2D results serially: at most one active read and one pending latest
        selection.
        """
        token = self._ft3_load_token
        with self._ft2_lock:
            self._ft2_pending = (token, path)
            if self._ft2_worker_active:
                return
            self._ft2_worker_active = True

        def worker() -> None:
            from viewer.spectrum import Spectrum

            while True:
                with self._ft2_lock:
                    request = self._ft2_pending
                    self._ft2_pending = None
                    if request is None:
                        self._ft2_worker_active = False
                        return
                request_token, target = request
                if request_token != self._ft3_load_token:
                    continue
                try:
                    spectrum = Spectrum.load_from_ft2(target)
                    spectrum.plane_max = spectrum.max_intensity
                except Exception as exc:  # noqa: BLE001
                    if request_token == self._ft3_load_token:
                        self._ft2_failed.emit(request_token, target, describe_exception(exc))
                else:
                    if request_token == self._ft3_load_token:
                        self._ft2_ready.emit(request_token, target, spectrum)

        threading.Thread(target=worker, daemon=True).start()

    def _on_ft2_ready(self, token, path, spectrum) -> None:
        if token != self._ft3_load_token or path != self._current_spectrum:
            return
        self._hide_loading_indicator()
        self._display_2d_spectrum(spectrum, name=path.stem)
        self._load_peaks(path)

    def _on_ft2_failed(self, token, path, message: str) -> None:
        if token != self._ft3_load_token or path != self._current_spectrum:
            return
        self._clear_current_spectrum()
        self.log_message.emit(tr("2D spectrum loading failed {p0}: {p1}", p0=path.name, p1=message))

    def open_with_peaks(self, path: Path, name: str | None = None) -> bool:
        """Open spectrum and load its peak table (for the main window to call to avoid external
        access to private members).
        """
        target = Path(path)
        if not self.open_spectrum(target, name=name):
            return False
        self._current_spectrum = target
        self._load_peaks(target)
        return True

    def _load_ft3_async(self, path: Path) -> None:
        """Read a large ft3 in a worker and bind the result on the main thread.

        Each request gets a generation token. A path-only check cannot reject earlier requests
        for the same path; discard stale generations to avoid repeated whole-spectrum reads and
        renders.
        """
        self._ft3_load_token += 1
        token = self._ft3_load_token
        state = self._viewer3d_state.get(self._display_key(), 2)
        slice_axis = {0: 2, 1: 1, 2: 0}.get(state, 0)

        def worker() -> None:
            try:
                from viewer.spectrum import Spectrum3D

                spectrum3d = Spectrum3D.load_from_ft3(path, lazy=True)
                if token != self._ft3_load_token:
                    return

                spectrum3d.estimate_noise()
                spectrum3d.slice(slice_axis, spectrum3d.axes[slice_axis].size // 2)
                self._ft3_ready.emit(token, path, spectrum3d)
            except Exception as exc:  # noqa: BLE001
                self._ft3_failed.emit(token, path, describe_exception(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ft3_ready(self, token, path, spectrum3d) -> None:
        """Bind and render a loaded ft3 on the main thread only if its context and generation token
        are still current.
        """
        if token != self._ft3_load_token or path != self._current_spectrum:
            return
        self._display_3d_spectrum(spectrum3d)
        self._load_peaks(path)
        self.status_message.emit(tr("Loaded 3D spectrum: {p0}", p0=path.name))

    def _display_3d_spectrum(self, spectrum3d) -> None:
        """Share synchronous/background display handling and fit the current plane when returning
        from a projection.
        """
        entering_3d = self._spectrum3d_panel.spectrum3d is None

        state = self._viewer3d_state.get((self._current_exp_id, self._current_data_id))
        self._spectrum3d_panel.set_spectrum3d(spectrum3d)
        self._spectrum3d_panel.setVisible(True)
        if state is not None:
            self._spectrum3d_panel.plane_combo.setCurrentIndex(state)
        self._render_3d_view()
        self._restore_display_state()
        if entering_3d:
            self.viewer.reset_view()

        self._ensure_plane_stream()

    def _on_ft3_failed(self, token, path, message: str) -> None:
        """Report a large ft3 load failure on the main thread only if the request is still current.

        """
        if token != self._ft3_load_token or path != self._current_spectrum:
            return
        self._current_spectrum = None
        self._hide_loading_indicator()
        self.status_message.emit(tr("3D spectrum loading failed: {p0}", p0=message))

        self.log_message.emit(tr("3D spectrum loading failed {p0}: {p1}", p0=path.name, p1=message))

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    def _show_loading_indicator(self, text: str = "", ready: int = 0, total: int = 0) -> None:
        """Show the indicator above the spectrum; total <= 0 uses indeterminate progress."""
        self.loading_label.setText(text)
        if total > 0:
            self.loading_progress.setRange(0, int(total))
            self.loading_progress.setValue(min(int(ready), int(total)))
            self.loading_progress.setFormat("%v / %m")
        else:
            self.loading_progress.setRange(0, 0)
            self.loading_progress.setFormat("")
        self._reveal_loading_indicator()

    def _reveal_loading_indicator(self) -> None:
        """Reserve the loading indicator's row above the spectrum rather than letting the idle
        height cap squeeze it out.
        """
        self.lists_row_widget.setMaximumHeight(132)
        self.loading_indicator.setVisible(True)

    def _update_loading_progress(self, ready: int, total: int) -> None:
        """Update plane-loading progress without changing the title text."""
        self.loading_progress.setRange(0, max(1, int(total)))
        self.loading_progress.setValue(min(int(ready), int(total)))
        self.loading_progress.setFormat("%v / %m")
        self.loading_label.setText(tr("Loading planes: {p0} / {p1}", p0=int(ready), p1=int(total)))
        self._reveal_loading_indicator()

    def _hide_loading_indicator(self) -> None:
        """Hide the loading indicator while idle."""
        self.loading_indicator.setVisible(False)
        self.loading_progress.setRange(0, 1)
        self.loading_progress.setValue(0)
        self.loading_label.setText("")
        self.lists_row_widget.setMaximumHeight(110)

    def _ensure_plane_stream(self) -> None:
        """Start plane streaming for the selected axis only when the 3D spectrum is lazy.

        Reuse the existing active or completed stream for the same spectrum and axis.
        In-memory/unbound spectra need no stream. Release the stream key's old spectrum
        reference on replacement so its large plane cache can be reclaimed.
        """
        spectrum3d = self._spectrum3d_panel.spectrum3d
        axis_idx = self._spectrum3d_panel.slice_axis
        if spectrum3d is None or not getattr(spectrum3d, "lazy", False):
            self._hide_loading_indicator()
            return
        key = self._stream_key
        if key is not None and key[0] is spectrum3d and key[1] == axis_idx:
            return
        self._stop_plane_stream()
        self._stream_token += 1
        token = self._stream_token
        self._stream_axis = axis_idx
        self._stream_key = (spectrum3d, axis_idx)
        total = int(spectrum3d.axes[axis_idx].size)
        ready = len(spectrum3d.cached_plane_indices(axis_idx))
        self._planes_ready = ready
        self._planes_total = total
        self._update_loading_progress(ready, total)
        cancel = threading.Event()
        self._stream_cancel = cancel

        def worker() -> None:
            try:
                self._stream_planes(spectrum3d, axis_idx, token, cancel.is_set)
            except Exception:  # noqa: BLE001
                try:
                    self._plane_stream_done.emit(token)
                except RuntimeError:  # pragma: no cover
                    pass

        thread = threading.Thread(target=worker, daemon=True)
        self._stream_thread = thread
        thread.start()

    def _stop_plane_stream(self) -> None:
        """Request nonblocking stream cancellation; the worker exits at a block boundary."""
        if self._stream_cancel is not None:
            self._stream_cancel.set()
            self._stream_cancel = None
        self._stream_thread = None
        self._stream_axis = -1
        self._stream_key = None

        self._stream_token += 1

    def _stream_planes(self, spectrum3d, axis_idx: int, token: int, is_cancelled) -> None:
        """Read plane blocks in the background and emit each plane to the main thread.

        When slicing the file-contiguous dimension, plane_block_size amortizes a full-file
        traversal. Other axes read individual planes so the first is available immediately.
        Cache completed planes to avoid repeated disk reads; tests may invoke this method
        synchronously.
        """
        total = int(spectrum3d.axes[axis_idx].size)
        block = max(1, int(spectrum3d.plane_block_size(axis_idx)))
        cached = set(spectrum3d.cached_plane_indices(axis_idx))
        ready = len(cached)
        self._plane_ready.emit(token, -1, ready, total)
        missing = [index for index in range(total) if index not in cached]
        for start, length in _contiguous_runs(missing):
            offset = 0
            while offset < length:
                if is_cancelled():
                    return
                count = min(block, length - offset)
                planes = spectrum3d.read_planes(axis_idx, start + offset, count)
                if not planes:
                    break
                for step, _plane in enumerate(planes):
                    if is_cancelled():
                        return
                    ready += 1
                    self._plane_ready.emit(token, start + offset + step, ready, total)
                offset += len(planes)
        self._plane_stream_done.emit(token)

    def _on_plane_ready(self, token: int, index: int, ready: int, total: int) -> None:
        """Handle a plane on the main thread, update progress and broadcast it unless the stream is
        stale.
        """
        if token != self._stream_token:
            return
        self._planes_ready = int(ready)
        self._planes_total = int(total)
        self._update_loading_progress(ready, total)
        if index >= 0:
            self.plane_loaded.emit(int(self._stream_axis), int(index))

    def _on_plane_stream_done(self, token: int) -> None:
        """Hide stream progress and remember that the selected axis has been fully loaded."""
        if token != self._stream_token:
            return
        self._planes_ready = self._planes_total
        self._stream_thread = None
        self._stream_cancel = None
        self._hide_loading_indicator()

    def closeEvent(self, event) -> None:
        """Stop plane streaming on close so workers do not signal destroyed widgets."""
        self._clear_current_spectrum()
        super().closeEvent(event)

    def _current_3d_nuclei(self) -> list[str] | None:
        """The complete kernel name of each F axis (F1/F2/F3) of the currently loaded 3D spectrum;
        kernel agnostic returns None. 0.2.199-patch29dk(user):.list/Peak table display according
        to external convention, internally interpreted according to F logic -- Here the kernel
        is taken from the loaded spectrum axis label (only the file header source is used, no
        metadata is used).
        """
        s3d = self._spectrum3d_panel.spectrum3d
        axes3 = getattr(s3d, "axes", None) if s3d is not None else None
        if not axes3 or len(axes3) != 3:
            return None
        symbols = {
            "H": "1H",
            "N": "15N",
            "C": "13C",
            "F": "19F",
            "P": "31P",
            "D": "2H",
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
        metadata; if not, None.
        """
        from viewer.axis_labels import nuclei_from_metadata

        if self._manager is None or not (self._current_exp_id and self._current_data_id):
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
        used (the caller falls back to F1/F2/F3).
        """
        from viewer.axis_labels import axis_labels_from_nuclei

        nuclei = self._axis_nuclei(required)
        if not nuclei:
            return None
        return axis_labels_from_nuclei(nuclei)

    def _is_projection_name(self, name: str) -> bool:
        """Projection file identification: new named {data_id}_{coreA}-{coreB}.ft2 or old
        *_proj_*.ft2.
        """
        if "_proj_" in name:
            return True
        data_id = self._current_data_id or ""
        if data_id and name.startswith(f"{data_id}_") and name.endswith(".ft2"):
            body = name[len(data_id) + 1 : -4]
            if "-" in body:
                return True
        return False

    def _load_projection_ft2(self, path: Path) -> object | None:
        """Load a single projection.ft2 (0.2.133, click directly to view) according to the file
        name. The kernel is parsed from the file name; display rules: abscissa priority H > N >
        C (0.2.153), transpose the data matrix if necessary. The axis parameter is first taken
        from the axis of the loaded 3D spectrum corresponding to the kernel (SW/OBS/CAR/ORIG),
        otherwise the file header slot is used. Return Pydantic Spectrum; parsing failure None.
        """
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
            -> 15N).
            """
            t = _norm(nuc)
            if t and t[-1] in "XYZ":
                t = t[:-1]
            return t

        name = path.name
        data_id = self._current_data_id or ""
        nuclei = self._axis_nuclei(3) or []
        a = b = None

        logical_mapped = False
        if data_id and name.startswith(f"{data_id}_") and name.endswith(".ft2"):
            body = name[len(data_id) + 1 : -4]
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
                m2 = _re.search(r"_proj_([A-Za-z0-9]{2,6})\.ft2$", name)
                if m2 and len(nuclei) == 3:
                    body = m2.group(1)
                    symbols = {nucleus_symbol(n).upper(): n for n in nuclei}
                    match = [symbols.get(c.upper()) for c in body]
                    if len(match) == 2 and all(match):
                        a, b = match[0], match[1]
                else:
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
                except Exception:  # noqa: BLE001
                    s3d_axes = []
                if len(s3d_axes) == 3:
                    break

        labels3 = None
        if len(nuclei) == 3:
            from viewer.axis_labels import axis_labels_from_nuclei as _alfn

            labels3 = _alfn(nuclei)

            if (
                len({_base_norm(n) for n in nuclei}) < len(nuclei)
                and _base_norm(nuclei[0]) == "15N"
                and _base_norm(nuclei[1]) == "15N"
            ):
                labels3 = ("Ny", "Nx", str(labels3[2]))
        x_params = y_params = None

        if len(s3d_axes) == 3:
            sym_a, sym_b = nucleus_symbol(a), nucleus_symbol(b)

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
            x_params = _Sn(
                label=nucleus_symbol(a),
                size=int(data.shape[1]),
                sw_hz=float(dic.get("FDF2SW", 1.0) or 1.0),
                obs_mhz=float(dic.get("FDF2OBS", 1.0) or 1.0),
                carrier_ppm=float(dic.get("FDF2CAR", 0.0) or 0.0),
                orig_hz=float(dic["FDF2ORIG"]) if "FDF2ORIG" in dic else None,
            )
            y_params = _Sn(
                label=nucleus_symbol(b),
                size=int(data.shape[0]),
                sw_hz=float(dic.get("FDF1SW", 1.0) or 1.0),
                obs_mhz=float(dic.get("FDF1OBS", 1.0) or 1.0),
                carrier_ppm=float(dic.get("FDF1CAR", 0.0) or 0.0),
                orig_hz=float(dic["FDF1ORIG"]) if "FDF1ORIG" in dic else None,
            )

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
            from viewer.axis_labels import nucleus_symbol as _nsym

            sx, sy = _nsym(a), _nsym(b)
            x_label = sx if sx[-1:].upper() in ("X", "Y", "Z") else sx + "x"
            y_label = sy if sy[-1:].upper() in ("X", "Y", "Z") else sy + "y"
        else:
            x_label, y_label = nucleus_symbol(a), nucleus_symbol(b)

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
            spectrum.dim_indices = tuple(i for i in range(3) if i != fixed_axis)
        return spectrum

    def _load_3d_projections(self) -> dict[int, object]:
        """Compatible interface: scan all projection files and press key = fixed axis index to
        return (0.2.133).
        """
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
        markers.
        """
        spectrum = self._spectrum3d_panel.current_spectrum()
        if spectrum is None:
            return
        base = self._current_spectrum.stem if self._current_spectrum else "3D"

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

    # ------------------------------------------------------------------
    def _peak_file_path(self, spectrum_path: Path) -> Path | None:
        """Peak table file:.list takes priority (peak table is list), and the old CSV is compatible
        with fallback.
        """
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
            return
        peak_path = self._peak_file_path(spectrum_path)
        if peak_path is None:
            return
        if peak_path.suffix.lower() == ".list":
            peaks = import_peaks_poky(peak_path, nuclei=self._current_3d_nuclei())
        else:
            peaks = load_peaks(peak_path)
        if not peaks:
            return
        self._peaks = self._assign_peak_ids(peaks)
        self._normalize_peak_snr(self._peaks)
        self._populate_peak_table()
        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()
        self._sync_peak_ui_visibility()

    def _set_peak_columns(self, is_3d: bool) -> None:

        keys: list[str]
        header_map: dict[str, str] = {}
        if is_3d:
            s3d = self._spectrum3d_panel.spectrum3d
            axes3 = getattr(s3d, "axes", None)
            nuclei3 = self._current_3d_nuclei()
            if axes3 and len(axes3) == 3 and nuclei3 and set(nuclei3) == {"15N", "13C", "1H"}:
                order = [nuclei3.index(n) for n in ("15N", "13C", "1H")]
                keys = (
                    ["Peak_ID", "label"] + [f"F{i + 1}_shift" for i in order] + ["Intensity", "SN"]
                )
                header_map = {f"F{i + 1}_shift": f"{axes3[i].label}_shift" for i in order}
            else:
                keys = [
                    "Peak_ID",
                    "label",
                    "F1_shift",
                    "F2_shift",
                    "F3_shift",
                    "Intensity",
                    "SN",
                ]
                if axes3 and len(axes3) == 3:
                    header_map = {f"F{i + 1}_shift": f"{axes3[i].label}_shift" for i in range(3)}
        else:
            keys = ["Peak_ID", "label", "H_shift", "N_shift", "Intensity", "SN"]
        tuple_keys = tuple(keys)
        if tuple_keys == self._peak_keys:
            return
        self._peak_keys = tuple_keys
        self.peak_table.setColumnCount(len(tuple_keys))

        self.peak_table.setHorizontalHeaderLabels(
            [
                ("Assignment ✓" if self.viewer.peak_labels_visible else "Assignment ✗")
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
        components are displayed with label item text, read/Save and go back to text.
        """
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
        """Populate the peak table with dimensionality-specific columns.

        Map pipeline CSV SNR to the existing SN column and format one decimal place. Poky lists
        contain no SNR column, so absent values remain blank rather than being invented.
        """
        is_3d = bool(self._peaks) and "F1_shift" in self._peaks[0]
        self._set_peak_columns(is_3d)
        label_col = self._peak_keys.index("label")
        self._loading_peaks = True
        try:
            self.peak_table.setRowCount(len(self._peaks))
            for row, peak in enumerate(self._peaks):
                for col, key in enumerate(self._peak_keys):
                    if key == "SN":
                        text = _peak_snr_text(peak)
                    else:
                        text = str(peak.get(key, ""))
                    self.peak_table.setItem(row, col, QTableWidgetItem(text))

                self.peak_table.item(row, 0).setData(0x0100, dict(peak))
            self.peak_table.setColumnWidth(label_col, 142 if is_3d else 94)
        finally:
            self._loading_peaks = False
        self._ensure_assignment_widgets()

    def _table_peaks(self) -> list[dict]:
        """Read the current contents of the table back into a peak dict list (the unedited line is
        an empty string).
        """
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
        selected (0.2.199-patch29ba).
        """
        has_context = bool(
            self.manager.project is not None and self._current_exp_id and self._current_data_id
        )
        self.delete_peak_button.setEnabled(has_context and self.peak_table.rowCount() > 0)

    def _sync_peaks_in_memory(self) -> None:
        """Synchronize memory _peaks after table editing (do not rebuild viewer immediately to
        avoid lagging).
        """
        if self._loading_peaks:
            return
        self._peaks = self._table_peaks()
        self._update_delete_button()

    def _on_peak_cell_edited(self, item) -> None:
        """Peak table cell editing: memory synchronization; the Assignment column is managed by the
        segment input box component (0.2.199-patch29cp) and is not processed here.
        """
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
        (0.2.199-patch29cp).
        """
        if self._loading_peaks or self._applying_label_format:
            return
        widget = self.peak_table.cellWidget(row, self._peak_keys.index("label"))
        if widget is None or not hasattr(widget, "merged_text"):
            return
        label = widget.merged_text()

        if 0 <= row < len(self._peaks):
            self._peaks[row]["label"] = label
        self.viewer.apply_label_edit(row, label)

    def _on_add_peak_toggled(self, checked: bool) -> None:
        """Add peak switch: After turning it on, click spectrum to add peak (adsorb peak top); with
        1D/Select mutually exclusive.
        """
        if checked:
            self.select_peaks_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_box_select_mode(False)
        self.viewer.set_peak_click_mode("add" if checked else "select")
        self.add_peak_button.setText("Add peak mode: ON" if checked else "Add peak mode")

    def _on_select_mode_toggled(self, checked: bool) -> None:
        """Selection mode: left-click and drag the box to select peaks; mutually exclusive with
        1D/Add peak.
        """
        if checked:
            self.add_peak_button.setChecked(False)
            self.viewer.show_1d_button.setChecked(False)
            self.viewer.set_peak_click_mode("select")
        self.viewer.set_box_select_mode(checked)
        self.select_peaks_button.setText("Select mode: ON" if checked else "Select mode")

    def _on_viewer_1d_toggled(self, checked: bool) -> None:
        """1D View close selection when open/Peak mode, and hide the peak-related controls
        (0.2.199-patch29bd).
        """
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
        single peak flashing is not triggered).
        """
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
        (automatic numbering) and displayed immediately.
        """
        if not (self.manager.project is not None and self._current_exp_id):
            return
        next_id = max((int(p.get("Peak_ID", 0) or 0) for p in self._peaks), default=0) + 1
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

        self._peaks = [peak for i, peak in enumerate(self._peaks) if i not in remove]
        self.viewer.set_peaks(self._peaks)
        self.save_peaks_button.setEnabled(bool(self._peaks))
        self._update_delete_button()

    def _on_import_poky(self) -> None:
        if self.manager.project is None or not self._current_exp_id:
            return
        start = str(Path.home())
        try:
            start = str(self.manager.data_dir(self._current_exp_id, self._current_data_id, "peaks"))
        except Exception:  # noqa: BLE001
            pass
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr(
                "import Poky peak table",
            ),
            start,
            tr(
                "Poky peak table (*.list);; all files (*)",
            ),
        )
        if not path:
            return
        try:
            peaks = import_peaks_poky(path, nuclei=self._current_3d_nuclei())
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, tr("import failed"), str(exc))
            return
        if not peaks:
            InfoDialog.show_info(
                self,
                tr(
                    "import results",
                ),
                tr(
                    "There are no parsable peak lines in the file",
                ),
            )
            return
        self._peaks = self._assign_peak_ids(peaks)
        self._populate_peak_table()

        self.viewer.set_peaks(self._peaks)
        self.export_poky_button.setEnabled(True)
        self.save_peaks_button.setEnabled(True)
        self._update_delete_button()

        InfoDialog.show_info(
            self.import_poky_button,
            tr("import completed"),
            tr(
                "The current peak table association was replaced with the Poky peak table ({p0} "
                'peak(s));\n(click "Save peak table" to write back to the .list '
                "file)",
                p0=len(peaks),
            ),
        )

    def _on_save_peaks(self) -> None:
        """The peak table is written back to data/peaks/<exp>-<data>.list and registered
        manual_peaks to run.
        """
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
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, tr("save failed"), describe_exception(exc))
            return
        self._peaks = peaks
        self.viewer.set_peaks(peaks)
        self.save_peaks_button.setEnabled(True)
        self.peaks_saved.emit()
        InfoDialog.show_info(
            self,
            tr(
                "save completed",
            ),
            tr(
                "peak table has been written:\n{p0}",
                p0=list_path,
            ),
        )

    def _export_peaks_poky(self) -> None:
        """Export the current peak table as Poky.list; disabled when there is no peak
        table/spectrum.
        """
        if not self._peaks or self.manager.project is None:
            return
        default = None
        try:
            default = (
                self.manager.data_dir(self._current_exp_id, self._current_data_id, "peaks")
                / f"{self._current_exp_id}-{self._current_data_id}.list"
            )
        except Exception:  # noqa: BLE001
            default = None
        start = str(default.parent) if default is not None else str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("export Poky peak table"),
            str(default) if default else start,
            tr("Poky peak table (*.list);; all files (*)"),
        )
        if not path:
            return
        try:
            export_peaks_poky(path, self._peaks, nuclei=self._current_3d_nuclei())
            InfoDialog.show_info(
                self,
                tr(
                    "export completed",
                ),
                tr(
                    "Poky peak table exported: {p0}",
                    p0=path,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            InfoDialog.show_info(self, tr("export failed"), str(exc))

    def _export_peaks_aligned(self) -> None:
        """Export after alignment: Select reference.list -> Overall translation search -> Export
        peak table after translation. 0.2.199-patch29fw(user): The exported reference spectrum
        can be any.list file, decoupled from the peak selection reference; press the overall
        translation to move the current peak file and output it, based on the reference.
        """
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
            from gui.settings import load_settings

            settings_tol = load_settings().get("alignment_tolerance_ppm") or None
            ref_path = Path(ref_path)
            ref_rows = import_peaks_poky(ref_path)
            if not ref_rows:
                InfoDialog.show_info(
                    self,
                    tr(
                        "Alignment export failed",
                    ),
                    tr(
                        "Reference peak file is empty or cannot be parsed",
                    ),
                )
                return

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
                InfoDialog.show_info(self, tr("Alignment export failed"), result["message"])
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
                    self.manager.data_dir(self._current_exp_id, self._current_data_id, "peaks")
                    / f"{self._current_exp_id}-{self._current_data_id}"
                    "_refaligned.list"
                )
            except Exception:  # noqa: BLE001
                default = None
            start = str(default.parent) if default is not None else str(Path.home())
            path, _ = QFileDialog.getSaveFileName(
                self,
                tr("export Poky peak table after alignment"),
                str(default) if default is not None else start,
                tr("Poky peak table (*.list);; all files (*)"),
            )
            if not path:
                return
            export_peaks_poky(path, out_rows, nuclei=nuclei)

            fig_lines: list[str] = []
            try:
                cur_path = None
                if self._current_spectrum:
                    try:
                        cur_path = self._peak_file_path(Path(self._current_spectrum))
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
                    tr("Alignment check chart (SVG): {p0}", p0=fig_path.with_suffix(".svg"))
                )
            except Exception as exc:  # noqa: BLE001
                fig_lines.append(tr("Alignment check map generation failed: {p0}", p0=exc))
            msg = (
                tr("Exported peak table after alignment: ")
                + str(path)
                + "\n"
                + tr(
                    "(alignment rate {p0:.0%}, shift {p1})",
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

    @staticmethod
    def _normalize_peak_snr(peaks: list[dict]) -> list[dict]:
        """Normalize a peak row's SNR to SN without changing the column layout.

        Copy pipeline CSV SNR only when SN is absent. Native Poky lists lack SNR; do not
        fabricate it.
        """
        for peak in peaks:
            if peak.get("SN") in (None, ""):
                value = peak.get("SNR")
                if value not in (None, ""):
                    peak["SN"] = value
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
        rather than silently responding.
        """
        if not self._current_exp_id:
            InfoDialog.show_info(self, tr("hint"), tr("No data is currently selected"))
            return
        if not self.load_current_spectrum():
            InfoDialog.show_info(
                self,
                tr(
                    "hint",
                ),
                tr(
                    "The current data has not generated spectrum yet",
                ),
            )

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
        self._clear_current_spectrum()

    def _clear_current_spectrum(self) -> None:
        """Clear the display and invalidate in-flight loads so old data cannot refill a new
        context.
        """
        self._autoload_timer.stop()
        self._ft3_load_token += 1
        with self._ft2_lock:
            self._ft2_pending = None
        self._stop_plane_stream()
        self._hide_loading_indicator()
        self._current_spectrum = None
        self._spectrum3d_panel.clear()
        self.viewer.clear()
        self._clear_peaks()
        self._viewer_1d_active = False
        self._projection_active = False

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

    def _comparison_candidates(self) -> list[tuple[str, str, str, Path]]:
        """List project main-result spectra for comparison, excluding the current spectrum and
        generated projections.
        """
        project = self.manager.project
        if project is None:
            return []
        current = self._current_spectrum
        candidates: list[tuple[str, str, str, Path]] = []
        for experiment in project.experiments:
            if getattr(experiment, "trashed", False):
                continue
            exp_title = experiment.title or experiment.id
            for data in experiment.data or []:
                if getattr(data, "trashed", False):
                    continue
                data_title = data.title or data.id
                spectra_dir = self.manager.data_dir(experiment.id, data.id, "spectra")
                for suffix in ("ft1", "ft2", "ft3"):
                    try:
                        paths = spectra_dir.glob(f"*.{suffix}")
                        for path in paths:
                            if self._is_projection_name(path.name) or path == current:
                                continue
                            label = f"{exp_title} / {data_title} / {path.name}"
                            candidates.append((label, experiment.id, data.id, path))
                    except OSError:
                        continue
        return sorted(candidates, key=lambda item: item[0].casefold())

    def _refresh_compare_menu(self) -> None:
        """Rebuild comparison choices from current project results before opening the menu."""
        self.compare_menu.clear()
        if self._comparison_active:
            close_action = self.compare_menu.addAction(tr("Close comparison"))
            close_action.setData("__close__")
            self.compare_menu.addSeparator()
        candidates = self._comparison_candidates()
        if not candidates:
            empty = self.compare_menu.addAction(tr("No other result spectra"))
            empty.setEnabled(False)
            return
        for label, exp_id, data_id, path in candidates:
            action = self.compare_menu.addAction(label)
            action.setData((exp_id, data_id, str(path)))

    def _on_compare_action(self, action) -> None:
        payload = action.data()
        if payload == "__close__":
            self._deactivate_comparison()
            return
        if not payload or len(payload) != 3:
            return
        _exp_id, _data_id, path_text = payload
        path = Path(path_text)
        if self._load_compare_spectrum(path):
            self._activate_comparison(path)

    def _ensure_compare_viewer(self) -> SpectrumViewer:
        if self._compare_viewer is None:
            self._compare_viewer = SpectrumViewer()
            self._compare_3d_panel = Spectrum3DPanel()
            self._compare_3d_panel.setVisible(False)
            self._compare_3d_panel.slice_changed.connect(self._render_compare_3d_view)
            self._compare_viewer.add_control_panel(self._compare_3d_panel)
            self._compare_viewer.view_splitter.splitterMoved.connect(
                lambda *_args: self._align_comparison_plot_height(
                    self._compare_viewer.view_splitter.sizes()[0]
                )
            )
        return self._compare_viewer

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if getattr(self, "_comparison_active", False):
            self._comparison_layout_timer.start(0)

    def _align_comparison_plot_height(self, height: int | None = None) -> None:
        """Match plot heights using the left plot, while reserving each control area's minimum
        space for mixed dimensionality.
        """
        if not self._comparison_active or self._compare_viewer is None:
            return
        viewers = (self._compare_viewer, self.viewer)
        totals = [sum(viewer.view_splitter.sizes()) for viewer in viewers]
        if min(totals) <= 0:
            return
        if height is None:
            height = self._compare_viewer.view_splitter.sizes()[0]
        limit = min(
            total
            - max(
                viewer.controls_layout.parentWidget().minimumSizeHint().height(),
                viewer.controls_layout.parentWidget().minimumHeight(),
            )
            for viewer, total in zip(viewers, totals)
        )
        height = max(1, min(height, limit))
        for viewer, total in zip(viewers, totals):
            with QSignalBlocker(viewer.view_splitter):
                viewer.view_splitter.setSizes([height, total - height])

    def _load_compare_spectrum(self, path: Path) -> bool:
        """Load the left comparison spectrum read-only into its independent viewer and controls."""
        viewer = self._ensure_compare_viewer()
        panel3d = self._compare_3d_panel
        assert panel3d is not None
        try:
            viewer.clear()
            panel3d.clear()
            suffix = path.suffix.lower()
            if suffix == ".ft1":
                from viewer.spectrum import Spectrum1D

                viewer.add_spectrum(Spectrum1D.load_from_ft1(path), name=path.stem)
            elif suffix == ".ft3":
                from viewer.spectrum import Spectrum3D

                panel3d.set_spectrum3d(Spectrum3D.load_from_ft3(path, lazy=True))
                panel3d.setVisible(True)
                self._compare_spectrum = path
                self._render_compare_3d_view()
            else:
                from viewer.spectrum import Spectrum

                viewer.add_spectrum(Spectrum.load_from_ft2(path), name=path.stem)
            self._compare_spectrum = path
            return True
        except Exception as exc:  # noqa: BLE001
            viewer.clear()
            panel3d.clear()
            self._compare_spectrum = None
            self.log_message.emit(
                tr(
                    "Comparison spectrum loading failed {p0}: {p1}",
                    p0=path.name,
                    p1=describe_exception(exc),
                )
            )
            return False

    def _render_compare_3d_view(self) -> None:
        viewer = self._compare_viewer
        panel3d = self._compare_3d_panel
        if viewer is None or panel3d is None:
            return
        spectrum = panel3d.current_spectrum()
        if spectrum is None:
            return
        base = self._compare_spectrum.stem if self._compare_spectrum else "3D"
        viewer.update_spectrum_data(
            spectrum,
            name=panel3d.current_name(base),
        )

    @staticmethod
    def _comparison_column(title: str, viewer: SpectrumViewer) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        layout.addWidget(label, 0)
        layout.addWidget(viewer, 1)
        return column

    def _activate_comparison(self, path: Path) -> None:
        """Use three columns: comparison spectrum, current spectrum, shared file/peak table."""
        if not self._expanded or self._expand_splitter is None:
            return
        if self._comparison_active:
            if self._compare_column is not None:
                label = self._compare_column.findChild(QLabel)
                if label is not None:
                    label.setText(path.name)
            self._comparison_layout_timer.start(0)
            return
        viewer = self.viewer
        compare_viewer = self._ensure_compare_viewer()
        plot_area = self._expand_plot_area
        controls_widget = self._expand_viewer_controls
        utility = self._expand_controls
        if plot_area is None or controls_widget is None or utility is None:
            return

        plot_area.setParent(None)
        controls_widget.setParent(None)
        utility.setParent(None)
        viewer.view_splitter.addWidget(plot_area)
        viewer.view_splitter.addWidget(controls_widget)
        if self._view_splitter_sizes:
            viewer.view_splitter.setSizes(self._view_splitter_sizes)
        viewer.setVisible(True)
        compare_viewer.setVisible(True)

        current_name = (
            self._current_spectrum.name
            if self._current_spectrum is not None
            else tr("Current spectrum")
        )
        compare_column = self._comparison_column(path.name, compare_viewer)
        current_column = self._comparison_column(current_name, viewer)
        self._compare_column = compare_column
        self._current_column = current_column
        self._expand_splitter.addWidget(compare_column)
        self._expand_splitter.addWidget(current_column)
        self._expand_splitter.addWidget(utility)
        self._expand_splitter.setStretchFactor(0, 1)
        self._expand_splitter.setStretchFactor(1, 1)
        self._expand_splitter.setStretchFactor(2, 0)
        width = max(self.width(), 1200)
        utility_width = max(320, min(width // 4, 480))
        spectrum_width = max(360, (width - utility_width) // 2)
        self._expand_splitter.setSizes([spectrum_width, spectrum_width, utility_width])
        self._comparison_active = True
        self._comparison_layout_timer.start(0)

    def _deactivate_comparison(self) -> None:
        """Restore the original expanded layout without sharing the two viewers' control state."""
        if not self._comparison_active:
            return
        self._comparison_layout_timer.stop()
        viewer = self.viewer
        compare_viewer = self._compare_viewer
        splitter = self._expand_splitter
        utility = self._expand_controls
        plot_area = self._expand_plot_area
        controls_widget = self._expand_viewer_controls
        if splitter is None or utility is None or plot_area is None or controls_widget is None:
            self._comparison_active = False
            return

        viewer.setParent(None)
        if compare_viewer is not None:
            compare_viewer.setParent(None)
            compare_viewer.setVisible(False)
        utility.setParent(None)
        plot_area.setParent(None)
        controls_widget.setParent(None)
        for column in (self._compare_column, self._current_column):
            if column is not None:
                column.setParent(None)
                column.deleteLater()
        self._compare_column = None
        self._current_column = None
        utility.layout().insertWidget(1, controls_widget, 0)
        splitter.addWidget(plot_area)
        splitter.addWidget(utility)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        right_width = max(360, min(self.width(), 560)) if self.width() > 100 else 520
        splitter.setSizes([max(400, self.width() - right_width), right_width])
        viewer.setVisible(False)
        self._comparison_active = False

    def _on_expand_toggled(self, expanded: bool) -> None:
        """Spectrum enlarge/close: Only the drawing area extends to the left, and the keys remain
        on the right.
        """
        self.expand_button.setText(tr("close") if expanded else tr("enlarge"))
        if expanded:
            self._enter_expand_mode()
        else:
            self._exit_expand_mode()
        self.expand_requested.emit(expanded)

    def _enter_expand_mode(self) -> None:
        """Zoom in: Only the drawing area (plot_area) is moved to the left alone to cover the
        original left three column area, and all keys are retained in the right control column;
        the original small drawing area (viewer container) is hidden and not displayed.
        """
        if self._expanded or self._expand_splitter is not None:
            return
        outer = self.layout()
        viewer = self.viewer
        self._collapsed_sizes = list(self._panel_splitter.sizes())
        self._view_splitter_sizes = list(viewer.view_splitter.sizes())
        plot_area = viewer.plot_area
        controls_widget = viewer.controls_layout.parentWidget()

        plot_area.setParent(None)
        controls_widget.setParent(None)
        self._expand_plot_area = plot_area
        self._expand_viewer_controls = controls_widget

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

        _hsplit_index = outer.indexOf(hsplit)
        if _hsplit_index >= 0:
            outer.setStretch(_hsplit_index, 1)
        self._panel_splitter.setVisible(False)
        viewer.setVisible(False)
        self._expanded = True
        self.compare_button.setVisible(True)

    def _exit_expand_mode(self) -> None:
        """Restore: The drawing area and control panel return to the viewer, and the controls on
        the right return to their original positions.
        """
        if not self._expanded or self._expand_splitter is None:
            return
        self._deactivate_comparison()
        outer = self.layout()
        viewer = self.viewer
        outer.replaceWidget(self._expand_splitter, self._panel_splitter)

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
        self.compare_button.setVisible(False)

    def _on_peak_header_clicked(self, section: int) -> None:
        """Click on the Assignment column heading: Peak Assignment Label on Switch Plot
        (0.2.199-patch29bf).
        """
        if section != 1:
            return
        new_state = not self.viewer.peak_labels_visible
        self.viewer.set_peak_labels_visible(new_state)
        header_item = self.peak_table.horizontalHeaderItem(1)
        if header_item is not None:
            header_item.setText("Assignment ✓" if new_state else "Assignment ✗")

    def _jump_3d_slice_to_peak(self, row: int) -> None:
        """3D peak table point peak: Jump the slice to the section corresponding to the fixed axis
        of the peak, and then display it according to 2D logic (0.2.199-patch29dc). Peaks that
        lack fixed axis coordinates or coordinates out of bounds (2D peak table/Old peak
        selection results) will not jump and will prompt (0.2.199-patch29de: avoid jumping to
        the last section for any peak).
        """
        s3d_panel = self._spectrum3d_panel
        s3d = s3d_panel.spectrum3d
        primary = self.viewer.primary_spectrum
        if s3d is None or primary is None:
            return
        if getattr(primary, "slice_axis", None) is None:
            return
        if not (0 <= row < len(self._peaks)):
            return
        slice_axis = getattr(s3d_panel, "_slice_axis", None)
        if slice_axis is None or not (0 <= int(slice_axis) < len(s3d.axes)):
            return
        peak = self._peaks[row]
        try:
            value = float(peak.get(f"F{int(slice_axis) + 1}_shift"))
        except (TypeError, ValueError):
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
            return
        rows = self.peak_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if 0 <= row < len(self._peaks):
            self._jump_3d_slice_to_peak(row)
            self.viewer.highlight_peak(row)

    def _on_peak_cell_clicked(self, row: int, column: int) -> None:
        """Clicking on the peak table cell: Clicking the selected row again will also trigger
        flickering positioning (0.2.199-patch29cm, selectionChanged will not trigger on the
        selected row).
        """
        if self._syncing_table_selection:
            return
        if 0 <= row < len(self._peaks):
            self._jump_3d_slice_to_peak(row)
            self.viewer.highlight_peak(row)
