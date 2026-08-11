"""SNR 指标：peak_height/sigma 与 peak_volume/noise；区分 global / median / top（框架 §18）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.qc import noise, peak_detection


@dataclass
class SnrMetrics:
    global_snr: float = 0.0
    median_peak_snr: float = 0.0
    top_peak_snr: float = 0.0


def compute(
    data: Any,
    peaks: list[peak_detection.Peak] | None = None,
    sigma: float | None = None,
) -> SnrMetrics:
    """计算全局 / 中位 / top10% 峰 SNR。"""
    arr = np.asarray(data)
    sigma = sigma if sigma is not None else noise.estimate(arr).global_sigma
    peaks = peaks if peaks is not None else peak_detection.detect(arr)
    snrs = sorted((p.snr for p in peaks), reverse=True)
    if not snrs:
        return SnrMetrics()
    top_n = max(1, len(snrs) // 10)
    return SnrMetrics(
        global_snr=float(snrs[0]),
        median_peak_snr=float(np.median(snrs)),
        top_peak_snr=float(np.mean(snrs[:top_n])),
    )
