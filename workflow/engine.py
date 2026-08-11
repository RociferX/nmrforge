"""AutoProcessor：一个数据集从理解到报告的自动编排。

流程（框架 §71）：understand（分类/维度映射/采样检测）→ plan（DAG）→
process（direct）→ optimize（reconstruction/direct/indirect，带预算）→
qc（before/after + 回滚）→ report。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.base import ProcessingBackend
from core.data.internal_data_model import Experiment
from core.optimization.parameter_space import OptimizationBudget
from core.qc.spectrum_quality import QualityResult


@dataclass
class RunResult:
    status: str = "pending"  # success / warning / failed / skipped
    report: Any = None
    quality: QualityResult | None = None
    logs: list[str] = field(default_factory=list)
    cache_hits: int = 0


class AutoProcessor:
    """自动化主流程（Phase 1 起逐步实现各步骤）。"""

    def __init__(
        self, backend: ProcessingBackend, budget: OptimizationBudget | None = None
    ) -> None:
        self.backend = backend
        self.budget = budget or OptimizationBudget()

    def run(self, experiment: Experiment) -> RunResult:
        """执行完整自动处理流程。"""
        raise NotImplementedError("Phase 1: 实现 AutoProcessor.run")
