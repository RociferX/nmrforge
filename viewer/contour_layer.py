"""pyqtgraph 轮廓图层(光栅化渲染,正黑负红)。

Poky/nmrDraw 风格:按强度把谱渲染为 RGBA 图像(正峰黑、负峰红,alpha
随强度与级别起点),缩放/平移由 Qt 快速重采样;级别/级数滑块只触发
numpy 向量化重渲染,避免 matplotlib 等高线几何计算的卡顿
(旧实现 512x1024 谱加载约 10 秒,光栅化后毫秒级)。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui


class ContourLayer(pg.GraphicsObject):
    """把二维数据渲染为正黑负红强度图;坐标即数据点下标(与 ppm 轴对应)。"""

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
            else pg.mkPen("#e74c3c", width=1),
        )
        self._data: np.ndarray | None = None
        self._levels: np.ndarray | None = None
        self._image: QtGui.QImage | None = None
        self._image_data = b""  # QImage 引用该内存,必须持有引用
        self._bounds = QtCore.QRectF()
        self.setZValue(5)
        self.setData(data, levels)

    def setData(self, data: np.ndarray, levels: np.ndarray) -> None:
        self._data = np.asarray(data, dtype=float)
        self._levels = np.asarray(levels, dtype=float)
        self._render()
        self.informViewBoundsChanged()

    def set_levels(self, levels: np.ndarray) -> None:
        """仅更新级别并重渲染图像(起点/级数由 levels 推导,快速)。"""
        self._levels = np.asarray(levels, dtype=float)
        self._render()
        self.update()

    def setPen(self, pen, neg_pen=None) -> None:
        self._pen = pg.mkPen(pen)
        if neg_pen is not None:
            self._pen_neg = pg.mkPen(neg_pen)
        self.update()

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
        painter.setRenderHint(
            QtGui.QPainter.RenderHint.SmoothPixmapTransform, True
        )
        if self._image is not None and not self._image.isNull():
            painter.drawImage(self._bounds, self._image)
