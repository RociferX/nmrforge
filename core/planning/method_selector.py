"""方法选择：根据实验类型与数据特征选择处理策略（窗口/相位/基线/校准方法）。

Phase 1：默认策略 = 每个维度一条 apodization → ZF → FT → phase 链（uniform 2D/3D）；
FT 的 alt 标志按 FnMODE 自动推断。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment
from core.experiment.acquisition_mode_detector import ft_alt_for
from core.planning.dependency_graph import PlanNode, ProcessingDag
from core.planning.processing_plan import ProcessingPlan


def _fnmode_for(experiment: Experiment, axis: str) -> int:
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    block = experiment.acquisition_parameters.get(mapping.get(axis, ""), {})
    try:
        return int(block.get("FnMODE", 0) or 0)
    except (TypeError, ValueError):
        return 0


def select_method(experiment: Experiment) -> ProcessingPlan:
    """生成默认处理计划：每个维度一条 SP→ZF→FT→PS 链（uniform 2D/3D）。"""
    plan = ProcessingPlan(
        experiment_id=experiment.dataset_id,
        confidence=experiment.experiment_type.confidence,
    )
    dag = ProcessingDag()
    axes = [d.logical_axis for d in experiment.dimensions]
    for axis in axes:
        prev: str | None = None
        steps = [
            (
                "apodization",
                {
                    "window": "sine_bell",
                    "axis": axis,
                    "params": {"off": 0.45, "end": 0.95, "pow": 1, "c": 0.5},
                },
            ),
            ("zero_fill", {"size": "auto", "axis": axis}),
            (
                "ft",
                {
                    "axis": axis,
                    "alt": ft_alt_for(_fnmode_for(experiment, axis)),
                    "neg": False,
                },
            ),
            (
                "phase",
                {
                    "p0": 0.0,
                    "p1": 0.0,
                    "axis": axis,
                    "source": "preset",
                    "confidence": 0.0,
                },
            ),
        ]
        for op, params in steps:
            node_id = f"{op}_{axis}"
            dag.add_node(
                PlanNode(
                    id=node_id,
                    operation=op,
                    params=params,
                    depends_on=[prev] if prev else [],
                )
            )
            prev = node_id
    plan.dag = dag
    plan.method_choices = {"strategy": "default_direct_first"}
    plan.rationale = {
        "strategy": "uniform 2D/3D：逐维 apodization→ZF→FT→phase；NUS reconstruction 待 Phase 3"
    }
    return plan
