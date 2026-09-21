"""3D Spectrum View Control Panel (Contract §10): View plane/third-axis slice slider (ppm).
Independent control, can be hung into the SpectrumViewer control area (SpectrumWindow is shared
with the GUI spectrum panel); the slice product is returned to the 2D Spectrum
via:func:`current_spectrum`, multiplexing the SpectrumViewer/ContourLayer drawing (positive
black and negative red, Frame selection zoom/Pan/Scroll wheels are retained). 0.2.133: Only keep
the slice Mode; projection file (proj3D product.ft2) can be viewed directly by clicking on the
spectrum list on the right. The 3D panel no longer provides projection mode."""

from __future__ import annotations

from qtcompat.QtCore import QSignalBlocker, Qt
from qtcompat.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from qtcompat import Signal
from ui_support.i18n import tr
from viewer.spectrum import Spectrum, Spectrum3D

# (Display name, be fixed/axis of slice index); The viewing plane is the other two axes other than
# this axis.
_PLANES = (("F1-F2", 2), ("F1-F3", 1), ("F2-F3", 0))
# Slice axis index -> plane drop-down item index.
_PLANE_INDEX_BY_SLICE_AXIS = {0: 2, 1: 1, 2: 0}


def _nucleus_of(label: str) -> str:
    """Axis label -> core name (compatible with 1H/15N/13C and H/N/C, go to x/y/z index)."""
    text = str(label or "").strip()
    if text[-1:] in ("x", "y", "z") and len(text) > 1:
        text = text[:-1]
    return {"H": "1H", "N": "15N", "C": "13C"}.get(text, text)


class Spectrum3DPanel(QWidget):
    """3D spectrum control: flat/slice slider, change slice_changed (caller redraw)."""

    slice_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spectrum3d: Spectrum3D | None = None
        self._slice_axis = 2  # Default F1-F2 plane (fixed F3).
        self._mode = "slice"  # 0.2.133:Slice mode only.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(QLabel("3D view"))
        # 0.2.148: Plane switching button/slicing bar/slicing specific value in one row.
        row = QHBoxLayout()
        row.setSpacing(12)
        self.plane_combo = QComboBox()
        self.plane_combo.addItems([name for name, _ in _PLANES])
        self.plane_combo.setToolTip(tr("Select viewing plane (third axis for slicing)"))
        self.plane_combo.currentIndexChanged.connect(self._on_plane_changed)
        row.addWidget(self.plane_combo)
        self.slice_slider = QSlider(Qt.Orientation.Horizontal)
        self.slice_slider.setToolTip(tr("Third axis slice position (drag and release to refresh)"))
        # 0.2.199-patch10: The drag bar is lengthened (minimum width, fine-tuning slices is more
        # convenient) 0.2.199-patch29dg: The minimum width is too large (~610px in total with the
        # fixed input box) will tighten the right side panel column/window spread to 140 (with a
        # stretch factor, it can still be lengthened within the available width).
        self.slice_slider.setMinimumWidth(140)
        self.slice_slider.valueChanged.connect(self._on_slider_value)
        self.slice_slider.sliderReleased.connect(self._emit)
        self.slice_slider.setEnabled(False)
        row.addWidget(self.slice_slider, 1)
        # 0.2.149: slice position point / ppm can be input directly and synchronized with the slider
        # in three directions.
        self.point_spin = QSpinBox()
        self.point_spin.setPrefix("pt: ")
        self.point_spin.setRange(0, 1)
        self.point_spin.setValue(0)
        self.point_spin.setFixedWidth(110)
        self.point_spin.valueChanged.connect(self._on_point_spin)
        self.point_spin.setEnabled(False)
        row.addWidget(self.point_spin)
        self.ppm_spin = QDoubleSpinBox()
        self.ppm_spin.setSuffix(" ppm")
        self.ppm_spin.setDecimals(3)
        self.ppm_spin.setRange(0.0, 1.0)
        self.ppm_spin.setValue(0.0)
        self.ppm_spin.setFixedWidth(150)
        self.ppm_spin.valueChanged.connect(self._on_ppm_spin)
        self.ppm_spin.setEnabled(False)
        row.addWidget(self.ppm_spin)
        layout.addLayout(row)

    # ------------------------------------------------------------- API
    def _preferred_slice_axis(self) -> int | None:
        """Prioritize slicing axes: fix non-1H/13C axes (such as 15N), and the remaining two axes
        are the CH plane. 0.2.199-patch29bh(user): The three-dimensional spectrum displays the
        CH plane first. When the axis label cannot form CH (such as HNN double 15N, generic
        F1/F2/F3), None is returned and the default is used."""
        if self._spectrum3d is None:
            return None
        nuclei = [_nucleus_of(a.label) for a in self._spectrum3d.axes]
        fixed = [i for i, n in enumerate(nuclei) if n not in ("1H", "13C")]
        remaining = {n for i, n in enumerate(nuclei) if i not in fixed}
        if len(fixed) == 1 and remaining == {"1H", "13C"}:
            return fixed[0]
        return None

    @property
    def spectrum3d(self) -> Spectrum3D | None:
        """The currently bound 3D spectrum (None if not loaded)."""
        return self._spectrum3d

    def set_spectrum3d(self, spectrum3d: Spectrum3D) -> None:
        """Bind the 3D spectrum and reset the default plane (priority CH, fallback F1-F2);
        automatically issue a redraw."""
        self._spectrum3d = spectrum3d
        self._slice_axis = self._preferred_slice_axis()
        if self._slice_axis is None:
            self._slice_axis = 2  # Fallback to default F1-F2 plane (fixed F3).
        self._mode = "slice"
        noise = spectrum3d.estimate_noise()
        self._proj_thresh = (
            3.0 * noise
            if noise > 0
            else 0.01 * max(spectrum3d.max_intensity, 1.0)
        )
        # Flat drop-down items use core names (such as N-H / N-C / H-C); the index is determined by
        # the axis label.
        for index, (_, axis) in enumerate(_PLANES):
            remaining = [i for i in range(3) if i != axis]
            name = "-".join(self._spectrum3d.axes[i].label for i in remaining)
            self.plane_combo.setItemText(index, name)
        # 0.2.199-patch29bh:CH plane priority (fixed non-1H/13C axis).
        self.plane_combo.setCurrentIndex(
            _PLANE_INDEX_BY_SLICE_AXIS.get(self._slice_axis, 0)
        )
        self._update_slider_range()
        self._update_position_controls()
        self._emit()

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
        spectrum = self._spectrum3d.slice(
            self._slice_axis, self.slice_slider.value()
        )
        # Record the original dimension order of the remaining two axes (F1=0/F2=1/F3=2) for
        # F*_shift mapping of the peak table; 0.2.153: The slice may be transposed according to the
        # abscissa priority, and the 3D dimension order can be checked back by the axis object.
        dims: list[int] = []
        for axis in spectrum.axes:
            for i, axis3 in enumerate(self._spectrum3d.axes):
                if axis is axis3:
                    dims.append(i)
                    break
        spectrum.dim_indices = tuple(dims)
        # 0.2.199-patch29da:Record slice fixed axis/Location(ppm), the viewer only displays the
        # peaks of this plane (avoiding the superposition of all layer peaks, which looks like peak
        # selection on the projection).
        try:
            import numpy as np

            axis3 = self._spectrum3d.axes[self._slice_axis]
            spectrum.slice_axis = int(self._slice_axis)
            spectrum.slice_ppm = float(axis3.ppm_at(self.slice_slider.value()))
            ppm = np.asarray(axis3.ppm, dtype=float)
            diff = np.abs(np.diff(ppm))
            diff = diff[diff > 0]
            spectrum.slice_step_ppm = (
                float(np.median(diff)) if diff.size else 0.0
            )
            # 0.2.199-patch29dj: Fixed the entire range of the axis (for the viewer to determine
            # whether the peak coordinates exceed the axis range; the original "current slice +/-10
            # steps" will treat almost all non-plane peaks as out of bounds).
            spectrum.slice_ppm_min = (
                float(np.min(ppm)) if ppm.size else None
            )
            spectrum.slice_ppm_max = (
                float(np.max(ppm)) if ppm.size else None
            )
        except Exception:  # noqa: BLE001 - Missing axis information does not block slicing.
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
        return tr("{p0} {p1} slice", p0=base or '3D', p1=plane)

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
        # When setting the range, the current value may be clamped to the range boundary. Use
        # QSignalBlocker to avoid writeback triggering the slider to jump.
        with QSignalBlocker(self.point_spin), QSignalBlocker(self.ppm_spin):
            self.point_spin.setRange(0, max(0, axis.size - 1))
            self.point_spin.setEnabled(True)
            self.ppm_spin.setRange(
                float(min(axis.ppm)), float(max(axis.ppm))
            )
            self.ppm_spin.setEnabled(True)

    def _update_position_controls(self) -> None:
        """Synchronize the current slider position to the point/ppm input box (prefixed by the axis
        label)."""
        if self._spectrum3d is None:
            return
        axis = self._spectrum3d.axes[self._slice_axis]
        value = self.slice_slider.value()
        with QSignalBlocker(self.point_spin), QSignalBlocker(self.ppm_spin):
            self.point_spin.setValue(value)
            ppm = axis.ppm_at(value) if axis.size else 0.0
            self.ppm_spin.setPrefix(
                f"{axis.label} " if axis.label else ""
            )
            self.ppm_spin.setValue(ppm)

    def _plane_name(self) -> str:
        index = self.plane_combo.currentIndex()
        if 0 <= index < len(_PLANES):
            return _PLANES[index][0]
        return "F1-F2"

    def _emit(self) -> None:
        self.slice_changed.emit()


__all__ = ["Spectrum3DPanel"]
