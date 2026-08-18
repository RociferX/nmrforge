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

def _axis_index(axis: str, ndim: int = 2) -> int:
    """逻辑轴名 → 生产布局谱数组下标。

    实测 NMRPipe 输出布局:2D 为 (F1, F2);3D(含 finalize ZTP 链)为
    (F2, F1, F3)——FDF 头标签在 3D 输出中不可靠,以尺寸/复型轴位置为准。
    """
    if ndim >= 3:
        return {"F2": 0, "F1": 1, "F3": 2}.get(axis, 0)
    return {"F1": 0, "F2": 1}.get(axis, 0)

def _template(experiment: Experiment) -> Any:
    """按实验类型名查模板(先精确,再大小写兜底)。"""
    import core.experiments  # noqa: F401  导入即注册内置模板
    from core.experiments.registry import get as get_template

    name = experiment.experiment_type.name if experiment.experiment_type else ""
    tpl = get_template(name)
    if tpl is None:
        for tname in (name.upper(), name.lower()):
            tpl = get_template(tname)
            if tpl is not None:
                break
    return tpl


def _sign_mode(experiment: Experiment) -> str:
    """按实验模板 peak_sign 返回评分符号约束
    (mixed=正负峰共存,uniform=同号;默认 uniform)。"""
    tpl = _template(experiment)
    if tpl is not None and tpl.peak_sign == "mixed":
        return "mixed"
    return "uniform"


def _disambiguate_180_mixed(
    complex_arr: np.ndarray,
    axis: int,
    phase: tuple[float, float],
    experiment: Experiment,
    searched_axis: str,
) -> tuple[float, float]:
    """mixed 实验的 ±180° 绝对符号消歧(化学位移分区先验)。

    搜索轴所在核若预设给出 peak_sign_regions(如 HNCACB 13C 的 Cα/Cβ),
    则在当前相位下统计各区内强峰占优符号:两区符号相反且各自干净(占优
    比例 ≥0.7、强峰数 ≥4)时,若绝对约定与预设不符则 p0 += 180 整体翻转;
    区域不干净/峰不足/核不匹配时不翻转(保守)。
    """
    tpl = _template(experiment)
    if tpl is None:
        return phase
    dim = next(
        (d for d in experiment.dimensions if d.logical_axis == searched_axis),
        None,
    )
    if dim is None or not dim.nucleus:
        return phase
    regions = (tpl.peak_sign_regions or {}).get(dim.nucleus)
    if not regions or len(regions) < 2:
        return phase
    from workflow.memory_phase_search import rotate_real
    n = complex_arr.shape[axis]
    ppm = dim.o1p + (n / 2.0 - np.arange(n)) * (float(dim.sw) / (n * float(dim.sf)))
    real = rotate_real(complex_arr, axis, phase[0], phase[1])
    moved = np.moveaxis(real, axis, -1)
    flat = moved.reshape(-1, n)
    mag = np.abs(flat)
    peak_val = flat[np.argmax(mag, axis=0), np.arange(n)]
    global_max = float(np.max(np.abs(peak_val)))
    if global_max <= 0:
        return phase
    observed: list[int] = []
    for cfg in regions.values():
        lo, hi = cfg["ppm"]
        idx = np.where((ppm >= lo) & (ppm <= hi))[0]
        if idx.size == 0:
            return phase
        vals = peak_val[idx]
        strong = vals[np.abs(vals) > 0.2 * global_max]
        if strong.size < 4:
            return phase
        pos = int((strong > 0).sum())
        neg = int((strong < 0).sum())
        if max(pos, neg) / strong.size < 0.7:
            return phase
        observed.append(1 if pos > neg else -1)
    expected = [int(cfg["sign"]) for cfg in regions.values()]
    if len(set(observed)) < 2 or observed == expected:
        return phase
    return ((phase[0] + 180.0) % 360.0, phase[1])


def _read_complex_preview(
    path: Path | str, unpack_axis: int | None = None
) -> np.ndarray:
    """读复型预览文件:nmrglue 直接读为复型则用之;否则交错实型沿
    unpack_axis 拆包(3D 输出复型轴不固定:preview_F2 在轴 0,preview_F1
    在轴 1,read_pipe_complex 只拆轴 0 会拆错)。"""
    import nmrglue as ng

    from core.data.pipe_io import read_pipe_complex

    path = Path(path)
    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    if unpack_axis is not None:
        moved = np.moveaxis(arr, unpack_axis, -1)
        even = moved[..., 0::2]
        odd = moved[..., 1::2]
        return np.moveaxis(even + 1j * odd, -1, unpack_axis).astype(
            np.complex128
        )
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
    sign_mode = _sign_mode(experiment)
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
        if progress is not None:
            progress(f"{axis} 复型预览中")
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
        if progress is not None:
            progress(f"{axis} 复型预览完成")
        ax = _axis_index(axis, experiment.ndim)
        arr = _read_complex_preview(str(resp["spectrum_path"]), unpack_axis=ax)
        est = search_axis_memory(arr, ax, sign_mode=sign_mode)
        if est is None:
            raise RuntimeError(f"内存相位搜索({axis})无可用迹线")
        if sign_mode == "mixed":
            resolved = _disambiguate_180_mixed(
                arr, ax, est.phase, experiment, axis
            )
            if resolved != est.phase:
                logs.append(
                    f"{axis}: ±180° 化学位移分区消歧 "
                    f"{est.phase} → {resolved}"
                )
            fixed[axis] = resolved
        else:
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
            axis_arrays, axis_index, axis_traces, fixed, sign_mode=sign_mode
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
    if progress is not None:
        progress("终跑(完整重跑)中")
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
    if progress is not None:
        progress("终跑完成")
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
    if progress is not None:
        progress("第一遍 SMILE 完成")
    backend_runs = 1
    planes = _load_recon_planes(experiment, work)
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    sign_mode = _sign_mode(experiment)
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
        if abs(direct_phase[1]) > 20.0:
            logs.append(
                f"直接维对称性搜索 p1={direct_phase[1]:g}° 幅值异常(>20°),归零"
            )
            direct_phase = (direct_phase[0], 0.0)
        logs.append(
            f"直接维对称性搜索: {direct_axis}=({direct_phase[0]:g}°, "
            f"{direct_phase[1]:g}°) score={direct_est[2]:.2f}"
        )
    else:
        logs.append("直接维对称性搜索无干净信号峰或置信度不足,保持 (0,0)")
    logs.append(
        f"直接维内存相位: {direct_axis}=({direct_phase[0]:g}°, {direct_phase[1]:g}°)"
    )
    # 间接维:finalize 复型预览(该轴 PS 不加 -di,其它轴按已固定相位 -di,
    # 零填零)提供基底,内存完整逐维搜索——FT/-alt/ZTP 约定由真实后端保证
    fixed: dict[str, tuple[float, float]] = {}
    axis_arrays: dict[str, np.ndarray] = {}
    axis_index: dict[str, int] = {}
    axis_traces: dict[str, tuple[list[int], list[int]]] = {}
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_none = {
        "zero_fill": {
            dim.logical_axis: {"mode": "none"} for dim in experiment.dimensions
        }
    }
    for axis in indirect_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        if progress is not None:
            progress(f"{axis} 复型预览中")
        resp = backend.finalize_nus(
            experiment,
            phases=fixed,
            work_dir=work,
            params={**zf_none, "preview_axis": axis},
            out_file=out_file,
            script_name=f"{experiment.dataset_id}_preview_{axis}_finalize.com",
            progress=progress,
        )
        backend_runs += 1
        if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(f"NUS 复型预览({axis})失败: {resp.get('message')}")
        if progress is not None:
            progress(f"{axis} 复型预览完成")
        ax = _axis_index(axis, experiment.ndim)
        arr = _read_complex_preview(str(resp["spectrum_path"]), unpack_axis=ax)
        est = search_axis_memory(arr, ax, sign_mode=sign_mode)
        if est is None:
            raise RuntimeError(f"内存相位搜索({axis})无可用迹线")
        if sign_mode == "mixed":
            resolved = _disambiguate_180_mixed(
                arr, ax, est.phase, experiment, axis
            )
            if resolved != est.phase:
                logs.append(
                    f"{axis}: ±180° 化学位移分区消歧 "
                    f"{est.phase} → {resolved}"
                )
            fixed[axis] = resolved
        else:
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
            axis_arrays, axis_index, axis_traces, fixed, sign_mode=sign_mode
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
        if backend._apply_direct_phase(
            experiment, work, direct_phase[0], direct_phase[1], logs, progress=progress
        ):
            planes_arg = (
                "nus3d_rc_ph/test%04d.ft1"
                if experiment.ndim >= 3
                else "nus2d/recon_ph.ft1"
            )
            logs.append("recon 平面已按直接维相位旋转(源平面不动)")
        else:
            logs.append("直接维相位应用失败,保持原 recon 平面")
    if progress is not None:
        progress("finalize 终跑中")
    final = backend.finalize_nus(
        experiment,
        phases=fixed,
        work_dir=work,
        planes=planes_arg,
        progress=progress,
    )
    backend_runs += 1
    if not final.get("success") or not final.get("spectrum_path"):
            raise RuntimeError(f"finalize 终跑失败: {final.get('message')}")
    if progress is not None:
        progress("finalize 终跑完成")
    logs += list(final.get("logs", []))
    return {
        "phases": fixed,
        "direct_phase": direct_phase,
        "spectrum_path": str(final["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
    }
