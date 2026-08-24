"""统一相位优化途径(替代简单/进阶分派,2026-08-17)。

统一方案:第一遍逐维复型预览(仅搜索轴 PS 不加 -di,其它轴按已固定
相位加 -di,零填零)→ 内存调相(旧算法判断标准:固定迹线中位数净吸收,
零额外后端)→ 联合复核 → 处理参数优化(基线/填零/窗函数)→ 完整终跑:
各维最终相位填入初始脚本生成新的完整脚本(NUS 不再写 nus3d_rc_ph 旋转
副本;直接维相位进 step1 PS(EXT 后),间接维相位进 step3 PS)。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method


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


def _cleanup_unified_intermediates(
    work: Path,
    dataset_id: str,
) -> None:
    """清理 unified 流程中间产物(相位校正预览与窗函数优化评分谱)。

    清理(仅限 process 工作目录,不递归、不跨目录):
    - {dataset_id}_preview_* 的 .com/.ft2/.ft3/.fdf
    - {dataset_id}_joint* 的 .com/.ft3/.fdf
    - {dataset_id}_win1* / _win2* / _win3* 的 .com/.ft3/.fdf

    保留项(终谱、最终脚本、phase.json、fid/、重构平面)不受影响。
    """
    if not work.is_dir():
        return
    exts = (".com", ".ft2", ".ft3", ".fdf")
    for base_pattern in (
        f"{dataset_id}_preview_*",
        f"{dataset_id}_joint*",
        f"{dataset_id}_win1*",
        f"{dataset_id}_win2*",
        f"{dataset_id}_win3*",
    ):
        for p in work.glob(base_pattern):
            if p.suffix in exts:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


def _append_final_summary(
    logs: list[str],
    spectrum_path: str,
    *,
    direct_axis: str = "",
    direct_phase: tuple[float, float] | None = None,
    phases: dict[str, tuple[float, float]] | None = None,
    backend_runs: int = 0,
    baseline: Any = None,
    zero_fill: Any = None,
    window: Any = None,
    diagnostics: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> None:
    """日志末尾质量与优化汇总(0.2.155/0.2.157):与 pipeline 参数报告
    共用统一格式(诊断显示详情),并同步经 progress 进入日志面板。"""
    from workflow.optimization_report import format_optimization_report

    lines: list[str] = ["== 质量与优化汇总 =="]
    try:
        import nmrglue as ng

        from core.qc import spectrum_quality

        _dic, data = ng.pipe.read(str(spectrum_path))
        q = spectrum_quality.evaluate(np.asarray(data))
        text = f"谱图质量: {q.decision.value}(综合分 {q.score.overall:.1f})"
        if q.reasons:
            text += " | " + "、".join(q.reasons[:3])
        lines.append(text)
    except Exception as exc:  # noqa: BLE001 - 质量评估失败不阻断报告
        lines.append(f"谱图质量: 评估跳过({exc})")
    lines += format_optimization_report(
        {
            "phase_route": "unified",
            "direct_phase": direct_phase,
            "phases": {k: v for k, v in (phases or {}).items()},
            "baseline": baseline,
            "window": window,
            "zero_fill": zero_fill,
            "diagnostics": diagnostics,
            "backend_runs": backend_runs,
        }
    )
    logs += lines
    if progress is not None:
        for line in lines:
            progress(line)


def _split_final_ext(params: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    """从参数取出仅终跑生效的直接维范围(final_ext_lo/final_ext_hi)。

    返回 (剩余参数, ext_lo, ext_hi);首遍/复型预览路径使用剩余参数,
    终跑路径把取出的值映射回 ext_lo/ext_hi(_apply_final_ext)。
    """
    p = dict(params)
    final_lo = p.pop("final_ext_lo", None)
    final_hi = p.pop("final_ext_hi", None)
    return p, final_lo, final_hi


def _apply_final_ext(
    params: dict[str, Any], final_lo: Any, final_hi: Any
) -> dict[str, Any]:
    """把用户指定的终跑直接维范围写入参数(空值保持原配置)。"""
    p = dict(params)
    if final_lo is not None and str(final_lo).strip():
        p["ext_lo"] = str(final_lo)
    if final_hi is not None and str(final_hi).strip():
        p["ext_hi"] = str(final_hi)
    return p


def _direct_phase_width(params: dict[str, Any]) -> float:
    """直接维提取窗口宽度(ppm):EXT -x1/-xn 有效值之差,缺失回退配置默认。"""
    from backend.config import resolve_ext_hi, resolve_ext_lo

    try:
        lo = float(resolve_ext_lo(params.get("ext_lo")))
        hi = float(resolve_ext_hi(params.get("ext_hi")))
    except (TypeError, ValueError):
        return 0.0
    return abs(lo - hi)


def _renormalize_direct_p1(
    direct_phase: tuple[float, float],
    first_params: dict[str, Any],
    final_params: dict[str, Any],
) -> tuple[float, float]:
    """终跑直接维范围与首遍不同时,把 p1 按窗口宽度比例重归一化。

    p1 表示跨整个提取窗口的总线性相位度数;范围变窄/变宽后,同样的
    p1 度数分布到不同频率范围,物理斜率随之变化。按
    (最终窗口宽/首遍窗口宽) 缩放保持与内存搜索一致的物理校正。
    """
    p0, p1 = direct_phase
    w_first = _direct_phase_width(first_params)
    w_final = _direct_phase_width(final_params)
    if w_first <= 0 or w_final <= 0:
        return (p0, p1)
    ratio = w_final / w_first
    if abs(ratio - 1.0) < 1e-9:
        return (p0, p1)
    return (p0, p1 * ratio)


def unified_route(    experiment: Experiment,
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
    内存搜索,间接维内存复刻 finalize 链完整搜索;最后把各维最终相位填入
    完整脚本重跑出良谱(不写旋转平面副本)。
    """
    from workflow.memory_phase_search import (
        PHASE_SCORE_FLAT_MARGIN,
        joint_recheck_memory,
        search_axis_memory,
    )

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
    work = Path(work_dir) if work_dir else backend._work_path(experiment)
    params = dict(base_params or {})
    params.pop("preview_axis", None)
    # 0.2.163-补6:uniform 与 NUS 同源——先跑直接维数据质量诊断门控
    # (FID 内存扫描:直流偏置→POLY -time、坏点→替换备份),不重跑后端
    diagnostics: dict[str, Any] = {}
    diag_logs: list[str] = []
    try:
        from workflow.direct_diagnostics import run_direct_diagnostics

        if progress is not None:
            progress("数据质量诊断中(直接维 FID 内存扫描)")
        diag_result = run_direct_diagnostics(work, experiment)
        diagnostics = {
            "reports": list(diag_result.reports),
            "metrics": dict(diag_result.metrics),
            "apply_poly_time": diag_result.apply_poly_time,
            "repaired_badpoints": diag_result.repaired_badpoints,
            "backup_dir": diag_result.backup_dir,
        }
        if diag_result.reports:
            diag_logs = ["== 数据质量诊断 =="] + [
                f"{i + 1}. {r}"
                for i, r in enumerate(diag_result.reports)
            ]
    except Exception as exc:  # noqa: BLE001 - 诊断失败不阻断谱图生成
        diag_logs = [f"数据质量诊断失败: {exc}"]
    # 0.2.162-补15:用户指定的终跑直接维范围(final_ext_lo/final_ext_hi)
    # 只进终跑完整脚本,首遍复型预览不改
    params, final_ext_lo, final_ext_hi = _split_final_ext(params)
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
    # 0.2.166:sampling.auto_phase=False 时直接维相位保持 (0,0)(与 NUS
    # 一致),跳过直接维搜索与预览;间接维联合复核不受影响
    if (params.get("sampling") or {}).get("auto_phase") is False:
        search_axes = [a for a in axes if a != direct_axis]
        logs.append("sampling.auto_phase=False,直接维相位保持 (0,0)(跳过搜索)")
    backend_runs = 0
    for axis in search_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        t_axis = time.time()
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
        logs.append(f"{axis} 相位搜索完成,耗时 {time.time() - t_axis:.1f} 秒")
    if len(search_axes) >= 2:
        t_joint = time.time()
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
        logs.append(f"联合复核完成,耗时 {time.time() - t_joint:.1f} 秒")
    # 0.2.166:auto_phase=False 时直接维未参与搜索与联合复核,保持 (0,0)
    fixed.setdefault(direct_axis, (0.0, 0.0))
    # 0.2.163-补6:处理参数优化(基线/直接维窗/填零+间接窗),与 NUS 对称;
    # uniform 无重构,候选重跑完整 process 更快
    if progress is not None:
        progress("联合复核完成,开始处理参数优化(基线/填零/窗函数)")
    t_opt = time.time()
    proc = _optimize_uniform_processing(
        experiment,
        backend,
        work,
        fixed,
        params,
        plan=plan,
        progress=progress,
    )
    logs += list(diag_logs) + proc["logs"]
    logs.append(
        f"处理参数优化(基线/填零/窗函数)完成,耗时 {time.time() - t_opt:.1f} 秒"
    )
    # 0.2.166:uniform 保留优化前完整脚本(joint 脚本,含最终相位与自动
    # 填零,未含优化基线/窗)为 {dataset_id}_before_optimize.com,与 NUS
    # 初跑脚本保留对称,便于对照优化前后脚本
    joint_script = work / f"{experiment.dataset_id}_joint.com"
    no_opt_script = work / f"{experiment.dataset_id}_before_optimize.com"
    try:
        if joint_script.is_file():
            no_opt_script.write_text(
                joint_script.read_text(encoding="utf-8"),
                encoding="utf-8",
                newline="\n",
            )
            logs.append(f"初跑脚本保留: {no_opt_script.name}")
    except OSError as exc:
        logs.append(f"初跑脚本保留失败: {exc}")
    if progress is not None:
        progress("终跑(完整重跑)中")
    t_final = time.time()
    params_final = _apply_final_ext(dict(params), final_ext_lo, final_ext_hi)
    # 0.2.162-补16:终跑直接维范围变化时,直接维 p1 按窗口宽度比例重归一化
    fixed_final = dict(fixed)
    if direct_axis in fixed_final:
        renormed = _renormalize_direct_p1(
            fixed[direct_axis], params, params_final
        )
        if renormed != fixed[direct_axis]:
            logs.append(
                f"直接维相位按终跑窗口重归一化: {direct_axis} "
                f"p1={fixed[direct_axis][1]:g}° → {renormed[1]:g}°"
            )
        fixed_final[direct_axis] = renormed
    params_final.update(
        {
            "direct_phase_search": False,
            "display_phase_search": False,
            "direct_poly_time": bool(diagnostics.get("apply_poly_time")),
            "baseline": proc["baseline"],
            "zero_fill": proc["zero_fill"],
            "window": proc["window"],
        }
    )
    resp = backend.process(
        experiment,
        plan,
        direct_phase_override=fixed_final,
        params=params_final,
        progress=progress,
    )
    backend_runs += 1
    if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(f"终跑失败: {resp.get('message')}")
    if progress is not None:
        progress("终跑完成")
    logs += list(resp.get("logs", []))
    logs.append(f"终跑完成,耗时 {time.time() - t_final:.1f} 秒")
    _append_final_summary(
        logs,
        str(resp["spectrum_path"]),
        direct_axis=direct_axis,
        direct_phase=fixed_final.get(direct_axis),
        phases=fixed_final,
        backend_runs=backend_runs,
        baseline=proc["baseline"],
        zero_fill=proc["zero_fill"],
        window=proc["window"],
        diagnostics=diagnostics,
        progress=progress,
    )
    _cleanup_unified_intermediates(work, experiment.dataset_id)
    return {
        "phases": fixed_final,
        "spectrum_path": str(resp["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
        "direct_phase": fixed_final.get(direct_axis),
        "baseline": proc["baseline"],
        "zero_fill": proc["zero_fill"],
        "window": proc["window"],
        "diagnostics": diagnostics,
    }

_DIRECT_PHASE_FP_KEYS = (
    "extract", "ext_lo", "ext_hi", "nsigma", "thresh",
    "smile_xq3", "smile_scaling", "zero_fill", "linewidth_hz",
    "points_per_line", "segment_shift_hz", "sampling",
    "window",  # 0.2.166:直接维窗进 SMILE step1 重构平面,缓存指纹必须含窗
    "direct_poly_time",  # 0.2.160:首遍脚本不再含 POLY -time,搜索基于原始平面
)


def _direct_phase_params_fp(experiment: Experiment, params: dict) -> str:
    """直接维相位缓存指纹:影响重构平面的参数 + 数据集标识。"""
    import hashlib
    import json as _json

    payload = {
        "dataset_id": experiment.dataset_id,
        "ndim": experiment.ndim,
        "segments": [str(s) for s in (experiment.segments or [])],
        "params": {k: params.get(k) for k in _DIRECT_PHASE_FP_KEYS},
    }
    return hashlib.sha256(
        _json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _direct_phase_cache_path(work: Path) -> Path:
    return work / "phase.json"


def _estimate_direct_phase_seconds(work: Path) -> float | None:
    """读上次 unified_direct 相位搜索耗时(用于进度预估)。"""
    import json as _json

    path = _direct_phase_cache_path(work)
    if not path.is_file():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if data.get("source") != "unified_direct":
        return None
    dur = data.get("duration_s")
    return float(dur) if dur else None


def _load_direct_phase_cache(
    work: Path, experiment: Experiment, params: dict, shape
) -> dict | None:
    """参数与谱面未变时复用 phase.json 缓存(跳过重复搜索)。"""
    import json as _json

    path = _direct_phase_cache_path(work)
    if not path.is_file():
        return None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if data.get("version") != 2 or data.get("source") != "unified_direct":
        return None
    if list(data.get("shape") or []) != list(shape):
        return None
    if data.get("params_fp") != _direct_phase_params_fp(experiment, params):
        return None
    return data


def _save_direct_phase_cache(
    work: Path, experiment: Experiment, params: dict, shape,
    p0: float, p1: float, score: float, duration_s: float,
) -> None:
    import json as _json

    try:
        _direct_phase_cache_path(work).write_text(
            _json.dumps(
                {
                    "version": 2,
                    "source": "unified_direct",
                    "p0": p0,
                    "p1": p1,
                    "score": score,
                    "shape": list(shape),
                    "params_fp": _direct_phase_params_fp(experiment, params),
                    "duration_s": round(float(duration_s), 2),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


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

def _optimize_uniform_processing(
    experiment: Experiment,
    backend: Any,
    work: Path,
    fixed: dict[str, tuple[float, float]],
    base_params: dict[str, Any] | None,
    plan: Any = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """联合复核后的处理参数优化(uniform 2D/3D):基线(内存评分)+ 直接维
    窗函数(FID 内存评分)+ 填零/间接维窗候选(process 重跑评分)。

    与 NUS 版本对称;uniform 无 SMILE 重构,候选重跑的是完整 process
    管道(带 fixed 相位覆盖),不做重构反而更快。任何评估失败均降级:
    保持 base_params 既有配置或默认,不阻断终跑。
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    indirect_axes = [a for a in axes if a != direct_axis]
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_params = {a: {"mode": "auto"} for a in axes}
    base = dict(base_params or {})
    out_logs: list[str] = []
    baseline_cfg = dict(base.get("baseline") or {})
    window_cfg = base.get("window")
    # 1) 联合复核谱:最终相位 + 完整填零,作基线/窗评分基底(process 一次)
    joint_file = f"{experiment.dataset_id}_joint.{ext}"
    resp = backend.process(
        experiment,
        plan,
        direct_phase_override=dict(fixed) if fixed else None,
        params={"zero_fill": zf_params},
        out_file=joint_file,
        script_name=f"{experiment.dataset_id}_joint.com",
        progress=progress,
    )
    if not resp.get("success") or not resp.get("spectrum_path"):
        out_logs.append(
            "处理参数优化: 联合复核谱生成失败("
            + str(resp.get("message"))
            + "),保持现有基线/填零/窗配置"
        )
        return {
            "baseline": baseline_cfg,
            "zero_fill": zf_params,
            "window": window_cfg,
            "logs": out_logs,
        }
    base_path = Path(resp["spectrum_path"])
    # 2) 基线:每轴内存评分(off/auto/order1-3),写回终跑
    try:
        from workflow.baseline_optimize import optimize_baseline

        if progress is not None:
            progress("基线优化中(内存评分)")
        opt = optimize_baseline(experiment, base_path)
        baseline_cfg = dict(opt.baseline)
        out_logs += opt.logs
    except Exception as exc:  # noqa: BLE001 - 基线评估失败不影响相位/终跑
        out_logs.append(f"基线优化(嵌入)失败: {exc}")
    # 2.1) 间接维基线变化 → 用新基线重渲基底谱(与 NUS 对称),供窗/填零
    #      候选评分;直接维基线随终跑完整脚本统一应用
    default_cfg: dict[str, Any] = {"enabled": True, "mode": "auto", "order": 0}
    changed_indirect = [
        a for a in indirect_axes if baseline_cfg.get(a) not in (None, default_cfg)
    ]
    if changed_indirect:
        apply_baseline = {a: baseline_cfg[a] for a in changed_indirect}
        resp = backend.process(
            experiment,
            plan,
            direct_phase_override=dict(fixed) if fixed else None,
            params={"zero_fill": zf_params, "baseline": apply_baseline},
            out_file=joint_file,
            script_name=f"{experiment.dataset_id}_joint.com",
            progress=progress,
        )
        if resp.get("success") and resp.get("spectrum_path"):
            base_path = Path(resp["spectrum_path"])
            out_logs.append("基线(嵌入): 间接维已用最优基线重渲基底谱")
        else:
            out_logs.append(
                "基线(嵌入): 间接维基线重渲失败("
                + str(resp.get("message"))
                + "),评分沿用默认基线"
            )
    # 2.5) 直接维窗函数:FID 直接维迹内存评分(不重跑 process),写回终跑
    try:
        from workflow.window_optimize import optimize_direct_window_from_work

        if progress is not None:
            progress("直接维窗函数优化中(FID 内存评分)")
        wres = optimize_direct_window_from_work(
            work, experiment, current=(window_cfg or {}).get(direct_axis)
        )
        if wres.changed:
            win = dict(window_cfg or {})
            win[direct_axis] = wres.choice
            window_cfg = win
        out_logs += wres.logs
    except Exception as exc:  # noqa: BLE001 - 窗优化失败不影响相位/终跑
        out_logs.append(f"直接维窗优化失败: {exc}")
    # 3) 填零/间接维窗函数候选:process 重跑 + 谱质量评分(候选有限,无重构)
    try:
        import numpy as np

        from backend.script_generator import zero_fill_plan as _zf_plan
        from core.qc import spectrum_quality

        if progress is not None:
            progress("填零/窗函数候选评分中")
        auto_plan = _zf_plan(experiment, zf_params)
        min_shape = tuple(
            int(auto_plan.get(a, {}).get("size") or 1) for a in axes
        )

        def _score_path(path: Path) -> float:
            import nmrglue as ng

            _dic, data = ng.pipe.read(str(path))
            q = spectrum_quality.evaluate(
                np.asarray(data), min_shape=min_shape
            )
            return float(q.score.overall)

        base_score = _score_path(base_path)
        windows: list[dict[str, Any]] = [
            {"type": "sine_bell"},
            {"type": "sine_bell_squared"},
            {"type": "gaussian", "lb": 5.0, "gb": 0.1},
        ]
        best_window: dict[str, Any] | None = None
        best_score = base_score
        for index, w in enumerate(windows, 1):
            wname = str(w.get("type"))
            # 0.2.166:候选评分含已优化直接维窗——与终跑窗口一致,避免
            # 评分面与终跑配置脱节(uniform 可重跑 process,NUS 受 SMILE
            # 重构限制仍为间接维候选)
            cand_window = dict(window_cfg or {})
            for a in indirect_axes:
                cand_window[a] = dict(w)
            cand_params = {
                "zero_fill": zf_params,
                "window": cand_window,
            }
            resp = backend.process(
                experiment,
                plan,
                direct_phase_override=dict(fixed) if fixed else None,
                params=cand_params,
                out_file=f"{experiment.dataset_id}_win{index}.{ext}",
                script_name=f"{experiment.dataset_id}_win{index}.com",
                progress=progress,
            )
            if not resp.get("success") or not resp.get("spectrum_path"):
                out_logs.append(
                    f"窗函数/填零(嵌入): {wname} 运行失败,跳过"
                )
                continue
            try:
                score = _score_path(Path(resp["spectrum_path"]))
            except Exception as exc:  # noqa: BLE001
                out_logs.append(
                    f"窗函数/填零(嵌入): {wname} 评分失败 {exc},跳过"
                )
                continue
            out_logs.append(
                f"窗函数/填零(嵌入): 间接维 {wname}+填零=auto "
                f"score={score:.1f}"
            )
            if score > best_score:
                best_score = score
                best_window = dict(w)
        if best_window is not None and best_score > base_score + 0.5:
            # 0.2.166:只覆盖间接维,保留直接维已优化窗(此前整表替换会丢窗)
            final_window = dict(window_cfg or {})
            for a in indirect_axes:
                final_window[a] = dict(best_window)
            window_cfg = final_window
            wtype = str(best_window.get("type", "sine_bell"))
            out_logs.append(
                "窗函数/填零(嵌入): 已选 "
                f"{wtype}+填零=auto "
                f"(score={base_score:.1f} → {best_score:.1f}),"
                "终跑完整脚本应用"
            )
        elif best_window is not None:
            out_logs.append("窗函数/填零(嵌入): 候选未优于当前(无窗),保持默认")
        else:
            out_logs.append("窗函数/填零(嵌入): 无有效候选,保持默认")
    except Exception as exc:  # noqa: BLE001
        out_logs.append(f"填零/窗函数优化(嵌入)失败: {exc}")
    return {
        "baseline": baseline_cfg,
        "zero_fill": zf_params,
        "window": window_cfg,
        "logs": out_logs,
    }


def _optimize_nus_processing(

    experiment: Experiment,
    backend: Any,
    work: Path,
    fixed: dict[str, tuple[float, float]],
    base_params: dict[str, Any] | None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """联合复核后的处理参数优化(NUS):基线(内存评分)+ 间接维窗函数/填零
    (候选 finalize 重渲评分,不重跑 SMILE)。

    返回 {"baseline", "zero_fill", "window", "logs"},优化结果写回终跑
    完整脚本。直接维基线仍逐轴评分后写回(终跑 step1 POLY 应用);直接维
    窗/SMILE 内部 apod 在重构内,候选评估需重跑 SMILE,保持默认并日志说明。
    任何评估失败均降级:保持 base_params 既有配置或默认,不阻断终跑。
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    indirect_axes = [a for a in axes if a != direct_axis]
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_params = {a: {"mode": "auto"} for a in axes}
    base = dict(base_params or {})
    out_logs: list[str] = []
    baseline_cfg = dict(base.get("baseline") or {})
    window_cfg = base.get("window")
    # 1) 联合复核谱:间接维最终相位 + 完整填零,作基线/窗函数评分基底
    joint_file = f"{experiment.dataset_id}_joint.{ext}"
    resp = backend.finalize_nus(
        experiment,
        phases=fixed,
        work_dir=work,
        params={"zero_fill": zf_params},
        out_file=joint_file,
        script_name=f"{experiment.dataset_id}_joint_finalize.com",
        progress=progress,
    )
    if not resp.get("success") or not resp.get("spectrum_path"):
        out_logs.append(
            "处理参数优化: 联合复核谱生成失败("
            + str(resp.get("message"))
            + "),保持现有基线/填零/窗配置"
        )
        return {
            "baseline": baseline_cfg,
            "zero_fill": zf_params,
            "window": window_cfg,
            "logs": out_logs,
        }
    base_path = Path(resp["spectrum_path"])
    # 2) 基线:每轴内存评分(off/auto/order1-3),写回终跑完整脚本
    try:
        from workflow.baseline_optimize import optimize_baseline

        if progress is not None:
            progress("基线优化中(内存评分)")
        opt = optimize_baseline(experiment, base_path)
        baseline_cfg = dict(opt.baseline)
        out_logs += opt.logs
    except Exception as exc:  # noqa: BLE001 - 基线评估失败不影响相位/终跑
        out_logs.append(f"基线优化(嵌入)失败: {exc}")
    # 间接维基线变化 → 用新基线重渲基底谱(供窗/填零评分);直接维基线在
    # SMILE 重构内,重渲不生效,由终跑完整脚本 step1 POLY 统一应用
    default_cfg: dict[str, Any] = {"enabled": True, "mode": "auto", "order": 0}
    changed_indirect = [
        a for a in indirect_axes if baseline_cfg.get(a) not in (None, default_cfg)
    ]
    apply_baseline: dict[str, Any] = {}
    if changed_indirect:
        apply_baseline = {a: baseline_cfg[a] for a in indirect_axes}
        resp = backend.finalize_nus(
            experiment,
            phases=fixed,
            work_dir=work,
            baseline=apply_baseline,
            params={"zero_fill": zf_params},
            out_file=joint_file,
            script_name=f"{experiment.dataset_id}_joint_finalize.com",
            progress=progress,
        )
        if resp.get("success") and resp.get("spectrum_path"):
            base_path = Path(resp["spectrum_path"])
            out_logs.append("基线(嵌入): 间接维已用最优基线重渲基底谱")
        else:
            out_logs.append(
                "基线(嵌入): 间接维基线重渲失败("
                + str(resp.get("message"))
                + "),评分沿用默认基线"
            )
            apply_baseline = {}
    # 2.5) 直接维窗函数:FID 直接维迹内存评分(不重跑 SMILE 重构),
    #      分辨率优先 + 信噪比/线形平衡,写回终跑 step1 SP
    try:
        from workflow.window_optimize import optimize_direct_window_from_work

        if progress is not None:
            progress("直接维窗函数优化中(FID 内存评分,不重跑 SMILE)")
        wres = optimize_direct_window_from_work(
            work, experiment, current=(window_cfg or {}).get(direct_axis)
        )
        if wres.changed:
            win = dict(window_cfg or {})
            win[direct_axis] = wres.choice
            window_cfg = win
        out_logs += wres.logs
    except Exception as exc:  # noqa: BLE001 - 窗优化失败不影响相位/终跑
        out_logs.append(f"直接维窗优化失败: {exc}")
    # 3) 间接维窗函数/填零候选:finalize 重渲 + 谱质量评分;直接维窗已由
    #    2.5 FID 内存评分(不重跑 SMILE);间接 apod 保持默认(日志说明)
    try:
        import numpy as np

        from backend.script_generator import zero_fill_plan as _zf_plan
        from core.qc import spectrum_quality

        if progress is not None:
            progress("填零/窗函数候选评分中")
        auto_plan = _zf_plan(experiment, zf_params)
        min_shape = tuple(
            int(auto_plan.get(a, {}).get("size") or 1) for a in axes
        )

        def _score_path(path: Path) -> float:
            import nmrglue as ng

            _dic, data = ng.pipe.read(str(path))
            q = spectrum_quality.evaluate(
                np.asarray(data), min_shape=min_shape
            )
            return float(q.score.overall)

        base_score = _score_path(base_path)
        windows: list[dict[str, Any]] = [
            {"type": "sine_bell"},
            {"type": "sine_bell_squared"},
            {"type": "gaussian", "lb": 5.0, "gb": 0.1},
        ]
        best_window: dict[str, Any] | None = None
        best_score = base_score
        for index, w in enumerate(windows, 1):
            wname = str(w.get("type"))
            resp = backend.finalize_nus(
                experiment,
                phases=fixed,
                work_dir=work,
                baseline=apply_baseline or None,
                params={
                    "zero_fill": zf_params,
                    "window": {a: dict(w) for a in indirect_axes},
                },
                out_file=f"{experiment.dataset_id}_win{index}.{ext}",
                script_name=f"{experiment.dataset_id}_win{index}_finalize.com",
                progress=progress,
            )
            if not resp.get("success") or not resp.get("spectrum_path"):
                out_logs.append(
                    f"窗函数/填零(嵌入): {wname} 运行失败,跳过"
                )
                continue
            try:
                score = _score_path(Path(resp["spectrum_path"]))
            except Exception as exc:  # noqa: BLE001
                out_logs.append(
                    f"窗函数/填零(嵌入): {wname} 评分失败 {exc},跳过"
                )
                continue
            out_logs.append(
                f"窗函数/填零(嵌入): 间接维 {wname}+填零=auto "
                f"score={score:.1f}"
            )
            if score > best_score:
                best_score = score
                best_window = dict(w)
        if best_window is not None and best_score > base_score + 0.5:
            # 0.2.166:只覆盖间接维,保留直接维已优化窗(此前整表替换会丢窗)
            final_window = dict(window_cfg or {})
            for a in indirect_axes:
                final_window[a] = dict(best_window)
            window_cfg = final_window
            out_logs.append(
                "窗函数/填零(嵌入): 已选 "
                f"{best_window.get('type')}+填零=auto "
                f"(score={base_score:.1f} → {best_score:.1f}),"
                "终跑完整脚本应用"
            )
        elif best_window is not None:
            out_logs.append(
                "窗函数/填零(嵌入): 候选未优于当前(无窗),保持默认"
            )
        else:
            out_logs.append("窗函数/填零(嵌入): 无有效候选,保持默认")
    except Exception as exc:  # noqa: BLE001
        out_logs.append(f"填零/窗函数优化(嵌入)失败: {exc}")
    out_logs.append(
        "窗函数(嵌入): 直接维窗/SMILE 内部 apod 保持默认"
        "(调整需重跑 SMILE,未纳入候选)"
    )
    return {
        "baseline": baseline_cfg,
        "zero_fill": zf_params,
        "window": window_cfg,
        "logs": out_logs,
    }


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
    内存搜索 → 间接维内存复刻 finalize 链完整逐维搜索 → 联合复核 →
    处理参数优化(基线/填零/窗函数)→ 终跑:各维最终相位填入初始脚本生成
    新的完整脚本(直接维相位进 step1 PS,EXT 后;间接维进 step3 PS),
    不再写 nus3d_rc_ph 旋转副本。"""
    from core.data.internal_data_model import AxisRole
    from workflow.memory_phase_search import (
        PHASE_SCORE_FLAT_MARGIN,
        joint_recheck_memory,
        search_axis_memory,
    )

    work = Path(work_dir) if work_dir else backend._work_path(experiment)
    # 0.2.140:生成谱图最开端先跑直接维数据质量诊断(FID 内存扫描,
    # 不重跑 SMILE):直流偏置→POLY -time、坏点→自动替换(备份),无法
    # 纠正的问题(漂移/宽带峰/分布不均)列报告建议用户处理
    diagnostics: dict[str, Any] = {}
    diag_logs: list[str] = []
    try:
        from workflow.direct_diagnostics import run_direct_diagnostics

        if progress is not None:
            progress("数据质量诊断中(直接维 FID 内存扫描)")
        diag_result = run_direct_diagnostics(work, experiment)
        diagnostics = {
            "reports": list(diag_result.reports),
            "metrics": dict(diag_result.metrics),
            "apply_poly_time": diag_result.apply_poly_time,
            "repaired_badpoints": diag_result.repaired_badpoints,
            "backup_dir": diag_result.backup_dir,
        }
        if diag_result.reports:
            diag_logs = ["== 数据质量诊断 =="] + [
                f"{i + 1}. {r}"
                for i, r in enumerate(diag_result.reports)
            ]
    except Exception as exc:  # noqa: BLE001 - 诊断失败不阻断谱图生成
        diag_logs = [f"数据质量诊断失败: {exc}"]
    params_first = dict(base_params or {})
    # 0.2.162-补15:终跑直接维范围(final_ext_lo/final_ext_hi)只进终跑,
    # 首遍重构/相位搜索保持原窗口
    params_first, final_ext_lo, final_ext_hi = _split_final_ext(params_first)
    params_first.update(
        {
            "direct_phase_search": False,
            "display_phase_search": False,
            # 0.2.160:首遍脚本不加 POLY -time——直接维相位搜索以原始
            # recon 平面为输入(POLY 会改变对称性评分,曾把 sampleC 直接维
            # 相位从 (0,0) 带偏到 (40,-15) 且分数更低);POLY -time 只进
            # 终跑完整脚本(params_final 的 direct_poly_time)。
        }
    )
    first = backend.reconstruct_nus(experiment, params_first, progress=progress)
    if not first.get("success") or not first.get("spectrum_path"):
        raise RuntimeError(f"第一遍 SMILE 重构失败: {first.get('message')}")
    logs: list[str] = list(diag_logs) + [
        f"第一遍 SMILE 重构完成: {first.get('spectrum_path')}"
    ]
    if progress is not None:
        progress("第一遍 SMILE 完成")
    # 0.2.156:初跑脚本保留(未含优化相位的完整 SMILE 脚本),命名
    # {dataset_id}_before_optimize.com,便于与优化后终跑脚本对照
    first_script = work / f"{experiment.dataset_id}_nus.com"
    no_opt_script = work / f"{experiment.dataset_id}_before_optimize.com"
    try:
        if first_script.is_file():
            no_opt_script.write_text(
                first_script.read_text(encoding="utf-8"),
                encoding="utf-8",
                newline="\n",
            )
            logs.append(f"初跑脚本保留: {no_opt_script.name}")
    except OSError as exc:  # noqa: BLE001 - 保留失败不影响流程
        logs.append(f"初跑脚本保留失败: {exc}")
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
    import time as _time

    from core.optimization.phase_search import search_direct_phase_on_spectrum

    sampling_block = dict((base_params or {}).get("sampling") or {})
    if sampling_block.get("auto_phase") is False:
        direct_phase = (0.0, 0.0)
        logs.append("sampling.auto_phase=False,直接维相位保持 (0,0)(跳过搜索)")
    else:
        cache = _load_direct_phase_cache(
            work, experiment, params_first, planes.shape
        )
        if cache is not None:
            direct_phase = (float(cache["p0"]), float(cache["p1"]))
            logs.append(
                f"直接维相位复用缓存 phase.json: {direct_axis}="
                f"({direct_phase[0]:g}°, {direct_phase[1]:g}°)"
            )
        else:
            last_s = _estimate_direct_phase_seconds(work)
            if progress is not None:
                if last_s:
                    progress(
                        f"直接维相位搜索中(上次约 {last_s:.0f} 秒),请稍候"
                    )
                else:
                    progress("直接维相位搜索中(首次运行,通常数十秒),请稍候")
            t0 = _time.time()
            direct_est = search_direct_phase_on_spectrum(
                planes, axis=0, metric="symmetry", progress=progress
            )
            elapsed = _time.time() - t0
            if progress is not None:
                progress(f"直接维相位搜索完成,耗时 {elapsed:.1f} 秒")
            logs.append(f"直接维相位搜索完成,耗时 {elapsed:.1f} 秒")
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
                _save_direct_phase_cache(
                    work, experiment, params_first, planes.shape,
                    direct_phase[0], direct_phase[1], float(direct_est[2]),
                    elapsed,
                )
            else:
                logs.append(
                    "直接维对称性搜索无干净信号峰或置信度不足,保持 (0,0)"
                )
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
        t_axis = time.time()
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
        logs.append(f"{axis} 相位搜索完成,耗时 {time.time() - t_axis:.1f} 秒")
    if len(indirect_axes) >= 2:
        t_joint = time.time()
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
        logs.append(f"联合复核完成,耗时 {time.time() - t_joint:.1f} 秒")
    # 处理参数优化(基线/填零/窗函数):联合复核后、终跑前;各维最终相位
    # 与优化后的处理参数一起填入初始脚本,生成新的完整脚本做终跑——
    # 直接维相位进 step1 PS(EXT 后,与 recon 平面内存旋转同归一化),
    # 间接维相位进 step3 PS;不写 nus3d_rc_ph 旋转副本。
    if progress is not None:
        progress("联合复核完成,开始处理参数优化(基线/填零/窗函数)")
    t_opt = time.time()
    proc = _optimize_nus_processing(
        experiment,
        backend,
        work,
        fixed,
        base_params,
        progress=progress,
    )
    logs += proc["logs"]
    logs.append(f"处理参数优化(基线/填零/窗函数)完成,耗时 {time.time() - t_opt:.1f} 秒")
    params_final = dict(base_params or {})
    params_final.pop("final_ext_lo", None)
    params_final.pop("final_ext_hi", None)
    params_final = _apply_final_ext(params_final, final_ext_lo, final_ext_hi)
    # 0.2.162-补16:终跑直接维范围变化时,p1 按窗口宽度比例重归一化
    # (p1 表示跨整个提取窗口的总度数,范围变窄后同一 p1 物理斜率会放大)
    direct_phase_final = _renormalize_direct_p1(
        direct_phase, params_first, params_final
    )
    if direct_phase_final != direct_phase:
        logs.append(
            f"直接维相位按终跑窗口重归一化: {direct_axis} "
            f"p1={direct_phase[1]:g}° → {direct_phase_final[1]:g}°"
        )
    params_final.update(
        {
            "direct_phase_search": False,
            "display_phase_search": False,
            "direct_poly_time": bool(diagnostics.get("apply_poly_time")),
            "direct_phase_override": [
                float(direct_phase_final[0]),
                float(direct_phase_final[1]),
            ],
            "phases": {
                axis: [float(p0), float(p1)]
                for axis, (p0, p1) in fixed.items()
            },
            "baseline": proc["baseline"],
            "zero_fill": proc["zero_fill"],
            "window": proc["window"],
        }
    )
    if progress is not None:
        progress("终跑(完整脚本,含各维最终相位)中")
    t_final = time.time()
    final = backend.reconstruct_nus(experiment, params_final, progress=progress)
    backend_runs += 1
    if not final.get("success") or not final.get("spectrum_path"):
        raise RuntimeError(f"终跑(完整脚本)失败: {final.get('message')}")
    if progress is not None:
        progress("终跑完成")
    logs.append(
        "终跑: 各维最终相位已填入完整脚本 "
        f"(直接维 {direct_axis}=({direct_phase_final[0]:g}°, {direct_phase_final[1]:g}°)"
        + "".join(
            f" {axis}=({p0:g}°, {p1:g}°)"
            for axis, (p0, p1) in fixed.items()
        )
        + "),未生成 nus3d_rc_ph 旋转副本"
    )
    logs += list(final.get("logs", []))
    logs.append(f"终跑完成,耗时 {time.time() - t_final:.1f} 秒")
    _append_final_summary(
        logs,
        str(final["spectrum_path"]),
        direct_axis=direct_axis,
        direct_phase=direct_phase_final,
        phases=fixed,
        backend_runs=backend_runs,
        baseline=proc["baseline"],
        zero_fill=proc["zero_fill"],
        window=proc["window"],
        diagnostics=diagnostics,
        progress=progress,
    )
    _cleanup_unified_intermediates(work, experiment.dataset_id)
    return {
        "phases": fixed,
        "direct_phase": direct_phase_final,
        "baseline": proc["baseline"],
        "zero_fill": proc["zero_fill"],
        "window": proc["window"],
        "diagnostics": diagnostics,
        "spectrum_path": str(final["spectrum_path"]),
        "backend_runs": backend_runs,
        "logs": logs,
    }
