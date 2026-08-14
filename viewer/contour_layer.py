"""pyqtgraph 等值线图层:POKY/nmrDraw 风格真实等高线(细线框)。

用 contourpy(C++ Marching Squares,matplotlib 底层引擎)在数据原始分辨率
逐级追踪等值线,正负级分别构 QPainterPath 折线,按级画 1px 线。与
POKY/SPARKY/nmrDraw 的「lowest × factor^n 离散等值线」观感一致(级别由
调用方给定;spectrum_viewer 默认 geomspace 等价于把最高级钉到谱峰 max 的
几何级数)。

0.2.75:彻底弃用 0.2.71 引入的光栅化 RGBA 强度图——其观感是连续 alpha
填色,与 nmrDraw/POKY 的细线框完全不同;且大缓冲分配模式曾在 VM 触发
PyQt6/sip wrapper 缓存错配段错误(0.2.73 以尺寸分流规避)。contourpy
原生分辨率提取 512x1024 谱 10 级约 16-50ms、36 级约 52-116ms,QPainterPath
构建最坏约 170ms,与光栅化同量级,无需再按尺寸分流。
"""

from __future__ import annotations

import contourpy
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui


class ContourLayer(pg.GraphicsObject):
    """把二维数据渲染为 POKY/nmrDraw 式离散等值线(坐标即数据下标)。"""

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
        # zoom 保留以兼容旧调用;0.2.75 起原生分辨率渲染,不再插值放大
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
        self._bounds = QtCore.QRectF()
        self.setZValue(5)
        self.setData(data, levels)

    def setData(self, data: np.ndarray, levels: np.ndarray) -> None:
        self._data = np.asarray(data, dtype=float)
        if self._data.ndim != 2:
            raise ValueError(f"轮廓仅支持二维数据,当前 {self._data.ndim} 维")
        self._levels = np.asarray(levels, dtype=float)
        self._gen = (
            contourpy.contour_generator(z=self._data)
            if self._data.size
            else None
        )
        self._build_paths()
        self.informViewBoundsChanged()

    def set_levels(self, levels: np.ndarray) -> None:
        """仅更新级别并重建等值线路径(小/大谱统一快速路径)。"""
        self._levels = np.asarray(levels, dtype=float)
        self._build_paths()
        self.update()

    def setPen(self, pen, neg_pen=None) -> None:
        self._pen = pg.mkPen(pen)
        if neg_pen is not None:
            self._pen_neg = pg.mkPen(neg_pen)
        self.update()

    def _build_paths(self) -> None:
        """用 contourpy 逐级追踪等值线,正负级分别写入 QPainterPath。"""
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
