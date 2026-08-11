"""化学位移校准。

保存 SF/SW/carrier/O1/O1P/reference；不把 O1 简单当作中心；
记录 calibration source 与 reference method/value（框架 §21）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class CalibrationParams:
    source: str = "acquisition"  # acquisition / reference_compound / user / known_peak
    reference_ppm: float = 0.0
    reference_value: float = 0.0


def ppm_axis(
    size: int,
    sf: float,
    sw: float,
    o1: float = 0.0,
    o1p: float = 0.0,
    decreasing: bool = True,
) -> np.ndarray:
    """生成与索引对应的 ppm 轴（NMRPipe 约定 FDF*ORIG：索引递减）。

    ppm_i = o1p + (center - i) * (sw / size) / sf
    """
    hz_per_point = sw / size
    center = size / 2.0
    idx = np.arange(size)
    if decreasing:
        return o1p + (center - idx) * hz_per_point / sf
    return o1p + (idx - center) * hz_per_point / sf


def apply(axis_ppm: object, params: CalibrationParams) -> np.ndarray:
    """应用参考偏移：把参考点移动到 reference_ppm。"""
    arr = np.asarray(axis_ppm)
    return arr + (params.reference_ppm - params.reference_value)
