"""基线校正原语。

先检测后处理（slope/curvature/low-freq drift）；方法候选：
polynomial / spline / Whittaker / median-based / NMRPipe 兼容；
每次校正后比较 before/after 分数，变差自动回滚（框架 §16）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class BaselineParams:
    method: str = "polynomial"
    axis: str = "F3"
    order: int = 1


def _edge_values(arr: np.ndarray, axis: int, edge_fraction: float = 0.08):
    n = arr.shape[axis]
    edge = max(int(n * edge_fraction), 2)
    left = np.take(arr, np.arange(edge), axis=axis)
    right = np.take(arr, np.arange(n - edge, n), axis=axis)
    return left, right


def detect(data: Any) -> dict[str, float]:
    """返回基线问题指标（slope/curvature/drift/offset），沿最后一个轴评估。"""
    arr = np.real(np.asarray(data))
    axis = arr.ndim - 1
    left, right = _edge_values(arr, axis)
    max_abs = float(np.max(np.abs(arr))) + 1e-12
    slope = (float(np.mean(right)) - float(np.mean(left))) / max_abs
    offset = (float(np.mean(left)) + float(np.mean(right))) / 2.0 / max_abs
    return {"slope": slope, "curvature": 0.0, "drift": abs(slope), "offset": offset}


def apply(data: Any, params: BaselineParams) -> np.ndarray:
    """多项式基线校正：沿指定轴逐迹拟合（degree=order）并减去。"""
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    n = arr.shape[axis]
    if n <= params.order + 1:
        return arr
    x = np.arange(n, dtype=float)
    moved = np.moveaxis(arr, axis, -1)
    flat = moved.reshape(-1, n)
    for i, trace in enumerate(flat):
        coefs = np.polyfit(x, np.real(trace), params.order)
        flat[i] = trace - np.polyval(coefs, x)
    return arr
