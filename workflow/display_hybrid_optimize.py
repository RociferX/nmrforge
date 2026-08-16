"""NUS 混合相位优化编排(直接维显示层 + 间接维真实后端)。

流程与人工 nmrDraw 一致,并复用旧方法对间接维的逐候选后端优化:

1. 先正常重构一遍(直接维 PS(0,0)),得到实型终谱;
2. 在终谱的直接维上做希尔伯特显示层调相,记录 (p0, p1);
3. 用记录的直接维相位重跑 SMILE 重构(得到带正确直接维相位的复型平面);
4. 以这些复型平面为起点,对每个间接维逐候选跑 finalize 并评分,选最优;
5. 用所有最优相位跑最后一次 finalize,生成良谱。

这里不依赖现有 workflow.phase_optimize,以便独立验证这条统一编排。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import AxisRole, Experiment
from core.optimization.display_phase_engine import search_axis_phase


def direct_axis_from_header(header: dict[str, Any], nucleus: str | None) -> int:
    """从 NMRPipe 谱头找直接维所在的数组轴(0-based)。

    NMRPipe 头用 FDF1LABEL/FDF2LABEL/FDF3LABEL 表示 (F1, F2, F3);
    谱数组轴顺序与 FDF1/FDF2/FDF3 一致,因此匹配核素标签即可。
    """
    nucleus = (nucleus or "").upper()
    for label, index in (("FDF1LABEL", 0), ("FDF2LABEL", 1), ("FDF3LABEL", 2)):
        value = str(header.get(label, "")).upper()
        if nucleus and value == nucleus:
            return index
    return 1  # 2D 直接维默认 F2 轴,3D 回退到中间轴(避免误用最后一维)


def estimate_direct_phase(
    spectrum_path: Path | str,
    experiment: Experiment,
) -> tuple[float, float, float] | None:
    """读实型终谱,在直接维上做显示层相位估计。"""
    import nmrglue as ng

    path = Path(spectrum_path)
    if not path.is_file():
        return None
    header, data = ng.pipe.read(str(path))
    arr = np.asarray(data, dtype=float)
    if arr.ndim < 2:
        return None
    direct = experiment.direct_dimension
    nucleus = direct.nucleus if direct is not None else None
    axis = direct_axis_from_header(dict(header), nucleus)
    estimate = search_axis_phase(arr, axis=axis)
    if estimate is None:
        return None
    return estimate.p0, estimate.p1, estimate.score


def optimize_nus_hybrid(
    experiment: Experiment,
    backend: Any,
    *,
    p0_values: tuple[float, ...] = tuple(float(v) for v in range(0, 360, 30)),
    p1_values: tuple[float, ...] = (-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0),
    score_fn: Callable[[str], tuple[float, dict[str, float]]] | None = None,
    work_dir: Path | str | None = None,
    base_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行 NUS 混合相位优化,返回 phases/backend_runs/spectrum_path/logs。"""
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    indirect_axes = [
        dim.logical_axis
        for dim in experiment.dimensions
        if dim.role is not AxisRole.DIRECT
    ]
    logs: list[str] = []
    backend_runs = 0

    params_first = dict(base_params or {})
    params_first.update(
        {"direct_phase_search": False, "display_phase_search": False}
    )
    first = backend.reconstruct_nus(experiment, params_first)
    backend_runs += 1
    if not first.get("success") or not first.get("spectrum_path"):
        raise RuntimeError(f"第一遍 NUS 重构失败: {first.get('message')}")

    direct_est = estimate_direct_phase(
        Path(first["spectrum_path"]), experiment
    )
    if direct_est is None:
        logs.append("直接维显示层相位估计失败,保持 (0,0)")
        direct_p0, direct_p1 = 0.0, 0.0
    else:
        direct_p0, direct_p1, _score = direct_est
        logs.append(f"直接维显示层相位: {direct_axis}=({direct_p0:g}, {direct_p1:g})")

    params_second = dict(base_params or {})
    params_second.update(
        {
            "direct_phase_override": (direct_p0, direct_p1),
            "direct_phase_search": False,
            "display_phase_search": False,
        }
    )
    second = backend.reconstruct_nus(experiment, params_second)
    backend_runs += 1
    if not second.get("success"):
        raise RuntimeError(f"应用直接维相位的 SMILE 重构失败: {second.get('message')}")
    logs.append("SMILE 已按直接维显示层相位重跑")

    fixed: dict[str, tuple[float, float]] = {}
    candidate_count = 0
    for axis in indirect_axes:
        best: tuple[float, tuple[float, float], str] | None = None
        for p0 in p0_values:
            for p1 in p1_values:
                phases = dict(fixed)
                phases[axis] = (float(p0), float(p1))
                resp = backend.finalize_nus(
                    experiment,
                    phases=phases,
                    work_dir=work_dir,
                    params={"zero_fill": {"mode": "none"}},
                )
                backend_runs += 1
                candidate_count += 1
                if not resp.get("success") or not resp.get("spectrum_path"):
                    continue
                try:
                    score, _components = score_fn(str(resp["spectrum_path"]))
                except Exception as exc:  # noqa: BLE001
                    logs.append(f"{axis} 候选评分失败: {exc}")
                    continue
                if best is None or score > best[0]:
                    best = (float(score), (float(p0), float(p1)), str(resp["spectrum_path"]))
        if best is None:
            fixed[axis] = (0.0, 0.0)
            logs.append(f"{axis}: 无可用候选,回退 (0,0)")
        else:
            fixed[axis] = best[1]
            logs.append(f"{axis}: 最优 {best[1]} score={best[0]:.2f}")

    final = backend.finalize_nus(experiment, phases=fixed, work_dir=work_dir)
    backend_runs += 1
    if not final.get("success"):
        raise RuntimeError(f"最终 finalize 失败: {final.get('message')}")

    return {
        "phases": fixed,
        "backend_runs": backend_runs,
        "candidates_scored": candidate_count,
        "spectrum_path": final.get("spectrum_path"),
        "direct_phase": (direct_p0, direct_p1),
        "logs": logs,
    }
