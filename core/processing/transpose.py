"""转置原语（处理轴重排）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class TransposeParams:
    order: tuple[str, ...] = ("F1", "F2", "F3")


def apply(data: Any, params: TransposeParams) -> np.ndarray:
    """按 order（逻辑轴）重排数据轴。"""
    arr = np.asarray(data)
    expected = {f"F{i + 1}" for i in range(arr.ndim)}
    if len(params.order) != arr.ndim or set(params.order) != expected:
        raise ValueError(f"order {params.order} 与数据维度 {arr.ndim} 不匹配")
    axes = [axis_index(a, arr.ndim) for a in params.order]
    return np.transpose(arr, axes)
