"""化学位移校准。

保存 SF/SW/carrier/O1/O1P/reference；不把 O1 简单当作中心；
记录 calibration source 与 reference method/value（框架 §21）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CalibrationParams:
    source: str = "acquisition"  # acquisition / reference_compound / user / known_peak
    reference_ppm: float = 0.0
    reference_value: float = 0.0


def apply(axis_ppm: object, params: CalibrationParams) -> object:
    """对 ppm 轴应用校准偏移，返回校准后的轴。"""
    raise NotImplementedError("Phase 1: 实现化学位移校准")
