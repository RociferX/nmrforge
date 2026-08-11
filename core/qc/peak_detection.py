"""峰检测（用于 QC 与峰表，不是 assignment，框架 §19）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    """检测峰列表。"""
    raise NotImplementedError("Phase 1: 实现峰检测")
