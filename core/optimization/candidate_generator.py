"""候选参数生成：coarse candidates → local refinement。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.optimization.parameter_space import ParameterSpace


@dataclass
class Candidate:
    """一个候选参数集及其评分/状态。"""

    params: dict[str, Any]
    score: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    source: str = ""  # coarse / local / bayesian / user
    status: str = "pending"  # pending / accepted / rejected / rolled_back


class CandidateGenerator:
    """候选生成器基类。"""

    def coarse(self, space: ParameterSpace) -> list[dict[str, Any]]:
        """生成粗候选。"""
        raise NotImplementedError("Phase 3: 实现粗候选生成")

    def refine(self, space: ParameterSpace, best: dict[str, Any]) -> list[dict[str, Any]]:
        """在最佳候选附近局部细化。"""
        raise NotImplementedError("Phase 3: 实现局部细化")
