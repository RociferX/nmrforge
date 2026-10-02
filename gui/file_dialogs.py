"""Shared file dialog helpers for GUI workflows."""

from __future__ import annotations

from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import QFileDialog, QWidget


def choose_directory(parent: QWidget | None, title: str, start: str = "") -> str:
    """Show a parented, Qt-owned directory picker that stays discoverable.

    Windows native directory dialogs can slip behind the application while
    retaining their modal lock. The non-native dialog remains a normal Qt
    window, parented to the caller's top-level window and kept above it.
    """
    owner = parent.window() if parent is not None else None
    dialog = QFileDialog(owner, title, start)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return ""
    selected = dialog.selectedFiles()
    return selected[0] if selected else ""
