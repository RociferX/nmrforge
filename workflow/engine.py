"""AutoProcessor：一个数据集从理解到报告的自动编排。

流程（框架 §71）：understand（分类/维度映射/采样检测）→ plan（DAG）→
process（direct）→ optimize（reconstruction/direct/indirect，带预算）→
qc（before/after + 回滚）→ report。
Phase 1：uniform 2D/3D 矩阵数据的最小闭环（process_matrix）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.base import ProcessingBackend
from core.data.internal_data_model import Experiment
from core.optimization.parameter_space import OptimizationBudget
from core.planning.dependency_graph import NodeStatus
from core.planning.method_selector import select_method
from core.qc import spectrum_quality
from core.qc.spectrum_quality import QualityResult
from workflow.pipeline import PipelineRunner


@dataclass
class RunResult:
    status: str = "pending"  # accept / warning / rollback / failed / skipped
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
        """完整自动流程：等待 Bruker 二进制数据读取/后端转换接入。"""
        raise NotImplementedError("等待 Bruker 数据读取与后端转换接入（NMRPipe bruker -AUTO）")

    def process_matrix(self, experiment: Experiment, data: Any) -> RunResult:
        """最小闭环：默认计划 → 管线执行 → QC（uniform 2D/3D 矩阵数据）。"""
        plan = select_method(experiment)
        runner = PipelineRunner(plan.dag, input_data=data)
        outputs = runner.run()
        final = data
        if plan.dag.nodes:
            last = plan.dag.execution_order()[-1]
            if plan.dag.nodes[last].status is NodeStatus.SUCCESS:
                final = outputs[last]
        quality = spectrum_quality.evaluate(final)
        result = RunResult(
            status=quality.decision.value,
            quality=quality,
            cache_hits=runner.cache_hits,
        )
        result.logs.append(
            f"plan confidence={plan.confidence:.2f}, nodes={len(plan.dag.nodes)}, "
            f"cache_hits={runner.cache_hits}"
        )
        return result
