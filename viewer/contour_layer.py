"""pyqtgraph 轮廓图层:小谱 matplotlib 等高线,大谱光栅化渲染(正黑负红)。

Poky/nmrDraw 风格:按强度把谱渲染为 RGBA 图像(正峰黑、负峰红,alpha
随强度与级别起点),缩放/平移由 Qt 原生重采样;级别/级数滑块只触发
numpy 向量化重渲染,避免 matplotlib 等高线几何计算的卡顿(真实 512x1024
谱加载从约 10 秒降到约 30 毫秒)。

尺寸分流(0.2.67):像素数小于 ``_RASTER_MIN_PIXELS`` 的小谱继续走
matplotlib 等高线。VM Linux 全量 pytest 下,小谱光栅化的内存分配模式
会触发 PyQt6/sip 对 C++ 已析构子控件的 wrapper 缓存错配,导致 Qt 控件
构造时随机段错误(与渲染逻辑本身无关);真实数据规模(512x1024 及以上)
的大缓冲走 mmap,光栅化路径稳定且保持毫秒级性能。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

# 光栅化阈值:像素数达到该值(512x1024)才走光栅化,更小谱走 matplotlib
# (Architect 基准:阈值处 matplotlib 中位 ≈167ms;上调后安全边际 4×)
_RASTER_MIN_PIXELS = 512 * 1024


class ContourLayer(pg.GraphicsObject):
    """把二维数据渲染为等高线/强度图;坐标即数据点下标(与 ppm 轴对应)。"""

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
        self._image: QtGui.QImage | None = None
        self._image_data = b""  # QImage 引用该内存,必须持有引用
        self._path = QtGui.QPainterPath()
        self._path_neg = QtGui.QPainterPath()
        self._smooth: np.ndarray | None = None
        self._raster = False
        self._bounds = QtCore.QRectF()
        self.setZValue(5)
        self.setData(data, levels)

    def setData(self, data: np.ndarray, levels: np.ndarray) -> None:
        self._data = np.asarray(data, dtype=float)
        self._levels = np.asarray(levels, dtype=float)
        self._raster = self._data.size >= _RASTER_MIN_PIXELS
        self._smooth = None
        if self._raster:
            self._render()
        else:
            self._rebuild()
        self.informViewBoundsChanged()

    def set_levels(self, levels: np.ndarray) -> None:
        """仅更新级别并重建(小谱路径/大谱光栅化均快速)。"""
        self._levels = np.asarray(levels, dtype=float)
        if self._raster:
            self._render()
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

    # ---- matplotlib 等高线路径(小谱,稳定) ----

    def _rebuild(self) -> None:
        """全量重建:插值数据 + 轮廓路径(小谱首次/换谱时)。"""
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

    # ---- 光栅化路径(大谱,性能) ----

    def _render(self) -> None:
        """把谱强度映射为 RGBA 图像:正峰黑、负峰红,alpha 按级别起点/级数。"""
        data = self._data
        levels = self._levels
        if (
            data is None
            or data.ndim != 2
            or data.size == 0
            or levels is None
            or not len(levels)
        ):
            self._image = None
            self._bounds = QtCore.QRectF()
            return
        height, width = data.shape
        maximum = float(np.max(np.abs(data))) or 1.0
        positive = levels[levels > 0]
        if positive.size:
            base_frac = float(positive.min() / (positive.max() or 1.0))
        else:
            base_frac = 0.0
        count = max(5, int(len(levels) // 2))
        norm = np.abs(data) / maximum
        alpha = np.clip(
            (norm - base_frac) / max(1e-6, 1.0 - base_frac), 0.0, 1.0
        )
        alpha = alpha ** 0.6  # 视觉增强(低强度快速可见)
        alpha = np.ceil(alpha * count) / count  # 按级数量化
        a8 = (alpha * 255).astype(np.uint8)
        img = np.zeros((height, width, 4), dtype=np.uint8)
        pos = data > 0
        neg = data < 0
        img[pos, 3] = a8[pos]  # 正峰黑
        img[neg, 0] = 255  # 负峰红
        img[neg, 3] = a8[neg]
        # 保存字节引用:QImage 不拷贝数据,bytes 被释放会导致悬空段错误
        self._image_data = img.tobytes()
        self._image = QtGui.QImage(
            self._image_data,
            width,
            height,
            width * 4,
            QtGui.QImage.Format.Format_RGBA8888,
        )
        self._bounds = QtCore.QRectF(0.0, 0.0, float(width), float(height))

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds

    def paint(self, painter, *args) -> None:
        if self._image is not None and not self._image.isNull():
            painter.setRenderHint(
                QtGui.QPainter.RenderHint.SmoothPixmapTransform, True
            )
            painter.drawImage(self._bounds, self._image)
            return
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if not self._path.isEmpty():
            painter.setPen(self._pen)
            painter.drawPath(self._path)
        if not self._path_neg.isEmpty():
            painter.setPen(self._pen_neg)
            painter.drawPath(self._path_neg)
