"""NMR 查看器专用 ViewBox:框选缩放(RectMode)+ 中键拖动平移 + 滚轮缩放。"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore


class NMRViewBox(pg.ViewBox):
    """在 RectMode 下保留「按住中键拖动平移」的手势。

    pyqtgraph 的 RectMode 会把中键也当作框选缩放;且其平移分支
    ``QPointF * numpy 数组`` 在 PyQt6 下会 TypeError,因此自行计算平移。
    """

    def mouseDragEvent(self, ev, axis=None) -> None:
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
