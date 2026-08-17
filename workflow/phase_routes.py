"""统一相位优化途径(替代简单/进阶分派,2026-08-17)。

统一方案:第一遍逐维复型预览(仅搜索轴 PS 不加 -di,其它轴按已固定
相位加 -di,零填零)→ 内存调相(旧算法判断标准:固定迹线中位数净吸收,
零额外后端)→ 联合复核 → 完整终跑(窗函数/填零/基线/各维 PS/EXT/-di)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method


def axis_to_logical(experiment: Experiment, axis: int) -> str:
    """把谱数组轴索引映射为逻辑轴名(F1/F2/F3)。"""
    dims = [dim.logical_axis for dim in reversed(experiment.dimensions)]
    return dims[axis]


def _axis_index(axis: str) -> int:
    """逻辑轴名 → 生产布局谱数组下标(F1=0, F2=1, F3=2)。"""
    return {"F1": 0, "F2": 1, "F3": 2}.get(axis, 0)


def _read_complex_preview(path: Path | str) -> np.ndarray:
    """读复型预览文件:nmrglue 直接读为复型则用之,否则按交错实型拆包。"""
    import nmrglue as ng

    from core.data.pipe_io import read_pipe_complex

    path = Path(path)
    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    return read_pipe_complex(path)


def unified_route(
    experiment: Experiment,
    backend: Any,
    *,
    plan: Any | None = None,
    work_dir: Path | str | None = None,
    base_params: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """统一方案(替代简单/进阶分派):第一遍逐维复型预览 → 内存调相
    (旧算法判断标准,零额外后端)→ 联合复核 → 完整终跑。

    uniform:每轴一条生产管道复型预览(仅搜索轴 PS 不加 -di,其它轴按已固定
    相位加 -di,零填零);NUS:SMILE 一次出复型 recon 平面,直接维在平面上
    内存搜索,间接维内存复刻 finalize 链完整搜索;最后完整重跑出良谱。
    """
    from workflow.memory_phase_search import (
        joint_recheck_memory,
        search_axis_memory,
    )
    from workflow.phase_optimize import PHASE_SCORE_FLAT_MARGIN

    plan = plan or select_method(experiment)
    if experiment.sampling.mode is SamplingMode.NUS:
        return _unified_nus(
            experiment,
            backend,
            plan=plan,
            work_dir=work_dir,
            base_params=base_params,
            progress=progress,
        )
    params = dict(base_params or {})
    params.pop("preview_axis", None)
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    axes = [dim.logical_axis for dim in experiment.dimensions]
    # 0.2.75:均匀路径先间接后直接(旧算法顺序,直接维在间接维校正后的谱上锁点)
    search_axes = [a for a in axes if a != direct_axis] + [direct_axis]
    fixed: dict[str, tuple[float, float]] = {}
    axis_arrays: dict[str, np.ndarray] = {}
    axis_index: dict[str, int] = {}
    axis_traces: dict[str, tuple[list[int], list[int]]] = {}
    logs: list[str] = []
    backend_runs = 0
    for axis in search_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        resp = backend.process(
            experiment,
            plan,
            direct_phase_override=dict(fixed) if fixed else None,
            params={**params, "preview_axis": axis},
            out_file=out_file,
            script_name=f"{experiment.dataset_id}_preview_{axis}.com",
            progress=progress,
        )
        backend_runs += 1
        if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(f"复型预览({axis})失败: {resp.get('message')}")
        arr = _read_complex_preview(str(resp["spectrum_path"]))
        ax = _axis_index(axis)
        est = search_axis_memory(arr, ax)
        if est is None:
            raise RuntimeError(f"内存相位搜索({axis})无可用迹线")
        fixed[axis] = est.phase
        axis_arrays[axis] = arr
        axis_index[axis] = ax
        axis_traces[axis] = est.traces
        logs += est.logs
        logs.append(
            f"{axis}: 内存相位 = ({est.phase[0]:g}°, {est.phase[1]:g}°) "
            f"score={est.score:.2f}"
        )
    if len(search_axes) >= 2:
        best, best_score, fixed_score, zero_score = joint_recheck_memory(
            axis_arrays, axis_index, axis_traces, fixed
        )
        if best != fixed and best_score - fixed_score >= PHASE_SCORE_FLAT_MARGIN:
            logs.append(
                f"联合复核: 联合最优 {best} (score={best_score:.2f}) "
                f"优于顺序固定 {fixed} (score={fixed_score:.2f}),已更新"
            )
            fixed = best
        else:
            logs.append(
                f"联合复核: 联合面平坦(顺序 {fixed} score={fixed_score:.2f} "
                f"vs 联合最优 {best} score={best_score:.2f}),保持顺序固定"
            )
    params_final = dict(params)
    resp = backend.process(
        experiment,
        plan,
        direct_phase_override=fixed,
        params=params_final,
        progress=progress,
    )
    backend_runs += 1
    if not resp.get("success") or not resp.get("spectrum_path"):
        raise RuntimeError(f"终跑失败: {resp.get('message')}")
    logs += list(resp.get("logs", []))
    return {
        "phases": fixed,
        "spectrum_path": str(resp["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
        "direct_phase": fixed.get(direct_axis),
    }


def _load_recon_planes(experiment: Experiment, work: Path) -> np.ndarray:
    """读 SMILE 重构复型平面:2D recon.ft1(直接维, F1 时间);
    3D nus3d_rc/test%04d.ft1 交错拆包后按 F1 增量堆叠(直接维, F2 时间, F1 时间)。"""
    from core.data.pipe_io import read_pipe_complex

    if experiment.ndim >= 3:
        plane_dir = work / "nus3d_rc"
        paths = sorted(plane_dir.glob("test*.ft1"))
        if not paths:
            raise RuntimeError(f"缺少 3D 重构平面: {plane_dir}")
        arrays = [read_pipe_complex(path) for path in paths]
        return np.stack(arrays, axis=-1)
    recon = work / "nus2d" / "recon.ft1"
    if not recon.is_file():
        raise RuntimeError(f"缺少 2D 重构平面: {recon}")
    return _read_complex_preview(recon)


def _finalize_substrate_nus(
    planes: np.ndarray,
    experiment: Experiment,
    searched_axis: str,
    fixed: dict[str, tuple[float, float]],
) -> tuple[np.ndarray, int]:
    """内存复刻 finalize 链,输出「搜索轴复型、其它间接轴按 fixed 相位实型」
    的生产布局数组,供逐维内存搜索(与 uniform 预览语义一致)。"""
    from core.experiment.acquisition_mode_detector import ft_alt_for
    from workflow.memory_phase_search import finalize_axis_in_memory

    if experiment.ndim == 2:
        # recon: (direct, f1_time) → FT(-alt) F1 → (f1_freq, direct)
        f1_fnmode = int(
            experiment.acquisition_parameters.get("acqu2s", {}).get("FnMODE", 0) or 0
        )
        arr = finalize_axis_in_memory(
            planes,
            1,
            p0=0.0,
            p1=0.0,
            alt=ft_alt_for(f1_fnmode),
            keep_complex=True,
        )
        return np.moveaxis(arr, 1, 0), 0
    # 3D recon: (direct, f2_time, f1_time)
    f2_fnmode = int(
        experiment.acquisition_parameters.get("acqu2s", {}).get("FnMODE", 0) or 0
    )
    f1_fnmode = int(
        experiment.acquisition_parameters.get("acqu3s", {}).get("FnMODE", 0) or 0
    )
    f2_fixed = fixed.get("F2", (0.0, 0.0))
    f1_fixed = fixed.get("F1", (0.0, 0.0))
    if searched_axis == "F1":
        # F2 固定(实型),F1 复型
        base = finalize_axis_in_memory(
            planes,
            1,
            p0=f2_fixed[0],
            p1=f2_fixed[1],
            alt=ft_alt_for(f2_fnmode),
            keep_complex=False,
        )
        base = np.moveaxis(base, 1, -1)  # (direct, f1_time, f2_freq)
        base = finalize_axis_in_memory(
            base,
            1,
            p0=0.0,
            p1=0.0,
            alt=ft_alt_for(f1_fnmode),
            keep_complex=True,
        )
        return np.transpose(base, (1, 2, 0)), 0  # (f1_freq, f2_freq, direct)
    # F2 搜索:F2 复型,F1 固定(实型)
    base = finalize_axis_in_memory(
        planes,
        1,
        p0=0.0,
        p1=0.0,
        alt=ft_alt_for(f2_fnmode),
        keep_complex=True,
    )
    base = np.moveaxis(base, 1, -1)  # (direct, f1_time, f2_freq)
    base = finalize_axis_in_memory(
        base,
        1,
        p0=f1_fixed[0],
        p1=f1_fixed[1],
        alt=ft_alt_for(f1_fnmode),
        keep_complex=False,
    )
    return np.transpose(base, (1, 2, 0)), 1  # (f1_freq, f2_freq, direct)


def _unified_nus(
    experiment: Experiment,
    backend: Any,
    *,
    plan: Any,
    work_dir: Path | str | None,
    base_params: dict[str, Any] | None,
    progress: Callable[[str], None] | None,
) -> dict[str, Any]:
    """NUS 统一流程:SMILE 一次(直接维 PS(0,0))→ 直接维在 recon 复型平面
    内存搜索 → 间接维内存复刻 finalize 链完整逐维搜索 → 旋转 recon 平面应用
    直接维相位 → finalize 终跑。"""
    from core.data.internal_data_model import AxisRole
    from workflow.memory_phase_search import (
        joint_recheck_memory,
        search_axis_memory,
    )
    from workflow.phase_optimize import PHASE_SCORE_FLAT_MARGIN

    work = Path(work_dir) if work_dir else backend._work_path(experiment)
    params_first = dict(base_params or {})
    params_first.update(
        {"direct_phase_search": False, "display_phase_search": False}
    )
    first = backend.reconstruct_nus(experiment, params_first, progress=progress)
    if not first.get("success") or not first.get("spectrum_path"):
        raise RuntimeError(f"第一遍 SMILE 重构失败: {first.get('message')}")
    logs: list[str] = [
        f"第一遍 SMILE 重构完成: {first.get('spectrum_path')}"
    ]
    backend_runs = 1
    planes = _load_recon_planes(experiment, work)
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    indirect_axes = [
        dim.logical_axis
        for dim in experiment.dimensions
        if dim.role is not AxisRole.DIRECT
    ]
    # 直接维:recon 平面 axis 0 复型 → 沿用旧权威的显示层对称性搜索
    # (0.2.96/0.2.98 机制;旧几十次后端方案从不把固定迹线净吸收用于 NUS
    # 直接维,直接维随 SMILE 固化)。score<30 时保持 (0,0)。
    from core.optimization.phase_search import search_direct_phase_on_spectrum

    direct_est = search_direct_phase_on_spectrum(planes, axis=0, metric="symmetry")
    direct_phase = (0.0, 0.0)
    if direct_est is not None and direct_est[2] >= 30.0:
        direct_phase = (float(direct_est[0]), float(direct_est[1]))
        logs.append(
            f"直接维对称性搜索: {direct_axis}=({direct_phase[0]:g}°, "
            f"{direct_phase[1]:g}°) score={direct_est[2]:.2f}"
        )
    else:
        logs.append("直接维对称性搜索无干净信号峰或置信度不足,保持 (0,0)")
    logs.append(
        f"直接维内存相位: {direct_axis}=({direct_phase[0]:g}°, {direct_phase[1]:g}°)"
    )
    fixed: dict[str, tuple[float, float]] = {}
    axis_arrays: dict[str, np.ndarray] = {}
    axis_index: dict[str, int] = {}
    axis_traces: dict[str, tuple[list[int], list[int]]] = {}
    for axis in indirect_axes:
        arr, ax = _finalize_substrate_nus(planes, experiment, axis, fixed)
        est = search_axis_memory(arr, ax)
        if est is None:
            raise RuntimeError(f"内存相位搜索({axis})无可用迹线")
        fixed[axis] = est.phase
        axis_arrays[axis] = arr
        axis_index[axis] = ax
        axis_traces[axis] = est.traces
        logs += est.logs
        logs.append(
            f"{axis}: 内存相位 = ({est.phase[0]:g}°, {est.phase[1]:g}°) "
            f"score={est.score:.2f}"
        )
    if len(indirect_axes) >= 2:
        best, best_score, fixed_score, zero_score = joint_recheck_memory(
            axis_arrays, axis_index, axis_traces, fixed
        )
        if best != fixed and best_score - fixed_score >= PHASE_SCORE_FLAT_MARGIN:
            logs.append(
                f"联合复核: 联合最优 {best} (score={best_score:.2f}) "
                f"优于顺序固定 {fixed} (score={fixed_score:.2f}),已更新"
            )
            fixed = best
        else:
            logs.append(
                f"联合复核: 联合面平坦(顺序 {fixed} score={fixed_score:.2f} "
                f"vs 联合最优 {best} score={best_score:.2f}),保持顺序固定"
            )
    # 应用直接维相位:旋转 recon 平面写副本(源平面不动),再 finalize
    planes_arg = None
    if abs((direct_phase[0] + 180.0) % 360.0 - 180.0) > 2.0 or abs(direct_phase[1]) > 2.0:
        if backend._apply_direct_phase(experiment, work, direct_phase[0], direct_phase[1], logs):
            planes_arg = (
                "nus3d_rc_ph/test%04d.ft1"
                if experiment.ndim >= 3
                else "nus2d/recon_ph.ft1"
            )
            logs.append("recon 平面已按直接维相位旋转(源平面不动)")
        else:
            logs.append("直接维相位应用失败,保持原 recon 平面")
    final = backend.finalize_nus(
        experiment,
        phases=fixed,
        work_dir=work,
        planes=planes_arg,
    )
    backend_runs += 1
    if not final.get("success") or not final.get("spectrum_path"):
        raise RuntimeError(f"finalize 终跑失败: {final.get('message')}")
    logs += list(final.get("logs", []))
    return {
        "phases": fixed,
        "direct_phase": direct_phase,
        "spectrum_path": str(final["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
    }
