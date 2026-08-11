"""噪声估计：避免把真实峰当噪声。

方法：用户指定空白区 / edge regions / robust MAD / histogram / local noise map / peak-masked。
输出 global_sigma / local_sigma / noise_confidence（框架 §17）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NoiseEstimate:
    global_sigma: float = 0.0
    local_sigma: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    method: str = ""


def estimate(data: Any) -> NoiseEstimate:
    """估计噪声水平。"""
    raise NotImplementedError("Phase 1: 实现噪声估计")
