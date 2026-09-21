"""Task/Log Vertical panel: task log/error display between pipeline and spectrum viewer."""

from __future__ import annotations

from datetime import datetime

from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.theme import TEXT_MUTED


def _data_is_trashed(manager: object, exp_id: str, data_id: str) -> bool:
    """Whether the data has been deleted/Already in the recycle bin (even if it does not exist), is
    used to stop writing its log. 0.2.199-patch29hz: The judgment logic is shared with the
    gui/per_data_records.data_is_trashed to avoid the drift of the two sets of judgments in the
    log and interface records."""
    from gui.per_data_records import data_is_trashed

    return data_is_trashed(manager, exp_id, data_id)


class LogPanel(QWidget):
    """Log panel: isolate by scope (single data/data group/experiment/overall situation) + append +
    clear. User requirements: logs of individual data are independent, click on which data to
    display which log; members of the data group share the same data group log. When the change
    is selected, the main window calls set_scope to switch the current display and append
    targets."""

    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 0.2.199-patch29hz-Repair 2: Partition card + unified title style.
        self.setObjectName("PanelCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("Task / Log")
        title.setObjectName("PanelTitle")
        header.addWidget(title)
        self.scope_label = QLabel("")
        self.scope_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
        )
        header.addWidget(self.scope_label)
        header.addStretch(1)
        self.clear_button = QPushButton(tr("Clear"))
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.clear_button)
        self.stop_button = QPushButton(tr("Stop task"))
        self.stop_button.setToolTip(
                tr(
                "Terminate the processing task that is running (including SMILE/nmrPipe child "
                "processes, leaving no "
                "residue)",
            )
        )
        self.stop_button.clicked.connect(self.stop_requested.emit)
        header.addWidget(self.stop_button)
        layout.addLayout(header)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        self.text.setMaximumBlockCount(2000)
        layout.addWidget(self.text, 1)
        self._buffers: dict[str, list[str]] = {"global": []}
        self._current_scope = "global"
        self._manager = None

    def set_manager(self, manager) -> None:
        """Bind the current ProjectManager (rebound by the main window when switching projects)."""
        self._manager = manager

    def _record_path(self, key: str):
        """Single data/data group scope -> report/log.txt."""
        manager = getattr(self, "_manager", None)
        if manager is None or getattr(manager, "project", None) is None:
            return None
        new_path = None
        try:

            from gui.per_data_records import data_log_path, group_log_path

            if key.startswith("data:"):
                _kind, exp_id, data_id = key.split(":", 2)
                # 0.2.199-patch29gp:Deleted/Data that has been put into the recycle bin is no longer
                # persisted log, to avoid rebuilding data_base/report/log.txt which may lead to
                # mistaken restoration during restart.
                if _data_is_trashed(manager, exp_id, data_id):
                    return None
                new_path = data_log_path(manager, exp_id, data_id)
            elif key.startswith("group:"):
                _kind, exp_id, group_id = key.split(":", 2)
                new_path = group_log_path(manager, exp_id, group_id)
        except Exception:  # noqa: BLE001 - Path failure is not persisted.
            return None
        return new_path

    def _persist_line(self, key: str, line: str) -> None:
        """Single data scope log is mirrored to d_xxx/log.txt(0.2.199-patch29ga)."""
        path = self._record_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                path.write_text(
                    "# NMRForge data log (report folder)\n", encoding="utf-8"
                )
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _seed_persisted(self, key: str) -> None:
        """Load the data history log when switching to single data scope (this session will not be
        repeated if it already exists)."""
        if self._buffers.get(key):
            return
        path = self._record_path(key)
        if path is None or not path.is_file():
            return
        try:
            lines = []
            for raw in path.read_text(encoding="utf-8").splitlines():
                text = raw.strip()
                if text and not text.startswith("#"):
                    lines.append(text)
            if lines:
                self._buffers[key] = lines
        except OSError:
            pass

    def _reset_persisted(self, key: str) -> None:
        """Clear d_xxx/log.txt synchronously when clearing a single data scope."""
        path = self._record_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "# NMRForge data log (report folder)\n", encoding="utf-8"
            )
        except OSError:
            pass

    @staticmethod
    def scope_key(
        kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> str:
        """Select the context -> log scope key. - Data group: the same group log
        (group:{exp}:{group}); - Single data: each data is independent (data:{exp}:{data}); -
        Experiment: experimental log (exp:{exp}); - Others (project/workspace/Not selected):
        global."""
        if kind == "group" and group_id:
            return f"group:{exp_id}:{group_id}"
        if kind == "data" and data_id:
            return f"data:{exp_id}:{data_id}"
        if kind == "experiment" and exp_id:
            return f"exp:{exp_id}"
        return "global"

    def set_scope(
        self, kind: str, exp_id: str, data_id: str = "", group_id: str = ""
    ) -> None:
        """Switch the current scope: display the existing log of this scope, and subsequent append
        will fall here."""
        self._current_scope = self.scope_key(kind, exp_id, data_id, group_id)
        self._buffers.setdefault(self._current_scope, [])
        self.scope_label.setText(
            {
                "global": tr("Overview"),
                f"exp:{exp_id}": tr("experiment {p0}", p0=exp_id),
            }.get(self._current_scope, self._current_scope)
        )
        # 0.2.199-patch29ga:Single data scope loading d_xxx/log.txt History.
        self._seed_persisted(self._current_scope)
        self._reload_text()

    def current_scope(self) -> str:
        return self._current_scope

    def append(self, message: str, scope: str | None = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        key = scope or self._current_scope
        self._buffers.setdefault(key, []).append(line)
        # 0.2.199-patch29ga:data scope is mirrored to d_xxx/log.txt.
        self._persist_line(key, line)
        if key == self._current_scope:
            self._append_line(line)

    def clear(self) -> None:
        self._buffers[self._current_scope] = []
        # 0.2.199-patch29ga: Single data scope is cleared synchronously d_xxx/log.txt.
        self._reset_persisted(self._current_scope)
        self._reload_text()

    def _append_line(self, line: str) -> None:
        self.text.appendPlainText(line)
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _reload_text(self) -> None:
        self.text.clear()
        for line in self._buffers.get(self._current_scope, []):
            self.text.appendPlainText(line)
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

