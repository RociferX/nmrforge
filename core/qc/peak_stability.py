"""峰稳定性：跨候选/重建参数比较峰位与强度抖动（框架 §20/§38）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PeakStabilityReport:
    position_jitter: float = 0.0
    intensity_jitter: float = 0.0
    stable_fraction: float = 0.0
    score: float = 0.0
    details: list[dict[str, Any]] = field(default_factory=list)


def compare(peak_sets: list[Any]) -> PeakStabilityReport:
    """比较多组峰列表的稳定性。"""
    raise NotImplementedError("Phase 4: 实现峰稳定性分析")
