"""Method selection: choose the processing strategy (window/phase/baseline/calibration
methods) from the experiment type and the data characteristics.

Phase 1: the default strategy is a per-dimension apodization -> ZF -> FT -> phase chain
(uniform 2D/3D), whose chained dependencies guarantee that every dimension ends up in the
final output; when the indirect dimension is hypercomplex (States/States-TPPI/
Echo-Antiecho) a combine node is inserted first.
"""

from __future__ import annotations

from core.data.internal_data_model import AxisRole, Experiment
from core.experiment.acquisition_mode_detector import (
    ft_alt_for,
    ft_kind_for,
    ft_neg_for,
)
from core.planning.dependency_graph import PlanNode, ProcessingDag
from core.planning.processing_plan import ProcessingPlan
from ui_support.i18n import tr

# official Bruker TopSpin enum (matching nmrglue): 1/2 = Magnitude (QF/QSEQ, not
# hypercomplex so there is nothing to combine), 3 = TPPI (real), 4 = States, 5 = States-TPPI,
# 6 = Echo-Antiecho
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
    """Build the default processing plan: per dimension SP -> ZF -> FT -> PS -> POLY (baseline
    correction, auto on every dimension by default).

    baseline holds the per-axis shape {axis: {enabled, mode, order}}; the default is
    mode="auto" on every dimension (POLY -auto, aligned with a hand-written xy.com). The
    direct 1H dimension can be configured with enabled=False to avoid over-correcting the
    water region.
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
        kind = ft_kind_for(fnmode) if dim.role is not AxisRole.DIRECT else "complex"
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
                    "kind": kind,
                },
            ),
        ]
        if kind != "magnitude":
            # magnitude (QF) has no phase concept: take the modulus right after the FT
            steps.append(
                (
                    "phase",
                    {
                        "p0": 0.0,
                        "p1": 0.0,
                        "axis": axis,
                        "source": "preset",
                        "confidence": 0.0,
                    },
                )
            )
        if kind == "magnitude":
            steps.append(("magnitude", {"axis": axis}))
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
        "strategy": tr(
            "uniform 2D/3D: per-dimension apodisation, ZF, FT, phase; hypercomplex indirect "
            "dimensions are merged first (States/States-TPPI); Echo-Antiecho is better served by "
            "the NMRPipe "
            "backend",
        )
    }
    return plan
