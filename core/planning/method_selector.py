"""方法选择：根据实验类型与数据特征选择处理策略（窗口/相位/基线/校准方法）。

Phase 1：默认策略 = 逐维 apodization → ZF → FT → phase 链（uniform 2D/3D），
链式依赖保证各维变换全部合成到最终输出；
间接维若为超复数采集（States/States-TPPI/Echo-Antiecho），先插入合并节点。
"""

from __future__ import annotations

from core.data.internal_data_model import AxisRole, Experiment
from core.experiment.acquisition_mode_detector import ft_alt_for, ft_neg_for
from core.planning.dependency_graph import PlanNode, ProcessingDag
from core.planning.processing_plan import ProcessingPlan

# Bruker TopSpin 官方枚举(与 nmrglue 一致):1/2=Magnitude(QF/QSEQ,
# 非超复数无需合并),3=TPPI(real),4=States,5=States-TPPI,6=Echo-Antiecho
_HYPER_MODE = {
    0: "states",
    4: "states",
    5: "states_tppi",
    6: "echo_antiecho",
}

_MULT_FNMODE = {0, 4, 5, 6}


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


def select_method(
    experiment: Experiment, *, baseline: dict | None = None
) -> ProcessingPlan:
    """生成默认处理计划:逐维 SP→ZF→FT→PS→POLY(基线校正,默认全维 auto)。

    baseline 为逐轴形态 {轴: {enabled, mode, order}},缺省全维
    mode="auto"(POLY -auto,与手工 xy.com 对齐);直接维 1H 可配
    enabled=False 关闭避免水峰区过度校正。
    """
    baseline = baseline or {}
    plan = ProcessingPlan(
        experiment_id=experiment.dataset_id,
        confidence=experiment.experiment_type.confidence,
    )
    dag = ProcessingDag()
    prev: str | None = None
    for dim in experiment.dimensions:
        axis = dim.logical_axis
        fnmode = _fnmode_for(experiment, axis)
        steps: list[tuple[str, dict]] = []
        if dim.role is not AxisRole.DIRECT and fnmode in _MULT_FNMODE:
            steps.append(
                (
                    "combine_hypercomplex",
                    {"axis": axis, "mode": _HYPER_MODE.get(fnmode, "states")},
                )
            )
        steps += [
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
                    "alt": ft_alt_for(fnmode),
                    "neg": ft_neg_for(experiment, fnmode, axis),
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
        baseline_cfg = {"axis": axis, "enabled": True, "mode": "auto", "order": 0}
        baseline_cfg.update(baseline.get(axis, {}))
        steps.append(("baseline", baseline_cfg))
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
        "strategy": "uniform 2D/3D：逐维 apodization→ZF→FT→phase；"
        "超复数间接维先合并（States/States-TPPI）；Echo-Antiecho 建议走 NMRPipe 后端"
    }
    return plan
