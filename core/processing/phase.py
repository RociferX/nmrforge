"""相位校正原语（p0/p1）。

自动相位优化见 core/optimization/ 与 core/qc/phase_quality；
参数需带 source 与 confidence（框架 §46）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PhaseParams:
    p0: float = 0.0
    p1: float = 0.0
    axis: str = "F3"
    source: str = ""  # automatic_optimizer / user / preset
    confidence: float = 0.0


def apply(data: Any, params: PhaseParams) -> Any:
    """应用相位校正，返回处理后的数据。"""
    raise NotImplementedError("Phase 1: 实现相位校正")
