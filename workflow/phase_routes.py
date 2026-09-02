"""统一相位优化途径(替代简单/进阶分派,2026-08-17)。

统一方案:第一遍逐维复型预览(仅搜索轴 PS 不加 -di,其它轴按已固定
相位加 -di,零填零)→ 内存调相(旧算法判断标准:固定迹线中位数净吸收,
零额外后端)→ 处理参数优化(基线/填零/窗函数)→ 完整终跑(0.2.199-补29fj
起跳过联合复核):
各维最终相位填入初始脚本生成新的完整脚本(NUS 不再写 nus3d_rc_ph 旋转
副本;直接维相位进 step1 PS(EXT 后),间接维相位进 step3 PS)。
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from backend.memory_disk import INTERMEDIATE_SUBDIR
from backend.runtime import cancel_requested
from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method


def _phase_delta(a: tuple[float, float], b: tuple[float, float]) -> float:
    """相位差(p0 环向差 + p1 差),用于迭代收敛判断(0.2.199-补29do)。"""
    p0 = abs((a[0] - b[0] + 180.0) % 360.0 - 180.0)
    return float(p0 + abs(a[1] - b[1]))


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


def _preview_memory_warning(
    path, *, axis="", progress=None, logs=None
) -> None:
    """相位搜索预览数组内存提示(0.2.199-补29ec):按文件大小估计 complex128
    数组,超过可用内存 85% 时提示(不阻断;失败时另有 MemoryError 明确报错)。"""
    try:
        size_bytes = Path(path).stat().st_size * 2
        est_mb = size_bytes / (1024.0 * 1024.0)
        from backend.memory_guard import available_memory_mb

        avail = available_memory_mb()
        if est_mb > avail * 0.85:
            msg = (
                f"内存提示: {axis} 复型预览约 {est_mb:.0f}MB,"
                f"可用约 {avail}MB,可能内存不足"
                "(失败请释放内存或减小数据)"
            )
            if progress is not None:
                progress(msg)
            if logs is not None:
                logs.append(msg)
    except OSError:
        pass


def _load_preview_with_memory_guard(
    path, *, axis, unpack_axis=None, progress=None, logs=None
) -> np.ndarray:
    """读复型预览并做内存提示(0.2.199-补29ec):读前估计内存超限提示;
    MemoryError 转明确 RuntimeError,避免崩溃/被杀无提示。"""
    _preview_memory_warning(path, axis=axis, progress=progress, logs=logs)
    try:
        return _read_complex_preview(path, unpack_axis=unpack_axis)
    except MemoryError:
        size_mb = 0.0
        try:
            size_mb = Path(path).stat().st_size * 2 / (1024.0 * 1024.0)
        except OSError:
            pass
        suffix = f"(约 {size_mb:.0f}MB)" if size_mb else ""
        raise RuntimeError(
            "内存不足: 无法加载 "
            f"{axis} 复型预览{suffix},"
            "请释放内存或减小数据"
        ) from None



def _read_complex_ft3(path: Path | str) -> np.ndarray:
    """读全复型 3D 终谱(0.2.199-补18 keep_complex 模式)。

    finalize 全部 PS 不加 -di 时,间接维(F2/F1)实虚交错存轴 0/1,
    直接维=最后一轴(平面流,不复交)。返回 (F2, F1, F3) 复型数组。
    """
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        return arr.astype(np.complex128)
    cplx = arr[0::2] + 1j * arr[1::2]
    if cplx.ndim >= 2:
        cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    return cplx.astype(np.complex128)


def _read_real_ft3(path: Path | str) -> np.ndarray:
    """读实型 3D/2D 终谱(0.2.199-补29q)。

    直接维搜索的实型 finalize 预览(全轴 PS 带 -di)不是复型交错布局,
    不能用 _read_complex_ft3(会把相邻实点错配成实虚对,100/101 实测
    形状 (512,512,670) 被错读成 (256,256,670) 导致结果偏 ~6-9°)。
    直接维 = 最后一轴。
    """
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    return np.asarray(arr, dtype=float)


def _cleanup_unified_intermediates(
    work: Path,
    dataset_id: str,
    *,
    experiment: Any | None = None,
    backend: Any | None = None,
) -> None:
    """清理 unified 流程中间产物(相位预览/窗填零评分谱 + NUS 重构平面目录)。

    清理(仅限 process 工作目录):
    - {dataset_id}_preview_* 的 .com/.ft2/.ft3/.fdf
    - {dataset_id}_joint* 的 .com/.ft3/.fdf
    - {dataset_id}_win1* / _win2* / _win3* / _winzf_* 的 .com/.ft3/.fdf
      (_winzf_* 为 0.2.199-补29em:旧版填零/窗候选遗留,当前不再生成但
      历史数据目录仍有残留)
    - 0.2.199-补29dy(用户):NUS 重构中间目录 nus3d_1 / nus3d_rc /
      nus3d_rc_ph / nus2d 不留下(清理后再重新优化会重跑 SMILE,属正常代价)
    - 0.2.199-补29em(用户):uniform 窗函数/相位优化中间文件未删除——某些
      调用路径未设置 backend.work_dir 时,后端会把 preview/joint 等写进
      默认工作目录 raw.parent/{dataset_id}.nmrpipe,该目录从不被清理;现
      一并删除(工作区数据目录下的同名目录与源数据旁的回退目录,均为纯
      临时产物,不包含终谱/最终脚本)。

    保留项(终谱、最终脚本、phase.json、fid/、nuslist、smile.log)不受影响。
    """
    if not work.is_dir():
        return
    # 0.2.199-补29ez:中间产物统一在 work/_intermediate(磁盘模式整目录清理;
    # 内存盘模式为符号链接,由 generate_spectrum 结束拆除)
    _intermediate = work / INTERMEDIATE_SUBDIR
    if _intermediate.is_dir() and not _intermediate.is_symlink():
        shutil.rmtree(_intermediate, ignore_errors=True)
    exts = (".com", ".ft2", ".ft3", ".fdf")
    for base_pattern in (
        f"{dataset_id}_preview_*",
        f"{dataset_id}_direct_*",
        f"{dataset_id}_joint*",
        f"{dataset_id}_win1*",
        f"{dataset_id}_win2*",
        f"{dataset_id}_win3*",
        f"{dataset_id}_winzf_*",
    ):
        for p in work.glob(base_pattern):
            if p.suffix in exts:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    for _dir in ("nus3d_1", "nus3d_rc", "nus3d_rc_ph", "nus2d"):
        _target = work / _dir
        if _target.is_dir():
            shutil.rmtree(_target, ignore_errors=True)
    # 0.2.199-补29em:清理后端默认工作目录回退(work_dir 未设置时
    # preview/joint 等中间产物落在 raw.parent/{dataset_id}.nmrpipe)
    fallbacks: list[Path] = [work.parent / f"{dataset_id}.nmrpipe"]
    if experiment is not None:
        try:
            fallbacks.append(
                Path(experiment.source_path).parent / f"{dataset_id}.nmrpipe"
            )
        except (TypeError, ValueError):
            pass
    for _fb in fallbacks:
        if _fb != work and _fb.is_dir():
            shutil.rmtree(_fb, ignore_errors=True)


def _append_final_summary(
    logs: list[str],
    spectrum_path: str,
    *,
    direct_axis: str = "",
    direct_phase: tuple[float, float] | None = None,
    phases: dict[str, tuple[float, float]] | None = None,
    backend_runs: int = 0,
    baseline: Any = None,
    baseline_scores: dict[str, float] | None = None,
    zero_fill: Any = None,
    window: Any = None,
    diagnostics: dict[str, Any] | None = None,
    optimization_logs: list[str] | None = None,
    peak_sign: str = "uniform",
    progress: Callable[[str], None] | None = None,
) -> None:
    """末尾报告:谱图质量与数据质量诊断(0.2.169-补,用户可读)。

    结构:
      ◆ 最终谱图质量 —— 综合判定 + 信噪比/相位/基线/伪影分项等级与分数;
        基线不平且基线优化存在时输出原因(优化基准/保持 off 的门槛)。
      ◆ 数据质量诊断 —— 处理前 FID 监测结论(检出 N 项/已自动处理 M 项)。
      ◆ 处理参数与优化 —— 与 pipeline 参数报告共用 format_optimization_report。
    全部行同时经 progress 进入 GUI 日志面板。"""
    from workflow.optimization_report import (
        format_optimization_report,
        spectrum_quality_report_lines,
    )

    lines: list[str] = ["== 谱图质量与数据质量报告 =="]
    # 存储轴序(与 nmrglue 读取一致,0.2.199-补29 修正 3D 标签):
    # 2D (F1,F2);3D (F2,F1,F3)
    storage_axes = (
        ["F1", "F2"] if direct_axis == "F2" else ["F2", "F1", "F3"]
    )
    reports = list((diagnostics or {}).get("reports") or [])
    lines.append("◆ 数据质量诊断(处理前的数据监测,FID 检查)")
    if reports:
        auto_count = int(bool((diagnostics or {}).get("apply_poly_time")))
        auto_count += int(
            int((diagnostics or {}).get("repaired_badpoints") or 0) > 0
        )
        suffix = f"(已自动处理 {auto_count} 项)" if auto_count else "(未自动处理)"
        lines.append(f"   ⚠ 检出 {len(reports)} 项问题 {suffix}")
        for i, report in enumerate(reports, 1):
            lines.append(f"     {i}. {report}")
    else:
        lines.append("   ✓ 未检出直流偏置、尖峰坏点、首点异常、宽带峰或漂移")
    lines.append("◆ 处理参数与优化")
    lines += format_optimization_report(
        {
            "phase_route": "unified",
            "direct_phase": direct_phase,
            "phases": {k: v for k, v in (phases or {}).items()},
            "baseline": baseline,
            "window": window,
            "zero_fill": zero_fill,
            "backend_runs": backend_runs,
        }
    )
    # 0.2.199-补29ab:报告顺序 = 数据质量 → 处理参数与优化 → 最终谱图质量
    lines += spectrum_quality_report_lines(
        str(spectrum_path),
        optimization_logs=optimization_logs,
        axis_names=storage_axes,
        sign_mode=peak_sign,
        baseline_scores=baseline_scores,
        progress=progress,
    )
    logs += lines
    if progress is not None:
        for line in lines:
            progress(line)


def _template_auto_phase(experiment: Experiment) -> bool:
    """实验类型级自动相位开关(presets processing_hints.auto_phase)。

    False(如 HMBC 幅度谱)时统一路径全轴跳过相位搜索,相位保持 (0,0);
    默认 True(相位敏感实验自动优化直接维/间接维相位)。模板缺失/解析
    失败时回退 True,不阻断处理。
    """
    try:
        from core.experiments.registry import REGISTRY

        tpl = REGISTRY.get(experiment.experiment_type.name)
        if tpl is not None:
            return bool(tpl.processing_hints.get("auto_phase", True))
    except Exception:  # noqa: BLE001 - 模板查询失败不阻断处理
        pass
    return True


def _template_peak_sign(experiment: Experiment) -> str:
    """实验类型峰符号(presets peak_sign):uniform 同号 / mixed 正负共存。
    与 _template_auto_phase 同源;模板缺失/解析失败回退 uniform。"""
    try:
        from core.experiments.registry import REGISTRY

        tpl = REGISTRY.get(experiment.experiment_type.name)
        if tpl is not None:
            return str(getattr(tpl, "peak_sign", "uniform") or "uniform")
    except Exception:  # noqa: BLE001 - 模板查询失败不阻断处理
        pass
    return "uniform"


def _apply_ext_opt_enabled(params: dict[str, Any]) -> bool:
    """「应用此范围到优化过程」开关(0.2.199-补3):默认开启。"""
    return str(params.get("apply_ext_to_opt", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _split_final_ext(
    params: dict[str, Any],
) -> tuple[dict[str, Any], Any, Any, bool]:
    """从参数取出直接维范围(final_ext_lo/final_ext_hi)与优化应用开关。

    返回 (剩余参数, ext_lo, ext_hi, apply_to_opt);首遍/复型预览路径使用
    剩余参数(默认 6.5-10.5 大窗口),终跑路径把取出的值映射回
    ext_lo/ext_hi(_apply_final_ext);apply_to_opt 开启时调用方把范围
    同时写入优化过程(首遍重构/相位搜索与基线/填零/窗函数评估)。
    """
    p = dict(params)
    final_lo = p.pop("final_ext_lo", None)
    final_hi = p.pop("final_ext_hi", None)
    apply_to_opt = _apply_ext_opt_enabled(p)
    p.pop("apply_ext_to_opt", None)
    return p, final_lo, final_hi, apply_to_opt


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
    """统一方案(替代简单/进阶分派):逐轴复型预览 → 内存调相
    (旧算法判断标准,零额外后端)→ 完整终跑。

    uniform(0.2.199-补29dr):初始逐轴搜索(间接维先行)→ 直接维确定后
    间接维重搜一轮(不再迭代;0.2.199-补29fj 起跳过联合复核);预览仅搜索轴 PS 不加 -di
    (保持 0,0),其它轴按已固定相位加 -di,auto 完整填零;
    NUS:SMILE 一次出复型 recon 平面,直接维在实型终谱+投影迹线上内存
    搜索(HT),间接维内存复刻 finalize 链完整搜索,直接维确定后间接维
    再重搜一轮;最后把各维最终相位填入完整脚本重跑出良谱(不写旋转
    平面副本)。
    """
    from workflow.memory_phase_search import search_axis_memory

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
        # 0.2.199-补29ab:诊断结果实时输出在进度消息之后
        for _line in diag_logs:
            if progress is not None:
                progress(_line)
    except Exception as exc:  # noqa: BLE001 - 诊断失败不阻断谱图生成
        diag_logs = [f"数据质量诊断失败: {exc}"]
    # 0.2.162-补15:用户指定的终跑直接维范围(final_ext_lo/final_ext_hi);
    # 0.2.199-补3:开启「应用此范围到优化过程」时同时进入复型预览/优化评估,
    # 否则首遍复型预览保持默认大范围,仅终跑用该范围
    params, final_ext_lo, final_ext_hi, apply_ext_opt = _split_final_ext(params)
    if apply_ext_opt:
        params = _apply_final_ext(params, final_ext_lo, final_ext_hi)
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    direct_axis = "F2" if experiment.ndim == 2 else "F3"
    sign_mode = _sign_mode(experiment)
    axes = [dim.logical_axis for dim in experiment.dimensions]
    # 0.2.75:均匀路径先间接后直接(旧算法顺序,直接维在间接维校正后的谱上锁点);
    # 0.2.199-补29ad:uniform 直接维复型数据有真实虚部,直接在复型预览上搜
    # (不套用 NUS 的 SMILE 强制实型 + HT 方案)
    search_axes = [a for a in axes if a != direct_axis] + [direct_axis]
    fixed: dict[str, tuple[float, float]] = {}
    # 0.2.169:数据质量诊断在流程最开头执行,日志必须排在预览/优化之前
    # (此前 diag_logs 延后到优化后拼接,时序错乱;NUS 分支本就在开头)
    logs: list[str] = list(diag_logs)
    # 0.2.167:实验类型级 auto_phase(presets processing_hints)——幅度谱
    # (HMBC 等)全轴跳过相位搜索保持 (0,0);相位敏感实验按 plan 相位
    # 节点过滤 magnitude 间接维(QF 无 PS 概念),只搜索真正有相位步骤的轴
    auto_phase = _template_auto_phase(experiment)
    if not auto_phase:
        search_axes = []
        logs.append(
            f"实验类型 {experiment.experiment_type.name}: 幅度谱不自动调相,"
            "跳过全轴相位搜索(保持 0,0)"
        )
    else:
        search_axes = [
            a for a in search_axes if f"phase_{a}" in plan.dag.nodes
        ]
    # 0.2.199-补29du(用户,VM 实测 sampleI、sampleH):相位搜索预览全轴不填零。
    # 填零对数据敏感:auto 填零 sampleI F1=90° 对但 sampleH F1=70° 错(应 85);
    # 零填零 sampleI F1=90°(重搜保证)、sampleH F1=87.5°(差 2.5° 可接受),
    # 直接维均 310°;故预览统一零填零,避免「填零对有的合理对有的不合理」。
    zf_phase = {"zero_fill": {a: {"mode": "none"} for a in axes}}
    backend_runs = 0
    # 0.2.199-补29dr(用户):不做迭代——初始逐轴搜索(间接维先行)后,
    # 直接维确定,再对间接维重搜一轮(预览带直接维固定相位),不再交替迭代。
    # 间接维先行:直接维迹线锁定依赖间接维校正后的谱;直接维先行实测发散
    # (sampleI F2 在 F1=(0,0) 下得 172°)。
    for axis in search_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        t_axis = time.time()
        if progress is not None:
            progress(f"相位优化中: {axis} 复型预览中")
        resp = backend.process(
            experiment,
            plan,
            direct_phase_override=dict(fixed) if fixed else None,
            params={**params, **zf_phase, "preview_axis": axis},
            out_file=out_file,
            script_name=f"{experiment.dataset_id}_preview_{axis}.com",
            progress=progress,
        )
        backend_runs += 1
        if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(f"复型预览({axis})失败: {resp.get('message')}")
        if progress is not None:
            progress(f"相位优化中: {axis} 复型预览完成")
        ax = _axis_index(axis, experiment.ndim)
        arr = _load_preview_with_memory_guard(
            str(resp["spectrum_path"]), axis=axis, unpack_axis=ax,
            progress=progress, logs=logs,
        )
        est = search_axis_memory(
            arr, ax, sign_mode=sign_mode, cancel=cancel_requested
        )
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
            phase = resolved
        else:
            phase = est.phase
        fixed[axis] = phase
        logs += est.logs
        logs.append(
            f"{axis}: 内存相位 = ({phase[0]:g}°, {phase[1]:g}°) "
            f"score={est.score:.2f}"
        )
        logs.append(f"{axis} 相位搜索完成,耗时 {time.time() - t_axis:.1f} 秒")
    # 0.2.199-补29fj(用户):跳过联合复核——历史与实测(900/102/101)均未
    # 越过 0.05 门控纠正顺序搜索相位(补29dn 修 sampleI 靠填零+直接维定后
    # 重搜,非复核);直接维定案后 r2 重搜已覆盖。joint_recheck_memory 代码
    # 与其测试保留(不调用,供参考/将来需要时恢复)。
    # 0.2.199-补29dr(用户):直接维确定后,间接维再优化一轮(预览带直接维
    # 固定相位;排除本轴自身相位,统一/内存语义,sampleI F1 回粗网格最优 90°)
    indirect_axes = [a for a in axes if a != direct_axis]
    if len(search_axes) >= 2 and direct_axis in fixed:
        for axis in indirect_axes:
            out_file = f"{experiment.dataset_id}_preview_{axis}_r2.{ext}"
            t_axis = time.time()
            if progress is not None:
                progress(f"相位优化中: {axis} 复型预览(直接维已定)中")
            resp = backend.process(
                experiment,
                plan,
                direct_phase_override={
                    k: v for k, v in fixed.items() if k != axis
                },
                params={**params, **zf_phase, "preview_axis": axis},
                out_file=out_file,
                script_name=f"{experiment.dataset_id}_preview_{axis}_r2.com",
                progress=progress,
            )
            backend_runs += 1
            if not resp.get("success") or not resp.get("spectrum_path"):
                raise RuntimeError(
                    f"复型预览重搜({axis})失败: {resp.get('message')}"
                )
            ax = _axis_index(axis, experiment.ndim)
            arr = _load_preview_with_memory_guard(
                str(resp["spectrum_path"]), axis=axis, unpack_axis=ax,
                progress=progress, logs=logs,
            )
            est = search_axis_memory(
                arr, ax, sign_mode=sign_mode, cancel=cancel_requested
            )
            if est is None:
                raise RuntimeError(f"内存相位重搜({axis})无可用迹线")
            if sign_mode == "mixed":
                resolved = _disambiguate_180_mixed(
                    arr, ax, est.phase, experiment, axis
                )
                phase = resolved
            else:
                phase = est.phase
            fixed[axis] = phase
            logs += est.logs
            logs.append(
                f"{axis}: 内存相位重搜(直接维已定) = "
                f"({phase[0]:g}°, {phase[1]:g}°) score={est.score:.2f}"
            )
            logs.append(
                f"{axis} 相位重搜完成,耗时 {time.time() - t_axis:.1f} 秒"
            )
    # 0.2.166:auto_phase=False 时直接维未参与搜索,保持 (0,0)
    fixed.setdefault(direct_axis, (0.0, 0.0))
    # 0.2.163-补6:处理参数优化(基线/直接维窗/填零+间接窗),与 NUS 对称;
    # uniform 无重构,候选重跑完整 process 更快
    if progress is not None:
        progress("相位搜索完成,开始处理参数优化(基线/填零/窗函数)")
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
    logs += proc["logs"]
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
        baseline_scores=proc.get("baseline_scores"),
        zero_fill=proc["zero_fill"],
        window=proc["window"],
        diagnostics=diagnostics,
        optimization_logs=proc["logs"],
        peak_sign=_template_peak_sign(experiment),
        progress=progress,
    )
    _cleanup_unified_intermediates(
        work, experiment.dataset_id, experiment=experiment, backend=backend
    )
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
    """读 2D SMILE 重构平面 recon.ft1(布局 (F2 频, F1 时),直接维在轴 0)。

    0.2.199-补29:仅 2D 使用(直接维相位搜索基底);3D 直接维搜索已改用
    keep_complex 复型终谱(补18),不再加载 3D 平面——旧代码无条件加载
    全部 test*.ft1 且用简单轴 0 交错拆包,对真实 SMILE 输出(13C 轴
    hypercomplex 4×75 存储)拆错,又白占内存(数百 MB)。
    """
    if experiment.ndim >= 3:
        # 3D 不加载(直接维搜索用 keep_complex 终谱),保持 2D 语义兼容
        return np.zeros((0, 0, 0))
    recon = work / "nus2d" / "recon.ft1"
    if not recon.is_file():
        raise RuntimeError(f"缺少 2D 重构平面: {recon}")
    return _read_complex_preview(recon)

def _chosen_baseline_scores(
    baseline_cfg: dict[str, dict[str, Any]],
    scores: dict[str, dict[str, float]],
) -> dict[str, float]:
    """各轴基线优化选中配置的评分(0.2.199-补29z):

    最终谱图质量报告的基线分与优化时一致——直接采用优化网格里选中
    配置(mode/order)的分数,不再对终谱另行评估(两基准不同会造成
    优化 66.8 而报告 50 的困惑)。
    """
    out: dict[str, float] = {}
    for axis, cfg in baseline_cfg.items():
        mode = str(cfg.get("mode", "auto"))
        order = int(cfg.get("order", 0) or 0)
        axis_scores = scores.get(axis) or {}
        if mode == "off":
            key = "off:0"
        elif mode == "order":
            key = f"order:{max(order, 1)}"
        else:
            key = "auto:1"
        val = axis_scores.get(key)
        out[axis] = float(val) if val is not None else 0.0
    return out


def _optimize_uniform_processing(
    experiment: Experiment,
    backend: Any,
    work: Path,
    fixed: dict[str, tuple[float, float]],
    base_params: dict[str, Any] | None,
    plan: Any = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """逐轴相位搜索后的处理参数优化(uniform 2D/3D):基线(内存评分)+ 直接维
    窗函数(FID 内存评分)+ 间接维窗函数(FID 内存评分,候选含无窗)。

    顺序约束(用户要求,0.2.190):基线校正先于窗函数优化——2) 基线 →
    2.5) 直接维窗 → 3) 间接维窗;窗候选
    不反过来影响基线选择(0.2.189 曾因 spectrum_quality 相位/基线联动
    把间接维从无窗带偏)。uniform 无 SMILE 重构,任何评估失败均降级:
    保持 base_params 既有配置或默认,不阻断终跑。
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_params = {a: {"mode": "auto"} for a in axes}
    base = dict(base_params or {})
    out_logs: list[str] = []
    baseline_cfg = dict(base.get("baseline") or {})
    window_cfg = base.get("window")
    # 1) 联合复核谱:最终相位 + 完整填零,作基线评分基底(process 一次);
    #    窗函数走 FID 内存评分,不消费该谱(0.2.199-补29fg 移除 2.1 重渲);
    #    开启「应用此范围到优化过程」时(0.2.199-补3)评估谱用用户直接维范围
    opt_ext = {k: base[k] for k in ("ext_lo", "ext_hi") if k in base}
    joint_file = f"{experiment.dataset_id}_joint.{ext}"
    resp = backend.process(
        experiment,
        plan,
        direct_phase_override=dict(fixed) if fixed else None,
        params={"zero_fill": zf_params, **opt_ext},
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
    opt = None
    try:
        from workflow.baseline_optimize import optimize_baseline

        if progress is not None:
            progress("基线优化中(内存评分)")
        opt = optimize_baseline(
            experiment,
            base_path,
            progress=progress,
            cancel=cancel_requested,
        )
        baseline_cfg = dict(opt.baseline)
        out_logs += opt.logs
    except Exception as exc:  # noqa: BLE001 - 基线评估失败不影响相位/终跑
        out_logs.append(f"基线优化(嵌入)失败: {exc}")
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
    # 3) 间接维窗函数:FID 间接维时间轴内存评分(候选含无窗,分辨率受限
    #    间接维加分辨率保留因子),写回每轴最优;直接维窗已由 2.5 单独
    #    优化。0.2.190:恢复真实选窗(0.2.189 硬编码固定无窗是对需求的误读)。
    try:
        from workflow.window_optimize import optimize_indirect_windows_from_work

        if progress is not None:
            progress("间接维窗函数优化中(FID 内存评分,不重跑 process)")
        ires = optimize_indirect_windows_from_work(
            work, experiment, current=window_cfg
        )
        if ires.changed:
            win = dict(window_cfg or {})
            win.update(ires.choice)
            window_cfg = win
        out_logs += ires.logs
    except Exception as exc:  # noqa: BLE001 - 窗优化失败不影响相位/终跑
        out_logs.append(f"间接维窗优化失败: {exc}")

    return {
        "baseline": baseline_cfg,
        # 0.2.199-补29z:各轴基线优化选中配置的评分——最终谱图质量报告
        # 的基线分与优化时一致(不再对终谱另行评估造成两套数)
        "baseline_scores": _chosen_baseline_scores(
            baseline_cfg, opt.scores if opt is not None else {}
        ),
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
    """逐轴相位搜索后的处理参数优化(NUS):基线(内存评分)+ 直接维窗(FID 内存
    评分)+ 间接维窗函数(重构平面内存评分,候选含无窗,不重跑 SMILE)。

    顺序约束(用户要求,0.2.190):基线校正先于窗函数优化——2) 基线 →
    2.5) 直接维窗 → 3) 间接维窗。返回
    {"baseline", "zero_fill", "window", "logs"},优化结果写回终跑完整
    脚本;直接维基线仍逐轴评分后写回(终跑 step1 POLY 应用)。任何评估
    失败均降级:保持 base_params 既有配置或默认,不阻断终跑。
    """
    axes = [dim.logical_axis for dim in experiment.dimensions]
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    zf_params = {a: {"mode": "auto"} for a in axes}
    base = dict(base_params or {})
    out_logs: list[str] = []
    baseline_cfg = dict(base.get("baseline") or {})
    window_cfg = base.get("window")
    # 1) 联合复核谱:间接维最终相位 + 完整填零,作基线评分基底
    #    (窗函数走 recon/FID 内存评分,不消费该谱;0.2.199-补29fg 移除 2.1 重渲)
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
    opt = None
    try:
        from workflow.baseline_optimize import optimize_baseline

        if progress is not None:
            progress("基线优化中(内存评分)")
        opt = optimize_baseline(
            experiment,
            base_path,
            progress=progress,
            cancel=cancel_requested,
        )
        baseline_cfg = dict(opt.baseline)
        out_logs += opt.logs
    except Exception as exc:  # noqa: BLE001 - 基线评估失败不影响相位/终跑
        out_logs.append(f"基线优化(嵌入)失败: {exc}")
    # 2.5) 直接维窗函数:FID 直接维迹内存评分(不重跑 SMILE 重构),
    #      分辨率优先 + 信噪比/线形平衡,写回终跑 step1 SP
    try:
        from workflow.window_optimize import optimize_direct_window_from_work

        if progress is not None:
            progress("直接维窗函数优化中(FID 内存评分,不重跑 SMILE)")
        wres = optimize_direct_window_from_work(
            work, experiment, current=(window_cfg or {}).get(direct_axis)
        )
        out_logs += wres.logs
        if wres.changed:
            # 0.2.199-补11:NUS 直接维窗固定 SP(SMILE 要求直接维加窗且尾部
            # 衰减),窗候选不覆盖直接维;结果仅 uniform 路径使用
            out_logs.append(
                "直接维窗: NUS SMILE 要求直接维加窗(SP),候选不覆盖直接维,"
                "保持默认 SP"
            )
    except Exception as exc:  # noqa: BLE001 - 窗优化失败不影响相位/终跑
        out_logs.append(f"直接维窗优化失败: {exc}")
    # 3) 间接维窗函数:SMILE 重构平面(F1/F2 时间域)内存评分,候选含无窗,
    #    不重跑 SMILE;直接维窗已由 2.5 单独优化。0.2.190:恢复真实选窗
    #    (0.2.189 硬编码固定无窗是对需求的误读)。
    try:
        from workflow.window_optimize import optimize_indirect_windows_from_recon

        if progress is not None:
            progress("间接维窗函数优化中(重构平面内存评分,不重跑 SMILE)")
        ires = optimize_indirect_windows_from_recon(
            work, experiment, current=window_cfg
        )
        if ires.changed:
            win = dict(window_cfg or {})
            win.update(ires.choice)
            window_cfg = win
        out_logs += ires.logs
    except Exception as exc:  # noqa: BLE001 - 窗优化失败不影响相位/终跑
        out_logs.append(f"间接维窗优化失败: {exc}")

    return {
        "baseline": baseline_cfg,
        # 0.2.199-补29z:各轴基线优化选中配置的评分——最终谱图质量报告
        # 的基线分与优化时一致(不再对终谱另行评估造成两套数)
        "baseline_scores": _chosen_baseline_scores(
            baseline_cfg, opt.scores if opt is not None else {}
        ),
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
    内存搜索 → 间接维内存复刻 finalize 链完整逐维搜索(0.2.199-补29fj 起
    跳过联合复核)→ 处理参数优化(基线/填零/窗函数)→ 终跑:各维最终相位填入初始脚本生成
    新的完整脚本(直接维相位进 step1 PS,EXT 后;间接维进 step3 PS),
    不再写 nus3d_rc_ph 旋转副本。"""
    from core.data.internal_data_model import AxisRole
    from workflow.memory_phase_search import search_axis_memory

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
        # 0.2.199-补29ab:诊断结果实时输出在进度消息之后
        for _line in diag_logs:
            if progress is not None:
                progress(_line)
    except Exception as exc:  # noqa: BLE001 - 诊断失败不阻断谱图生成
        diag_logs = [f"数据质量诊断失败: {exc}"]
    params_first = dict(base_params or {})
    # 0.2.162-补15:终跑直接维范围(final_ext_lo/final_ext_hi)默认只进终跑;
    # 0.2.199-补3:开启「应用此范围到优化过程」时同时进入首遍重构/相位搜索
    # 与优化评估(重构平面窗口即评估窗口,且直接维窗口越窄 SMILE 内存越低)
    params_first, final_ext_lo, final_ext_hi, apply_ext_opt = _split_final_ext(
        params_first
    )
    if apply_ext_opt:
        params_first = _apply_final_ext(params_first, final_ext_lo, final_ext_hi)
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
    direct_axis = "F3" if experiment.ndim >= 3 else "F2"
    sign_mode = _sign_mode(experiment)
    indirect_axes = [
        dim.logical_axis
        for dim in experiment.dimensions
        if dim.role is not AxisRole.DIRECT
    ]
    # 0.2.167:幅度谱(HMBC 等)间接维同样不搜索——finalize 复型预览跳过,
    # 相位保持 (0,0)(plan 无相位节点,搜索无意义且会污染日志)
    auto_phase = _template_auto_phase(experiment)
    if not auto_phase:
        indirect_axes = []
        logs.append("间接维: 幅度谱不自动调相,跳过 finalize 复型预览与搜索")
    # 间接维:finalize 复型预览(该轴 PS 不加 -di,其它轴按已固定相位 -di,
    # 零填零)提供基底,内存完整逐维搜索——FT/-alt/ZTP 约定由真实后端保证
    fixed: dict[str, tuple[float, float]] = {}
    ext = "ft3" if experiment.ndim >= 3 else "ft2"
    # 0.2.199-补29dn(方案A,用户):填零先定——间接维相位搜索预览用与终跑
    # 一致的 auto 完整填零,避免低分辨率下评分最优与终谱不一致;
    # 0.2.199-补29dt:NUS 直接维在 finalize 实型终谱上搜索(直接维=平面序号,
    # 不 FT/不填零),预览填零只作用于间接维,故 NUS 保持全 auto
    zf_phase = {
        "zero_fill": {
            a: {"mode": "auto"}
            for a in (dim.logical_axis for dim in experiment.dimensions)
        }
    }
    for axis in indirect_axes:
        out_file = f"{experiment.dataset_id}_preview_{axis}.{ext}"
        t_axis = time.time()
        if progress is not None:
            progress(f"相位优化中: {axis} 复型预览中")
        resp = backend.finalize_nus(
            experiment,
            phases=fixed,
            work_dir=work,
            params={**zf_phase, "preview_axis": axis},
            out_file=out_file,
            script_name=f"{experiment.dataset_id}_preview_{axis}_finalize.com",
            progress=progress,
        )
        backend_runs += 1
        if not resp.get("success") or not resp.get("spectrum_path"):
            raise RuntimeError(f"NUS 复型预览({axis})失败: {resp.get('message')}")
        if progress is not None:
            progress(f"相位优化中: {axis} 复型预览完成")
        ax = _axis_index(axis, experiment.ndim)
        arr = _load_preview_with_memory_guard(
            str(resp["spectrum_path"]), axis=axis, unpack_axis=ax,
            progress=progress, logs=logs,
        )
        est = search_axis_memory(
            arr, ax, sign_mode=sign_mode, cancel=cancel_requested
        )
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
        logs += est.logs
        logs.append(
            f"{axis}: 内存相位 = ({est.phase[0]:g}°, {est.phase[1]:g}°) "
            f"score={est.score:.2f}"
        )
        logs.append(f"{axis} 相位搜索完成,耗时 {time.time() - t_axis:.1f} 秒")
    # 0.2.199-补29fj(用户):跳过联合复核(与 uniform 一致,代码保留)。
    # 直接维:0.2.199-补29l 改到「纯实终谱 + 投影迹线 + HT」上搜。
    # 间接维已按上述搜索校正(phases=fixed),生成真实(实型)finalize 终谱
    # (HNN 等含两个同名 15N 间接核时头标签重复,proj3D 无法按标签选轴,
    # 必须用 numpy 投影路径,按直接维轴角色/尺寸选平面)
    # ——即用户人工调相看到的最终谱(直接维仍未校正);等价 proj3D.tcl -sum
    # 的含直接维两平面(XZ/YZ,对间接维求和)抽直接维投影迹线,逐条实谱
    # Hilbert 补虚部(nmrPipe 符号约定:Im=-H_scipy)调相后统计最优
    # (0.2.199-补29l/补29m:投影迹线逐条+共识;振铃旁瓣过滤+p1 平缓保护)。
    # score<30 时保持 (0,0)。
    import time as _time

    from core.optimization.phase_consensus import search_direct_phase_real_ht

    if not auto_phase:
        direct_phase = (0.0, 0.0)
        logs.append(
            f"实验类型 {experiment.experiment_type.name}: 幅度谱不自动调相,"
            "直接维相位保持 (0,0)(跳过搜索)"
        )
    else:
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        preview_out = f"{experiment.dataset_id}_direct_final.{ext}"
        resp_direct = backend.finalize_nus(
            experiment,
            phases=fixed,
            work_dir=work,
            # 0.2.199-补29dn(方案A,用户):直接维搜索预览同样用与终跑一致的
            # auto 完整填零——相位搜索与终谱同分辨率,避免低分辨率下评分
            # 最优与终谱不一致(原为最低填零,直接维 2048 vs 终跑 4096)
            params={**params_first, **zf_phase},
            out_file=preview_out,
            script_name=(
                f"{experiment.dataset_id}_direct_final_finalize.com"
            ),
            progress=progress,
        )
        backend_runs += 1
        if (
            not resp_direct.get("success")
            or not resp_direct.get("spectrum_path")
        ):
            raise RuntimeError(
                f"直接维实型终谱预览失败: {resp_direct.get('message')}"
            )
        search_arr = _read_real_ft3(
            str(resp_direct["spectrum_path"])
        )
        logs.append(
            f"直接维相位搜索基底: 实型终谱 {search_arr.shape}"
            f"(间接维已校正,直接维=最后一轴,投影迹线=间接维点数之和)"
        )
        cache = _load_direct_phase_cache(
            work, experiment, params_first, search_arr.shape
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
            direct_est = search_direct_phase_real_ht(
                search_arr,
                axis=-1,
                sign_mode=sign_mode,
                progress=progress,
                cancel=cancel_requested,
            )
            elapsed = _time.time() - t0
            if progress is not None:
                progress(f"直接维相位搜索完成,耗时 {elapsed:.1f} 秒")
            logs.append(f"直接维相位搜索完成,耗时 {elapsed:.1f} 秒")
            direct_phase = (0.0, 0.0)
            if direct_est is not None and direct_est[2] >= 30.0:
                direct_phase = (float(direct_est[0]), float(direct_est[1]))
                # 0.2.199-补22:放宽 p1 归零护栏(20→170)——高场(1200MHz)
                # 采集延迟使真实 p1 可达 100°+;仅 >170°(近全幅翻转,
                # 疑似包装伪影)才归零;终跑窗口变化时 p1 由 0.2.162-补16
                # 按窗口宽度重归一化。
                if abs(direct_phase[1]) > 170.0:
                    logs.append(
                        f"直接维 HT 搜索 p1={direct_phase[1]:g}° 幅值异常"
                        "(>170°),归零"
                    )
                    direct_phase = (direct_phase[0], 0.0)
                logs.append(
                    f"直接维投影 HT 搜索: {direct_axis}="
                    f"({direct_phase[0]:g}°, {direct_phase[1]:g}°) "
                    f"score={direct_est[2]:.2f}"
                )
                _save_direct_phase_cache(
                    work, experiment, params_first, search_arr.shape,
                    direct_phase[0], direct_phase[1], float(direct_est[2]),
                    elapsed,
                )
            else:
                logs.append(
                    "直接维投影 HT 搜索无干净信号峰或置信度不足,保持 (0,0)"
                )
    logs.append(
        f"直接维内存相位: {direct_axis}=({direct_phase[0]:g}°, "
        f"{direct_phase[1]:g}°)"
    )
    # 0.2.199-补29do(用户):迭代式——间接维在直接维确定后重搜一轮
    # (直接维为独立实型 HT 方法,经 phase.json 缓存;间接维重搜重新锁定
    # 迹线,解双向依赖。3D NUS 实测多轮重搜会 ±180° 符号摆动且每轮约
    # 30s,故收敛迭代限一轮)
    if indirect_axes and auto_phase:
        for _round in range(1):
            changed = False
            for axis in indirect_axes:
                out_file = (
                    f"{experiment.dataset_id}_preview_{axis}"
                    f"_r{_round + 2}.{ext}"
                )
                t_axis = time.time()
                if progress is not None:
                    progress(f"相位优化中: {axis} 复型预览(迭代)中")
                # 预览轴自身须排除:finalize 预览会把 phases 里预览轴的
                # 相位直接写进 PS(与 uniform 预览不同,后者会过滤),带旧
                # 相位生成会搜到残差(≈0)并覆盖丢失绝对相位
                resp = backend.finalize_nus(
                    experiment,
                    phases={
                        k: v for k, v in fixed.items() if k != axis
                    },
                    work_dir=work,
                    params={**zf_phase, "preview_axis": axis},
                    out_file=out_file,
                    script_name=(
                        f"{experiment.dataset_id}_preview_{axis}"
                        f"_r{_round + 2}_finalize.com"
                    ),
                    progress=progress,
                )
                backend_runs += 1
                if not resp.get("success") or not resp.get("spectrum_path"):
                    raise RuntimeError(
                        f"NUS 复型预览重搜({axis})失败: {resp.get('message')}"
                    )
                ax = _axis_index(axis, experiment.ndim)
                arr = _load_preview_with_memory_guard(
                    str(resp["spectrum_path"]), axis=axis, unpack_axis=ax,
                    progress=progress, logs=logs,
                )
                est = search_axis_memory(
                    arr, ax, sign_mode=sign_mode, cancel=cancel_requested
                )
                if est is None:
                    raise RuntimeError(f"内存相位重搜({axis})无可用迹线")
                if sign_mode == "mixed":
                    resolved = _disambiguate_180_mixed(
                        arr, ax, est.phase, experiment, axis
                    )
                    phase = resolved
                else:
                    phase = est.phase
                prev = fixed.get(axis)
                fixed[axis] = phase
                logs += est.logs
                logs.append(
                    f"{axis}: 内存相位重搜(迭代 {_round + 2}) = "
                    f"({phase[0]:g}°, {phase[1]:g}°) score={est.score:.2f}"
                )
                if prev is None or _phase_delta(prev, phase) >= 5.0:
                    changed = True
                logs.append(
                    f"{axis} 相位重搜完成,耗时 {time.time() - t_axis:.1f} 秒"
                )
            if not changed:
                logs.append(f"相位迭代: 间接维第 {_round + 2} 轮无变化,收敛")
                break
    # 处理参数优化(基线/填零/窗函数):逐轴相位搜索后、终跑前;各维最终相位
    # 与优化后的处理参数一起填入初始脚本,生成新的完整脚本做终跑——
    # 直接维相位进 step1 PS(EXT 后,与 recon 平面内存旋转同归一化),
    # 间接维相位进 step3 PS;不写 nus3d_rc_ph 旋转副本。
    if progress is not None:
        progress("相位搜索完成,开始处理参数优化(基线/填零/窗函数)")
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
    # 0.2.199-补29u:终脚本基线交叉核对——报告说选了 order3 时,
    # 终脚本应有对应 POLY(排查「报告/脚本不一致」)
    try:
        final_script = work / f"{experiment.dataset_id}_nus.com"
        if final_script.is_file():
            _text = final_script.read_text(
                encoding="utf-8", errors="replace"
            )
            _polys = [
                ln.strip() for ln in _text.splitlines() if "POLY" in ln
            ]
            logs.append(
                "终脚本基线核对: "
                + ("; ".join(_polys) if _polys else "无 POLY")
            )
    except OSError:
        pass
    _append_final_summary(
        logs,
        str(final["spectrum_path"]),
        direct_axis=direct_axis,
        direct_phase=direct_phase_final,
        phases=fixed,
        backend_runs=backend_runs,
        baseline=proc["baseline"],
        baseline_scores=proc.get("baseline_scores"),
        zero_fill=proc["zero_fill"],
        window=proc["window"],
        diagnostics=diagnostics,
        optimization_logs=proc["logs"],
        peak_sign=_template_peak_sign(experiment),
        progress=progress,
    )
    _cleanup_unified_intermediates(
        work, experiment.dataset_id, experiment=experiment, backend=backend
    )
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
