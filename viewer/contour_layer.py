"""pyqtgraph 轮廓图层(contour):matplotlib contour 生成 QPainterPath,正负峰分色。

Poky/nmrDraw 风格:
- 插值后绘制使轮廓圆润(``zoom`` 因子,默认 2;级别越多越细腻);
- 开启抗锯齿,避免折线锯齿;
- 正峰主色(默认黑)、负峰红色,与 Poky 正黑负红约定一致。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui


class ContourLayer(pg.GraphicsObject):
    """把二维数据画成轮廓(contour);坐标即数据点下标(与 ppm 轴对应)。"""

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
        self._path = QtGui.QPainterPath()
        self._path_neg = QtGui.QPainterPath()
        self._bounds = QtCore.QRectF()
        self.setZValue(5)
        self.setData(data, levels)

    def setData(self, data: np.ndarray, levels: np.ndarray) -> None:
        self._data = np.asarray(data, dtype=float)
        self._levels = np.asarray(levels, dtype=float)
        self._smooth = None
        self._rebuild()
        self.informViewBoundsChanged()

    def set_levels(self, levels: np.ndarray) -> None:
        """仅更新级别并重建轮廓(复用插值数据,避免重复 zoom)。"""
        self._levels = np.asarray(levels, dtype=float)
        if self._smooth is not None:
            self._rebuild_paths()
            self.update()
        else:
            self._rebuild()

    def setPen(self, pen, neg_pen=None) -> None:
        self._pen = pg.mkPen(pen)
        if neg_pen is not None:
            self._pen_neg = pg.mkPen(neg_pen)
        self.update()

    def _rebuild(self) -> None:
        """全量重建:插值数据 + 轮廓路径(首次/换谱时)。"""
        from scipy import ndimage

        data = self._data
        levels = self._levels
        if data is not None and data.ndim != 2:
            raise ValueError(f"轮廓仅支持二维数据(当前 {data.ndim} 维)")
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
        """用已缓存插值数据重建轮廓路径(滑块/级数变化时快速)。"""
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
            # view y 直接取 matplotlib 行号(数据行 0 → view y=0);
            # 配合视图 invertY(False)(view y 增大=屏幕向上),
            # 数据行 0(高 ppm)显示在屏幕底部。
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

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds

    def paint(self, painter, *args) -> None:
        painter.setRenderHint(
            QtGui.QPainter.RenderHint.Antialiasing, True
        )
        if not self._path.isEmpty():
            painter.setPen(self._pen)
            painter.drawPath(self._path)
        if not self._path_neg.isEmpty():
            painter.setPen(self._pen_neg)
            painter.drawPath(self._path_neg)
