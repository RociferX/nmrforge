"""NMR ViewBox dedicated to the viewer: frame selection zoom (RectMode) + middle-click drag pan +
wheel zoom. pyqtgraph's RectMode will treat the middle button as a frame selection zoom; and its
pan branch ``QPointF * numpy array `` will cause TypeError (PyQt6 measured), so the pan is
calculated by itself. 0.2.133: The right click no longer responds to any behaviour; the view
range is limited to the complete data range (reset_view Within the smallest rectangle obtained)
-- zooming out cannot shrink the complete spectrum out of the field of view."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore

_MIN_VIEW_RANGE = 8.0


class NMRViewBox(pg.ViewBox):
    """Keep the "middle-click drag pan" gesture under RectMode and limit the view boundaries."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._min_rect: list[tuple[float, float]] | None = None

    # ---------------------------------------------------------- Mouse.

    def mouseDragEvent(self, ev, axis=None) -> None:
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            ev.accept()
            return
        # 1D mode/PanMode: left button is for crosshair tracking, not panning (0.2.150)
        if (
            ev.button() == QtCore.Qt.MouseButton.LeftButton
            and self.state["mouseMode"] == pg.ViewBox.PanMode
        ):
            ev.accept()
            return
        if ev.button() == QtCore.Qt.MouseButton.MiddleButton:
            ev.accept()
            pos = ev.pos()
            last_pos = ev.lastPos()
            diff = np.array(
                [pos.x() - last_pos.x(), pos.y() - last_pos.y()]
            ) * -1.0
            mouse_enabled = np.array(self.state["mouseEnabled"], dtype=float)
            transform = self.childGroup.transform().inverted()[0]
            p1 = transform.map(
                QtCore.QPointF(float(diff[0]), float(diff[1]))
            )
            p0 = transform.map(QtCore.QPointF(0.0, 0.0))
            moved = p1 - p0
            x = moved.x() if mouse_enabled[0] == 1 else None
            y = moved.y() if mouse_enabled[1] == 1 else None
            self._resetTarget()
            if x is not None or y is not None:
                self.translateBy(x=x, y=y)
            self.sigRangeChangedManually.emit(self.state["mouseEnabled"])
            return
        super().mouseDragEvent(ev, axis)

    # ---------------------------------------------------------- scope.

    # ---------------------------------------------------------- Zoom.

    def wheelEvent(self, ev, axis=None):
        """The scroll wheel clamps immediately after zooming (zooming cannot exceed the complete
        range)."""
        super().wheelEvent(ev, axis)
        self._clamp_view_range()

    def scaleBy(self, s=None, center=None, **kwargs):
        """Clamp immediately after scaling(roller/Zoom animation)."""
        super().scaleBy(s=s, center=center, **kwargs)
        self._clamp_view_range()

    def translateBy(self, t=None, **kwargs):
        """Clamp immediately after translation (spectrum cannot be moved out of view)."""
        super().translateBy(t=t, **kwargs)
        self._clamp_view_range()

    def set_full_range(self, x_range, y_range, padding=0) -> None:
        """Restore the full view and update the zoom base (reset_view is exclusive). Update
        _min_rect first and then set the range to avoid being clamped by the old base; then the
        user's zoom view cannot exceed the full range."""
        self._min_rect = [
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        ]
        super().setRange(xRange=x_range, yRange=y_range, padding=padding)

    def setRange(self, rect=None, **kwargs):
        """Restrictions narrowed/Translate boundaries (No constraints will be applied when the
        complete range is not recorded)."""
        super().setRange(rect=rect, **kwargs)
        if getattr(self, "_min_rect", None) is not None:
            self._clamp_view_range()

    def _clamp_view_range(self) -> None:
        """The view cannot exceed the complete data range (recorded by set_full_range). The upper
        limit of reduction = the complete range (all long sides are exposed, that is, to the
        top); translation keeps the view within the complete range; single-axis minimum span
        protection (prevents over-zooming), and the overall translation keeps the span unchanged
        when close to the boundary."""
        if getattr(self, "_min_rect", None) is None:
            return
        vr = self.viewRange()
        new_ranges = list(vr)
        changed = False
        for axis_idx in (0, 1):
            mlo, mhi = self._min_rect[axis_idx][0], self._min_rect[axis_idx][1]
            mspan = mhi - mlo
            lo, hi = vr[axis_idx]
            span = hi - lo
            if mspan <= 0:
                continue
            # The upper limit of reduction: the span cannot exceed the complete range (all long
            # sides are exposed, that is, the top is reached).
            if span > mspan:
                center = (lo + hi) / 2.0
                lo, hi = center - mspan / 2.0, center + mspan / 2.0
                changed = True
            # Panning cannot move the spectrum out of view (aligned with full range).
            if lo < mlo:
                shift = mlo - lo
                hi = min(hi + shift, mhi)
                lo = mlo
                changed = True
            if hi > mhi:
                shift = hi - mhi
                lo = max(lo - shift, mlo)
                hi = mhi
                changed = True
            # Minimum span protection (anti-over-amplification): span maintenance _MIN_VIEW_RANGE.
            if span < _MIN_VIEW_RANGE and mspan > _MIN_VIEW_RANGE:
                center = (lo + hi) / 2.0
                half = _MIN_VIEW_RANGE / 2.0
                lo, hi = center - half, center + half
                if lo < mlo:
                    lo = mlo
                    hi = mlo + _MIN_VIEW_RANGE
                elif hi > mhi:
                    hi = mhi
                    lo = mhi - _MIN_VIEW_RANGE
                changed = True
            new_ranges[axis_idx] = (lo, hi)
        if changed:
            super().setRange(
                xRange=new_ranges[0], yRange=new_ranges[1], padding=0
            )

