"""Middle context panel: Switch with the tree selection level on the left. - Workspace selected ->
New project/Open project/Recent projects (embedded welcome page); - Project selected -> New
experiment type (embedded form); - Experiment type selected -> Import sample data (embedded
form); - Data / subdirectory selected -> Pipeline processing step (generate FID -> Peak
selection; New/The import form is embedded directly in the middle, independent window does not
pop up."""

from __future__ import annotations

from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.dashboards import ExperimentDashboard, ProjectDashboard
from gui.group_panel import GroupBatchPanel
from gui.pipeline_panel import PipelinePanel
from gui.welcome_page import WelcomePage
from qtcompat import Signal
from ui_support.i18n import tr


class CenterPanel(QWidget):
    """Middle panel container: switch pages according to the selected level."""

    log_message = Signal(str)
    log_scoped = Signal(str, str)  # (message, scope):Forward pipeline scope log (0.2.199-patch29d).
    memory_guard_requested = Signal(str)  # 0.2.112:Forward SMILE Out of memory.
    manual_open_requested = Signal(str)
    import_data_requested = Signal(str)  # exp_id(Compatible with: open import form).
    import_options_requested = Signal(str, str, str, bool)  # (exp_id, name, source, copy)
    data_rename_requested = Signal(str, str, str)  # (exp_id, data_id, new_name)
    batch_import_requested = Signal(str, list, bool)  # (exp_id, folders, group)
    # Segmented collection container directory).
    segmented_import_requested = Signal(str, str)
    create_experiment_requested = Signal(str)  # Experiment type title.
    edit_notes_requested = Signal(str, str, str)  # (kind, exp_id, data_id)
    group_run_requested = Signal(str, str, list, str, dict)
    # (exp_id, group_id, steps, reference_data_id)
    new_project_requested = Signal(str)  # Project name.
    open_project_requested = Signal(str)  # Project path.

    def __init__(
        self,
        manager=None,
        controller=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # 0.2.199-patch29hz - Modification 2: The central area also uses the card appearance (with
        # left/right/ log column separation is consistent).
        self.setObjectName("PanelCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._manager = manager

        self.welcome_page = WelcomePage()
        self.welcome_page.new_project_requested.connect(self.new_project_requested.emit)
        self.welcome_page.open_project_requested.connect(self.open_project_requested.emit)

        self.pipeline = PipelinePanel(manager, controller)
        self.pipeline.log_message.connect(self.log_message.emit)
        self.pipeline.log_scoped.connect(self.log_scoped.emit)
        self.pipeline.memory_guard_requested.connect(
            self.memory_guard_requested.emit
        )
        self.pipeline.manual_open_requested.connect(self.manual_open_requested.emit)

        self.project_page = ProjectDashboard()
        self.project_page.create_experiment_requested.connect(
            self.create_experiment_requested.emit
        )

        self.experiment_page = ExperimentDashboard()
        self.experiment_page.data_rename_requested.connect(
            self.data_rename_requested.emit
        )
        self.experiment_page.import_options_requested.connect(
            self.import_options_requested.emit
        )
        self.experiment_page.batch_import_requested.connect(
            self.batch_import_requested.emit
        )
        self.experiment_page.segmented_import_requested.connect(
            self.segmented_import_requested.emit
        )

        self.group_page = GroupBatchPanel()
        self.group_page.log_message.connect(self.log_message.emit)
        self.group_page.run_group_batch_requested.connect(
            self.group_run_requested.emit
        )
        self.group_page.summary_requested.connect(self._on_group_summary)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome_page)  # index 0: Workspace
        self.stack.addWidget(self.project_page)  # index 1: Project
        self.stack.addWidget(self.experiment_page)  # index 2: Experiment
        self.stack.addWidget(self.pipeline)  # index 3: Data / folder
        self.stack.addWidget(self.group_page)  # index 4: Data group.

        # Top annotation bar: project/experiment type/sample data three-level annotation display +
        # post-editing entrance.
        self.notes_header = QHBoxLayout()
        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        self.notes_label.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #3c3c3c; "
            "color: #ffffff; padding: 4px 8px;"
        )
        self.notes_header.addWidget(self.notes_label, 1)
        # 0.2.199-patch29gp: Data group comments are displayed in one column for each data, With
        # landscape/Vertical scroll bar prevents it from being too long or too wide.
        self.group_notes_scroll = QScrollArea()
        self.group_notes_scroll.setWidgetResizable(True)
        self.group_notes_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.group_notes_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.group_notes_scroll.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #3c3c3c; color: #ffffff;"
        )
        self.group_notes_scroll.setVisible(False)
        self.notes_header.addWidget(self.group_notes_scroll, 1)
        self.edit_notes_button = QPushButton(tr("Editor's note"))
        self.edit_notes_button.setToolTip(
            tr("Add or edit the note for the current project, experiment type or sample data")
        )
        self.edit_notes_button.clicked.connect(self._on_edit_notes)
        self.notes_header.addWidget(self.edit_notes_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.notes_header)
        layout.addWidget(self.stack)

    # ------------------------------------------------------------------
    def set_selection(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """Press the tree selection level to switch to the middle page and refresh the top comment
        bar."""
        self._update_notes(kind, exp_id, data_id, group_id)
        if kind == "workspace":
            self.stack.setCurrentIndex(0)
            self.welcome_page.refresh()
        elif kind == "project":
            self.stack.setCurrentIndex(1)
            if self._manager is not None and self._manager.project is not None:
                self.project_page.set_context(self._manager)
        elif kind == "experiment":
            self.stack.setCurrentIndex(2)
            if self._manager is not None and self._manager.project is not None:
                exp = self._manager.project.experiment(exp_id)
                label = exp.title if exp is not None else exp_id
                self.experiment_page.set_context(self._manager, exp_id, label)
        elif kind == "group":
            self.stack.setCurrentIndex(4)
            self.group_page.set_context(self._manager, exp_id, group_id)
        else:  # data / folder / Others: Show Pipeline.
            self.stack.setCurrentIndex(3)
            self.pipeline.set_selection(kind, exp_id, data_id)

    # ------------------------------------------------------------------
    def set_manager(self, manager) -> None:
        """Binding/renew ProjectManager (called by the main window when switching projects)."""
        self._manager = manager

    def update_notes(self, *args, **kwargs) -> None:
        """Refresh the comments page (public packaging: _update_notes)."""
        self._update_notes(*args, **kwargs)

    def _update_notes(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """Display the project/experiment type/sample data/Data group annotation according to the
        selected level (the top of the middle area)."""
        from gui.notes import data_note, experiment_note, sample_note

        self._notes_kind = kind if kind in (
            "project", "experiment", "data", "folder", "group"
        ) else ""
        self._notes_exp_id = exp_id
        self._notes_data_id = data_id if kind in ("data", "folder") else ""
        self._notes_group_id = group_id if kind == "group" else ""
        show = bool(self._notes_kind)
        # Group comments use a scroll area with one column per data; other levels use a single
        # label, and the edit button is only available for non-groups 0.2.199-patch29gw: Group
        # comments are displayed on the data group page (placed above "Processing by reference
        # data") and no longer occupy the top comment bar.
        self.notes_label.setVisible(show and kind != "group")
        self.group_notes_scroll.setVisible(False)
        self.edit_notes_button.setVisible(show and kind != "group")
        if not show or kind == "group":
            return
        project = self._manager.project if self._manager is not None else None
        if project is None:
            return
        text = ""
        if kind == "project":
            text = sample_note(project)
        elif kind == "experiment":
            text = experiment_note(project, exp_id)
        elif kind in ("data", "folder"):
            text = data_note(project, exp_id, data_id)
        self.notes_label.setText(
            tr("Note:\n{p0}", p0=text) if text else tr("Note: (not filled in)")
        )

    def _set_group_notes(self, project, exp_id: str, group_id: str) -> None:
        """Data group annotation: Annotation fields are rows (field names in the first column), and
        each data is a column."""
        from gui.notes import DATA_FIELDS, data_note_fields

        exp = project.experiment(exp_id) if project is not None else None
        group = None
        if exp is not None:
            group = next(
                (g for g in (getattr(exp, "groups", None) or []) if g.id == group_id),
                None,
            )
        member_ids = (group.data_ids or []) if group is not None else []
        cols_data: list[tuple[str, str, dict]] = []
        for data_id in member_ids:
            d = (
                next((x for x in exp.data if x.id == data_id), None)
                if exp is not None
                else None
            )
            label_text = (d.title or f"Data {data_id}") if d else f"Data {data_id}"
            cols_data.append(
                (
                    data_id,
                    label_text,
                    data_note_fields(project, exp_id, data_id),
                )
            )
        # Field rows: first follow the standard DATA_FIELDS order, and then fill in the additional
        # fields that appear in each data.
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
        # Header row: first cell "data", followed by one column for each data.
        head0 = QLabel(tr("data"))
        head0.setStyleSheet(
            "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
        )
        grid.addWidget(head0, 0, 0)
        for ci, (data_id, label_text, _f) in enumerate(cols_data, start=1):
            head = QLabel(f"◆ {label_text}\n({data_id})")
            head.setWordWrap(True)
            head.setStyleSheet(
                "font-weight: bold; color: #f0f0f0; border: none; background: transparent;"
            )
            grid.addWidget(head, 0, ci)
        # Field row.
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
        self.group_notes_scroll.setWidget(container)

    def _on_group_summary(self, summary: dict) -> None:
        """Summary of group batch processing completion: log output + panel clearing progress."""
        info = str(summary.get("info", ""))
        if info:
            self.log_message.emit(info)
        for item in summary.get("items") or []:
            data_id = item.get("data_id", "")
            if item.get("skipped"):
                self.log_message.emit(
                    tr(
                        "  Skipped {p0}: "
                        "{p1}",
                        p0=data_id,
                        p1=item.get("error", ""),
                    )
                )
            elif item.get("failed"):
                self.log_message.emit(
                    tr(
                        "  Failed {p0}: "
                        "{p1}",
                        p0=data_id,
                        p1=item.get("error", ""),
                    )
                )
            elif item.get("cancelled"):
                self.log_message.emit(
                    tr(
                        "  Cancelled {p0}: "
                        "{p1}",
                        p0=data_id,
                        p1=item.get("error", ""),
                    )
                )
            elif item.get("ok"):
                self.log_message.emit(
                    tr(
                        "  Done {p0}: "
                        "{p1}",
                        p0=data_id,
                        p1=item.get("message", ""),
                    )
                )
            else:
                self.log_message.emit(
                    tr(
                        "  Failed {p0}: "
                        "{p1}",
                        p0=data_id,
                        p1=item.get("error", ""),
                    )
                )
        self.group_page.set_progress("")

    def _on_edit_notes(self) -> None:
        """Click "Edit Comment": Send an edit request (the main window opens the comment dialog
        box)."""
        self.edit_notes_requested.emit(
            self._notes_kind, self._notes_exp_id, self._notes_data_id
        )

    def refresh(self) -> None:
        self.welcome_page.refresh()
        self.pipeline.refresh()
        self.project_page.refresh()
        self.experiment_page.refresh()
        if self._exp_group_context():
            self.group_page.refresh()

    def _exp_group_context(self) -> bool:
        """Whether you are currently staying on the data group page (for refresh)."""
        return bool(
            self.stack.currentWidget() is self.group_page
            and self.group_page.current_group_id
        )

    def run_step(self, step_id: str, data_id: str | None = None) -> None:
        self.pipeline.run_step(step_id, data_id=data_id)

    def current_experiment_id(self) -> str:
        return self.pipeline.current_experiment_id()
