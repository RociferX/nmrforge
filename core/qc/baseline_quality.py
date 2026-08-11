"""基线质量：slope / curvature / low-freq drift / 无峰区偏差 / 残差偏置（框架 §16）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class BaselineQuality:
    slope: float = 0.0
    curvature: float = 0.0
    drift: float = 0.0
    offset: float = 0.0
    score: float = 0.0
    needs_correction: bool = False


def evaluate(data: Any) -> BaselineQuality:
    """评估基线质量（沿最后一个轴取两端与中部均值）。"""
    arr = np.asarray(data)
    real = np.real(arr)
    max_abs = float(np.max(np.abs(real))) + 1e-12
    axis = real.ndim - 1
    n = real.shape[axis]
    edge = max(int(n * 0.08), 2)
    left = float(np.mean(np.take(real, np.arange(edge), axis=axis)))
    right = float(np.mean(np.take(real, np.arange(n - edge, n), axis=axis)))
    center = slice(n // 2 - edge, n // 2 + edge)
    mid = float(np.mean(np.take(real, np.arange(center.start, center.stop), axis=axis)))
    slope = (right - left) / max_abs
    offset = ((left + right) / 2.0) / max_abs
    curvature = abs(left + right - 2.0 * mid) / max_abs
    score = float(
        np.clip(
            100.0 * (1.0 - min(1.0, abs(slope) * 4.0 + abs(offset) * 2.0 + curvature * 6.0)),
            0.0,
            100.0,
        )
    )
    needs = abs(offset) > 0.02 or abs(slope) > 0.05 or curvature > 0.05
    return BaselineQuality(
        slope=float(slope),
        curvature=float(curvature),
        drift=float(abs(slope)),
        offset=float(offset),
        score=score,
        needs_correction=bool(needs),
    )
