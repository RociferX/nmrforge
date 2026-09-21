"""Pyqtgraph contour layer: POKY/nmrDraw style real contours (thin line frame). The small spectrum
uses matplotlib contours (from 0.2.73 to VM, fully verified and stable); the real data scale
(number of pixels >= ``_CONTOURPY_MIN_PIXELS``) uses contourpy (C++ Marching Squares, matplotlib
underlying engine) to track the real contours step by step in the original resolution of the
data, positive = layer colour, negative = red 1px Polyline. Consistent with
POKY/SPARKY/nmrDraw's "lowest x factor^n discrete contour" look and feel (the level is given by
the caller; spectrum_viewer default geomspace is equivalent to the geometric series that pins
the highest level to the spectral peak max). 0.2.75: Large spectrum rendering is changed from
rasterized RGBA intensity map (0.2.71-0.2.74) to contourpy true contour -- Rasterization is
continuous alpha coloring, which is completely different from the thin line frame of
nmrDraw/POKY; the performance is of the same order as rasterization (512x1024 spectrum, 10
levels are about 16-50ms, 36 levels are about 52-116ms, QPainterPath build worst case is about
170ms). VM Linux full-scale pytest has confirmed: small spectrum The rasterized memory
allocation mode will trigger the Qt binding to the wrapper cache mismatch of the C++ destructed
sub-control, so the small spectrum retains the matplotlib path; the unified use of contourpy
(including small spectrum) will re-trigger the segmentation fault (crash point drift, 39737a5 is
all green in the same package), so the size shunt is retained."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

from ui_support.i18n import tr

# Threshold for using contourpy on large spectra: once the pixel count reaches this value
# (512x1024) contourpy is used, and smaller spectra go through matplotlib (benchmarked in 0.2.73,
# which also fixed the crash matplotlib hit on those sizes).
_CONTOURPY_MIN_PIXELS = 512 * 1024


class ContourLayer(pg.GraphicsObject):
    """Render two-dimensional data as contour lines; the coordinate is the data point index
    (corresponding to the ppm axis)."""

    def __init__(
        self,
        data: np.ndarray,
        levels: np.ndarray,
        pen,
        parent=None,
        neg_pen=None,
        zoom: float = 2.0,
    ) -> None:
        super().__init__(parent)
        self._zoom = float(zoom)
        self._pen = pg.mkPen(pen)
        self._pen_neg = (
            pg.mkPen(neg_pen)
            if neg_pen is not None
            else pg.mkPen("#e74c3c", width=1)
        )
        self._data: np.ndarray | None = None
        self._levels: np.ndarray | None = None
        self._gen = None
        self._path = QtGui.QPainterPath()
        self._path_neg = QtGui.QPainterPath()
        self._smooth: np.ndarray | None = None
        self._use_contourpy = False
        self._bounds = QtCore.QRectF()
        self.setZValue(5)
        self.setData(data, levels)

    def setData(self, data: np.ndarray, levels: np.ndarray) -> None:
        self._data = np.asarray(data, dtype=float)
        if self._data.ndim != 2:
            raise ValueError(
                tr(
                "Contours only support 2D data (currently {p0} "
                "dimension)",
                p0=self._data.ndim,
            )
            )
        self._levels = np.asarray(levels, dtype=float)
        self._smooth = None
        self._use_contourpy = self._data.size >= _CONTOURPY_MIN_PIXELS
        if self._use_contourpy:
            # Lazy: Avoid loading extensions during the collection phase that change the heap
            # layout.
            import contourpy

            self._gen = (
                contourpy.contour_generator(z=self._data)
                if self._data.size
                else None
            )
            self._build_paths()
        else:
            self._gen = None
            self._rebuild()
        self.informViewBoundsChanged()

    def set_levels(self, levels: np.ndarray) -> None:
        """Only update levels and rebuild (matplotlib path/Big score and contourpy are both
        fast)."""
        self._levels = np.asarray(levels, dtype=float)
        if self._use_contourpy:
            self._build_paths()
            self.update()
        elif self._smooth is not None:
            self._rebuild_paths()
            self.update()
        else:
            self._rebuild()

    def setPen(self, pen, neg_pen=None) -> None:
        self._pen = pg.mkPen(pen)
        if neg_pen is not None:
            self._pen_neg = pg.mkPen(neg_pen)
        self.update()

    # ---- matplotlib contour path (small spectrum, starting from 0.2.73 VM, fully verified and
    # stable) ----.

    def _rebuild(self) -> None:
        """Full reconstruction: interpolation data + contour path (Xiaopu first time/When changing
        spectrum)."""
        from scipy import ndimage

        data = self._data
        levels = self._levels
        self._smooth = None
        if data is not None and data.size and levels is not None and len(levels):
            zoom = self._zoom
            smooth = ndimage.zoom(data, zoom, order=1)
            self._smooth = smooth
            self._rebuild_paths()
        else:
            self._path = QtGui.QPainterPath()
            self._path_neg = QtGui.QPainterPath()
        if data is not None and data.ndim == 2:
            height, width = data.shape
            self._bounds = QtCore.QRectF(0.0, 0.0, float(width), float(height))
        else:
            self._bounds = QtCore.QRectF()
        self.prepareGeometryChange()

    def _rebuild_paths(self) -> None:
        """Reconstruct the contour path using cached interpolation data (Score slider/Fast when
        level changes)."""
        import matplotlib.pyplot as plt

        path_pos = QtGui.QPainterPath()
        path_neg = QtGui.QPainterPath()
        smooth = self._smooth
        levels = self._levels
        if smooth is None or levels is None or not len(levels):
            return
        zoom = self._zoom
        fig = plt.figure()
        try:
            cs = plt.contour(smooth, levels=levels)
            # View y directly takes the matplotlib row number (data row 0 -> view y=0); with view
            # invertY(False) (view y increases = screen upward), data row 0 (height ppm) is
            # displayed at the bottom of the screen.
            for level, segs in zip(cs.levels, cs.allsegs):
                target = path_neg if level < 0 else path_pos
                for seg in segs:
                    if len(seg) < 2:
                        continue
                    target.moveTo(seg[0, 0] / zoom, seg[0, 1] / zoom)
                    for point in seg[1:]:
                        target.lineTo(point[0] / zoom, point[1] / zoom)
        finally:
            plt.close(fig)
        self._path = path_pos
        self._path_neg = path_neg

    # ---- contourpy real contours (large spectrum, 0.2.75, native resolution) ----.

    def _build_paths(self) -> None:
        """Use contourpy to track the contours step by step, and write the positive and negative
        levels into QPainterPath respectively."""
        path_pos = QtGui.QPainterPath()
        path_neg = QtGui.QPainterPath()
        gen = self._gen
        levels = self._levels
        data = self._data
        if gen is not None and levels is not None and len(levels):
            for level in levels:
                if level == 0:
                    continue
                target = path_neg if level < 0 else path_pos
                for line in gen.create_contour(float(level)):
                    if len(line) < 2:
                        continue
                    target.moveTo(line[0, 0], line[0, 1])
                    for point in line[1:]:
                        target.lineTo(point[0], point[1])
        if data is not None and data.ndim == 2:
            height, width = data.shape
            self._bounds = QtCore.QRectF(0.0, 0.0, float(width), float(height))
        else:
            self._bounds = QtCore.QRectF()
        self._path = path_pos
        self._path_neg = path_neg
        self.prepareGeometryChange()

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds

    def paint(self, painter, *args) -> None:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if not self._path.isEmpty():
            painter.setPen(self._pen)
            painter.drawPath(self._path)
        if not self._path_neg.isEmpty():
            painter.setPen(self._pen_neg)
            painter.drawPath(self._path_neg)
