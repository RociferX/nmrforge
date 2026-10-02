"""3D Spectrum View Control Panel (Contract §10): View plane/third-axis slice slider (ppm).
Independent control, can be hung into the SpectrumViewer control area (SpectrumWindow is shared
with the GUI spectrum panel); the slice product is returned to the 2D Spectrum
via:func:`current_spectrum`, multiplexing the SpectrumViewer/ContourLayer drawing (positive
black and negative red, Frame selection zoom/Pan/Scroll wheels are retained). 0.2.133: Only keep
the slice Mode; projection file (proj3D product.ft2) can be viewed directly by clicking on the
spectrum list on the right. The 3D panel no longer provides projection mode.
"""

from __future__ import annotations

import math

from qtcompat.QtCore import QSignalBlocker, Qt
from qtcompat.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from qtcompat import Signal
from ui_support.i18n import tr
from ui_support.numeric_inputs import CommitDoubleSpinBox, CommitSpinBox
from viewer.spectrum import Spectrum, Spectrum3D

_PLANES = (("F1-F2", 2), ("F1-F3", 1), ("F2-F3", 0))

_PLANE_INDEX_BY_SLICE_AXIS = {0: 2, 1: 1, 2: 0}


class Spectrum3DPanel(QWidget):
    """3D spectrum control: flat/slice slider, change slice_changed (caller redraw)."""

    slice_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spectrum3d: Spectrum3D | None = None
        self._slice_axis = 0
        self._mode = "slice"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(QLabel("3D view"))

        row = QHBoxLayout()
        row.setSpacing(12)
        self.plane_combo = QComboBox()
        self.plane_combo.addItems([name for name, _ in _PLANES])
        self.plane_combo.setToolTip(tr("Select viewing plane (third axis for slicing)"))
        self.plane_combo.currentIndexChanged.connect(self._on_plane_changed)
        row.addWidget(self.plane_combo)
        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.slice_slider.setToolTip(tr("Third axis slice position (drag and release to refresh)"))

        self.slice_slider.setMinimumWidth(140)
        self.slice_slider.valueChanged.connect(self._on_slider_value)
        self.slice_slider.sliderReleased.connect(self._emit)
        self.slice_slider.setEnabled(False)
        row.addWidget(self.slice_slider, 1)

        point_field = QWidget()
        point_layout = QHBoxLayout(point_field)
        point_layout.setContentsMargins(0, 0, 0, 0)
        point_layout.setSpacing(2)
        self.point_label = QLabel("F1 pt")
        point_layout.addWidget(self.point_label)
        self.point_spin = CommitSpinBox()
        self.point_spin.setRange(0, 1)
        self.point_spin.setValue(0)
        self.point_spin.setSingleStep(1)
        self.point_spin.setFixedWidth(62)
        self.point_spin.valueChanged.connect(self._on_point_spin)
        self.point_spin.setEnabled(False)
        point_layout.addWidget(self.point_spin)
        row.addWidget(point_field)

        ppm_field = QWidget()
        ppm_layout = QHBoxLayout(ppm_field)
        ppm_layout.setContentsMargins(0, 0, 0, 0)
        ppm_layout.setSpacing(2)
        self.ppm_label = QLabel("F1 ppm")
        ppm_layout.addWidget(self.ppm_label)
        self.ppm_spin = CommitDoubleSpinBox()
        self.ppm_spin.setDecimals(3)
        self.ppm_spin.setRange(0.0, 1.0)
        self.ppm_spin.setValue(0.0)
        self.ppm_spin.setFixedWidth(108)
        self.ppm_spin.valueChanged.connect(self._on_ppm_spin)
        self.ppm_spin.setEnabled(False)
        ppm_layout.addWidget(self.ppm_spin)
        row.addWidget(ppm_field)
        layout.addLayout(row)

    # ------------------------------------------------------------- API
    @property
    def spectrum3d(self) -> Spectrum3D | None:
        """The currently bound 3D spectrum (None if not loaded)."""
        return self._spectrum3d

    @property
    def slice_axis(self) -> int:
        """Return the fixed logical slicing axis index: 0=F1, 1=F2, 2=F3, for background plane
        streaming.
        """
        return int(self._slice_axis)

    def set_spectrum3d(self, spectrum3d: Spectrum3D) -> None:
        """Bind a 3D spectrum, reset to the default F3-F2 plane (fixed F1) and emit redraw."""
        self._spectrum3d = spectrum3d
        self._slice_axis = 0
        self._mode = "slice"
        noise = spectrum3d.estimate_noise()
        self._proj_thresh = 3.0 * noise if noise > 0 else 0.01 * max(spectrum3d.max_intensity, 1.0)

        for index, (_, axis) in enumerate(_PLANES):
            remaining = [i for i in range(3) if i != axis]
            name = "-".join(self._spectrum3d.axes[i].label for i in remaining)
            self.plane_combo.setItemText(index, name)

        self._fit_plane_combo_width()

        self.plane_combo.setCurrentIndex(_PLANE_INDEX_BY_SLICE_AXIS.get(self._slice_axis, 0))
        self._update_slider_range()
        self._update_position_controls()
        self._emit()

    def _fit_plane_combo_width(self) -> None:
        """Fit the plane selector to its longest nucleus-labelled item to avoid ellipsis; item text
        changes do not refresh QComboBox's width hint automatically.
        """
        metrics = self.plane_combo.fontMetrics()
        widest = 0
        for index in range(self.plane_combo.count()):
            text = self.plane_combo.itemText(index)
            widest = max(widest, metrics.horizontalAdvance(text))
        if widest <= 0:
            return

        self.plane_combo.setMinimumWidth(widest + 48)

    def clear(self) -> None:
        """Unbind the 3D spectrum and hide the panel."""
        self._spectrum3d = None
        self.slice_slider.setEnabled(False)
        self.slice_slider.setValue(0)
        self.point_spin.setEnabled(False)
        self.point_spin.setValue(0)
        self.ppm_spin.setEnabled(False)
        self.ppm_spin.setValue(0.0)
        self.setVisible(False)

    def current_spectrum(self) -> Spectrum | None:
        """Current slice product (2D Spectrum); returns None for unbound 3D spectrum."""
        if self._spectrum3d is None:
            return None
        spectrum = self._spectrum3d.slice(self._slice_axis, self.slice_slider.value())

        dims: list[int] = []
        for axis in spectrum.axes:
            for i, axis3 in enumerate(self._spectrum3d.axes):
                if axis is axis3:
                    dims.append(i)
                    break
        spectrum.dim_indices = tuple(dims)

        try:
            import numpy as np

            axis3 = self._spectrum3d.axes[self._slice_axis]
            spectrum.slice_axis = int(self._slice_axis)
            spectrum.slice_ppm = float(axis3.ppm_at(self.slice_slider.value()))
            ppm = np.asarray(axis3.ppm, dtype=float)
            diff = np.abs(np.diff(ppm))
            diff = diff[diff > 0]
            spectrum.slice_step_ppm = float(np.median(diff)) if diff.size else 0.0

            spectrum.slice_ppm_min = float(np.min(ppm)) if ppm.size else None
            spectrum.slice_ppm_max = float(np.max(ppm)) if ppm.size else None
        except Exception:  # noqa: BLE001
            spectrum.slice_axis = None
            spectrum.slice_ppm = None
            spectrum.slice_step_ppm = None
            spectrum.slice_ppm_min = None
            spectrum.slice_ppm_max = None
        return spectrum

    def current_name(self, base: str = "") -> str:
        """Product display name: for example 'hsqc3d N-H slice'."""
        plane = self._plane_name()
        if not base and self._spectrum3d is not None and self._spectrum3d.source:
            base = self._spectrum3d.source.stem
        return tr("{p0} {p1} slice", p0=base or "3D", p1=plane)

    def slice_axis_label(self) -> str:
        """The label of the currently pinned (sliced) axis."""
        if self._spectrum3d is None:
            return ""
        return self._spectrum3d.axes[self._slice_axis].label

    def refresh(self) -> None:
        """Recalculate the product and issue redraw(After dragging the slider/external trigger)."""
        self._update_position_controls()
        self._emit()

    # ------------------------------------------------------------- slots
    def _on_plane_changed(self, index: int) -> None:
        if 0 <= index < len(_PLANES):
            self._slice_axis = _PLANES[index][1]
            self._update_slider_range()
            self._update_position_controls()
            self._emit()

    def _on_slider_value(self, _value: int) -> None:
        self._update_position_controls()

    def _on_point_spin(self, value: int) -> None:
        """Enter the point number -> synchronize the slider and refresh the slice."""
        if self._spectrum3d is None or value == self.slice_slider.value():
            return
        self.slice_slider.setValue(value)
        self._emit()

    def _on_ppm_spin(self, value: float) -> None:
        """Enter ppm -> get the nearest pixel and refresh the slice."""
        if self._spectrum3d is None:
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        index = axis.index_at(value)
        if index == self.slice_slider.value():
            with QSignalBlocker(self.ppm_spin):
                self.ppm_spin.setValue(axis.ppm_at(index))
            return
        self.slice_slider.setValue(index)
        self._emit()

    def _update_slider_range(self) -> None:
        if self._spectrum3d is None:
            self.slice_slider.setEnabled(False)
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        self.slice_slider.setRange(0, max(0, axis.size - 1))
        self.slice_slider.setValue(axis.size // 2)
        self.slice_slider.setEnabled(True)

        with QSignalBlocker(self.point_spin), QSignalBlocker(self.ppm_spin):
            self.point_spin.setRange(0, max(0, axis.size - 1))
            self.point_spin.setEnabled(True)
            self.point_label.setText(f"{axis.label} pt" if axis.label else "pt")
            self.ppm_spin.setRange(float(min(axis.ppm)), float(max(axis.ppm)))
            self.ppm_spin.setEnabled(True)
            self.ppm_label.setText(f"{axis.label} ppm" if axis.label else "ppm")

    def _update_position_controls(self) -> None:
        """Sync plane position, external axis labels and the actual adjacent-point step for the
        selected axis.
        """
        if self._spectrum3d is None:
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        value = self.slice_slider.value()
        with QSignalBlocker(self.point_spin), QSignalBlocker(self.ppm_spin):
            self.point_label.setText(f"{axis.label} pt" if axis.label else "pt")
            self.ppm_label.setText(f"{axis.label} ppm" if axis.label else "ppm")
            if axis.size > 1:
                neighbor = value + 1 if value < axis.size - 1 else value - 1
                ppm_step = abs(axis.ppm_at(neighbor) - axis.ppm_at(value))
            else:
                ppm_step = 1.0
            if ppm_step > 0:
                decimals = max(
                    3,
                    min(10, int(math.ceil(-math.log10(ppm_step))) + 2),
                )
                self.ppm_spin.setDecimals(decimals)
                self.ppm_spin.setSingleStep(ppm_step)
            self.point_spin.setValue(value)
            ppm = axis.ppm_at(value) if axis.size else 0.0
            self.ppm_spin.setValue(ppm)

    def _plane_name(self) -> str:
        index = self.plane_combo.currentIndex()
        if 0 <= index < len(_PLANES):
            return _PLANES[index][0]
        return "F1-F2"

    def _emit(self) -> None:
        self.slice_changed.emit()


__all__ = ["Spectrum3DPanel"]
