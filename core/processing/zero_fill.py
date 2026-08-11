"""Zero filling 处理原语。

区分「采集分辨率」与「数字插值」；候选 1×/2×/4×，避免无意义的大矩阵（框架 §14/§65）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class ZeroFillParams:
    size: str | int = "auto"  # auto：>= 2*当前点数 的最小 2 的幂
    axis: str = "F3"


def apply(data: Any, params: ZeroFillParams) -> np.ndarray:
    """在指定维度补零，返回处理后的数据。"""
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    current = arr.shape[axis]
    if isinstance(params.size, int):
        target = params.size
    elif params.size == "auto":
        target = 1
        while target < 2 * current:
            target *= 2
    else:
        target = int(params.size)
    if target <= current:
        return arr
    pad = [(0, 0)] * arr.ndim
    pad[axis] = (0, target - current)
    return np.pad(arr, pad, mode="constant")
