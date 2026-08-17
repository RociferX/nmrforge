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


def _axis_net_absorption(path: str, axis: int) -> float:
    """候选显示谱沿 axis 动态锁定峰位后,固定迹线净吸收中位数。"""
    import nmrglue as ng

    from workflow.phase_optimize import _trace_indices_fixed, _trace_metrics_median

    _dic, data = ng.pipe.read(path)
    real = np.real(np.asarray(data)).astype(float)
    indices, positions = _trace_indices_fixed(real, axis)
    if not indices:
        return 0.0
    return 50.0 * (_trace_metrics_median(real, axis, indices, positions) + 1.0)


def estimate_axis_phase_ht(
    backend: Any,
    spectrum_path: Path | str,
    axis: int,
    *,
    work_dir: Path | str | None = None,
    coarse_p0_step: float = 30.0,
) -> tuple[float, float, float] | None:
    """对任意谱轴用 nmrPipe PS -ht 候选 + 动态峰锁定评分估计相位。"""
    path = Path(spectrum_path)

    def score(p0: float, p1: float) -> float:
        resp = backend.phase_ht_candidate_axis(
            path, axis, p0, p1, work_dir=work_dir
        )
        if not resp.get("success") or not resp.get("spectrum_path"):
            return 0.0
        return _axis_net_absorption(str(resp["spectrum_path"]), -1)

    best = None
    for p0 in np.arange(0.0, 360.0, coarse_p0_step):
        s = score(float(p0), 0.0)
        if best is None or s > best[0]:
            best = (s, float(p0), 0.0)
    assert best is not None
    s, p0, p1 = best
    for _ in range(2):
        for dp0 in (-15.0, -5.0, 0.0, 5.0, 15.0):
            ss = score((p0 + dp0) % 360.0, 0.0)
            if ss > s:
                s, p0, p1 = ss, (p0 + dp0) % 360.0, 0.0
    for dp1 in (-22.5, -10.0, 0.0, 10.0, 22.5):
        ss = score(p0, p1 + dp1)
        if ss > s:
            s, p1 = ss, p1 + dp1
    return p0, p1, s


def estimate_direct_phase_ht(
    backend: Any,
    spectrum_path: Path | str,
    experiment: Experiment,
    *,
    work_dir: Path | str | None = None,
    coarse_p0_step: float = 30.0,
) -> tuple[float, float, float] | None:
    """用 nmrPipe PS -ht 候选 + 进阶版固定迹线评分估计直接维相位。

    不依赖 numpy 模拟旋转,与 nmrDraw/真实 PS 同源。
    """
    import nmrglue as ng

    from workflow.display_hybrid_optimize import direct_axis_from_header
    from workflow.phase_optimize import (
        _score_fixed_traces,
        _spectrum_real,
        _trace_indices_fixed,
        _trace_metrics_median,
    )

    path = Path(spectrum_path)
    header, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    real = np.real(arr).astype(float)
    direct = experiment.direct_dimension
    nucleus = direct.nucleus if direct is not None else None
    axis = direct_axis_from_header(dict(header), nucleus)
    axis_name = f"F{axis + 1}"
    indices, positions = _trace_indices_fixed(real, axis)
    if not indices:
        return None

    if np.iscomplexobj(arr):
        # 第一遍谱已保留真实虚部(不加 -di):直接 numpy 频域旋转,与真实后端等价
        complex_arr = arr.astype(np.complex128)
        n = complex_arr.shape[axis]
        ramp_shape = [1] * complex_arr.ndim
        ramp_shape[axis] = n

        def score(p0: float, p1: float) -> float:
            k = np.arange(n, dtype=float)
            ramp = np.exp(
                1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1))
            ).reshape(ramp_shape)
            rotated = np.real(complex_arr * ramp)
            return 50.0 * (
                _trace_metrics_median(rotated, axis, indices, positions) + 1.0
            )
    else:

        def score(p0: float, p1: float) -> float:
            resp = backend.phase_ht_candidate(
                path, p0, p1, work_dir=work_dir
            )
            if not resp.get("success") or not resp.get("spectrum_path"):
                return 0.0
            candidate_real = _spectrum_real(str(resp["spectrum_path"]))
            candidate_indices, candidate_positions = _trace_indices_fixed(
                candidate_real, axis
            )
            if not candidate_indices:
                candidate_indices, candidate_positions = indices, positions
            return _score_fixed_traces(
                str(resp["spectrum_path"]),
                axis_name,
                candidate_indices,
                candidate_positions,
            )[0]

    best = None
    for p0 in np.arange(0.0, 360.0, coarse_p0_step):
        s = score(float(p0), 0.0)
        if best is None or s > best[0]:
            best = (s, float(p0), 0.0)
    assert best is not None
    s, p0, p1 = best
    for _ in range(2):
        for dp0 in (-15.0, -5.0, 0.0, 5.0, 15.0):
            ss = score((p0 + dp0) % 360.0, 0.0)
            if ss > s:
                s, p0, p1 = ss, (p0 + dp0) % 360.0, 0.0
    for dp1 in (-22.5, -10.0, 0.0, 10.0, 22.5):
        ss = score(p0, p1 + dp1)
        if ss > s:
            s, p1 = ss, p1 + dp1
    return p0, p1, s


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
        params_first["keep_direct_complex"] = True
        first = backend.process(experiment, plan or select_method(experiment), params=params_first)
    if not first.get("success") or not first.get("spectrum_path"):
        raise RuntimeError(f"第一遍处理失败: {first.get('message')}")
    import nmrglue as ng

    from workflow.display_hybrid_optimize import direct_axis_from_header

    try:
        _header, _data = ng.pipe.read(str(first["spectrum_path"]))
        ndim = int(_header.get("FDDIMCOUNT", 2) or 2)
        direct_nucleus = (
            experiment.direct_dimension.nucleus if experiment.direct_dimension else None
        )
        direct_axis = direct_axis_from_header(dict(_header), direct_nucleus)
    except Exception:  # noqa: BLE001 - 假后端/占位谱回退按维度推断
        ndim = experiment.ndim
        direct_axis = ndim - 1
    phases: dict[str, tuple[float, float]] = {}
    for ax in range(ndim):
        if ax == direct_axis:
            est = estimate_direct_phase_ht(
                backend, first["spectrum_path"], experiment, work_dir=work_dir
            )
        else:
            est = estimate_axis_phase_ht(
                backend, first["spectrum_path"], ax, work_dir=work_dir
            )
        if est is not None:
            phases[f"F{ax + 1}"] = (est[0], est[1])

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
