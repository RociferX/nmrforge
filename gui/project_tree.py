"""Project management tree on the left: Project -> Experiment -> Sample data. Hierarchy (contract
v1.2 §8.5): - Project node: right-click to delete the project (strong confirmation); -
Experiment node: can be created blank, Right-click to import sample data/Rename/delete; - Sample
data node: sample data entries under the experiment (can be in multiple groups), right-click to
generate FID/generate spectrum/open directory/delete. Backend is used directly after landing in
the DataEntry level entry.data;The current compatibility stage (schema 1.1) uses the experiment
itself as a single sample data node."""

from __future__ import annotations

from pathlib import Path

from qtcompat.QtCore import QEvent, QPoint, Qt
from qtcompat.QtGui import QKeyEvent
from qtcompat.QtWidgets import (
    QAbstractItemDelegate,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import ProjectManager
from core.project.artifacts import (
    find_primary_spectrum,
    is_projection_spectrum_file,
)
from gui.pipeline_state import ALL_STEP_RUN_REFS
from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_SECONDARY

# The real directory under the data node (contract v1.3
# §9:raw/process/spectra/peaks/figures/report).
DATA_SUBFOLDERS = ("raw", "process", "spectra", "peaks", "figures", "report")


def _terminal_argv(directory: str) -> list[str] | None:
    """Construct the command "open directory in the terminal" (csh is preferred); if there is no
    available terminal, None is returned. Linux/macOS: Prioritize gnome-
    terminal/konsole/x-terminal-emulator/xterm to start csh (automatically read the NMRPipe
    environment of ~/.cshrc); Windows gives priority to the csh found (such as Cygwin/Git Bash),
    otherwise it falls back to cmd."""
    import shlex
    import shutil
    import sys

    if sys.platform.startswith("win"):
        csh = shutil.which("csh")
        if csh:
            return [csh, "-c", f"cd {shlex.quote(directory)} && exec csh"]
        cmd = shutil.which("cmd") or "cmd.exe"
        return [cmd, "/K", f'cd /d "{directory}"']

    csh = shutil.which("csh")
    terminal = None
    for name in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        candidate = shutil.which(name)
        if candidate:
            terminal = candidate
            break
    if terminal is None:
        return None
    base = terminal
    if "gnome-terminal" in base:
        args = [base, f"--working-directory={directory}"]
        if csh:
            args += ["--", "csh"]
        return args
    if "konsole" in base:
        args = [base, f"--workdir={directory}"]
        if csh:
            args += ["-e", "csh"]
        return args
    shell = "csh" if csh else "bash"
    quoted = shlex.quote(directory)
    return [base, "-e", shell, "-c", f"cd {quoted} && exec {shell}"]


def open_in_terminal(path: str) -> bool:
    """Open directory in the terminal (csh is preferred) and return whether it started
    successfully."""
    from qtcompat.QtCore import QProcess

    argv = _terminal_argv(str(path))
    if not argv:
        return False
    return QProcess.startDetached(argv[0], argv[1:])


class _InlineRenameEditor(QWidget):
    """Lightly rename the input box: displayed in the right-click menu position (the menu changes
    to the input box in place), Enter to submit/Esc cancel."""

    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        # Embedded sub-component (0.2.163-patch4): no longer use Popup independent window to avoid
        # Wayland xdg_popup crawl/Synthesizer positioning issue; position is controlled by Qt
        # relative coordinates.
        super().__init__(parent)
        self._finished = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._edit = QLineEdit()
        self._edit.setMinimumWidth(180)
        self._edit.setStyleSheet(
            "QLineEdit { border: 1px solid #2c3e50; border-radius: 3px; "
            "padding: 2px 6px; background: white; color: #222; }"
        )
        self._edit.installEventFilter(self)
        self._edit.editingFinished.connect(self._commit)
        layout.addWidget(self._edit)
        self.setFocusProxy(self._edit)
        # Inline sub-components are initially hidden (sub-components are visible by default when the
        # parent window is displayed).
        self.hide()

    def open_at(self, point, text: str) -> None:
        """Display and focus at global coordinate point (all text is selected by default, and the
        parent window is retracted)."""
        self._finished = False
        self._edit.setText(text)
        self._edit.selectAll()
        self.adjustSize()
        # Global coordinates are converted to relative parent window coordinates (Qt controls it
        # itself and does not depend on the window system).
        parent = self.parentWidget()
        if parent is not None:
            local = parent.mapFromGlobal(point)
            rect = parent.rect()
            x = min(
                max(local.x(), 0),
                max(0, rect.right() - self.width()),
            )
            y = min(
                max(local.y(), 0),
                max(0, rect.bottom() - self.height()),
            )
            point = QPoint(x, y)
        self.move(point)
        self.show()
        self.raise_()
        self._edit.setFocus()

    def eventFilter(self, obj, event) -> bool:
        """Enter to submit / Esc to cancel (other events are returned to default processing)."""
        if obj is self._edit and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Return:
                self._commit()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._cancel()
                return True
        return super().eventFilter(obj, event)

    def _commit(self) -> None:
        if self._finished:
            return
        self._finished = True
        text = self._edit.text().strip()
        self.hide()
        if text:
            self.submitted.emit(text)

    def _cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.cancelled.emit()


class ProjectTreePanel(QWidget):
    """Project Management Tree;selection_changed Emitted when the context (experiment) changes."""

    selection_changed = Signal(str, str, str, str)  # (kind, exp_id, data_id, group_id)
    # Double-click experiment:Request to open/Focus on this experiment.
    open_requested = Signal(str)
    open_project_requested = Signal(str)  # Double-click an unopened item: Request to open.
    data_rename_requested = Signal(str, str, str)  # (exp_id, data_id, new_name):Rename data.
    rename_project_requested = Signal(str)  # (new_name):Rename current project.
    project_create_submitted = Signal(str)  # Inline named commit: project name.
    experiment_create_submitted = Signal(str)  # Inline named commit:experiment title.
    # Open the directory where it is located (right-click on the sub-file folder).
    open_path_requested = Signal(str)
    open_terminal_requested = Signal(str)  # Open in terminal (right click on sub-file folder).
    # Double-click spectrum file: displayed directly on the right.
    open_spectrum_requested = Signal(str)
    delete_project_requested = Signal()  # Project Right click: delete item.
    # Right-click on a blank space: Create a new blank experiment.
    create_experiment_requested = Signal()
    import_data_requested = Signal(str)  # Experiment right click: Import sample data (exp_id).
    data_action_requested = Signal(str, str)  # (action, data_id):GenerateFID/Spectrum/delete.
    group_add_data_requested = Signal(str, str, list)  # (exp_id, group_id, data_ids)
    group_remove_data_requested = Signal(str, str, str)  # (exp_id, group_id, data_id)
    group_rename_requested = Signal(str, str, str)  # (exp_id, group_id, new_title)
    group_delete_requested = Signal(str, str)  # (exp_id, group_id)
    group_delete_with_members_requested = Signal(str, str)
    # (exp_id, group_id): Delete the group together with the data in the group.
    rename_requested = Signal(str, str)  # (exp_id, new_title):Rename experiment.
    delete_requested = Signal(str)  # Delete Experiment(exp_id).

    def __init__(
        self,
        manager: ProjectManager | None = None,
        workspace=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager or ProjectManager()
        self.workspace = workspace
        # 0.2.199-patch5: The status of the data node that is running and processing is overwritten
        # as "Running".
        self._running: set[tuple[str, str]] = set()
        # 0.2.199-patch29hd: After the batch is completed, the data status is superimposed
        # (success/fail/jump over), and it is retained after refreshing.
        self._batch_status: dict[tuple[str, str], str] = {}

        # 0.2.199-patch29hz-Repair 2: Partition card + title bar, four main partitions can be
        # separated at a glance.
        self.setObjectName("PanelCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        panel_title = QLabel(tr("project/data"))
        panel_title.setObjectName("PanelTitle")
        header.addWidget(panel_title)
        header.addStretch(1)
        layout.addLayout(header)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("object"), tr("state")])
        self.tree.setAlternatingRowColors(True)
        # Readable column width: explicit width + minimum segment width (stretch override disabled).
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.tree.header().setMinimumSectionSize(80)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 90)
        self.tree.setMinimumWidth(180)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        # Inline naming(New project/experiment):Listen for editor submissions in the tree/Cancel.
        delegate = self.tree.itemDelegate()
        delegate.commitData.connect(self._on_editor_commit_data)
        delegate.closeEditor.connect(self._on_editor_closed)
        self._pending_item: QTreeWidgetItem | None = None
        self._pending_kind: str | None = None
        self._pending_text: str | None = None
        # Rename the input box: the right-click menu becomes the input box in place (Enter to
        # submit/Esc cancel); parent passes the top-level window, Popup gets the transientParent
        # (required in Wayland), click outside to automatically close and submit.
        self._rename_editor = _InlineRenameEditor(self)
        self._rename_editor.submitted.connect(self._on_rename_editor_submitted)
        self._rename_editor.cancelled.connect(self._clear_rename_state)
        self._rename_target: tuple | None = None
        layout.addWidget(self.tree)
        self.refresh()

    # ------------------------------------------------------------------
    # Build.
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Incrementally refresh the tree: Only update changed nodes/son directory, unchanged nodes
        retain instances."""
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            workspace_item = QTreeWidgetItem([self._workspace_name(), ""])
            workspace_item.setIcon(0, self._icon("workspace"))
            workspace_item.setToolTip(0, self._workspace_path())
            workspace_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "workspace"})
            self.tree.addTopLevelItem(workspace_item)

        current_root = self.manager.root
        project_dirs = self._workspace_projects()
        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(workspace_item.childCount()):
            item = workspace_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "project":
                existing[str(data.get("path"))] = item
        new_paths = {str(path) for path in project_dirs}
        for path in list(existing):
            if path not in new_paths:
                workspace_item.removeChild(existing[path])

        for project_dir in project_dirs:
            key = str(project_dir)
            is_current = (
                current_root is not None
                and project_dir == Path(current_root).resolve()
            )
            display_name = project_dir.name
            if is_current and self.manager.project is not None:
                display_name = self.manager.project.name or project_dir.name
            project_item = existing.get(key)
            if project_item is None:
                project_item = QTreeWidgetItem([display_name, ""])
                project_item.setIcon(0, self._icon("project"))
                project_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"kind": "project", "path": key},
                )
                workspace_item.addChild(project_item)
            project_item.setText(0, display_name)
            project_item.setText(1, tr("current") if is_current else "")
            project_item.setToolTip(
                0,
                f"{project_dir}\n"
                + (tr("Click to open project") if not is_current else tr("current project")),
            )
            if is_current and self.manager.project is not None:
                self._refresh_experiments(project_item)
                project_item.setExpanded(True)
            else:
                for index in range(project_item.childCount() - 1, -1, -1):
                    project_item.removeChild(project_item.child(index))
        workspace_item.setExpanded(True)

    def _refresh_experiments(self, project_item: QTreeWidgetItem) -> None:
        """Incrementally refresh experimental nodes (press exp_id to reuse unchanged instances)."""
        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(project_item.childCount()):
            item = project_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "experiment":
                existing[str(data.get("exp_id"))] = item
        experiments = [
            e
            for e in (self.manager.project.experiments if self.manager.project else [])
            if not getattr(e, "trashed", False)
        ]
        ids = {exp.id for exp in experiments}
        for exp_id in list(existing):
            if exp_id not in ids:
                project_item.removeChild(existing[exp_id])
        for exp in experiments:
            exp_item = existing.get(exp.id)
            if exp_item is None:
                exp_item = self._make_experiment_item(exp)
                project_item.addChild(exp_item)
            else:
                self._update_experiment_item(exp_item, exp)

    def _update_experiment_item(
        self, exp_item: QTreeWidgetItem, exp
    ) -> None:
        """Update the experimental node text and incrementally refresh the data group and ungrouped
        sample data nodes."""
        exp_item.setText(0, exp.title or exp.id)
        # 0.2.199-patch29dx(user): The experiment is consistent with the project -- The currently
        # selected experiment displays "current", otherwise it is empty.
        exp_item.setText(1, tr("current") if exp.id == self.current_experiment_id() else "")
        exp_item.setToolTip(0, tr(
            "{p0}Right click: import sample data / rename / "
            "delete",
            p0=exp.id,
        ))
        existing_data: dict[str, QTreeWidgetItem] = {}
        existing_groups: dict[str, QTreeWidgetItem] = {}
        for index in range(exp_item.childCount()):
            item = exp_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict):
                continue
            if data.get("kind") == "data":
                existing_data[str(data.get("data_id"))] = item
            elif data.get("kind") == "group":
                existing_groups[str(data.get("group_id"))] = item
        # Data group node.
        groups = self._groups_of(exp)
        group_ids = {g.id for g in groups}
        for group_id in list(existing_groups):
            if group_id not in group_ids:
                exp_item.removeChild(existing_groups[group_id])
        for group in groups:
            group_item = existing_groups.get(group.id)
            if group_item is None:
                group_item = self._make_group_item(exp, group)
                exp_item.insertChild(0, group_item)
            else:
                self._update_group_item(group_item, exp, group)
        # The sample data node is not included in the group (same level as the data group).
        data_nodes = self._ungrouped_data_of(exp)
        ids = {getattr(n, "id", exp.id) for n in data_nodes}
        for data_id in list(existing_data):
            if data_id not in ids:
                exp_item.removeChild(existing_data[data_id])
        for data_node in data_nodes:
            data_id = getattr(data_node, "id", exp.id)
            data_item = existing_data.get(data_id)
            if data_item is None:
                data_item = self._make_data_item(exp, data_node)
                exp_item.addChild(data_item)
            else:
                self._update_data_item(data_item, exp, data_node)

    def _update_group_item(
        self, group_item: QTreeWidgetItem, exp, group
    ) -> None:
        """Update data group node text/Number of members, and incrementally refresh the sample data
        nodes in the group."""
        group_id = getattr(group, "id", "")
        by_id = {getattr(d, "id", ""): d for d in self._data_of(exp)}
        members = [m for m in (getattr(group, "data_ids", None) or []) if m in by_id]
        title = getattr(group, "title", "") or f"Group {group_id}"
        group_item.setText(0, title)
        group_item.setText(1, tr("{p0} data", p0=len(members)))
        group_item.setToolTip(
            0,
            tr(
                "{p0}\nright click: add other data to this group / rename the group / remove the "
                "group tag (data kept) / delete the group (with its "
                "data)",
                p0=group_id,
            ),
        )
        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(group_item.childCount()):
            item = group_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "data":
                existing[str(data.get("data_id"))] = item
        member_set = set(members)
        for data_id in list(existing):
            if data_id not in member_set:
                group_item.removeChild(existing[data_id])
        for data_id in members:
            data_node = by_id.get(data_id)
            if data_node is None:
                continue
            data_item = existing.get(data_id)
            if data_item is None:
                data_item = self._make_data_item(exp, data_node, in_group=True)
                group_item.addChild(data_item)
            else:
                self._update_data_item(data_item, exp, data_node)

    def _update_data_item(
        self, data_item: QTreeWidgetItem, exp, data_node
    ) -> None:
        """Update sample data node text/state, and refresh the subfolder file according to the
        directory fingerprint increment."""
        data_id = getattr(data_node, "id", exp.id)
        status = self._data_status(exp, data_node)
        title = getattr(data_node, "title", "") or ""
        label = title or f"Data {data_id}"
        # The data in the group is identified by the group node; starting from 0.2.164-patch1, the
        # batch group is the data group, and there is no longer an independent mark suffix of
        # pipeline_state.
        data_item.setText(0, label)
        data_item.setText(1, status)

        existing: dict[str, QTreeWidgetItem] = {}
        for index in range(data_item.childCount()):
            item = data_item.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "folder":
                existing[str(data.get("folder"))] = item
        for sub in DATA_SUBFOLDERS:
            folder_item = existing.get(sub)
            fingerprint = self._folder_fingerprint(exp.id, data_id, sub)
            if folder_item is None:
                folder_item = QTreeWidgetItem([sub, ""])
                folder_item.setIcon(0, self._icon("folder"))
                folder_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {
                        "kind": "folder",
                        "exp_id": exp.id,
                        "data_id": data_id,
                        "folder": sub,
                        "fingerprint": fingerprint,
                    },
                )
                data_item.addChild(folder_item)
                self._populate_folder_children(folder_item, exp.id, data_id, sub)
            else:
                role = folder_item.data(0, Qt.ItemDataRole.UserRole)
                if role.get("fingerprint") != fingerprint:
                    role["fingerprint"] = fingerprint
                    for index in range(folder_item.childCount() - 1, -1, -1):
                        folder_item.removeChild(folder_item.child(index))
                    self._populate_folder_children(
                        folder_item, exp.id, data_id, sub
                    )
        for sub in list(existing):
            if sub not in DATA_SUBFOLDERS:
                data_item.removeChild(existing[sub])

    def _folder_fingerprint(
        self, exp_id: str, data_id: str, folder: str
    ) -> str:
        """Directory fingerprint: dir mtime + (name, size, mtime) list to determine whether the
        subdirectory has changed."""
        path = self._folder_path(exp_id, data_id, folder)
        if path is None or not path.is_dir():
            return ""
        try:
            parts = [str(path.stat().st_mtime_ns)]
            for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
                try:
                    cst = child.stat()
                    parts.append(
                        f"{child.name}:{cst.st_size}:{cst.st_mtime_ns}"
                    )
                except OSError:
                    continue
            return "|".join(parts)
        except OSError:
            return ""

    def _workspace_projects(self) -> list[Path]:
        """All projects in the workspace directory (including project.json)."""
        try:
            if self.workspace is not None:
                return list(self.workspace.list_projects())
            from core.workspace import WorkspaceManager

            return WorkspaceManager().list_projects()
        # Roll back to current project when workspace is unavailable.
        except Exception:  # noqa: BLE001 -
            if self.manager.root is not None:
                return [Path(self.manager.root)]
            return []

    # ------------------------------------------------------------------
    # Workspace path (contract v1.3;core/workspace will be switched to WorkspaceManager after
    # implementation).
    # ------------------------------------------------------------------
    def _workspace_path(self) -> str:
        from core.workspace import default_workspace_path

        try:
            return str(default_workspace_path())
        except Exception:  # noqa: BLE001
            if self.manager.root is not None:
                return str(self.manager.root.parent)
            return str(Path.home())

    def _workspace_name(self) -> str:
        return Path(self._workspace_path()).name or "Workspace"

    def _make_experiment_item(self, exp) -> QTreeWidgetItem:
        exp_item = QTreeWidgetItem(
            [exp.title or exp.id, tr("current") if exp.id == self.current_experiment_id() else ""]
        )
        exp_item.setIcon(0, self._icon("experiment"))
        exp_item.setToolTip(0, tr(
            "{p0}Right click: import sample data / rename / "
            "delete",
            p0=exp.id,
        ))
        exp_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "experiment", "exp_id": exp.id})
        for group in self._groups_of(exp):
            group_item = self._make_group_item(exp, group)
            exp_item.addChild(group_item)
        for data_node in self._ungrouped_data_of(exp):
            data_item = self._make_data_item(exp, data_node)
            exp_item.addChild(data_item)
        exp_item.setExpanded(False)
        return exp_item

    def _data_of(self, exp) -> list:
        """Sample data node under the experiment (soft deleted entries are not displayed)."""
        return [
            d
            for d in (getattr(exp, "data", None) or [])
            if not getattr(d, "trashed", False)
        ]

    def _groups_of(self, exp) -> list:
        """The data group node under the experiment (schema 1.4; empty returns an empty list)."""
        return list(getattr(exp, "groups", None) or [])

    @staticmethod
    def _grouped_ids(exp) -> set[str]:
        """A collection of data ids that have been grouped (may belong to multiple groups at the
        same time, all are considered within the group)."""
        grouped: set[str] = set()
        for group in getattr(exp, "groups", None) or []:
            grouped.update(getattr(group, "data_ids", None) or [])
        return grouped

    def _ungrouped_data_of(self, exp) -> list:
        """Ungrouped sample data (directly linked to the experiment, at the same level as the data
        group)."""
        grouped = self._grouped_ids(exp)
        return [d for d in self._data_of(exp) if getattr(d, "id", "") not in grouped]

    def _make_group_item(self, exp, group) -> QTreeWidgetItem:
        group_id = getattr(group, "id", "")
        title = getattr(group, "title", "") or f"Group {group_id}"
        by_id = {getattr(d, "id", ""): d for d in self._data_of(exp)}
        members = [m for m in (getattr(group, "data_ids", None) or []) if m in by_id]
        group_item = QTreeWidgetItem([title, tr("{p0} data", p0=len(members))])
        group_item.setIcon(0, self._icon("group"))
        group_item.setToolTip(
            0,
            tr(
                "{p0}\nright click: add other data to this group / rename the group / remove the "
                "group tag (data kept) / delete the group (with its "
                "data)",
                p0=group_id,
            ),
        )
        group_item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {"kind": "group", "exp_id": exp.id, "group_id": group_id},
        )
        for data_id in members:
            data_node = by_id.get(data_id)
            if data_node is None:
                continue
            data_item = self._make_data_item(exp, data_node, in_group=True)
            group_item.addChild(data_item)
        group_item.setExpanded(False)
        return group_item

    def _make_data_item(self, exp, data_node, in_group: bool = False) -> QTreeWidgetItem:
        data_id = getattr(data_node, "id", exp.id)
        source = getattr(data_node, "source", "") or getattr(exp, "source", "")
        status = self._data_status(exp, data_node)
        title = getattr(data_node, "title", "") or ""
        label = title or f"Data {data_id}"
        # The data in the group is identified by the group node; the batch group is the data group
        # and has no independent label suffix (0.2.164-patch1).
        data_item = QTreeWidgetItem([label, status])
        data_item.setIcon(0, self._icon("data"))
        tooltip = tr("{p0}source: {p1}", p0=data_id, p1=source)
        if in_group:
            tooltip += (
                tr(
                    "\nright click: remove this data from the group / open its folder / open in a "
                    "terminal / rename / delete the sample "
                    "data",
                )
            )
        else:
            tooltip += (
                tr(
                "Right click: open the directory / open in the terminal / rename / delete sample "
                "data",
            )
            )
        data_item.setToolTip(0, tooltip)
        data_item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {"kind": "data", "exp_id": exp.id, "data_id": data_id},
        )
        for sub in DATA_SUBFOLDERS:
            sub_item = QTreeWidgetItem([sub, ""])
            sub_item.setIcon(0, self._icon("folder"))
            sub_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {
                    "kind": "folder",
                    "exp_id": exp.id,
                    "data_id": data_id,
                    "folder": sub,
                    "fingerprint": self._folder_fingerprint(
                        exp.id, data_id, sub
                    ),
                },
            )
            self._populate_folder_children(sub_item, exp.id, data_id, sub)
            data_item.addChild(sub_item)
        data_item.setExpanded(False)
        return data_item

    def _populate_folder_children(
        self, folder_item: QTreeWidgetItem, exp_id: str, data_id: str, folder: str
    ) -> None:
        """Hang the file in the sub-file folder under the file folder node (you can pull it down to
        view)."""
        path = self._folder_path(exp_id, data_id, folder)
        if path is None or not path.is_dir():
            return
        for child in sorted(path.iterdir(), key=lambda p: (p.is_dir(), p.name.lower())):
            name = child.name
            file_item = QTreeWidgetItem([name, ""])
            file_item.setIcon(0, self._icon("folder" if child.is_dir() else "file"))
            file_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {
                    "kind": "file",
                    "exp_id": exp_id,
                    "data_id": data_id,
                    "folder": folder,
                    "name": name,
                },
            )
            folder_item.addChild(file_item)

    def mark_running(self, exp_id: str, data_id: str) -> None:
        """Mark that a certain data is being processed (the status on the left shows "Running")."""
        if not (exp_id and data_id):
            return
        self._running.add((exp_id, data_id))
        self.refresh()

    def clear_running(
        self, exp_id: str | None = None, data_id: str | None = None
    ) -> None:
        """Clear running flags (clear all by default)."""
        if exp_id is None and data_id is None:
            self._running.clear()
        else:
            self._running = {
                (e, d)
                for (e, d) in self._running
                if (exp_id is not None and e != exp_id)
                or (data_id is not None and d != data_id)
            }
        self.refresh()

    def set_batch_status(self, exp_id: str, data_id: str, status: str) -> None:
        """Record the status of certain data after the batch is completed (success/fail/jump over),
        and retain it after refreshing."""
        if exp_id and data_id and status:
            self._batch_status[(exp_id, data_id)] = status
            self._running.discard((exp_id, data_id))
            self.refresh()

    def clear_batch_status(self, exp_id: str = "", data_id: str = "") -> None:
        """Clear batch status overlay (clear all by default)."""
        if not exp_id and not data_id:
            self._batch_status.clear()
        else:
            self._batch_status = {
                (e, d): s
                for (e, d), s in self._batch_status.items()
                if (exp_id and e != exp_id) or (data_id and d != data_id)
            }
        self.refresh()

    def _data_last_run_failed(self, exp_id: str, data_id: str) -> bool:
        """Whether the latest processing run of this data failed. 0.2.199-patch29hz: It shares the
        same judgment with the Pipeline step status (ProjectManager.last_run_for_data +
        STEP_RUN_REFS) to avoid the drift of the two rules."""
        try:
            run = self.manager.last_run_for_data(
                exp_id, data_id, ALL_STEP_RUN_REFS
            )
        except Exception:  # noqa: BLE001 - If the judgment fails, it will be treated as not failed.
            return False
        return run is not None and str(getattr(run, "status", "")) == "failed"


    def _data_status(self, exp, data_node) -> str:
        """Infer sample data status by product file (0.2.199-patch29aa/patch29av): running ->
        selected peak -> generated spectrum -> generated FID -> imported; directly read
        data_entry registered spectrum_path/fid_path (more reliable than scanning by file name)."""
        try:
            exp_id = exp.id
            data_id = getattr(data_node, "id", exp_id)
            if (exp_id, data_id) in self._running:
                return tr("Running")
            batch_status = self._batch_status.get((exp_id, data_id))
            if batch_status:
                return batch_status
            # 0.2.199-patch29hd: The latest processing operation of single data failed -> the status
            # shows "Failed".
            if self._data_last_run_failed(exp_id, data_id):
                return tr("Failed")
            entry = self.manager.data(exp_id, data_id)
            # 0.2.199-patch29av: Peak table exists -> selected peak (priority to spectrum).
            peaks_dir = self.manager.data_dir(exp_id, data_id, "peaks")
            for suffix in (".list", ".csv"):
                if (peaks_dir / f"{exp_id}-{data_id}{suffix}").is_file():
                    return tr("Already peak picking")
            if find_primary_spectrum(self.manager, exp_id, data_id) is not None:
                return tr("Spectrum has been generated")
            fid = str(getattr(entry, "fid_path", "") or "")
            if fid and Path(fid).is_file():
                return tr("Generated FID")
            work = self.manager.data_dir(exp_id, data_id, "process")
            if work.is_dir():
                work_spectra = [
                    path
                    for extension in ("ft2", "ft3")
                    for path in work.glob(f"*.{extension}")
                    if not is_projection_spectrum_file(path.name, data_id)
                ]
                if work_spectra:
                    return tr("Spectrum has been generated")
                if list(work.glob("*.fid")) or (work / "fid").is_dir():
                    return tr("Generated FID")
        except Exception:  # noqa: BLE001 - Display conservatively when directory is missing.
            pass
        return tr("Already imported")

    @staticmethod
    def _icon(kind: str):
        """Simple Unicode icons (avoid relying on external resource files)."""
        from qtcompat.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

        glyph = {
            "workspace": "W",
            "project": "P",
            "experiment": "E",
            "data": "D",
            "group": "G",
            "folder": "▸",
            "file": "•",
        }.get(kind, "•")
        pix = QPixmap(16, 16)
        pix.fill(QColor("transparent"))
        painter = QPainter(pix)
        painter.setPen(QColor(TEXT_SECONDARY))
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        return QIcon(pix)

    # ------------------------------------------------------------------
    # Choice and context.
    # ------------------------------------------------------------------
    def current_experiment_id(self) -> str:
        """Returns the experiment id to which the currently selected node belongs (normalized to
        the experiment when selecting sample data nodes)."""
        item = self.tree.currentItem()
        return self._experiment_id_of(item) if item is not None else ""

    def _experiment_id_of(self, item: QTreeWidgetItem | None) -> str:
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("exp_id"):
                return str(data["exp_id"])
            item = item.parent()
        return ""

    def _data_id_of(self, item: QTreeWidgetItem | None) -> str:
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("data_id"):
                return str(data["data_id"])
            item = item.parent()
        return ""

    def current_data_id(self) -> str:
        """The sample data id to which the currently selected node belongs (empty string for non-
        data nodes)."""
        return self._data_id_of(self.tree.currentItem())

    def select_data(self, exp_id: str, data_id: str) -> None:
        """Select the sample data node under the experiment (expand the experiment; silent if not
        found)."""
        exp_item = self._find_experiment_item(exp_id)
        if exp_item is None:
            return
        def _find(target_item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            for index in range(target_item.childCount()):
                child = target_item.child(index)
                if self._data_id_of(child) == data_id:
                    return child
                if child.childCount():
                    found = _find(child)
                    if found is not None:
                        return found
            return None

        target = _find(exp_item)
        if target is not None:
            self.tree.expandItem(exp_item)
            parent = target.parent()
            if parent is not None and parent is not exp_item:
                parent.setExpanded(True)
            self.tree.setCurrentItem(target)

    def select_experiment(self, exp_id: str) -> None:
        """Recursively locate and select the experiment node by id (Workspace -> Project ->
        Experiment)."""
        target = self._find_experiment_item(exp_id)
        if target is None:
            return
        parent = target.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        target.setExpanded(True)
        self.tree.setCurrentItem(target)

    def _find_experiment_item(
        self, exp_id: str, item: QTreeWidgetItem | None = None
    ) -> QTreeWidgetItem | None:
        """Depth-first search for experimental nodes."""
        if item is None:
            for i in range(self.tree.topLevelItemCount()):
                found = self._find_experiment_item(exp_id, self.tree.topLevelItem(i))
                if found is not None:
                    return found
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("exp_id") == exp_id:
            return item
        for i in range(item.childCount()):
            found = self._find_experiment_item(exp_id, item.child(i))
            if found is not None:
                return found
        return None

    def _update_experiment_current_markers(self) -> None:
        """When the experimental change is selected, the second column of the experimental node
        "current/null" (0.2.199-patch29dx) is updated."""
        current = self.current_experiment_id()
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            return
        for index in range(workspace_item.childCount()):
            child = workspace_item.child(index)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "experiment":
                child.setText(1, tr("current") if str(data.get("exp_id", "")) == current else "")

    def _on_selection_changed(self) -> None:
        self._update_experiment_current_markers()
        item = self.tree.currentItem()
        if item is None:
            self.selection_changed.emit("", "", "")
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        kind = data.get("kind") if isinstance(data, dict) else ""
        group_id = (
            str(data.get("group_id", ""))
            if isinstance(data, dict)
            else ""
        )
        self.selection_changed.emit(
            kind, self._experiment_id_of(item), self._data_id_of(item), group_id
        )

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Click the project node to open it (no need to double-click)."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("kind") != "project":
            return
        path = data.get("path")
        if not path:
            return
        is_current = (
            self.manager.root is not None
            and Path(str(path)) == Path(self.manager.root).resolve()
        )
        if not is_current:
            self.open_project_requested.emit(str(path))

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        kind = data.get("kind") if isinstance(data, dict) else ""
        if kind == "project" and data.get("path"):
            path = str(data["path"])
            if self.manager.root is None or Path(path) != Path(self.manager.root).resolve():
                self.open_project_requested.emit(path)
            return
        if kind == "file":
            # Double-click the file: spectrum file will be displayed directly on the right side, and
            # other files will be opened in the directory where they are located.
            folder = data.get("folder", "")
            name = data.get("name", "")
            # 0.2.199-patch29hz: 1D final spectrum is.ft1, which is already supported by the right
            # panel. Only matching.ft2/.ft3 will cause the 1D user to double-click the spectrum to
            # "open the directory where it is located".
            if folder == "spectra" and name.lower().endswith(
                (".ft1", ".ft2", ".ft3")
            ):
                path = self._folder_path_for_item(item)
                if path is not None and path.is_file():
                    self.open_spectrum_requested.emit(str(path))
            else:
                folder_path = self._folder_path_for_item(item)
                if folder_path is not None:
                    target = (
                        folder_path.parent
                        if not folder_path.is_dir()
                        else folder_path
                    )
                    self.open_path_requested.emit(str(target))
            return
        if kind in ("data", "folder"):
            # Double click on sample data/son file folder: Open the file manager corresponding to
            # the directory, and keep the Pipeline in the middle.
            folder_path = self._folder_path_for_item(item)
            if folder_path is not None:
                self.open_path_requested.emit(str(folder_path))
            return
        exp_id = self._experiment_id_of(item)
        if exp_id:
            self.open_requested.emit(exp_id)

    def _request_group_add(self, exp_id: str, group_id: str) -> None:
        """A multi-select dialog box for ungrouped data pops up, and the selected data is added to
        the group."""
        if self.manager.project is None:
            return
        exp = self.manager.project.experiment(exp_id)
        if exp is None:
            return
        group = self.manager.group(exp_id, group_id)
        if group is None:
            return
        grouped = self._grouped_ids(exp)
        candidates = [
            d
            for d in self._data_of(exp)
            if getattr(d, "id", "") not in grouped
        ]
        if not candidates:
            from gui.dialogs import InfoDialog

            InfoDialog(self, tr(
                "No data to "
                "add",
            ), (
                tr(
                "There is no ungrouped sample data under "
                "experiment",
            )
            )).exec()
            return
        from gui.dialogs import MultiSelectDataDialog

        dialog = MultiSelectDataDialog(
            self,
            tr("Add other data to the group"),
            [
                (d.id, getattr(d, "title", "") or f"Data {d.id}")
                for d in candidates
            ],
        )
        if dialog.exec():
            self.group_add_data_requested.emit(exp_id, group_id, dialog.selected_ids())

    def _folder_path_for_item(self, item: QTreeWidgetItem) -> Path | None:
        """Parse the real directory of the data or folder node (double-click to open)."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return None
        exp_id = self._experiment_id_of(item)
        data_id = self._data_id_of(item)
        if not exp_id or not data_id:
            return None
        if data.get("kind") == "folder":
            return self._folder_path(exp_id, data_id, data.get("folder", ""))
        if data.get("kind") == "file":
            folder = self._folder_path(exp_id, data_id, data.get("folder", ""))
            if folder is not None:
                candidate = folder / str(data.get("name", ""))
                return candidate if candidate.exists() else None
        # Data node (0.2.199-patch29ge/patch29gf, user): double click/Right click "Open the
        # directory" and "Open in the terminal" both use d_xxx data base; the raw child node itself
        # can open the terminal.
        try:
            base = self.manager.data_base(exp_id, data_id)
            return base if base.is_dir() else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Right click menu.
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = self._on_context_menu_impl(QMenu(self), item, pos)
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_context_menu_impl(self, menu: QMenu, item, pos=None) -> QMenu:
        """Build a right-click menu (an independent method is convenient for testing trigger
        actions); pos is the coordinate within the viewport, used for pop-up window positioning."""
        anchor = self.tree.viewport().mapToGlobal(pos) if pos is not None else None
        if item is None:
            # Blank space: Create a new blank experiment (when the project is open).
            if self.manager.project is not None:
                menu.addAction((
                    tr(
                    "Create a new blank "
                    "experiment...",
                )
                ), self.create_experiment_requested.emit)
        else:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            kind = data.get("kind") if isinstance(data, dict) else None
            exp_id = self._experiment_id_of(item)
            data_id = self._data_id_of(item)
            if kind == "project":
                is_current = (
                    self.manager.root is not None
                    and data.get("path")
                    and Path(str(data["path"])) == Path(self.manager.root).resolve()
                )
                if not is_current and data.get("path"):
                    menu.addAction(
                        tr("Open project..."),
                        lambda p=str(data["path"]): self.open_project_requested.emit(p),
                    )
                else:
                    menu.addAction((
                        tr(
                        "Create a new blank "
                        "experiment...",
                    )
                    ), self.create_experiment_requested.emit)
                    menu.addAction(
                        tr("rename project..."),
                        lambda _checked=False: self._begin_rename("project", item, anchor),
                    )
                    menu.addSeparator()
                    menu.addAction(tr("delete project..."), self.delete_project_requested.emit)
            elif kind == "experiment" and exp_id:
                menu.addAction((
                    tr(
                    "import sample "
                    "data...",
                )
                ), lambda: self.import_data_requested.emit(exp_id))
                menu.addAction(
                    tr("rename..."),
                    lambda _checked=False: self._begin_rename("experiment", item, anchor),
                )
                menu.addSeparator()
                menu.addAction(tr("delete experiment"), lambda: self.delete_requested.emit(exp_id))
            elif kind == "group" and exp_id:
                group_id = str(data.get("group_id", ""))
                if group_id:
                    menu.addAction(
                        tr("Add other data to the group..."),
                        lambda: self._request_group_add(exp_id, group_id),
                    )
                    menu.addAction(
                        tr("rename group..."),
                        lambda _checked=False: self._begin_rename(
                            "group", item, anchor, exp_id=exp_id, group_id=group_id
                        ),
                    )
                    menu.addSeparator()
                    menu.addAction(
                        tr("delete group tag (data retained)..."),
                        lambda: self.group_delete_requested.emit(exp_id, group_id),
                    )
                    menu.addAction(
                        tr("delete group (including data)..."),
                        lambda: self.group_delete_with_members_requested.emit(
                            exp_id, group_id
                        ),
                    )
            elif kind == "data" and exp_id and data_id:
                parent_item = item.parent()
                parent_data = (
                    parent_item.data(0, Qt.ItemDataRole.UserRole)
                    if parent_item is not None
                    else None
                )
                parent_group_id = (
                    str(parent_data.get("group_id", ""))
                    if isinstance(parent_data, dict) and parent_data.get("kind") == "group"
                    else ""
                )
                if parent_group_id:
                    menu.addAction(
                        tr("Move the data out of the group"),
                        lambda: self.group_remove_data_requested.emit(
                            exp_id, parent_group_id, data_id
                        ),
                    )
                    menu.addSeparator()
                folder_path = self._folder_path_for_item(item)
                if folder_path is not None:
                    menu.addAction(
                        tr("Open the directory where it is located"),
                        lambda p=folder_path: self.open_path_requested.emit(str(p)),
                    )
                    menu.addAction(
                        tr("Open in terminal"),
                        lambda p=folder_path: self.open_terminal_requested.emit(str(p)),
                    )
                menu.addAction(
                    tr("rename..."),
                    lambda _checked=False: self._begin_rename("data", item, anchor),
                )
                menu.addSeparator()
                menu.addAction(
                    tr("delete sample data"),
                    lambda: self.data_action_requested.emit("delete", data_id),
                )
            elif kind == "folder" and exp_id and data_id:
                folder_path = self._folder_path(exp_id, data_id, data.get("folder", ""))
                if folder_path is not None:
                    menu.addAction(
                        tr("Open the directory where it is located"),
                        lambda p=folder_path: self.open_path_requested.emit(str(p)),
                    )
                    menu.addAction(
                        tr("Open in terminal"),
                        lambda p=folder_path: self.open_terminal_requested.emit(str(p)),
                    )
        return menu

    def _source_of(self, exp_id: str) -> Path | None:
        if self.manager.project is None:
            return None
        exp = self.manager.project.experiment(exp_id)
        if exp is None:
            return None
        source = getattr(exp, "source", "") or ""
        path = Path(source)
        if path.is_dir():
            return path
        return path.parent if path.exists() else None

    # ------------------------------------------------------------------
    # Inline naming (New project/experiment, no pop-up window).
    # ------------------------------------------------------------------
    def begin_create_project(self, initial: str = "unnamed") -> None:
        """New project: Name inline in the project tree (no pop-up window), press Enter to submit /
        Esc to cancel."""
        self._cancel_pending_create()
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            return
        item = QTreeWidgetItem([initial or "unnamed", ""])
        item.setIcon(0, self._icon("project"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "pending_project"})
        item.setToolTip(0, tr("Enter the project name and press Enter to create, Esc to cancel"))
        workspace_item.addChild(item)
        workspace_item.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self._pending_item = item
        self._pending_kind = "project"
        self._pending_text = initial
        self._start_editing(item)

    def begin_create_experiment(self, initial: str = "") -> None:
        """Create a new blank experiment: name it inline in the project tree (no pop-up window),
        press Enter to submit / Esc to cancel."""
        if self.manager.project is None:
            return
        project_item = self._current_project_item()
        if project_item is None:
            return
        self._cancel_pending_create()
        item = QTreeWidgetItem([initial or tr("new experiment"), ""])
        item.setIcon(0, self._icon("experiment"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "pending_experiment"})
        item.setToolTip(0, tr(
            "Enter the experiment title and press Enter to create, Esc to "
            "cancel",
        ))
        project_item.addChild(item)
        project_item.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self._pending_item = item
        self._pending_kind = "experiment"
        self._pending_text = initial
        self._start_editing(item)

    def _start_editing(self, item: QTreeWidgetItem) -> None:
        """Enter editing directly when the tree is visible (test/Off-screen scenes are driven by
        commit hooks)."""
        if self.tree.isVisible():
            self.tree.editItem(item, 0)

    def _on_editor_commit_data(self, editor) -> None:
        if self._pending_item is not None and editor is not None:
            self._pending_text = editor.text()

    def _on_editor_closed(self, _editor, hint) -> None:
        if self._pending_item is None:
            return
        if hint == QAbstractItemDelegate.EndEditHint.RevertModelCache:
            self._cancel_pending_create()
            return
        self._commit_pending_create(self._pending_text or "")

    def _commit_pending_create(self, text: str) -> None:
        """Submit inline naming (Enter/out of focus submission, the test can be adjusted directly);
        empty text is considered canceled."""
        kind = self._pending_kind
        self._remove_pending()
        text = (text or "").strip()
        if kind == "project" and text:
            self.project_create_submitted.emit(text)
        elif kind == "experiment" and text:
            self.experiment_create_submitted.emit(text)

    def _cancel_pending_create(self) -> None:
        """Cancel inline naming (remove the node to be named)."""
        self._remove_pending()

    def _remove_pending(self) -> None:
        item, self._pending_item = self._pending_item, None
        self._pending_kind = None
        self._pending_text = None
        if item is None:
            return
        parent = item.parent()
        if parent is not None and parent.indexOfChild(item) >= 0:
            parent.removeChild(item)

    def _current_project_item(self) -> QTreeWidgetItem | None:
        """The tree node corresponding to the currently opened project (returns to the first
        project node if it cannot be found)."""
        workspace_item = self.tree.topLevelItem(0)
        if workspace_item is None:
            return None
        root = self.manager.root
        for index in range(workspace_item.childCount()):
            child = workspace_item.child(index)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict) or data.get("kind") != "project":
                continue
            path = data.get("path")
            if root is None:
                return child
            try:
                if path and Path(str(path)).resolve() == Path(root).resolve():
                    return child
            except OSError:
                continue
        return workspace_item.child(0) if workspace_item.childCount() else None

    # ------------------------------------------------------------------
    # Rename: The right-click menu changes to the rename input box (Enter to submit/Esc cancel).
    # ------------------------------------------------------------------
    def begin_rename_experiment(self, exp_id: str) -> None:
        """Menu "Rename Experiment": Display the rename input box near the tree node."""
        item = self._find_experiment_item(exp_id)
        if item is None:
            return
        exp = self.manager.project.experiment(exp_id) if self.manager.project else None
        current = exp.title if exp is not None else item.text(0)
        anchor = self.tree.viewport().mapToGlobal(self.tree.visualItemRect(item).center())
        self._rename_target = ("experiment", exp_id)
        self._rename_editor.open_at(anchor, current)

    def _begin_rename(
        self,
        kind: str,
        item: QTreeWidgetItem,
        anchor,
        exp_id: str = "",
        group_id: str = "",
    ) -> None:
        """Right-click "Rename": Record the target and open the rename input box at the right-click
        position."""
        current = ""
        target: tuple | None = None
        if kind == "project":
            current = (
                self.manager.project.name
                if self.manager.project is not None
                else item.text(0)
            )
            target = ("project",)
        elif kind == "experiment":
            exp_id = self._experiment_id_of(item)
            exp = (
                self.manager.project.experiment(exp_id)
                if self.manager.project is not None
                else None
            )
            current = exp.title if exp is not None else item.text(0)
            target = ("experiment", exp_id)
        elif kind == "data":
            exp_id = self._experiment_id_of(item)
            data_id = self._data_id_of(item)
            entry = (
                self.manager.project.experiment(exp_id)
                if self.manager.project is not None
                else None
            )
            data_entry = (
                next((d for d in entry.data if d.id == data_id), None)
                if entry is not None
                else None
            )
            current = getattr(data_entry, "title", "") or ""
            target = ("data", exp_id, data_id)
        elif kind == "group":
            exp_id = exp_id or self._experiment_id_of(item)
            group_id = group_id or str(
                (
                    item.data(0, Qt.ItemDataRole.UserRole) or {}
                ).get("group_id", "")
            )
            group = (
                self.manager.group(exp_id, group_id)
                if self.manager.project is not None
                else None
            )
            current = getattr(group, "title", "") or ""
            target = ("group", exp_id, group_id)
        if target is None or anchor is None:
            return
        self._rename_target = target
        self._rename_editor.open_at(anchor, current)

    def _on_rename_editor_submitted(self, text: str) -> None:
        """Rename the input box and press Enter to submit: Send a rename signal according to the
        target."""
        target = self._rename_target
        self._rename_target = None
        if target is None:
            return
        kind = target[0]
        if kind == "project":
            self.rename_project_requested.emit(text)
        elif kind == "experiment":
            self.rename_requested.emit(target[1], text)
        elif kind == "data":
            self.data_rename_requested.emit(target[1], target[2], text)
        elif kind == "group":
            self.group_rename_requested.emit(target[1], target[2], text)

    def _clear_rename_state(self) -> None:
        """Rename Cancel(Esc/click outside): Clean up the target."""
        self._rename_target = None

    def _folder_path(self, exp_id: str, data_id: str, folder: str) -> Path | None:
        """Sample data sub-file folder real path (contract v1.3:data_dir(exp_id, data_id, key))."""
        try:
            return self.manager.data_dir(exp_id, data_id, folder)
        except Exception:  # noqa: BLE001 - Old layout/Return before landing None.
            return None
