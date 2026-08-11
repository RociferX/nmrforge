"""符号修正原语（FID/谱域符号、axis flip、conjugation）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.processing.axes import axis_index


@dataclass
class SignCorrectionParams:
    sign_flip: bool = False
    conjugate: bool = False
    reverse: bool = False
    axis: str = "F1"


def apply(data: Any, params: SignCorrectionParams) -> np.ndarray:
    """应用符号修正，返回处理后的数据。"""
    arr = np.asarray(data)
    out = arr
    if params.conjugate:
        out = np.conj(out)
    if params.sign_flip:
        out = -out
    if params.reverse:
        out = np.flip(out, axis=axis_index(params.axis, arr.ndim))
    return out
