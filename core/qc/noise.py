"""Noise estimation: avoid mistaking real peaks for noise.

Methods: user-specified blank regions / edge regions / robust MAD / histogram /
local noise map / peak-masked. Outputs global_sigma / local_sigma / noise_confidence
(framework §17). Phase 1: robust MAD (insensitive to strong peaks).
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
    """Estimate the noise level (robust MAD, scaled by 1.4826 to a Gaussian sigma)."""
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
