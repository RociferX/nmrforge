"""傅里叶变换处理原语（含 -alt/-neg/翻转标志，框架 §10/§6.1）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class FtParams:
    axis: str = "F3"
    alt: bool = False
    neg: bool = False


def apply(data: Any, params: FtParams) -> np.ndarray:
    """沿指定轴做复 FFT。

    约定：使用 numpy FFT 的指数符号；alt=True 先做 ±交替符号修正
    （TPPI/States-TPPI）；neg=True FT 后翻转该轴。
    """
    arr = np.asarray(data)
    axis = axis_index(params.axis, arr.ndim)
    work = arr.copy()
    if params.alt:
        n = arr.shape[axis]
        sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        shape = [1] * arr.ndim
        shape[axis] = n
        work = work * sign.reshape(shape)
    out = np.fft.fft(work, axis=axis)
    if params.neg:
        out = np.flip(out, axis=axis)
    return out
