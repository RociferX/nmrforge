"""NMR 查看器专用 ViewBox:框选缩放(RectMode)+ 中键拖动平移 + 滚轮缩放。

pyqtgraph 的 RectMode 会把中键也当作框选缩放;且其平移分支
``QPointF * numpy 数组`` 在 PyQt6 下会 TypeError,因此自行计算平移。
0.2.133:右键不再响应任何行为;视图范围被限制在完整数据范围
(reset_view 得到的最小矩形)之内——缩小不能把完整谱图缩出视野。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore

_MIN_VIEW_RANGE = 8.0


class NMRViewBox(pg.ViewBox):
    """在 RectMode 下保留「按住中键拖动平移」的手势,并限制视图边界。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._min_rect: list[tuple[float, float]] | None = None

    # ---------------------------------------------------------- 鼠标

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

    # ---------------------------------------------------------- 范围

    # ---------------------------------------------------------- 缩放

    def wheelEvent(self, ev, axis=None):
        """滚轮缩放后立即 clamp(缩小不能越过完整范围)。"""
        super().wheelEvent(ev, axis)
        self._clamp_view_range()

    def scaleBy(self, s=None, center=None, **kwargs):
        """缩放(滚轮/缩放动画)后立即 clamp。"""
        super().scaleBy(s=s, center=center, **kwargs)
        self._clamp_view_range()

    def translateBy(self, t=None, **kwargs):
        """平移后立即 clamp(谱图不能被移出视野)。"""
        super().translateBy(t=t, **kwargs)
        self._clamp_view_range()

    def set_full_range(self, x_range, y_range, padding=0) -> None:
        """恢复完整视图并更新缩小基准(reset_view 专用)。

        先更新 _min_rect 再设置范围,避免被旧基准 clamp;之后用户缩小
        视图不能超过该完整范围。
        """
        self._min_rect = [
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        ]
        super().setRange(xRange=x_range, yRange=y_range, padding=padding)

    def setRange(self, rect=None, **kwargs):
        """限制缩小/平移边界(完整范围未记录时不做约束)。"""
        super().setRange(rect=rect, **kwargs)
        if getattr(self, "_min_rect", None) is not None:
            self._clamp_view_range()

    def _clamp_view_range(self) -> None:
        """视图不能越过完整数据范围(由 set_full_range 记录)。

        缩小上限 = 完整范围(长边全部露出即到顶);平移保持视图在完整
        范围之内;单轴最小跨度保护(防过度放大),贴近边界时整体平移
        保持跨度不变。
        """
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
            # 缩小上限:跨度不能超过完整范围(长边全部露出即到顶)
            if span > mspan:
                center = (lo + hi) / 2.0
                lo, hi = center - mspan / 2.0, center + mspan / 2.0
                changed = True
            # 平移不能把谱图移出视野(与完整范围对齐)
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
            # 最小跨度保护(防过度放大):跨度保持 _MIN_VIEW_RANGE
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

