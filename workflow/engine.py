"""AutoProcessor：一个数据集从理解到报告的自动编排。

流程（框架 §71）：understand（分类/维度映射/采样检测）→ plan（DAG）→
process（direct）→ optimize（reconstruction/direct/indirect，带预算）→
qc（before/after + 回滚）→ report。
Phase 1：uniform 2D/3D Bruker 数据端到端（run）；NUS 待 Phase 3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.base import ProcessingBackend
from core.data.bruker_reader import BrukerDataError, read_data
from core.data.internal_data_model import Experiment, SamplingMode
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
        """读取 Bruker 数据并执行自动处理（uniform 2D/3D；NUS 待 Phase 3）。"""
        if experiment.sampling.mode is SamplingMode.NUS:
            return RunResult(status="failed", logs=["NUS 数据处理待 Phase 3 接入"])
        try:
            data = read_data(experiment)
        except (BrukerDataError, OSError) as exc:
            return RunResult(status="failed", logs=[f"Bruker 数据读取失败: {exc}"])
        result = self.process_matrix(experiment, data.matrix)
        result.logs.append(
            f"data_file={data.data_file}, byte_order={data.byte_order}, "
            f"layout={data.layout_summary}"
        )
        return result

    def process_matrix(self, experiment: Experiment, data: Any) -> RunResult:
        """最小闭环：默认计划 → 管线执行 → QC（uniform 2D/3D 矩阵数据）。"""
        plan = select_method(experiment)
        runner = PipelineRunner(plan.dag, input_data=data)
        outputs = runner.run()
        failed = [
            nid
            for nid, node in plan.dag.nodes.items()
            if node.status is NodeStatus.FAILED
        ]
        if failed:
            messages = "; ".join(
                f"{nid}: {plan.dag.nodes[nid].message}" for nid in failed
            )
            return RunResult(
                status="failed",
                logs=[f"管线节点失败: {messages}"],
                cache_hits=runner.cache_hits,
            )
        final = outputs[plan.dag.execution_order()[-1]]
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
