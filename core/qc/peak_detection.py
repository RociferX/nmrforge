"""峰检测（用于 QC 与峰表，不是 assignment，框架 §19）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter

from core.qc import noise


@dataclass
class Peak:
    position: tuple[float, ...] = ()
    height: float = 0.0
    volume: float = 0.0
    width: tuple[float, ...] = ()
    snr: float = 0.0


@dataclass
class PeakDetectionParams:
    sigma_multiplier: float = 3.0
    min_snr: float = 3.0
    neighborhood: int = 3


def detect(data: Any, params: PeakDetectionParams | None = None) -> list[Peak]:
    """局部极大值 + 强度>噪声×sigma + S/N 阈值（2D/3D 通用）。"""
    arr = np.asarray(data)
    params = params or PeakDetectionParams()
    sigma = noise.estimate(arr).global_sigma
    real = np.real(arr)
    footprint = np.ones([params.neighborhood] * real.ndim, dtype=bool)
    maxima = maximum_filter(real, footprint=footprint, mode="constant")
    mask = (real == maxima) & (real > sigma * params.sigma_multiplier)
    peaks: list[Peak] = []
    for idx in np.argwhere(mask):
        value = float(real[tuple(idx)])
        snr_value = value / sigma if sigma > 0 else 0.0
        if snr_value >= params.min_snr:
            peaks.append(
                Peak(
                    position=tuple(float(i) for i in idx),
                    height=value,
                    snr=snr_value,
                )
            )
    peaks.sort(key=lambda p: p.height, reverse=True)
    return peaks
