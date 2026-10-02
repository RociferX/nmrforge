"""Slider companions: typed drafts commit only on Enter, steps remain immediate."""

from qtcompat.QtCore import QEvent, Qt
from qtcompat.QtWidgets import QDoubleSpinBox, QSpinBox

from ui_support.i18n import tr


class _EnterCommitMixin:
    """Keep Qt's numeric validation without committing partially typed numbers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._has_draft = False
        self.setKeyboardTracking(False)
        self.setToolTip(
            tr(
                "Type a value and press Enter to apply; leaving the field cancels the edit. "
                "Arrow buttons apply immediately."
            )
        )
        self.lineEdit().textEdited.connect(self._mark_draft)
        self.lineEdit().installEventFilter(self)

    def _mark_draft(self, _text):
        self._has_draft = True

    def _discard_draft(self):
        if self._has_draft:
            self._has_draft = False
            self.lineEdit().setText(
                self.prefix() + self.textFromValue(self.value()) + self.suffix()
            )

    def setValue(self, value):  # noqa: N802 - Qt API
        self._has_draft = False
        super().setValue(value)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if watched is self.lineEdit():
            if event.type() == QEvent.Type.FocusOut:
                # QAbstractSpinBox also interprets text on focus loss. Restore it
                # before Qt sees that event, not after valueChanged has escaped.
                self._discard_draft()
            elif event.type() == QEvent.Type.KeyPress:
                if self._handle_confirmation_key(event):
                    return True
        return super().eventFilter(watched, event)

    def _handle_confirmation_key(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._has_draft = False
            self.interpretText()
            self.selectAll()
            self.editingFinished.emit()
            return True
        if event.key() == Qt.Key.Key_Escape:
            self._discard_draft()
            self.selectAll()
            return True
        return False

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        if self._handle_confirmation_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 - Qt API
        self._discard_draft()
        super().focusOutEvent(event)

    def stepBy(self, steps):  # noqa: N802 - Qt API
        # Arrow buttons/keys operate on the confirmed value, not a pending draft.
        self._discard_draft()
        super().stepBy(steps)


class CommitSpinBox(_EnterCommitMixin, QSpinBox):
    """Integer input with explicit Enter confirmation."""


class CommitDoubleSpinBox(_EnterCommitMixin, QDoubleSpinBox):
    """Decimal input with explicit Enter confirmation."""
