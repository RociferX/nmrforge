"""噪声估计：避免把真实峰当噪声。

方法：用户指定空白区 / edge regions / robust MAD / histogram / local noise map / peak-masked。
输出 global_sigma / local_sigma / noise_confidence（框架 §17）。
Phase 1：robust MAD（对强峰不敏感）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class NoiseEstimate:
    global_sigma: float = 0.0
    local_sigma: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    method: str = ""


def estimate(data: Any) -> NoiseEstimate:
    """估计噪声水平（robust MAD，1.4826 因子换算为高斯 sigma）。"""
    arr = np.asarray(data)
    flat = np.real(arr).ravel()
    if flat.size == 0:
        return NoiseEstimate()
    median = np.median(flat)
    mad = np.median(np.abs(flat - median))
    sigma = float(1.4826 * mad)
    if sigma < 1e-12:
        sigma = float(np.std(flat))
    return NoiseEstimate(global_sigma=sigma, confidence=0.9, method="robust_mad")
