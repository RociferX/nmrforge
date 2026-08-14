"""pyqtgraph 轮廓图层:POKY/nmrDraw 风格真实等值线(细线框)。

小谱走 matplotlib 等高线(0.2.73 起 VM 全量验证稳定);真实数据规模
(像素数 >= ``_CONTOURPY_MIN_PIXELS``)走 contourpy(C++ Marching Squares,
matplotlib 底层引擎)在数据原始分辨率逐级追踪真实等值线,正=层色、负=红
1px 折线。与 POKY/SPARKY/nmrDraw 的「lowest × factor^n 离散等值线」观感
一致(级别由调用方给定;spectrum_viewer 默认 geomspace 等价于把最高级
钉到谱峰 max 的几何级数)。

0.2.75:大谱渲染从光栅化 RGBA 强度图(0.2.71-0.2.74)改为 contourpy 真实
等值线——光栅化是连续 alpha 填色,与 nmrDraw/POKY 的细线框完全不同;
性能与光栅化同量级(512x1024 谱 10 级约 16-50ms、36 级约 52-116ms,
QPainterPath 构建最坏约 170ms)。VM Linux 全量 pytest 下曾证实:小谱
光栅化的内存分配模式会触发 PyQt6/sip 对 C++ 已析构子控件的 wrapper
缓存错配段错误,故小谱保留 matplotlib 路径;统一走 contourpy(含小谱)
会重新触发该段错误(崩溃点漂移,39737a5 同套件全绿),因此保留尺寸分流。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

# 大谱走 contourpy 的阈值:像素数达到该值(512x1024)才走 contourpy,
# 更小谱走 matplotlib(0.2.73 Architect 基准;VM 段错误规避边界)
_CONTOURPY_MIN_PIXELS = 512 * 1024


class ContourLayer(pg.GraphicsObject):
    """把二维数据渲染为等高线;坐标即数据点下标(与 ppm 轴对应)。"""

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
            raise ValueError(f"轮廓仅支持二维数据(当前 {self._data.ndim} 维)")
        self._levels = np.asarray(levels, dtype=float)
        self._smooth = None
        self._use_contourpy = self._data.size >= _CONTOURPY_MIN_PIXELS
        if self._use_contourpy:
            import contourpy  # 惰性:避免收集阶段加载扩展改变堆布局

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
        """仅更新级别并重建(小谱 matplotlib 路径/大谱 contourpy 均快速)。"""
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

    # ---- matplotlib 等高线路径(小谱,0.2.73 起 VM 全量验证稳定) ----

    def _rebuild(self) -> None:
        """全量重建:插值数据 + 轮廓路径(小谱首次/换谱时)。"""
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
        """用已缓存插值数据重建轮廓路径(小谱滑块/级数变化时快速)。"""
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

    # ---- contourpy 真实等值线(大谱,0.2.75,原生分辨率) ----

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
