"""GUI Dialog component: information/confirm/Import experiments/project form. Do not use
QMessageBox (when triggering the modal QMessageBox from the menu under Windows + Qt6, it will
print "This plugin supports grabbing the mouse only for popup windows"), use ordinary QDialog."""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtCore import QEvent, QObject, Qt, QTimer
from qtcompat.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.notes import (
    DIMENSION_OPTIONS,
    EXPERIMENT_CATEGORY_OPTIONS,
    NUCLEI_OPTIONS,
    experiment_type_options,
    note_fields,
)
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_MUTED, TEXT_PRIMARY


def _center_on_screen(dialog: QDialog) -> None:
    """Move the dialog box to the centre of the screen where it is located (all pop-up windows are
    centered, 0.2.112)."""
    parent = dialog.parentWidget()
    screen = None
    if parent is not None:
        window = parent.window()
        if window is not None:
            screen = QApplication.screenAt(window.frameGeometry().center())
    screen = screen or QApplication.primaryScreen()
    if screen is None:
        return
    geo = screen.availableGeometry()
    dialog.move(
        geo.left() + max(0, (geo.width() - dialog.width()) // 2),
        geo.top() + max(0, (geo.height() - dialog.height()) // 2),
    )


class _DialogCenteringFilter(QObject):
    """QDialog is automatically centered on the screen (0.2.112) when displayed."""

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QDialog) and event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, lambda d=obj: _center_on_screen(d))
        return super().eventFilter(obj, event)


def install_dialog_centering(app) -> None:
    """Installs the application-level dialog centering filter (main window/Independent viewer entry
    call)."""
    app._dialog_centering_filter = _DialogCenteringFilter(app)
    app.installEventFilter(app._dialog_centering_filter)


class InfoDialog(QDialog):
    """Information dialog box with OK button (replacing QMessageBox.information/critical/about).
    0.2.199-patch29et: Popup type + show front screen centre -- The move() of ordinary QDialog
    under native Wayland is ignored by the synthesizer (the pop-up window falls in the upper
    left corner), and the Popup moves to the xdg_popup positioner and can be reliably centered;
    click outside to close (small information pop-up window semantics)."""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Popup)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        message = QLabel(text)
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        # 0.2.199-patch29et: Centered in front of show (Wayland xdg_popup is anchored at the initial
        # position when displayed, and moving after show is invalid).
        self.adjustSize()
        _center_on_screen(self)

    @staticmethod
    def show_info(parent: QWidget | None, title: str, text: str) -> None:
        InfoDialog(parent, title, text).exec()


class MultiSelectDataDialog(QDialog):
    """Multi-select sample data dialog box (used to add other data to the data group). items:
    [(data_id, label),...];selected_ids() Returns the checked data id list."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        items: list[tuple[str, str]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        hint = QLabel(
            tr(
            "Check the sample data to be added to the group (multiple choices are "
            "available):",
        )
        )
        layout.addWidget(hint)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        for data_id, label in items:
            item = QListWidgetItem(f"{label} ({data_id})")
            item.setData(Qt.ItemDataRole.UserRole, data_id)
            self._list.addItem(item)
        layout.addWidget(self._list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Join group"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_ids(self) -> list[str]:
        """Returns a list of checked data ids."""
        return [
            str(self._list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self._list.count())
            if self._list.item(index).isSelected()
        ]


class ConfirmDialog(QDialog):
    """Yes/No confirmation dialog (replaces QMessageBox.question)."""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        message = QLabel(text)
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes
            | QDialogButtonBox.StandardButton.No
        )
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText(tr("yes"))
        buttons.button(QDialogButtonBox.StandardButton.No).setText(tr("no"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def confirm(parent: QWidget | None, title: str, text: str) -> bool:
        return ConfirmDialog(parent, title, text).exec() == QDialog.DialogCode.Accepted


class ImportExperimentDialog(QDialog):
    """Import experiment: Select Bruker dataset directory + title + associated projects."""

    def __init__(
        self,
        parent: QWidget | None,
        samples: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("import sample data"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.source_edit = QLineEdit()
        self.source_edit.textChanged.connect(self._update_segmented_hint)
        browse = QPushButton(tr("Browse..."))
        browse.clicked.connect(self._browse)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_edit, 1)
        source_row.addWidget(browse)
        self.source_widget = QWidget()
        self.source_widget.setLayout(source_row)
        form.addRow(tr("Bruker dataset directory:"), self.source_widget)

        self.title_edit = QLineEdit()
        form.addRow(tr("Title (can be left blank):"), self.title_edit)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(70)
        self.notes_edit.setPlaceholderText(
            tr(
                "sample data annotation (optional, can also be edited in the upper middle after "
                "import)",
            )
        )
        form.addRow(tr("Comments (optional):"), self.notes_edit)

        self.sample_combo = QComboBox()
        self.sample_combo.addItem(tr("(none)"), "")
        for sample_id, name in samples or []:
            self.sample_combo.addItem(f"{sample_id} {name}".strip(), sample_id)
        form.addRow(tr("Related projects:"), self.sample_combo)
        self.copy_check = QCheckBox(
            tr(
            "Link raw data to project (read-only file link, copy if "
            "necessary)",
        )
        )
        self.copy_check.setChecked(True)
        self.copy_check.setToolTip(
            tr(
                "When checked, the Bruker data set is linked into the project raw/ directory and "
                "an input fingerprint is computed; when unchecked it is only registered by "
                "reference (the source directory must stay "
                "accessible).",
            )
        )
        form.addRow("", self.copy_check)
        # 0.2.108: Segmented collection and import (container directory, multiple subdirectories
        # containing acqus are merged into one piece of data).
        self.segmented_check = QCheckBox(
                tr(
                "Segmented data or repeated experiment overlay import (container directory: "
                "multiple subdirectories containing acqus are merged into one "
                "data)",
            )
        )
        self.segmented_check.setToolTip(
            tr(
                "Use this for one acquisition split into several segments (segmented NUS / "
                "averaged repeat experiments); leave it unchecked for an ordinary single data set. "
                "It is ticked automatically when you pick a container "
                "directory.",
            )
        )
        self.segmented_check.setChecked(False)
        form.addRow("", self.segmented_check)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("import"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

    def _update_segmented_hint(self, text: str = "") -> None:
        """When the source directory is a container directory (>= 2 subdirectories containing
        acqus), segmented import is automatically checked."""
        try:
            from gui.processing import is_segmented_container

            if text and is_segmented_container(text):
                self.segmented_check.setChecked(True)
        except Exception:  # noqa: BLE001 - Maintain current state when directory is unreadable.
            pass

    def _validate_and_accept(self) -> None:
        source = self.source_edit.text().strip()
        if not source:
            InfoDialog.show_info(self, tr("hint"), tr("Please select Bruker dataset directory"))
            return
        source_path = Path(source)
        if not source_path.is_dir():
            InfoDialog.show_info(self, tr("hint"), tr("The selected directory does not exist"))
            return
        if not source_path.joinpath("acqus").is_file():
            from gui.processing import is_segmented_container

            if not is_segmented_container(source_path):
                # 0.2.198: It is normal that the top level of the container does not contain acqus.
                # It prompts to focus on the "data segment" and no longer regards "the top level is
                # missing acqus" as a problem.
                InfoDialog.show_info(
                    self,
                    tr("hint"),
                    tr(
                        "The selected directory is neither a Bruker data set nor a "
                        "segmented/repeat-experiment container\n(a container needs at least 2 "
                        "subdirectories that each hold an acqus data segment; non-data "
                        "subdirectories were "
                        "ignored)",
                    ),
                )
                return
        self.accept()

    def result_data(self) -> dict:
        return {
            "source": self.source_edit.text().strip(),
            "title": self.title_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
            "sample_id": self.sample_combo.currentData() or "",
            "copy": self.copy_check.isChecked(),
            "segmented": self.segmented_check.isChecked(),
        }


class NotesDialog(QDialog):
    """Third-level annotation form: Fill in line by line according to the hierarchical field list;
    use drop-down for fields with only a few values. For sample data annotation, select
    dimension first, and then filter by presets to provide data type options; experimental
    annotations only display currently supported experimental categories; core (combination)
    also provides common options. The remaining fields remain text input."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        kind: str = "",
        values: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        hint = QLabel(tr("General information (can be left blank):"))
        layout.addWidget(hint)
        self._edits: dict[str, QLineEdit] = {}
        self._combos: dict[str, QComboBox] = {}
        self._type_combo: QComboBox | None = None
        values = dict(values or {})
        form = QFormLayout()
        for key, label in note_fields(kind):
            if key == "dimension":
                combo = QComboBox()
                combo.addItem("", "")
                for option in DIMENSION_OPTIONS:
                    combo.addItem(option, option)
                current = str(values.get("dimension", "") or "")
                if current:
                    if current not in DIMENSION_OPTIONS:
                        combo.addItem(current, current)
                    combo.setCurrentText(current)
                combo.currentIndexChanged.connect(self._on_dimension_changed)
                self._combos[key] = combo
                form.addRow(f"{label}:", combo)
            elif key == "experiment_type":
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItem("", "")
                if kind == "experiment":
                    # Experiment annotations only display currently supported experimental
                    # categories.
                    combo.addItems(EXPERIMENT_CATEGORY_OPTIONS)
                else:
                    self._type_combo = combo
                self._combos[key] = combo
                current = str(values.get("experiment_type", "") or "")
                if current:
                    combo.setEditText(current)
                form.addRow(f"{label}:", combo)
            elif key == "peak_sign":
                combo = QComboBox()
                combo.addItem("", "")
                combo.addItem(tr("Single symbol spectrum (uniform)"), "uniform")
                combo.addItem(tr("Positive and negative peak spectrum (mixed)"), "mixed")
                current = str(values.get("peak_sign", "") or "")
                if current in ("uniform", "mixed"):
                    combo.setCurrentData(current)
                self._combos["peak_sign"] = combo
                form.addRow(f"{label}:", combo)
            elif key == "nuclei":
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItem("", "")
                combo.addItems(NUCLEI_OPTIONS)
                current = str(values.get("nuclei", "") or "")
                if current:
                    combo.setCurrentText(current)
                self._combos[key] = combo
                form.addRow(f"{label}:", combo)
            else:
                edit = QLineEdit()
                edit.setText(str(values.get(key, "") or ""))
                edit.setPlaceholderText(tr("Can be left blank"))
                self._edits[key] = edit
                form.addRow(f"{label}:", edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Sure"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        # Sample data annotation: After dimension is determined, fill in the data type option
        # (select dimension first, then type).
        self._on_dimension_changed()

    def _dimension_value(self) -> str:
        """The currently selected dimension (empty means not selected)."""
        combo = self._combos.get("dimension")
        if combo is None:
            return ""
        return str(combo.currentData() or combo.currentText() or "")

    def _on_dimension_changed(self, *_args) -> None:
        """Sample data annotation: dimension changes -> repopulate data type options by presets."""
        type_combo = self._type_combo
        if type_combo is None:
            return
        current = type_combo.currentText().strip()
        options = experiment_type_options(self._dimension_value())
        type_combo.blockSignals(True)
        try:
            type_combo.clear()
            type_combo.addItem("", "")
            type_combo.addItems(options)
            if current in options:
                type_combo.setCurrentText(current)
            elif current:
                type_combo.setEditText(current)
        finally:
            type_combo.blockSignals(False)

    def result_fields(self) -> dict[str, str]:
        """Return the filled field dict (null values are eliminated)."""
        fields: dict[str, str] = {}
        for key, edit in self._edits.items():
            text = edit.text().strip()
            if text:
                fields[key] = text
        for key, combo in self._combos.items():
            text = combo.currentText().strip()
            if text:
                fields[key] = text
        return fields

class ScriptEditorDialog(QDialog):
    """Script editor (a simple text editor that imitates VSCode). Load.com content from
    render_scripts / manual_scripts; save written data to process directory (or fid.com of raw
    directory); "Run" issues run_requested(content), which is executed and registered by the
    main window via ProcessingController.run_manual_spectrum / run_manual_fid_com and registers
    WorkflowRun."""

    run_requested = Signal(str)  # Script content: emitted when "Run" is clicked.

    @staticmethod
    def _hint_text(script_name: str) -> str:
        """Corresponding prompts are given according to the script type: fid.com is a Bruker
        conversion script, and the processing script (process/nus) is NMRPipe spectrum
        processing. The prompts are different."""
        if script_name == "fid.com":
            return (
                tr(
                    "fid.com converts the raw Bruker data into an NMRPipe fid; the backend "
                    "generates it from the acquisition parameters, so it usually needs no "
                    "changes.\n- Conversion parameters (OBS/CAR/SW and friends) come from the "
                    "Bruker parameters; do not change them casually;\n- To adjust the spectrum "
                    "reference or carrier, edit the CAR entries such as -xCAR/-yCAR;\n- For NUS "
                    "data keep the nuslist handling; do not delete the sampling information;\n- "
                    "The output name is already {data id}.fid (Bruker's default test.fid is\n  "
                    "rewritten automatically by the backend; 3D slices keep test%03d.fid); after "
                    "the run the\n  backend moves it back to process/, so there is no need to move "
                    "or rename it by "
                    "hand.",
                )
            )
        return (
            tr(
                "The processing script is generated automatically by the backend from the sampling "
                "mode and usually needs no changes.\n- Poor baseline (wavy spectrum / spurious "
                "peaks): append `| nmrPipe -fn POLY -auto` after the FT of that dimension, or "
                "fine-tune POLY -ord;\n- Poor peak shape or resolution: adjust -off/-end/-pow/-c "
                "of the SP window function, or increase ZF -size;\n- Poor phase: adjust PS -p0/-p1 "
                "(auto phasing fills them in; normally leave them alone);\n- The -alt/-neg/-real "
                "flags of the FT are decided automatically from the sampling mode; if you believe "
                "the choice is wrong you can edit them: -neg flips the spectrum, -alt shifts it by "
                "half the spectral "
                "width.",
            )
        )

    def __init__(
        self,
        parent: QWidget | None,
        experiment_label: str,
        script_name: str = "process.com",
        content: str = "",
        save_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("script editor - {p0} ({p1})", p0=experiment_label, p1=script_name))
        self.resize(760, 540)
        self.script_name = script_name
        self.save_dir = save_dir
        layout = QVBoxLayout(self)
        hint = QLabel(self._hint_text(script_name))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(content)
        font = self.editor.font()
        font.setFamily("Consolas")
        font.setStyleHint(font.StyleHint.Monospace)
        self.editor.setFont(font)
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.save_btn.setText(tr("save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        self.run_btn = buttons.addButton(
            tr("run"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.run_btn.setToolTip(
            tr(
            "Save the current script to the data directory and run (register "
            "WorkflowRun)",
        )
        )
        self.run_btn.clicked.connect(self._on_run)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.save_message = QLabel("")
        self.save_message.setWordWrap(True)
        layout.addWidget(self.save_message)

    def _on_save(self) -> None:
        """"Save": write back the data directory and then close (0.2.192 Repair: the previous save
        did not save to disk)."""
        self.save_script()
        self.accept()

    def _on_run(self) -> None:
        """"Run": Save the current script first, then automatically close after sending the content
        (0.2.192)."""
        self.save_script()
        self.run_requested.emit(self.editor.toPlainText())
        self.close()

    def result_data(self) -> dict:
        return {"content": self.editor.toPlainText()}

    def save_script(self) -> Path | None:
        """Save the current content to the data directory (returns None if there is no
        directory)."""
        if self.save_dir is None:
            self.save_message.setText(tr("No save directory specified (data needs to be selected)"))
            return None
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            target = self.save_dir / self.script_name
            target.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.save_message.setText(tr("Saved: {p0}", p0=target))
            return target
        except OSError as exc:
            self.save_message.setText(tr("save failed: {p0}", p0=exc))
            return None


class RunHistoryDialog(QDialog):
    """Run History Dialog:workflow_runs List + Details(state/time/information/product)."""

    def __init__(
        self,
        parent: QWidget | None,
        runs: list,
        project_name: str = "",
        project_root: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("run history - {p0}", p0=project_name or 'NMRForge'))
        self.project_root = Path(project_root) if project_root else None
        self._current_snapshot = ""
        self.resize(720, 480)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(len(runs), 6)
        self.table.setHorizontalHeaderLabels(
            [tr("run"), tr("experiment"), tr("process"), tr("state"), tr("start"), tr("Finish")]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row, run in enumerate(runs):
            self.table.setItem(row, 0, QTableWidgetItem(run.run_id))
            self.table.setItem(row, 1, QTableWidgetItem(run.experiment_id))
            self.table.setItem(row, 2, QTableWidgetItem(run.workflow_ref))
            self.table.setItem(row, 3, QTableWidgetItem(run.status))
            self.table.setItem(row, 4, QTableWidgetItem(run.started_at[:19]))
            self.table.setItem(row, 5, QTableWidgetItem((run.finished_at or "")[:19]))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, run)
        self.table.itemSelectionChanged.connect(self._show_detail)
        layout.addWidget(self.table, 1)

        self.detail_label = QLabel(tr("Select a row to view details"))
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        layout.addWidget(self.detail_label)
        self.snapshot_button = QPushButton(tr("Open snapshot directory"))
        self.snapshot_button.setEnabled(False)
        self.snapshot_button.setToolTip(
            tr("Open the script/parameter snapshot directory of the run (if any)")
        )
        self.snapshot_button.clicked.connect(self._open_snapshot)
        layout.addWidget(self.snapshot_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_detail(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        run = items[0].data(Qt.ItemDataRole.UserRole)
        if run is None:
            return
        outputs = "\n".join(f"  {k}: {v}" for k, v in run.outputs.items()) or tr(" (none)")
        snapshot = run.snapshot_dir or ""
        scripts = ",".join(run.scripts) if run.scripts else tr("(none)")
        self._current_snapshot = ""
        if snapshot and self.project_root is not None:
            candidate = self.project_root / snapshot
            if candidate.is_dir():
                self._current_snapshot = str(candidate)
        self.snapshot_button.setEnabled(bool(self._current_snapshot))
        self.detail_label.setText(
            tr(
                "run: {p0}  [{p1}]\nPipeline: {p2} experiment: {p3}\nMessage: {p4}\nSnapshot: {p5} "
                "script: {p6}\nOutputs:\n{p7}",
                p0=run.run_id,
                p1=run.status,
                p2=run.workflow_ref,
                p3=run.experiment_id,
                p4=run.message or '-',
                p5=snapshot or '(none)',
                p6=scripts,
                p7=outputs,
            )
        )

    def _open_snapshot(self) -> None:
        """Open the script/parameter snapshot directory currently selected to run."""
        if not self._current_snapshot:
            return
        from qtcompat.QtCore import QUrl
        from qtcompat.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._current_snapshot))

class BatchSummaryDialog(QDialog):
    """Batch processing summary: success/failure list; Double-click the failed item to locate the
    data (stage C1)."""

    locate_requested = Signal(str)  # data_id

    def __init__(
        self,
        parent: QWidget | None,
        summary: dict,
        project_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Batch processing summary - {p0}", p0=project_name or 'NMRForge'))
        self.resize(520, 380)
        layout = QVBoxLayout(self)
        info = QLabel(str(summary.get("info", "")))
        info.setWordWrap(True)
        layout.addWidget(info)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)
        for item in summary.get("items", []):
            status = (
                tr("success")
                if item.get("ok")
                else tr("fail: ") + str(item.get("error", ""))
            )
            list_item = QListWidgetItem(
                f"{item.get('data_id', '')} {item.get('step', '')} · {status}"
            )
            list_item.setData(Qt.ItemDataRole.UserRole, item.get("data_id", ""))
            self.list_widget.addItem(list_item)
        self.list_widget.itemDoubleClicked.connect(self._on_item_activated)
        hint = QLabel(
            tr(
            "Double-click an entry to locate the corresponding data (failed items) or view the "
            "status on the "
            "left",
        )
        )
        hint.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        data_id = item.data(Qt.ItemDataRole.UserRole)
        if data_id:
            self.locate_requested.emit(str(data_id))


class SettingsDialog(QDialog):
    """Software settings (simplification, stage C3): interface language, NMRPipe path, default
    line width, simple mode; line width access generates spectrum parameter; save to
    nmrforge_data/config/nmrforge.local.yaml, restart to take effect."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Software settings"))
        # 0.2.199-patch29gh(user):The window is too short causing row content to be
        # cropped/Incomplete text display.
        self.resize(560, 480)
        self.setMinimumWidth(520)
        from gui.settings import (
            DEFAULTS,
            SETTINGS_FILENAME,
            is_appimage,
            load_settings,
        )

        settings = load_settings()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.nmrpipe_edit = QLineEdit(str(settings.get("nmrpipe_path", "")))
        self.nmrpipe_edit.setPlaceholderText(tr("Not configured (auto-find)"))
        form.addRow(tr("NMRPipe path"), self.nmrpipe_edit)
        # 0.2.199-patch29gg(user): data directory (empty = user main directory, import browsing
        # starting point).
        self.data_root_edit = QLineEdit(str(settings.get("data_root", "")))
        self.data_root_edit.setPlaceholderText(tr("default: {p0}", p0=Path.home()))
        data_root_browse = QPushButton(tr("Browse..."))
        data_root_browse.clicked.connect(self._browse_data_root)
        data_root_row = QHBoxLayout()
        data_root_row.addWidget(self.data_root_edit, 1)
        data_root_row.addWidget(data_root_browse)
        data_root_widget = QWidget()
        data_root_widget.setLayout(data_root_row)
        form.addRow(tr("Data directory"), data_root_widget)
        # 2026-09-21 (user): the interface language can be pinned here instead of relying on
        # the system locale / NMRFORGE_LANG. Same order as the resolver: auto follows the
        # system, zh/en pin it (an environment variable can still override it per run).
        self.language_combo = QComboBox()
        for _label, _code in (
            (tr("Follow the system language"), "auto"),
            (tr("Chinese"), "zh"),
            (tr("English"), "en"),
        ):
            self.language_combo.addItem(_label, _code)
        _index = self.language_combo.findData(str(settings.get("language", "auto")))
        self.language_combo.setCurrentIndex(_index if _index >= 0 else 0)
        self.language_combo.setToolTip(
            tr(
                "Pins the interface language; when following the system, NMRFORGE_LANG can "
                "still override it for a single run. Takes effect after restarting the program.",
            )
        )
        form.addRow(tr("Interface language"), self.language_combo)
        self.linewidth_spins: dict[str, QDoubleSpinBox] = {}
        self.tolerance_spins: dict[str, QDoubleSpinBox] = {}
        for nucleus, default in DEFAULTS["linewidth_hz"].items():
            spin = QDoubleSpinBox()
            spin.setRange(0, 200)
            spin.setDecimals(1)
            spin.setValue(float(settings["linewidth_hz"].get(nucleus, default)))
            form.addRow(tr("{p0} Default line width (Hz)", p0=nucleus), spin)
            self.linewidth_spins[nucleus] = spin
            # 0.2.199-patch29fx(user): Alignment tolerance and line width are set at the same place.
            # Default Poky kr: 1H +/-0.02, 15N/13C +/-0.2 ppm.
            tol_default = float(
                DEFAULTS["alignment_tolerance_ppm"].get(nucleus, 0.2)
            )
            tol_spin = QDoubleSpinBox()
            tol_spin.setRange(0.001, 2.0)
            tol_spin.setDecimals(3)
            tol_spin.setValue(
                float(
                    (settings.get("alignment_tolerance_ppm") or {})
                    .get(nucleus, tol_default)
                )
            )
            tol_spin.setToolTip(
                tr(
                    "Peak alignment / reference matching tolerance (ppm). The Poky kr default is "
                    "0.02 for 1H and 0.2 for 15N/13C; smaller is "
                    "stricter",
                )
            )
            form.addRow(tr("{p0} alignment tolerance (ppm)", p0=nucleus), tol_spin)
            self.tolerance_spins[nucleus] = tol_spin
        # 0.2.199-patch29hq(user):SMILE Number of threads (default 2; upper limit = number of
        # machine cores - 2, <= 3 cores can only be 1).
        from backend.config import smile_thread_limit
        sm_limit = smile_thread_limit()
        self.smile_thread_spin = QSpinBox()
        self.smile_thread_spin.setRange(1, max(1, sm_limit))
        default_threads = int((settings.get("smile") or {}).get("nthread", 2) or 0)
        if default_threads <= 0:
            default_threads = 2
        self.smile_thread_spin.setValue(max(1, min(default_threads, sm_limit)))
        self.smile_thread_spin.setToolTip(
            tr(
                "SMILE Number of reconstruction threads (default 2); upper limit {p0} (machine "
                "cores minus 2, capped at 1 when the core count is 3 or less);too high may trip "
                "the high-load shutdown, so keeping it at 2 is "
                "recommended",
                p0=sm_limit,
            )
        )
        form.addRow(tr("SMILE Number of threads"), self.smile_thread_spin)
        layout.addLayout(form)
        pipeline = settings.get("pipeline") or {}
        self.simple_mode_check = QCheckBox(
            tr("Simple mode (just press the previous step product file to determine the status)")
        )
        self.simple_mode_check.setToolTip(
            tr(
                "When on, the Pipeline only checks whether the output files exist "
                "(.fid/.ft2/.ft3/.list/report); it no longer compares input or script "
                "fingerprints, and it does not show "
                "\"stale\"",
            )
        )
        self.simple_mode_check.setChecked(
            bool(pipeline.get("simple_mode", False))
        )
        # 0.2.199-patch29gb(user):AppImage hides "simple mode" when packaging and running.
        self.simple_mode_check.setVisible(not is_appimage())
        layout.addWidget(self.simple_mode_check)
        if is_appimage():
            dest = str(
                Path.home() / ".config" / "NMRForge" / SETTINGS_FILENAME
            )
        else:
            dest = "nmrforge_data/config/" + SETTINGS_FILENAME
        hint = QLabel(
                tr(
                "save to {p0}, it will take effect after restarting; the default value will be "
                "displayed when not "
                "configured",
                p0=dest,
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_data_root(self) -> None:
        """Select data directory (0.2.199-patch29gg)."""
        start = self.data_root_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, tr("Select the data directory"), start
        )
        if path:
            self.data_root_edit.setText(path)

    def _on_accept(self) -> None:
        from gui.settings import save_settings

        settings = {
            "language": str(self.language_combo.currentData() or "auto"),
            "nmrpipe_path": self.nmrpipe_edit.text().strip(),
            "data_root": self.data_root_edit.text().strip(),
            "linewidth_hz": {
                nucleus: spin.value()
                for nucleus, spin in self.linewidth_spins.items()
            },
            "alignment_tolerance_ppm": {
                nucleus: spin.value()
                for nucleus, spin in self.tolerance_spins.items()
            },
            "pipeline": {
                "simple_mode": self.simple_mode_check.isChecked(),
            },
            "smile": {
                "nthread": self.smile_thread_spin.value(),
            },
        }
        save_settings(settings)
        self.accept()

