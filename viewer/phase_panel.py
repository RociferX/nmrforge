"""Interactive phase correction panel (P0/P1 slider, 0.2.87). View the one-dimensional spectrum
like nmrDraw and drag P0/P1 to see the phase with the naked eye -- only display, no data
changes, real spectrum is also available; values can be copied with one click for writing back
manual phase parameter or script PS lines."""

from __future__ import annotations

from qtcompat.QtCore import Qt
from qtcompat.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from qtcompat import Signal
from ui_support.i18n import tr


class PhasePanel(QWidget):
    """P0/P1 phase slider; ``phase_changed(final)`` emitted when adjusting (final=False for real-
    time, True for release)."""

    phase_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._p0 = 0.0
        self._p1 = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        # 0.2.147:P0/P1 One row side by side, the gap is obvious.
        row = QHBoxLayout()
        row.setSpacing(12)

        # 0.2.148:P0/P1 The value is displayed directly after the title and can be entered, and is
        # synchronized with the slider in both directions.
        self.p0_label = QDoubleSpinBox()
        self.p0_label.setPrefix("P0: ")
        self.p0_label.setSuffix("°")
        self.p0_label.setRange(-180.0, 180.0)
        self.p0_label.setDecimals(0)
        self.p0_label.setValue(0.0)
        self.p0_label.setFixedWidth(110)
        self.p0_label.valueChanged.connect(self._on_p0_spin_changed)
        self.p0_slider = QSlider(Qt.Orientation.Horizontal)
        self.p0_slider.setRange(-180, 180)
        self.p0_slider.setValue(0)
        self.p0_slider.valueChanged.connect(self._on_p0_changed)
        self.p0_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        row.addWidget(self.p0_label)
        row.addWidget(self.p0_slider, 1)

        self.p1_label = QDoubleSpinBox()
        self.p1_label.setPrefix("P1: ")
        self.p1_label.setSuffix("°")
        self.p1_label.setRange(-180.0, 180.0)
        self.p1_label.setDecimals(0)
        self.p1_label.setValue(0.0)
        self.p1_label.setFixedWidth(110)
        self.p1_label.valueChanged.connect(self._on_p1_spin_changed)
        self.p1_slider = QSlider(Qt.Orientation.Horizontal)
        self.p1_slider.setRange(-180, 180)
        self.p1_slider.setValue(0)
        self.p1_slider.valueChanged.connect(self._on_p1_changed)
        self.p1_slider.sliderReleased.connect(lambda: self.phase_changed.emit(True))
        row.addWidget(self.p1_label)
        row.addWidget(self.p1_slider, 1)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setToolTip(tr("P0/P1 Return to zero"))
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip(
                tr(
                "Copy the P0/P1 value, which can be pasted into the manual phase parameter or "
                "script PS "
                "line",
            )
        )
        self.copy_button.clicked.connect(self._copy_values)
        row.addWidget(self.copy_button)
        outer.addLayout(row)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #888;")
        outer.addWidget(self.hint_label)
        self.set_available(False)



    # ------------------------------------------------------------- API
    def values(self) -> tuple[float, float]:
        """Current (P0, P1) degrees."""
        return self._p0, self._p1

    def set_values(self, p0: float, p1: float) -> None:
        self.p0_slider.setValue(max(-180, min(180, int(round(p0)))))
        self.p1_slider.setValue(max(-180, min(180, int(round(p1)))))

    def reset(self) -> None:
        self.set_values(0.0, 0.0)
        self.phase_changed.emit(True)

    def set_visible_1d_mode(self, visible: bool) -> None:
        """The phase panel is shown in 1D mode (also shown in strip mode); it is not interactive
        when hidden."""
        self.setVisible(visible)
        self.set_available(visible)

    def set_available(self, available: bool, hint: str = "") -> None:
        """Enabled when replica data is available; disabled and prompted for real spectrum, etc."""
        for widget in (
            self.p0_label,
            self.p0_slider,
            self.p1_label,
            self.p1_slider,
            self.reset_button,
            self.copy_button,
        ):
            widget.setEnabled(available)
        self.hint_label.setText(
            ""
            if available
            else (hint or
                tr(
                "Only display phase modulation (do not change data): Available after viewing "
                "one-dimensional spectra or turning on 1D "
                "strips",
            ))
        )

    # ------------------------------------------------------------- slots
    def _on_p0_changed(self, value: int) -> None:
        self._p0 = float(value)
        self.p0_label.setValue(value)
        self.phase_changed.emit(False)

    def _on_p1_changed(self, value: int) -> None:
        self._p1 = float(value)
        self.p1_label.setValue(value)
        self.phase_changed.emit(False)

    def _on_p0_spin_changed(self, value: float) -> None:
        self.p0_slider.setValue(int(round(value)))

    def _on_p1_spin_changed(self, value: float) -> None:
        self.p1_slider.setValue(int(round(value)))

    def _copy_values(self) -> None:
        QApplication.clipboard().setText(f"P0={self._p0:.0f} P1={self._p1:.0f}")


__all__ = ["PhasePanel"]
