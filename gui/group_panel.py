"""The middle page of the data group: Batch processing of all data in the group (0.2.163). Two
modes: - Process the entire group according to reference data: select the data that has run
spectrum in the experiment, and take the valid parameters of the most recent successful spectrum
run and apply it to each data in the group (can be cut off to a certain step in the pipeline); -
optimise data in the group sequentially: perform a complete automatic processing process for
each data in the group in sequence (fid -> spectrum [unified automatic optimisation] -> peaks;
analysis step has been deleted, 2026-09-12). The actual execution is started by main_window and
the background thread is called ProcessingController.run_group_batch (workflow.batch.run_batch).
This panel is only responsible for parameter selection and signal issuance."""

from __future__ import annotations

from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import (
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    fit_combo_width,
)

# Cutoff step options: value=BATCH_STEPS subset.
STOP_STEP_OPTIONS: list[tuple[str, str]] = [
    ("fid", tr("Generate FID")),
    ("spectrum", tr("Generate spectrum")),
    ("peaks", tr("Peak picking")),
]


class GroupBatchPanel(QWidget):
    """Data group batch processing panel (displayed when the group node is selected)."""

    log_message = Signal(str)
    run_group_batch_requested = Signal(str, str, list, str, dict)
    # (exp_id, group_id, steps, reference_data_id)
    summary_requested = Signal(dict)  # Batch summary (information + data-by-data results).

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = None
        self._exp_id = ""
        self._group_id = ""

        # 0.2.199-patch29hd: The whole page can be scrolled up and down -- member/Contextual
        # annotation content is no longer squashed when it is long.
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._content = QWidget()
        self._scroll.setWidget(self._content)
        _outer = QVBoxLayout(self)
        _outer.setContentsMargins(0, 0, 0, 0)
        _outer.addWidget(self._scroll)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel(tr("data group batch processing"))
        title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)

        self.context_label = QLabel("")
        self.context_label.setWordWrap(True)
        self.context_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.context_label)

        self.member_label = QLabel("")
        self.member_label.setWordWrap(True)
        self.member_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.member_label)
        # 0.2.199-patch29gw: Data annotation within the group (field x data grid), placed above
        # "Process by reference data".
        self.notes_label = QLabel(tr("In-group data annotation"))
        self.notes_label.setStyleSheet(
            f"font-weight: bold; color: {TEXT_PRIMARY};"
        )
        self.notes_label.setVisible(False)
        layout.addWidget(self.notes_label)
        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.notes_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.notes_scroll.setStyleSheet(
            "background: #1e1e1e; color: #ffffff; border: 1px solid #3c3c3c;"
        )
        self.notes_scroll.setMaximumHeight(200)
        self.notes_scroll.setVisible(False)
        layout.addWidget(self.notes_scroll)
        layout.addSpacing(6)

        # ---- Mode A: Process the entire group according to reference data ----.
        ref_box = QGroupBox(tr("Process entire group by reference data"))
        ref_layout = QVBoxLayout(ref_box)
        ref_hint = QLabel(
            tr(
                "Pick a data set of this experiment that already has a spectrum; its processing "
                "parameters are applied to every data set in the group. You may also stop at a "
                "chosen step (for example, generate the FID "
                "only).",
            )
        )
        ref_hint.setWordWrap(True)
        ref_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        ref_layout.addWidget(ref_hint)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(tr("Reference data:")))
        self.reference_combo = QComboBox()
        self.reference_combo.setMinimumWidth(240)
        row1.addWidget(self.reference_combo, 1)
        ref_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Processed to:")))
        self.stop_combo = QComboBox()
        for _value, label in STOP_STEP_OPTIONS:
            self.stop_combo.addItem(label)
        fit_combo_width(self.stop_combo)  # Fix ellipses (0.2.199-patch29hz-fix 14).
        row2.addWidget(self.stop_combo)
        row2.addStretch(1)
        ref_layout.addLayout(row2)

        self.run_ref_button = QPushButton(tr("Process entire group by reference data"))
        self.run_ref_button.setEnabled(False)
        self.run_ref_button.clicked.connect(self._on_run_reference)
        ref_layout.addWidget(self.run_ref_button)
        layout.addWidget(ref_box)

        # ---- Mode B: optimise the data within the group in sequence ----.
        opt_box = QGroupBox(tr("optimise the data within the group in turn"))
        opt_layout = QVBoxLayout(opt_box)
        opt_hint = QLabel(
            tr(
                "Runs the automatic pipeline (FID -> spectrum -> peak picking) for every data set "
                "in the group in turn; one failure does not abort the whole "
                "group.",
            )
        )
        opt_hint.setWordWrap(True)
        opt_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        opt_layout.addWidget(opt_hint)
        # Process to a certain step.
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel(tr("Processed to:")))
        self.opt_stop_combo = QComboBox()
        for _value, _label in STOP_STEP_OPTIONS:
            self.opt_stop_combo.addItem(_label)
        fit_combo_width(self.opt_stop_combo)  # Fix ellipses (0.2.199-patch29hz-fix 14).
        opt_row.addWidget(self.opt_stop_combo)
        opt_row.addStretch(1)
        opt_layout.addLayout(opt_row)
        # Direct dimension range setting (displayed when processing to spectrum/peaks).
        self.ext_group = QGroupBox(tr("direct dimension range setting"))
        ext_row = QHBoxLayout(self.ext_group)
        self.ext_enable = QCheckBox(tr("Specify range"))
        ext_row.addWidget(self.ext_enable)
        ext_row.addWidget(QLabel(tr("Lower limit(ppm):")))
        self.ext_lo_edit = QLineEdit("10.5")
        ext_row.addWidget(self.ext_lo_edit)
        ext_row.addWidget(QLabel(tr("Upper limit(ppm):")))
        self.ext_hi_edit = QLineEdit("6.5")
        ext_row.addWidget(self.ext_hi_edit)
        opt_layout.addWidget(self.ext_group)
        # Peak picking threshold setting (displayed when peaks are processed).
        self.thresh_group = QGroupBox(tr("Peak picking threshold setting"))
        thr_row = QHBoxLayout(self.thresh_group)
        thr_row.addWidget(QLabel(tr("Noise threshold (σ):")))
        self.thresh_edit = QLineEdit("")
        self.thresh_edit.setPlaceholderText(tr("Leave blank to use default"))
        thr_row.addWidget(self.thresh_edit)
        opt_layout.addWidget(self.thresh_group)
        self.opt_stop_combo.currentIndexChanged.connect(
            self._on_opt_stop_changed
        )
        # 0.2.199-patch29gp: Initially press the default cut-off step (generate FID)Hidden
        # range/Threshold setting.
        self._on_opt_stop_changed()
        self.run_optimize_button = QPushButton(tr("optimise the data within the group in turn"))
        self.run_optimize_button.setEnabled(False)
        self.run_optimize_button.clicked.connect(self._on_run_optimize)
        opt_layout.addWidget(self.run_optimize_button)
        layout.addWidget(opt_box)

        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    def set_context(
        self, manager, exp_id: str, group_id: str
    ) -> None:
        """Bind project context and refresh group information/Reference data drop-down."""
        self._manager = manager
        self._exp_id = exp_id
        self._group_id = group_id
        self._refresh()
        self._refresh_notes()

    def _refresh_notes(self) -> None:
        """Data annotation within the group: Annotation fields are rows (field names in the first
        column), and each data is a column."""
        from gui.notes import DATA_FIELDS, data_note_fields

        manager = self._manager
        exp_id, group_id = self._exp_id, self._group_id
        self.notes_label.setVisible(False)
        self.notes_scroll.setVisible(False)
        if manager is None or manager.project is None:
            return
        exp = manager.project.experiment(exp_id)
        group = manager.group(exp_id, group_id) if exp is not None else None
        if group is None:
            return
        member_ids = (group.data_ids or [])
        if not member_ids:
            return
        cols_data: list[tuple[str, str, dict]] = []
        for data_id in member_ids:
            d = next((x for x in exp.data if x.id == data_id), None)
            label_text = (d.title or f"Data {data_id}") if d else f"Data {data_id}"
            cols_data.append(
                (data_id, label_text, data_note_fields(manager.project, exp_id, data_id))
            )
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
        head0 = QLabel(tr("data"))
        head0.setStyleSheet(
            "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
        )
        grid.addWidget(head0, 0, 0)
        for ci, (_data_id, label_text, _f) in enumerate(cols_data, start=1):
            head = QLabel(f"◆ {label_text}")
            head.setWordWrap(True)
            head.setStyleSheet(
                "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
            )
            grid.addWidget(head, 0, ci)
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
        self.notes_scroll.setWidget(container)
        self.notes_label.setVisible(True)
        self.notes_scroll.setVisible(True)

    def _refresh(self) -> None:
        manager = self._manager
        exp_id, group_id = self._exp_id, self._group_id
        if manager is None or manager.project is None:
            return
        exp = manager.project.experiment(exp_id)
        group = manager.group(exp_id, group_id) if exp is not None else None
        if exp is None or group is None:
            self.context_label.setText(tr("Group {p0}(does not exist)", p0=group_id))
            self.run_ref_button.setEnabled(False)
            self.run_optimize_button.setEnabled(False)
            return
        title = group.title or f"Group {group_id}"
        self.context_label.setText(tr(
            "experiment: {p0} · Group: "
            "{p1}",
            p0=exp.title or exp_id,
            p1=title,
        ))
        members = [
            d
            for d in exp.data
            if d.id in (group.data_ids or [])
        ]
        names = ",".join(
            f"{d.title or 'Data ' + d.id}" for d in members
        )
        self.member_label.setText(
            tr(
                "Data within the group ({p0} indivual): "
                "{p1}",
                p0=len(members),
                p1=names or '(empty group)',
            )
        )
        self.run_optimize_button.setEnabled(bool(members))
        self._refresh_reference_combo(exp, members)

    def _refresh_reference_combo(self, exp, members) -> None:
        """Reference data drop-down: spectrum data generated in the experiment (including group
        members)."""
        self.reference_combo.clear()
        candidates: list[tuple[str, str]] = []
        for data in exp.data:
            if not data.spectrum_path:
                continue
            label = data.title or f"Data {data.id}"
            candidates.append((data.id, f"{label} ({data.id})"))
        if not candidates:
            self.run_ref_button.setEnabled(False)
            self.reference_combo.addItem(tr("(No spectrum data has been generated)"))
            return
        for data_id, label in candidates:
            self.reference_combo.addItem(label, data_id)
        self.run_ref_button.setEnabled(True)

    def _stop_steps(self) -> list[str]:
        """BATCH_STEPS prefix subset (starting from fid) corresponding to the cutoff step."""
        value = STOP_STEP_OPTIONS[self.stop_combo.currentIndex()][0]
        order = ("fid", "spectrum", "peaks")
        return list(order[: order.index(value) + 1])

    def _on_run_reference(self) -> None:
        ref_id = str(self.reference_combo.currentData() or "")
        if not ref_id:
            self.log_message.emit(tr("Please select reference data first"))
            return
        steps = self._stop_steps()
        self.run_group_batch_requested.emit(
            self._exp_id, self._group_id, steps, ref_id, {}
        )

    def _opt_steps(self) -> list[str]:
        """The cut-off steps of the optimisation mode (prefixed by fid) are followed in turn."""
        value = STOP_STEP_OPTIONS[self.opt_stop_combo.currentIndex()][0]
        order = ("fid", "spectrum", "peaks")
        return list(order[: order.index(value) + 1])

    def _on_opt_stop_changed(self) -> None:
        """Show and hide direct dimension by cutoff step scope/Peak threshold setting."""
        steps = self._opt_steps()
        self.ext_group.setVisible("spectrum" in steps)
        self.thresh_group.setVisible("peaks" in steps)

    def _collect_params(self) -> dict:
        """Collects the currently visible settings into the processing parameter."""
        params: dict = {}
        steps = self._opt_steps()
        if "spectrum" in steps and self.ext_enable.isChecked():
            lo = self.ext_lo_edit.text().strip()
            hi = self.ext_hi_edit.text().strip()
            if lo:
                params["ext_lo"] = lo
            if hi:
                params["ext_hi"] = hi
        if "peaks" in steps and self.thresh_edit.text().strip():
            params["sigma_multiplier"] = self.thresh_edit.text().strip()
        return params

    def _on_run_optimize(self) -> None:
        steps = self._opt_steps()
        self.run_group_batch_requested.emit(
            self._exp_id,
            self._group_id,
            steps,
            "",
            self._collect_params(),
        )

    @property
    def current_group_id(self) -> str:
        """Current data group id (empty string if not selected)."""
        return str(self._group_id or "")

    def refresh(self) -> None:
        """Refresh the group page (exposed package: _refresh, for center_panel call)."""
        self._refresh()

    def set_progress(self, text: str) -> None:
        """Batch progress prompt (main thread update)."""
        self.progress_label.setText(text)
        self.progress_label.setVisible(bool(text))
