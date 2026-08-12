"""AutoProcessor：一个数据集从理解到报告的自动编排。

流程（框架 §71）：understand（分类/维度映射/采样检测）→ plan（DAG）→
process（direct）→ optimize（reconstruction/direct/indirect，带预算）→
qc（before/after + 回滚）→ report。
Phase 1-3：NMRPipe 后端（均匀 process / NUS reconstruct_nus）或原生路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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
    phase_report: Any = None  # workflow.phase_optimize.PhaseOptimizeResult


class AutoProcessor:
    """自动化主流程（Phase 1 起逐步实现各步骤）。"""

    def __init__(
        self, backend: ProcessingBackend, budget: OptimizationBudget | None = None
    ) -> None:
        self.backend = backend
        self.budget = budget or OptimizationBudget()

    def run(self, experiment: Experiment) -> RunResult:
        """执行自动处理：NMRPipe 后端可用时走后端，否则走原生读取路径。"""
        provider = getattr(getattr(self.backend, "capabilities", None), "provider", "")
        if provider == "nmrpipe":
            return self.run_backend(experiment)
        if experiment.sampling.mode is SamplingMode.NUS:
            return RunResult(
                status="failed",
                logs=["原生路径暂不支持 NUS（Phase 3 走 NMRPipe 后端）"],
            )
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

    def run_backend(self, experiment: Experiment) -> RunResult:
        """NMRPipe 后端：均匀采样 process / NUS reconstruct_nus，成功后读谱 QC。"""
        plan = select_method(experiment)
        try:
            if experiment.sampling.mode is SamplingMode.NUS:
                result = self.backend.reconstruct_nus(experiment, {})
            else:
                result = self.backend.process(experiment, plan)
        except Exception as exc:  # noqa: BLE001
            return RunResult(status="failed", logs=[f"NMRPipe 后端异常: {exc}"])
        logs = list(result.get("logs", []))
        if not result.get("success"):
            return RunResult(
                status="failed",
                logs=[result.get("message", "NMRPipe 处理失败")] + logs,
            )
        spectrum = (
            Path(result["spectrum_path"]) if result.get("spectrum_path") else None
        )
        phase_report = None
        try:
            from workflow.phase_optimize import default_work_dir, estimate_auto_phase

            work = default_work_dir(experiment, self.backend)
            phase_report = estimate_auto_phase(
                experiment, work, spectrum_path=spectrum
            )
            logs.extend(phase_report.logs)
        except Exception as exc:  # noqa: BLE001
            logs.append(f"自动相位估计跳过: {exc}")
        if spectrum is not None and spectrum.is_file():
            try:
                import nmrglue as ng

                _dic, data = ng.pipe.read(str(spectrum))
                quality = spectrum_quality.evaluate(data)
                return RunResult(
                    status=quality.decision.value,
                    quality=quality,
                    report=spectrum,
                    logs=logs,
                    phase_report=phase_report,
                )
            except Exception as exc:  # noqa: BLE001
                logs.append(f"谱图 QC 读取失败（保留后端结果）: {exc}")
        return RunResult(
            status="success", report=spectrum, logs=logs, phase_report=phase_report
        )

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
