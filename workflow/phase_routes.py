"""简单/进阶两条相位优化途径。

- 简单途径(新方法):先正常处理一遍得到实型谱,在每维显示层做希尔伯特
  调相,再把每维相位填回脚本重生成一次;所有谱型统一。
- 进阶途径(旧方法):uniform 谱所有维度重跑后端优化;NUS 谱采用混合方案
  ——直接维走显示层,间接维走逐候选后端 finalize。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment, SamplingMode
from core.optimization.display_phase_engine import inspect_spectrum
from core.planning.method_selector import select_method
from workflow.display_hybrid_optimize import optimize_nus_hybrid


def axis_to_logical(experiment: Experiment, axis: int) -> str:
    """把谱数组轴索引映射为逻辑轴名(F1/F2/F3)。"""
    dims = [dim.logical_axis for dim in reversed(experiment.dimensions)]
    return dims[axis]


def estimate_all_axes(
    spectrum_path: Path | str,
    experiment: Experiment,
) -> dict[str, tuple[float, float]]:
    """读实型谱,逐维做显示层相位估计,返回 {逻辑轴: (p0, p1)}。"""
    import nmrglue as ng

    path = Path(spectrum_path)
    if not path.is_file():
        return {}
    header, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    phases: dict[str, tuple[float, float]] = {}
    if arr.ndim < 2:
        return phases
    inspected = inspect_spectrum(arr)
    for estimate in inspected["phases"]:
        if estimate is None:
            continue
        # NMRPipe 谱头约定:数组轴 0/1/2 对应 F1/F2/F3
        logical = f"F{estimate.axis + 1}"
        phases[logical] = (estimate.p0, estimate.p1)
    return phases


def simple_route(
    experiment: Experiment,
    backend: Any,
    *,
    plan: Any | None = None,
    work_dir: Path | str | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """简单途径:先跑一遍 → 显示层逐维调相 → 填回重跑一遍。"""
    is_nus = experiment.sampling.mode is SamplingMode.NUS
    params_first = dict(base_params or {})
    params_first.update({"direct_phase_search": False, "display_phase_search": False})
    if is_nus:
        first = backend.reconstruct_nus(experiment, params_first)
    else:
        first = backend.process(experiment, plan or select_method(experiment), params=params_first)
    if not first.get("success") or not first.get("spectrum_path"):
        raise RuntimeError(f"第一遍处理失败: {first.get('message')}")
    display_path = first["spectrum_path"]
    hilbert = getattr(backend, "hilbert_spectrum", None)
    if callable(hilbert):
        ht = hilbert(
            first["spectrum_path"],
            work_dir=work_dir,
        )
        if ht.get("success") and ht.get("spectrum_path"):
            display_path = ht["spectrum_path"]
    phases = estimate_all_axes(display_path, experiment)

    if is_nus:
        direct = experiment.direct_dimension.logical_axis if experiment.direct_dimension else "F2"
        direct_phase = phases.get(direct, (0.0, 0.0))
        params_second = dict(base_params or {})
        params_second.update(
            {
                "direct_phase_override": direct_phase,
                "direct_phase_search": False,
                "display_phase_search": False,
            }
        )
        second = backend.reconstruct_nus(experiment, params_second)
        indirect_phases = {
            axis: value for axis, value in phases.items() if axis != direct
        }
        final = backend.finalize_nus(
            experiment,
            phases=indirect_phases,
            work_dir=work_dir,
        )
        spectrum_path = final.get("spectrum_path")
        backend_runs = 2 + 1
    else:
        params_second = dict(base_params or {})
        params_second["direct_phase_search"] = False
        second = backend.process(
            experiment,
            plan or select_method(experiment),
            direct_phase_override=dict(phases),
            params=params_second,
        )
        spectrum_path = second.get("spectrum_path")
        backend_runs = 2
    return {
        "phases": phases,
        "spectrum_path": spectrum_path,
        "backend_runs": backend_runs,
        "logs": [],
    }


def advanced_route(
    experiment: Experiment,
    backend: Any,
    *,
    plan: Any | None = None,
    work_dir: Path | str | None = None,
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """进阶途径:uniform 全维度后端优化;NUS 混合优化。"""
    is_nus = experiment.sampling.mode is SamplingMode.NUS
    if is_nus:
        from workflow.phase_optimize import _default_phase_score

        return optimize_nus_hybrid(
            experiment,
            backend,
            score_fn=score_fn or _default_phase_score,
            work_dir=work_dir,
            base_params=base_params,
        )
    from workflow.phase_optimize import optimize_phase_sequential

    result = optimize_phase_sequential(experiment, backend, work_dir=work_dir)
    return {
        "phases": dict(result.phases),
        "spectrum_path": result.spectrum_path,
        "backend_runs": result.backend_runs,
        "logs": result.logs,
    }
