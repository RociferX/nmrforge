"""The welcome page for starting the workspace for the first time (G2B-003 / Contract v1.3 §9.4).
Displays the workspace path, recent project list and new project entry. The workspace uses
core/workspace.WorkspaceManager (remove the compatibility layer _FallbackWorkspaceManager and
the duplicate workspace_manager factory from 0.2.164 onwards)."""

from __future__ import annotations

from qtcompat.QtCore import QEvent, Qt
from qtcompat.QtGui import QKeyEvent
from qtcompat.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_PRIMARY, TEXT_SECONDARY


class WelcomePage(QWidget):
    """First start/Welcome page when the project is not open."""

    new_project_requested = Signal(str)  # Project name.
    open_project_requested = Signal(str)  # Project path.

    def __init__(
        self, parent: QWidget | None = None, workspace=None
    ) -> None:
        super().__init__(parent)
        if workspace is None:
            from core.workspace import WorkspaceManager

            workspace = WorkspaceManager()
        self.ws = workspace
        self._name_committing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)

        title = QLabel("NMRForge")
        title.setStyleSheet(
            f"font-size: 28px; font-weight: bold; color: {TEXT_PRIMARY};"
        )
        layout.addWidget(title)

        subtitle = QLabel(
            tr(
            "Automated processing, parameter optimisation and quality control platform for Bruker "
            "2D/3D "
            "NMR",
        ))
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self.workspace_label = QLabel("")
        self.workspace_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.workspace_label)
        layout.addSpacing(10)

        recent_title = QLabel(tr("recent project"))
        recent_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(recent_title)

        self.recent_list = QListWidget()
        self.recent_list.setMinimumHeight(140)
        self.recent_list.itemClicked.connect(self._on_recent_clicked)
        layout.addWidget(self.recent_list, 1)

        actions = QHBoxLayout()
        self.new_button = QPushButton(tr("New project..."))
        self.new_button.clicked.connect(self._on_new_clicked)
        actions.addWidget(self.new_button)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("Enter project name"))
        self._name_edit.setMaximumWidth(240)
        self._name_edit.setVisible(False)
        self._name_edit.installEventFilter(self)
        self._name_edit.editingFinished.connect(self._commit_name)
        actions.addWidget(self._name_edit)
        self._name_ok_button = QPushButton(tr("Sure"))
        self._name_ok_button.setVisible(False)
        self._name_ok_button.clicked.connect(self._commit_name)
        actions.addWidget(self._name_ok_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        path = self.ws.ensure()
        self.workspace_label.setText(tr("workspace: {p0}", p0=path))
        self.workspace_label.setToolTip(str(path))
        self.recent_list.clear()
        for project_dir in self.ws.list_projects():
            item = QListWidgetItem(project_dir.name)
            item.setData(0x0100, str(project_dir))  # Qt.UserRole
            item.setToolTip(str(project_dir))
            self.recent_list.addItem(item)
        if self.recent_list.count() == 0:
            self.recent_list.addItem(
                tr("(There is no project in the workspace yet, click \"New Project\" to start)"))

    def refresh(self) -> None:
        self._refresh()

    def workspace_path(self) -> str:
        return str(self.ws.ensure())

    # ------------------------------------------------------------------
    def _on_recent_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(0x0100)
        if path:
            self.open_project_requested.emit(str(path))

    def _on_new_clicked(self) -> None:
        self.begin_inline_name()

    def begin_inline_name(self, initial: str = "") -> None:
        """Inline naming within the page (no pop-up window): Display the name input line + OK
        button and focus, press Enter to submit / Esc to cancel."""
        self._name_edit.setText(initial)
        self._name_edit.selectAll()
        self._name_edit.setVisible(True)
        self._name_ok_button.setVisible(True)
        self._name_edit.setFocus()

    def _commit_name(self) -> None:
        if self._name_committing:
            return
        self._name_committing = True
        try:
            name = self._name_edit.text().strip()
            self._name_edit.setVisible(False)
            self._name_ok_button.setVisible(False)
            if name:
                self.new_project_requested.emit(name)
        finally:
            self._name_committing = False

    def _cancel_name(self) -> None:
        self._name_edit.setVisible(False)
        self._name_ok_button.setVisible(False)

    def eventFilter(self, obj, event) -> bool:
        """Esc cancels inline naming (other events are returned to default processing)."""
        if (
            obj is self._name_edit
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._cancel_name()
            return True
        return super().eventFilter(obj, event)

    def recent_paths(self) -> list[str]:
        return [
            str(item.data(0x0100))
            for item in self.recent_list
            if item.data(0x0100)
        ]
