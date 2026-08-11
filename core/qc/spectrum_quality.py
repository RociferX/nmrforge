"""综合谱质量：把各 QC 分量汇总为 QualityScore，
并在处理前后比较决定 ACCEPT/ROLLBACK（框架 §47）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.optimization.scoring import QualityScore


class QcDecision(str, Enum):
    ACCEPT = "accept"
    WARNING = "warning"
    ROLLBACK = "rollback"


@dataclass
class QualityResult:
    score: QualityScore = field(default_factory=QualityScore)
    decision: QcDecision = QcDecision.ACCEPT
    reasons: list[str] = field(default_factory=list)


def evaluate(data: Any) -> QualityResult:
    """评估综合谱质量。"""
    raise NotImplementedError("Phase 1: 实现综合质量评估")
